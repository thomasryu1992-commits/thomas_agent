"""Pre-window replay: does any family's entry beat a coin flip on the years the search never saw?

``docs/proposals/PRE_WINDOW_REPLAY_V0.1.md`` (pre-registered; Thomas 2026-10-23: Q1 cohort members only, Q2 an
input to mint-reallocation proposals only, Q3 public data back to 2019). Runs in the research sandbox only:
it needs the lake collector (``RESEARCH_CACHE``) and reads the lake snapshot's cohort and null records.

- **Population:** every frozen cohort member on 1h or 4h whose entry reads only feeds that exist before 2024
  (candles, funding, mark/index/premium, taker flow, trade count), with its active coin-flip twin. 1d members
  are replayed on the one window they have and reported, never judged (§4).
- **Windows:** calendar years 2020, 2021, 2022 and 2023-01-01..2023-11-01, each built as a collector at the
  window's end would have held it (``lake.as_of``), with ``WARMUP_BARS`` before the window for indicators.
  Trades are counted when they open inside the window; a position still open at the window's end is
  dropped, because ``backtest._replay`` records closed trades only.
- **Replay:** the live components, as ``backtest_spec`` calls them (``_replay`` with the frame's cost and
  funding), on frames built over the whole fetched span and then sliced, so every row is the point-in-time
  read for its bar. The twin is the member's frozen ``null_spec`` on the same rows plus its seeded coin
  (``forward_cohort_null.null_rows``), the forward walker's own construction.
- **Reading:** the decided H2 stage (``scripts/selection_evidence.family_stage`` and ``calibration``) over the
  member-minus-twin pairs, then the pre-registered LEAD / NULL / ANTI rule per family.

Writes nothing. ``--json`` prints the whole report.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from runtime.mvp_runtime.crypto import forward_cohort, forward_cohort_null, market_data  # noqa: E402
from runtime.mvp_runtime.crypto.backtest import _replay, build_replay_frame, unsuppliable_features  # noqa: E402
from runtime.mvp_runtime.crypto.candidate_identity import candidate_id  # noqa: E402
from runtime.mvp_runtime.crypto.feed_assembly import (  # noqa: E402
    attach_cross_section, attach_feeds, attach_htf, attach_reference,
)
from runtime.mvp_runtime.crypto.null_control import _slice  # noqa: E402
from runtime.mvp_runtime.crypto.pool_state import read_candidates  # noqa: E402
from runtime.mvp_runtime.crypto.strategy import StrategySpec  # noqa: E402
from runtime.mvp_runtime.crypto.template_space import _FEATURE_FEED  # noqa: E402
from runtime.mvp_runtime.errors import ToolBlocked, ToolError  # noqa: E402
from runtime.mvp_runtime.paths import repo_root  # noqa: E402
from scripts.forward_cohort import clustered_pair_mean  # noqa: E402
from scripts.research import lake  # noqa: E402
from scripts.selection_evidence import (  # noqa: E402
    CALIBRATION_SEED, DEFAULT_DRAWS, DEFAULT_Q, MIN_FAMILY_DAYS, calibration, family_pairs, family_stage,
)

JUDGED_TIMEFRAMES = ("1h", "4h")
REPORTED_TIMEFRAMES = ("1d",)
WINDOWS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "1h": (("2020", "2020-01-01T00:00:00Z", "2021-01-01T00:00:00Z"),
           ("2021", "2021-01-01T00:00:00Z", "2022-01-01T00:00:00Z"),
           ("2022", "2022-01-01T00:00:00Z", "2023-01-01T00:00:00Z"),
           ("2023", "2023-01-01T00:00:00Z", "2023-11-01T00:00:00Z")),
    "1d": (("2020", "2020-01-01T00:00:00Z", "2021-01-01T00:00:00Z"),),
}
WINDOWS["4h"] = WINDOWS["1h"]
WARMUP_BARS = 400            # the longest feature windows are 100 bars or settlements; four times that
HOURS = {"1h": 1, "4h": 4, "1d": 24}
PRE_2024_FEEDS = frozenset({
    market_data.FEED_CANDLES, market_data.FEED_FUNDING, market_data.FEED_DERIVATIVE_PRICE,
    market_data.FEED_TAKER_FLOW, market_data.FEED_TRADE_COUNT,
})
LEAD_MIN_POSITIVE_WINDOWS = 3


def _entry_feeds(spec: Mapping[str, Any]) -> set[str]:
    conditions = ((spec.get("entry_rules") or {}).get("conditions")) or []
    names = {str(v) for c in conditions if isinstance(c, Mapping)
             for v in (c.get("feature"), c.get("value_from")) if v}
    return {_FEATURE_FEED.get(name, market_data.FEED_CANDLES) for name in names}


def population(root: Path) -> list[dict[str, Any]]:
    """Members (latest store row) with their active twin, measurable before 2024, fixed before any replay."""
    latest = {candidate_id(r): r for r in read_candidates(root)}
    twins = {str(t["parent_candidate_id"]): t
             for record in forward_cohort_null.active_null_records(root) for t in record.get("members") or []}
    out: list[dict[str, Any]] = []
    for cohort in forward_cohort.read_cohorts(root):
        for member in cohort.get("members") or []:
            cid = str(member["candidate_id"])
            record, twin = latest.get(cid), twins.get(cid)
            if record is None or twin is None:
                continue
            if member["timeframe"] not in JUDGED_TIMEFRAMES + REPORTED_TIMEFRAMES:
                continue
            if not _entry_feeds(record["strategy_spec"]) <= PRE_2024_FEEDS:
                continue
            out.append({"member": dict(member), "spec": record["strategy_spec"], "twin": dict(twin),
                        "cohort_id": cohort["cohort_id"]})
    return sorted(out, key=lambda e: (e["member"]["timeframe"], e["member"]["candidate_id"]))


def _snapshot(symbol: str, timeframe: str, *, end: str, bars: int, root: Path) -> dict[str, Any]:
    """The factory's snapshot, leg for leg, as a collector at ``end`` would have held it."""
    with lake.as_of(end):
        collector = market_data.select_market_data_collector(now=end, root=root)
        snapshot, _ = market_data.collect_market_data(symbol, timeframe, collector=collector, now=end, limit=bars)
        attach_feeds(snapshot, collector=collector,
                     liquidation_feed=market_data.select_liquidation_feed(now=end, root=root), now=end, root=root)
        higher = market_data.HIGHER_TIMEFRAME.get(timeframe)
        attach_htf(snapshot, collector=collector, now=end,
                   limit=market_data.factory_candle_target(higher) if higher else None)
        attach_reference(snapshot, collector=collector, now=end, limit=bars)
        attach_cross_section(snapshot, collector=collector, now=end, limit=bars)
    return snapshot


def _window(frame: Any, start: str) -> Any | None:
    index = next((i for i, c in enumerate(frame.candles) if str(c.get("open_time")) >= start), None)
    return None if index is None or index < WARMUP_BARS // 2 else _slice(frame, index)


def _trades(spec: StrategySpec, windows: Sequence[Any], *, seed: str | None = None) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    for w in windows:
        rows = w.rows if seed is None else forward_cohort_null.null_rows({"seed": seed}, w.rows, w.candles)
        outcomes, *_ = _replay(spec, rows, w.candles, cost=w.cost, funding=w.funding)
        out.extend((str(o["created_at_utc"])[:10], float(o["result_R"])) for o in outcomes)
    return out


def replay(root: Path, entries: Sequence[Mapping[str, Any]], *,
           say: Any = lambda *_a, **_k: None) -> list[dict[str, Any]]:
    """One row per (member, window): its trades and its twin's, or why it was not replayed."""
    by_context: dict[tuple[tuple[str, ...], str], list[Mapping[str, Any]]] = defaultdict(list)
    for entry in entries:
        spec = entry["spec"]
        by_context[(tuple(spec["symbol_scope"]), spec["timeframe"])].append(entry)
    rows: list[dict[str, Any]] = []
    for (scope, timeframe), group in sorted(by_context.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        for label, start, end in WINDOWS[timeframe]:
            span = timeutil.parse_iso(end) - timeutil.parse_iso(start)
            bars = int(span / timedelta(hours=HOURS[timeframe])) + WARMUP_BARS
            say(f"  {'+'.join(scope)} {timeframe} {label}: {len(group)} member(s)", file=sys.stderr, flush=True)
            try:
                frames = [build_replay_frame(_snapshot(s, timeframe, end=end, bars=bars, root=root)) for s in scope]
            except (ToolError, ToolBlocked) as exc:
                rows += [{"candidate_id": e["member"]["candidate_id"], "window": label,
                          "status": f"no_data:{exc.reason_code}"} for e in group]
                continue
            windows = [_window(frame, start) for frame in frames]
            if any(w is None for w in windows):
                rows += [{"candidate_id": e["member"]["candidate_id"], "window": label, "status": "no_window"}
                         for e in group]
                continue
            for entry in group:
                spec = StrategySpec.from_dict(entry["spec"])
                twin_spec = StrategySpec.from_dict(entry["twin"]["null_spec"])
                if any(unsuppliable_features(spec, w.rows) for w in windows):
                    rows.append({"candidate_id": entry["member"]["candidate_id"], "window": label,
                                 "status": "unsuppliable"})
                    continue
                rows.append({
                    "candidate_id": entry["member"]["candidate_id"], "window": label, "status": "replayed",
                    "member": _trades(spec, windows),
                    "twin": _trades(twin_spec, windows, seed=str(entry["twin"]["seed"])),
                })
            del frames, windows
    return rows


def read(entries: Sequence[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]], *,
         q: float = DEFAULT_Q, min_days: int = MIN_FAMILY_DAYS, draws: int = DEFAULT_DRAWS,
         seed: int = CALIBRATION_SEED) -> dict[str, Any]:
    """The H2 stage over all windows, the per-window sign, and the pre-registered reading per family."""
    member_of = {e["member"]["candidate_id"]: e["member"] for e in entries}
    pooled: dict[str, tuple[list, list]] = {}
    per_window: dict[str, list[tuple[Mapping[str, Any], Any]]] = defaultdict(list)
    for row in rows:
        if row.get("status") != "replayed":
            continue
        member = member_of[row["candidate_id"]]
        m, t = pooled.setdefault(row["candidate_id"], ([], []))
        m += row["member"]
        t += row["twin"]
        per_window[row["window"]].append((member, (row["member"], row["twin"])))
    report: dict[str, Any] = {}
    for timeframe in JUDGED_TIMEFRAMES + REPORTED_TIMEFRAMES:
        pairs = [(member_of[cid], pair) for cid, pair in pooled.items() if member_of[cid]["timeframe"] == timeframe]
        groups = {family: p for (tf, family), p in family_pairs(pairs).items() if tf == timeframe}
        if not groups:
            continue
        signs: dict[str, dict[str, float | None]] = defaultdict(dict)
        for label, window_pairs in per_window.items():
            for (tf, family), p in family_pairs(window_pairs).items():
                if tf == timeframe:
                    summary = clustered_pair_mean(p)
                    signs[family][label] = None if summary is None else summary["mean_diff_r"]
        block: dict[str, Any] = {"all": clustered_pair_mean([p for ps in groups.values() for p in ps]),
                                 "per_window": {f: dict(sorted(s.items())) for f, s in sorted(signs.items())}}
        if timeframe in JUDGED_TIMEFRAMES:
            families = family_stage(groups, q=q, min_days=min_days)
            for fam in families:
                positive = sum(1 for v in signs[fam["family"]].values() if v is not None and v > 0)
                fam["positive_windows"] = positive
                fam["reading"] = reading(fam, positive)
            block["families"] = families
            block["calibration"] = calibration(groups, q=q, min_days=min_days, draws=draws, seed=seed)
            block["overall"] = ("LEAD_FOUND" if any(f["reading"] == "LEAD" for f in families)
                                else "NO_TIMING_EDGE_BEFORE_THE_WINDOW")
        report[timeframe] = block
    return report


def reading(family: Mapping[str, Any], positive_windows: int) -> str:
    """PRE_WINDOW_REPLAY_V0.1 §4: LEAD, NULL or ANTI for one family."""
    mean = family.get("mean_diff_r")
    if family.get("bh_pass") and isinstance(mean, (int, float)):
        if mean > 0 and positive_windows >= LEAD_MIN_POSITIVE_WINDOWS:
            return "LEAD"
        if mean < 0:
            return "ANTI"
    return "NULL"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pre_window_replay", description=__doc__.splitlines()[0])
    parser.add_argument("--list", action="store_true", help="print the fixed population and exit; replays nothing")
    parser.add_argument("--json", action="store_true", help="print the whole report as JSON")
    args = parser.parse_args(argv)
    # The sandbox's sitecustomize installs the lake selectors; anywhere else the runtime's collector would
    # reach the venue, and the as-of cut would not exist.
    if getattr(market_data.select_market_data_collector, "__module__", "") != lake.__name__:
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    root = repo_root()
    try:
        entries = population(root)
    except (ToolError, ToolBlocked) as exc:
        print(f"BLOCKED: {exc.reason_code}", file=sys.stderr)
        return EXIT_BLOCKED
    counts = defaultdict(int)
    for e in entries:
        counts[e["member"]["timeframe"]] += 1
    say = (lambda *_a, **_k: None) if args.json else print
    say(f"population: {dict(sorted(counts.items()))} (fixed before the replay)")
    if args.list:
        for e in entries:
            m = e["member"]
            print(f"  {m['timeframe']:3s} {m['candidate_id']} {m['strategy_family']:28s} twin {e['twin']['null_id']}")
        return EXIT_OK
    rows = replay(root, entries, say=print)
    report = {
        "measured_at": timeutil.utc_now_iso(),
        "population": [{"candidate_id": e["member"]["candidate_id"], "null_id": e["twin"]["null_id"],
                        "timeframe": e["member"]["timeframe"], "strategy_family": e["member"]["strategy_family"]}
                       for e in entries],
        "replays": [{k: (len(v) if k in ("member", "twin") else v) for k, v in row.items()} for row in rows],
        "reading": read(entries, rows),
    }
    if args.json:
        print(json.dumps(report, indent=1, sort_keys=True))
        return EXIT_OK
    for timeframe, block in report["reading"].items():
        whole = block.get("all") or {}
        print(f"{timeframe}: pooled member-minus-twin {whole.get('mean_diff_r')} over {whole.get('days')} days"
              + (f" | overall {block['overall']}" if "overall" in block else " | reported, not judged"))
        for fam in block.get("families") or []:
            print(f"  {fam['family']:24s} pairs {fam['pairs']:3d} mean {fam['mean_diff_r']:+.4f} p {fam['p']} "
                  f"BH {fam['bh_pass']} positive windows {fam['positive_windows']} -> {fam['reading']}")
        if "calibration" in block:
            print(f"  calibration: any-pass rate {block['calibration']['any_pass_rate']} (should be <= {DEFAULT_Q})")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
