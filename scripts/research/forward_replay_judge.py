"""Historical forward replay, the judge: merge the shards, freeze, score each anchor forward, and read §4.

``docs/proposals/HISTORICAL_FORWARD_REPLAY_V0.1.md``. A separate module from ``forward_replay`` on purpose: the
shards carry that module's code digest, and nothing written after they started may change it.

- **Shards:** every ``*.jsonl`` under ``--shards``. Per arm, all seven contexts, one code digest, and every day of
  the fire span fired (or recorded why not). Anything short of that is BLOCKED, never judged on what is there.
- **Memory (one arm per process):** an arm's shards are ~94,000 candidates, ~5 GB as dicts, and the sandbox holds
  700 MB. :func:`load_shards` streams them into a *selection store*: a candidate id no row of which can ever be a
  member (``eligible_members`` on the row alone) keeps only what the door's attempt count reads
  (:func:`slim_row`), and the rest drop the evidence blocks selection never reads (``SELECTION_DROPS``). The
  members' full rows are read again for scoring (:func:`member_rows`). Measured on real shards: the same members
  and null twins at every anchor as the full store (``docs/history/2026-11-06-forward-replay-judge-memory.md``).
- **Freeze and score:** ``forward_replay.freeze_all`` at the 55 anchors, ``score_cohort`` on frames cut at each
  window's end plus the hold allowance (:func:`scoring_frame`, the mining snapshot at a scoring depth).
- **Checks (§4):** the fire leak records (``forward_replay leak-check`` output, one per arm × context × the first,
  middle and last anchor, all identical, same digest), the scoring leak check (:func:`scoring_leak_check`, the
  same three anchors on a cache cut at the window's end), and the real-cohort sanity rule.
- **Reading:** ``forward_replay.arm_reading`` and ``verdicts`` per arm; B against A paired by anchor (B_BETTER).
- **Designs (:data:`DESIGNS`):** ``original`` is the study above. ``heldout``
  (``HISTORICAL_FORWARD_REPLAY_HELDOUT_V0.1.md``) is the same judge over ``forward_replay_heldout``'s two contexts
  and fire span, without the real-cohort sanity rule (no real cohort ever held its symbols; the proposal says what
  stands in for it). Each design reads only shards of its own spec.

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
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

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
from scripts.research import forward_replay_heldout as heldout  # noqa: E402
from scripts.research import lake  # noqa: E402

SPEC = fr.SPEC
WARMUP_BARS = 400                     # pre_window_replay's: four times the longest feature window
SANITY_MIN_MEMBERS = 1
SANITY_SIZE_RATIO = 4.0
B_BETTER_ALPHA = 0.05
# A slim row's derivation: not in forward_cohort.PROMOTABLE_DERIVATION_TYPES, so the door skips the row as it would
# have skipped the full one, while the row still charges its context in attempts_by_context.
SLIM_DERIVATION = "replay_judge_slim"
# Evidence blocks the selection chain (rank_candidates, eligible_members) does not read. regime_breakdown and
# distribution_reference are NOT here although selection ignores them: the null twin reads them, and it gets the
# member's full row anyway (member_rows), so dropping them would be safe but was measured, not assumed.
SELECTION_DROPS = ("regime_breakdown", "per_symbol", "walk_forward", "distribution_reference", "liquidation_guard",
                   "intrabar_gap", "entry_cost_door", "cooldown_door")


@dataclass(frozen=True)
class Design:
    """What a study's shards must be: its spec, its contexts, the days each fires, and whether the real cohorts
    are its sanity rule."""
    name: str
    spec: str
    contexts: tuple[fr.Context, ...]
    fire_span: Callable[[], tuple[date, date]]
    real_cohort_sanity: bool


ORIGINAL = Design("original", fr.SPEC, fr.CONTEXTS, fr.fire_span, True)
HELDOUT = Design("heldout", heldout.SPEC, heldout.CONTEXTS, heldout.fire_span, False)
DESIGNS = {d.name: d for d in (ORIGINAL, HELDOUT)}
BY_SPEC = {d.spec: d for d in DESIGNS.values()}


# --- shards ------------------------------------------------------------------------------------------------

def shard_lines(path: Path) -> Iterator[dict[str, Any]]:
    """A shard's rows one at a time, the manifest first (``forward_replay.read_shard`` reads the whole file)."""
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue                                         # a torn last line, as read_shard skips it


def _head(path: Path) -> dict[str, Any] | None:
    for row in shard_lines(path):
        return row.get("manifest")
    return None


def _candidates(path: Path) -> Iterator[dict[str, Any]]:
    rows = shard_lines(path)
    next(rows, None)
    for fire in rows:
        yield from fire.get("candidates") or []


def can_be_member(record: Mapping[str, Any]) -> bool:
    """The door's per-row filters alone: a row that fails them is never a member, whatever else is in the store."""
    return bool(forward_cohort.eligible_members([record], {}))


def slim_row(record: Mapping[str, Any]) -> dict[str, Any]:
    """What a never-member row contributes to the selection: its id and time, and its attempt charge."""
    evidence = record.get("backtest_evidence") if isinstance(record.get("backtest_evidence"), Mapping) else {}
    kept: dict[str, Any] = {}
    if isinstance(evidence.get("ablation"), Mapping) and "lattice_size" in evidence["ablation"]:
        kept["ablation"] = {"lattice_size": evidence["ablation"]["lattice_size"]}
    if isinstance(evidence.get("holdout"), Mapping) and "symbols" in evidence["holdout"]:
        kept["holdout"] = {"symbols": evidence["holdout"]["symbols"]}
    if "symbols_replayed" in evidence:
        kept["symbols_replayed"] = evidence["symbols_replayed"]
    spec = record.get("strategy_spec") if isinstance(record.get("strategy_spec"), Mapping) else {}
    return {"candidate_id": candidate_id(record), "created_at_utc": record.get("created_at_utc"),
            "derivation_type": SLIM_DERIVATION,
            "strategy_spec": {"symbol_scope": spec.get("symbol_scope"), "timeframe": spec.get("timeframe")},
            "backtest_evidence": kept}


def selection_row(record: Mapping[str, Any]) -> dict[str, Any]:
    evidence = record.get("backtest_evidence")
    if not isinstance(evidence, Mapping):
        return dict(record)
    return {**record, "backtest_evidence": {k: v for k, v in evidence.items() if k not in SELECTION_DROPS}}


def load_shards(directory: Path, *, arm: str | None = None, design: Design = ORIGINAL,
                ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any], list[str]]:
    """``{arm: selection store}``, ``{arm: code_digest}`` and the reasons the set cannot be judged (empty when it
    can). ``arm`` loads that arm's store only; the checks still cover every shard in the directory."""
    first, last = design.fire_span()
    want_days = {(first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)}
    digests: dict[str, set[str]] = defaultdict(set)
    seen: dict[str, set[str]] = defaultdict(set)
    paths: dict[str, list[Path]] = defaultdict(list)
    problems: list[str] = []
    for path in sorted(directory.glob("*.jsonl")):
        head = _head(path)
        if head is None or head.get("spec") != design.spec or head.get("shard_version") != fr.SHARD_VERSION:
            problems.append(f"{path.name}: not a shard of {design.spec}")
            continue
        shard_arm, context = head["arm"], head["context"]
        if context in seen[shard_arm]:
            problems.append(f"{path.name}: a second shard for {shard_arm}/{context}")
            continue
        seen[shard_arm].add(context)
        digests[shard_arm].add(head["code_digest"])
        rows = shard_lines(path)
        next(rows, None)
        missing = want_days - {fire["day"] for fire in rows}
        if missing:
            problems.append(f"{path.name}: {len(missing)} fire days missing (first {min(missing)})")
        paths[shard_arm].append(path)
    for name in sorted(seen):
        absent = sorted({c.name for c in design.contexts} - seen[name])
        if absent:
            problems.append(f"arm {name}: no shard for {absent}")
        if len(digests[name]) != 1:
            problems.append(f"arm {name}: shards of {len(digests[name])} code digests")
    stores: dict[str, list[dict[str, Any]]] = {}
    for name in sorted(paths):
        if arm is not None and name != arm:
            continue
        members = {candidate_id(r) for path in paths[name] for r in _candidates(path) if can_be_member(r)}
        stores[name] = [selection_row(r) if candidate_id(r) in members else slim_row(r)
                        for path in paths[name] for r in _candidates(path)]
    return stores, {a: next(iter(d)) for a, d in digests.items() if len(d) == 1}, problems


def member_rows(directory: Path, arm: str, cids: Iterable[str], *, spec: str = SPEC) -> dict[str, dict[str, Any]]:
    """The full rows of ``cids``, the last of each in shard order (the full store's ``latest``)."""
    wanted, out = set(cids), {}
    for path in sorted(directory.glob("*.jsonl")):
        head = _head(path)
        if head is None or head.get("arm") != arm or head.get("spec") != spec:
            continue
        for record in _candidates(path):
            cid = candidate_id(record)
            if cid in wanted:
                out[cid] = record
    return out


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
              build: Callable[..., Any] = scoring_frame,
              full_rows: Callable[[set[str]], Mapping[str, Mapping[str, Any]]] | None = None,
              ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Mapping[str, Any]]]:
    """The arm's replayed cohorts, one scored row per anchor, and the members' rows they were scored from.
    ``full_rows`` returns the members' full rows (:func:`member_rows`); without it the store's own rows are used."""
    cohorts = fr.freeze_all(store, fr.replay_anchors())
    cids = {m["candidate_id"] for c in cohorts for m in c["members"]}
    latest = dict(full_rows(cids)) if full_rows else {candidate_id(r): r for r in store}
    rows = []
    for cohort in cohorts:
        rows.append(fr.score_cohort(cohort, latest, frames_for=frames_source(arm, cohort["anchor"], state=state,
                                                                           build=build)))
    return cohorts, rows, latest


# --- the checks -------------------------------------------------------------------------------------------

def check_anchors() -> list[date]:
    at = fr.replay_anchors()
    return [at[0], at[len(at) // 2], at[-1]]


def fire_leak_ok(records: Iterable[Mapping[str, Any]], digests: Mapping[str, str], *,
                 contexts: Sequence[fr.Context] = fr.CONTEXTS) -> tuple[bool, list[str]]:
    """Every arm × context × check anchor has an identical leak record of the shards' own code."""
    have = {(r.get("arm", "A"), r.get("context"), r.get("day")): r for r in records}
    missing = []
    for arm, digest in digests.items():
        for context in contexts:
            for anchor in check_anchors():
                r = have.get((arm, context.name, anchor.isoformat()))
                if r is None or r.get("identical") is not True or r.get("code_digest") != digest:
                    missing.append(f"{arm}/{context.name}/{anchor.isoformat()}")
    return not missing, missing


def scoring_leak_check(arm: str, cohorts: Sequence[Mapping[str, Any]], latest: Mapping[str, Mapping[str, Any]], *,
                       cache_root: Path, work: Path, state: Path) -> dict[str, Any]:
    """The check anchors scored on the whole cache and on one cut at the latest window end: the same values."""
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

def judge_arm(arm: str, shards: Path, leak_records: Path, *, work: Path | None = None,
              design: Design = ORIGINAL) -> tuple[int, dict[str, Any]]:
    """One arm's report: its fire leak records, reading, anchors, scoring leak check and (arm A, where the design
    has it) the sanity rule."""
    stores, digests, problems = load_shards(shards, arm=arm, design=design)
    if problems or arm not in stores:
        return EXIT_BLOCKED, {"spec": design.spec, "arm": arm, "verdict": "BLOCKED",
                              "problems": problems or [f"no shards for arm {arm}"]}
    records = []
    for path in sorted(leak_records.glob("*.json")):
        text = path.read_text(encoding="utf-8")
        try:     # the sandbox may append its own "research run ..." line after the report
            records.append(json.loads(text[text.index("{"):text.rindex("}") + 1]))
        except ValueError:
            continue
    leak_fires, leak_missing = fire_leak_ok(records, {arm: digests[arm]}, contexts=design.contexts)
    cache = Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache")))
    report: dict[str, Any] = {"spec": design.spec, "arm": arm, "code_digest": digests[arm], "fire_leak_ok": leak_fires,
                              "fire_leak_missing": leak_missing}
    with tempfile.TemporaryDirectory(prefix="forward-replay-judge-") as tmp:
        state, cut_root = Path(tmp), work or Path(os.environ.get("RESEARCH_RUN") or tmp)
        store = stores[arm]
        cohorts, rows, latest = score_arm(arm, store, state=state,
                                          full_rows=lambda cids: member_rows(shards, arm, cids,
                                                                             spec=design.spec))
        report["reading"], report["anchors"] = fr.arm_reading(rows), rows
        report["scoring_leak"] = scoring_leak_check(arm, cohorts, latest, cache_root=cache, work=cut_root, state=state)
        if arm == "A" and design.real_cohort_sanity:
            report["sanity"] = sanity(store, cohorts, forward_cohort.read_cohorts(repo_root()))
    return EXIT_OK, report


def combine(reports: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The verdicts over the per-arm reports: Holm across arms, the leak checks of every arm, A's sanity rule
    (where the reports' design has one). Every report must be of one design."""
    by_arm = {r["arm"]: r for r in reports}
    specs = {r.get("spec") for r in reports}
    design = BY_SPEC.get(next(iter(specs))) if len(specs) == 1 else None
    problems = [f"arm {r.get('arm')}: {r.get('verdict')}" for r in reports if "reading" not in r]
    if design is None:
        problems.append(f"reports of {sorted(map(str, specs))}, not one known design")
    if len(by_arm) != len(reports):
        problems.append("two reports for one arm")
    if "A" not in by_arm:
        problems.append("no arm A report (the sanity rule is A's)")
    if problems or design is None:
        return {"spec": design.spec if design else SPEC, "verdict": "BLOCKED", "problems": problems}
    leak_ok = all(r["fire_leak_ok"] and r["scoring_leak"]["identical"] for r in by_arm.values())
    sanity_ok = bool(by_arm["A"]["sanity"]["passed"]) if design.real_cohort_sanity else True
    out: dict[str, Any] = {"spec": design.spec, "design": design.name,
                           "sanity": "real_cohorts" if design.real_cohort_sanity else "not_applicable",
                           "code_digests": {a: r["code_digest"] for a, r in by_arm.items()},
                           "leak_ok": leak_ok, "sanity_ok": sanity_ok,
                           "readings": {a: r["reading"] for a, r in sorted(by_arm.items())},
                           "verdicts": fr.verdicts({a: r["reading"] for a, r in by_arm.items()}, leak_ok=leak_ok,
                                                   sanity_ok=sanity_ok)}
    if "A" in by_arm and "B" in by_arm:
        out["b_against_a"] = b_against_a(by_arm["A"]["anchors"], by_arm["B"]["anchors"])
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="forward_replay_judge", description=__doc__.splitlines()[0])
    parser.add_argument("--arm", choices=fr.ARMS, help="judge this arm (one arm per process: the sandbox's memory)")
    parser.add_argument("--design", choices=sorted(DESIGNS), default=ORIGINAL.name,
                        help="which study's shards (default: the original)")
    parser.add_argument("--shards", type=Path, help="the directory holding every arm's shards")
    parser.add_argument("--leak-records", type=Path,
                        help="a directory of forward_replay leak-check outputs (JSON), one per arm/context/anchor")
    parser.add_argument("--work", type=Path, default=None, help="where cut caches are written (default: run dir)")
    parser.add_argument("--combine", type=Path, nargs="+", default=None,
                        help="instead: the per-arm reports to read the verdicts from")
    args = parser.parse_args(argv)
    if args.combine:
        reports = []
        for path in args.combine:
            text = path.read_text(encoding="utf-8")
            reports.append(json.loads(text[text.index("{"):text.rindex("}") + 1]))
        out = combine(reports)
        print(json.dumps(out, indent=1, sort_keys=True, default=str))
        return EXIT_BLOCKED if out.get("verdict") == "BLOCKED" else EXIT_OK
    if not (args.arm and args.shards and args.leak_records):
        print("usage: --arm A|B --shards DIR --leak-records DIR, or --combine REPORT...", file=sys.stderr)
        return EXIT_USAGE
    if not fr._in_sandbox():
        print("refused: the judge runs in the research sandbox only (it reads the lake's real cohorts)",
              file=sys.stderr)
        return EXIT_USAGE
    code, report = judge_arm(args.arm, args.shards, args.leak_records, work=args.work, design=DESIGNS[args.design])
    print(json.dumps(report, indent=1, sort_keys=True, default=str))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
