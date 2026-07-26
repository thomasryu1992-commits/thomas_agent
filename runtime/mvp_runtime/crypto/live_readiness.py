"""LP6 live-readiness board — every condition between here and a live order, in one read.

    python -m runtime.mvp_runtime.crypto.live_readiness
    python -m runtime.mvp_runtime.crypto.live_readiness --json

Read-only and ungated: it opens no socket, writes nothing, and places nothing. It answers
one question — *what is still standing between this machine and an autonomous live order* —
by asking each gate directly rather than by reasoning about them from documentation, so an
answer here cannot drift from what the code actually enforces.

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
from ..control import ControlStore
from ..errors import MvpRuntimeError
from ..paths import repo_root as _repo_root
from . import live_promotion
from .account import ACCOUNT_API_KEY_ENV, ACCOUNT_API_SECRET_ENV, ACCOUNT_FEED_ENV, BINANCE_ACCOUNT
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
    LIVE_TRADING_FLAGS,
    LIVE_TRADING_PROVIDER_ID,
    REAL_LIVE_TRADING,
    live_risk_snapshot,
)
from .market_data import BINANCE_FUTURES, MARKET_DATA_ENV

# LP4's order adapter exists (merged 2026-07-25): `live_execution.BinanceFuturesOrderAdapter`
# can sign, send, and reconcile an order. This is a constant rather than a computed check
# because it is a fact about the codebase, not about this machine — whether an order may
# actually be sent is the `live_trading` grant, the confirmation phrase, the registered budget,
# the kill switches, and the canary evidence, each of which the board checks on its own row.
# Kept in lockstep with the policy's `financial_transaction_execution_implemented`.
ORDER_PATH_IMPLEMENTED = True

# Whether any AUTONOMOUS entry point can reach the order path. The executing leg
# (`crypto/live_leg.py`) exists and can place an order with an injected adapter, but no
# scheduled or operator-triggered run imports it — `live_leg` is in the surface list that
# `test_no_autonomous_entry_point_reaches_the_live_order_path` enforces, so this constant and the
# real import graph are pinned to agree. Flipping it is the cycle-routing decision, and it must
# move in the same commit that relaxes that test.
AUTONOMOUS_ROUTING_WIRED = False


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

    # 1. The switch itself.
    opted_in = os.environ.get(LIVE_TRADING_ENV, "").strip().lower() == REAL_LIVE_TRADING
    grant_error: str | None = None
    try:
        safety_gate.authorize(
            LIVE_TRADING_FLAGS, provider_id=LIVE_TRADING_PROVIDER_ID, now=now, root=root
        )
    except MvpRuntimeError as exc:
        grant_error = exc.reason_code
    checks.append(_check(
        "live_trading_grant",
        grant_error is None and opted_in,
        "granted and opted in" if grant_error is None and opted_in
        else f"grant: {grant_error or 'ok'}; {LIVE_TRADING_ENV}={'set' if opted_in else 'unset'}",
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

    # 6. Today's realized loss.
    # The snapshot already folds in the unconfigured-limit rule (no limit reads as breached)
    # and fails closed on an unverifiable history, so this one value covers every case.
    risk = live_risk_snapshot(limit_usdt=limits.daily_loss_limit_usdt, root=root, now=now)
    breached = bool(risk["daily_loss_limit_breached"])
    checks.append(_check(
        "daily_loss_breaker",
        not breached,
        f"realized today {risk['daily_realized_pnl_usdt']} USDT, limit {risk['daily_loss_limit_usdt']}"
        if not breached else
        f"BREACHED (realized {risk['daily_realized_pnl_usdt']}, limit {risk['daily_loss_limit_usdt']}"
        + (f", history_error={risk['history_error']}" if risk["history_error"] else "") + ")",
    ))

    # 7. Canary evidence.
    promotion = live_promotion.promotion_status(
        min_orders=limits.min_clean_canary_orders, root=root
    )
    checks.append(_check(
        "canary_evidence",
        promotion["ready"],
        f"{promotion['clean_count']}/{promotion['required']} clean canary orders"
        + ("" if promotion["ready"] else " - " + "; ".join(promotion["reasons"])),
    ))

    # 8. The account read (LP1) — not required to place an order, but going live without
    #    being able to see the account is flying blind, so it is reported.
    account_configured = (
        os.environ.get(ACCOUNT_FEED_ENV, "").strip().lower() == BINANCE_ACCOUNT
        and bool(os.environ.get(ACCOUNT_API_KEY_ENV, "").strip())
        and bool(os.environ.get(ACCOUNT_API_SECRET_ENV, "").strip())
    )
    checks.append(_check(
        "account_visibility",
        account_configured,
        "live account read configured" if account_configured
        else f"{ACCOUNT_FEED_ENV} / {ACCOUNT_API_KEY_ENV} / {ACCOUNT_API_SECRET_ENV} not all set",
    ))

    # 8b. The market-data read (C2) — a canary precondition since 2026-07-26, because the canary
    #     tool checks the notional the operator declares against the notional the quantity implies
    #     at the venue's own price. Without this feed the mock collector is selected and its price
    #     is a hash of the symbol, so `check_declared_notional` refuses rather than clear a real
    #     order against a fabricated number.
    #
    #     Reported as its own row for the reason #201 exists: a precondition that only a document
    #     knows about is discovered by an operator standing at a terminal with real keys. Env AND
    #     grant, like row 1 — the env var alone fails closed, so checking only the var would show a
    #     green tick for a machine that still refuses.
    market_data_opted_in = (
        os.environ.get(MARKET_DATA_ENV, "").strip().lower() == BINANCE_FUTURES
    )
    market_data_error: str | None = None
    try:
        safety_gate.authorize(
            (safety_gate.NETWORK_ACCESS,), provider_id=BINANCE_FUTURES, now=now, root=root
        )
    except MvpRuntimeError as exc:
        market_data_error = exc.reason_code
    market_data_ok = market_data_error is None and market_data_opted_in
    checks.append(_check(
        "market_data_visibility",
        market_data_ok,
        "live market-data read configured (the canary's notional check needs a real price)"
        if market_data_ok
        else f"grant: {market_data_error or 'ok'}; "
             f"{MARKET_DATA_ENV}={'set' if market_data_opted_in else 'unset'} - a canary cannot "
             "verify its declared notional without a real price",
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
        gate_open=(grant_error is None and opted_in),
        runtime_active=runtime_active,
        daily_loss_breached=breached,
        clean_canary_orders=promotion["clean_count"],
        submitted_today=submitted_today,
        # LP5.1: this board performs no venue read, so the open exposure is genuinely
        # UNKNOWN here — and unknown exposure is reported at the cap, never as zero. The
        # literal 0.0 that used to sit here asserted "the account is flat" on no evidence,
        # which is the fail-open the guard's required argument now prevents. LP5.3 supplies
        # the real figure from the account snapshot; until then this row honestly blocks.
        current_open_notional_usdt=compute_open_notional_usdt(
            None, at_cap=limits.max_open_notional_usdt
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
            lines.append("NOTE  : autonomous routing is WIRED - a scheduled run can place orders")
        else:
            lines.append("NOTE  : autonomous routing is NOT wired - the only door is")
            lines.append("        scripts/place_canary_order.py, one deliberate canary at a time")
    else:
        lines.append("NOTE  : no order path exists yet; this board cannot report READY until LP4 lands")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
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
