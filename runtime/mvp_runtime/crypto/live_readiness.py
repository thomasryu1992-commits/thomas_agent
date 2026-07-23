"""LP6 live-readiness board — every condition between here and a live order, in one read.

    python -m runtime.mvp_runtime.crypto.live_readiness
    python -m runtime.mvp_runtime.crypto.live_readiness --json

Read-only and ungated: it opens no socket, writes nothing, and places nothing. It answers
one question — *what is still standing between this machine and an autonomous live order* —
by asking each gate directly rather than by reasoning about them from documentation, so an
answer here cannot drift from what the code actually enforces.

The final line is deliberately blunt. Until LP4 exists there is **no order path at all**, so
readiness can never report READY no matter how much is configured; the board says so rather
than showing a row of green ticks that imply otherwise.

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
from .live_order import (
    CONFIRMATION_ENV,
    MANUAL_KILL_SWITCH_ENV,
    LiveOrderLimits,
    count_today,
    evaluate_live_order_guard,
)
from .live_pnl import (
    LIVE_TRADING_ENV,
    LIVE_TRADING_FLAGS,
    LIVE_TRADING_PROVIDER_ID,
    REAL_LIVE_TRADING,
    live_risk_snapshot,
)

# LP4 has not been built. This is a constant rather than a computed check because there is
# nothing to compute: no module in this package can send an order to a venue. It flips only
# when an order adapter actually exists, and the governance decisions it needs are recorded
# in docs/runtime-contracts/LIVE_EXECUTION_GOVERNANCE_V0.1.md.
ORDER_PATH_IMPLEMENTED = False


def _check(check_id: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"check": check_id, "ok": bool(ok), "detail": detail}


def build_readiness(root: Path | None = None, *, now: str | None = None) -> dict[str, Any]:
    """Ask every gate and collect the answers. Never raises: an unreadable input is a
    failed check with its reason, not a crashed board."""
    root = root if root is not None else _repo_root()
    now = now or timeutil.utc_now_iso()
    limits = LiveOrderLimits.from_env()
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

    # 3. The four caps. Zero means not configured, which blocks.
    caps = {
        "max_order_notional_usdt": limits.max_order_notional_usdt,
        "max_daily_order_count": limits.max_daily_order_count,
        "max_open_notional_usdt": limits.max_open_notional_usdt,
        "daily_loss_limit_usdt": limits.daily_loss_limit_usdt,
    }
    unset = [name for name, value in caps.items() if value <= 0]
    over_ceiling = limits.max_order_notional_usdt > limits.absolute_max_notional_usdt
    checks.append(_check(
        "risk_caps",
        not unset and not over_ceiling,
        "all four configured" if not unset and not over_ceiling
        else (f"unconfigured: {', '.join(unset)}" if unset else "")
        + (" ; per-order cap exceeds the absolute ceiling" if over_ceiling else ""),
    ))

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

    # 9. The order path itself.
    checks.append(_check(
        "order_path_implemented",
        ORDER_PATH_IMPLEMENTED,
        "implemented" if ORDER_PATH_IMPLEMENTED
        else "NOT IMPLEMENTED - no module can send an order (LP4 pending governance)",
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
        current_open_notional_usdt=0.0,
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
    if not status["order_path_implemented"]:
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
