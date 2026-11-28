"""L29 timing on recorded labels: does holding the always-long top-30 book at twice its size in one state of a
recorded market label, and not at all in the other, add anything over holding it at a constant size?

``docs/proposals/TIMING_LABEL_EXPOSURE_V0.1.md`` (Thomas 2026-11-20: "타이밍 후보 설계", then "펀딩 수준도 넣기").
Four labels from ``signal_lab.scenario_labels``, as recorded: each axis's value at a decision against the median of
its own 52 earlier decisions, no label under 26 of them. :data:`AXES` in this order, one family.

- **The base book** is L28's and the trend book's always-long book (``vol_managed``): inverse-volatility shares
  ``v`` of the month's top 30, weekly, through ``cross_section_basket.simulate``.
- **What is judged is the excess:** ``2·s·v`` with ``s = +1`` in the good state (the book at twice its size) and
  ``s = −1`` in the state named in :data:`CUT` (the book at nothing); no label, no excess (an unread week).
- **Order (fixed before anything is read):** overlap and counts for every axis (no return) → for the axes left, the
  door (the recorded T01 numbers again) → 600 null books of the record's own spell shuffle → 150 planted books
  (the real spells kept, each spell's state set by a planted signal) → the real book once. Each stage refuses
  without the one before it.
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
from scripts.research import signal_lab as lab  # noqa: E402
from scripts.research import strategy_ladder as sl  # noqa: E402
from scripts.research import trend_book_search as tbs  # noqa: E402
from scripts.research import vol_managed as vm  # noqa: E402

SPEC = "TIMING_LABEL_EXPOSURE_V0.1"
SPAN = vm.SPAN
AXES = ("breadth", "dominance", "correlation", "heat")          # the order overlap compares them in
CUT = {"breadth": "thin", "dominance": "btc_led", "correlation": "tight", "heat": "hot"}
FUNDING_AXES = frozenset({"heat"})            # must also clear the Sharpe floor with the funding leg left out
ALPHA = 0.05 / len(AXES)
OVERLAP_CLOSE = 0.5                           # on |r|: a mirror carries the same information
MAX_YEAR_DRIFT = 0.25
MIN_YEAR_LABELS = 20                          # a year with fewer labelled decisions is not judged for drift
NULLS = 600
PLANT_RHOS = (0.0,) + vm.PLANT_RHOS          # size 0 is the false-alarm control
PLANT_SEEDS = vm.PLANT_SEEDS
PLANT_SEES = vm.PLANT_SEES
PLANT_FALSE_ALARMS = 3                        # more of the 50 size-0 seeds at p <= ALPHA voids the plant reading


# --- the states ---------------------------------------------------------------------------------------------------

def states(labels: Mapping[date, Mapping[str, str]], span_days: Sequence[date]) -> dict[str, dict[date, int | None]]:
    """``{axis: {decision: +1 good, −1 cut, None unlabelled}}`` from the recorded labels."""
    out: dict[str, dict[date, int | None]] = {}
    for axis in AXES:
        row = {}
        for day in span_days:
            label = (labels.get(day) or {}).get(axis)
            row[day] = None if label is None else (-1 if label == CUT[axis] else 1)
        out[axis] = row
    return out


def shares_by_day(market: xsb.Market, members, span_days: Sequence[date]) -> dict[date, dict[str, float]]:
    return {d: tbs.shares(market, members.get(date(d.year, d.month, 1), ()), d, vm.LOOKBACK)[0] for d in span_days}


def excess_weights(share: Mapping[str, float], s: int | None) -> dict[str, float]:
    if not s or not share:
        return {}
    return {name: bs.POSITION_TOTAL * s * v for name, v in sorted(share.items())}


def weigher(shares: Mapping[date, Mapping[str, float]], state: Mapping[date, int | None]):
    def weights(market, members, day, signal, *, seed=None):
        return excess_weights(shares.get(day, {}), state.get(day))
    return weights


def null_state(state: Mapping[date, int | None], span_days: Sequence[date], axis: str, seed: int) -> dict[date, int | None]:
    """The record's own placebo (``signal_lab.shuffled_spells``): the same spells in another order, the unlabelled
    decisions left where they are."""
    days = sorted(span_days)
    moved = lab.shuffled_spells([state[d] for d in days], f"{SPEC}|{axis}|null|{seed}")
    return dict(zip(days, moved))


def _spells(state: Mapping[date, int | None], days: Sequence[date]) -> list[list[date]]:
    runs: list[list[date]] = []
    previous = None
    for d in days:
        s = state[d]
        if s is None:
            previous = None
            continue
        if runs and s == previous:
            runs[-1].append(d)
        else:
            runs.append([d])
        previous = s
    return runs


def planted_state(state: Mapping[date, int | None], span_days: Sequence[date], weeks: Mapping[date, float],
                  rho: float, seed: int) -> dict[date, int | None]:
    """The real spells kept in place and length; each spell is good when the mean over its weeks of
    ``rho·(normal score of the week's base return) + √(1 − rho²)·noise`` is above the median of the spells' means.
    A spell with no week of return keeps its real state."""
    rng = random.Random(f"{SPEC}|plant|{rho}|{seed}")
    days = sorted(span_days)
    known = [d for d in days if d in weeks and state[d] is not None]
    ranked = sorted(known, key=lambda d: (weeks[d], d))
    n = len(ranked)
    score = {d: statistics.NormalDist().inv_cdf((i + 0.5) / n) for i, d in enumerate(ranked)}
    z = {d: rho * score[d] + math.sqrt(1 - rho * rho) * rng.gauss(0, 1) for d in known}
    runs = _spells(state, days)
    means = {i: statistics.fmean(z[d] for d in run if d in z) for i, run in enumerate(runs) if any(d in z for d in run)}
    middle = statistics.median(means.values()) if means else 0.0
    out = dict(state)
    for i, run in enumerate(runs):
        if i in means:
            for d in run:
                out[d] = 1 if means[i] > middle else -1
    return out


# --- the gates without a return -----------------------------------------------------------------------------

def overlap(market: xsb.Market, members, span_days: Sequence[date], by_axis: Mapping[str, Mapping[date, int | None]],
            l28: Mapping[date, dict]) -> dict[str, Any]:
    """Per axis: the weekly correlation of its excess net exposure ``2s`` with T01's and T02's excess net exposure,
    with L28's scale ``2(k − 1)`` (its read weeks only) and with every earlier axis. ``|r|`` at
    :data:`OVERLAP_CLOSE` or above closes it."""
    references: dict[str, dict[date, float]] = {}
    for name, lookback in tbs.DEFINITIONS.items():
        row = {}
        for day in span_days:
            share, rets = tbs.shares(market, members.get(date(day.year, day.month, 1), ()), day, lookback)
            if share:
                row[day] = -bs.POSITION_TOTAL * sum(v for s, v in share.items() if rets[s] <= 0)
        references[name] = row
    references["L28"] = {d: bs.POSITION_TOTAL * (r["k"] - 1.0) for d, r in l28.items()
                         if d in set(span_days) and r.get("target") is not None and r.get("sigma") is not None}
    out: dict[str, Any] = {}
    for i, axis in enumerate(AXES):
        mine = {d: bs.POSITION_TOTAL * s for d, s in by_axis[axis].items() if s is not None}
        against = dict(references)
        for earlier in AXES[:i]:
            against[earlier] = {d: bs.POSITION_TOTAL * s for d, s in by_axis[earlier].items() if s is not None}
        rows = {}
        for name, ref in against.items():
            weeks = sorted(set(mine) & set(ref))
            rows[name] = {"weeks": len(weeks), "correlation": vm._corr([mine[d] for d in weeks], [ref[d] for d in weeks])}
        worst = max((abs(r["correlation"]) for r in rows.values() if r["correlation"] is not None), default=None)
        out[axis] = {"against": rows, "unread_weeks": sum(1 for s in by_axis[axis].values() if s is None),
                     "passes": worst is not None and worst < OVERLAP_CLOSE}
    return {"axes": out, "close_at_abs": OVERLAP_CLOSE}


def counts(by_axis: Mapping[str, Mapping[date, int | None]], shares: Mapping[date, Mapping[str, float]],
           span_days: Sequence[date]) -> dict[str, Any]:
    """Per axis, no return read: the labelled, good and cut decisions and flips per year, the signed mean of ``s``
    per year, each state's decisions and spells over the span, and the paper cost of trading the excess."""
    unit = (xsb.cost.DEFAULT_TAKER_FEE_BPS + xsb.cost.DEFAULT_SLIPPAGE_BPS) / 10_000   # the engine's own
    n_years = len(bs._complete_months(*SPAN)) / 12
    days = sorted(span_days)
    out: dict[str, Any] = {}
    for axis in AXES:
        state = by_axis[axis]
        years: dict[str, dict[str, Any]] = {}
        held: dict[str, float] = {}
        paper, previous = 0.0, None
        for day in days:
            s = state[day]
            y = years.setdefault(str((day + timedelta(days=1)).year), {"decisions": 0, "unlabelled": 0, "good": 0,
                                                                         "cut": 0, "flips": 0, "signed": []})
            y["decisions"] += 1
            if s is None:
                y["unlabelled"] += 1
            else:
                y["good" if s > 0 else "cut"] += 1
                y["signed"].append(s)
                y["flips"] += previous is not None and s != previous
                previous = s
            target = excess_weights(shares.get(day, {}), s)
            paper += sum(abs(target.get(k, 0.0) - held.get(k, 0.0)) for k in set(held) | set(target)) * unit
            held = target
        drift = 0.0
        for y in years.values():
            signed = y.pop("signed")
            y["mean_s"] = round(statistics.fmean(signed), 4) if signed else None
            if len(signed) >= MIN_YEAR_LABELS:
                drift = max(drift, abs(y["mean_s"]))
        positions = {v: [i for i, d in enumerate(days) if state[d] == v] for v in (1, -1)}
        whole = {("good" if v > 0 else "cut"): {"decisions": len(p), "spells": lab.spells(p)} for v, p in positions.items()}
        thin = any(w["decisions"] < lab.MIN_DECISIONS or w["spells"] < lab.MIN_SPELLS for w in whole.values())
        paper_pct = paper * 100 / bs.POSITION_TOTAL / n_years
        stops = drift >= MAX_YEAR_DRIFT or thin or paper_pct >= bs.ENTRY_RETURN_TOTAL_PCT
        out[axis] = {"years": years, "states": whole, "largest_year_drift": round(drift, 4),
                     "paper_cost_total_position_annual_pct": round(paper_pct, 3),
                     "stop": {"year_drift_ceiling": MAX_YEAR_DRIFT, "spell_minimum": [lab.MIN_DECISIONS, lab.MIN_SPELLS],
                              "paper_cost_ceiling_pct": bs.ENTRY_RETURN_TOTAL_PCT, "thin": thin, "stops": stops}}
    return out


# --- the books --------------------------------------------------------------------------------------------------

def run(market: xsb.Market, members, weigh) -> dict[str, Any]:
    """``vol_managed.run``'s reading, plus the annual Sharpe with the funding leg left out."""
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
    unfunded = {d: {**r, "net": r["net"] - r["funding"]} for d, r in sim["daily"].items()}
    report["annual_sharpe_without_funding"] = bs.annual_sharpe(unfunded, start, end)[0]
    return report


def judged_value(axis: str, row: Mapping[str, Any]) -> float | None:
    """What the null p reads: the annual Sharpe, or for a funding axis the annual Sharpe without the funding leg (the
    shuffled nulls do not carry the funding the label times)."""
    return row.get("annual_sharpe_without_funding") if axis in FUNDING_AXES else row.get("annual_sharpe")


def good_share(state: Mapping[date, int | None]) -> float | None:
    labelled = [s for s in state.values() if s is not None]
    return round(sum(1 for s in labelled if s > 0) / len(labelled), 4) if labelled else None


def judge(axis: str, report: Mapping[str, Any], nulls: Sequence[float | None]) -> dict[str, Any]:
    """``nulls`` are the null books' :func:`judged_value`."""
    p = vm.null_p(judged_value(axis, report), nulls)
    a, b = bool(report["clears_entry"]), bool(report["later_years"]["passes"])
    c = p is not None and p <= ALPHA
    unfunded = report.get("annual_sharpe_without_funding")
    d = axis not in FUNDING_AXES or (unfunded is not None and unfunded >= bs.ENTRY_SHARPE)
    return {"entry": a, "later_years": b, "null": {"books": len(nulls), "p": round(p, 4) if p is not None else None,
                                                    "alpha": round(ALPHA, 4), "passes": c},
            "p_reads": "annual_sharpe_without_funding" if axis in FUNDING_AXES else "annual_sharpe",
            "without_funding": {"applies": axis in FUNDING_AXES, "annual_sharpe": unfunded, "passes": d},
            "candidate": a and b and c and d}


def plant_reading(axis: str, rows: Mapping[str, Mapping[str, Any]], nulls: Sequence[float | None]) -> dict[str, Any]:
    """Per size, the seeds at p <= :data:`ALPHA` and those also clearing the entry rule. Size 0 is the control: more
    than :data:`PLANT_FALSE_ALARMS` of its seeds at p <= ALPHA voids the reading (the planted books then beat the null
    without a planted effect, e.g. on the share of good weeks), and a void reading says nothing about size."""
    out = {}
    for rho in PLANT_RHOS:
        books = [r for r in rows.values() if r["rho"] == rho]
        ps = [vm.null_p(judged_value(axis, r), nulls) for r in books]
        seen = sum(1 for p in ps if p is not None and p <= ALPHA)
        both = sum(1 for p, r in zip(ps, books) if p is not None and p <= ALPHA and r["clears_entry"])
        out[str(rho)] = {"seeds": len(books), "p_at_alpha": seen, "and_entry": both,
                         "sees": bool(books) and seen / len(books) >= PLANT_SEES,
                         "mean_good_share": round(statistics.fmean(r["good_share"] for r in books), 4) if books else None}
    void = out[str(0.0)]["p_at_alpha"] > PLANT_FALSE_ALARMS
    sizes = [rho for rho in PLANT_RHOS if rho > 0]
    return {"by_rho": out, "false_alarms": out[str(0.0)]["p_at_alpha"], "void": void,
            "smallest_seen": None if void else min((rho for rho in sizes if out[str(rho)]["sees"]), default=None)}


# --- the command --------------------------------------------------------------------------------------------------

def _read(path: Path | None) -> dict[str, Any] | None:
    return None if path is None else json.loads(path.read_text(encoding="utf-8"))


def left(overlap_body: Mapping[str, Any] | None, counts_body: Mapping[str, Any] | None) -> list[str]:
    """The axes that passed overlap and were not stopped by the counts."""
    if not overlap_body or not counts_body:
        return []
    return [a for a in AXES if overlap_body["overlap"]["axes"][a]["passes"]
            and not counts_body["counts"][a]["stop"]["stops"]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="timing_labels", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("timing-overlap", "timing-counts", "timing-door", "timing-null",
                                            "timing-plant", "timing-book"))
    parser.add_argument("--axis", choices=AXES, default=None)
    parser.add_argument("--seeds", default=None)
    parser.add_argument("--rho", type=float, default=None)
    parser.add_argument("--overlap", type=Path, default=None)
    parser.add_argument("--counts", type=Path, default=None)
    parser.add_argument("--door", type=Path, default=None)
    parser.add_argument("--null", default=None)
    parser.add_argument("--plant", default=None)
    parser.add_argument("--archive", type=Path, default=None, help="default: $RESEARCH_CACHE/archive")
    args = parser.parse_args(argv)
    if not xsb._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    cmd = args.command
    over, cnt, door_body = _read(args.overlap), _read(args.counts), _read(args.door)
    if cmd != "timing-overlap" and not (over and "overlap" in over):
        print("refused: needs --overlap first", file=sys.stderr)
        return EXIT_USAGE
    if cmd in ("timing-door", "timing-null", "timing-plant", "timing-book") and not left(over, cnt):
        print("refused: needs --counts, and an axis left after overlap and counts", file=sys.stderr)
        return EXIT_USAGE
    if cmd in ("timing-null", "timing-plant", "timing-book"):
        if not (door_body and door_body["door"]["passes"]):
            print("refused: needs a passing --door", file=sys.stderr)
            return EXIT_USAGE
        if args.axis not in left(over, cnt):
            print(f"refused: --axis must be one of {left(over, cnt)}", file=sys.stderr)
            return EXIT_USAGE
    seeds = None
    if cmd == "timing-null":
        seeds = vm._seeds(args.seeds)
        if seeds is None:
            print("refused: --seeds lo:hi within 0:600", file=sys.stderr)
            return EXIT_USAGE
    if cmd == "timing-plant":
        seeds = vm._seeds(args.seeds)
        if seeds is None or seeds.stop > PLANT_SEEDS or args.rho not in PLANT_RHOS:
            print(f"refused: --rho one of {PLANT_RHOS} and --seeds lo:hi within 0:{PLANT_SEEDS}", file=sys.stderr)
            return EXIT_USAGE
    nulls: list[float | None] = []
    plants: dict[str, Any] = {}
    if cmd == "timing-book":
        try:
            rows, plants = vm._books(args.null), vm._books(args.plant)
        except ValueError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return EXIT_USAGE
        if sorted(rows, key=int) != [str(k) for k in range(NULLS)] or any(r.get("axis") != args.axis for r in rows.values()):
            print(f"refused: --null must hold this axis's {NULLS} seeds 0..{NULLS - 1}", file=sys.stderr)
            return EXIT_USAGE
        want = sorted(f"{rho}|{k}" for rho in PLANT_RHOS for k in range(PLANT_SEEDS))
        if sorted(plants) != want or any(r.get("axis") != args.axis for r in plants.values()):
            print(f"refused: --plant must hold this axis's {PLANT_SEEDS} seeds at each of {PLANT_RHOS}", file=sys.stderr)
            return EXIT_USAGE
        nulls = [judged_value(args.axis, rows[str(k)]) for k in range(NULLS)]
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
    members = vm.monthly(market, vm.HISTORY_START, SPAN[1])
    span_days = vm.decisions(*SPAN)
    labels = lab.scenario_labels(market, lab.tier_members(market, SPAN)["top30"], span_days, SPAN)
    by_axis = states(labels, span_days)
    report: dict[str, Any] = {"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC, "command": cmd,
                              "axis": args.axis}
    if cmd == "timing-overlap":
        l28 = vm.scales(market, members, vm.decisions(vm.HISTORY_START, SPAN[1]))
        report["overlap"] = overlap(market, members, span_days, by_axis, l28)
    elif cmd == "timing-counts":
        report["counts"] = counts(by_axis, shares_by_day(market, members, span_days), span_days)
    elif cmd == "timing-door":
        report["door"] = vm.door(market, members)
        report["left"] = left(over, cnt)
    else:
        shares = shares_by_day(market, members, span_days)
        state = by_axis[args.axis]
        if cmd == "timing-null":
            books = {}
            for s in seeds:
                r = run(market, members, weigher(shares, null_state(state, span_days, args.axis, s)))
                books[str(s)] = {"axis": args.axis, "annual_sharpe": r["annual_sharpe"],
                                 "annual_sharpe_without_funding": r["annual_sharpe_without_funding"]}
            report["books"] = books
        elif cmd == "timing-plant":
            weeks = vm.base_weeks(market, members, span_days)
            books = {}
            for s in seeds:
                planted = planted_state(state, span_days, weeks, args.rho, s)
                r = run(market, members, weigher(shares, planted))
                books[f"{args.rho}|{s}"] = {"axis": args.axis, "rho": args.rho, "seed": s,
                                            "annual_sharpe": r["annual_sharpe"],
                                            "annual_sharpe_without_funding": r["annual_sharpe_without_funding"],
                                            "clears_entry": r["clears_entry"], "good_share": good_share(planted)}
            report["books"] = books
        else:
            real = run(market, members, weigher(shares, state))
            report["book"] = real
            report["judge"] = judge(args.axis, real, nulls)
            report["good_share"] = good_share(state)
            report["plant"] = plant_reading(args.axis, plants, nulls)
            kept = sorted(v for v in nulls if v is not None)
            report["null_p95"] = kept[int(0.95 * (len(kept) - 1))] if kept else None
    print(json.dumps(report, indent=1, sort_keys=True, ensure_ascii=False, default=str))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
