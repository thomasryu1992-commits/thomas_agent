"""C11 dashboard — the crypto pipeline's read-only status board.

    python -m runtime.mvp_runtime.crypto.dashboard            # human-readable
    python -m runtime.mvp_runtime.crypto.dashboard --json     # machine-readable
    python -m runtime.mvp_runtime.crypto.dashboard --account  # + the live exchange account

Reads only what the runtime already persists (cycle records in the ledger, the paper
outcome store, the active pool, the counterfactual registry, the safety-flag grants)
and renders uptime, performance, digest trends, lifecycle state, and gate-calibration
summaries. Pure reads at ALLOW tier: no gate, no writes, no network — the source
``scripts/dashboard.py`` posture. Unreadable inputs degrade to an explicit warning
line, never a crash and never silence.

``--account`` is the one exception and is therefore opt-in: it adds a *live* read of the
real exchange account through the separately-gated ``binance_futures_account`` feed
(LP1). Without the flag this board still makes no network call at all.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .. import timeutil
from ..errors import MvpRuntimeError
from ..paths import repo_root as _repo_root
from ..store import LEDGER_REL, RECORDS_FILE
from . import account, counterfactual, digest, feedback, paper, pool


def _read_cycle_records(root: Path, limit: int) -> tuple[list[dict[str, Any]], str | None]:
    path = root / LEDGER_REL / RECORDS_FILE
    if not path.is_file():
        return [], None
    try:
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("kind") == "crypto_cycle":
                rows.append(row.get("record") or {})
        return rows[-limit:], None
    except (OSError, ValueError) as exc:
        return [], f"cycle ledger unreadable: {type(exc).__name__}"


def _grants(root: Path) -> list[dict[str, Any]]:
    grants_dir = root / ".runtime_governance_state" / "safety_flag_activations"
    rows: list[dict[str, Any]] = []
    if not grants_dir.is_dir():
        return rows
    for path in sorted(grants_dir.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            rows.append({"provider_id": record.get("provider_id", path.stem),
                         "expires_at": record.get("expires_at")})
        except (OSError, ValueError):
            rows.append({"provider_id": path.stem, "expires_at": "UNREADABLE"})
    return rows


def build_status(root: Path | None = None, *, now: str | None = None, cycles: int = 12) -> dict[str, Any]:
    """Assemble the full status document. Read-only; failures become warnings."""
    root = root if root is not None else _repo_root()
    now = now or timeutil.utc_now_iso()
    warnings: list[str] = []

    cycle_rows, cycle_warning = _read_cycle_records(root, cycles)
    if cycle_warning:
        warnings.append(cycle_warning)

    try:
        outcomes = paper.read_outcomes(root)
        report = feedback.build_performance_report(outcomes, now=now)
        outcome_digest = digest.build_performance_digest(outcomes, now=now)
    except MvpRuntimeError as exc:
        outcomes, report, outcome_digest = [], None, None
        warnings.append(f"outcome store unreadable ({exc.reason_code})")

    try:
        active = pool.load_active_pool(root)
        status_counts: dict[str, int] = {}
        for entry in active.get("active_strategies") or []:
            status = str(entry.get("status") or "?")
            status_counts[status] = status_counts.get(status, 0) + 1
    except MvpRuntimeError as exc:
        active, status_counts = {"active_strategies": []}, {}
        warnings.append(f"active pool unreadable ({exc.reason_code})")

    try:
        cf_records = counterfactual.read_counterfactual_outcomes(root)
        cf_summary = counterfactual.summarize_counterfactuals(cf_records)
    except MvpRuntimeError as exc:
        cf_records, cf_summary = [], {}
        warnings.append(f"counterfactual store unreadable ({exc.reason_code})")

    # Every book, not just one: positions are keyed per (venue, symbol, timeframe),
    # so a dashboard reading a single slot would under-report open exposure.
    open_positions: list[dict[str, Any]] = []
    try:
        for context, position in paper.list_open_positions(root):
            open_positions.append({"context": context.key,
                                   "symbol": context.symbol,
                                   "timeframe": context.timeframe,
                                   "position_id": position.get("position_id"),
                                   "direction": position.get("direction"),
                                   "strategy_id": position.get("strategy_id"),
                                   "entry_price": position.get("entry_price"),
                                   "opened_at": position.get("opened_at_utc")})
    except MvpRuntimeError as exc:
        warnings.append(f"position state unreadable ({exc.reason_code})")
    open_position = open_positions[0] if open_positions else None

    last_cycle = cycle_rows[-1] if cycle_rows else None
    return {
        "created_at": now,
        "cycles_seen": len(cycle_rows),
        "last_cycle": {
            "at": last_cycle.get("created_at"),
            "verdict": last_cycle.get("verdict_status"),
            "route": last_cycle.get("route_status"),
            "feeds": last_cycle.get("feeds"),
            "degraded": last_cycle.get("degraded"),
            "reason_codes": last_cycle.get("reason_codes"),
        } if last_cycle else None,
        "open_position": open_position,
        "open_positions": open_positions,
        "pool_status_counts": status_counts,
        "pool_size": len(active.get("active_strategies") or []),
        "performance": {
            "closed_count": report.get("sample_size") if report else None,
            "expectancy": (report.get("summary") or {}).get("expectancy") if report else None,
            "max_drawdown": (report.get("summary") or {}).get("max_drawdown") if report else None,
            "recommendation": report.get("recommendation") if report else None,
        },
        "digest": {
            "weekly_trend": (outcome_digest or {}).get("weekly_trend"),
            "monthly_trend": (outcome_digest or {}).get("monthly_trend"),
        } if outcome_digest else None,
        "counterfactual_by_reason": cf_summary,
        "counterfactual_closed": sum(1 for r in cf_records if r.get("outcome_closed") is True),
        "grants": _grants(root),
        "warnings": warnings,
    }


def render_status_text(status: dict[str, Any]) -> str:
    lines = ["=== crypto pipeline dashboard ==="]
    last = status.get("last_cycle")
    if last:
        lines.append(f"last cycle  : {last['at']}  verdict={last['verdict']} route={last['route']} "
                     f"feeds={last['feeds']}")
        if last.get("reason_codes"):
            lines.append(f"reasons     : {', '.join(last['reason_codes'])}")
    else:
        lines.append("last cycle  : (no cycle records yet)")
    position = status.get("open_position")
    lines.append(
        "position    : "
        + (f"{position['direction']} {position['strategy_id']} @ {position['entry_price']}" if position else "none")
    )
    lines.append(f"pool        : {status['pool_size']} strategies {status['pool_status_counts']}")
    perf = status["performance"]
    lines.append(f"performance : {perf['closed_count']} closed, expectancy {perf['expectancy']}R, "
                 f"dd {perf['max_drawdown']}R, recommend {perf['recommendation']}")
    if status.get("digest"):
        for label in ("weekly_trend", "monthly_trend"):
            trend = status["digest"].get(label) or {}
            lines.append(f"{label:12}: {trend.get('verdict')}"
                         + (f" (Δ {trend.get('expectancy_delta_R')}R)" if trend.get("expectancy_delta_R") is not None else ""))
    if status.get("counterfactual_by_reason"):
        lines.append(f"gates       : {status['counterfactual_closed']} shadow outcomes")
        for reason, bucket in sorted(status["counterfactual_by_reason"].items()):
            lines.append(f"  {reason:34}: {bucket['closed_count']} closed, expectancy {bucket['expectancy_R']}R "
                         f"({bucket['missed_opportunity']} missed / {bucket['avoided_loss']} avoided)")
    for grant in status.get("grants") or []:
        lines.append(f"grant       : {grant['provider_id']:24} expires {grant['expires_at']}")
    for warning in status.get("warnings") or []:
        lines.append(f"WARNING     : {warning}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Crypto pipeline status board (read-only).")
    parser.add_argument("--json", action="store_true", help="emit the full status as JSON")
    parser.add_argument("--cycles", type=int, default=12, help="how many recent cycle records to read")
    parser.add_argument(
        "--account", action="store_true",
        help="also read the live exchange account (balance/positions/P&L) — makes a network call",
    )
    args = parser.parse_args(argv)
    status = build_status(cycles=args.cycles)

    # Opt-in only: without --account this board keeps its "no gate, no network" posture and
    # reports purely from what the runtime already persisted. The live read is a separate,
    # separately-gated capability, so asking for it has to be deliberate.
    account_snapshot = None
    if args.account:
        account_snapshot, account_record = account.read_account()
        status["account"] = account_record

    if args.json:
        sys.stdout.write(json.dumps(status, ensure_ascii=False, indent=1) + "\n")
    else:
        sys.stdout.write(render_status_text(status) + "\n")
        if args.account:
            sys.stdout.write(account.render_account_text(account_snapshot) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
