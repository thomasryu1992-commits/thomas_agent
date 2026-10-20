"""Does the sub-daily OI holdout edge survive the 2026-10-20 lookahead fix? A paired, read-only replay.

Until 2026-10-20 the daily OI reading was aligned on its label (the period's start) while carrying the
period's close, so every 1h/4h bar of day D read day D's close early (``features.OI_READING_KNOWN_AFTER_HOURS``,
``docs/history/2026-10-20-oi-daily-lookahead.md``). The evidence the research documents call "the only
signal" — the 4h OI holdouts — was produced under it. Thomas 2026-10-20: flag that evidence, do not
re-judge it; measure instead. The question, the method and the reading criteria were written before
this ran (``docs/proposals/CRYPTO_ARCHIVE_BACKFILL_V0.1.md``, "결정 (Thomas 2026-10-20)").

**Paired.** Every selected candidate's frozen spec is replayed twice over the SAME current snapshot and
holdout window: (L) the legacy alignment (reading keyed at its label, i.e. the known-after shift set to
0 h) and (F) the fixed one. Only the OI alignment differs between the two, so the difference is the
lookahead and nothing else. The window is today's, as ``rescore_stale_holdout_candidates`` replays
today's: a different draw from the stored one, the same draw for (L) and (F).

**Read-only.** It reads the candidate store and collects market data the way the factory does
(``rescore_stale_holdout_candidates._build_snapshot``). It writes nothing: no candidate, no verdict, no
state. Its output is a report for Thomas to paste into the record. Run in the container, module form::

    docker exec thomas-scheduler python -m scripts.measure_oi_lookahead --list   # the container with the OI feed
    docker exec thomas-scheduler python -m scripts.measure_oi_lookahead            # lines
    docker exec thomas-scheduler python -m scripts.measure_oi_lookahead --json     # the full report

If every row prints "starved", the container lacks the OI feed (``MVP_LIQUIDATION_FEED``): it is set on
``thomas-scheduler`` and ``thomas-scheduler-maint``.

**Reading** (pre-registered, judged on F): edge kept when the pooled per-trade mean net R is above 0 and
the pooled period-level 95% lower bound is above 0 and at least half of L's CONFIRMED stay CONFIRMED in
F; vanished when the pooled mean is at or below 0, or 0-1 stay CONFIRMED; weakened otherwise. The
period bound is ``robustness``'s own period rule over the candidates' period subtotals summed by period
index — the market period is the unit, so siblings that share it are not counted as extra evidence.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from contextlib import contextmanager
from typing import Any, Iterator, Mapping, Sequence

from runtime.mvp_runtime import timeutil
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE
from runtime.mvp_runtime.errors import ToolBlocked, ToolError
from runtime.mvp_runtime.paths import repo_root as _repo_root
from runtime.mvp_runtime.crypto import candidate_identity, features, market_data, pool_state, robustness
from runtime.mvp_runtime.crypto.backtest import backtest_spec_pooled
from runtime.mvp_runtime.crypto.candidate_ranking import OI_LOOKAHEAD_FEATURES
from runtime.mvp_runtime.crypto.factory import build_replay_frame, unsuppliable_features
from runtime.mvp_runtime.crypto.strategy import StrategySpec
from scripts.rescore_stale_holdout_candidates import _build_snapshot

LEGACY_KNOWN_AFTER_HOURS = 0   # the reading keyed at its label: the alignment before the fix
# Every timeframe but 1d: the flag's own rule (`candidate_ranking.oi_lookahead_affected`).
SUB_DAILY = tuple(tf for tf in market_data.TIMEFRAMES if tf != "1d")


def _oi_entry(spec: Mapping[str, Any]) -> bool:
    conditions = ((spec.get("entry_rules") or {}).get("conditions")) or []
    return any(isinstance(c, Mapping) and ({c.get("feature"), c.get("value_from")} & OI_LOOKAHEAD_FEATURES)
               for c in conditions)


def select(records: Sequence[Mapping[str, Any]], *, timeframes: frozenset[str],
           candidate_ids: frozenset[str] | None = None) -> list[dict[str, Any]]:
    """Every sub-daily candidate whose entry reads an OI change or z-score, latest row per id."""
    chosen: dict[str, dict[str, Any]] = {}
    for record in records:
        spec = record.get("strategy_spec") or {}
        if spec.get("timeframe") not in timeframes or not _oi_entry(spec):
            continue
        scope = spec.get("symbol_scope")
        if not isinstance(scope, list) or not scope or not all(isinstance(s, str) and s for s in scope):
            continue
        cid = candidate_identity.candidate_id(record)
        if candidate_ids is not None and cid not in candidate_ids:
            continue
        chosen[cid] = dict(record)
    return [chosen[cid] for cid in sorted(chosen)]


@contextmanager
def oi_alignment(known_after_hours: int) -> Iterator[None]:
    """Build frames under one OI alignment. The constant is read at call time by ``_oi_known_at``."""
    saved = features.OI_READING_KNOWN_AFTER_HOURS
    features.OI_READING_KNOWN_AFTER_HOURS = known_after_hours
    try:
        yield
    finally:
        features.OI_READING_KNOWN_AFTER_HOURS = saved


def holdout_of(spec: StrategySpec, snapshots: Sequence[Mapping[str, Any]], *,
               known_after_hours: int) -> tuple[dict[str, Any], str | None] | None:
    """The spec's holdout block and robustness verdict under one alignment, or None when a frame
    cannot supply its features. The whole replay runs inside the alignment, so nothing it builds
    can fall back to the other one."""
    with oi_alignment(known_after_hours):
        frames = [build_replay_frame(snapshot) for snapshot in snapshots]
        if any(unsuppliable_features(spec, frame.rows) for frame in frames):
            return None
        evidence = backtest_spec_pooled(spec, snapshots, frames=frames)
    verdict = (evidence.get("robustness") or {}).get("verdict")
    return dict(evidence.get("holdout") or {}), (str(verdict) if verdict is not None else None)


def summarize(blocks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Pooled per-trade mean and the period-level 95% lower bound over period subtotals summed by index."""
    trades = sum(int(b.get("closed_count") or 0) for b in blocks)
    total_r = sum(float(b.get("total_R") or 0.0) for b in blocks)
    width = max((len(b.get("period_r") or []) for b in blocks), default=0)
    period_r = [0.0] * width
    period_n = [0] * width
    for block in blocks:
        for k, (r, n) in enumerate(zip(block.get("period_r") or [], block.get("period_trades") or [])):
            period_r[k] += float(r)
            period_n[k] += int(n)
    values = [r for r, n in zip(period_r, period_n) if n > 0]
    lower = None
    if len(values) >= 2 and statistics.stdev(values) > 0:
        stderr = statistics.stdev(values) / math.sqrt(len(values))
        lower = statistics.mean(values) - robustness.t_critical_95(len(values) - 1) * stderr
    return {
        "candidates": len(blocks),
        "trades": trades,
        "mean_net_r": round(total_r / trades, 6) if trades else None,
        "periods_traded": len(values),
        "period_mean_r": round(statistics.mean(values), 6) if values else None,
        "period_lower_95": None if lower is None else round(lower, 6),
        "confirmed": sum(1 for b in blocks if robustness.holdout_status(b) == robustness.HOLDOUT_CONFIRMED),
    }


def read(legacy: Mapping[str, Any], fixed: Mapping[str, Any], *, kept: int) -> str:
    """The pre-registered reading, judged on F: KEPT, VANISHED or WEAKENED."""
    mean = fixed.get("mean_net_r")
    lower = fixed.get("period_lower_95")
    if mean is None or mean <= 0 or int(fixed.get("confirmed") or 0) <= 1:
        return "VANISHED"
    if lower is not None and lower > 0 and kept * 2 >= int(legacy.get("confirmed") or 0):
        return "KEPT"
    return "WEAKENED"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="measure_oi_lookahead", description=__doc__.splitlines()[0])
    parser.add_argument("--timeframe", action="append", dest="timeframes", default=None,
                        help=f"restrict to this timeframe (repeatable; default {', '.join(SUB_DAILY)})")
    parser.add_argument("--candidate-id", action="append", dest="candidate_ids", default=None)
    parser.add_argument("--limit", type=int, default=None, help="measure at most this many candidates")
    parser.add_argument("--list", action="store_true", help="print the selection and exit; collects no data")
    parser.add_argument("--json", action="store_true", help="print the full report as JSON instead of lines")
    args = parser.parse_args(argv)
    timeframes = frozenset(args.timeframes or SUB_DAILY)
    if not timeframes <= set(SUB_DAILY):
        print(f"only sub-daily timeframes are affected: {', '.join(SUB_DAILY)}", file=sys.stderr)
        return EXIT_USAGE

    root = _repo_root()
    now = timeutil.utc_now_iso()
    chosen = select(pool_state.read_candidates(root), timeframes=timeframes,
                    candidate_ids=frozenset(args.candidate_ids) if args.candidate_ids else None)
    if args.limit is not None:
        chosen = chosen[: max(0, args.limit)]
    by_context: dict[tuple[tuple[str, ...], str], list[dict[str, Any]]] = defaultdict(list)
    for record in chosen:
        spec = record["strategy_spec"]
        by_context[(tuple(spec["symbol_scope"]), spec["timeframe"])].append(record)
    say = (lambda *_a, **_k: None) if args.json else print
    say(f"selected: {len(chosen)} sub-daily OI candidate(s) across {len(by_context)} context(s)")
    if args.list:
        for (scope, timeframe), group in sorted(by_context.items()):
            stored = sum(1 for r in group if robustness.holdout_status(
                (r.get("backtest_evidence") or {}).get("holdout")) == robustness.HOLDOUT_CONFIRMED)
            print(f"  {'+'.join(scope):40s} {timeframe:4s} {len(group):4d} rows, {stored} stored CONFIRMED")
        return EXIT_OK

    snapshots: dict[tuple[str, str], dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    blocks: dict[str, list[dict[str, Any]]] = {"L": [], "F": []}
    for (scope, timeframe), group in sorted(by_context.items()):
        try:
            for symbol in scope:
                if (symbol, timeframe) not in snapshots:
                    snapshots[(symbol, timeframe)] = _build_snapshot(symbol, timeframe, now=now, root=root)
        except (ToolBlocked, ToolError) as exc:
            print(f"  {'+'.join(scope)} {timeframe}: BLOCKED ({exc.reason_code})", file=sys.stderr)
            return EXIT_BLOCKED
        legs = [snapshots[(symbol, timeframe)] for symbol in scope]
        for record in group:
            spec = StrategySpec.from_dict(record["strategy_spec"])
            legacy_run = holdout_of(spec, legs, known_after_hours=LEGACY_KNOWN_AFTER_HOURS)
            fixed_run = holdout_of(spec, legs, known_after_hours=features.OI_READING_KNOWN_AFTER_HOURS)
            if legacy_run is None or fixed_run is None:
                rows.append({"candidate_id": candidate_identity.candidate_id(record), "starved": True})
                continue
            (legacy, legacy_verdict), (fixed, fixed_verdict) = legacy_run, fixed_run
            blocks["L"].append(legacy)
            blocks["F"].append(fixed)
            rows.append({
                "candidate_id": candidate_identity.candidate_id(record), "strategy_id": record.get("strategy_id"),
                "family": (record.get("strategy_spec") or {}).get("strategy_family"),
                "scope": list(scope), "timeframe": timeframe,
                **{f"{tag}_{key}": block.get(key) for tag, block in (("L", legacy), ("F", fixed))
                   for key in ("closed_count", "expectancy")},
                "L_status": robustness.holdout_status(legacy), "F_status": robustness.holdout_status(fixed),
                "L_verdict": legacy_verdict, "F_verdict": fixed_verdict,
            })
    legacy_sum, fixed_sum = summarize(blocks["L"]), summarize(blocks["F"])
    kept = sum(1 for row in rows if row.get("L_status") == robustness.HOLDOUT_CONFIRMED
               and row.get("F_status") == robustness.HOLDOUT_CONFIRMED)
    # Counted beside CONFIRMED, as pre-registered; reported only, the reading does not use it.
    for tag, summary in (("L", legacy_sum), ("F", fixed_sum)):
        summary["robust"] = sum(1 for row in rows if row.get(f"{tag}_verdict") == robustness.ROBUST)
    report = {"measured_at": now, "known_after_hours": {"L": LEGACY_KNOWN_AFTER_HOURS,
                                                        "F": features.OI_READING_KNOWN_AFTER_HOURS},
              "candidates": rows, "pooled": {"L": legacy_sum, "F": fixed_sum},
              "confirmed_kept": kept, "reading": read(legacy_sum, fixed_sum, kept=kept)}
    if args.json:
        print(json.dumps(report, indent=1, sort_keys=True))
        return EXIT_OK
    for row in rows:
        if row.get("starved"):
            print(f"  {row['candidate_id']}: starved (a feature this runtime cannot supply), skipped")
            continue
        print(f"  {row['candidate_id']} {row['family']:24s} {row['timeframe']:3s} "
              f"L n={row['L_closed_count']} {row['L_expectancy']:+.4f} {row['L_status']:13s} "
              f"F n={row['F_closed_count']} {row['F_expectancy']:+.4f} {row['F_status']}")
    for tag in ("L", "F"):
        s = report["pooled"][tag]
        print(f"pooled {tag}: {s['candidates']} candidates, {s['trades']} trades, mean {s['mean_net_r']}, "
              f"period lower 95% {s['period_lower_95']} over {s['periods_traded']} periods, "
              f"{s['confirmed']} CONFIRMED, {s['robust']} ROBUST")
    print(f"CONFIRMED in L that stay CONFIRMED in F: {kept}")
    print(f"reading (pre-registered, on F): {report['reading']}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
