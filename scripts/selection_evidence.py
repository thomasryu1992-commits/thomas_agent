#!/usr/bin/env python3
"""Does the factory's selection beat a coin flip on the data already collected? Read-only.

    docker exec -u 10001 thomas-scheduler python -m scripts.selection_evidence null-control
    docker exec -u 10001 thomas-scheduler python -m scripts.selection_evidence families
    docker exec -u 10001 thomas-scheduler python -m scripts.selection_evidence verdict

Writes nothing, asks no venue, opens no door (2026-10-07, ``SELECTION_EVIDENCE_V0.1.md``). Two
instruments, kept apart because they measure different populations and only one carries an interval:

- ``null-control`` reads the newest ``crypto_null_control`` fire per (symbol, timeframe) cell. Every
  fire re-replays each selected spec on the bars minted AFTER it against 12 coin-flip draws through
  the same exits (``crypto/null_control.py``), so the newest fire per cell is the whole current
  answer. A pooled spec appears once per leg; its legs are folded trade-weighted into one spec, and
  specs are folded into lineages (``promotion_backlog._lineage_key``: family, scope, timeframe),
  because a parameter tweak of one family on one context is the same bet. **Direction only.** The
  record keeps a per-spec aggregate, not per-day trades, and every spec in a cell shares the same
  post-mint market, so a count of lineages ahead is not a count of independent observations
  (``scripts/family_period_test.py`` states the unit: the market period).
- ``families`` pools the forward cohort's member-minus-twin pairs (``forward_cohort.member_twin_pairs``)
  per timeframe × economic family, with :func:`scripts.forward_cohort.clustered_pair_mean`'s
  day-clustered interval. It then runs the first stage of the hierarchical judgement drafted in
  ``SELECTION_EVIDENCE_V0.1.md`` as a dry run: Benjamini–Hochberg at ``q`` over the families of one
  timeframe that span at least ``MIN_FAMILY_DAYS`` settlement days, and a calibration that swaps
  member and twin inside random pairs (the null of no difference makes the two exchangeable) and
  counts how often anything passes. A dry run is not a verdict: no door, board or ranking reads it.

- ``verdict`` is the hierarchical judgement itself, the rule Thomas decided on 2026-10-07 (H2, and the
  five gaps building it found, decided the same day): read once per epoch boundary, a cohort's close
  (``frozen_at_utc`` + ``COHORT_LIFETIME_DAYS``; the first is 2027-03-22). It refuses before that
  close and while the walker has not reached it, and counts only trades settled by it, so a run on
  any later day gives the same answer. Every frozen cohort's pairs enter (H1's 38 of 75 counts both
  cohorts). Stage 1 is ``families``' BH; if the (centred) swap calibration passes anything in more
  than ``q`` of its draws, the epoch's stage 1 is withheld and stage 2 does not run. Tests stay
  two-sided; a rejection PASSES only when the selection is ahead (behind is BEHIND, reported, never
  carried on). Stage 2 runs inside each passing family only: pairs fold into lineages
  (``_lineage_key``, so a marked sibling counts with the member it names), a lineage under
  ``MIN_FAMILY_DAYS`` is shown and not tested, and Holm at ``HOLM_ALPHA`` runs over every lineage of
  the family. The H1 line beside it is the S1 condition. No door, board or ranking reads it; Thomas
  does (``SELECTION_EVIDENCE_V0.1.md``).

The economic family is a grouping of template names, the one
``RESEARCH_FORWARD_THROUGHPUT_ANALYSIS_V0.1.md`` §4 used, frozen as the judgement's grouping by H2:
:data:`ECONOMIC_FAMILY_MARKERS` changes only by a Thomas decision before the next epoch boundary
(its hash is pinned by a test).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK  # noqa: E402
from runtime.mvp_runtime.crypto import null_control  # noqa: E402
from runtime.mvp_runtime.crypto import forward_cohort, forward_cohort_null  # noqa: E402
from runtime.mvp_runtime.crypto.candidate_identity import candidate_id  # noqa: E402
from runtime.mvp_runtime.crypto.market_data import TIMEFRAMES  # noqa: E402
from runtime.mvp_runtime.crypto.pool_state import read_candidates  # noqa: E402
from runtime.mvp_runtime.crypto.promotion_backlog import _lineage_key  # noqa: E402
from runtime.mvp_runtime.errors import MvpRuntimeError, ToolError  # noqa: E402
from runtime.mvp_runtime.store import LEDGER_REL, LedgerStore  # noqa: E402
from scripts.forward_cohort import CONFIDENCE_Z, clustered_pair_mean, member_twin_pairs  # noqa: E402

# Ordered: the first marker found in a template name wins, so the specific markers sit above the
# general ones (`bollinger_break` before `breakout`, `volatility_expansion` before nothing broader).
# A fused label (`a+b`) is its own group and a mined feature set is the experimental one, as in §4.
ECONOMIC_FAMILY_MARKERS: tuple[tuple[str, str], ...] = (
    ("oi_", "open_interest"), ("funding", "funding"), ("premium", "basis/premium"),
    ("basis", "basis/premium"), ("taker", "order_flow(taker)"), ("positioning", "positioning"),
    ("xs_", "cross_sectional"), ("rel_strength", "relative_strength"), ("session", "time_of_day"),
    ("htf_", "multi_timeframe"), ("volatility_expansion", "volatility_expansion"),
    ("volatility_squeeze", "volatility_compression"), ("bollinger_break", "breakout(bollinger)"),
    ("breakout", "breakout"), ("breakdown", "breakout"), ("mean_reversion", "mean_reversion"),
    ("trend_pullback", "trend_following"), ("ma_cross", "trend_following"), ("macd", "momentum"),
)
EXPERIMENTAL = "mined/experimental"
FUSED = "hybrid(fused)"

# How far back to open archived ledger files. The null-control fire is daily per cell and each fire
# is complete on its own, so two weeks finds every live cell's newest fire with room for outages.
LOOKBACK_DAYS = 14

# A family must span this many settlement days before its interval enters the BH stage. Under it the
# day-clustered variance rests on too few clusters to be read as a normal interval; it is shown, not
# tested.
MIN_FAMILY_DAYS = 10
DEFAULT_Q = 0.10
DEFAULT_DRAWS = 400
CALIBRATION_SEED = 20261007

# The verdict's own constants (Thomas 2026-10-07: H1, H2 and the gaps building it found). Stage 2's
# Holm uses the same 0.10 as stage 1's q, and a lineage needs MIN_FAMILY_DAYS settlement days to be
# tested, as a family does. S1 runs at a boundary only if the 1d line holds S1_MIN_PAIRS pairs (a majority of the
# two cohorts' 75 1d members) and its 95% upper bound is at or above zero.
VERDICT_RULE = "hierarchical_family_verdict.v1"
HOLM_ALPHA = 0.10
S1_MIN_PAIRS = 38
S1_TIMEFRAME = "1d"
# The tests stay two-sided, as decided, so the calibration counts a false rejection either way. A
# rejection only PASSES when the selection is AHEAD of its twins; one where it is behind is BEHIND,
# reported, and never enters stage 2 — a family that loses to coin flips is not where to look first.
PASS, BEHIND, NOT_REJECTED, UNTESTED = "PASS", "BEHIND", "-", "·"
VERDICT_NOT_DUE = "SELECTION_VERDICT_NOT_DUE"
VERDICT_WALK_BEHIND = "SELECTION_VERDICT_WALK_BEHIND"


def economic_family(label: Any) -> str:
    """The display group of one template label (``strategy_family``)."""
    text = str(label or "")
    if not text:
        return "unknown"
    if "mined:" in text or text.startswith("low+") or "index_price" in text:
        return EXPERIMENTAL
    if "+" in text:
        return FUSED
    for marker, family in ECONOMIC_FAMILY_MARKERS:
        if marker in text:
            return family
    return "other:" + text


# --- null-control: the store's selected specs on their post-mint bars --------------------------

def newest_cells(records: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str], tuple[str, Mapping[str, Any]]]:
    """(symbol, timeframe) -> (fire time, cell) for the newest fire of each cell."""
    newest: dict[tuple[str, str], tuple[str, Mapping[str, Any]]] = {}
    for row in records:
        if row.get("kind") != null_control.LEDGER_KIND:
            continue
        record = row.get("record") or {}
        at = str(record.get("created_at") or "")
        for cell in record.get("cells") or []:
            key = (str(cell.get("symbol")), str(cell.get("timeframe")))
            if key not in newest or at >= newest[key][0]:
                newest[key] = (at, cell)
    return newest


def spec_edges(cells: Mapping[tuple[str, str], tuple[str, Mapping[str, Any]]],
               specs: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One row per (timeframe, measured spec): its legs folded trade-weighted."""
    legs: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for (_symbol, timeframe), (_at, cell) in cells.items():
        for m in cell.get("measurements") or []:
            if m.get("status") == "measured" and int(m.get("trades") or 0) > 0:
                legs.setdefault((timeframe, str(m.get("candidate_id"))), []).append(m)
    out = []
    for (timeframe, cid), measured in sorted(legs.items()):
        trades = sum(int(m["trades"]) for m in measured)

        def weighted(field: str) -> float:
            return sum(float(m[field]) * int(m["trades"]) for m in measured) / trades

        spec = specs.get(cid) or {}
        family = str(spec.get("strategy_family") or measured[0].get("strategy_family") or "")
        out.append({
            "timeframe": timeframe, "candidate_id": cid, "strategy_family": family,
            "economic_family": economic_family(family),
            "lineage": _lineage_key({**spec, "strategy_family": family, "timeframe": timeframe}),
            "legs": len(measured), "trades": trades,
            "edge_vs_null": weighted("edge_vs_null"), "real_r_per_trade": weighted("real_r_per_trade"),
            "null_r_per_trade": weighted("null_r_per_trade_median"),
        })
    return out


def lineage_edges(specs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Specs folded into lineages: the plain mean of their edges, one row per lineage."""
    groups: dict[tuple[Any, ...], list[Mapping[str, Any]]] = {}
    for spec in specs:
        groups.setdefault(tuple(spec["lineage"]), []).append(spec)
    return [{
        "timeframe": rows[0]["timeframe"], "strategy_family": rows[0]["strategy_family"],
        "economic_family": rows[0]["economic_family"], "specs": len(rows),
        "edge_vs_null": statistics.fmean(r["edge_vs_null"] for r in rows),
    } for _key, rows in sorted(groups.items(), key=lambda kv: str(kv[0]))]


def summarize(rows: Sequence[Mapping[str, Any]], *keys: str) -> list[dict[str, Any]]:
    """Count, mean and median edge, and how many are ahead of their null, per ``keys``."""
    groups: dict[tuple[str, ...], list[float]] = {}
    for row in rows:
        groups.setdefault(tuple(str(row[k]) for k in keys), []).append(float(row["edge_vs_null"]))
    out = [{**dict(zip(keys, key)), "count": len(edges), "mean_edge": statistics.fmean(edges),
            "median_edge": statistics.median(edges), "ahead": sum(1 for e in edges if e > 0)}
           for key, edges in groups.items()]
    return sorted(out, key=lambda g: (g.get("timeframe", ""), -g["count"], str(g)))


def null_control_evidence(root: Path, *, now: str) -> dict[str, Any]:
    since = timeutil.format_iso(timeutil.parse_iso(now) - timedelta(days=LOOKBACK_DAYS))
    ledger = LedgerStore(root / LEDGER_REL)
    cells = newest_cells(ledger.iter_records_with_archive(appended_since=since,
                                                          kinds=[null_control.LEDGER_KIND]))
    specs = {candidate_id(r): (r.get("strategy_spec") or {}) for r in read_candidates(root)}
    per_spec = spec_edges(cells, specs)
    per_lineage = lineage_edges(per_spec)
    return {
        "cells": {f"{s} {t}": at for (s, t), (at, _cell) in sorted(cells.items())},
        "by_timeframe_specs": summarize(per_spec, "timeframe"),
        "by_timeframe_lineages": summarize(per_lineage, "timeframe"),
        "by_family_lineages": summarize(per_lineage, "timeframe", "economic_family"),
    }


# --- families: the cohort's pairs, pooled per timeframe × economic family ----------------------

def two_sided_p(summary: Mapping[str, Any] | None) -> float | None:
    """The normal two-sided p of a :func:`clustered_pair_mean` summary, read off its interval."""
    if not summary or summary.get("ci_low_r") is None:
        return None
    se = (float(summary["ci_high_r"]) - float(summary["mean_diff_r"])) / CONFIDENCE_Z
    if se <= 0:
        return None
    return math.erfc(abs(float(summary["mean_diff_r"]) / se) / math.sqrt(2))


def benjamini_hochberg(pvalues: Sequence[float], q: float) -> set[int]:
    """Indices BH rejects at ``q`` (step-up: the largest rank whose p clears ``q·rank/m``)."""
    order = sorted(range(len(pvalues)), key=lambda i: pvalues[i])
    cutoff = 0
    for rank, index in enumerate(order, 1):
        if pvalues[index] <= q * rank / len(pvalues):
            cutoff = rank
    return set(order[:cutoff])


def family_pairs(pairs: Iterable[tuple[Mapping[str, Any], Any]]) -> dict[tuple[str, str], list[Any]]:
    groups: dict[tuple[str, str], list[Any]] = {}
    for member, pair in pairs:
        if pair[0] and pair[1]:
            key = (str(member.get("timeframe")), economic_family(member.get("strategy_family")))
            groups.setdefault(key, []).append(pair)
    return groups


def _testable(groups: Mapping[str, Sequence[Any]], min_days: int) -> list[tuple[str, dict[str, Any], float]]:
    out = []
    for family, pairs in sorted(groups.items()):
        summary = clustered_pair_mean(pairs)
        p = two_sided_p(summary)
        if summary is not None and p is not None and summary["days"] >= min_days:
            out.append((family, summary, p))
    return out


def family_stage(groups: Mapping[str, Sequence[Any]], *, q: float, min_days: int) -> list[dict[str, Any]]:
    """One timeframe's families: summary, p, and whether BH passes it (None: shown, not tested)."""
    testable = _testable(groups, min_days)
    passed = benjamini_hochberg([p for _, _, p in testable], q)
    tested = {family: (p, i in passed) for i, (family, _, p) in enumerate(testable)}
    out = []
    for family, pairs in groups.items():
        summary = clustered_pair_mean(pairs)
        if summary is None:
            continue
        p, passes = tested.get(family, (two_sided_p(summary), None))
        out.append({"family": family, **summary, "p": p, "bh_pass": passes})
    return sorted(out, key=lambda g: (-g["pairs"], g["family"]))


def centered(pairs: Sequence[Any]) -> list[Any]:
    """``pairs`` with the family's mean difference taken off every member trade: the null imposed,
    the spread between and within pairs kept."""
    summary = clustered_pair_mean(pairs)
    shift = summary["mean_diff_r"] if summary else 0.0
    return [([(day, net - shift) for day, net in member], twin) for member, twin in pairs]


def calibration(groups: Mapping[str, Sequence[Any]], *, q: float, min_days: int, draws: int,
                seed: int) -> dict[str, Any]:
    """The same stage with member and twin swapped inside random pairs, ``draws`` times, after each
    family is centred (:func:`centered`). Under no difference the swap changes nothing in
    distribution, so the share of draws where any family passes estimates the stage's false-pass
    rate on this data; it should sit at or under ``q``. Centred first (Thomas 2026-10-07): swapping
    pairs that carry a real edge δ makes a ±δ spread the data never had, and the uncentred rate rose
    with the edge (about 0.29 against 0.14 at 1R on synthetic pairs), withholding the very families
    that lead. Centred, a family with an edge calibrates exactly as the same family without it."""
    rng = random.Random(seed)
    any_pass = total = 0
    nulls = {family: centered(pairs) for family, pairs in groups.items()}
    for _ in range(draws):
        flipped = {family: [(t, m) if rng.random() < 0.5 else (m, t) for m, t in pairs]
                   for family, pairs in nulls.items()}
        testable = _testable(flipped, min_days)
        n = len(benjamini_hochberg([p for _, _, p in testable], q)) if testable else 0
        any_pass += n > 0
        total += n
    return {"draws": draws, "any_pass_rate": any_pass / draws if draws else None,
            "mean_passes": total / draws if draws else None}


def family_evidence(root: Path, *, q: float = DEFAULT_Q, min_days: int = MIN_FAMILY_DAYS,
                    draws: int = DEFAULT_DRAWS, seed: int = CALIBRATION_SEED) -> dict[str, Any]:
    by_key = family_pairs(member_twin_pairs(root))
    out: dict[str, Any] = {}
    for timeframe in sorted({tf for tf, _ in by_key}):
        groups = {family: pairs for (tf, family), pairs in by_key.items() if tf == timeframe}
        out[timeframe] = {
            "all": clustered_pair_mean([p for pairs in groups.values() for p in pairs]),
            "families": family_stage(groups, q=q, min_days=min_days),
            "calibration": calibration(groups, q=q, min_days=min_days, draws=draws, seed=seed),
        }
    return out


# --- verdict: the hierarchical judgement, once per epoch boundary ------------------------------

def family_markers_sha256() -> str:
    """The frozen grouping's fingerprint, stamped into every verdict and pinned by a test."""
    return hashlib.sha256(json.dumps(ECONOMIC_FAMILY_MARKERS).encode()).hexdigest()


def holm(pvalues: Sequence[float], alpha: float, *, m: int | None = None) -> set[int]:
    """Indices Holm rejects at ``alpha`` over ``m`` hypotheses (default: the ones given): step-down,
    the k-th smallest p against ``alpha / (m − k + 1)``, stopping at the first that misses."""
    total = len(pvalues) if m is None else m
    order = sorted(range(len(pvalues)), key=lambda i: pvalues[i])
    out: set[int] = set()
    for rank, index in enumerate(order):
        if pvalues[index] > alpha / (total - rank):
            break
        out.add(index)
    return out


def cohort_close(cohort: Mapping[str, Any]) -> str:
    """The cohort's close, its epoch boundary: frozen plus ``COHORT_LIFETIME_DAYS``."""
    frozen = timeutil.parse_iso(str(cohort.get("frozen_at_utc")))
    return timeutil.format_iso(frozen + timedelta(days=forward_cohort.COHORT_LIFETIME_DAYS))


def boundary_cohort(cohorts: Sequence[Mapping[str, Any]], *, now: str,
                    cohort_id: str | None = None) -> tuple[Mapping[str, Any], str]:
    """The cohort whose close the verdict reads, and that close: the named one, or the latest closed.
    VERDICT_NOT_DUE before the close — the rule reads each boundary once, on or after it."""
    at = timeutil.parse_iso(now)
    if cohort_id is not None:
        named = [c for c in cohorts if c.get("cohort_id") == cohort_id]
        if not named:
            raise ToolError(VERDICT_NOT_DUE, f"no frozen cohort {cohort_id!r}")
        close = cohort_close(named[0])
        if timeutil.parse_iso(close) > at:
            raise ToolError(VERDICT_NOT_DUE, f"{cohort_id} closes {close}; the verdict reads it once, after that")
        return named[0], close
    closed = [(cohort_close(c), c) for c in cohorts]
    due = [(close, c) for close, c in closed if timeutil.parse_iso(close) <= at]
    if not due:
        upcoming = min((close for close, _ in closed), default=None)
        raise ToolError(VERDICT_NOT_DUE, f"no cohort has closed yet (next close: {upcoming or 'none frozen'})")
    close, cohort = max(due, key=lambda item: item[0])
    return cohort, close


def walk_behind(root: Path, cohorts: Sequence[Mapping[str, Any]], *, close: str) -> list[str]:
    """The walked contexts, members' and active twins', of cohorts frozen by ``close`` whose newest
    seen bar leaves a bar closed by ``close`` unseen (its next close is not after ``close``). Under
    the daily walk this is the hour between a boundary and the walk after it; reading then would miss
    trades the boundary counts, and a later run would not reproduce it. A context with no mark yet
    counts as behind."""
    cutoff = timeutil.parse_iso(close)
    members = {f"cand:{m.get('candidate_id')}" for c in cohorts
               if timeutil.parse_iso(str(c.get("frozen_at_utc"))) <= cutoff for m in c.get("members") or []}
    parents = {lineage[len("cand:"):] for lineage in members}
    twins = {f"cand:{t.get('null_id')}" for arm in forward_cohort_null.active_null_records(root)
             for t in arm.get("members") or [] if str(t.get("parent_candidate_id")) in parents}
    behind = []
    for book, lineages in ((forward_cohort.load_positions(root), members),
                           (forward_cohort_null.load_null_positions(root), twins)):
        for key, entry in sorted((book.get("entries") or {}).items()):
            if not isinstance(entry, Mapping) or entry.get("lineage") not in lineages:
                continue
            minutes = TIMEFRAMES.get(str(entry.get("timeframe")))
            seen = entry.get("last_seen_candle")
            if not seen or not minutes or timeutil.parse_iso(str(seen)) + timedelta(minutes=minutes) <= cutoff:
                behind.append(f"{key} (last seen {seen or 'none'})")
    return behind


def verdict_label(rejected: bool | None, mean_diff_r: float) -> str:
    if rejected is None:
        return UNTESTED
    if not rejected:
        return NOT_REJECTED
    return PASS if mean_diff_r > 0 else BEHIND


def lineage_stage(entries: Sequence[tuple[Mapping[str, Any], Any]], *, alpha: float,
                  min_days: int) -> list[dict[str, Any]]:
    """Stage 2 inside one passing family: its pairs folded by lineage key, each lineage's clustered
    difference, and Holm at ``alpha`` over every lineage of the family (None: under ``min_days``,
    shown and not tested — it still counts in Holm's ``m``)."""
    by_lineage: dict[tuple[Any, ...], list[tuple[Mapping[str, Any], Any]]] = {}
    for member, pair in entries:
        by_lineage.setdefault(tuple(_lineage_key(member)), []).append((member, pair))
    rows = []
    for key, items in sorted(by_lineage.items(), key=lambda kv: str(kv[0])):
        summary = clustered_pair_mean([pair for _, pair in items])
        if summary is None:
            continue
        rows.append({"lineage": list(key), "members": sorted(str(m.get("candidate_id")) for m, _ in items),
                     **summary, "p": two_sided_p(summary), "holm_pass": None})
    testable = [i for i, r in enumerate(rows) if r["p"] is not None and r["days"] >= min_days]
    passed = holm([rows[i]["p"] for i in testable], alpha, m=len(rows))
    for j, i in enumerate(testable):
        rows[i]["holm_pass"] = j in passed
    for row in rows:
        row["verdict"] = verdict_label(row["holm_pass"], row["mean_diff_r"])
    return sorted(rows, key=lambda r: (r["p"] is None, r["p"] or 0.0))


def s1_condition(whole: Mapping[str, Any] | None) -> str:
    """H1 on the 1d line: EXECUTE, HOLD (upper bound under zero) or WAIT (under S1_MIN_PAIRS)."""
    if not whole or int(whole.get("pairs") or 0) < S1_MIN_PAIRS:
        return "WAIT"
    high = whole.get("ci_high_r")
    return "EXECUTE" if high is not None and float(high) >= 0 else "HOLD"


def verdict(root: Path, *, now: str, cohort_id: str | None = None) -> dict[str, Any]:
    """The hierarchical judgement at one epoch boundary (see the module docstring). Read-only."""
    cohorts = forward_cohort.read_cohorts(root)
    cohort, close = boundary_cohort(cohorts, now=now, cohort_id=cohort_id)
    behind = walk_behind(root, cohorts, close=close)
    if behind:
        raise ToolError(VERDICT_WALK_BEHIND, f"{len(behind)} walked context(s) have not reached the close "
                                             f"{close}, e.g. {behind[0]}; read after the next walk")
    by_key: dict[tuple[str, str], list[tuple[Mapping[str, Any], Any]]] = {}
    for member, pair in member_twin_pairs(root, settled_by=close):
        if pair[0] and pair[1]:
            key = (str(member.get("timeframe")), economic_family(member.get("strategy_family")))
            by_key.setdefault(key, []).append((member, pair))
    timeframes: dict[str, Any] = {}
    for timeframe in sorted({tf for tf, _ in by_key}):
        entries = {family: items for (tf, family), items in by_key.items() if tf == timeframe}
        groups = {family: [pair for _, pair in items] for family, items in entries.items()}
        stage1 = family_stage(groups, q=DEFAULT_Q, min_days=MIN_FAMILY_DAYS)
        cal = calibration(groups, q=DEFAULT_Q, min_days=MIN_FAMILY_DAYS, draws=DEFAULT_DRAWS,
                          seed=CALIBRATION_SEED)
        withheld = cal["any_pass_rate"] is not None and cal["any_pass_rate"] > DEFAULT_Q
        for g in stage1:
            g["verdict"] = verdict_label(g["bh_pass"], g["mean_diff_r"])
        stage2 = {} if withheld else {
            g["family"]: lineage_stage(entries[g["family"]], alpha=HOLM_ALPHA, min_days=MIN_FAMILY_DAYS)
            for g in stage1 if g["verdict"] == PASS}
        timeframes[timeframe] = {
            "all": clustered_pair_mean([pair for pairs in groups.values() for pair in pairs]),
            "calibration": cal, "stage1_withheld": withheld, "families": stage1, "lineages": stage2,
        }
    s1_line = (timeframes.get(S1_TIMEFRAME) or {}).get("all")
    return {
        "rule": {"version": VERDICT_RULE, "q": DEFAULT_Q, "min_days": MIN_FAMILY_DAYS, "draws": DEFAULT_DRAWS,
                 "seed": CALIBRATION_SEED, "holm_alpha": HOLM_ALPHA, "s1_min_pairs": S1_MIN_PAIRS,
                 "family_markers_sha256": family_markers_sha256()},
        "boundary": {"cohort_id": cohort.get("cohort_id"), "frozen_at_utc": cohort.get("frozen_at_utc"),
                     "close_utc": close, "cohorts_read": len(cohorts)},
        "timeframes": timeframes,
        "s1": {"timeframe": S1_TIMEFRAME, "line": s1_line, "condition": s1_condition(s1_line)},
    }


# --- text ---------------------------------------------------------------------------------------

def _signed(value: float | None, digits: int = 3) -> str:
    return "-" if value is None else f"{value:+.{digits}f}"


def _print_null_control(evidence: Mapping[str, Any]) -> None:
    print("null-control (display only; direction only — no interval: one post-mint market per cell). "
          "Selected specs' net R per trade minus the median of 12 coin-flip entries through the same exits.")
    # The count first: a cell whose last fire is older than LOOKBACK_DAYS is not read, and a shrinking
    # denominator must be visible rather than look like a quieter store.
    print(f"  newest fire per cell ({len(evidence['cells'])} cells): "
          + ", ".join(f"{k} {v[:16]}" for k, v in evidence["cells"].items()))
    for title, key in (("specs", "by_timeframe_specs"), ("lineages", "by_timeframe_lineages")):
        print(f"  by timeframe, {title}:")
        for g in evidence[key]:
            print(f"    {g['timeframe']:<4} {g['count']:>4}  mean {_signed(g['mean_edge'])}  "
                  f"median {_signed(g['median_edge'])}  ahead {g['ahead']}/{g['count']}")
    print("  by timeframe × economic family, lineages:")
    for g in evidence["by_family_lineages"]:
        print(f"    {g['timeframe']:<4} {g['economic_family']:<24} {g['count']:>4}  mean {_signed(g['mean_edge'])}  "
              f"ahead {g['ahead']}/{g['count']}")


def _print_families(evidence: Mapping[str, Any], *, q: float, min_days: int) -> None:
    print(f"families (display only; a dry run of stage 1, not a verdict). Member minus its own twin, "
          f"pooled per timeframe × economic family, 95% interval clustered by settlement day; BH q={q} "
          f"over families with ≥{min_days} days ('·' = shown, not tested).")
    for timeframe, block in evidence.items():
        whole = block["all"] or {}
        cal = block["calibration"]
        print(f"  {timeframe}: all {whole.get('pairs', 0)} pairs {_signed(whole.get('mean_diff_r'))} "
              f"[{_signed(whole.get('ci_low_r'))}, {_signed(whole.get('ci_high_r'))}]; "
              f"calibration: any family passes in {cal['any_pass_rate']:.3f} of {cal['draws']} swaps")
        for g in block["families"]:
            mark = "·" if g["bh_pass"] is None else ("PASS" if g["bh_pass"] else "-")
            print(f"    {g['family']:<24} {g['pairs']:>3} pairs {g['days']:>3} days  {_signed(g['mean_diff_r'])} "
                  f"[{_signed(g['ci_low_r'])}, {_signed(g['ci_high_r'])}]  ahead {g['member_ahead']}/{g['pairs']}  "
                  f"p {'-' if g['p'] is None else format(g['p'], '.4f')}  {mark}")


def _print_verdict(evidence: Mapping[str, Any]) -> None:
    rule, boundary, s1 = evidence["rule"], evidence["boundary"], evidence["s1"]
    print(f"verdict {rule['version']} at {boundary['cohort_id']}'s close {boundary['close_utc']} "
          f"(frozen {boundary['frozen_at_utc']}; {boundary['cohorts_read']} cohort(s) read, "
          f"trades settled by the close). "
          f"Stage 1: BH q={rule['q']} per timeframe over families with ≥{rule['min_days']} days; stage 2: Holm "
          f"α={rule['holm_alpha']} over a passing family's lineages; "
          f"family markers {rule['family_markers_sha256'][:12]}.")
    for timeframe, block in evidence["timeframes"].items():
        whole, cal = block["all"] or {}, block["calibration"]
        state = "WITHHELD (calibration over q)" if block["stage1_withheld"] else \
            f"{sum(1 for g in block['families'] if g['verdict'] == PASS)} family(ies) pass"
        print(f"  {timeframe}: all {whole.get('pairs', 0)} pairs {_signed(whole.get('mean_diff_r'))} "
              f"[{_signed(whole.get('ci_low_r'))}, {_signed(whole.get('ci_high_r'))}]; calibration "
              f"{cal['any_pass_rate']:.3f} of {cal['draws']}; stage 1 {state}")
        for g in block["families"]:
            print(f"    {g['family']:<24} {g['pairs']:>3} pairs {g['days']:>3} days  {_signed(g['mean_diff_r'])} "
                  f"[{_signed(g['ci_low_r'])}, {_signed(g['ci_high_r'])}]  "
                  f"p {'-' if g['p'] is None else format(g['p'], '.4f')}  {g['verdict']}")
            for row in block["lineages"].get(g["family"], []):
                family, scope, tf = row["lineage"]
                print(f"      {family} {','.join(scope or ())} {tf}".ljust(46) + f" {row['pairs']:>2} pairs "
                      f"{row['days']:>3} days  {_signed(row['mean_diff_r'])}  "
                      f"p {'-' if row['p'] is None else format(row['p'], '.4f')}  {row['verdict']}")
    line = s1["line"] or {}
    print(f"  S1 (H1): {s1['timeframe']} all {line.get('pairs', 0)} pairs (need {rule['s1_min_pairs']}), "
          f"95% upper {_signed(line.get('ci_high_r'))} (need ≥ 0) -> {s1['condition']}")


def main(argv: list[str] | None = None, *, root: Path | None = None, now: str | None = None) -> int:
    parser = argparse.ArgumentParser(prog="selection_evidence", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    nc = sub.add_parser("null-control", help="the store's selected specs against coin flips, post-mint")
    nc.add_argument("--json", action="store_true")
    fam = sub.add_parser("families", help="the cohort's pairs per timeframe × economic family, BH dry run")
    fam.add_argument("--q", type=float, default=DEFAULT_Q)
    fam.add_argument("--min-days", type=int, default=MIN_FAMILY_DAYS)
    fam.add_argument("--draws", type=int, default=DEFAULT_DRAWS)
    fam.add_argument("--json", action="store_true")
    ver = sub.add_parser("verdict", help="the hierarchical judgement at a cohort's close (refuses before it)")
    ver.add_argument("--cohort", help="the cohort whose close to read (default: the latest closed)")
    ver.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    base = root if root is not None else ROOT
    try:
        if args.command == "null-control":
            evidence = null_control_evidence(base, now=now or timeutil.utc_now_iso())
            printer = _print_null_control
        elif args.command == "verdict":
            evidence = verdict(base, now=now or timeutil.utc_now_iso(), cohort_id=args.cohort)
            printer = _print_verdict
        else:
            evidence = family_evidence(base, q=args.q, min_days=args.min_days, draws=args.draws)
            printer = lambda e: _print_families(e, q=args.q, min_days=args.min_days)  # noqa: E731
    except MvpRuntimeError as exc:
        print(f"BLOCKED {exc.reason_code}: {exc.reason}", file=sys.stderr)
        return EXIT_BLOCKED
    if args.json:
        print(json.dumps(evidence, ensure_ascii=False, indent=1, default=list))
    else:
        printer(evidence)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
