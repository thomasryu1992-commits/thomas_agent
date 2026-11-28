"""L28 volatility-managed exposure: does sizing the always-long top-30 book by its own recent volatility add
anything over holding it at a constant size?

``docs/proposals/VOLATILITY_MANAGED_EXPOSURE_V0.1.md`` (Thomas 2026-11-20: "L28 등록"). Every other book on record
bets on *which* coins (a ranking, a pair, a sign); the trend book (``trend_book_search``, T01/T02) and the regime
timing test (T1) changed the *direction* of the market exposure. This book changes only its *size* over time.

- **The base book** is the trend book's always-long form, unchanged: each member of the month's top 30 gets its
  inverse-volatility share ``v`` (shares sum to one, :func:`trend_book_search.shares` with the 30-day lookback),
  weekly, through ``cross_section_basket.simulate``.
- **The scale** at decision ``t`` is ``k_t = min(CAP, target_t / σ_t)``: ``σ_t`` is the standard deviation of the
  base book's daily returns over the 30 days to ``t``'s close, held at ``t``'s shares (:func:`book_vol`);
  ``target_t`` is the median of ``σ`` over every earlier decision that has one (:func:`scales`). Under
  :data:`MIN_HISTORY` earlier values the scale is 1.
- **What is judged is the excess,** as the trend book's was: the managed book minus the base book, ``(k_t − 1)·v``,
  doubled so that its largest total is :data:`basket_search.POSITION_TOTAL` like every basket's
  (:func:`excess_weights`). Doubling moves no Sharpe; it puts the 15% line on the same footing.
- **Order (fixed before anything is read):** overlap with the trend book's exposure (no return) → counts (no
  return) → the door (the recorded T01 numbers again) → 600 null books whose scales are the real scales in
  shuffled 13-week blocks → planted books (the real scales rearranged to follow the next week's base return at
  a known rank correlation) → the real book once. Each stage refuses without the one before it; the real book
  also needs every planted book.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from scripts.research import basket_search as bs  # noqa: E402
from scripts.research import cross_section_basket as xsb  # noqa: E402
from scripts.research import momentum_basket_screen as mbs  # noqa: E402
from scripts.research import strategy_ladder as sl  # noqa: E402
from scripts.research import trend_book_search as tbs  # noqa: E402

SPEC = "VOLATILITY_MANAGED_EXPOSURE_V0.1"
SPAN = bs.DEV                               # 2020-01-01 .. 2024-12-31, 60 months: the trend book's span
HISTORY_START = date(2019, 1, 1)            # the target reads every earlier decision the archive can size
LOOKBACK = 30                               # the base book's shares and the trend book's T01: one window
CAP = 2.0
MIN_HISTORY = 13                            # earlier decisions the target needs; under it the scale is 1
OVERLAP_CLOSE = 0.5
MIN_MEAN_MOVE = 0.10                        # counts stop: the mean |k − 1| under it leaves nothing to judge
MAX_YEAR_DRIFT = 0.25                       # counts stop: a year whose signed mean k − 1 reaches it is a year-long bet
BLOCK_WEEKS = 13
NULLS = 600
ALPHA = 0.05
PLANT_RHOS = (0.05, 0.10, 0.20)
PLANT_SEEDS = 50
PLANT_SEES = 0.80                           # the share of planted seeds at p <= ALPHA that counts as "the door sees"
RECORDED = {"T01": {"annual_sharpe": 0.056, "sum_pct": 21.86},           # ~/research/trend-search/T01.json
            "always_long": {"annual_sharpe": 0.677, "sum_pct": 256.67}}


# --- the scale --------------------------------------------------------------------------------------------------

def book_vol(market: xsb.Market, share: Mapping[str, float], day: date) -> float | None:
    """The base book's daily return volatility over the 30 days to ``day``'s close, at ``day``'s shares. A day's
    return is the share-weighted mean over the members with both closes; under ``mbs.MIN_VOL_RETURNS`` days, None."""
    returns = []
    for k in range(mbs.VOL_LOOKBACK_DAYS):
        now_day, then_day = day - timedelta(days=k), day - timedelta(days=k + 1)
        num = den = 0.0
        for s, v in share.items():
            bars = market.bars.get(s) or {}
            now, then = bars.get(now_day), bars.get(then_day)
            if now is not None and then is not None:
                num += v * (now.close / then.close - 1)
                den += v
        if den > 0:
            returns.append(num / den)
    if len(returns) < mbs.MIN_VOL_RETURNS:
        return None
    sd = statistics.stdev(returns)
    return sd if sd > 0 else None


def decisions(start: date, end: date) -> list[date]:
    """The Sundays whose decision the engine holds over [start, end] (it decides on ``day − 1``)."""
    return [d for d in xsb.held_days(start - timedelta(days=1), end - timedelta(days=1))
            if xsb.is_rebalance_day(bs.CADENCE, d)]


def monthly(market: xsb.Market, start: date, end: date) -> dict[date, list[str]]:
    out = xsb.universes(market, top_n=bs.TOP_N, start=date(start.year, start.month, 1) - timedelta(days=1), end=end)
    return {date(m.year, m.month, 1): v for m, v in out.items()}


def scales(market: xsb.Market, members: Mapping[date, Sequence[str]], days: Sequence[date]) -> dict[date, dict]:
    """Per decision (in order): the shares, ``σ``, the target from the earlier decisions and the scale ``k``."""
    out: dict[date, dict] = {}
    seen: list[float] = []
    for day in sorted(days):
        share, _ = tbs.shares(market, members.get(date(day.year, day.month, 1), ()), day, LOOKBACK)
        sigma = book_vol(market, share, day) if share else None
        target = statistics.median(seen) if len(seen) >= MIN_HISTORY else None
        k = 1.0 if sigma is None or target is None else min(CAP, target / sigma)
        out[day] = {"share": share, "sigma": sigma, "target": target, "k": k}
        if sigma is not None:
            seen.append(sigma)
    return out


def excess_weights(share: Mapping[str, float], k: float) -> dict[str, float]:
    move = bs.POSITION_TOTAL * (k - 1.0)
    return {} if move == 0 else {s: move * v for s, v in sorted(share.items())}


def weigher(table: Mapping[date, dict], k_of: Mapping[date, float] | None = None):
    """A ``cross_section_basket.simulate`` weigher for the excess book; ``k_of`` replaces the scales (the null)."""
    def weights(market, members, day, signal, *, seed=None):
        row = table.get(day)
        if row is None or not row["share"]:
            return {}
        return excess_weights(row["share"], row["k"] if k_of is None else k_of[day])
    return weights


def shuffled(table: Mapping[date, dict], span_days: Sequence[date], seed: int) -> dict[date, float]:
    """The span's scales in :data:`BLOCK_WEEKS`-week blocks, the blocks in a seeded order; each decision keeps its
    own shares."""
    days = sorted(span_days)
    blocks = [days[i:i + BLOCK_WEEKS] for i in range(0, len(days), BLOCK_WEEKS)]
    order = list(range(len(blocks)))
    random.Random(f"{SPEC}|null|{seed}").shuffle(order)
    ks = [table[d]["k"] for b in order for d in blocks[b]]
    return dict(zip(days, ks))


def base_weeks(market: xsb.Market, members, span_days: Sequence[date]) -> dict[date, float]:
    """The base (always-long) book's net return over the week each decision holds: a recorded book's return,
    read only to plant (:func:`planted`)."""
    start, end = SPAN
    sim = xsb.simulate(market, bs.CADENCE, members, start=start, end=end, weigher=tbs.book(LOOKBACK, tbs.ALWAYS_LONG))
    days = sorted(span_days)
    out: dict[date, float] = {}
    for day, row in sim["daily"].items():
        earlier = [d for d in days if d < day]
        if earlier:
            out[earlier[-1]] = out.get(earlier[-1], 0.0) + row["net"]
    return out


def planted(table: Mapping[date, dict], span_days: Sequence[date], weeks: Mapping[date, float], rho: float,
            seed: int) -> dict[date, float]:
    """The span's own scales rearranged: each decision's place in the order of ``rho·(normal score of its week's
    base return) + √(1 − rho²)·noise``, the largest scale to the largest. The values are the real ones, so the
    real null's 600 books are this book's null too."""
    rng = random.Random(f"{SPEC}|plant|{rho}|{seed}")
    days = [d for d in sorted(span_days) if d in weeks]
    ranked = sorted(days, key=lambda d: (weeks[d], d))
    n = len(ranked)
    score = {d: statistics.NormalDist().inv_cdf((i + 0.5) / n) for i, d in enumerate(ranked)}
    z = {d: rho * score[d] + math.sqrt(1 - rho * rho) * rng.gauss(0, 1) for d in days}
    values = sorted(table[d]["k"] for d in days)
    out = {d: table[d]["k"] for d in span_days}
    out.update(dict(zip(sorted(days, key=lambda d: (z[d], d)), values)))
    return out


def plant_reading(rows: Mapping[str, Mapping[str, Any]], nulls: Sequence[float | None]) -> dict[str, Any]:
    """Per size: the share of planted seeds at p <= :data:`ALPHA` against the null, and the share that would be a
    candidate on rung 2(a) and (c). A size the door sees is one whose first share reaches :data:`PLANT_SEES`."""
    out = {}
    for rho in PLANT_RHOS:
        books = [r for r in rows.values() if r["rho"] == rho]
        ps = [null_p(r["annual_sharpe"], nulls) for r in books]
        seen = sum(1 for p in ps if p is not None and p <= ALPHA)
        both = sum(1 for p, r in zip(ps, books) if p is not None and p <= ALPHA and r["clears_entry"])
        out[str(rho)] = {"seeds": len(books), "p_at_alpha": seen, "and_entry": both,
                         "sees": bool(books) and seen / len(books) >= PLANT_SEES}
    return {"by_rho": out, "smallest_seen": min((float(k) for k, v in out.items() if v["sees"]), default=None)}


# --- the gates without a return -----------------------------------------------------------------------------

def _corr(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) < 8 or statistics.pstdev(xs) == 0 or statistics.pstdev(ys) == 0:
        return None
    return round(statistics.correlation(xs, ys), 4)


def overlap(market: xsb.Market, table: Mapping[date, dict], members, span_days: Sequence[date]) -> dict[str, Any]:
    """The weekly correlation of the excess book's net exposure ``2(k − 1)`` with each trend book's excess net
    exposure ``−2·Σ v`` over its members in a downtrend. Either at :data:`OVERLAP_CLOSE` or above closes L28.
    Only decisions whose scale is read (a target and a σ) count: a scale of 1 for want of either is a constant 0
    that would pull the correlation toward passing."""
    out: dict[str, Any] = {}
    for name, lookback in tbs.DEFINITIONS.items():
        xs, ys, unread = [], [], 0
        for day in span_days:
            share, rets = tbs.shares(market, members.get(date(day.year, day.month, 1), ()), day, lookback)
            if table[day].get("target") is None or table[day].get("sigma") is None:
                unread += 1
                continue
            if not share or not table[day]["share"]:
                continue
            xs.append(bs.POSITION_TOTAL * (table[day]["k"] - 1.0))
            ys.append(-bs.POSITION_TOTAL * sum(v for s, v in share.items() if rets[s] <= 0))
        out[name] = {"weeks": len(xs), "unread_scale_weeks": unread, "correlation": _corr(xs, ys)}
    worst = max((v["correlation"] for v in out.values() if v["correlation"] is not None), default=None)
    return {"trend": out, "close_at": OVERLAP_CLOSE, "passes": worst is not None and worst < OVERLAP_CLOSE}


def counts(table: Mapping[date, dict], span_days: Sequence[date]) -> dict[str, Any]:
    """How far the scale moves, how often it sits at the cap or at 1 for want of history, its signed mean per year
    (a drift in σ leaves the expanding median off 1 for whole years: the excess is then a levered or cut market
    for that year, not timing), and the paper cost of trading the excess book (engine fee + slippage on every
    weekly change, no return read)."""
    unit = (xsb.cost.DEFAULT_TAKER_FEE_BPS + xsb.cost.DEFAULT_SLIPPAGE_BPS) / 10_000   # the engine's own
    years: dict[str, dict[str, Any]] = {}
    held: dict[str, float] = {}
    paper = 0.0
    moves = []
    for day in span_days:
        row = table[day]
        y = years.setdefault(str((day + timedelta(days=1)).year), {"decisions": 0, "no_history": 0, "no_sigma": 0,
                                                                     "at_cap": 0, "below_one": 0, "signed": []})
        y["decisions"] += 1
        y["no_history"] += row["target"] is None
        y["no_sigma"] += row["sigma"] is None
        y["at_cap"] += row["k"] >= CAP
        y["below_one"] += row["k"] < 1.0
        y["signed"].append(row["k"] - 1.0)
        moves.append(abs(row["k"] - 1.0))
        target = excess_weights(row["share"], row["k"]) if row["share"] else {}
        paper += sum(abs(target.get(s, 0.0) - held.get(s, 0.0)) for s in set(held) | set(target)) * unit
        held = target
    n_years = len(bs._complete_months(*SPAN)) / 12
    mean_move = statistics.fmean(moves) if moves else 0.0
    paper_pct = paper * 100 / bs.POSITION_TOTAL / n_years
    for y in years.values():
        signed = y.pop("signed")
        y["mean_k_minus_1"] = round(statistics.fmean(signed), 4) if signed else None
    drift = max((abs(y["mean_k_minus_1"]) for y in years.values() if y["mean_k_minus_1"] is not None), default=0.0)
    stops = mean_move < MIN_MEAN_MOVE or paper_pct >= bs.ENTRY_RETURN_TOTAL_PCT or drift >= MAX_YEAR_DRIFT
    ks = sorted(table[d]["k"] for d in span_days)
    return {"years": years, "mean_abs_k_minus_1": round(mean_move, 4),
            "k_quartiles": [round(v, 4) for v in statistics.quantiles(ks, n=4)] if len(ks) > 1 else None,
            "paper_cost_total_position_annual_pct": round(paper_pct, 3),
            "largest_year_drift": round(drift, 4),
            "stop": {"mean_move_floor": MIN_MEAN_MOVE, "paper_cost_ceiling_pct": bs.ENTRY_RETURN_TOTAL_PCT,
                     "year_drift_ceiling": MAX_YEAR_DRIFT, "stops": stops}}


# --- the books --------------------------------------------------------------------------------------------------

def run(market: xsb.Market, members, weigh) -> dict[str, Any]:
    start, end = SPAN
    sim = xsb.simulate(market, bs.CADENCE, members, start=start, end=end, weigher=weigh)
    sharpe, values = bs.annual_sharpe(sim["daily"], start, end)
    report: dict[str, Any] = {"annual_sharpe": sharpe, "sum_pct": round(sum(values) * 100, 2), "months": len(values)}
    report["total_position_annual_pct"] = bs.total_position_annual_pct(report["sum_pct"], report["months"])
    report["clears_entry"] = bs.clears_entry(report)
    report["years_total_position_pct"] = {
        str(y): round(sum(r["net"] for d, r in sim["daily"].items() if d.year == y) * 100 / bs.POSITION_TOTAL, 2)
        for y in sorted({d.year for d in sim["daily"]})}
    report["cost_pct"] = round(sum(r["cost"] for r in sim["daily"].values()) * 100, 2)
    report["funding_pct"] = round(sum(r["funding"] for r in sim["daily"].values()) * 100, 2)
    report["later_years"] = sl.later_years(report["years_total_position_pct"])
    return report


def door(market: xsb.Market, members) -> dict[str, Any]:
    """The recorded trend book T01 and its always-long book, again, to the digit (a return of recorded books only)."""
    out = {}
    for name, form in (("T01", tbs.EXCESS), ("always_long", tbs.ALWAYS_LONG)):
        r = run(market, members, tbs.book(LOOKBACK, form))
        out[name] = {"annual_sharpe": r["annual_sharpe"], "sum_pct": r["sum_pct"],
                     "same": r["annual_sharpe"] == RECORDED[name]["annual_sharpe"]
                     and r["sum_pct"] == RECORDED[name]["sum_pct"]}
    return {"books": out, "passes": all(v["same"] for v in out.values())}


def null_p(value: float | None, nulls: Sequence[float | None]) -> float | None:
    if value is None:
        return None
    return (1 + sum(1 for v in nulls if v is not None and v >= value)) / (1 + len(nulls))


def judge(report: Mapping[str, Any], nulls: Sequence[float | None]) -> dict[str, Any]:
    p = null_p(report["annual_sharpe"], nulls)
    a, b = bool(report["clears_entry"]), bool(report["later_years"]["passes"])
    c = p is not None and p <= ALPHA
    return {"entry": a, "later_years": b, "null": {"books": len(nulls), "p": round(p, 4) if p is not None else None,
                                                    "alpha": ALPHA, "passes": c},
            "candidate": a and b and c}


# --- the command --------------------------------------------------------------------------------------------------

def _seeds(text: str | None) -> range | None:
    try:
        lo, hi = (int(v) for v in (text or "").split(":"))
    except ValueError:
        return None
    return range(lo, hi) if 0 <= lo < hi <= NULLS else None


def _passed(path: Path | None, key: str) -> bool:
    if path is None:
        return False
    body = json.loads(path.read_text(encoding="utf-8"))
    return {"overlap": lambda b: b["overlap"]["passes"], "counts": lambda b: not b["counts"]["stop"]["stops"],
            "door": lambda b: b["door"]["passes"]}[key](body)


def _books(files: str | None) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    for path in (files or "").split(","):
        if not path:
            continue
        for k, row in json.loads(Path(path).read_text(encoding="utf-8"))["books"].items():
            if k in rows:
                raise ValueError(f"book {k} appears twice")
            rows[k] = row
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vol_managed", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("vol-overlap", "vol-counts", "vol-door", "vol-null", "vol-plant",
                                            "vol-book"))
    parser.add_argument("--seeds", default=None, help="vol-null: lo:hi within 0:600; vol-plant: within 0:50")
    parser.add_argument("--rho", type=float, default=None, help=f"vol-plant: one of {PLANT_RHOS}")
    parser.add_argument("--plant", default=None, help="vol-book: comma-separated vol-plant outputs, every size")
    parser.add_argument("--overlap", type=Path, default=None)
    parser.add_argument("--counts", type=Path, default=None)
    parser.add_argument("--door", type=Path, default=None)
    parser.add_argument("--null", default=None, help="vol-book: comma-separated vol-null outputs, 600 books in all")
    parser.add_argument("--archive", type=Path, default=None, help="default: $RESEARCH_CACHE/archive")
    args = parser.parse_args(argv)
    if not xsb._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    seeds = _seeds(args.seeds)
    if args.command == "vol-null" and seeds is None:
        print("refused: --seeds lo:hi within 0:600", file=sys.stderr)
        return EXIT_USAGE
    if args.command == "vol-plant" and (seeds is None or seeds.stop > PLANT_SEEDS or args.rho not in PLANT_RHOS):
        print(f"refused: --rho one of {PLANT_RHOS} and --seeds lo:hi within 0:{PLANT_SEEDS}", file=sys.stderr)
        return EXIT_USAGE
    if args.command in ("vol-counts", "vol-door", "vol-null", "vol-book") and not _passed(args.overlap, "overlap"):
        print("refused: needs a passing --overlap first", file=sys.stderr)
        return EXIT_USAGE
    if args.command in ("vol-door", "vol-null", "vol-book") and not _passed(args.counts, "counts"):
        print("refused: needs --counts that did not stop", file=sys.stderr)
        return EXIT_USAGE
    if args.command in ("vol-null", "vol-plant", "vol-book") and not _passed(args.door, "door"):
        print("refused: needs a passing --door", file=sys.stderr)
        return EXIT_USAGE
    nulls: list[float | None] = []
    plants: dict[str, Any] = {}
    if args.command == "vol-book":
        try:
            rows, plants = _books(args.null), _books(args.plant)
        except ValueError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return EXIT_USAGE
        if sorted(rows, key=int) != [str(k) for k in range(NULLS)]:
            print(f"refused: --null must hold the {NULLS} seeds 0..{NULLS - 1}", file=sys.stderr)
            return EXIT_USAGE
        want = sorted(f"{rho}|{k}" for rho in PLANT_RHOS for k in range(PLANT_SEEDS))
        if sorted(plants) != want:
            print(f"refused: --plant must hold {PLANT_SEEDS} seeds at each of {PLANT_RHOS}", file=sys.stderr)
            return EXIT_USAGE
        nulls = [rows[str(k)]["annual_sharpe"] for k in range(NULLS)]
    try:
        bs.check_development_span(SPAN)
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return EXIT_USAGE
    archive = args.archive or xsb._archive_default()
    snapshot = xsb.read_contract_types(archive)
    if snapshot is None:
        print(f"BLOCKED: no readable {xsb.CONTRACT_TYPES_FILENAME} under {archive}", file=sys.stderr)
        return EXIT_BLOCKED
    market = xsb.load_market(archive)
    market.contract_types = snapshot[0]
    market = bs.cut(market, SPAN[1])                     # before anything is computed
    members = monthly(market, HISTORY_START, SPAN[1])
    span_days = decisions(*SPAN)
    table = scales(market, members, decisions(HISTORY_START, SPAN[1]))
    report: dict[str, Any] = {"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC,
                              "command": args.command}
    if args.command == "vol-overlap":
        report["overlap"] = overlap(market, table, members, span_days)
    elif args.command == "vol-counts":
        report["counts"] = counts(table, span_days)
    elif args.command == "vol-door":
        report["door"] = door(market, members)
    elif args.command == "vol-null":
        report["books"] = {str(s): {"annual_sharpe": run(market, members, weigher(table, shuffled(table, span_days, s)))
                                    ["annual_sharpe"]} for s in seeds}
    elif args.command == "vol-plant":
        weeks = base_weeks(market, members, span_days)
        books = {}
        for s in seeds:
            r = run(market, members, weigher(table, planted(table, span_days, weeks, args.rho, s)))
            books[f"{args.rho}|{s}"] = {"rho": args.rho, "seed": s, "annual_sharpe": r["annual_sharpe"],
                                        "clears_entry": r["clears_entry"]}
        report["books"] = books
    else:
        real = run(market, members, weigher(table))
        report["book"] = real
        report["judge"] = judge(real, nulls)
        report["plant"] = plant_reading(plants, nulls)
        nulls_kept = sorted(v for v in nulls if v is not None)
        report["null_p95"] = nulls_kept[int(0.95 * (len(nulls_kept) - 1))] if nulls_kept else None
    print(json.dumps(report, indent=1, sort_keys=True, ensure_ascii=False, default=str))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
