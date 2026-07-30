"""LP6 live-readiness board — every condition between here and a live order, in one read.

    python -m runtime.mvp_runtime.crypto.live_readiness
    python -m runtime.mvp_runtime.crypto.live_readiness --json

Read-only and ungated: it writes nothing, places nothing, and holds no authority of its own.
It answers one question — *what is still standing between this machine and an autonomous live
order* — by asking each gate directly rather than by reasoning about them from documentation,
so an answer here cannot drift from what the code actually enforces.

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
from .live_position import compute_open_notional_usdt
from .live_order import (
    CONFIRMATION_ENV,
    MANUAL_KILL_SWITCH_ENV,
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


def _check(check_id: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"check": check_id, "ok": bool(ok), "detail": detail}


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
    except MvpRuntimeError as exc:
        runtime_active, runtime_detail = False, f"control state unreadable ({exc.reason_code})"
    checks.append(_check("runtime_active", runtime_active, runtime_detail))

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
    try:
        submitted_today, counter_error = count_today(root), None
    except MvpRuntimeError as exc:
        submitted_today, counter_error = 0, exc.reason_code
    guard = evaluate_live_order_guard(
        {
            "status": "ORDER_INTENT_CREATED",
            "symbol": "BTCUSDT",
            "quantity": 0.001,
            "order_notional_usdt": limits.max_order_notional_usdt,
            "reduce_only": False,
            "connectivity_test": False,
        },
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
        "ready": all(c["ok"] for c in checks),
        "checks": checks,
        "guard_dry_run": guard,
        "submitted_today": submitted_today,
        "counter_error": counter_error,
        "order_path_implemented": ORDER_PATH_IMPLEMENTED,
        "autonomous_routing_wired": AUTONOMOUS_ROUTING_WIRED,
    }


def render_readiness_text(status: dict[str, Any]) -> str:
    """ASCII-only board. Windows consoles are cp949."""
    lines = ["=== live trading readiness ==="]
    for check in status["checks"]:
        mark = "PASS" if check["ok"] else "FAIL"
        lines.append(f"[{mark}] {check['check']:24} {check['detail']}")
    guard = status["guard_dry_run"]
    lines.append("")
    lines.append(f"guard dry-run (an order at the configured cap): {guard['status']}")
    for block in guard.get("blocks") or []:
        lines.append(f"  BLOCK  : {block}")
    for repair in guard.get("repairs") or []:
        lines.append(f"  REPAIR : {repair}")
    if status.get("counter_error"):
        lines.append(f"WARNING : daily order counter unreadable ({status['counter_error']})")
    lines.append("")
    lines.append("READY" if status["ready"] else "NOT READY - every FAIL above must clear first")
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
