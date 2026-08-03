"""Prove the protective stop actually places at the venue — with nothing at risk.

    python -m scripts.verify_algo_stop --symbol ETHUSDT
    python -m scripts.verify_algo_stop --symbol ETHUSDT --json

The runtime's protective stop was refused by the venue on 2026-08-02, twice, because Binance
had moved every conditional order type onto the Algo Order API. The migration to
``/fapi/v1/algoOrder`` is implemented and covered by tests that run on fake adapters with zero
network — which proves what the code SENDS and nothing about what the venue does with it.

**The bracket has never once been exercised end to end against the real venue.** The three
clean canaries the live-promotion gate required are entry-only by construction, so they proved
signing, submission and reconciliation, and the thing that was broken was none of those. That
is the evidence gap this closes, and it is a different gap from the one the canary fills.

**Why this cannot cost anything.** It places ONE ``closePosition`` STOP_MARKET — the exact leg
the bracket uses, built by the exact same code — and then cancels it. ``closePosition`` is
Close-All: with no position open there is nothing for a trigger to close, so the order cannot
fill. It carries no quantity, so it cannot open anything either; and the trigger sits
``--distance-pct`` away from the market so it should never even trigger.

That argument rests entirely on the account being FLAT, so being flat is checked rather than
assumed, and an account this cannot read refuses instead of guessing. It also runs the REAL
``place_bracket_leg`` / ``cancel_bracket_legs`` rather than a parallel implementation: a
verification that exercises different code proves the wrong thing.

**Whatever it places, it withdraws.** A stray resting algo order counts against the venue's
per-symbol conditional cap, so a cancel that fails is reported loudly, with the id, and exits
non-zero — an order this tool left behind must never read as a pass.

**Real money's door, even though nothing can fill. Every step here is Thomas's.** Claude does
not run this, does not handle the keys, and does not enable live trading. Without
``MVP_LIVE_TRADING=real`` and the canary confirmation phrase it selects the inert adapter and
sends nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.control import ControlStore  # noqa: E402
from runtime.mvp_runtime.crypto.account import read_account  # noqa: E402
from runtime.mvp_runtime.crypto.live_execution import select_order_adapter  # noqa: E402
from runtime.mvp_runtime.crypto.live_leg import (  # noqa: E402
    build_bracket_intent,
    cancel_bracket_legs,
    place_bracket_leg,
)
from runtime.mvp_runtime.crypto.live_order import (  # noqa: E402
    CANARY_CONFIRMATION_ENV,
    CANARY_CONFIRMATION_PHRASE,
)
from runtime.mvp_runtime.crypto.market_data import (  # noqa: E402
    read_reference_price,
    select_market_data_collector,
)
from runtime.mvp_runtime.errors import MvpRuntimeError  # noqa: E402

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_BLOCKED = 3
EXIT_LEFT_BEHIND = 4

# How far above the market the trigger sits. The order cannot fill whatever this is — there is
# no position for a Close-All to close — so the distance is not what makes this safe; being flat
# is. It exists so a trigger is not even approached while the check runs, which keeps the venue's
# own trigger machinery out of the result: an order that triggered and closed nothing would be
# a confusing pass rather than a clean one.
DEFAULT_DISTANCE_PCT = 10.0
MIN_DISTANCE_PCT = 2.0

WORKING_TYPE = "MARK_PRICE"


def _flat_for(snapshot, symbol: str) -> tuple[bool, str | None]:
    """Whether the venue reports no open position in ``symbol``. Fail-closed on no answer."""
    if snapshot is None:
        return False, "the account could not be read"
    for position in snapshot.positions:
        if str(position.symbol).upper() != symbol.upper():
            continue
        if abs(float(position.quantity)) > 0:
            return False, (
                f"{position.symbol} {position.side} {position.quantity} is open — a "
                "closePosition stop CAN fill against it, which is the one case this tool's "
                "safety argument does not cover"
            )
    return True, None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbol", default="ETHUSDT")
    parser.add_argument("--distance-pct", type=float, default=DEFAULT_DISTANCE_PCT,
                        help=f"trigger this %% above the market (min {MIN_DISTANCE_PCT})")
    parser.add_argument("--timeout-seconds", type=int, default=10)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.distance_pct < MIN_DISTANCE_PCT:
        print(f"BLOCKED: --distance-pct must be at least {MIN_DISTANCE_PCT}")
        return EXIT_USAGE

    now = timeutil.utc_now_iso()
    symbol = args.symbol.upper()
    steps: list[dict] = []

    def record(step: str, ok: bool | None, **detail) -> None:
        steps.append({"step": step, "ok": ok, **detail})

    # 1. The kill switch. A runtime an operator has halted must not reach the venue through a
    #    side door either, however harmless the order.
    try:
        state = ControlStore(ROOT).load()
    except MvpRuntimeError as exc:
        print(f"BLOCKED: control state unreadable ({exc.reason_code})")
        return EXIT_BLOCKED
    if not state.execution_allowed:
        print(f"BLOCKED: runtime is {state.mode}")
        return EXIT_BLOCKED
    record("kill_switch", True, mode=state.mode)

    # 2. The adapter. Inert unless the operator opted in; the canary phrase is required for the
    #    same reason it is on a canary — this is the venue's real order door, and a deliberate
    #    phrase is what separates an intended send from a stray invocation.
    adapter = select_order_adapter(now=now, root=None)
    live = bool(getattr(adapter, "network_egress", False))
    if live and os.environ.get(CANARY_CONFIRMATION_ENV, "").strip() != CANARY_CONFIRMATION_PHRASE:
        print("BLOCKED: this reaches the venue's order endpoint; set the canary confirmation "
              f"phrase in {CANARY_CONFIRMATION_ENV} to proceed")
        return EXIT_BLOCKED
    record("adapter", True, live=live)

    # 3. A price to sit away from. Refused rather than skipped when unavailable — the canary's
    #    own rule: on a run where market data is broken, "nothing to check" must not read as
    #    "approved".
    price, price_error = read_reference_price(
        symbol, collector=select_market_data_collector(now=now, root=None), now=now,
        timeout_seconds=args.timeout_seconds,
    )
    if price is None or price <= 0:
        print(f"BLOCKED: no reference price for {symbol} ({price_error})")
        return EXIT_BLOCKED
    record("reference_price", True, price=price)

    # 4. FLAT, checked at the venue. This is the whole safety argument, so it is the one thing
    #    that is never assumed.
    if live:
        snapshot, _ = read_account(timeout_seconds=args.timeout_seconds, root=None)
        flat, why = _flat_for(snapshot, symbol)
        if not flat:
            print(f"BLOCKED: {why}")
            return EXIT_BLOCKED
        record("flat", True)
    else:
        record("flat", None, detail="dry run — the venue was not asked")

    # 5. The leg itself, built by the code the money path uses. A BUY stop above the market is
    #    the shape a SHORT's protective stop takes; the direction is irrelevant to what is being
    #    proved and this one keeps the trigger away from the price on a rising market.
    trigger = round(price * (1.0 + args.distance_pct / 100.0), 8)
    intent = build_bracket_intent(
        symbol=symbol, leg="SL", side="BUY", price=trigger,
        working_type=WORKING_TYPE, position_seed=f"VERIFY_{symbol}_{now}",
    )
    placement = place_bracket_leg(intent, adapter=adapter, timeout_seconds=args.timeout_seconds)
    record("place", bool(placement.get("placed")), trigger=trigger,
           status=placement.get("status"), error=placement.get("error"),
           error_detail=placement.get("error_detail"),
           client_order_id=placement.get("client_order_id"))

    # 6. Withdraw it, through the real cancel path, whether or not the place reported success —
    #    a submit that was reported as failed may still have landed, which is exactly why
    #    `place_bracket_leg` asks the venue rather than trusting the submit.
    cancels = cancel_bracket_legs(
        {"symbol": symbol, "stop_client_order_id": placement.get("client_order_id")},
        adapter=adapter, timeout_seconds=args.timeout_seconds,
    )
    cancel = cancels[0] if cancels else {"cancelled": False, "error": "no cancel attempted"}
    record("cancel", bool(cancel.get("cancelled")), already_gone=cancel.get("already_gone"),
           error=cancel.get("error"), error_detail=cancel.get("error_detail"))

    left_behind = bool(placement.get("placed")) and not cancel.get("cancelled")
    report = {"symbol": symbol, "asked_the_venue": live, "steps": steps,
              "placed": bool(placement.get("placed")), "left_behind": left_behind}

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        if not live:
            print("DRY RUN — MVP_LIVE_TRADING is not 'real'. The venue was not asked; the "
                  "verdicts below are the inert adapter's.\n")
        for step in steps:
            mark = {True: "ok", False: "FAILED", None: "-"}[step["ok"]]
            extra = " ".join(f"{k}={v!r}" for k, v in step.items()
                             if k not in ("step", "ok") and v is not None)
            print(f"  {step['step']:16} {mark:8} {extra}")
        if placement.get("placed") and cancel.get("cancelled"):
            print("\nThe stop placed and rested at the venue, and was withdrawn. This is the "
                  "evidence the canaries could not produce.")
        elif not placement.get("placed"):
            print(f"\nThe stop did NOT place: {placement.get('error_detail') or placement.get('error')}")
        if left_behind:
            print(f"\nLEFT BEHIND: {placement.get('client_order_id')} is resting and the cancel "
                  "failed. Withdraw it by hand — it counts against this symbol's conditional "
                  "order cap until you do.")

    if left_behind:
        return EXIT_LEFT_BEHIND
    return EXIT_OK if placement.get("placed") else EXIT_BLOCKED


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
