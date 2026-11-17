"""L24, the combination chosen on earlier years, and the search that is read beside itself run on noise.

``docs/proposals/SEARCH_THEN_PRUNE_V0.1.md`` (Thomas 2026-11-17, S1 - S5 "기본값대로"). Each tier's registered
variables, less what the record forbids there, are scored by their weekly rank correlation on the years before each
traded year (2022, 2023, 2024, the ladder's ``TRANSFER_SPLITS``). A configuration picks components and signs from
those years only, and the coins are ranked by the components' centred ranks added with those signs. The book is the
tier's own sweep book, always long the high score, read by that tier's own rule.

- **The search is read beside itself.** The same search, every configuration, end to end, is run on fresh random
  variables (as many as the tier has usable material, new ones every repeat) :data:`REPEATS` times. They are of the
  class the tier's own sweep null uses, so they trade as slowly as that null does: on the top tier half redrawn
  weekly and half fixed per coin (``strategy_ladder.null_variables``), below it fixed per coin
  (``TierSweep.fixed``). The real search's best is read against the bests of those runs. Choosing components and
  signs is paid for there, so no count of tries is needed.
- **Values first, apart.** ``values`` computes one group's values on one tier (the sandbox's memory cap does not hold
  every group at once) and prints them; nothing earned is read there. ``noise`` and ``real`` read those files with
  the bars and funding only; ``noise`` takes from them only the members and how many variables are usable.
- **Development years only.** Everything is on :data:`signal_lab.SPAN`.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import sys
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
from scripts.research import signal_lab as lab  # noqa: E402
from scripts.research import strategy_ladder as sl  # noqa: E402

SPEC = "SEARCH_THEN_PRUNE_V0.1"
LOGIC = "L24"
SPAN = lab.SPAN
TOP, MID, LOWER = "top30", sl.MID_TIER, sl.LOWER_NAME
TIERS = (TOP, MID, LOWER)
# (rule, size): the t's top k by size, every |t| of at least two, or every eligible weighted by its mean correlation.
CONFIGS: tuple[tuple[str, int | None], ...] = (("top", 3), ("top", 5), ("top", 10), ("top", 20), ("t2", None),
                                               ("weighted", None))
T_FLOOR = 2.0
REPEATS = 200                              # noise searches per tier
NOISE_ALPHA = 0.05
COMPONENT_SHARE = 0.5                      # of a configuration's components a coin needs values of to be scored
TRAINED_YEARS = tuple(after[0].year for _, after in sl.TRANSFER_SPLITS)       # 2022, 2023, 2024
# S5 (Thomas 2026-11-17, "기본값대로"): the real search runs once, after the noise searches of its tier are recorded.
REAL_SEARCH_DECIDED = True
NOISE_KINDS = {TOP: ("week", "fixed"), MID: ("fixed",), LOWER: ("fixed",)}


def config_name(config: tuple[str, int | None]) -> str:
    rule, size = config
    return f"{rule}{size}" if size is not None else rule


# --- material: what each tier may combine ----------------------------------------------------------------------------

def struck(name: str, tier: str) -> str | None:
    """Why ``name`` is not material on ``tier`` (S3): every book the record forbids there, the tier sweep's own list on
    ranks 31 - 100, and the depth group, which has no loader of its own."""
    registered = lab.variables()
    if registered[name][0] == "depth":
        return "the depth group has no loader of its own (strategy_ladder.sweep_market)"
    if tier == MID and name in sl.TIER_SWEEP_OUT:
        return sl.TIER_SWEEP_OUT[name]
    return lab.no_book(name, tier)


def material(tier: str) -> dict[str, str]:
    """``{name: group}``: the registered variables a tier's combination may use."""
    return {name: group for name, (group, _) in lab.variables().items() if not struck(name, tier)}


def material_groups(tier: str) -> list[str]:
    return sorted(set(material(tier).values()))


# --- the tier as a book reads it -------------------------------------------------------------------------------------

class View:
    """A tier's members per day, its forward weeks, and its own sweep book always long the high value."""

    def __init__(self, market: xsb.Market, tier: str, span: tuple[date, date] = SPAN) -> None:
        if tier not in TIERS:
            raise ValueError(f"unknown tier {tier!r}")
        self.market, self.tier, self.span = market, tier, span
        self.ic_days = lab.decisions(span, lab.HORIZONS[0])
        self.book_days = sl.sundays(span)
        if tier == TOP:
            self.monthly = sl.monthly_members(market, span)
            self.sweep: sl.TierSweep | None = None
        else:
            self.monthly = {}
            self.sweep = sl.TierSweep(market, span) if tier == MID else sl.lower_sweep_tier(market, span)
        self._forward: dict[date, dict[str, float]] = {}

    def members(self, day: date) -> tuple[str, ...]:
        if self.sweep is not None:
            return tuple(self.sweep.members(day))
        return tuple(self.monthly.get(date(day.year, day.month, 1), ()))

    def days(self) -> list[date]:
        return sorted(set(self.ic_days) | set(self.book_days))

    def forward(self, day: date) -> dict[str, float]:
        if day not in self._forward:
            self._forward[day] = {s: v for s in self.members(day)
                                  if (v := lab.forward_return(self.market, s, day, lab.HORIZONS[0])) is not None}
        return self._forward[day]

    def weigher(self, read: lab.Variable) -> Callable[..., dict[str, float]]:
        if self.sweep is not None:
            return self.sweep.weigher(read, 1)
        inner = sl.BOOKS[sl.SWEEP_LOGIC](read, lambda _day: 1)
        return lambda market, members, day, signal, seed=None: inner(market, self.members(day), day, signal, seed=seed)

    def turnover(self, read: lab.Variable) -> float | None:
        """Mean over consecutive decisions with weights of half the summed change in weights (the tier sweep's)."""
        weigher, moves, before = self.weigher(read), [], None
        for day in self.book_days:
            now = weigher(self.market, (), day, bs.CADENCE)
            if before and now:
                moves.append(sum(abs(now.get(s, 0.0) - before.get(s, 0.0)) for s in set(now) | set(before)) / 2)
            before = now
        return round(statistics.fmean(moves), 4) if moves else None

    def report(self, read: lab.Variable) -> dict[str, Any]:
        """The book long the high side of ``read``, read the way the tier's sweep reads a book: the top tier over
        the whole span (a month without a book counts as flat, section 44.1), the others over their own span."""
        if self.sweep is None:
            weigher = sl.BOOKS[sl.SWEEP_LOGIC](read, lambda _day: 1)
            sim = sl.ACCOUNTING[sl.SWEEP_ACCOUNTING](self.market, bs.CADENCE, self.monthly, start=self.span[0],
                                                      end=self.span[1], weigher=weigher)
            sharpe, monthly = bs.annual_sharpe(sim["daily"], *self.span)
            out: dict[str, Any] = {"annual_sharpe": sharpe, "sum_pct": round(sum(monthly) * 100, 2), "months": len(monthly)}
            out["total_position_annual_pct"] = bs.total_position_annual_pct(out["sum_pct"], out["months"])
            out["clears_entry"] = bs.clears_entry(out)
            years = sorted({d.year for d in sim["daily"]})
            out["years_total_position_pct"] = {
                str(y): round(sum(r["net"] for d, r in sim["daily"].items() if d.year == y) * 100 / bs.POSITION_TOTAL, 2)
                for y in years}
            out["monthly"] = [round(v, 8) for v in monthly]
            return out
        start = self.sweep.start(read)
        months = self.sweep.months(start)
        if len(months) < sl.TIER_SWEEP_MIN_MONTHS:
            return {"annual_sharpe": None, "skipped": f"its own span is {len(months)} complete months"}
        rows = self.sweep.monthly_rows(read, 1)
        out = dict(sl.tier_reading(rows, months))
        out["years_total_position_pct"] = {
            y: round(sum(rows.get(m, {}).get("net", 0.0) for m in months if m[:4] == y) * 100 / bs.POSITION_TOTAL, 2)
            for y in sorted({m[:4] for m in months})}
        out["monthly"] = [round(rows.get(m, {}).get("net", 0.0), 8) for m in months]
        out["start"] = None if start is None else start.isoformat()
        return out


# --- values (no return is read) ----------------------------------------------------------------------------------

def values(view: View, names: Mapping[str, lab.Variable]) -> dict[str, Any]:
    """Every name's value for every member on every day a book or a correlation reads."""
    days = view.days()
    members = {d.isoformat(): list(view.members(d)) for d in days}
    table: dict[str, dict[str, dict[str, float]]] = {}
    coverage: dict[str, dict[str, int]] = {}
    for name, read in names.items():
        per: dict[str, dict[str, float]] = {}
        for day in days:
            row = {s: v for s in view.members(day) if (v := read(view.market, s, day)) is not None}
            if row:
                per[day.isoformat()] = row
        table[name] = per
        first = min(per, default=None)
        coverage[name] = {"cells": sum(len(r) for r in per.values()),
                          "with_bar": 0 if first is None else sum(
                              1 for d in days if d.isoformat() >= first for s in view.members(d)
                              if d in (view.market.bars.get(s) or {}))}
    return {"members": members, "values": table, "coverage": coverage}


def usable(coverage: Mapping[str, Mapping[str, int]], tier: str) -> list[str]:
    """The tier's material with values; on ranks 101 and after, also at the tier sweep's coverage floor (its own
    span's member-days with a bar)."""
    out = []
    for name in material(tier):
        cov = coverage.get(name)
        if cov is None or not cov["cells"]:
            continue
        if tier == LOWER and (not cov["with_bar"] or cov["cells"] / cov["with_bar"] < sl.LOWER_COVERAGE):
            continue
        out.append(name)
    return sorted(out)


def centred_ranks(row: Mapping[str, float]) -> dict[str, float]:
    """Ranks spread over -1 .. +1 (ties share their mean rank); a single value sits at 0."""
    ordered = sorted(row.items(), key=lambda kv: kv[1])
    n = len(ordered)
    if n == 0:
        return {}
    if n == 1:
        return {ordered[0][0]: 0.0}
    out: dict[str, float] = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and ordered[j + 1][1] == ordered[i][1]:
            j += 1
        mean_rank = (i + j) / 2
        for k in range(i, j + 1):
            out[ordered[k][0]] = 2 * mean_rank / (n - 1) - 1
        i = j + 1
    return out


def rank_table(table: Mapping[str, Mapping[str, Mapping[str, float]]]) -> dict[str, dict[date, dict[str, float]]]:
    """Values to centred ranks per name and day. A rank correlation reads the same either way."""
    return {name: {date.fromisoformat(d): centred_ranks(row) for d, row in per.items()} for name, per in table.items()}


# --- the search ----------------------------------------------------------------------------------------------------

def correlations(view: View, ranks: Mapping[date, Mapping[str, float]]) -> dict[date, float]:
    out = {}
    for day in view.ic_days:
        row = ranks.get(day, {})
        pairs = [(row[s], f) for s, f in view.forward(day).items() if s in row]
        if len(pairs) >= lab.MIN_NAMES and (ic := lab.rank_correlation(pairs)) is not None:
            out[day] = ic
    return out


def trained(ics: Mapping[date, float], year: int) -> dict[str, float] | None:
    """The correlations a ``year`` may use: decisions whose week ends before it (the ladder's rule for a learned
    sign). Their mean and t, or None under :data:`signal_lab.MIN_DECISIONS` or with no spread."""
    end = date(year - 1, 12, 31)
    kept = [ic for day, ic in ics.items() if day + timedelta(days=1 + lab.HORIZONS[0]) <= end]
    if len(kept) < lab.MIN_DECISIONS:
        return None
    spread = statistics.stdev(kept)
    if spread == 0:
        return None
    mean = statistics.fmean(kept)
    return {"mean": mean, "t": mean / (spread / math.sqrt(len(kept))), "n": len(kept)}


def choose(stats: Mapping[str, Mapping[str, float] | None], config: tuple[str, int | None]) -> dict[str, float]:
    """``{name: weight}`` for one year: the sign of each chosen name's mean, or the mean itself (``weighted``)."""
    eligible = {n: s for n, s in stats.items() if s is not None}
    rule, size = config
    if rule == "weighted":
        return {n: s["mean"] for n, s in sorted(eligible.items())}
    ordered = sorted(eligible, key=lambda n: (-abs(eligible[n]["t"]), n))
    if rule == "top":
        picked = ordered[:size]
    elif rule == "t2":
        picked = [n for n in ordered if abs(eligible[n]["t"]) >= T_FLOOR]
    else:
        raise ValueError(f"unknown rule {rule!r}")
    return {n: (1.0 if eligible[n]["mean"] > 0 else -1.0) for n in picked}


def scores(ranks: Mapping[str, Mapping[date, Mapping[str, float]]], chosen: Mapping[int, Mapping[str, float]],
           days: Sequence[date]) -> dict[date, dict[str, float]]:
    """The combined score per day of a traded year, from that year's components; nothing on other days."""
    out: dict[date, dict[str, float]] = {}
    for day in days:
        weights = chosen.get(day.year)
        if not weights:
            continue
        need = math.ceil(len(weights) * COMPONENT_SHARE)
        coins = set().union(*(ranks[n].get(day, {}).keys() for n in weights))
        row = {}
        for s in coins:
            have = [(w, ranks[n][day][s]) for n, w in weights.items() if s in ranks[n].get(day, {})]
            if len(have) >= need and have:
                row[s] = sum(w * r for w, r in have) / sum(abs(w) for w, _ in have)
        if row:
            out[day] = row
    return out


def search(view: View, ranks: Mapping[str, Mapping[date, Mapping[str, float]]], names: Sequence[str],
           *, ics: Mapping[str, Mapping[date, float]] | None = None, keep_detail: bool = True) -> dict[str, Any]:
    """Every configuration on ``names``: the components each traded year picked and the book's reading."""
    ics = ics or {n: correlations(view, ranks[n]) for n in names}
    stats = {y: {n: trained(ics[n], y) for n in names} for y in TRAINED_YEARS}
    days = view.days()
    out: dict[str, Any] = {}
    for config in CONFIGS:
        chosen = {y: choose(stats[y], config) for y in TRAINED_YEARS}
        table = scores(ranks, chosen, days)
        read = (lambda _m, s, d, table=table: table.get(d, {}).get(s))
        report = view.report(read)
        traded = [d for d in view.book_days if d.year in chosen and chosen[d.year]]
        row: dict[str, Any] = {"annual_sharpe": report.get("annual_sharpe"), "turnover": view.turnover(read),
                               "book_days": len(traded), "scored_book_days": sum(1 for d in traded if table.get(d))}
        if keep_detail:
            row.update({"report": report, "chosen": {str(y): {n: round(w, 6) for n, w in c.items()} for y, c in chosen.items()}})
            if report.get("years_total_position_pct") is not None:
                row["later_years"] = sl.later_years(report["years_total_position_pct"])
        out[config_name(config)] = row
    return out


def best(rows: Mapping[str, Mapping[str, Any]]) -> tuple[str | None, float | None]:
    """The configuration with the highest Sharpe (ties go to the earlier configuration)."""
    order = [config_name(c) for c in CONFIGS]
    found = [(rows[c]["annual_sharpe"], -order.index(c), c) for c in rows if rows[c].get("annual_sharpe") is not None]
    if not found:
        return None, None
    sharpe, _, name = max(found)
    return name, sharpe


def noise_variable(tier: str, repeat: int, index: int, kind: str, space: str = "noise") -> lab.Variable:
    """A random variable of its own seed: redrawn every day (``week``) or one draw per coin (``fixed``). Below the
    top tier it has a value only where the coin has a bar that day, as ``TierSweep.fixed``. ``space`` keeps the
    planted door's pools (section 71) apart from the recorded noise."""
    seed = f"{LOGIC}|{space}|{tier}|{repeat}|{index}|{kind}"
    needs_bar = tier != TOP

    def value(market: xsb.Market, symbol: str, day: date) -> float | None:
        if needs_bar and day not in (market.bars.get(symbol) or {}):
            return None
        stamp = day if kind == "week" else date(2000, 1, 1)
        return int(xsb.random_key(seed, stamp, symbol)[:12], 16) / 16 ** 12
    return value


def noise_kind(tier: str, count: int, index: int) -> str:
    """On the top tier the first half redrawn weekly and the rest fixed (``strategy_ladder.null_variables``'s split),
    below it all fixed."""
    half = count // 2 if len(NOISE_KINDS[tier]) == 2 else 0
    return "week" if index < half else "fixed"


def noise_names(tier: str, repeat: int, count: int) -> dict[str, lab.Variable]:
    """``count`` fresh random variables for one repeat, of the tier's null class (:func:`noise_kind`)."""
    return {f"noise_{repeat:03d}_{i:02d}": noise_variable(tier, repeat, i, noise_kind(tier, count, i))
            for i in range(count)}


def tabulate(view: View, names: Mapping[str, lab.Variable]) -> dict[str, dict[date, dict[str, float]]]:
    """Ranks of ``names`` straight from their functions, on the days and members the tables hold."""
    days = view.days()
    return {n: {d: centred_ranks({s: v for s in view.members(d) if (v := read(view.market, s, d)) is not None})
                for d in days} for n, read in names.items()}


def noise(view: View, tier: str, count: int, repeats: range = range(REPEATS)) -> dict[str, Any]:
    """The search on ``count`` fresh random variables per repeat."""
    if repeats.start < 0 or repeats.stop > REPEATS:
        raise ValueError(f"repeats run 0 .. {REPEATS - 1}")
    rows = []
    for r in repeats:
        ranks = tabulate(view, noise_names(tier, r, count))
        found = search(view, ranks, sorted(ranks), keep_detail=False)
        rows.append({"repeat": r, "configs": {c: v["annual_sharpe"] for c, v in found.items()},
                     "turnover": {c: v["turnover"] for c, v in found.items()},
                     "scored_book_days": {c: [v["scored_book_days"], v["book_days"]] for c, v in found.items()}})
    return {"tier": tier, "drawn_per_repeat": count, "repeats": [repeats.start, repeats.stop], "rows": rows}


def merge_noise(chunks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """One tier's noise chunks as one run: same tier, size and material, every repeat exactly once."""
    if not chunks:
        raise ValueError("no noise chunks")
    first = chunks[0]
    for c in chunks:
        if (c["tier"], c["drawn_per_repeat"], c.get("usable")) != (first["tier"], first["drawn_per_repeat"], first.get("usable")):
            raise ValueError("the noise chunks differ in tier, size or material")
    rows = sorted((r for c in chunks for r in c["rows"]), key=lambda r: r["repeat"])
    if [r["repeat"] for r in rows] != list(range(REPEATS)):
        raise ValueError(f"the noise chunks must hold repeats 0 .. {REPEATS - 1} once each")
    return {"tier": first["tier"], "drawn_per_repeat": first["drawn_per_repeat"], "usable": first.get("usable"),
            "repeats": REPEATS, "rows": rows}


# --- the reading -------------------------------------------------------------------------------------------------

def across_tiers(rows_by_tier: Sequence[Mapping[str, float | None]]) -> float | None:
    values = [v for rows in rows_by_tier for v in rows.values() if v is not None]
    return max(values) if values else None


def null_bests(noise_runs: Sequence[Mapping[str, Any]], tiers: Sequence[str]) -> list[float]:
    """Each noise repeat's best over every tier and configuration, sorted (repeats with none left out)."""
    repeats = {n["repeats"] for n in noise_runs}
    if len(repeats) != 1:
        raise ValueError("the noise runs differ in repeats")
    by_tier = {n["tier"]: n for n in noise_runs}
    nulls = [across_tiers([by_tier[t]["rows"][r]["configs"] for t in tiers]) for r in range(next(iter(repeats)))]
    return sorted(v for v in nulls if v is not None)


def noise_p(value: float | None, kept: Sequence[float]) -> float | None:
    """(1 + noise bests at or above ``value``) / (1 + noise bests); a missing value counts no noise best above it."""
    if not kept:
        return None
    return (1 + sum(1 for v in kept if value is not None and v >= value)) / (1 + len(kept))


def deflated_sharpe(monthly: Sequence[float], trial_sharpes: Sequence[float], trials: int) -> float | None:
    """Bailey and Lopez de Prado's deflated Sharpe ratio of the best book: the chance its monthly Sharpe beats the
    expected best of ``trials`` with the trial Sharpes' spread, given its skew and kurtosis. Shown, never a gate."""
    t = len(monthly)
    kept = [s for s in trial_sharpes if s is not None]
    if t < 3 or len(kept) < 2 or trials < 2 or statistics.pstdev(monthly) == 0:
        return None
    mean, sd = statistics.fmean(monthly), statistics.pstdev(monthly)
    sr = mean / sd
    skew = statistics.fmean(((x - mean) / sd) ** 3 for x in monthly)
    kurt = statistics.fmean(((x - mean) / sd) ** 4 for x in monthly)
    var_trials = statistics.variance([s / math.sqrt(12) for s in kept])
    gamma, z = 0.5772156649, statistics.NormalDist()
    sr0 = math.sqrt(var_trials) * ((1 - gamma) * z.inv_cdf(1 - 1 / trials) + gamma * z.inv_cdf(1 - 1 / (trials * math.e)))
    denominator = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    if denominator <= 0:
        return None
    return round(z.cdf((sr - sr0) * math.sqrt(t - 1) / math.sqrt(denominator)), 4)


def overfit_probability(series: Mapping[str, Sequence[float]], blocks: int = 6) -> dict[str, Any] | None:
    """CSCV: the months cut into ``blocks`` equal blocks (the earliest leftover months dropped); for every half of
    the blocks, the configuration best on it, and where it ranks on the other half. PBO is the share of splits where
    it falls at or below the middle. Shown, never a gate."""
    names = sorted(series)
    length = min((len(v) for v in series.values()), default=0)
    size = length // blocks
    if len(names) < 2 or size < 1:
        return None
    cut = {n: list(series[n])[-size * blocks:] for n in names}
    parts = {n: [cut[n][b * size:(b + 1) * size] for b in range(blocks)] for n in names}

    def sharpe(values: Sequence[float]) -> float:
        sd = statistics.pstdev(values)
        return statistics.fmean(values) / sd if sd > 0 else 0.0

    import itertools
    below, splits = 0, 0
    for picked in itertools.combinations(range(blocks), blocks // 2):
        rest = [b for b in range(blocks) if b not in picked]
        inside = {n: sharpe([x for b in picked for x in parts[n][b]]) for n in names}
        outside = {n: sharpe([x for b in rest for x in parts[n][b]]) for n in names}
        winner = max(names, key=lambda n: (inside[n], n))
        rank = sorted(names, key=lambda n: (outside[n], n)).index(winner) + 1
        below += rank / (len(names) + 1) <= 0.5
        splits += 1
    return {"blocks": blocks, "months_per_block": size, "splits": splits, "pbo": round(below / splits, 4)}


def reading(noise_runs: Sequence[Mapping[str, Any]], real_runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The real search's best over every tier and configuration, beside the same best of each noise repeat."""
    tiers = sorted(r["tier"] for r in real_runs)
    if sorted(n["tier"] for n in noise_runs) != tiers or len(set(tiers)) != len(tiers):
        raise ValueError("one noise run and one real run per tier")
    by_tier = {n["tier"]: n for n in noise_runs}
    kept = null_bests(noise_runs, tiers)
    real = {r["tier"]: r["configs"] for r in real_runs}
    flat = {(t, c): v for t in tiers for c, v in real[t].items()}
    value = across_tiers([{c: v["annual_sharpe"] for c, v in real[t].items()} for t in tiers])
    winner = max((k for k, v in flat.items() if v["annual_sharpe"] is not None),
                 key=lambda k: (flat[k]["annual_sharpe"], k), default=None)
    p = noise_p(value, kept)
    out: dict[str, Any] = {
        "best": None if winner is None else {"tier": winner[0], "config": winner[1], **flat[winner]},
        "best_annual_sharpe": value, "null_bests": len(kept),
        "null": {"mean": round(statistics.fmean(kept), 3), "p95": kept[int(0.95 * (len(kept) - 1))], "max": kept[-1]} if kept else None,
        "p": None if p is None else round(p, 4), "alpha": NOISE_ALPHA, "passes_noise": p is not None and p < NOISE_ALPHA,
    }
    out["noise_turnover_median"] = {
        t: (round(statistics.median(vals), 4) if (vals := [v for row in by_tier[t]["rows"] for v in row.get("turnover", {}).values()
                                                         if v is not None]) else None) for t in tiers}
    out["real_turnover"] = {f"{t}:{c}": v.get("turnover") for (t, c), v in flat.items()}
    if winner is not None:
        report = flat[winner]["report"]
        out["clears_entry"] = report.get("clears_entry")
        out["later_years"] = flat[winner].get("later_years")
        trials = [v["annual_sharpe"] for v in flat.values()]
        out["shown_not_gates"] = {
            "deflated_sharpe": deflated_sharpe(report.get("monthly", []), trials, len(flat)),
            "pbo_by_tier": {t: overfit_probability({c: v["report"]["monthly"] for c, v in real[t].items()
                                                    if v.get("report", {}).get("monthly")}) for t in tiers}}
    return out


# --- the planted door (section 71): how large an effect the search must carry to pass its own noise ----------------
#
# No real variable is read. Each seed draws a fresh pool of the tier's noise (its own seed space, apart from the
# recorded noise) and replaces the pool's last ``count`` variables, all of the fixed kind, by planted ones: ``rho``
# times the normal score of the coin's next-week return among the tier's members (round 39's rank shape; 0 for a coin
# with none) plus the replaced variable's own fixed draw moved to a normal score. At size 0 a planted variable ranks
# exactly as the variable it replaced, so size 0 is that seed's noise search whatever the count. Real forward returns
# and real books on the development span are read; the values files give only the members and how many variables are
# usable. Every book is read from its functions (``tabulate``), so the table-path gate section 70.2 promised is not
# spent here.
PLANT_TIER = TOP
PLANT_SIZES = (0.0, 0.02, 0.03, 0.04, 0.05, 0.065, 0.08, 0.10)
PLANT_COUNTS = (1, 5)                      # one planted variable; five that share the signal with independent noise
PLANT_SEEDS = 50
PLANT_SHARE = 0.80                         # of the seeds: the bracket's upper size is the smallest that reaches it
PLANT_REPLAY = 3                           # recorded noise repeats replayed in the first chunk: they must match
PLANT_FALSE_ALARM = (0, 7)                 # at size 0, seeds with p < NOISE_ALPHA (binomial 50 at about 5%)
PLANT_SPACE = "plant"


def size_key(size: float) -> str:
    return f"{size:g}"


def pool_names(tier: str, seed: int, count: int) -> dict[str, lab.Variable]:
    """One seed's pool: ``count`` random variables of the tier's null class, in the planted door's own seed space."""
    return {f"pool_{seed:03d}_{i:02d}": noise_variable(tier, seed, i, noise_kind(tier, count, i), PLANT_SPACE)
            for i in range(count)}


def planted_variable(view: View, base: lab.Variable, rho: float) -> lab.Variable:
    """``rho`` times the next week's normal score plus ``base``'s own uniform draw moved to a normal score (monotone,
    so at ``rho`` 0 the ranks are ``base``'s). It reads the future by construction: never registered, never material."""
    normal, keep, edge = statistics.NormalDist(), math.sqrt(1.0 - rho * rho), 0.5 / 16 ** 12
    scores: dict[date, dict[str, float]] = {}

    def read(market: xsb.Market, symbol: str, day: date) -> float | None:
        u = base(market, symbol, day)
        if u is None:
            return None
        if day not in scores:
            forward = view.forward(day)
            scores[day] = sl._normal_scores(forward) if rho and forward else {}
        return rho * scores[day].get(symbol, 0.0) + keep * normal.inv_cdf(min(max(u, edge), 1.0 - edge))
    return read


def planted_indices(tier: str, pool: int, count: int) -> list[int]:
    out = list(range(pool - count, pool))
    if any(noise_kind(tier, pool, i) != "fixed" for i in out):
        raise ValueError("a planted variable replaces only a fixed one")
    return out


def _summary(found: Mapping[str, Mapping[str, Any]], planted: Sequence[str]) -> dict[str, Any]:
    name, sharpe = best(found)
    out: dict[str, Any] = {"configs": {c: v["annual_sharpe"] for c, v in found.items()}, "best": name, "best_sharpe": sharpe}
    if name is not None:
        row, report = found[name], found[name]["report"]
        out.update({"clears_entry": report.get("clears_entry"),
                    "total_position_annual_pct": report.get("total_position_annual_pct"),
                    "later_years": (row.get("later_years") or {}).get("passes"),
                    "turnover": row.get("turnover"),
                    "planted_chosen_years": sum(1 for c in row["chosen"].values() if set(c) & set(planted))})
    return out


def plant(view: View, tier: str, pool: int, seeds: range, *, sizes: Sequence[float] = PLANT_SIZES,
          counts: Sequence[int] = PLANT_COUNTS) -> dict[str, Any]:
    """Per seed: the pool's search (size 0), then for every count and size above 0 the search with planted variables,
    and one planted variable's own book (the units a single-variable book is read in)."""
    if seeds.start < 0 or seeds.stop > PLANT_SEEDS:
        raise ValueError(f"seeds run 0 .. {PLANT_SEEDS - 1}")
    rows = []
    for seed in seeds:
        base = pool_names(tier, seed, pool)
        names = sorted(base)
        ranks = tabulate(view, base)
        ics = {n: correlations(view, ranks[n]) for n in names}
        row: dict[str, Any] = {"seed": seed, "sizes": {size_key(0.0): _summary(search(view, ranks, names, ics=ics), [])}}
        single: dict[str, Any] = {}
        for size in sizes:
            if size == 0:
                continue
            made = {names[i]: planted_variable(view, base[names[i]], size) for i in planted_indices(tier, pool, max(counts))}
            made_ranks = tabulate(view, made)
            made_ics = {n: correlations(view, made_ranks[n]) for n in made}
            for count in counts:
                swapped = [names[i] for i in planted_indices(tier, pool, count)]
                found = search(view, {**ranks, **{n: made_ranks[n] for n in swapped}}, names,
                               ics={**ics, **{n: made_ics[n] for n in swapped}})
                out = _summary(found, swapped)
                out["planted_mean_ic"] = round(statistics.fmean(v for n in swapped for v in made_ics[n].values()), 4)
                row["sizes"].setdefault(size_key(size), {})[str(count)] = out
            last = names[planted_indices(tier, pool, 1)[0]]
            report = view.report(made[last])
            single[size_key(size)] = {"annual_sharpe": report.get("annual_sharpe"),
                                      "total_position_annual_pct": report.get("total_position_annual_pct"),
                                      "mean_ic": round(statistics.fmean(made_ics[last].values()), 4)}
        row["single"] = single
        rows.append(row)
    return {"tier": tier, "drawn_per_seed": pool, "seeds": [seeds.start, seeds.stop], "rows": rows}


def replay(view: View, tier: str, pool: int, recorded: Mapping[str, Any], repeats: int = PLANT_REPLAY) -> list[int]:
    """The first recorded noise repeats run again here; the repeats whose configurations differ (empty: they hold)."""
    again = noise(view, tier, pool, range(repeats))
    kept = {r["repeat"]: r["configs"] for r in recorded["rows"]}
    return [r["repeat"] for r in again["rows"] if kept.get(r["repeat"]) != r["configs"]]


def merge_plant(chunks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not chunks:
        raise ValueError("no plant chunks")
    first = chunks[0]
    for c in chunks:
        if (c["tier"], c["drawn_per_seed"], c.get("usable")) != (first["tier"], first["drawn_per_seed"], first.get("usable")):
            raise ValueError("the plant chunks differ in tier, size or material")
    rows = sorted((r for c in chunks for r in c["rows"]), key=lambda r: r["seed"])
    if [r["seed"] for r in rows] != list(range(PLANT_SEEDS)):
        raise ValueError(f"the plant chunks must hold seeds 0 .. {PLANT_SEEDS - 1} once each")
    replays = [c["replay_differs"] for c in chunks if c.get("replay_differs") is not None]
    return {"tier": first["tier"], "drawn_per_seed": first["drawn_per_seed"], "usable": first.get("usable"),
            "replay_differs": replays[0] if len(replays) == 1 else None, "rows": rows}


def _median(values: Sequence[float | None]) -> float | None:
    kept = [v for v in values if v is not None]
    return round(statistics.median(kept), 4) if kept else None


def plant_reading(merged: Mapping[str, Any], noise_runs: Sequence[Mapping[str, Any]],
                  *, counts: Sequence[int] = PLANT_COUNTS, sizes: Sequence[float] = PLANT_SIZES) -> dict[str, Any]:
    """Each seed's best read against the recorded noise bests over every tier with ``reading``'s p, per count and size;
    the share of seeds that pass, and the bracket around :data:`PLANT_SHARE`."""
    kept = null_bests(noise_runs, sorted(n["tier"] for n in noise_runs))
    rows = merged["rows"]
    zero = [r["sizes"][size_key(0.0)] for r in rows]
    alarms = sum(1 for z in zero if (p := noise_p(z["best_sharpe"], kept)) is not None and p < NOISE_ALPHA)
    out: dict[str, Any] = {
        "null_bests": len(kept), "seeds": len(rows),
        "doors": {"replay_differs": merged.get("replay_differs"),
                  "replay_holds": merged.get("replay_differs") == [],
                  "false_alarms_at_zero": alarms,
                  "false_alarms_within": PLANT_FALSE_ALARM[0] <= alarms <= PLANT_FALSE_ALARM[1]},
        "zero": {"mean_best": round(statistics.fmean(z["best_sharpe"] for z in zero if z["best_sharpe"] is not None), 4)},
        "by_count": {}}
    for count in counts:
        table = {}
        for size in sizes:
            if size == 0:
                continue
            cells = [r["sizes"][size_key(size)][str(count)] for r in rows]
            singles = [r["single"][size_key(size)] for r in rows]
            ps = [noise_p(c["best_sharpe"], kept) for c in cells]
            passed = [p is not None and p < NOISE_ALPHA for p in ps]
            table[size_key(size)] = {
                "passes": round(sum(passed) / len(rows), 4),
                "passes_and_entry": round(sum(1 for c, ok in zip(cells, passed) if ok and c.get("clears_entry")) / len(rows), 4),
                "passes_entry_later": round(sum(1 for c, ok in zip(cells, passed)
                                                if ok and c.get("clears_entry") and c.get("later_years")) / len(rows), 4),
                "planted_every_year": round(sum(1 for c in cells if c.get("planted_chosen_years") == len(TRAINED_YEARS)) / len(rows), 4),
                "median_best_sharpe": _median([c["best_sharpe"] for c in cells]),
                "median_best_annual_pct": _median([c.get("total_position_annual_pct") for c in cells]),
                "median_planted_ic": _median([c["planted_mean_ic"] for c in cells]),
                "median_turnover": _median([c.get("turnover") for c in cells]),
                "single_median_sharpe": _median([s["annual_sharpe"] for s in singles]),
                "single_median_annual_pct": _median([s["total_position_annual_pct"] for s in singles]),
            }
        below = [s for s in table if table[s]["passes"] < PLANT_SHARE]
        above = [s for s in table if table[s]["passes"] >= PLANT_SHARE]
        out["by_count"][str(count)] = {"sizes": table,
                                       "bracket": {"below": max(below, key=float) if below else None,
                                                   "reaches": min(above, key=float) if above else None}}
    return out


# --- loading -----------------------------------------------------------------------------------------------------

def group_market(cache: Path, tier: str, group: str) -> xsb.Market | None:
    """The market a group's values are read from on a tier, as that tier's sweep loads it."""
    if tier == LOWER:
        if group == "positioning":
            return sl.lower_positioning_market(cache)
        if group == "spot":
            return sl.lower_spot_market(cache)
        if group == "spot_flow":
            return sl.lower_spot_flow_market(cache)
    return sl.sweep_market(cache, {group})


def load_tier(files: Sequence[Path], tier: str, keep: Callable[[str], bool]) -> dict[str, Any]:
    """Every values file of one tier, one at a time: the members (the same in every file), every name's coverage, and
    the ranks of the names ``keep`` admits. Every material group must be there, once."""
    ranks: dict[str, dict[date, dict[str, float]]] = {}
    coverage: dict[str, dict[str, int]] = {}
    members: dict[str, list[str]] | None = None
    groups: list[str] = []
    for path in files:
        body = json.loads(Path(path).read_text(encoding="utf-8"))
        if body.get("tier") != tier:
            raise ValueError(f"{path} is tier {body.get('tier')!r}, not {tier!r}")
        if members is None:
            members = body["members"]
        elif body["members"] != members:
            raise ValueError(f"{path}: the tier's members differ from the first file's")
        clash = set(coverage) & set(body["coverage"])
        if clash:
            raise ValueError(f"{path}: names already loaded: {sorted(clash)}")
        groups.append(body["group"])
        coverage.update(body["coverage"])
        ranks.update(rank_table({n: v for n, v in body["values"].items() if keep(n)}))
        del body
    if sorted(groups) != material_groups(tier):
        raise ValueError(f"the files hold groups {sorted(groups)}, the tier needs {material_groups(tier)}")
    return {"ranks": ranks, "coverage": coverage, "members": members or {}}


def members_differ(view: View, members: Mapping[str, Sequence[str]]) -> list[str]:
    """Days where the search's own tier and the values files disagree: a day only one of them has, or other members."""
    days = {d.isoformat() for d in view.days()}
    out = sorted(days ^ set(members))
    out += sorted(d for d in set(members) & days if list(view.members(date.fromisoformat(d))) != list(members[d]))
    return out


def parse_repeats(text: str | None) -> range:
    if not text:
        return range(REPEATS)
    first, _, last = text.partition(":")
    return range(int(first), int(last))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("material", "values", "noise", "real", "reading", "plant", "plant-reading"))
    parser.add_argument("--tier", choices=TIERS, default=None)
    parser.add_argument("--only", default=None, help="values: one group of the tier's material")
    parser.add_argument("--values", default=None, help="noise, real: the tier's values files, comma-separated")
    parser.add_argument("--repeats", default=None, help="noise: a range of repeats FIRST:STOP (default all)")
    parser.add_argument("--noise", default=None,
                        help="real: this tier's noise chunks; reading: every tier's noise chunks (comma-separated)")
    parser.add_argument("--real", default=None, help="reading: the three real runs, comma-separated")
    parser.add_argument("--seeds", default=None, help="plant: a range of seeds FIRST:STOP")
    parser.add_argument("--plant", default=None, help="plant-reading: every plant chunk, comma-separated")
    parser.add_argument("--cache", type=Path, default=None, help="default: $RESEARCH_CACHE")
    args = parser.parse_args(argv)
    stamp = {"measured_at": timeutil.utc_now_iso(), "spec": SPEC, "logic": LOGIC}
    load = lambda text: [json.loads(Path(p).read_text(encoding="utf-8")) for p in text.split(",") if p]  # noqa: E731
    if args.command == "material":
        print(json.dumps({**stamp, "tiers": {t: {"material": material(t), "groups": material_groups(t),
                                                 "struck": {n: r for n in lab.variables() if (r := struck(n, t))}}
                                             for t in TIERS}}, indent=1, sort_keys=True))
        return EXIT_OK
    if args.command == "reading":
        if not args.noise or not args.real:
            print("refused: reading needs --noise and --real", file=sys.stderr)
            return EXIT_USAGE
        try:
            chunks = load(args.noise)
            merged = [merge_noise([c for c in chunks if c["tier"] == t]) for t in sorted({c["tier"] for c in chunks})]
            body = reading(merged, load(args.real))
        except ValueError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return EXIT_USAGE
        print(json.dumps({**stamp, "judgement": None, **body}, indent=1, sort_keys=True))
        return EXIT_OK
    if args.command == "plant-reading":
        if not args.noise or not args.plant:
            print("refused: plant-reading needs --noise (every tier's chunks) and --plant", file=sys.stderr)
            return EXIT_USAGE
        try:
            chunks = load(args.noise)
            merged_noise = [merge_noise([c for c in chunks if c["tier"] == t]) for t in sorted({c["tier"] for c in chunks})]
            if sorted(n["tier"] for n in merged_noise) != sorted(TIERS):
                raise ValueError("the noise of every tier is needed")
            body = plant_reading(merge_plant(load(args.plant)), merged_noise)
        except ValueError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return EXIT_USAGE
        print(json.dumps({**stamp, "door": "planted", **body}, indent=1, sort_keys=True))
        return EXIT_OK
    if args.command == "plant" and args.tier != PLANT_TIER:
        print(f"refused: the planted door is registered on {PLANT_TIER} only", file=sys.stderr)
        return EXIT_USAGE
    if args.tier is None:
        print("refused: --tier is needed", file=sys.stderr)
        return EXIT_USAGE
    cache = args.cache or Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache")))
    if args.command == "values":
        own = material(args.tier)
        if args.only not in set(own.values()):
            print(f"refused: --only is one of {material_groups(args.tier)}", file=sys.stderr)
            return EXIT_USAGE
        names = {n: lab.variables()[n][1] for n, g in own.items() if g == args.only}
        market = group_market(cache, args.tier, args.only)
        if market is None:
            print("BLOCKED: the group's market is missing from the cache", file=sys.stderr)
            return EXIT_BLOCKED
        body = values(View(market, args.tier), names)
        print(json.dumps({**stamp, **xsb.provenance(), "tier": args.tier, "group": args.only, "reads_returns": False,
                          **body}, sort_keys=True))
        return EXIT_OK
    if not args.values:
        print("refused: --values names the tier's values files (every material group)", file=sys.stderr)
        return EXIT_USAGE
    if args.command == "real" and not REAL_SEARCH_DECIDED:
        print("BLOCKED: the real search waits on S1 - S5", file=sys.stderr)
        return EXIT_BLOCKED
    recorded = None
    try:
        repeats = parse_repeats(args.repeats)
        seeds = parse_repeats(args.seeds) if args.command == "plant" else None
        if args.command == "plant" and not args.seeds:
            print("refused: plant needs --seeds FIRST:STOP", file=sys.stderr)
            return EXIT_USAGE
        if args.command in ("real", "plant"):
            if not args.noise:
                print(f"refused: {args.command} needs this tier's recorded noise chunks (--noise)", file=sys.stderr)
                return EXIT_USAGE
            recorded = merge_noise(load(args.noise))
        keep = (lambda n: False) if args.command in ("noise", "plant") else (lambda n: n in material(args.tier))
        loaded = load_tier([Path(p) for p in args.values.split(",")], args.tier, keep)
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return EXIT_USAGE
    names = usable(loaded["coverage"], args.tier)
    if recorded is not None and (recorded["tier"] != args.tier or recorded["usable"] != names):
        print("refused: the noise run is not this tier's, or not on the same material", file=sys.stderr)
        return EXIT_USAGE
    market = lab.hold(cache, attach=False)
    if market is None:
        print("BLOCKED: contract types are missing from the cache", file=sys.stderr)
        return EXIT_BLOCKED
    view = View(market, args.tier)
    differ = members_differ(view, loaded["members"])
    if differ:
        print(f"BLOCKED: the tier's days or members differ from the values files' on {len(differ)} days, first {differ[0]}",
              file=sys.stderr)
        return EXIT_BLOCKED
    common = {**stamp, **xsb.provenance(), "tier": args.tier, "material": len(material(args.tier)), "usable": names}
    if args.command == "noise":
        try:
            body = noise(view, args.tier, len(names), repeats)
        except ValueError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return EXIT_USAGE
        print(json.dumps({**common, **body}, sort_keys=True))
        return EXIT_OK
    if args.command == "plant":
        try:
            body = plant(view, args.tier, len(names), seeds)
        except ValueError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return EXIT_USAGE
        body["replay_differs"] = replay(view, args.tier, len(names), recorded) if seeds.start == 0 else None
        print(json.dumps({**common, "reads_real_variables": False, **body}, sort_keys=True))
        return EXIT_OK
    found = search(view, loaded["ranks"], names)
    print(json.dumps({**common, "configs": found}, indent=1, sort_keys=True))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
