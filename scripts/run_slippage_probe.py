#!/usr/bin/env python3
"""Operator tool: the stop-slippage probe (proposal §5, Thomas 2026-08-11).

    # 1) Read the board: plan, cells, the recorded sample, the §5-5 readiness line.
    python -m scripts.run_slippage_probe --status

    # 2) ASK Thomas for one batch (stores the PENDING approval; places nothing).
    #    --symbols overrides the approved default three (comma list; second-batch
    #    approval, Thomas 2026-08-11) — the content hash then binds THAT set.
    python -m scripts.run_slippage_probe --request [--symbols "BNBUSDT,DOGEUSDT"]

    # 3) Thomas answers /approve <id> on the verified control channel, then the plan
    #    store is written (still places nothing). A --symbols request must repeat the
    #    same set here: the confirm re-derives the batch and verifies it against the
    #    approval's content hash, so a different set refuses.
    python -m scripts.run_slippage_probe --confirm --approval-id approval_abc123 \
        [--symbols "BNBUSDT,DOGEUSDT"]

    # 4) Operator-only, one probe per invocation, synchronous until the stop fills or
    #    the timeout closes it at market:
    python -m scripts.run_slippage_probe --fire --symbol BTCUSDT

``--fire`` is real money and every run of it is Thomas's. It hard-refuses unless
``MVP_LIVE_TRADING=real`` selects the capable adapter — the inert dry-run selector is
surfaced as ``PROBE_LIVE_TRADING_OFF``, never as a silent dry run — and the order passes
the SAME final guard the canary door uses (canary mode: the deliberate one-at-a-time
phrase, ``MVP_LIVE_CANARY_CONFIRMATION``, authorizes it; the promotion gate is the one
check that does not apply, exactly as it does not apply to a canary). The daily-loss
breaker, the bracket breaker, the R-based risk guard, the registered budget's caps and
allowlist, and the daily order counter all apply — a probe is a smaller real order, not a
different kind of one.

The probe position is booked in the ordinary live book with the stop leg's client id, so
the scheduler's live leg sees a tracked, protected position rather than venue drift — if
this process dies mid-probe, the cycle settles or times the position out through the same
helpers, and the next ``--fire`` resolves the cell from the ledger. Settlement is the
EXISTING #683 path (`settle_venue_closed_position` / `execute_live_exit`), which stamps
``stop_price`` and measures ``stop_slippage_bps`` at source; this tool adds no
measurement code.
"""

from __future__ import annotations

import argparse
import json
import sys
import time as time_mod
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.approval_store import STORE_REL as APPROVAL_STORE_REL  # noqa: E402
from runtime.mvp_runtime.approval_store import ApprovalStore  # noqa: E402
from runtime.mvp_runtime.audit import build_approval_request_audit  # noqa: E402
from runtime.mvp_runtime.cli_common import (  # noqa: E402
    EXIT_BLOCKED,
    EXIT_OK,
    EXIT_USAGE,
    force_utf8_io,
    gate_banners,
)
from runtime.mvp_runtime.control import ControlStore  # noqa: E402
from runtime.mvp_runtime.crypto import live_execution, live_governance, live_leg, probe  # noqa: E402
from runtime.mvp_runtime.crypto import live_promotion  # noqa: E402
from runtime.mvp_runtime.crypto.account import read_account, select_account_feed  # noqa: E402
from runtime.mvp_runtime.crypto.features import latest_feature_row  # noqa: E402
from runtime.mvp_runtime.crypto.guards import run_risk_guard  # noqa: E402
from runtime.mvp_runtime.crypto.live_entry import BRACKET_WORKING_TYPE  # noqa: E402
from runtime.mvp_runtime.crypto.live_filters import read_symbol_filters  # noqa: E402
from runtime.mvp_runtime.crypto.live_order import (  # noqa: E402
    bracket_breaker_status,
    build_live_order_intent,
    count_today,
    evaluate_live_order_guard,
    render_guard_text,
    resolve_live_order_limits,
    select_live_order_counter,
)
from runtime.mvp_runtime.crypto.live_pnl import (  # noqa: E402
    live_outcomes_for_analysis,
    live_risk_snapshot,
    read_live_outcomes,
    select_live_ledger,
    stop_slippage_observations,
    venue_daily_realized_net,
)
from runtime.mvp_runtime.crypto.live_position import (  # noqa: E402
    build_live_position,
    compute_open_notional_usdt,
    load_open_live_position,
    select_live_position_store,
)
from runtime.mvp_runtime.crypto.market_data import (  # noqa: E402
    collect_market_data,
    read_reference_price,
    select_market_data_collector,
)
from runtime.mvp_runtime.crypto.risk_limits import resolve_risk_limits  # noqa: E402
from runtime.mvp_runtime.errors import MvpRuntimeError  # noqa: E402
from runtime.mvp_runtime.state_guard import assert_not_foreign_root_run  # noqa: E402
from runtime.mvp_runtime.events import stamped_event  # noqa: E402
from runtime.mvp_runtime.store import LedgerStore  # noqa: E402

# Enough 4h bars for the atr_percentile window (100) plus indicator warm-up; the venue
# caps a candle read well above this.
REGIME_CANDLE_LIMIT = 200

# How often the poll loop asks the venue about the resting stop. Coarse on purpose: the
# stop is a venue-side order and needs no supervision — the poll only decides when to
# settle, and the #683 row is measured from the venue's own fill whenever that happens.
DEFAULT_POLL_SECONDS = 30.0


class _Refusal(Exception):
    """A typed --fire refusal: (reason_code, message). Nothing was sent."""

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


def _parse_symbols(text: str | None) -> tuple[str, ...] | None:
    """The --symbols comma list, split and stripped ONLY. None when the flag is absent
    (= the approved default set). Validation is `build_batch_params`'s alone — an empty
    or duplicate entry must refuse there with its typed code, never be dropped here."""
    if text is None:
        return None
    return tuple(part.strip() for part in text.split(","))


# --- small seams (each delegates to the existing surface; tests monkeypatch these) -----

def _read_regime(symbol: str, *, now: str, root: Path | None, timeout_seconds: int) -> str:
    """The symbol's CURRENT regime, measured on the gated market-data collector.

    Synthetic data refuses: a regime judged off the mock's hash-walk would file the probe
    under a coin flip, and the grid exists to prevent exactly that."""
    collector = select_market_data_collector(now=now, root=root)
    snapshot, _record = collect_market_data(
        symbol, probe.REGIME_TIMEFRAME, collector=collector, now=now,
        limit=REGIME_CANDLE_LIMIT, timeout_seconds=timeout_seconds,
    )
    if snapshot.get("is_synthetic"):
        raise _Refusal(
            probe.PROBE_REGIME_UNREADABLE,
            "market data is synthetic (mock collector); the regime cannot be judged",
        )
    row = latest_feature_row(snapshot)
    return probe.regime_of(row.get(probe.REGIME_FEATURE))


def _read_filters(symbol: str, *, now: str, root: Path | None, timeout_seconds: int) -> Any:
    collector = select_market_data_collector(now=now, root=root)
    filters, reason = read_symbol_filters(collector, symbol, timeout_seconds=timeout_seconds)
    if filters is None:
        raise _Refusal(probe.PROBE_FILTERS_UNAVAILABLE, f"no venue filters for {symbol} ({reason})")
    return filters


def _read_price(symbol: str, *, now: str, root: Path | None, timeout_seconds: int) -> float:
    collector = select_market_data_collector(now=now, root=root)
    price, reason = read_reference_price(
        symbol, collector=collector, now=now, timeout_seconds=timeout_seconds,
    )
    if price is None:
        raise _Refusal(probe.PROBE_PRICE_UNREADABLE, f"no usable reference price for {symbol} ({reason})")
    return float(price)


def _audit_order(governance, submit_result, *, guard, now, root) -> str | None:
    """The post-order half of EXECUTE_AND_REPORT, best-effort past the venue (the canary
    door's posture: the money already moved, so a failure here is reported, never allowed
    to imply the order did not happen)."""
    try:
        event, _sha = live_governance.report_live_order(
            governance, submit_result, guard_verdict=guard, now=now, repo_root=root,
        )
        LedgerStore.default(root).append_audit_events([event])
        return None
    except Exception as exc:  # noqa: BLE001 — past the point of no return; report, never raise
        return getattr(exc, "reason_code", type(exc).__name__)


# --- verbs -----------------------------------------------------------------------------

def run_status(*, root: Path | None) -> int:
    plan = probe.read_plan(root)
    outcomes = read_live_outcomes(root)
    full_sample = stop_slippage_observations(outcomes)
    probe_sample = probe.probe_slippage_sample(outcomes)
    readiness = probe.decision_readiness(full_sample)

    if plan is None:
        print("plan      : none confirmed (ask with --request, then --confirm)")
    else:
        print(f"plan      : {plan['batch_id']} {plan['status']} "
              f"(approval {plan['approval_id']}, created {plan['created_at']})")
        p = plan["params"]
        print(f"params    : n={p['n']} {'/'.join(p['symbols'])} stop={p['stop_bps']}bps "
              f"{p['regime_feature']}@{p['regime_timeframe']} split {p['regime_split']} "
              f"timeout={p['timeout_minutes']}m cap={p['budget_cap_usdt']} USDT "
              f"(worst case {probe.worst_case_loss_usdt(p)} USDT)")
        for index, cell in enumerate(plan["cells"]):
            extra = ""
            if cell["status"] == probe.CELL_FILLED:
                extra = f"  slippage={cell['stop_slippage_bps']}bps ({cell['outcome_id']})"
            elif cell["status"] == probe.CELL_TIMEOUT:
                extra = f"  ({cell['outcome_id']})"
            elif cell["status"] == probe.CELL_OPEN:
                extra = f"  position={cell['position_id']} opened={cell['opened_at']}"
            elif cell["note"]:
                extra = f"  note: {cell['note']}"
            print(f"  cell {index:2d} : {cell['symbol']:8} {cell['regime']:8} #{cell['repeat']} "
                  f"{cell['status']:7}{extra}")

    print(f"sample    : {len(probe_sample)} probe row(s), {len(full_sample)} recorded stop "
          "close(s) total (pre-#683 stops are absent, not zero — REMAINING_WORK §C keeps them)")
    for row in probe_sample:
        print(f"  {row['closed_at_utc']}  {row['symbol']:8} {row['stop_slippage_bps']:+8.2f} bps "
              f"({row['outcome_id']})")
    if readiness["ready"]:
        print(f"readiness : n={readiness['sample_n']} >= {readiness['decision_floor']} — the "
              f"§5-5 re-decision is OPEN: measured median {readiness['median_bps']} bps "
              f"(upper quartile {readiness['upper_quartile_bps']}) would replace the interim "
              f"DEFAULT_STOP_SLIPPAGE_BPS={readiness['interim_default_bps']}")
    else:
        print(f"readiness : n={readiness['sample_n']} < {readiness['decision_floor']} — the "
              f"interim DEFAULT_STOP_SLIPPAGE_BPS={readiness['interim_default_bps']} stands "
              f"(median so far: {readiness['median_bps']})")
    return EXIT_OK


def run_request(
    *, root: Path | None, now: str | None = None, symbols: tuple[str, ...] | None = None
) -> int:
    """Build + store + audit the R9 ask for one batch (the promotion script's pattern).

    ``symbols`` is the request-time set (None = the approved default three); the batch is
    built HERE so an invalid set refuses, typed, before anything is stored."""
    now = now or timeutil.utc_now_iso()
    params = probe.build_batch_params(symbols=symbols) if symbols is not None else None
    prepared = probe.request_probe_batch(params=params, now=now, repo_root=root)
    store = ApprovalStore(root / APPROVAL_STORE_REL) if root is not None else ApprovalStore.default()
    store.append_permission_decision(prepared["permission_decision"])
    store.append([prepared["approval_request"]])
    ledger = LedgerStore.default(root)
    try:
        ledger.append_audit_events(build_approval_request_audit(
            prepared["approval_request"], now=now, genesis_previous_hash=ledger.last_audit_hash(),
        ))
    except MvpRuntimeError as exc:
        sys.stderr.write(f"WARNING: request audit failed ({exc.reason_code}); the request stands\n")
    request = prepared["approval_request"]
    from runtime.mvp_runtime import approval as approval_mod  # noqa: E402 (message renderer)
    print(approval_mod.request_message(request, prepared["permission_decision"], history=None))
    confirm_cmd = f"--confirm --approval-id {request['approval_id']}"
    if symbols is not None:
        # The confirm re-derives the batch, so a --symbols ask must be confirmed with
        # the SAME set — hand the operator the exact command rather than a mismatch.
        confirm_cmd += " --symbols \"{}\"".format(",".join(prepared["params"]["symbols"]))
    print(f"\nSTORED: {request['approval_id']} is PENDING until {request['validity']['expires_at']}.")
    print("Thomas answers /approve <id> or /reject <id> on the verified control channel; then "
          f"run {confirm_cmd}.")
    return EXIT_OK


def run_confirm(
    *,
    approval_id: str,
    root: Path | None,
    now: str | None = None,
    symbols: tuple[str, ...] | None = None,
) -> int:
    """Verify the approval against the re-derived batch and write the plan store.

    ``symbols`` must repeat the set the request named (None = the default three): the
    approval's content hash binds one exact batch, so a different set refuses with
    APPROVAL_CONTENT_MISMATCH rather than confirming something Thomas never saw."""
    now = now or timeutil.utc_now_iso()
    params = probe.build_batch_params(symbols=symbols) if symbols is not None else None
    store = ApprovalStore(root / APPROVAL_STORE_REL) if root is not None else ApprovalStore.default()
    plan = probe.confirm_probe_batch(store.get(approval_id), params=params, root=root, now=now)
    print(f"CONFIRMED: plan {plan['batch_id']} is {plan['status']} with {len(plan['cells'])} "
          f"cells under approval {plan['approval_id']}. No orders were placed; fire one at a "
          "time with --fire --symbol <SYMBOL>.")
    return EXIT_OK


# The abandonment record, on the CONTROL ledger like the promotion door's summaries: the plan
# file keeps its closed key set and records only the status flip; the WHY lives here, where a
# reason outlives the file that motivated it.
PROBE_ABANDON_EVENT_TYPE = "crypto_probe_batch_abandoned_event.v0"


def run_abandon(*, reason: str, root: Path | None = None, now: str | None = None) -> int:
    """Retire the ACTIVE batch early — the PROBE_PLAN_EXISTS refusal's own 'resolve' verb."""
    now = now or timeutil.utc_now_iso()
    plan = probe.read_plan(root)
    if plan is None:
        raise _Refusal(probe.PROBE_PLAN_MISSING, "no confirmed probe plan; nothing to abandon")
    # The same reconcile --fire's preamble does, for the same reason one gate over: a
    # crashed --fire must not wedge the RETIREMENT either. Measured the day this shipped:
    # the operator hand-closed the probe position at the venue, and without this the only
    # verb that could clear the stale OPEN cell was the one that then places a NEW probe.
    # Still OPEN after the reconcile = a position genuinely on the book, and the refusal
    # below is then the honest one.
    opened = probe.open_cell_index(plan)
    if opened is not None:
        open_symbol = plan["cells"][opened]["symbol"]
        plan, resolution = probe.resolve_open_cell(
            plan,
            outcomes=read_live_outcomes(root),
            position_open=load_open_live_position(open_symbol, root) is not None,
            now=now,
        )
        if resolution is not None:
            probe.write_plan(plan, root)
            print(f"resolved  : cell {resolution['index']} -> {resolution['status']} "
                  f"(outcome {resolution['outcome_id']})")
    plan = probe.abandon_plan(reason=reason, now=now, root=root)
    counts: dict[str, int] = {}
    for cell in plan["cells"]:
        counts[cell["status"]] = counts.get(cell["status"], 0) + 1
    summary = {
        "batch_id": plan["batch_id"],
        "approval_id": plan["approval_id"],
        "reason": reason,
        "cells": counts,
        "abandoned_at": now,
    }
    LedgerStore.default(root).append_control(stamped_event(PROBE_ABANDON_EVENT_TYPE, **summary))
    kept = counts.get(probe.CELL_FILLED, 0)
    print(f"ABANDONED: plan {plan['batch_id']} retired early ({kept} filled cell(s) keep their "
          f"sample rows; {counts.get(probe.CELL_EMPTY, 0)} cell(s) never happen). The next "
          "batch may now --request/--confirm.")
    return EXIT_OK


def run_fire(
    *,
    root: Path | None,
    symbol: str,
    timeout_seconds: int = 10,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    sleep: Any = time_mod.sleep,
    clock: Any = time_mod.monotonic,
) -> int:
    """Place ONE probe synchronously: entry, resting stop, settle (or timeout-close).

    Every refusal below is typed and happens before any order; past the entry submit the
    posture flips to the canary door's — report everything, imply nothing."""
    symbol = str(symbol).strip().upper()
    now = timeutil.utc_now_iso()

    # 0. Ordered refusals. Nothing below the guard block touches the venue.
    assert_not_foreign_root_run(root)

    plan = probe.read_plan(root)
    if plan is None:
        raise _Refusal(probe.PROBE_PLAN_MISSING, "no confirmed probe plan (run --request, then --confirm)")

    # Reconcile a leftover OPEN cell against the book and the ledger before refusing on
    # it: a crashed --fire must not wedge the batch once the cycle has settled its
    # position. Still OPEN after that = a probe genuinely in flight.
    opened = probe.open_cell_index(plan)
    if opened is not None:
        open_symbol = plan["cells"][opened]["symbol"]
        plan, resolution = probe.resolve_open_cell(
            plan,
            outcomes=read_live_outcomes(root),
            position_open=load_open_live_position(open_symbol, root) is not None,
            now=now,
        )
        if resolution is not None:
            plan = probe.write_plan(plan, root)
            print(f"resolved  : cell {resolution['index']} -> {resolution['status']} "
                  f"(outcome {resolution['outcome_id']})")

    adapter = live_execution.select_order_adapter(now=now, root=root)
    if not bool(getattr(adapter, "network_egress", False)):
        # The selectors are inert: surface it, never dry-run through them.
        raise _Refusal(
            probe.PROBE_LIVE_TRADING_OFF,
            "live trading is not opted in on this machine (MVP_LIVE_TRADING is not 'real'); "
            "a probe places a REAL order and refuses to pretend otherwise",
        )
    gate_banners(live_order_adapter=adapter)

    regime = _read_regime(symbol, now=now, root=root, timeout_seconds=timeout_seconds)
    cell_index = probe.select_cell(plan, symbol=symbol, regime=regime)
    cell = plan["cells"][cell_index]
    print(f"cell      : {cell_index} ({cell['symbol']} {cell['regime']} #{cell['repeat']}), "
          f"measured {probe.REGIME_FEATURE}@{probe.REGIME_TIMEFRAME} regime = {regime}")

    limits, budget = resolve_live_order_limits(root, now=now)
    control_state = ControlStore(root).load() if root is not None else ControlStore.default().load()
    # `trading_allowed`, live_route's bar for entries: a runtime whose trading arm is down
    # must hold probes too — a probe is an entry, not a close.
    runtime_active = control_state.trading_allowed

    snapshot, account_use = read_account(timeout_seconds=timeout_seconds, root=root)
    if snapshot is None:
        raise _Refusal(
            probe.PROBE_ACCOUNT_UNREADABLE,
            "cannot read the venue account, so exposure and the daily loss are unknown "
            f"({account_use.get('degraded_reason_code')})",
        )
    open_notional = compute_open_notional_usdt(snapshot, at_cap=limits.max_open_notional_usdt)

    # §4-4: the breakers apply to probes unchanged. The daily-loss figure is the venue's
    # own (stricter of calendar day / rolling 24h), the bracket breaker is the counter the
    # naked-entry loop latches, and the R-based guard reads the same live rows the cycle
    # meters — all three refuse BEFORE the guard so the refusal names the breaker.
    risk = live_risk_snapshot(
        limit_usdt=limits.daily_loss_limit_usdt, root=root, now=now,
        venue_realized_pnl_usdt=venue_daily_realized_net(snapshot.realized_windows),
    )
    if risk["daily_loss_limit_breached"]:
        raise _Refusal(
            probe.PROBE_DAILY_LOSS_BREAKER,
            f"daily loss breaker: realized {risk['daily_realized_pnl_usdt']} USDT against "
            f"limit {risk['daily_loss_limit_usdt']} (source {risk['pnl_source']})",
        )
    breaker = bracket_breaker_status(root)
    if breaker["tripped"]:
        raise _Refusal(
            probe.PROBE_BRACKET_BREAKER,
            f"bracket-failure breaker is tripped ({breaker['consecutive']}/{breaker['limit']}); "
            "clear it (scripts/clear_bracket_breaker.py) after reading why",
        )
    readable, _excluded = live_outcomes_for_analysis(read_live_outcomes(root))
    guard_verdict = run_risk_guard(readable, now=now, limits=resolve_risk_limits(root, now=now))
    if not guard_verdict["allow_new_position"]:
        raise _Refusal(
            probe.PROBE_RISK_GUARD_BLOCKED,
            f"risk guard refuses new positions: {', '.join(guard_verdict['problems'])}",
        )

    filters = _read_filters(symbol, now=now, root=root, timeout_seconds=timeout_seconds)
    price = _read_price(symbol, now=now, root=root, timeout_seconds=timeout_seconds)
    quantity, notional = probe.probe_quantity(filters, price)
    cap = float(plan["params"]["per_probe_notional_cap_usdt"])
    if notional > cap:
        raise _Refusal(
            probe.PROBE_NOTIONAL_ABOVE_PLAN,
            f"venue minimum prices this probe at {notional} USDT, above the {cap} USDT "
            "per-probe ceiling the approval was budgeted on; a wider batch needs a new ask",
        )

    stop_estimate = probe.probe_stop_price(price, filters.tick_size)
    sid = probe.probe_strategy_id(plan["batch_id"])
    intent = build_live_order_intent(
        # Lineage-free on purpose: strategy_id is the probe marker, and no candidate /
        # generation / rule hash exists to ride to the outcome row.
        {"direction": probe.PROBE_DIRECTION, "entry_price": price, "stop_loss": stop_estimate,
         "strategy_id": sid},
        symbol=symbol, quantity=quantity, notional_usdt=notional, now=now,
    )
    verdict = evaluate_live_order_guard(
        intent,
        gate_open=True,  # the capable adapter above IS the opt-in
        runtime_active=runtime_active,
        daily_loss_breached=bool(risk["daily_loss_limit_breached"]),
        clean_canary_orders=live_promotion.clean_canary_order_count(root)[0],
        submitted_today=count_today(root),
        current_open_notional_usdt=open_notional,
        limits=limits,
        budget_registered=bool(budget.get("valid")),
        allowed_symbols=budget.get("symbol_allowlist") or (),
        # Canary mode: a probe is the guard's own definition of a deliberate operator
        # order — authorized by the canary phrase, exempt only from the promotion gate.
        canary=True,
    )
    print(render_guard_text(verdict))
    if not verdict["approved"]:
        raise _Refusal(probe.PROBE_GUARD_REFUSED, "the final order guard refused; fix what it names")

    # Governance BEFORE the order: a refusal here costs nothing (the canary door's rule).
    governance = live_governance.prepare_live_order_governance(
        intent, purpose=live_governance.PURPOSE_CANARY, now=now, repo_root=root,
    )

    # 1. Claim the cell BEFORE the send: a crash between the two leaves the cell OPEN,
    #    which is the honest state (an order may be at the venue) and what keeps
    #    one-probe-at-a-time enforceable across processes.
    plan = probe.mark_cell(
        plan, cell_index, status=probe.CELL_OPEN, now=now,
        opened_at=now, entry_client_order_id=intent["client_order_id"],
    )
    plan = probe.write_plan(plan, root)

    position_store = select_live_position_store(now=now, root=root)
    ledger = select_live_ledger(now=now, root=root)
    counter = select_live_order_counter(now=now, root=root)
    counter_error = None
    try:
        entry = live_execution.submit_and_reconcile(
            intent, adapter=adapter, guard_verdict=verdict, now=now,
            timeout_seconds=timeout_seconds,
        )
    finally:
        # In `finally`: an ambiguous submit may have reached the venue and must consume
        # daily budget (the canary door's rule, verbatim).
        try:
            counter.record_submission()
        except Exception as exc:  # noqa: BLE001 — past the venue; report, never raise
            counter_error = getattr(exc, "reason_code", type(exc).__name__)
    audit_error = _audit_order(governance, entry, guard=verdict, now=now, root=root)

    fill = entry.get("fill") or {}
    filled_qty = float(fill.get("executed_qty") or 0.0)
    fill_price = float(fill.get("avg_price") or 0.0)
    confirmed = entry["reconcile_status"] == live_promotion.RECONCILED and filled_qty > 0 and fill_price > 0

    def _fail_cell(note: str) -> None:
        nonlocal plan
        plan = probe.write_plan(
            probe.mark_cell(plan, cell_index, status=probe.CELL_EMPTY,
                            now=timeutil.utc_now_iso(), note=note),
            root,
        )

    if not confirmed:
        if filled_qty > 0:
            # Real exposure the venue reported, with no stop yet: close it now (rule 2).
            # The reference price stands in when the fill's own price will not parse —
            # the reduceOnly close needs a bookable record, not an exact basis.
            naked = build_live_position(
                symbol=symbol, direction=probe.PROBE_DIRECTION, quantity=filled_qty,
                entry_price=fill_price if fill_price > 0 else price, opened_at=now,
                entry_client_order_id=entry["client_order_id"],
                entry_exchange_order_id=entry["exchange_order_id"], strategy_id=sid,
            )
            closed = live_leg.execute_live_exit(
                naked, adapter=adapter, position_store=position_store, ledger=ledger,
                gate_open=True, limits=limits, close_reason=live_leg.CLOSE_REASON_NAKED,
                now=timeutil.utc_now_iso(), timeout_seconds=timeout_seconds,
            )
            _fail_cell(f"{probe.PROBE_ENTRY_NOT_CONFIRMED}: partial fill closed "
                       f"({closed['status']})")
            if closed["status"] != live_leg.EXIT_CLOSED:
                sys.stderr.write(
                    "INCIDENT: a partially filled probe entry could not be closed; resolve "
                    f"at the venue ({closed['reason_codes']})\n"
                )
        else:
            _fail_cell(f"{probe.PROBE_ENTRY_NOT_CONFIRMED}: {entry['reconcile_status']}")
        sys.stderr.write(
            f"BLOCKED {probe.PROBE_ENTRY_NOT_CONFIRMED}: the entry did not confirm "
            f"({entry['reconcile_status']}); the cell is EMPTY again\n"
        )
        return EXIT_BLOCKED

    # 2. The resting stop, through the same leg placement the autonomous bracket uses.
    trigger = probe.probe_stop_price(fill_price, filters.tick_size)
    stop_intent = live_leg.build_bracket_intent(
        symbol=symbol, leg="SL", side="SELL", price=trigger,
        working_type=BRACKET_WORKING_TYPE, position_seed=str(entry["client_order_id"]),
    )
    placement = live_leg.place_bracket_leg(
        stop_intent, adapter=adapter, timeout_seconds=timeout_seconds, sleep=sleep,
    )
    if not placement.get("placed"):
        # Rule 2: an unprotected probe is closed, not held. The possibly-resting stop is
        # withdrawn by the same close (its id is on the position record).
        naked = build_live_position(
            symbol=symbol, direction=probe.PROBE_DIRECTION, quantity=filled_qty,
            entry_price=fill_price, opened_at=now,
            entry_client_order_id=entry["client_order_id"],
            entry_exchange_order_id=entry["exchange_order_id"], strategy_id=sid,
        )
        naked = {**naked, "stop_client_order_id": placement.get("client_order_id")}
        closed = live_leg.execute_live_exit(
            naked, adapter=adapter, position_store=position_store, ledger=ledger,
            gate_open=True, limits=limits, close_reason=live_leg.CLOSE_REASON_NAKED,
            now=timeutil.utc_now_iso(), timeout_seconds=timeout_seconds,
        )
        _fail_cell(f"{probe.PROBE_STOP_NOT_PLACED}: {placement.get('error_detail') or placement.get('error')}")
        if closed["status"] != live_leg.EXIT_CLOSED:
            sys.stderr.write(
                "INCIDENT: the probe stop was refused AND the close did not confirm; "
                f"resolve at the venue ({closed['reason_codes']})\n"
            )
        sys.stderr.write(
            f"BLOCKED {probe.PROBE_STOP_NOT_PLACED}: the stop would not rest "
            f"({placement.get('error')}); the entry was closed and the cell is EMPTY again\n"
        )
        return EXIT_BLOCKED

    # 3. Book the position from the ACTUAL fill, stop id attached, so the ordinary live
    #    machinery (reconcile / settle / protect) owns it if this process dies.
    position = build_live_position(
        symbol=symbol, direction=probe.PROBE_DIRECTION, quantity=filled_qty,
        entry_price=fill_price, stop_loss=trigger, opened_at=now,
        entry_client_order_id=entry["client_order_id"],
        entry_exchange_order_id=entry["exchange_order_id"], strategy_id=sid,
    )
    position = {
        **position,
        "stop_client_order_id": placement["client_order_id"],
        "entry_quote_usdt": float(fill.get("cum_quote") or 0.0) or None,
    }
    position_store.save_position(position)
    # The cell is already OPEN (claimed before the send); stamp the booked identity onto
    # it so a later resolver can find this position's outcome in the ledger.
    cells = [dict(c) for c in plan["cells"]]
    cells[cell_index]["position_id"] = position["position_id"]
    cells[cell_index]["updated_at"] = timeutil.utc_now_iso()
    plan = probe.write_plan({**plan, "cells": cells}, root)

    print(f"probe     : {symbol} LONG {filled_qty} @ {fill_price} (notional {notional} USDT), "
          f"stop resting at {trigger} ({placement['client_order_id']})")
    if counter_error:
        print(f"DAILY CAP : NOT counted ({counter_error}) — the order IS placed")
    if audit_error:
        print(f"AUDIT     : NOT recorded ({audit_error}) — the order IS placed")

    # 4. Wait for the venue: stop fill -> settle through #683; timeout -> market close.
    deadline = clock() + float(plan["params"]["timeout_minutes"]) * 60.0
    account_feed = select_account_feed(now=now, root=root)
    while clock() < deadline:
        if load_open_live_position(symbol, root) is None:
            # Someone else (the scheduler's live leg) settled it first — the ledger says how.
            resolved_now = timeutil.utc_now_iso()
            plan, resolution = probe.resolve_open_cell(
                plan, outcomes=read_live_outcomes(root), position_open=False, now=resolved_now,
            )
            plan = probe.write_plan(plan, root)
            if resolution is None:
                sys.stderr.write(f"BLOCKED {probe.PROBE_UNSETTLED}: the book cleared but the "
                                 "cell could not be resolved\n")
                return EXIT_BLOCKED
            print(f"settled   : cell -> {resolution['status']} (outcome {resolution['outcome_id']}, "
                  "recorded by the live cycle)")
            return EXIT_OK if resolution["status"] != probe.CELL_EMPTY else EXIT_BLOCKED
        try:
            leg = adapter.fetch_order(
                symbol, placement["client_order_id"], timeout_seconds=timeout_seconds, algo=True,
            )
        except MvpRuntimeError:
            leg = None  # a failed read is not a fill; ask again next tick
        status = str((leg or {}).get("status") or "")
        executed = float((live_execution.fill_facts(leg) or {}).get("executed_qty") or 0.0)
        if status in live_leg.FILLED_STATUSES and executed > 0:
            settle_now = timeutil.utc_now_iso()
            settled = live_leg.settle_venue_closed_position(
                position, adapter=adapter, position_store=position_store, ledger=ledger,
                account_feed=account_feed, now=settle_now, timeout_seconds=timeout_seconds,
            )
            outcome = settled.get("outcome")
            if settled["status"] != live_leg.EXIT_CLOSED or not isinstance(outcome, dict):
                sys.stderr.write(f"BLOCKED {probe.PROBE_UNSETTLED}: the stop filled but the "
                                 f"settle did not close ({settled['reason_codes']})\n")
                return EXIT_BLOCKED
            plan = probe.write_plan(
                probe.mark_cell(plan, cell_index, status=probe.CELL_FILLED, now=settle_now,
                                outcome_id=outcome.get("outcome_id"),
                                close_reason=outcome.get("close_reason"),
                                stop_slippage_bps=outcome.get("stop_slippage_bps")),
                root,
            )
            print(f"FILLED    : stop filled; slippage {outcome.get('stop_slippage_bps')} bps "
                  f"(outcome {outcome.get('outcome_id')})")
            return EXIT_OK
        sleep(max(0.0, float(poll_seconds)))

    # 5. Timeout: close at market. The row this writes is a `time_exit` — NOT a slippage
    #    sample, by `STOP_EXIT_REASONS`'s own definition — and the cell says so.
    close_now = timeutil.utc_now_iso()
    closed = live_leg.execute_live_exit(
        position, adapter=adapter, position_store=position_store, ledger=ledger,
        gate_open=True, limits=limits, close_reason=live_leg.CLOSE_REASON_TIME_EXIT,
        now=close_now, timeout_seconds=timeout_seconds,
    )
    close_audit_error = None
    if closed["status"] == live_leg.EXIT_CLOSED and isinstance(closed.get("intent"), dict):
        try:
            close_governance = live_governance.prepare_live_order_governance(
                closed["intent"], purpose=live_governance.PURPOSE_CANARY, now=close_now,
                repo_root=root,
            )
        except (MvpRuntimeError, ValueError) as exc:
            close_audit_error = getattr(exc, "reason_code", type(exc).__name__)
        else:
            close_audit_error = _audit_order(
                close_governance, closed["exit"], guard=closed["close_guard"],
                now=close_now, root=root,
            )
    outcome = closed.get("outcome")
    if closed["status"] != live_leg.EXIT_CLOSED or not isinstance(outcome, dict):
        sys.stderr.write(
            f"BLOCKED {probe.PROBE_UNSETTLED}: the timeout close did not confirm "
            f"({closed['reason_codes']}); the stop is still resting and the cell stays OPEN\n"
        )
        return EXIT_BLOCKED
    plan = probe.write_plan(
        probe.mark_cell(plan, cell_index, status=probe.CELL_TIMEOUT, now=close_now,
                        outcome_id=outcome.get("outcome_id"),
                        close_reason=outcome.get("close_reason")),
        root,
    )
    print(f"TIMEOUT   : closed at market after {plan['params']['timeout_minutes']}m; the row is "
          f"NOT a slippage sample (outcome {outcome.get('outcome_id')})")
    if close_audit_error:
        print(f"AUDIT     : close NOT recorded ({close_audit_error}) — the close IS done")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    parser = argparse.ArgumentParser(
        prog="run_slippage_probe",
        description="Stop-slippage probe: status board, batch ask/confirm, and the operator fire door.",
    )
    parser.add_argument("--status", action="store_true", help="read-only board and §5-5 readiness")
    parser.add_argument("--request", action="store_true",
                        help="ASK Thomas: build + store + audit the R9 approval request")
    parser.add_argument("--confirm", action="store_true",
                        help="write the approved plan store (places NO orders)")
    parser.add_argument("--approval-id", help="APPROVED approval id from the /approve answer")
    parser.add_argument("--symbols",
                        help='comma list for --request/--confirm (e.g. "BNBUSDT,DOGEUSDT"); '
                             "omitted = the approved default three. The content hash binds "
                             "the set, so a --symbols request is confirmed with the same set")
    parser.add_argument("--abandon", action="store_true",
                        help="retire the ACTIVE batch early (needs --reason; the sample rows "
                             "stay on the ledger, unfilled cells never happen, the next "
                             "batch may then confirm)")
    parser.add_argument("--reason", help="operator reason for --abandon (recorded on the "
                                         "control ledger)")
    parser.add_argument("--fire", action="store_true",
                        help="operator-only: place ONE probe synchronously (real money)")
    parser.add_argument("--symbol", help="which symbol to probe (required with --fire)")
    parser.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS,
                        help="how often the fire loop polls the resting stop")
    parser.add_argument("--timeout-seconds", type=int, default=10, help="per-venue-call timeout")
    parser.add_argument("--root", type=Path, default=None, help="state root (defaults to the repo)")
    args = parser.parse_args(argv)

    verbs = [args.status, args.request, args.confirm, args.abandon, args.fire]
    if sum(1 for v in verbs if v) != 1:
        print("USAGE: exactly one of --status / --request / --confirm / --abandon / --fire")
        return EXIT_USAGE

    try:
        if args.status:
            return run_status(root=args.root)
        # Every remaining verb writes state (approvals, the plan, or real orders).
        assert_not_foreign_root_run(args.root)
        if args.abandon:
            if not args.reason or not args.reason.strip():
                print("USAGE: --abandon needs --reason (the why outlives the batch)")
                return EXIT_USAGE
            return run_abandon(reason=args.reason.strip(), root=args.root)
        if args.request:
            return run_request(root=args.root, symbols=_parse_symbols(args.symbols))
        if args.confirm:
            if not args.approval_id:
                print("USAGE: --confirm needs --approval-id (ask first with --request)")
                return EXIT_USAGE
            return run_confirm(approval_id=args.approval_id, root=args.root,
                               symbols=_parse_symbols(args.symbols))
        if not args.symbol:
            print("USAGE: --fire needs --symbol")
            return EXIT_USAGE
        return run_fire(
            root=args.root, symbol=args.symbol,
            timeout_seconds=args.timeout_seconds, poll_seconds=args.poll_seconds,
        )
    except _Refusal as exc:
        sys.stderr.write(f"BLOCKED {exc.reason_code}: {exc}\n")
        return EXIT_BLOCKED
    except MvpRuntimeError as exc:
        sys.stderr.write(f"BLOCKED {exc.reason_code}: {exc.reason}\n")
        return EXIT_BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())
