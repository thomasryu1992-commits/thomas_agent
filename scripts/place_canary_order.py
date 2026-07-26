"""Place ONE deliberate live canary order (LP4 increment 2b) — an operator tool.

    python -m scripts.place_canary_order --symbol BTCUSDT --quantity 0.001 --notional 60
    python -m scripts.place_canary_order --symbol BTCUSDT --quantity 0.001 --notional 60 --json

A canary is one small **real** order, placed on purpose, to prove that signing, submission, and
reconciliation actually work at the venue. Three clean canaries are what the autonomous path's
promotion gate requires — so this is the only door that is *not* gated on that evidence, because
it is what earns it. Everything else still applies: the `live_trading` grant, the **canary**
confirmation phrase (distinct from the autonomous one), a valid registered budget, both kill
switches, the daily-loss breaker, and the size / daily-count / exposure caps.

**Real money. Every step here is Thomas's.** Claude does not run this, does not handle the keys,
and does not enable live trading. Without the grant and the canary phrase this command refuses;
with the default (no grant) it selects the inert dry-run adapter and sends nothing.

Deliberately one order per invocation, and deliberately **entry-only**: a canary opens a small
position, which the operator then closes on the venue. It is not wired into any autonomous cycle.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from runtime.mvp_runtime import timeutil
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE, force_utf8_io
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.audit import AuditError
from runtime.mvp_runtime.crypto import live_execution, live_governance, live_promotion
from runtime.mvp_runtime.crypto.account import read_account
from runtime.mvp_runtime.crypto.live_order import (
    CANARY_CONFIRMATION_ENV,
    build_live_order_intent,
    count_today,
    enrich_order_identity,
    evaluate_live_order_guard,
    render_guard_text,
    resolve_live_order_limits,
)
from runtime.mvp_runtime.crypto.live_pnl import live_risk_snapshot
from runtime.mvp_runtime.crypto.live_position import compute_open_notional_usdt
from runtime.mvp_runtime.errors import MvpRuntimeError
from runtime.mvp_runtime.store import LedgerStore


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="place_canary_order",
        description="Place ONE deliberate live canary order and record the reconciled result.",
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--direction", default="LONG", choices=["LONG", "SHORT"])
    parser.add_argument("--quantity", type=float, required=True,
                        help="base-asset quantity (keep a canary small)")
    parser.add_argument("--notional", type=float, required=True,
                        help="the order's notional in USDT (never back-filled from the cap)")
    parser.add_argument("--timeout-seconds", type=int, default=10)
    parser.add_argument("--json", action="store_true", help="emit the full result as JSON")
    parser.add_argument("--root", type=Path, default=None, help="state root (defaults to the repo)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    args = _parse_args(argv)
    if args.quantity <= 0 or args.notional <= 0:
        sys.stderr.write("ERROR: --quantity and --notional must both be positive\n")
        return EXIT_USAGE

    root = args.root
    now = timeutil.utc_now_iso()

    try:
        # 1. Caps come from the REGISTERED budget (never the env cap vars).
        limits, budget = resolve_live_order_limits(root, now=now)

        # 2. The live facts the guard judges. Each is read, never assumed.
        control_state = ControlStore(root).load() if root is not None else ControlStore.default().load()
        runtime_active = control_state.execution_allowed
        risk = live_risk_snapshot(limit_usdt=limits.daily_loss_limit_usdt, root=root, now=now)
        clean_count, canary_error = live_promotion.clean_canary_order_count(root)
        submitted_today = count_today(root)

        # 3. Open exposure from the VENUE, via LP5's fail-closed supplier (an unreadable account
        #    reports at-cap, never 0.0 — the guard's one fail-open path). A canary additionally
        #    refuses outright, because "configure the account feed" is the actionable answer for a
        #    deliberate operator tool, rather than a cap refusal it has to decode.
        snapshot, account_use = read_account(timeout_seconds=args.timeout_seconds, root=root)
        open_notional = compute_open_notional_usdt(
            snapshot, at_cap=limits.max_open_notional_usdt
        )
        if snapshot is None:
            sys.stderr.write(
                "BLOCKED NO_ACCOUNT_VISIBILITY: cannot read the venue account, so open exposure "
                "is unknown and the exposure cap cannot be honored. Configure the read-only "
                f"account feed first (account_use={account_use.get('degraded_reason_code')}).\n"
            )
            return EXIT_BLOCKED

        # 4. The gate. Selecting the adapter is what proves the grant: an inert dry-run adapter
        #    means no grant is active, so nothing can be sent — and the guard is told so rather
        #    than being handed an optimistic assumption.
        adapter = live_execution.select_order_adapter(now=now, root=root)
        grant_open = bool(getattr(adapter, "network_egress", False))

        # 5. The intent, then the guard in CANARY mode: every check except the promotion gate,
        #    authorized by the canary phrase.
        intent = enrich_order_identity(build_live_order_intent(
            {"direction": args.direction}, symbol=args.symbol, quantity=args.quantity,
            notional_usdt=args.notional, now=now,
        ))
        verdict = evaluate_live_order_guard(
            intent,
            gate_open=grant_open,
            runtime_active=runtime_active,
            daily_loss_breached=bool(risk["daily_loss_limit_breached"]),
            clean_canary_orders=clean_count,
            submitted_today=submitted_today,
            current_open_notional_usdt=open_notional,
            budget_registered=bool(budget.get("valid")),
            canary=True,
            limits=limits,
        )

        sys.stdout.write(render_guard_text(verdict) + "\n")
        if canary_error:
            sys.stdout.write(f"canary history: {canary_error}\n")
        if not verdict["approved"]:
            sys.stderr.write(
                "BLOCKED: the canary guard refused; fix what it names rather than working around it.\n"
                f"(the canary phrase env is {CANARY_CONFIRMATION_ENV})\n"
            )
            return EXIT_BLOCKED

        # 5b. The governance record, BEFORE the order. `p5_policy_gate` requires
        #     `post_action_report_and_audit` for FINANCIAL_APPROVED_TRADING_USE, and until this
        #     landed a real order left nothing on the hash chain. Preparing it first means a
        #     governance failure (no active Core, an authority conflict) costs nothing — it
        #     refuses before any money moves. It grants nothing; the guard above is what permits.
        governance = live_governance.prepare_live_order_governance(
            intent, purpose=live_governance.PURPOSE_CANARY, now=now, repo_root=root,
        )

        # 6. Send exactly one order, then learn the truth from the venue.
        result = live_execution.submit_and_reconcile(
            intent, adapter=adapter, guard_verdict=verdict, now=now,
            timeout_seconds=args.timeout_seconds,
        )

        # 7. Record it. `clean` is derived by the record from the reconcile facts — this tool
        #    cannot assert that its own canary was clean.
        record = live_promotion.build_canary_order_record(
            reconcile_status=result["reconcile_status"],
            symbol=result["symbol"],
            exchange_order_id=result["exchange_order_id"],
            client_order_id=result["client_order_id"],
            mismatches=result["mismatches"],
            notional_usdt=args.notional,
            now=now,
        )
        registry_error = None
        try:
            live_promotion.select_canary_registry(now=now, root=root).append_canary_order(record)
        except MvpRuntimeError as exc:
            # The order is already at the venue; say so rather than implying nothing happened.
            registry_error = exc.reason_code

        # 8. The report half of EXECUTE_AND_REPORT: one audit event on the durable chain,
        #    carrying what the VENUE said. Best-effort by the same reasoning as the registry
        #    write above — the money has already moved, so a failure here is reported, never
        #    swallowed and never allowed to imply the order did not happen.
        audit_error = None
        try:
            event, _sha = live_governance.report_live_order(
                governance, result, guard_verdict=verdict, now=now, repo_root=root,
            )
            LedgerStore(root).append_audit_events([event])
        except (MvpRuntimeError, AuditError) as exc:
            audit_error = getattr(exc, "reason_code", type(exc).__name__)
    except MvpRuntimeError as exc:
        sys.stderr.write(f"BLOCKED {exc.reason_code}: {exc.reason}\n")
        return EXIT_BLOCKED

    payload = {"guard": verdict, "result": result, "canary_record": record,
               "registry_error": registry_error, "audit_error": audit_error,
               "permission_decision_id": governance["permission_decision"]["permission_decision_id"]}
    if args.json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=1, default=str) + "\n")
    else:
        sys.stdout.write(
            f"\ncanary {record['canary_order_id']}\n"
            f"  reconcile : {record['reconcile_status']}\n"
            f"  clean     : {record['clean']}\n"
            f"  venue id  : {result['exchange_order_id']}\n"
            f"  fill      : {result['fill']}\n"
        )
        if result["mismatches"]:
            sys.stdout.write(f"  MISMATCH  : {'; '.join(result['mismatches'])}\n")
        if registry_error:
            sys.stdout.write(f"  REGISTRY  : NOT recorded ({registry_error}) — the order IS placed\n")
        sys.stdout.write(
            f"  permdec   : {governance['permission_decision']['permission_decision_id']} (P5)\n"
        )
        if audit_error:
            sys.stdout.write(f"  AUDIT     : NOT recorded ({audit_error}) — the order IS placed\n")
        sys.stdout.write(
            "\nClose this canary position on the venue yourself — a canary only opens.\n"
        )
    # A placed-but-unrecorded canary, or one that did not reconcile, is not a success.
    if registry_error or not record["clean"]:
        return EXIT_BLOCKED
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
