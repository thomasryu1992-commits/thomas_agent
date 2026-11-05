"""Historical forward replay, the judge: merge the shards, freeze, score each anchor forward, and read §4.

``docs/proposals/HISTORICAL_FORWARD_REPLAY_V0.1.md``. A separate module from ``forward_replay`` on purpose: the
shards carry that module's code digest, and nothing written after they started may change it.

- **Shards:** every ``*.jsonl`` under ``--shards``. Per arm, all seven contexts, one code digest, and every day of
  the fire span fired (or recorded why not). Anything short of that is BLOCKED, never judged on what is there.
- **Freeze and score:** ``forward_replay.freeze_all`` at the 55 anchors, ``score_cohort`` on frames cut at each
  window's end plus the hold allowance (:func:`scoring_frame`, the mining snapshot at a scoring depth).
- **Checks (§4):** the fire leak records (``forward_replay leak-check`` output, one per arm × context × the first,
  middle and last anchor, all identical, same digest), the scoring leak check (:func:`scoring_leak_check`, the
  same three anchors on a cache cut at the window's end), and the real-cohort sanity rule.
- **Reading:** ``forward_replay.arm_reading`` and ``verdicts`` per arm; B against A paired by anchor (B_BETTER).

In the research sandbox only: it reads the lake's real cohorts. Writes nothing but its report.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import statistics
import sys
import tempfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from runtime.mvp_runtime.crypto import feed_assembly, forward_cohort, market_data  # noqa: E402
from runtime.mvp_runtime.crypto.backtest import build_replay_frame  # noqa: E402
from runtime.mvp_runtime.crypto.candidate_identity import candidate_id  # noqa: E402
from runtime.mvp_runtime.errors import ToolBlocked, ToolError  # noqa: E402
from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.paths import repo_root  # noqa: E402
from scripts.research import forward_replay as fr  # noqa: E402
from scripts.research import lake  # noqa: E402

SPEC = fr.SPEC
WARMUP_BARS = 400                     # pre_window_replay's: four times the longest feature window
SANITY_MIN_MEMBERS = 1
SANITY_SIZE_RATIO = 4.0
B_BETTER_ALPHA = 0.05


# --- shards ------------------------------------------------------------------------------------------------

def load_shards(directory: Path) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any], list[str]]:
    """``{arm: store}``, ``{arm: code_digest}`` and the reasons the set cannot be judged (empty when it can)."""
    first, last = fr.fire_span()
    want_days = {(first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)}
    stores: dict[str, list[dict[str, Any]]] = defaultdict(list)
    digests: dict[str, set[str]] = defaultdict(set)
    seen: dict[str, set[str]] = defaultdict(set)
    problems: list[str] = []
    for path in sorted(directory.glob("*.jsonl")):
        head, fires = fr.read_shard(path)
        if head is None or head.get("spec") != SPEC or head.get("shard_version") != fr.SHARD_VERSION:
            problems.append(f"{path.name}: not a shard of {SPEC}")
            continue
        arm, context = head["arm"], head["context"]
        if context in seen[arm]:
            problems.append(f"{path.name}: a second shard for {arm}/{context}")
            continue
        seen[arm].add(context)
        digests[arm].add(head["code_digest"])
        days = {f["day"] for f in fires}
        missing = want_days - days
        if missing:
            problems.append(f"{path.name}: {len(missing)} fire days missing (first {min(missing)})")
        stores[arm].extend(c for f in fires for c in f.get("candidates") or [])
    for arm in sorted(seen):
        absent = sorted({c.name for c in fr.CONTEXTS} - seen[arm])
        if absent:
            problems.append(f"arm {arm}: no shard for {absent}")
        if len(digests[arm]) != 1:
            problems.append(f"arm {arm}: shards of {len(digests[arm])} code digests")
    return dict(stores), {a: next(iter(d)) for a, d in digests.items() if len(d) == 1}, problems


# --- the scoring frame ----------------------------------------------------------------------------------------

def window_end(anchor: date, timeframe: str) -> str:
    """The instant a member's window can be read up to: FORWARD_DAYS, then the hold allowance in bars."""
    minutes = market_data.TIMEFRAMES[timeframe] * fr.HOLD_ALLOWANCE_BARS
    end = datetime(anchor.year, anchor.month, anchor.day, tzinfo=timezone.utc) + timedelta(days=fr.FORWARD_DAYS)
    return timeutil.format_iso(end + timedelta(minutes=minutes))


def scoring_bars(timeframe: str) -> int:
    span = fr.FORWARD_DAYS * 1440 + market_data.TIMEFRAMES[timeframe] * fr.HOLD_ALLOWANCE_BARS
    return WARMUP_BARS + span // market_data.TIMEFRAMES[timeframe] + 1


def scoring_frame(symbol: str, timeframe: str, *, now: str, state: Path, arm: str) -> Any | None:
    """The mining snapshot's legs at a scoring depth (warm-up plus the window), as one replay frame."""
    with lake.as_of(now):
        collector = market_data.select_market_data_collector(now=now, root=state)
        try:
            built, _ = market_data.collect_market_data(symbol, timeframe, collector=collector, now=now,
                                                       limit=scoring_bars(timeframe))
        except (ToolBlocked, ToolError):
            return None
        feed_assembly.attach_mining_legs(
            built, collector=collector, timeframe=timeframe, now=now, root=state, liquidation_feed=fr._NoFeed(),
            candle_target=scoring_bars, candle_cache=market_data.PeerCandleCache(collector), record_vendor_daily=False)
        if arm == "B":
            try:
                daily, _ = market_data.collect_market_data(market_data.REFERENCE_SYMBOL, "1d", collector=collector,
                                                           now=now, limit=scoring_bars("1d") + 300)
            except (ToolBlocked, ToolError):
                return None
            built[fr.DAILY_KEY] = list(daily.get("candles") or [])
    with (fr.arm_b() if arm == "B" else contextlib.nullcontext()):
        return build_replay_frame(built)


def frames_source(arm: str, anchor: date, *, state: Path,
                  build: Callable[..., Any] = scoring_frame) -> Callable[[Sequence[str], str, str], list[Any]]:
    cache: dict[tuple[str, str], Any] = {}

    def frames_for(scope: Sequence[str], timeframe: str, _end: str) -> list[Any]:
        out = []
        for symbol in scope:
            key = (symbol, timeframe)
            if key not in cache:
                cache[key] = build(symbol, timeframe, now=window_end(anchor, timeframe), state=state, arm=arm)
            if cache[key] is not None:
                out.append(cache[key])
        return out
    return frames_for


def score_arm(arm: str, store: Sequence[Mapping[str, Any]], *, state: Path,
              build: Callable[..., Any] = scoring_frame) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The arm's replayed cohorts and one scored row per anchor."""
    cohorts = fr.freeze_all(store, fr.replay_anchors())
    latest = {candidate_id(r): r for r in store}
    rows = []
    for cohort in cohorts:
        rows.append(fr.score_cohort(cohort, latest, frames_for=frames_source(arm, cohort["anchor"], state=state,
                                                                           build=build)))
    return cohorts, rows


# --- the checks -------------------------------------------------------------------------------------------

def check_anchors() -> list[date]:
    at = fr.replay_anchors()
    return [at[0], at[len(at) // 2], at[-1]]


def fire_leak_ok(records: Iterable[Mapping[str, Any]], digests: Mapping[str, str]) -> tuple[bool, list[str]]:
    """Every arm × context × check anchor has an identical leak record of the shards' own code."""
    have = {(r.get("arm", "A"), r.get("context"), r.get("day")): r for r in records}
    missing = []
    for arm, digest in digests.items():
        for context in fr.CONTEXTS:
            for anchor in check_anchors():
                r = have.get((arm, context.name, anchor.isoformat()))
                if r is None or r.get("identical") is not True or r.get("code_digest") != digest:
                    missing.append(f"{arm}/{context.name}/{anchor.isoformat()}")
    return not missing, missing


def scoring_leak_check(arm: str, cohorts: Sequence[Mapping[str, Any]], store: Sequence[Mapping[str, Any]], *,
                       cache_root: Path, work: Path, state: Path) -> dict[str, Any]:
    """The check anchors scored on the whole cache and on one cut at the latest window end: the same values."""
    latest = {candidate_id(r): r for r in store}
    out = {}
    for cohort in (c for c in cohorts if c["anchor"] in check_anchors()):
        anchor = cohort["anchor"]
        cut_at = max(window_end(anchor, tf) for tf in ("1h", "4h", "1d"))
        full = fr.score_cohort(cohort, latest, frames_for=frames_source(arm, anchor, state=state))
        cut = work / f"cut-score-{arm}-{anchor.isoformat()}"
        fr.truncate_cache(cache_root, cut, cut_at)
        # the cut must not hide anything the window reads: it keeps every row that opened before the latest end
        lake.install(cache=cut, lake=None)
        try:
            bounded = fr.score_cohort(cohort, latest, frames_for=frames_source(arm, anchor, state=state))
        finally:
            lake.install(cache=cache_root, lake=None)
            shutil.rmtree(cut, ignore_errors=True)
        out[anchor.isoformat()] = full["value_R"] == bounded["value_R"] and \
            [r["excess_R"] for r in full["rows"]] == [r["excess_R"] for r in bounded["rows"]]
    return {"identical": bool(out) and all(out.values()), "anchors": out}


def sanity(store: Sequence[Mapping[str, Any]], replayed: Sequence[Mapping[str, Any]],
           real: Sequence[Mapping[str, Any]], *,
           eligible: Callable[..., list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    """§4: the replayed store frozen at each real cohort's instant, against that cohort."""
    eligible = eligible or forward_cohort.eligible_members
    rows, ok = [], True
    for cohort in real:
        instant = str(cohort["frozen_at_utc"])
        day = date.fromisoformat(instant[:10])
        earlier = [c for c in replayed if c["anchor"] < day]
        held = frozenset(m["candidate_id"] for c in earlier for m in c["members"])
        lineages = frozenset(forward_cohort._lineage_key(m) for c in earlier
                             if (day - c["anchor"]).days < fr.COHORT_OPEN_DAYS for m in c["members"])
        members = eligible(fr.made_before(store, instant), {}, exclude_candidate_ids=held, exclude_lineages=lineages)
        real_n, ours = len(cohort.get("members") or []), len(members)
        common = sorted({m["strategy_family"] for m in members} & {m.get("strategy_family") for m in cohort["members"]})
        passed = ours >= SANITY_MIN_MEMBERS and bool(common) and \
            real_n / SANITY_SIZE_RATIO <= ours <= real_n * SANITY_SIZE_RATIO
        ok = ok and passed
        rows.append({"real_cohort": cohort.get("cohort_id"), "frozen_at_utc": instant, "real_members": real_n,
                     "replayed_members": ours, "common_families": len(common), "passed": passed})
    return {"passed": ok and bool(rows), "cohorts": rows}


def b_against_a(a: Sequence[Mapping[str, Any]], b: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    va = {r["anchor"]: r["value_R"] for r in a if r["value_R"] is not None}
    pairs = [vb["value_R"] - va[vb["anchor"]] for vb in b if vb["value_R"] is not None and vb["anchor"] in va]
    stats = fr.t_stats(pairs)
    better = stats["p_one_sided"] is not None and stats["mean_R"] > 0 and stats["p_one_sided"] <= B_BETTER_ALPHA
    return {**stats, "b_better": better}


# --- the command line ---------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="forward_replay_judge", description=__doc__.splitlines()[0])
    parser.add_argument("--shards", type=Path, required=True, help="the directory holding every arm's shards")
    parser.add_argument("--leak-records", type=Path, required=True,
                        help="a directory of forward_replay leak-check outputs (JSON), one per arm/context/anchor")
    parser.add_argument("--work", type=Path, default=None, help="where cut caches are written (default: run dir)")
    args = parser.parse_args(argv)
    if not fr._in_sandbox():
        print("refused: the judge runs in the research sandbox only (it reads the lake's real cohorts)",
              file=sys.stderr)
        return EXIT_USAGE
    stores, digests, problems = load_shards(args.shards)
    if problems:
        print(json.dumps({"spec": SPEC, "verdict": "BLOCKED", "problems": problems}, indent=1))
        return EXIT_BLOCKED
    records = []
    for path in sorted(args.leak_records.glob("*.json")):
        text = path.read_text(encoding="utf-8")
        try:     # the sandbox may append its own "research run ..." line after the report
            records.append(json.loads(text[text.index("{"):text.rindex("}") + 1]))
        except ValueError:
            continue
    leak_fires, leak_missing = fire_leak_ok(records, digests)
    cache = Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache")))
    real = forward_cohort.read_cohorts(repo_root())
    report: dict[str, Any] = {"spec": SPEC, "code_digests": digests, "fire_leak_ok": leak_fires,
                              "fire_leak_missing": leak_missing, "arms": {}}
    with tempfile.TemporaryDirectory(prefix="forward-replay-judge-") as tmp:
        state, work = Path(tmp), args.work or Path(os.environ.get("RESEARCH_RUN") or tmp)
        scored: dict[str, list[dict[str, Any]]] = {}
        cohorts_of: dict[str, list[dict[str, Any]]] = {}
        for arm in sorted(stores):
            cohorts, rows = score_arm(arm, stores[arm], state=state)
            scored[arm], cohorts_of[arm] = rows, cohorts
            report["arms"][arm] = {"reading": fr.arm_reading(rows), "anchors": rows,
                                   "scoring_leak": scoring_leak_check(arm, cohorts, stores[arm], cache_root=cache,
                                                                      work=work, state=state)}
        report["sanity"] = sanity(stores.get("A", []), cohorts_of.get("A", []), real)
    leak_ok = leak_fires and all(r["scoring_leak"]["identical"] for r in report["arms"].values())
    report["verdicts"] = fr.verdicts({a: r["reading"] for a, r in report["arms"].items()}, leak_ok=leak_ok,
                                     sanity_ok=report["sanity"]["passed"])
    if "A" in scored and "B" in scored:
        report["b_against_a"] = b_against_a(scored["A"], scored["B"])
    print(json.dumps(report, indent=1, sort_keys=True, default=str))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
