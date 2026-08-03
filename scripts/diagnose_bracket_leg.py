"""Ask the venue why it refuses a protective bracket leg — creating no order.

    python -m scripts.diagnose_bracket_leg --symbol ETHUSDT --direction SHORT \
        --entry-price 1876.35 --stop-price 1907.98 --take-profit 1808.70 --quantity 0.031

On 2026-08-02 the runtime's first two autonomous entries both filled and both had their
``closePosition`` STOP_MARKET protective leg refused. ``live_leg`` did the right thing — it
cancelled the surviving target and closed the exposure, twice, for 0.12 USDT in fees — but the
record kept only ``error: ORDER_REJECTED``, the same string for every rejection there is. So
**the runtime cannot hold a live position and nobody knows why.**

#426 now records the venue's own code and message. That only helps on the NEXT rejection, and
reaching one costs a real entry and a real close, happens only when a strategy routes, and is
capped at two orders a day. This asks the same question for free: ``POST /fapi/v1/order/test``
takes identical parameters and identical signing and **creates nothing**. No fill, no resting
order, nothing to cancel.

**The request is not re-written here.** It is built through the same
``live_leg.build_bracket_intent`` → ``live_execution.build_order_request`` the money path uses,
so what is validated is what would be sent. A hand-rolled dict would test a request the runtime
does not make, which is worse than not testing at all — it would produce a confident wrong
answer. Only the inputs (symbol, side, prices, quantity) come from the command line, and
``--from-record`` takes them from the incident itself.

**It found the answer on 2026-08-03, and the answer changed what this tool can do.** The stop
came back `-4120: Order type not supported for this endpoint. Please use the Algo Order API
endpoints instead.` Binance moved every conditional order type off `/fapi/v1/order` (announced
2025-11-06, enforced 2025-12-09); the runtime now places the stop on `/fapi/v1/algoOrder`.

Which means the STOP leg can no longer be validated here at all: the test endpoint belongs to
the order API — the door conditional orders were moved off — so asking it about an algo request
would return -4120 forever as an artefact of the question rather than a fact about the request.
The venue publishes no test counterpart under the Algo API, so this reports the stop leg as
`UNSUPPORTED` and validates the target leg, which is a plain LIMIT and still lives there.

**What an ACCEPTED result does and does not mean.** The endpoint validates the REQUEST. A
rejection that depends on account state at that instant — no position to reduce, an algo-order
count, a margin condition — can pass here and still fail on the real path. So ACCEPTED narrows
the cause to something state-dependent; it never says the order would land.

**Read-only against the venue, and still Thomas's.** It signs with the live order key, so
without ``MVP_LIVE_TRADING=real`` it selects the inert dry-run adapter and asks nothing — and
says so, rather than reporting an unasked question as a pass. Claude does not run this and does
not handle the keys.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime.crypto.live_execution import (  # noqa: E402
    build_order_request,
    select_order_adapter,
)
from runtime.mvp_runtime.crypto.live_leg import build_bracket_intent  # noqa: E402
from runtime.mvp_runtime.errors import MvpRuntimeError  # noqa: E402

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_REJECTED = 3

# The 2026-08-02 incident, exactly as the cycle records hold it. Defaults rather than a separate
# code path so the reproduction is one flag, and so the numbers cannot drift from the record
# they came from: cycle crypto_cycle_2e06c80e7e96ee7d20ac, ETHUSDT 4h SHORT, entry filled
# 1876.35, stop 1907.98 REJECTED, target 1808.70 accepted, quantity 0.031.
INCIDENT = {
    "symbol": "ETHUSDT", "direction": "SHORT", "entry_price": 1876.35,
    "stop_price": 1907.98, "take_profit": 1808.70, "quantity": 0.031,
}
WORKING_TYPE = "MARK_PRICE"


def _legs(args: argparse.Namespace) -> list[dict]:
    """Both legs, built the way `live_leg.execute_live_entry` builds them.

    The target is included deliberately even though it placed fine on 2026-08-02: it is the
    control. A run where BOTH legs are refused says something about the account or the key; a
    run where only the stop is refused says something about the stop, and without the control
    the two are indistinguishable."""
    # A SHORT is closed by BUYing, a LONG by SELLing — both legs take the same side.
    exit_side = "BUY" if args.direction.upper() == "SHORT" else "SELL"
    seed = f"DIAG_{args.symbol}_{args.direction.upper()}"
    return [
        build_bracket_intent(
            symbol=args.symbol, leg="SL", side=exit_side, price=args.stop_price,
            working_type=WORKING_TYPE, position_seed=seed,
        ),
        build_bracket_intent(
            symbol=args.symbol, leg="TP", side=exit_side, price=args.take_profit,
            working_type=WORKING_TYPE, position_seed=seed, quantity=args.quantity,
        ),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from-record", action="store_true",
                        help="use the 2026-08-02 incident's own numbers (default if no others given)")
    parser.add_argument("--symbol")
    parser.add_argument("--direction", choices=["LONG", "SHORT", "long", "short"])
    parser.add_argument("--entry-price", type=float)
    parser.add_argument("--stop-price", type=float)
    parser.add_argument("--take-profit", type=float)
    parser.add_argument("--quantity", type=float)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    supplied = {k: getattr(args, k) for k in
                ("symbol", "direction", "entry_price", "stop_price", "take_profit", "quantity")}
    if args.from_record or all(v is None for v in supplied.values()):
        for key, value in INCIDENT.items():
            if getattr(args, key) is None:
                setattr(args, key, value)
    missing = [k for k, v in vars(args).items()
               if k in INCIDENT and getattr(args, k) is None]
    if missing:
        print(f"BLOCKED: missing {', '.join(sorted(missing))} (or pass --from-record)")
        return EXIT_USAGE

    try:
        adapter = select_order_adapter()
    except MvpRuntimeError as exc:
        print(f"BLOCKED: could not select an order adapter ({exc.reason_code})")
        return EXIT_USAGE
    live = bool(getattr(adapter, "network_egress", False))

    results = []
    for intent in _legs(args):
        request = build_order_request(intent)
        try:
            verdict = adapter.validate_order(request)
        except MvpRuntimeError as exc:
            # "The venue refused it" and "I could not ask" are different answers.
            verdict = {"accepted": None, "code": None, "msg": None,
                       "unreachable": exc.reason_code}
        results.append({
            "leg": intent["order_type_exchange"],
            # The signature never appears; the request itself carries no secret.
            "request": request,
            "verdict": verdict,
        })

    report = {"asked_the_venue": live, "results": results}
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        if not live:
            print("DRY RUN — MVP_LIVE_TRADING is not 'real', so nothing was asked.")
            print("The verdicts below are the inert adapter's, not the venue's.\n")
        for row in results:
            verdict = row["verdict"]
            if verdict.get("unreachable"):
                mark = f"UNREACHABLE ({verdict['unreachable']})"
            elif verdict.get("supported") is False:
                mark = f"UNSUPPORTED — {verdict.get('detail')}"
            elif verdict.get("accepted"):
                mark = "ACCEPTED" + (" (dry run — nothing checked)" if verdict.get("dry_run") else "")
            else:
                mark = f"REJECTED  code={verdict.get('code')}  msg={verdict.get('msg')!r}"
            print(f"{row['leg']:14} {mark}")
            print(f"  sent: {row['request']}")
        if live:
            print("\nNo order was created. This endpoint validates and discards.")
            print("An ACCEPTED stop means the SHAPE is right — the 2026-08-02 cause would then")
            print("be account state at that instant, not the request. See the module docstring.")

    rejected = any(r["verdict"].get("accepted") is False for r in results)
    return EXIT_REJECTED if rejected else EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
