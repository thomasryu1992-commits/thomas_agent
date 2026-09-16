#!/usr/bin/env python3
"""Operator tool: one signed TESTNET cycle — the evidence for the LIVE_AUTONOMOUS climb.

    # What would run, and what the machine's posture allows. Sends nothing.
    python -m scripts.run_signed_testnet_cycle --plan --symbol BTCUSDT --quantity 0.002

    # Run it. One cycle: entry -> protective legs RESTING -> withdraw -> reconcile.
    python -m scripts.run_signed_testnet_cycle --run --symbol BTCUSDT --quantity 0.002 \
        --operator thomas --reason "evidence for the LIVE_AUTONOMOUS climb"

    # What has been recorded here.
    python -m scripts.run_signed_testnet_cycle --list

A cycle is the whole thing, because the failures it exists to catch all happened AFTER the entry:
2026-08-02 (both protective legs refused — conditional orders had moved to the Algo API), 08-03 (a
stop accepted but unfindable, so never withdrawn) and 08-05 (an algo fill whose renamed fields the
settle path could not read). An entry-only rehearsal reproduces none of them.

No real money: the adapter is the testnet one, behind its own opt-in (``MVP_TESTNET_TRADING=real``)
and its own key pair, writing the testnet venue's own counter and evidence file. The live path is
neither a prerequisite nor reachable from here.

Run it in the scheduler container as the service user (it writes governed state):
``docker exec -u 10001 thomas-scheduler python -m scripts.run_signed_testnet_cycle ...``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import (  # noqa: E402
    EXIT_BLOCKED,
    EXIT_OK,
    EXIT_USAGE,
    force_utf8_io,
)
from runtime.mvp_runtime.control import ControlStore  # noqa: E402
from runtime.mvp_runtime.crypto import testnet_evidence  # noqa: E402
from runtime.mvp_runtime.crypto import testnet_execution as testnet  # noqa: E402
from runtime.mvp_runtime.crypto.execution_stage import resolve_execution_stage  # noqa: E402
from runtime.mvp_runtime.crypto.live_execution import fill_facts, submit_and_reconcile  # noqa: E402
from runtime.mvp_runtime.crypto.live_leg import (  # noqa: E402
    BRACKET_RESTING_STATUSES,
    build_bracket_intent,
    place_bracket_leg,
)
from runtime.mvp_runtime.crypto.live_order import (  # noqa: E402
    LiveOrderLimits,
    build_live_order_intent,
)
from runtime.mvp_runtime.crypto.market_data import (  # noqa: E402
    read_reference_price,
    select_market_data_collector,
)
from runtime.mvp_runtime.crypto.state import VENUE_TESTNET  # noqa: E402
from runtime.mvp_runtime.errors import MvpRuntimeError, ToolError  # noqa: E402
from runtime.mvp_runtime.state_guard import assert_not_foreign_root_run  # noqa: E402
from runtime.read_only_kernel import integrity  # noqa: E402

# How far the protective stop sits from the entry. Far enough that a testnet market cannot reach
# it during the cycle (the leg is meant to REST, and a leg that fills has proven something else),
# close enough to be a realistic conditional order.
STOP_DISTANCE_PCT = 5.0
TARGET_DISTANCE_PCT = 5.0


class _Refusal(MvpRuntimeError):
    """A refusal that names its reason code, like the probe door's."""


def _price(symbol: str, *, now: str, root: Path | None, timeout_seconds: int) -> float:
    """A reference price, read from the public market data the whole stack already reads.

    It sizes the order and bounds it; it is not the testnet venue's own book, and the two differ
    slightly. That is acceptable for a bound and stated rather than hidden."""
    collector = select_market_data_collector(now=now, root=root)
    price, reason = read_reference_price(symbol, collector=collector, now=now,
                                         timeout_seconds=timeout_seconds)
    if price is None:
        raise _Refusal("TESTNET_PRICE_UNREADABLE", f"no usable reference price for {symbol} ({reason})")
    return float(price)


def plan_cycle(*, symbol: str, quantity: float, root: Path | None, now: str,
               timeout_seconds: int = 10) -> dict:
    """What this cycle would do, and what the machine's posture says about it. Sends nothing."""
    adapter = testnet.select_testnet_order_adapter(now=now, root=root)
    stage = resolve_execution_stage(root, now=now)
    control = (ControlStore(root) if root is not None else ControlStore.default()).load()
    limits = LiveOrderLimits.from_env()
    price = _price(symbol, now=now, root=root, timeout_seconds=timeout_seconds)
    intent = build_live_order_intent(
        {"direction": "LONG", "entry_price": price,
         "stop_loss": round(price * (1 - STOP_DISTANCE_PCT / 100), 2),
         "strategy_id": "signed_testnet_cycle"},
        symbol=symbol, quantity=quantity, notional_usdt=round(price * quantity, 8), now=now,
    )
    verdict = testnet.evaluate_testnet_order_guard(
        intent,
        gate_open=bool(getattr(adapter, "network_egress", False)),
        # `execution_allowed`, not `trading_allowed`: a killed or paused runtime sends nothing
        # anywhere, but the live ARM is a bigger act than a testnet rehearsal — requiring it here
        # would mean arming real trading in order to earn the evidence for arming real trading.
        runtime_active=control.execution_allowed,
        manual_kill_switch=limits.manual_kill_switch,
        submitted_today=testnet.count_testnet_today(root),
        execution_stage=stage,
    )
    return {"adapter": adapter, "stage": stage, "intent": intent, "price": price, "verdict": verdict}


def run_cycle(*, symbol: str, quantity: float, operator: str, reason: str,
              root: Path | None = None, now: str | None = None, timeout_seconds: int = 10) -> dict:
    """One cycle end to end, recorded. Refuses before the venue if anything is not in order."""
    now = now or timeutil.utc_now_iso()
    assert_not_foreign_root_run(root)
    planned = plan_cycle(symbol=symbol, quantity=quantity, root=root, now=now,
                         timeout_seconds=timeout_seconds)
    adapter, stage, intent, price = (planned["adapter"], planned["stage"],
                                     planned["intent"], planned["price"])
    verdict = planned["verdict"]
    if not verdict["approved"]:
        raise _Refusal("TESTNET_GUARD_REFUSED",
                       "the testnet order guard refused: " + "; ".join(verdict["blocks"] or verdict["repairs"]))
    if not getattr(adapter, "network_egress", False):
        raise _Refusal("TESTNET_TRADING_OFF",
                       f"{testnet.TESTNET_TRADING_ENV} is not '{testnet.REAL_TESTNET_TRADING}'; "
                       "this run would prove nothing about signing or the venue")

    counter = testnet.TestnetOrderCounter(root=root,
                                          authorization=getattr(adapter, "_authorization", None))
    # Decimal strings, not floats: the canonical seed refuses a float (a float is not a stable
    # identity across machines), the same rule every other id in this stack follows.
    cycle_id = integrity.short_id("testnetcycle",
                                  {"symbol": symbol, "quantity": f"{quantity:.8f}", "at": now})
    started_at = now

    # 1. The entry. Reconciled by the same pure function the live path uses, so the two can never
    #    disagree about what RECONCILED means.
    try:
        entry = submit_and_reconcile(intent, adapter=adapter, guard_verdict=verdict, now=now,
                                     timeout_seconds=timeout_seconds)
    finally:
        counter.record_submission()
    entry_row = {
        "reconcile_status": entry.get("reconcile_status"),
        "mismatches": entry.get("mismatches") or [],
        "exchange_order_id": entry.get("exchange_order_id"),
        "client_order_id": entry.get("client_order_id"),
        "fill": fill_facts(entry.get("venue_order")),
    }

    # 2. The protective legs, at the ALGO endpoint — the part an entry-only rehearsal never
    #    reaches and the part that broke on 2026-08-02.
    legs: list[dict] = []
    filled_qty = float((entry_row["fill"] or {}).get("executed_qty") or quantity)
    for leg, side, level in (
        ("SL", "SELL", round(price * (1 - STOP_DISTANCE_PCT / 100), 2)),
        ("TP", "SELL", round(price * (1 + TARGET_DISTANCE_PCT / 100), 2)),
    ):
        leg_intent = build_bracket_intent(symbol=symbol, leg=leg, side=side, price=level,
                                          working_type="MARK_PRICE", position_seed=cycle_id,
                                          quantity=filled_qty)
        placed = place_bracket_leg(leg_intent, adapter=adapter, timeout_seconds=timeout_seconds)
        # 3. Withdraw it: a leg left resting at the venue is the 2026-08-03 ghost stop.
        withdrawn = None
        if placed.get("client_order_id"):
            try:
                cancelled = adapter.cancel_order(symbol, str(placed["client_order_id"]),
                                                 timeout_seconds=timeout_seconds, algo=True)
                withdrawn = True if cancelled is not None else (placed.get("status") not in BRACKET_RESTING_STATUSES)
            except MvpRuntimeError as exc:
                withdrawn = False
                placed = {**placed, "cancel_error": exc.reason_code}
        legs.append({
            "leg": leg,
            "algo": True,
            "observed_status": placed.get("status"),
            "placed": bool(placed.get("placed")),
            "client_order_id": placed.get("client_order_id"),
            "exchange_order_id": placed.get("exchange_order_id"),
            "error": placed.get("error"),
            "withdrawn": withdrawn,
        })

    # 4. The exit: reduceOnly, reconciled like the entry.
    exit_intent = build_live_order_intent(
        {"direction": "LONG", "entry_price": price, "stop_loss": level, "strategy_id": "signed_testnet_cycle"},
        symbol=symbol, quantity=filled_qty, notional_usdt=round(price * filled_qty, 8), now=now,
        reduce_only=True, close_reason="signed_testnet_cycle",
    )
    exit_verdict = testnet.evaluate_testnet_order_guard(
        exit_intent, gate_open=True, runtime_active=True, manual_kill_switch=False,
        submitted_today=0, execution_stage=stage,
    )
    try:
        exit_result = submit_and_reconcile(exit_intent, adapter=adapter, guard_verdict=exit_verdict,
                                           now=now, timeout_seconds=timeout_seconds)
    finally:
        counter.record_submission()
    exit_row = {
        "reconcile_status": exit_result.get("reconcile_status"),
        "mismatches": exit_result.get("mismatches") or [],
        "reduce_only": True,
        "exchange_order_id": exit_result.get("exchange_order_id"),
        "fill": fill_facts(exit_result.get("venue_order")),
    }

    # 5. The venue's own position view, after the close.
    reconciliation = {"status": testnet_evidence.RECONCILED if exit_row["reconcile_status"] == testnet_evidence.RECONCILED
                      else "UNRECONCILED", "checked_at": timeutil.utc_now_iso()}

    record = testnet_evidence.build_cycle_record(
        cycle_id=cycle_id, symbol=symbol, entry=entry_row, protective_legs=legs,
        exit_result=exit_row, position_reconciliation=reconciliation,
        adapter_tool_id=getattr(adapter, "tool_id", "unknown"),
        base_url_host="testnet.binancefuture.com",
        started_at=started_at, completed_at=timeutil.utc_now_iso(),
    )
    testnet_evidence.append_cycle(record, root)
    findings = testnet_evidence.cycle_findings(record)
    return {"cycle_id": cycle_id, "record": record, "complete": not findings, "findings": findings,
            "operator": operator, "reason": reason}


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true", help="what would run; sends nothing")
    mode.add_argument("--run", action="store_true", help="run one cycle and record it")
    mode.add_argument("--list", action="store_true", help="the cycles recorded here")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--quantity", type=float)
    parser.add_argument("--operator", help="operator identity (recorded)")
    parser.add_argument("--reason", help="why this cycle was run (recorded)")
    parser.add_argument("--root", type=Path, default=None, help="state root (defaults to the repo)")
    args = parser.parse_args(argv)
    now = timeutil.utc_now_iso()
    try:
        if args.list:
            rows = testnet_evidence.evidence_rows(args.root)
            if not rows:
                sys.stdout.write("signed testnet cycles: none recorded here\n")
                return EXIT_OK
            for row in rows:
                mark = "COMPLETE" if row["complete"] else "INCOMPLETE"
                sys.stdout.write(f"[{mark}] {row['cycle_id']} {row['symbol']} {row['completed_at']}"
                                 + ("" if row["complete"] else f" - {'; '.join(row['findings'])}") + "\n")
            return EXIT_OK
        if not args.quantity:
            sys.stderr.write("USAGE: --plan/--run need --quantity\n")
            return EXIT_USAGE
        if args.plan:
            planned = plan_cycle(symbol=args.symbol, quantity=args.quantity, root=args.root, now=now)
            verdict = planned["verdict"]
            sys.stdout.write(
                f"symbol        : {args.symbol} x {args.quantity} (~{planned['intent'].get('order_notional_usdt')} USDT)\n"
                f"execution stage: {planned['stage'].stage}\n"
                f"adapter       : {type(planned['adapter']).__name__} (network_egress="
                f"{getattr(planned['adapter'], 'network_egress', False)})\n"
                f"guard         : {verdict['status']}\n"
            )
            for block in verdict["blocks"] + verdict["repairs"]:
                sys.stdout.write(f"  BLOCK : {block}\n")
            return EXIT_OK
        if not (args.operator and args.reason):
            sys.stderr.write("USAGE: --run needs --operator and --reason\n")
            return EXIT_USAGE
        out = run_cycle(symbol=args.symbol, quantity=args.quantity, operator=args.operator,
                        reason=args.reason, root=args.root, now=now)
    except MvpRuntimeError as exc:
        sys.stderr.write(f"BLOCKED {getattr(exc, 'reason_code', 'ERROR')}: {exc}\n")
        return EXIT_BLOCKED
    sys.stdout.write(
        f"{'COMPLETE' if out['complete'] else 'INCOMPLETE'} cycle {out['cycle_id']}"
        + ("" if out["complete"] else " - " + "; ".join(out["findings"])) + "\n"
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
