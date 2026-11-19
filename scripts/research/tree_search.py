"""L27 nonlinear model (NONLINEAR_MODEL_SEARCH_V0.1): depth-2 boosting on eleven daily-bar ranks, walked forward.

Ranks 31-100 (``strategy_ladder.TierSweep``). At each weekly decision every member's eleven inputs are read as
percentile ranks among the members that have a value (0.5 when it has none), and its next-week return as the same
kind of rank. For each year Y of 2021-2024 a model is fitted on the rows whose target window ends on or before
Y-01-01 (the open of the year's first day) and scores the decisions inside Y. The score is the book's variable:
L22's rank weights, long the high score, weekly, held, stressed cost, read over the 48 months of 2021-2024.

Commands in the registered order: ``tree-time`` (one null pipeline timed; the inputs' coverage; nothing of the real
inputs is fitted), ``tree-overlap`` (the real model's weights beside every struck book on the tier and H-R2's: no
book's profit and loss is read; it can close L27 before any null is built), ``tree-null`` (the whole pipeline on
eleven fresh fixed random variables per seed), ``tree-plant`` (one of them replaced by a planted variable),
``tree-book`` (the real book, once, only after both overlaps passed).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from scripts.research import basket_search as bs  # noqa: E402
from scripts.research import cross_section_basket as xsb  # noqa: E402
from scripts.research import search_prune as sp  # noqa: E402
from scripts.research import signal_lab as lab  # noqa: E402
from scripts.research import strategy_ladder as sl  # noqa: E402

SPEC = "NONLINEAR_MODEL_SEARCH_V0.1"
SPAN = lab.SPAN                                   # rows from the span's first decision; nothing after 2024-12-31
YEARS = (2021, 2022, 2023, 2024)
TIER = sl.MID_TIER
INPUT_GROUPS = ("price_volume", "coin_character")  # the daily-bar groups (Thomas 2026-11-19 Q1: daily only)
TREES = 100
LEARNING_RATE = 0.05
MIN_LEAF = 200
BINS = 5                                           # percentile 0.2 apart: four cuts per input
HORIZON = lab.HORIZONS[0]                          # 7 days
NULLS = 200
NULL_SEED = "tree-null"
PLANT_RHOS = (0.03, 0.05, 0.08)
PLANT_SEEDS = 30
PLANT_SEED = "tree-plant"
PLANT_POWER = 0.5                                  # under this share at rho 0.08, a "none" is "the door cannot see"
ALPHA = 0.05
OVERLAP_CLOSE = sl.LOWER_OVERLAP_CLOSE             # 0.5
OVERLAP_MARKETS = ("bars", "positioning")
# The groups each overlap market loads: the bars with trade flow, and the positioning values. Depth has no files on
# this tier (D11), so its three struck books are named, not compared.
OVERLAP_GROUPS = {"bars": frozenset({"price_volume", "coin_character", "funding", "trade_flow"}),
                  "positioning": frozenset({"positioning"})}


def inputs() -> dict[str, lab.Variable]:
    """The registered daily-bar variables not struck on the tier and not left out of its sweep (section 3: eleven)."""
    return {name: read for name, (group, read) in sorted(lab.variables().items())
            if group in INPUT_GROUPS and name not in sl.TIER_SWEEP_OUT and lab.no_book(name, TIER) is None}


def struck() -> dict[str, str]:
    """Every registered variable the record keeps from a book on this tier, with its group."""
    registered = lab.variables()
    return {n: registered[n][0] for n in sorted(registered) if n in sl.TIER_SWEEP_OUT or lab.no_book(n, TIER)}


def null_inputs(k: int, space: str = NULL_SEED) -> dict[str, lab.Variable]:
    """Seed ``k``'s eleven random variables of this tier's null class: one fresh draw per coin, fixed over the span,
    with a value only where the coin has a bar that day (``search_prune.noise_variable``, as ``TierSweep.fixed``).
    A weekly-redrawn variable would give a score that turns over in full every week and pays for it, which none of
    the eleven real inputs does."""
    return {f"{space}_{k:03d}_{i:02d}": sp.noise_variable(TIER, k, i, "fixed", space) for i in range(len(inputs()))}


# --- the table -------------------------------------------------------------------------------------------------

def percentiles(values: Mapping[str, float]) -> dict[str, float]:
    """(average rank - 0.5) / n: ties share a rank, the lowest is near 0 and the highest near 1."""
    order = sorted(values, key=lambda s: (values[s], s))
    out: dict[str, float] = {}
    i, n = 0, len(order)
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        rank = (i + j) / 2 + 1
        for s in order[i:j + 1]:
            out[s] = (rank - 0.5) / n
        i = j + 1
    return out


def binned(p: float) -> int:
    return min(int(p * BINS), BINS - 1)


class Table:
    """Per decision day: each member's input bins and, where it has one, its target rank."""

    def __init__(self, market: xsb.Market, tier: sl.TierSweep, reads: Mapping[str, lab.Variable],
                 forward: Callable[[date], Mapping[str, float]] | None = None):
        self.names = sorted(reads)
        self.days = [d for d in tier.days if SPAN[0] <= d <= SPAN[1]]
        self.rows: dict[date, list[tuple[str, tuple[int, ...], float | None]]] = {}
        forward = forward or tier.forward
        for day in self.days:
            members = [s for s in tier.members(day) if day in (market.bars.get(s) or {})]   # a member without a bar is not a row
            if not members:
                continue
            ranks = []
            for name in self.names:
                values = {s: v for s in members if (v := reads[name](market, s, day)) is not None}
                ranks.append(percentiles(values))
            target = percentiles(dict(forward(day)))
            self.rows[day] = [(s, tuple(binned(r.get(s, 0.5)) for r in ranks), target.get(s)) for s in sorted(members)]

    def training(self, year: int) -> tuple[list[tuple[int, ...]], list[float]]:
        """The rows whose target window (open after the decision to the open seven days later) ends by Y-01-01."""
        cut = date(year, 1, 1)
        xs, ys = [], []
        for day, rows in sorted(self.rows.items()):
            if day + timedelta(days=1 + HORIZON) > cut:
                continue
            for _s, x, y in rows:
                if y is not None:
                    xs.append(x)
                    ys.append(y)
        return xs, ys


# --- the model -------------------------------------------------------------------------------------------------

Node = tuple  # ("leaf", value) or ("split", feature, cut, left, right): left is bin < cut


def _best_split(xs: Sequence[tuple[int, ...]], r: Sequence[float], idx: Sequence[int]) -> tuple[int, int] | None:
    features = len(xs[idx[0]]) if idx else 0
    total, n = sum(r[i] for i in idx), len(idx)
    best, gain_best = None, 1e-15
    for j in range(features):
        s, c = [0.0] * BINS, [0] * BINS
        for i in idx:
            b = xs[i][j]
            s[b] += r[i]
            c[b] += 1
        sl_, cl = 0.0, 0
        for cut in range(1, BINS):
            sl_ += s[cut - 1]
            cl += c[cut - 1]
            cr = n - cl
            if cl < MIN_LEAF or cr < MIN_LEAF:
                continue
            gain = sl_ * sl_ / cl + (total - sl_) ** 2 / cr - total * total / n
            if gain > gain_best:
                best, gain_best = (j, cut), gain
    return best


def _leaf(r: Sequence[float], idx: Sequence[int]) -> Node:
    return ("leaf", sum(r[i] for i in idx) / len(idx) if idx else 0.0)


def _grow(xs, r, idx, depth: int) -> Node:
    split = _best_split(xs, r, idx) if depth > 0 else None
    if split is None:
        return _leaf(r, idx)
    j, cut = split
    left = [i for i in idx if xs[i][j] < cut]
    right = [i for i in idx if xs[i][j] >= cut]
    return ("split", j, cut, _grow(xs, r, left, depth - 1), _grow(xs, r, right, depth - 1))


def _value(node: Node, x: tuple[int, ...]) -> float:
    while node[0] == "split":
        node = node[3] if x[node[1]] < node[2] else node[4]
    return node[1]


def fit(xs: Sequence[tuple[int, ...]], ys: Sequence[float]) -> tuple[float, list[Node]]:
    """Squared-loss gradient boosting: depth 2, :data:`TREES` trees, :data:`LEARNING_RATE`, leaves of at least
    :data:`MIN_LEAF` rows, cuts only between the five bins. Deterministic: ties keep the first split found."""
    if not ys:
        return 0.5, []
    base = sum(ys) / len(ys)
    f = [base] * len(ys)
    trees: list[Node] = []
    idx = list(range(len(ys)))
    for _ in range(TREES):
        r = [y - p for y, p in zip(ys, f)]
        tree = _grow(xs, r, idx, 2)
        trees.append(tree)
        for i in idx:
            f[i] += LEARNING_RATE * _value(tree, xs[i])
    return base, trees


def predict(model: tuple[float, list[Node]], x: tuple[int, ...]) -> float:
    base, trees = model
    return base + LEARNING_RATE * sum(_value(t, x) for t in trees)


def walk_forward(table: Table) -> dict[date, dict[str, float]]:
    """Year Y's decisions scored by the model fitted on :meth:`Table.training` of Y."""
    scores: dict[date, dict[str, float]] = {}
    for year in YEARS:
        model = fit(*table.training(year))
        for day, rows in table.rows.items():
            if day.year == year:
                scores[day] = {s: predict(model, x) for s, x, _y in rows}
    return scores


def score_variable(scores: Mapping[date, Mapping[str, float]]) -> lab.Variable:
    def read(market: xsb.Market, symbol: str, day: date) -> float | None:
        return scores.get(day, {}).get(symbol)
    return read


# --- the book and its reading ----------------------------------------------------------------------------------

def months() -> list[str]:
    return bs._complete_months(date(YEARS[0], 1, 1), SPAN[1])


def reading(rows: Mapping[str, Mapping[str, float]]) -> dict[str, Any]:
    span = months()
    report = sl.tier_reading(rows, span)
    report["years_total_position_pct"] = {
        str(y): round(sum(rows.get(m, {}).get("net", 0.0) for m in span if m.startswith(str(y))) * 100
                      / bs.POSITION_TOTAL, 2) for y in YEARS}
    report["later_years"] = sl.later_years(report["years_total_position_pct"])
    return report


def pipeline(market: xsb.Market, tier: sl.TierSweep, reads: Mapping[str, lab.Variable]) -> dict[str, Any]:
    scores = walk_forward(Table(market, tier, reads))
    return reading(tier.monthly_rows(score_variable(scores), 1))


KEEP = ("annual_sharpe", "total_position_annual_pct", "years_total_position_pct", "clears_entry", "months")


def null_books(market: xsb.Market, tier: sl.TierSweep, seeds: range) -> dict[str, Any]:
    out = {}
    for k in seeds:
        report = pipeline(market, tier, null_inputs(k))
        out[str(k)] = {"inputs": sorted(null_inputs(k)), **{key: report[key] for key in KEEP}}
    return out


def null_p(sharpe: float | None, nulls: Sequence[float | None]) -> float | None:
    """(1 + nulls at or above) / (nulls + 1); a null without a Sharpe is never at or above and stays counted."""
    if sharpe is None:
        return None
    return (1 + sum(1 for v in nulls if v is not None and v >= sharpe)) / (len(nulls) + 1)


def plant_books(market: xsb.Market, tier: sl.TierSweep, rho: float, seeds: range,
                nulls: Sequence[float | None]) -> dict[str, Any]:
    """Seed k: :data:`NULL_SEED`'s inputs of a seed in the plant's own space, the last one replaced by a planted
    variable (``search_prune.planted_variable``: rho times next week's normal score plus that control's own draw)."""
    view = sp.View(market, TIER, SPAN)
    out = {}
    for k in seeds:
        reads = null_inputs(k, PLANT_SEED)
        last = sorted(reads)[-1]
        reads[last] = sp.planted_variable(view, reads[last], rho)
        report = pipeline(market, tier, reads)
        p = null_p(report["annual_sharpe"], nulls)
        out[str(k)] = {"planted": last, **{key: report[key] for key in KEEP},
                       "p": round(p, 4) if p is not None else None, "p_passes": p is not None and p <= ALPHA}
    return out


def overlap(market: xsb.Market, tier: sl.TierSweep, which: str) -> dict[str, Any]:
    """Section 4.4.1: the real model's book weights beside every struck book whose group this market loads, and
    H-R2's (bars market). Fitting reads the training years' targets; no book's profit and loss is read."""
    registered = lab.variables()
    loaded = OVERLAP_GROUPS[which]
    others = {n: tier.weigher(registered[n][1], 1) for n, g in struck().items() if g in loaded}
    if which == "bars":
        others["H-R2"] = tier.weigher(registered[sl.MID_VARIABLE][1], -1)
    scores = walk_forward(Table(market, tier, inputs()))
    read = score_variable(scores)
    rows = sl._beside(market, tier, tier.weigher(read, 1), others)
    closed = sorted(n for n, r in rows.items() if r["closes"])
    unloaded = sorted(n for n, g in struck().items() if not any(g in v for v in OVERLAP_GROUPS.values()))
    return {"market": which, "threshold": OVERLAP_CLOSE, "turnover": tier.turnover(read), "compared": sorted(others), "beside": rows,
            "not_loaded_anywhere": unloaded, "closed_by": closed,
            "passes": bool(rows) and all(r["decisions"] > 0 for r in rows.values()) and not closed}


def judge(report: Mapping[str, Any], nulls: Sequence[float | None]) -> dict[str, Any]:
    p = null_p(report["annual_sharpe"], nulls)
    a, b, c = bool(report["clears_entry"]), bool(report["later_years"]["passes"]), p is not None and p <= ALPHA
    return {"entry": a, "later_years": b, "null": {"books": len(nulls), "p": round(p, 4) if p is not None else None,
                                                    "alpha": ALPHA, "passes": c}, "candidate": a and b and c}


def coverage(market: xsb.Market, tier: sl.TierSweep) -> dict[str, Any]:
    """Section 4.1's counts: per year, decisions, member rows, rows with a target, and each input's share with a value."""
    reads = inputs()
    out: dict[str, Any] = {}
    for year in range(SPAN[0].year, SPAN[1].year + 1):
        days = [d for d in tier.days if d.year == year and SPAN[0] <= d]
        rows = with_target = 0
        have = dict.fromkeys(reads, 0)
        for day in days:
            members = tier.members(day)
            rows += len(members)
            with_target += len(tier.forward(day))
            for name, read in reads.items():
                have[name] += sum(1 for s in members if read(market, s, day) is not None)
        out[str(year)] = {"decisions": len(days), "rows": rows, "rows_with_target": with_target,
                          "input_share": {n: round(v / rows, 3) if rows else None for n, v in have.items()}}
    return out


# --- the command -----------------------------------------------------------------------------------------------

def _seeds(text: str | None, top: int) -> range | None:
    try:
        lo, hi = (int(v) for v in (text or "").split(":"))
    except ValueError:
        return None
    return range(lo, hi) if 0 <= lo < hi <= top else None


def read_books(files: str) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    for path in files.split(","):
        for k, row in json.loads(Path(path).read_text(encoding="utf-8"))["books"].items():
            if k in rows:
                raise ValueError(f"seed {k} appears twice")
            rows[k] = row
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tree_search", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("tree-time", "tree-null", "tree-plant", "tree-overlap", "tree-book"))
    parser.add_argument("--seeds", default=None, help="tree-null: A:B of 0..200; tree-plant: A:B of 0..30")
    parser.add_argument("--rho", type=float, choices=PLANT_RHOS, default=None, help="tree-plant")
    parser.add_argument("--nulls", type=int, default=None, help="tree-plant, tree-book: how many null books were built")
    parser.add_argument("--null", default=None, help="tree-plant, tree-book: the null chunk files, comma-separated")
    parser.add_argument("--market", choices=OVERLAP_MARKETS, default=None, help="tree-overlap")
    parser.add_argument("--overlap", default=None, help="tree-book: both overlap files, comma-separated")
    parser.add_argument("--cache", type=Path, default=None, help="default: $RESEARCH_CACHE")
    args = parser.parse_args(argv)
    if not xsb._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    seeds = None
    if args.command in ("tree-null", "tree-plant"):
        seeds = _seeds(args.seeds, NULLS if args.command == "tree-null" else PLANT_SEEDS)
        if seeds is None:
            print(f"refused: {args.command} needs --seeds A:B inside its range", file=sys.stderr)
            return EXIT_USAGE
    if args.command == "tree-plant" and args.rho is None:
        print("refused: tree-plant needs --rho", file=sys.stderr)
        return EXIT_USAGE
    if args.command == "tree-overlap" and args.market is None:
        print("refused: tree-overlap needs --market bars|positioning (both are run)", file=sys.stderr)
        return EXIT_USAGE
    nulls: list[float | None] = []
    if args.command in ("tree-plant", "tree-book"):
        if args.null is None or args.nulls is None or not 0 < args.nulls <= NULLS:
            print(f"refused: {args.command} needs --null and --nulls (the count recorded in the document)", file=sys.stderr)
            return EXIT_USAGE
        rows = read_books(args.null)
        if sorted(rows, key=int) != [str(k) for k in range(args.nulls)]:
            print(f"refused: the null files do not hold seeds 0..{args.nulls - 1} exactly", file=sys.stderr)
            return EXIT_USAGE
        nulls = [rows[str(k)]["annual_sharpe"] for k in range(args.nulls)]
    if args.command == "tree-book":
        if args.overlap is None:
            print("refused: tree-book needs --overlap (section 4.4.1 first)", file=sys.stderr)
            return EXIT_USAGE
        found = [json.loads(Path(p).read_text(encoding="utf-8")) for p in args.overlap.split(",")]
        if sorted(f.get("market") for f in found) != sorted(OVERLAP_MARKETS):
            print("refused: tree-book needs the bars and the positioning overlap files", file=sys.stderr)
            return EXIT_USAGE
        if not all(f.get("passes") is True for f in found):
            print("refused: the overlap closed L27: no book is measured", file=sys.stderr)
            return EXIT_BLOCKED
    cache = args.cache or Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache")))
    if args.command == "tree-overlap" and args.market == "positioning":
        market = lab.hold_positioning(cache)
    else:
        market = lab.hold(cache, attach=False, flow=args.command == "tree-overlap")
    if market is None:
        print("BLOCKED: contract types or the attached values are missing from the cache", file=sys.stderr)
        return EXIT_BLOCKED
    tier = sl.TierSweep(market, SPAN)
    head = {"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC,
            "span": [d.isoformat() for d in SPAN], "last_day_read": lab.last_day(market).isoformat(),
            "command": args.command, "inputs": sorted(inputs())}
    if args.command == "tree-time":
        began = time.monotonic()
        table = Table(market, tier, null_inputs(0))
        built = time.monotonic()
        sizes = {str(y): len(table.training(y)[1]) for y in YEARS}
        scores = walk_forward(table)
        fitted = time.monotonic()
        read = score_variable(scores)
        tier.monthly_rows(read, 1)                       # timed only: its months are not kept or printed
        booked = time.monotonic()
        body: dict[str, Any] = {"reads_book_returns": False, "training_rows": sizes,
                                "seconds": {"table": round(built - began, 1), "four_fits": round(fitted - built, 1),
                                            "book": round(booked - fitted, 1)},
                                "null_seed_0_turnover": tier.turnover(read), "coverage": coverage(market, tier)}
    elif args.command == "tree-null":
        body = {"reads_book_returns": True, "seeds": [seeds.start, seeds.stop], "books": null_books(market, tier, seeds)}
    elif args.command == "tree-plant":
        rows = plant_books(market, tier, args.rho, seeds, nulls)
        n = len(rows)
        body = {"reads_book_returns": True, "rho": args.rho, "seeds": [seeds.start, seeds.stop], "books": rows,
                "reading": {"seeds": n, "p_passes_share": round(sum(r["p_passes"] for r in rows.values()) / n, 3),
                            "clears_entry_share": round(sum(r["clears_entry"] for r in rows.values()) / n, 3)}}
    elif args.command == "tree-overlap":
        body = {"reads_book_returns": False, **overlap(market, tier, args.market)}
    else:
        report = pipeline(market, tier, inputs())
        body = {"reads_book_returns": True, "book": report, "judgement": judge(report, nulls)}
    print(json.dumps({**head, **body}, indent=1, sort_keys=True, ensure_ascii=False, default=str))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
