"""L31 equal-weight composite (STRATEGY_DISCOVERY_LOOP section 119; Thomas 2026-11-20 "기본값대로").

Ranks 31-100. Every variable the record does not strike on the tier and whose recorded sweep book chose a side
(the latest family, ``hourly69/family-mid.json``: 41 variables, :data:`COMPONENTS`) is read as a percentile rank
among the decision's members that have a value, turned to its recorded side (1 - rank when the side is low), and
averaged with the others at equal weight; a member with no component value is not ranked, a missing component
counts as the middle (0.5). Nothing is fitted and nothing is chosen. The book is the tier sweep's (L22 rank weights,
weekly, held, the stressed cost), long the high score.

The sides were chosen on 2020-2024, so the book is judged only on the confirm span (2025-01 .. 2026-09) as the
fourth confirm read (``strategy_ladder.confirm_bar(4)``), not before 2026-12-13 (the monthly interval), once.

Commands in the registered order:
- ``composite-ranks --load L --span search|confirm``: the load's component (and, on the search span, struck)
  variables' percentile ranks per decision, written as data; no return.
- ``composite-counts --ranks FILES``: each component's share of member rows with a value on the confirm span;
  under :data:`MIN_COVERAGE` a component is left out before any read.
- ``composite-overlap --ranks FILES --counts F``: the search-span composite book's weights beside every struck book
  on the tier and H-R2's; a median of size 0.5 or more closes L31. No return.
- ``composite-read --ranks FILES --counts F --overlap F``: the one read.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from scripts.research import basket_search as bs  # noqa: E402
from scripts.research import cross_section_basket as xsb  # noqa: E402
from scripts.research import signal_lab as lab  # noqa: E402
from scripts.research import strategy_ladder as sl  # noqa: E402

SPEC = "STRATEGY_DISCOVERY_LOOP_V0.1 section 119 (L31)"
TIER = sl.MID_TIER
SEARCH = lab.SPAN                                   # 2020-01-01 .. 2024-12-31: the overlap only
CONFIRM = sl.CONFIRM                                # 2025-01-01 .. 2026-09-30: the read
WHOLE = (SEARCH[0], CONFIRM[1])
READ_NUMBER = 4
READ_NOT_BEFORE = date(2026, 12, 13)                # one month after the third read (section 119.5 Q4)
MIN_COVERAGE = 0.8
OVERLAP_CLOSE = sl.LOWER_OVERLAP_CLOSE               # 0.5, on the median's size
SOURCE = "~/research/discovery/hourly69/family-mid.json"   # the latest 31-100 sweep family (45 books)

# The recorded side of every unstruck variable with a recorded 31-100 sweep book, copied from SOURCE at registration.
COMPONENTS: dict[str, str] = {
    "divergence_30": "low", "funding_change_30": "high", "funding_position_30": "high", "illiquidity_30": "high",
    "listing_age": "high", "ls_change_30": "high", "ls_position_30": "high", "momentum_change_30": "low",
    "momentum_position_30": "low", "premium_14": "low", "premium_30": "low", "premium_7": "low", "premium_90": "low",
    "premium_change_30": "high", "premium_position_30": "high", "price_delay_30": "low",
    "residual_momentum_30": "low", "spot_share_30": "high", "spot_share_change_30": "high",
    "spot_share_position_30": "high", "spot_taker_share_14": "high", "spot_taker_share_30": "high",
    "spot_taker_share_7": "high", "spot_taker_share_90": "high", "spot_taker_share_change_30": "high",
    "spot_taker_share_position_30": "high", "spot_trade_size_30": "low", "spread_14": "high", "spread_30": "low",
    "spread_7": "low", "spread_90": "high", "spread_position_30": "low", "taker_buy_share_change_30": "low",
    "taker_buy_share_position_30": "low", "taker_change_30": "low", "taker_position_30": "low",
    "top_level_30": "low", "trade_size_30": "low", "trade_size_growth_30": "low", "volume_growth_30": "low",
    "volume_peak_share_30": "low",
}
# The record's loads (``strategy_ladder.sweep_market``): the bar groups together, the others alone.
LOADS = {"bars": frozenset(sl.SWEEP_BARS_GROUPS), **{g: frozenset({g}) for g in sl.SWEEP_ALONE}}


def struck() -> dict[str, str]:
    """Every registered variable the record strikes on the tier, with its group."""
    registered = lab.variables()
    return {n: registered[n][0] for n in sorted(registered) if n in sl.TIER_SWEEP_OUT or lab.no_book(n, TIER)}


def load_of(group: str) -> str | None:
    return next((name for name, groups in LOADS.items() if group in groups), None)


def market_for(cache: Path, load: str, span: tuple[date, date]) -> xsb.Market | None:
    """The record's loader for the load, cut at the span's end (the confirm span reads up to 2026-09-30, no later)."""
    if load == "bars":
        return lab.hold(cache, span, attach=False, flow=True)
    return getattr(lab, sl.SWEEP_ALONE[load])(cache, span)


# --- the ranks -----------------------------------------------------------------------------------------------------

def percentiles(values: Mapping[str, float]) -> dict[str, float]:
    """(mean rank - 0.5) / n: ties share a rank; the lowest is near 0, the highest near 1."""
    order = sorted(values, key=lambda s: (values[s], s))
    out: dict[str, float] = {}
    i, n = 0, len(order)
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = ((i + j) / 2 + 1 - 0.5) / n
        i = j + 1
    return out


def ranks(market: xsb.Market, tier: sl.TierSweep, names: Sequence[str]) -> dict[str, Any]:
    """Per variable: its percentile among the members with a value at each decision, and its share of member rows
    with a value. No return is read."""
    registered = lab.variables()
    out: dict[str, Any] = {"variables": {}, "coverage": {}, "member_rows": 0}
    rows = 0
    have = dict.fromkeys(names, 0)
    tables: dict[str, dict[str, dict[str, float]]] = {n: {} for n in names}
    for day in tier.days:
        members = [s for s in tier.members(day) if day in (market.bars.get(s) or {})]
        rows += len(members)
        for name in names:
            read = registered[name][1]
            values = {s: v for s in members if (v := read(market, s, day)) is not None}
            have[name] += len(values)
            if values:
                tables[name][day.isoformat()] = {s: round(p, 6) for s, p in percentiles(values).items()}
    out["variables"] = tables
    out["coverage"] = {n: round(have[n] / rows, 4) if rows else None for n in names}
    out["member_rows"] = rows
    return out


def merged(files: Sequence[Mapping[str, Any]], span: str) -> dict[str, dict[str, dict[str, float]]]:
    tables: dict[str, dict[str, dict[str, float]]] = {}
    for body in files:
        if body.get("span") != span:
            raise ValueError(f"a ranks file of span {body.get('span')} given where {span} is read")
        for name, table in body["ranks"]["variables"].items():
            if name in tables:
                raise ValueError(f"{name} appears in two ranks files")
            tables[name] = table
    return tables


def signed(pct: float, side: str) -> float:
    return pct if side == "high" else 1.0 - pct


def composite(tables: Mapping[str, Mapping[str, Mapping[str, float]]], components: Sequence[str]) -> lab.Variable:
    """The equal-weight score: the mean over ``components`` of the signed rank, a missing one at 0.5; None for a
    member with no component value at all."""
    def read(market: xsb.Market, symbol: str, day: date) -> float | None:
        key, total, seen = day.isoformat(), 0.0, 0
        for name in components:
            pct = tables.get(name, {}).get(key, {}).get(symbol)
            if pct is None:
                total += 0.5
            else:
                total += signed(pct, COMPONENTS[name])
                seen += 1
        return total / len(components) if seen else None
    return read


def table_read(tables: Mapping[str, Mapping[str, Mapping[str, float]]], name: str) -> lab.Variable:
    """A struck variable's recorded ranks as a variable: the same order as its values, so the same book."""
    def read(market: xsb.Market, symbol: str, day: date) -> float | None:
        return tables.get(name, {}).get(day.isoformat(), {}).get(symbol)
    return read


# --- the gates and the read ----------------------------------------------------------------------------------------

def counts(files: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Section 119.4.1: each component's confirm-span coverage; the kept components (coverage alone decides)."""
    coverage: dict[str, float | None] = {}
    for body in files:
        if body.get("span") != "confirm":
            raise ValueError("counts read the confirm span's ranks files only")
        coverage.update({n: c for n, c in body["ranks"]["coverage"].items() if n in COMPONENTS})
    missing = sorted(set(COMPONENTS) - set(coverage))
    kept = sorted(n for n, c in coverage.items() if c is not None and c >= MIN_COVERAGE)
    return {"coverage": dict(sorted(coverage.items())), "missing_files": missing, "min_coverage": MIN_COVERAGE,
            "kept": kept, "left_out": sorted(set(coverage) - set(kept)),
            "stop": {"stops": bool(missing) or not kept}}


def overlap(market: xsb.Market, tables, kept: Sequence[str]) -> dict[str, Any]:
    tier = sl.TierSweep(market, SEARCH)
    mine = tier.weigher(composite(tables, kept), 1)
    have = {n: g for n, g in struck().items() if n in tables}
    others = {n: tier.weigher(table_read(tables, n), 1) for n in sorted(have)}
    if sl.MID_VARIABLE in tables:
        others["H-R2"] = tier.weigher(table_read(tables, sl.MID_VARIABLE), -1)
    rows = sl._beside(market, tier, mine, others)
    closed = sorted(n for n, r in rows.items() if r["closes"])
    empty = sorted(n for n, r in rows.items() if r["decisions"] == 0)
    return {"threshold": OVERLAP_CLOSE, "compared": sorted(others), "beside": rows, "closed_by": closed,
            "not_compared": sorted(set(struck()) - set(have)), "no_decisions": empty,
            "turnover": tier.turnover(composite(tables, kept)),
            "blocked": not rows or bool(empty), "passes": bool(rows) and not empty and not closed}


def read_once(market: xsb.Market, tables, kept: Sequence[str]) -> dict[str, Any]:
    tier = sl.TierSweep(market, CONFIRM)
    rows = tier.monthly_rows(composite(tables, kept), 1)
    report = sl.tier_reading(rows, bs._complete_months(*CONFIRM))
    bar = sl.confirm_bar(READ_NUMBER)
    sharpe = report.get("annual_sharpe")
    return {"read": READ_NUMBER, "of": sl.CONFIRM_BUDGET, "candidate": "L31", "bar_annual_sharpe": bar,
            "verdict": "CONFIRM_PASS" if sharpe is not None and sharpe >= bar else "CONFIRM_FAIL",
            "components": len(kept), **report}


# --- the command ---------------------------------------------------------------------------------------------------

def _load_json(paths: str | None) -> list[dict[str, Any]]:
    return [json.loads(Path(p).read_text(encoding="utf-8")) for p in (paths or "").split(",") if p]


def main(argv: list[str] | None = None, *, today: date | None = None) -> int:
    parser = argparse.ArgumentParser(prog="composite", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("composite-ranks", "composite-counts", "composite-overlap", "composite-read"))
    parser.add_argument("--load", choices=sorted(LOADS), default=None)
    parser.add_argument("--span", choices=("search", "confirm"), default=None)
    parser.add_argument("--ranks", default=None, help="ranks files, comma-separated")
    parser.add_argument("--counts", default=None)
    parser.add_argument("--overlap", default=None)
    parser.add_argument("--cache", type=Path, default=None, help="default: $RESEARCH_CACHE")
    args = parser.parse_args(argv)
    if not xsb._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    cmd = args.command
    if cmd == "composite-ranks" and (args.load is None or args.span is None):
        print("refused: composite-ranks needs --load and --span", file=sys.stderr)
        return EXIT_USAGE
    files = _load_json(args.ranks)
    cnt = (_load_json(args.counts) or [None])[0]
    if cmd in ("composite-overlap", "composite-read") and not (cnt and not cnt["counts"]["stop"]["stops"]):
        print("refused: needs --counts that did not stop", file=sys.stderr)
        return EXIT_USAGE
    if cmd == "composite-read":
        over = (_load_json(args.overlap) or [None])[0]
        if not over or over.get("blocked"):
            print("BLOCKED: needs an overlap file that compared every book (a block is reported, not a verdict)",
                  file=sys.stderr)
            return EXIT_BLOCKED
        if not over.get("passes"):
            print("refused: the overlap closed L31: no read", file=sys.stderr)
            return EXIT_BLOCKED
        if (today or date.today()) < READ_NOT_BEFORE:
            print(f"refused: the fourth confirm read is not before {READ_NOT_BEFORE} (section 119.5 Q4)", file=sys.stderr)
            return EXIT_USAGE
        entry = sl.next_confirm_read()
        if READ_NUMBER in sl.CONFIRM_DONE or not entry or entry.get("read") != READ_NUMBER or entry.get("candidate") != "L31":
            print("refused: read 4 is not registered for L31 in strategy_ladder.CONFIRM_READS, or is spent", file=sys.stderr)
            return EXIT_USAGE
    head = {"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC, "command": cmd}
    if cmd == "composite-counts":
        try:
            body: dict[str, Any] = {"reads_book_returns": False, "counts": counts(files)}
        except ValueError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return EXIT_USAGE
        print(json.dumps({**head, **body}, indent=1, sort_keys=True, ensure_ascii=False))
        return EXIT_OK
    cache = args.cache or Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache")))
    span = SEARCH if (cmd == "composite-overlap" or args.span == "search") else WHOLE
    market = market_for(cache, args.load or "bars", span)
    if market is None:
        print("BLOCKED: contract types or the attached values are missing from the cache", file=sys.stderr)
        return EXIT_BLOCKED
    if cmd == "composite-ranks":
        groups = LOADS[args.load]
        registered = lab.variables()
        names = [n for n in COMPONENTS if registered[n][0] in groups]
        if args.span == "search":
            names += [n for n, g in struck().items() if g in groups and n not in names]
        tier = sl.TierSweep(market, SEARCH if args.span == "search" else CONFIRM)
        body = {"reads_book_returns": False, "load": args.load, "span": args.span,
                "last_day_read": lab.last_day(market).isoformat(), "ranks": ranks(market, tier, sorted(names))}
    elif cmd == "composite-overlap":
        try:
            tables = merged(files, "search")
        except ValueError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return EXIT_USAGE
        body = {"reads_book_returns": False, **overlap(market, tables, cnt["counts"]["kept"])}
    else:
        try:
            tables = merged(files, "confirm")
        except ValueError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return EXIT_USAGE
        body = {"reads_book_returns": True, "last_day_read": lab.last_day(market).isoformat(),
                **read_once(market, tables, cnt["counts"]["kept"])}
    print(json.dumps({**head, **body}, indent=1, sort_keys=True, ensure_ascii=False, default=str))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
