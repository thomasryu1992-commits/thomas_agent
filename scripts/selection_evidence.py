#!/usr/bin/env python3
"""Does the factory's selection beat a coin flip on the data already collected? Read-only.

    docker exec -u 10001 thomas-scheduler python -m scripts.selection_evidence null-control
    docker exec -u 10001 thomas-scheduler python -m scripts.selection_evidence families

Writes nothing, asks no venue, decides nothing (2026-10-07, ``SELECTION_EVIDENCE_V0.1.md``). Two
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

The economic family is a display grouping of template names, the one
``RESEARCH_FORWARD_THROUGHPUT_ANALYSIS_V0.1.md`` §4 used. Using it to JUDGE is a decision the proposal
asks for; until then it only groups rows.
"""

from __future__ import annotations

import argparse
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
from runtime.mvp_runtime.crypto.candidate_identity import candidate_id  # noqa: E402
from runtime.mvp_runtime.crypto.pool_state import read_candidates  # noqa: E402
from runtime.mvp_runtime.crypto.promotion_backlog import _lineage_key  # noqa: E402
from runtime.mvp_runtime.errors import MvpRuntimeError  # noqa: E402
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


def calibration(groups: Mapping[str, Sequence[Any]], *, q: float, min_days: int, draws: int,
                seed: int) -> dict[str, Any]:
    """The same stage with member and twin swapped inside random pairs, ``draws`` times. Under no
    difference the swap changes nothing in distribution, so the share of draws where any family
    passes estimates the stage's false-pass rate on this data; it should sit at or under ``q``."""
    rng = random.Random(seed)
    any_pass = total = 0
    for _ in range(draws):
        flipped = {family: [(t, m) if rng.random() < 0.5 else (m, t) for m, t in pairs]
                   for family, pairs in groups.items()}
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


# --- text ---------------------------------------------------------------------------------------

def _signed(value: float | None, digits: int = 3) -> str:
    return "-" if value is None else f"{value:+.{digits}f}"


def _print_null_control(evidence: Mapping[str, Any]) -> None:
    print("null-control (display only; direction only — no interval: one post-mint market per cell). "
          "Selected specs' net R per trade minus the median of 12 coin-flip entries through the same exits.")
    print("  newest fire per cell: " + ", ".join(f"{k} {v[:16]}" for k, v in evidence["cells"].items()))
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


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(prog="selection_evidence", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    nc = sub.add_parser("null-control", help="the store's selected specs against coin flips, post-mint")
    nc.add_argument("--json", action="store_true")
    fam = sub.add_parser("families", help="the cohort's pairs per timeframe × economic family, BH dry run")
    fam.add_argument("--q", type=float, default=DEFAULT_Q)
    fam.add_argument("--min-days", type=int, default=MIN_FAMILY_DAYS)
    fam.add_argument("--draws", type=int, default=DEFAULT_DRAWS)
    fam.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    base = root if root is not None else ROOT
    try:
        if args.command == "null-control":
            evidence = null_control_evidence(base, now=timeutil.utc_now_iso())
            printer = _print_null_control
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
