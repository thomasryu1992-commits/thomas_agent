"""LP6 live-readiness board — every condition between here and a live order, in one read.

    python -m runtime.mvp_runtime.crypto.live_readiness
    python -m runtime.mvp_runtime.crypto.live_readiness --json

Read-only and ungated: it writes nothing, places nothing, and holds no authority of its own.
It answers one question — *what is still standing between this machine and an autonomous live
order* — by asking each gate directly rather than by reasoning about them from documentation,
so an answer here cannot drift from what the code actually enforces.

**"This machine" is the whole claim, and it is not the whole system.** Most rows are computed
from ``os.environ``, so this board answers for the process that runs it. That was invisible
while only the scheduler ran it and became a false statement the moment other surfaces did:
the operator console and the assistant read door run in containers that deliberately carry no
``MVP_LIVE_*``, and both rendered "live trading off" while the scheduler held an open gate
(#382). The env is not forwarded to fix that — keeping the money path out of those containers
is the point. Instead the board reports what the trading process itself last recorded
(``recorded_gate``, from the cycle ledger both already mount) and refuses to render a bare
"off" that its own environment is not entitled to claim. ``ready`` still means "can THIS
process trade", because the CLI exit code is documented as a script precondition.

It opens **one** socket, and only when the operator has already configured an account feed:
the daily-loss breaker measures against what the venue realized, because the local outcome
ledger cannot supply that figure (its only writer is the autonomous leg nothing may import,
and the canary path is entry-only). Without a configured feed the board makes no outbound
call at all and the breaker row fails for want of a source — which is the honest answer, not
a degraded one. The read is the same gated, read-only `account` module the dashboard uses;
it cannot place, amend, or cancel anything.

The final line is deliberately blunt. Since LP4 landed (2026-07-25) an order path **does** exist,
so READY here no longer means "configured" — it means a real order could actually be placed on
this machine. The board says that out loud rather than letting a row of green ticks read as
harmless.

**Status claims live in computed rows, not in prose.** This module's whole purpose is that an
answer here cannot drift from what the code enforces — and it drifted anyway: for a day after the
LP5 executing leg shipped, this docstring still described it as missing. The computed rows were
right the whole time; the sentence a human reads before risking money was wrong. So whether an
autonomous path exists is now the `autonomous_routing_wired` row, derived from a constant that a
test pins to the actual import graph, and a second test asserts this prose makes no build claim.

Exit code is 0 only when every check passes, so it can be used as a precondition in a script.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .. import safety_gate, timeutil
from ..cli_common import force_utf8_io
from ..control import ControlStore
from ..errors import MvpRuntimeError
from ..paths import repo_root as _repo_root
from . import live_promotion
from .account import (
    ACCOUNT_API_KEY_ENV, ACCOUNT_API_SECRET_ENV, ACCOUNT_FEED_ENV, BINANCE_ACCOUNT,
    read_account,
)
from .dashboard import _read_cycle_records
from .live_position import compute_open_notional_usdt
from .live_route import ROUTE_DISABLED
from .live_order import (
    CONFIRMATION_ENV,
    MANUAL_KILL_SWITCH_ENV,
    bracket_breaker_status,
    count_today,
    evaluate_live_order_guard,
    resolve_live_order_limits,
)
from .live_pnl import (
    LIVE_TRADING_ENV,
    REAL_LIVE_TRADING,
    LIVE_PNL_NO_SOURCE,
    live_risk_snapshot,
    venue_daily_realized_net,
)
from .market_data import BINANCE_FUTURES, MARKET_DATA_ENV
from .risk_limits import limits_status as risk_limits_status

# LP4's order adapter exists (merged 2026-07-25): `live_execution.BinanceFuturesOrderAdapter`
# can sign, send, and reconcile an order. This is a constant rather than a computed check
# because it is a fact about the codebase, not about this machine — whether an order may
# actually be sent is the live-trading opt-in, the confirmation phrase, the registered budget,
# the kill switches, and the canary evidence, each of which the board checks on its own row.
# Kept in lockstep with the policy's `financial_transaction_execution_implemented`.
ORDER_PATH_IMPLEMENTED = True

# Whether any AUTONOMOUS entry point can reach the order path. TRUE since LP5.3 step 3
# (cycle routing): `crypto/cycle.py` runs a live leg through `crypto/live_route.py`, so a
# scheduled crypto fire on a machine whose environment sets `MVP_LIVE_TRADING=real` can open and
# close real positions. Pinned to the real import graph by
# `test_the_cycle_reaches_the_live_order_path_through_exactly_one_module`, so this constant and
# the code cannot disagree.
#
# It is still deliberately NOT part of `ready`. Wired is not permitted: every door below it —
# the opt-in, the confirmation phrase, the registered budget, the canary evidence, both kill
# switches, the loss breaker — is unchanged, and each has its own row above.
AUTONOMOUS_ROUTING_WIRED = True

# The symbol the guard dry-run probes when no budget names one. Only reached on a machine with
# no usable budget — where the honest answer is a block either way — so it decides nothing; a
# registered budget supplies the probe symbol from its own allowlist.
DEFAULT_PROBE_SYMBOL = "BTCUSDT"

# How old the trading process's own record may be before this board stops treating it as a
# statement about now. Cycles land every few minutes, so two hours is far outside normal and
# means the scheduler stopped rather than that the gate changed.
RECORDED_GATE_STALE_AFTER_SECONDS = 2 * 60 * 60


def _check(check_id: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"check": check_id, "ok": bool(ok), "detail": detail}


def _recorded_gate(root: Path, *, now: str) -> dict[str, Any]:
    """What the process that CAN trade last recorded about its own live gate.

    Every row this board computes from ``os.environ`` answers "can *this process* place a live
    order". That is the right question where the scheduler runs and a false one everywhere else:
    the operator console and the assistant read door run in containers that deliberately carry
    no ``MVP_LIVE_*``, so the env rows read FAIL there and the board states that live trading is
    off while the scheduler is placing real orders. Forwarding the env would "fix" it by putting
    the money path in the containers built to be without it — the wrong direction.

    So the board also reports the trading process's own answer. Every crypto cycle stamps
    ``live_route_status``, and ``DISABLED`` is written only when the gate refused to open, which
    makes the newest cycle record an authoritative "was the gate open". It is read from the
    ledger those containers already mount: no new writer, no new env, no second authority.

    Never raises and never guesses — an absent or unreadable ledger reports ``known: False``
    rather than an assumption in either direction.
    """
    unknown: dict[str, Any] = {
        "known": False, "open": None, "status": None,
        "recorded_at": None, "age_seconds": None, "stale": False, "error": None,
    }
    try:
        records, warning = _read_cycle_records(root, 1)
    except Exception as exc:  # noqa: BLE001 — an observability row must not break the board
        return {**unknown, "error": type(exc).__name__}
    if not records:
        return {**unknown, "error": warning}
    record = records[-1]
    status = record.get("live_route_status")
    recorded_at = record.get("created_at")
    if not isinstance(status, str) or not isinstance(recorded_at, str):
        return {**unknown, "error": "cycle record carries no live route status"}
    try:
        age = (timeutil.parse_iso(now) - timeutil.parse_iso(recorded_at)).total_seconds()
    except (MvpRuntimeError, ValueError):
        age = None
    return {
        "known": True,
        # DISABLED is the one status meaning the gate refused to open. Every other status was
        # reached through an opened gate, so the reading is positive rather than a guess.
        "open": status != ROUTE_DISABLED,
        "status": status,
        "recorded_at": recorded_at,
        "age_seconds": age,
        # An unparsable stamp counts as stale: an age this board cannot compute is not one it
        # may present as current.
        "stale": age is None or age > RECORDED_GATE_STALE_AFTER_SECONDS,
        "error": warning,
    }


def build_readiness(root: Path | None = None, *, now: str | None = None) -> dict[str, Any]:
    """Ask every gate and collect the answers. Never raises: an unreadable input is a
    failed check with its reason, not a crashed board."""
    root = root if root is not None else _repo_root()
    now = now or timeutil.utc_now_iso()
    # The caps come from the registered budget now, not the environment; confirmation + manual
    # kill still come from env (resolve_live_order_limits folds both in). budget["valid"] is the
    # authoritative "a budget backs these caps" fact the guard needs.
    limits, budget = resolve_live_order_limits(root, now=now)
    checks: list[dict[str, Any]] = []

    # 1. The switch itself — the environment, and only the environment (Thomas, 2026-07-28).
    #    The per-machine grant this row used to also require is gone; the row is renamed with it,
    #    because a board that still said "grant" while checking an env var would be the most
    #    misleading line on the page. The name change is intentional and load-bearing: the whole
    #    point of this board is that its rows mean what they say.
    opted_in = os.environ.get(LIVE_TRADING_ENV, "").strip().lower() == REAL_LIVE_TRADING
    checks.append(_check(
        "live_trading_opt_in",
        opted_in,
        f"{LIVE_TRADING_ENV}={REAL_LIVE_TRADING}" if opted_in
        else f"{LIVE_TRADING_ENV} is not {REAL_LIVE_TRADING!r} (live trading off)",
    ))

    # 2. The confirmation phrase (presence and exact match, never echoed).
    checks.append(_check(
        "confirmation_phrase",
        limits.confirmation_present(),
        "present and correct" if limits.confirmation_present()
        else f"{CONFIRMATION_ENV} missing or does not match the live-trading phrase",
    ))

    # 3. The registered trading budget (step 6b). The caps now come FROM this record, and a
    #    valid budget is schema-guaranteed to carry positive caps within the 200 USDT ceiling —
    #    so this one row subsumes the old env-caps check. A missing / expired / tampered budget
    #    fails here, and the caps fall back to the blocking defaults so the guard refuses too.
    budget_detail = (
        f"registered {budget['budget_id']} "
        f"(order<={limits.max_order_notional_usdt}, {limits.max_daily_order_count}/day, "
        f"open<={limits.max_open_notional_usdt}, loss<={limits.daily_loss_limit_usdt})"
        if budget.get("valid")
        else (f"registered but invalid: {budget['error']}" if budget.get("registered")
              else "no live-trading budget registered "
                   "(register with scripts/register_live_trading_budget.py)")
    )
    checks.append(_check("registered_budget", bool(budget.get("valid")), budget_detail))

    # 3b. The C4 breaker limits. Unlike every other row this one is GREEN when nothing is
    #     registered: the guards.py defaults are the supported steady state, not a gap, so a
    #     fresh machine must not read as unready over a record it is not expected to have. It
    #     goes red only for a record that exists and cannot be used — tampered, out of bounds,
    #     or lapsed — which is exactly the state in which the C4 guard refuses every entry, live
    #     and paper alike. Without this row that refusal would be invisible here and the operator
    #     would find it in a cycle record instead.
    risk_status = risk_limits_status(root, now=now)
    effective = risk_status.get("effective") or {}
    # ASCII only, like every other row: this text is rendered to a terminal board.
    risk_numbers = (
        f"daily {effective.get('daily_max_loss_r')}R, weekly {effective.get('weekly_max_loss_r')}R, "
        f"consecutive {effective.get('max_consecutive_losses')}, "
        f"drawdown {effective.get('max_drawdown_pct')}%"
    )
    if not risk_status["registered"]:
        risk_detail = f"none registered - guard uses the defaults ({risk_numbers})"
    elif risk_status["valid"]:
        risk_detail = (
            f"registered {risk_status['limits_id']} ({risk_numbers}), "
            f"valid until {risk_status.get('valid_until')}"
        )
    else:
        risk_detail = (
            f"registered but unusable: {risk_status['error']} - the C4 guard REFUSES new "
            "positions until it is re-registered or deleted "
            "(scripts/register_crypto_risk_limits.py --show)"
        )
    checks.append(_check("risk_limits_record", bool(risk_status["valid"]), risk_detail))

    # 4. The manual halt.
    manual_halt = limits.manual_kill_switch
    checks.append(_check(
        "manual_kill_switch",
        not manual_halt,
        "clear" if not manual_halt else f"{MANUAL_KILL_SWITCH_ENV} is engaged",
    ))

    # 5. The runtime kill switch (kill_blocks: external_execution).
    try:
        state = ControlStore(root).load()
        runtime_active, runtime_detail = state.execution_allowed, f"runtime is {state.mode}"
        trading_armed = state.trading_armed
        armed_detail = (
            "armed" if trading_armed else
            "DISARMED - the runtime resumed without re-arming live entries; open positions "
            "still close and paper is unaffected"
        )
    except MvpRuntimeError as exc:
        runtime_active, runtime_detail = False, f"control state unreadable ({exc.reason_code})"
        trading_armed, armed_detail = False, f"control state unreadable ({exc.reason_code})"
    checks.append(_check("runtime_active", runtime_active, runtime_detail))
    # 5b. The arm, as its own row. Folding it into `runtime_active` would make one FAIL mean two
    # different situations with different fixes — a kill needs a resume, a disarm needs a
    # re-arm — and this board exists so an operator does not have to guess which.
    checks.append(_check("trading_armed", trading_armed, armed_detail))

    # Whether an account feed is configured at all. Computed here rather than at row 8 because
    # row 6's loss breaker needs the same answer first: it is what decides whether this board
    # reads the venue or stays offline.
    account_configured = (
        os.environ.get(ACCOUNT_FEED_ENV, "").strip().lower() == BINANCE_ACCOUNT
        and bool(os.environ.get(ACCOUNT_API_KEY_ENV, "").strip())
        and bool(os.environ.get(ACCOUNT_API_SECRET_ENV, "").strip())
    )

    # 6. Today's realized loss.
    # The snapshot already folds in the unconfigured-limit rule (no limit reads as breached)
    # and fails closed on an unverifiable history, so this one value covers every case.
    # The breaker needs a figure the local ledger structurally cannot supply (see below), so
    # the board reads the account for it — but ONLY when the operator has already configured
    # one. That keeps the surprise out: an unconfigured machine still opens no socket and the
    # row still fails, exactly as before. A configured read that fails degrades the same way —
    # to no figure, and therefore to a FAILING row. A loss breaker is the one place where
    # "could not measure" must never soften into "nothing to report".
    venue_realized = None
    account_error = None
    snapshot = None
    if account_configured:
        try:
            snapshot, _ = read_account(root=root)
            venue_realized = (
                venue_daily_realized_net(snapshot.realized_windows) if snapshot else None
            )
            if venue_realized is None:
                account_error = "account read returned no realized figure"
        except MvpRuntimeError as exc:      # degrade, never block — the R3 posture
            account_error = exc.reason_code
    risk = live_risk_snapshot(
        limit_usdt=limits.daily_loss_limit_usdt, root=root, now=now,
        venue_realized_pnl_usdt=venue_realized,
    )
    breached = bool(risk["daily_loss_limit_breached"])
    # A breaker with nothing to measure is not a passing check, however comfortable its number
    # looks. The local outcome ledger is written only by `live_leg.execute_live_exit` — the
    # autonomous leg nothing may import — and the canary path is entry-only, so on this board
    # the figure below has no source at all. It read `realized today 0.0 USDT` and PASSED while
    # the venue reported a real realized loss for the same day. Reporting that as ready is the
    # failure `cycle.py` names: a breaker that cannot trip is not a breaker.
    no_source = risk.get("history_error") == LIVE_PNL_NO_SOURCE
    # BREACHED is reported ahead of NO DATA SOURCE, and the order matters: an unconfigured limit
    # already reads as breached ("zero means not configured, never unlimited"), and that is the
    # stronger statement of the two. Letting the newer message win would have downgraded it.
    if breached:
        detail = (
            f"BREACHED (realized {risk['daily_realized_pnl_usdt']}, "
            f"limit {risk['daily_loss_limit_usdt']}"
            + (f", history_error={risk['history_error']}" if risk["history_error"] else "") + ")"
        )
    elif no_source:
        detail = (
            f"NO DATA SOURCE - the local outcome ledger has no closed trade for today, so the "
            f"{risk['daily_loss_limit_usdt']} USDT limit currently bounds nothing. The venue "
            "knows the figure; a caller that reads the account can pass it in."
        )
    else:
        detail = (
            f"realized today {risk['daily_realized_pnl_usdt']} USDT, "
            f"limit {risk['daily_loss_limit_usdt']} (source={risk.get('pnl_source')})"
        )
    checks.append(_check("daily_loss_breaker", not breached and not no_source, detail))

    # 6b. The bracket breaker. Unlike the loss breaker above it always has a source: it counts
    # what this runtime's own leg did, so it reads zero only when zero is true. It is on the
    # board because a tripped breaker means live entries are shut off for a reason no other row
    # would show — the account is fine, the limits are fine, and nothing will trade.
    try:
        bracket = bracket_breaker_status(root)
    except MvpRuntimeError as exc:
        # An unreadable record must not take the board down, and must not read as clear either:
        # the entry path refuses on this same error, so the board reports what it reports.
        bracket = None
        bracket_detail = (
            f"UNREADABLE ({getattr(exc, 'reason_code', 'UNKNOWN')}) - the entry path refuses on "
            "this too, so live entries are blocked until the record is repaired or removed"
        )
    else:
        if bracket["tripped"]:
            bracket_detail = (
                f"TRIPPED - {bracket['consecutive']} consecutive entries filled and could not be "
                f"protected (limit {bracket['limit']}), last {bracket['last_symbol']} at "
                f"{bracket['last_failure_at']}. New entries are refused until an operator clears "
                "it: python -m scripts.clear_bracket_breaker --cleared-by ... --reason ..."
            )
        else:
            bracket_detail = (
                f"{bracket['consecutive']}/{bracket['limit']} consecutive bracket failures"
                + (f" ({bracket['total']} total, last {bracket['last_failure_at']})"
                   if bracket["total"] else "")
            )
    checks.append(
        _check("bracket_breaker", bracket is not None and not bracket["tripped"], bracket_detail)
    )

    # 7. Canary evidence.
    promotion = live_promotion.promotion_status(
        min_orders=limits.min_clean_canary_orders, root=root
    )
    # The count says how MANY orders back the promotion; this says whether they can prove what
    # they were. The records gained a declared-versus-filled subtraction so it would stop being
    # a memory, and then nothing read it — a number stored where the person the gate consists of
    # never sees it is only half the repair. Appended rather than folded into `ready`: making a
    # size disagreement block promotion would change what the count means, which is a separate
    # decision the field's own author declined to take.
    size_note = ""
    if promotion["size_unproven"]:
        size_note = (f" [{promotion['size_unproven']} of {promotion['clean_count']} cannot prove "
                     "their size — no fill recorded]")
    elif promotion["largest_size_gap_usdt"]:
        size_note = f" [largest declared-vs-filled gap {promotion['largest_size_gap_usdt']:.2f} USDT]"
    checks.append(_check(
        "canary_evidence",
        promotion["ready"],
        f"{promotion['clean_count']}/{promotion['required']} clean canary orders"
        + ("" if promotion["ready"] else " - " + "; ".join(promotion["reasons"]))
        + size_note,
    ))

    # 8. The account read (LP1) — not required to place an order, but going live without
    #    being able to see the account is flying blind, so it is reported. `account_configured`
    #    is computed above, because row 6's breaker depends on the same feed.
    checks.append(_check(
        "account_visibility",
        account_configured,
        "live account read configured" if account_configured
        else f"{ACCOUNT_FEED_ENV} / {ACCOUNT_API_KEY_ENV} / {ACCOUNT_API_SECRET_ENV} not all set",
    ))

    # 8b. Market data — a canary PRECONDITION since the declared-notional check landed, not a
    #     nicety. `place_canary_order` verifies `--notional` against the venue's own last close
    #     and refuses when there is no usable price, so a machine without this feed cannot place
    #     the canaries that row 7 is counting. Checked as env AND grant — this feed KEPT its
    #     per-machine grant when live trading lost one (2026-07-28), so the two rows above and
    #     here now legitimately differ, and the difference is not drift. The env var alone
    #     selects the mock, whose synthesised price the check rejects.
    #     Stated here because #201's lesson was that a precondition only a document knows about
    #     is discovered by an operator standing at a terminal with real keys.
    market_data_opted_in = (
        os.environ.get(MARKET_DATA_ENV, "").strip().lower() == BINANCE_FUTURES
    )
    market_data_grant_error: str | None = None
    try:
        safety_gate.authorize(
            (safety_gate.NETWORK_ACCESS,), provider_id=BINANCE_FUTURES, now=now, root=root
        )
    except MvpRuntimeError as exc:
        market_data_grant_error = exc.reason_code
    market_data_ready = market_data_opted_in and market_data_grant_error is None
    checks.append(_check(
        "market_data_visibility",
        market_data_ready,
        "live market data configured (the declared-notional check has a real price)"
        if market_data_ready
        else (f"grant: {market_data_grant_error or 'ok'}; "
              f"{MARKET_DATA_ENV}={'set' if market_data_opted_in else 'unset'} "
              f"- without it a canary is refused, so no canary evidence can be earned"),
    ))

    # 9. The order path itself.
    checks.append(_check(
        "order_path_implemented",
        ORDER_PATH_IMPLEMENTED,
        "implemented (LP4 adapter + LP5.3 executing leg)" if ORDER_PATH_IMPLEMENTED
        else "NOT IMPLEMENTED - no module can send an order (LP4 pending governance)",
    ))

    # 10. Whether anything autonomous can reach it. Reported as its own row rather than asserted
    #     in prose, because this is the fact most likely to go stale — and the one that decides
    #     whether READY means "an operator can place an order" or "this machine can trade on its
    #     own". It is deliberately NOT part of `ready`: an unwired runtime is the safe state, so
    #     failing the board on it would invert the meaning of every other row.
    checks.append(_check(
        "autonomous_routing_wired",
        True,  # informational: neither state is a failure
        "WIRED - a scheduled run can place live orders" if AUTONOMOUS_ROUTING_WIRED
        else "not wired - the only door is scripts/place_canary_order.py, one canary at a time",
    ))

    # A dry-run of the real guard against a representative order at the configured cap.
    # This is the authoritative answer: whatever the rows above say, this is what would
    # actually happen. Nothing is sent — the guard is pure.
    #
    # It is only authoritative if it is given what the real doors are given. Until 2026-07-31
    # this call omitted `allowed_symbols`, whose default is EMPTY — and an empty allowlist
    # blocks every symbol — so the dry-run reported "no symbol allowlist backs this order" on
    # every machine, forever, however the budget was registered. Both real callers
    # (`live_route.plan_live_entry`, `scripts/place_canary_order.py`) read the scope off the
    # same budget the caps come from; so does this now. The board under-reported what the
    # machine could do, which is the dangerous direction for a line an operator reads before
    # deciding whether live trading is stopped.
    #
    # The probe symbol comes from that allowlist rather than a hardcoded BTCUSDT: a machine
    # budgeted for ETHUSDT alone is correctly configured, and probing a symbol its own budget
    # excludes would answer a question nobody asked. With no allowlist the probe keeps the old
    # constant — the block it then reports is the true one.
    allowed_symbols = tuple(budget.get("symbol_allowlist") or ())
    probe_symbol = allowed_symbols[0] if allowed_symbols else DEFAULT_PROBE_SYMBOL
    try:
        submitted_today, counter_error = count_today(root), None
    except MvpRuntimeError as exc:
        submitted_today, counter_error = 0, exc.reason_code
    guard = evaluate_live_order_guard(
        {
            "status": "ORDER_INTENT_CREATED",
            "symbol": probe_symbol,
            "quantity": 0.001,
            "order_notional_usdt": limits.max_order_notional_usdt,
            "reduce_only": False,
            "connectivity_test": False,
        },
        allowed_symbols=allowed_symbols,
        gate_open=opted_in,
        runtime_active=runtime_active,
        daily_loss_breached=breached,
        clean_canary_orders=promotion["clean_count"],
        submitted_today=submitted_today,
        # LP5.3: the board now reads the account for the loss breaker, so the same snapshot
        # answers the exposure question — and the honest block-at-cap can finally lift on a
        # machine that can see its own account. `compute_open_notional_usdt` still fails
        # closed: `snapshot is None` (no feed configured, or the read degraded) reports AT
        # the cap, never zero. What changed is that a configured, readable account now
        # reports what it actually holds rather than the worst case, so a dry-run BLOCK here
        # means real exposure, not merely an unconfigured board.
        current_open_notional_usdt=compute_open_notional_usdt(
            snapshot, at_cap=limits.max_open_notional_usdt
        ),
        budget_registered=bool(budget.get("valid")),
        limits=limits,
    )

    return {
        "created_at": now,
        # Deliberately still "can THIS process trade": the CLI's exit code is documented as a
        # script precondition, and a script asking that question runs where trading runs. The
        # recorded gate below is reported ALONGSIDE the checks, never folded into them, so a
        # board read off the scheduler cannot flip this verdict.
        "ready": all(c["ok"] for c in checks),
        "checks": checks,
        "recorded_gate": _recorded_gate(root, now=now),
        "guard_dry_run": guard,
        # Which order the dry-run judged. A symbol-scoped block is unreadable without it: the
        # operator cannot tell "my budget excludes this symbol" from "this symbol is blocked".
        "guard_dry_run_symbol": probe_symbol,
        "submitted_today": submitted_today,
        "counter_error": counter_error,
        "order_path_implemented": ORDER_PATH_IMPLEMENTED,
        "autonomous_routing_wired": AUTONOMOUS_ROUTING_WIRED,
    }


def _opted_in(status: Mapping[str, Any]) -> bool:
    return any(c["check"] == "live_trading_opt_in" and c["ok"] for c in status.get("checks") or ())


def contradicts_recorded_gate(status: Mapping[str, Any]) -> bool:
    """True when this board's env rows say "off" and the trading process says otherwise.

    The single state this board must never render as a plain "live trading off": the reader is
    on a console that cannot see the money path, and the process that can was trading when it
    last wrote. A stale or unknown record does NOT qualify — an old record is not evidence about
    now, and inventing a contradiction from one would trade this false negative for a false
    positive.
    """
    recorded = status.get("recorded_gate") or {}
    return (
        not _opted_in(status)
        and bool(recorded.get("known"))
        and bool(recorded.get("open"))
        and not recorded.get("stale")
    )


def _recorded_gate_line(status: Mapping[str, Any]) -> str:
    recorded = status.get("recorded_gate") or {}
    if not recorded.get("known"):
        detail = recorded.get("error") or "no crypto cycle has been recorded yet"
        return f"[----] {'live_gate_recorded':24} UNKNOWN - {detail}"
    state = "OPEN" if recorded.get("open") else "CLOSED"
    age = recorded.get("age_seconds")
    when = recorded.get("recorded_at")
    suffix = " [STALE - not a statement about now]" if recorded.get("stale") else ""
    minutes = f", {int(age // 60)}m ago" if isinstance(age, (int, float)) else ""
    return (
        f"[----] {'live_gate_recorded':24} trading process recorded the gate {state} "
        f"at {when} ({recorded.get('status')}{minutes}){suffix}"
    )


def render_readiness_text(status: dict[str, Any]) -> str:
    """ASCII-only board. Windows consoles are cp949."""
    lines = ["=== live trading readiness ==="]
    # Before the rows, not after: the rows are what mislead, so a reader must meet the warning
    # first. #382 — the operator console and the assistant read door both ran this board in
    # containers with no MVP_LIVE_*, and both told a reader live trading was off while the
    # scheduler held an open gate.
    if contradicts_recorded_gate(status):
        recorded = status["recorded_gate"]
        lines.append("!! THIS PROCESS CANNOT SEE THE LIVE-TRADING ENVIRONMENT")
        lines.append(f"   the trading process recorded the gate OPEN at {recorded['recorded_at']}")
        lines.append("   the env rows below describe THIS container, not the system")
        lines.append("   authoritative board:")
        lines.append("     docker exec thomas-scheduler python -m runtime.mvp_runtime.crypto"
                     ".live_readiness")
        lines.append("")
    for check in status["checks"]:
        mark = "PASS" if check["ok"] else "FAIL"
        lines.append(f"[{mark}] {check['check']:24} {check['detail']}")
    lines.append(_recorded_gate_line(status))
    guard = status["guard_dry_run"]
    lines.append("")
    probe = status.get("guard_dry_run_symbol") or DEFAULT_PROBE_SYMBOL
    lines.append(f"guard dry-run ({probe} at the configured cap): {guard['status']}")
    for block in guard.get("blocks") or []:
        lines.append(f"  BLOCK  : {block}")
    for repair in guard.get("repairs") or []:
        lines.append(f"  REPAIR : {repair}")
    if status.get("counter_error"):
        lines.append(f"WARNING : daily order counter unreadable ({status['counter_error']})")
    lines.append("")
    if status["ready"]:
        lines.append("READY")
    elif contradicts_recorded_gate(status):
        # Unqualified "NOT READY" here would be the same false statement the banner exists to
        # prevent, in the one line a hurried reader keeps.
        lines.append("NOT READY (THIS PROCESS ONLY) - it carries no live-trading environment;")
        lines.append("           this says nothing about whether the system is trading")
    else:
        lines.append("NOT READY - every FAIL above must clear first")
    if status["order_path_implemented"]:
        # READY is no longer an abstract "configured" — say what it now means.
        lines.append("NOTE  : an order path EXISTS; READY here means a real order can be placed")
        if status.get("autonomous_routing_wired"):
            # The loudest line the board has, and it earns it: this is the one state in which
            # nobody is standing at a terminal when the order goes out. It says how to stop it
            # too — an operator reading a board they do not like should not have to go and find
            # the runbook first.
            lines.append("NOTE  : autonomous routing is WIRED - a scheduled crypto run on this")
            lines.append("        machine opens and closes REAL positions once every FAIL clears")
            # The stop instruction changed with the gate (2026-07-28): there is no grant file to
            # delete any more. `console_cli kill` is what replaces it and is strictly the better
            # instruction — it writes control state, so it lands on the RUNNING scheduler at its
            # next guard rather than at the next restart, and the close path is exempt from it.
            # Both env-based alternatives are worse: MVP_LIVE_MANUAL_KILL_SWITCH needs a restart,
            # and clearing MVP_LIVE_TRADING needs a restart AND strands open positions, because
            # the close guard still requires the opt-in. Named in that order, because this line
            # is read in a hurry.
            lines.append("NOTE  : to stop new entries immediately, run:")
            lines.append("          python -m runtime.mvp_runtime.console_cli kill --reason ...")
            lines.append("        it takes effect on the running service and open positions can")
            lines.append("        still close. Do NOT clear MVP_LIVE_TRADING to halt - it needs a")
            lines.append("        restart and it shuts the close path too")
        else:
            lines.append("NOTE  : autonomous routing is NOT wired - the only door is")
            lines.append("        scripts/place_canary_order.py, one deliberate canary at a time")
    else:
        lines.append("NOTE  : no order path exists yet; this board cannot report READY until LP4 lands")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    parser = argparse.ArgumentParser(
        description="Live-trading readiness board (read-only: no network, no writes, no orders)."
    )
    parser.add_argument("--json", action="store_true", help="emit the full status as JSON")
    args = parser.parse_args(argv)
    status = build_readiness()
    if args.json:
        sys.stdout.write(json.dumps(status, ensure_ascii=False, indent=1) + "\n")
    else:
        sys.stdout.write(render_readiness_text(status) + "\n")
    return 0 if status["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
