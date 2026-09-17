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
    gate_banners,
)
from runtime.mvp_runtime.control import ControlStore  # noqa: E402
from runtime.mvp_runtime.crypto import pre_order_gate, testnet_evidence  # noqa: E402
from runtime.mvp_runtime.crypto import testnet_execution as testnet  # noqa: E402
from runtime.mvp_runtime.crypto.execution_stage import resolve_execution_stage  # noqa: E402
from runtime.mvp_runtime.crypto.live_execution import (  # noqa: E402
    SubmitRefused,
    fill_facts,
    submit_and_reconcile,
)
from runtime.mvp_runtime.crypto.live_leg import (  # noqa: E402
    BRACKET_RESTING_STATUSES,
    CONDITIONAL_ORDER_TYPES,
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


def _entry_intent(*, symbol: str, quantity: float, price: float, now: str) -> dict:
    """The cycle's entry order, from its own inputs — built once to send and once more by the
    pre-order gate to check that what is sent is this (PR2b)."""
    return build_live_order_intent(
        {"direction": "LONG", "entry_price": price,
         "stop_loss": round(price * (1 - STOP_DISTANCE_PCT / 100), 2),
         "strategy_id": "signed_testnet_cycle"},
        symbol=symbol, quantity=quantity, notional_usdt=round(price * quantity, 8), now=now,
    )


def plan_cycle(*, symbol: str, quantity: float, root: Path | None, now: str,
               timeout_seconds: int = 10) -> dict:
    """What this cycle would do, and what the machine's posture says about it. Sends nothing.

    The posture is judged even when the reference price cannot be read: a door that reported only
    "no price" would hide the stage, the switch and both halts — the refusals an operator is
    actually asking about (review of #877)."""
    adapter = testnet.select_testnet_order_adapter(now=now, root=root)
    stage = resolve_execution_stage(root, now=now)
    control = (ControlStore(root) if root is not None else ControlStore.default()).load()
    limits = LiveOrderLimits.from_env()
    price, price_error = None, None
    try:
        price = _price(symbol, now=now, root=root, timeout_seconds=timeout_seconds)
    except MvpRuntimeError as exc:
        price_error = getattr(exc, "reason_code", "TESTNET_PRICE_UNREADABLE")
    if price is None:
        posture = testnet.evaluate_testnet_order_guard(
            {"status": "ORDER_INTENT_CREATED", "symbol": symbol, "quantity": quantity,
             "order_notional_usdt": 0.0, "reduce_only": False, "connectivity_test": False},
            gate_open=bool(getattr(adapter, "network_egress", False)),
            runtime_active=control.execution_allowed,
            manual_kill_switch=limits.manual_kill_switch,
            submitted_today=testnet.count_testnet_today(root),
            execution_stage=stage,
        )
        posture["blocks"] = [*posture["blocks"], f"no usable reference price for {symbol} ({price_error})"]
        posture["approved"] = False
        posture["status"] = testnet.TESTNET_STATUS_BLOCKED
        return {"adapter": adapter, "stage": stage, "intent": None, "price": None,
                "price_error": price_error, "verdict": posture}
    intent = _entry_intent(symbol=symbol, quantity=quantity, price=price, now=now)
    guard_kwargs = dict(
        gate_open=bool(getattr(adapter, "network_egress", False)),
        # `execution_allowed`, not `trading_allowed`: a killed or paused runtime sends nothing
        # anywhere, but the live ARM is a bigger act than a testnet rehearsal — requiring it here
        # would mean arming real trading in order to earn the evidence for arming real trading.
        runtime_active=control.execution_allowed,
        manual_kill_switch=limits.manual_kill_switch,
        submitted_today=testnet.count_testnet_today(root),
        execution_stage=stage,
    )
    verdict = testnet.evaluate_testnet_order_guard(intent, **guard_kwargs)
    return {"adapter": adapter, "stage": stage, "intent": intent, "price": price,
            "price_error": None, "verdict": verdict, "guard_kwargs": guard_kwargs}


def run_cycle(*, symbol: str, quantity: float, operator: str, reason: str,
              root: Path | None = None, now: str | None = None, timeout_seconds: int = 10) -> dict:
    """One cycle end to end, recorded. Refuses before the venue if anything is not in order."""
    now = now or timeutil.utc_now_iso()
    planned = plan_cycle(symbol=symbol, quantity=quantity, root=root, now=now,
                         timeout_seconds=timeout_seconds)
    adapter, stage, intent, price = (planned["adapter"], planned["stage"],
                                     planned["intent"], planned["price"])
    verdict = planned["verdict"]
    if not verdict["approved"]:
        raise _Refusal("TESTNET_GUARD_REFUSED",
                       "the testnet order guard refused: " + "; ".join(verdict["blocks"] or verdict["repairs"]))
    # The operator-visible SAFETY_GATE notice, before anything is sent.
    gate_banners(testnet_order_adapter=adapter)
    if not getattr(adapter, "network_egress", False):
        raise _Refusal("TESTNET_TRADING_OFF",
                       f"{testnet.TESTNET_TRADING_ENV} is not '{testnet.REAL_TESTNET_TRADING}'; "
                       "this run would prove nothing about signing or the venue")

    counter = testnet.TestnetOrderCounter(root=root,
                                          authorization=getattr(adapter, "_authorization", None))
    # Seeded with the venue-facing time AND how many cycles this machine has recorded, so two runs
    # in the same second cannot collide into one id (review of #877).
    cycle_id = integrity.short_id("testnetcycle", {
        "symbol": symbol, "quantity": f"{quantity:.8f}", "at": now,
        "seq": len(testnet_evidence.read_cycles(root)),
    })
    # The pre-order gate (PR2b, decision 20): the testnet guard re-run on the same facts, the order
    # rebuilt from the cycle's inputs, sealed and recorded on the testnet venue's store before the
    # entry leaves. A refusal here sends nothing and records no cycle.
    snapshot = testnet.gate_testnet_order(
        intent,
        expected_intent=_entry_intent(symbol=symbol, quantity=quantity, price=price, now=now),
        guard_kwargs=planned["guard_kwargs"], stage=stage, cycle_id=cycle_id, now=now,
    )
    if not snapshot["approved"]:
        raise _Refusal("TESTNET_PRE_ORDER_GATE_REFUSED",
                       "the pre-order gate refused: " + ", ".join(snapshot["failed_checks"]))
    intent = pre_order_gate.bind_intent(intent, snapshot)
    snapshot_store = testnet.testnet_snapshot_store(
        root=root, authorization=getattr(adapter, "_authorization", None))
    try:
        pre_order_gate.verify_and_persist(intent, snapshot, store=snapshot_store)
    except Exception as exc:  # noqa: BLE001 — before the venue: a refusal, never an escape
        raise _Refusal("TESTNET_SNAPSHOT_NOT_RECORDED",
                       f"the pre-order snapshot was not recorded "
                       f"({getattr(exc, 'reason_code', type(exc).__name__)}); nothing was sent") from exc

    started_at = now
    legs: list[dict] = []
    entry_row: dict = {}
    exit_row: dict = {}
    reconciliation: dict = {"status": "NOT_CHECKED"}

    def _record(failure: str | None = None) -> dict:
        """What is known so far, written down. An interrupted cycle is evidence of what happened —
        and the row it leaves is what says the position may still be open (review of #877)."""
        row = testnet_evidence.build_cycle_record(
            cycle_id=cycle_id, symbol=symbol, entry=entry_row, protective_legs=legs,
            exit_result=exit_row, position_reconciliation=reconciliation,
            adapter_tool_id=getattr(adapter, "tool_id", "unknown"),
            base_url_host=testnet.ALLOWED_TESTNET_HOSTS and sorted(testnet.ALLOWED_TESTNET_HOSTS)[0],
            operator=operator, reason=reason, failure=failure,
            started_at=started_at, completed_at=timeutil.utc_now_iso(),
        )
        testnet_evidence.append_cycle(row, root)
        return row

    # The entry's own refusal before the venue: nothing left, so it counts no order and records no
    # cycle. Kept apart from a SubmitRefused later in the cycle, when the entry has already left.
    entry_refused: SubmitRefused | None = None
    try:
        # 1. The entry. Reconciled by the same pure function the live path uses, so the two can
        #    never disagree about what RECONCILED means.
        try:
            entry = submit_and_reconcile(intent, adapter=adapter, guard_verdict=verdict, now=now,
                                         timeout_seconds=timeout_seconds,
                                         risk_snapshot=snapshot, snapshot_store=snapshot_store)
        except SubmitRefused as exc:
            entry_refused = exc
            raise
        finally:
            if entry_refused is None:
                counter.record_submission()
        entry_row = {
            "risk_snapshot_sha256": entry.get("risk_snapshot_sha256"),
            "reconcile_status": entry.get("reconcile_status"),
            "mismatches": entry.get("mismatches") or [],
            "exchange_order_id": entry.get("exchange_order_id"),
            "client_order_id": entry.get("client_order_id"),
            "fill": entry.get("fill") or {},
        }

        # 2. The protective legs, each at the endpoint its own type belongs to — the part an
        #    entry-only rehearsal never reaches and the part that broke on 2026-08-02.
        filled_qty = float((entry_row["fill"] or {}).get("executed_qty") or 0.0) or quantity
        stop_level = round(price * (1 - STOP_DISTANCE_PCT / 100), 2)
        for leg, side, level in (
            ("SL", "SELL", stop_level),
            ("TP", "SELL", round(price * (1 + TARGET_DISTANCE_PCT / 100), 2)),
        ):
            leg_intent = build_bracket_intent(symbol=symbol, leg=leg, side=side, price=level,
                                              working_type="MARK_PRICE", position_seed=cycle_id,
                                              quantity=filled_qty)
            # Which endpoint this leg really belongs to, computed the same way `place_bracket_leg`
            # computes it. Recording `algo: True` for a plain LIMIT both made a false claim and
            # sent its cancel to the wrong endpoint, which is how a leg is left resting (#877).
            is_algo = str(leg_intent.get("order_type_exchange")) in CONDITIONAL_ORDER_TYPES
            placed = place_bracket_leg(leg_intent, adapter=adapter, timeout_seconds=timeout_seconds)
            # 3. Withdraw it: a leg left resting at the venue is the 2026-08-03 ghost stop.
            withdrawn, cancel_error = None, None
            if placed.get("client_order_id"):
                try:
                    cancelled = adapter.cancel_order(symbol, str(placed["client_order_id"]),
                                                     timeout_seconds=timeout_seconds, algo=is_algo)
                    withdrawn = (cancelled is not None
                                 or placed.get("status") not in BRACKET_RESTING_STATUSES)
                except MvpRuntimeError as exc:
                    withdrawn, cancel_error = False, exc.reason_code
            legs.append({
                "leg": leg,
                "algo": is_algo,
                "order_type": placed.get("order_type"),
                "observed_status": placed.get("status"),
                "placed": bool(placed.get("placed")),
                "client_order_id": placed.get("client_order_id"),
                "exchange_order_id": placed.get("exchange_order_id"),
                "error": placed.get("error"),
                "withdrawn": withdrawn,
                "cancel_error": cancel_error,
            })

        # 4. The exit: reduceOnly, reconciled like the entry.
        exit_intent = build_live_order_intent(
            # Its own `position_id`, so its identity key — and therefore its client order id —
            # differs from the entry's. Without it the two intents hashed the same inputs (symbol,
            # direction, strategy, `now`) into ONE client order id: the venue would refuse the
            # exit as a duplicate (-4116), the reconcile would read the ENTRY back under that id,
            # and the cycle could never complete — the LIVE climb's evidence path, broken at the
            # source. Found by the PR2 investigation, 2026-09-16; the live path derives its close
            # ids from the position and the reason for the same reason (live_leg.execute_live_exit).
            {"direction": "LONG", "entry_price": price, "stop_loss": stop_level,
             "strategy_id": "signed_testnet_cycle", "position_id": f"{cycle_id}:exit"},
            symbol=symbol, quantity=filled_qty, notional_usdt=round(price * filled_qty, 8), now=now,
            reduce_only=True, close_reason="signed_testnet_cycle",
        )
        exit_verdict = testnet.evaluate_testnet_order_guard(
            exit_intent,
            gate_open=bool(getattr(adapter, "network_egress", False)),
            runtime_active=True,          # a close is not gated by a halt, here as on the live path
            manual_kill_switch=False,
            submitted_today=0,
            execution_stage=stage,
        )
        try:
            exit_result = submit_and_reconcile(exit_intent, adapter=adapter,
                                               guard_verdict=exit_verdict, now=now,
                                               timeout_seconds=timeout_seconds)
        finally:
            counter.record_submission()
        exit_row = {
            "reconcile_status": exit_result.get("reconcile_status"),
            "mismatches": exit_result.get("mismatches") or [],
            "reduce_only": True,
            "exchange_order_id": exit_result.get("exchange_order_id"),
            "fill": exit_result.get("fill") or {},
        }

        # 5. The venue's own position view, ASKED — not the exit's status under a second name.
        positions = adapter.open_positions(symbol, timeout_seconds=timeout_seconds)
        held = [p for p in positions if abs(float(p.get("positionAmt") or 0.0)) > 0]
        reconciliation = {
            "status": testnet_evidence.RECONCILED if not held else "DRIFT",
            "checked_at": timeutil.utc_now_iso(),
            "venue_positions": [{"symbol": p.get("symbol"), "positionAmt": str(p.get("positionAmt"))}
                                for p in positions],
        }
    except Exception as exc:  # noqa: BLE001 — the venue has already been reached; record it
        if exc is entry_refused:
            raise _Refusal(entry_refused.reason_code,
                           f"the entry was refused before the venue ({entry_refused}); nothing was "
                           "sent and no cycle is recorded") from exc
        reason_code = getattr(exc, "reason_code", type(exc).__name__)
        record = _record(failure=reason_code)
        raise _Refusal(
            "TESTNET_CYCLE_INCOMPLETE",
            f"the cycle stopped at {reason_code}; cycle {cycle_id} is recorded with what is known "
            f"({'; '.join(testnet_evidence.cycle_findings(record)) or 'no findings'}) - check the "
            "venue for anything still open",
        ) from exc

    record = _record()
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
    parser.add_argument("--operator", help="operator identity (recorded on the cycle row)")
    parser.add_argument("--reason", help="why this cycle was run (recorded on the cycle row)")
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
            gate_banners(testnet_order_adapter=planned["adapter"])
            sys.stdout.write(
                f"symbol        : {args.symbol} x {args.quantity} "
                f"(~{(planned['intent'] or {}).get('order_notional_usdt')} USDT)\n"
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
        # After the read-only modes (--list, --plan) and before the first write: a host-side root
        # run would leave the testnet counter and the evidence file owned by a uid the services
        # cannot write again. Same placement as the promotion door's.
        assert_not_foreign_root_run(args.root)
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
