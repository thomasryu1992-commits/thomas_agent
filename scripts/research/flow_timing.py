"""Stage A of the USDT net-inflow timing control: the price-free gates (loop §149, FLOW_TIMING_V0.1).

``docs/proposals/FLOW_TIMING_V0.1.md`` (Thomas 2026-11-24: "설계 시작", §148 Q1 and §149 Q1 "기본값대로").
USDT's net inflow to known exchange addresses on Ethereum is one number a day for the whole market, so it can only
time exposure. Stage A reads no price and no return:

- **The label** for the decision at 00:00 UTC of day D: ``x`` = the net inflow (in − out, both sides known left
  out) over days D−W … D−1, against the median of the 90 earlier decisions' ``x`` (D−90 … D−1). Above → +1 (hold
  more), else −1. Every ``x`` a decision compares is built from **one address set**, the one known at D (in the
  list at the end of D−1), so an address joining the list moves the window and its base together and no level
  jump reaches the label. The past transfers of an address known at D are on chain for anyone at D.
- **Windows** W = 1, 3, 7 days, one family: α = 0.05 / 3 whatever stops first.
- **Counts gate** per window: paper cost (a switch trades gross 4 at 8 bps = 0.16 % of gross 2) under 15 % a
  year; no year with 20+ labels whose mean state reaches 0.25 in size; each state 20+ decisions in 4+ spells.
- **Synthetic plant** per window left: independent normal daily returns stand in for the base book; the record's
  own ``planted_state`` sets each real spell's state from ``ρ·score + noise``, and the record's spell shuffle
  (``null_state``, 600) is the control on the same returns. Size 0 is the false-alarm control (more than 3 of 50
  at p ≤ α voids). Read one way only: such returns have no trend and no fat tail, so the door looks brighter than
  it is. ρ 0.20 not seen by 80 % of seeds stops the window; seen only earns it stage B (prices, its own
  registration).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from scripts.research import cross_section_basket as xsb  # noqa: E402
from scripts.research import timing_labels as tl  # noqa: E402
from scripts.research import vol_managed as vm  # noqa: E402

SPEC = "FLOW_TIMING_V0.1"
FIRST_DAY = date(2022, 7, 27)                 # the fetched span (EXCHANGE_FLOW_DATA_V0.1 Q6); nothing before it
LAST_DECISION = date(2024, 12, 31)            # flows after 2024-12-31 are not built into the past track (§5)
WINDOWS = (1, 3, 7)
BASE = 90                                     # earlier decisions the median is taken over
ALPHA = 0.05 / len(WINDOWS)
SWITCH_COST = 4 * 8 / 10_000 / 2              # gross 4 traded at 8 bps, as a share of gross 2
MAX_COST = 0.15
MAX_YEAR_DRIFT = 0.25
MIN_YEAR_LABELS = 20
MIN_DECISIONS = 20
MIN_SPELLS = 4
NULLS = 600
PLANT_RHOS = (0.0, 0.05, 0.10, 0.20)
PLANT_SEEDS = 50
PLANT_SEES = 0.80
PLANT_FALSE_ALARMS = 3
MUST_SEE = 0.20

Keys = Mapping[str, Sequence[tuple[date, date | None]]]
Flows = Mapping[date, Sequence[tuple[str, str, float]]]


# --- inputs -------------------------------------------------------------------------------------------------------

def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def load_keys(path: Path) -> dict[str, list[tuple[date, date | None]]]:
    """``{key: [(f, u), ...]}``: an address key is known on day D when ``f <= D < u`` for one of its periods."""
    raw = json.loads(path.read_text())
    return {k: [(date.fromisoformat(f), date.fromisoformat(u) if u else None) for f, u in periods]
            for k, periods in raw.items()}


def load_flows(path: Path) -> dict[date, list[tuple[str, str, float]]]:
    """``{day: [(from_key, to_key, usdt), ...]}`` from the keyed daily sums; '' is an address in no key. A row with a
    quantity the chain could not read refuses the whole file."""
    out: dict[date, list[tuple[str, str, float]]] = {}
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            if int(row["n_bad_qty"]):
                raise ValueError(f"{row['day']}: {row['n_bad_qty']} transfers without a readable quantity")
            out.setdefault(date.fromisoformat(row["day"]), []).append((row["from_key"], row["to_key"], float(row["usdt"])))
    return out


# --- the label ----------------------------------------------------------------------------------------------------

def known(keys: Keys, day: date) -> frozenset[str]:
    return frozenset(k for k, periods in keys.items() if any(f <= day and (u is None or day < u) for f, u in periods))


def net(rows: Iterable[tuple[str, str, float]], members: frozenset[str]) -> float:
    """In minus out for one address set: a transfer counts when exactly one side is in the set."""
    total = 0.0
    for fk, tk, usdt in rows:
        a, b = fk in members, tk in members
        if b and not a:
            total += usdt
        elif a and not b:
            total -= usdt
    return total


def decisions(window: int) -> list[date]:
    first = FIRST_DAY + timedelta(days=BASE + window)
    return [first + timedelta(days=i) for i in range((LAST_DECISION - first).days + 1)]


def states(flows: Flows, keys: Keys, window: int) -> dict[date, int]:
    """``{decision: +1 | −1}``. Reads flows on days before the decision only, all with the set known at it."""
    cache: dict[tuple[frozenset[str], date], float] = {}

    def daily(members: frozenset[str], day: date) -> float:
        k = (members, day)
        if k not in cache:
            cache[k] = net(flows.get(day, ()), members)
        return cache[k]

    def x(members: frozenset[str], decision: date) -> float:
        return sum(daily(members, decision - timedelta(days=j)) for j in range(1, window + 1))

    out = {}
    for d in decisions(window):
        members = known(keys, d)
        middle = statistics.median(x(members, d - timedelta(days=j)) for j in range(1, BASE + 1))
        out[d] = 1 if x(members, d) > middle else -1
    return out


# --- the counts gate ----------------------------------------------------------------------------------------------

def counts(state: Mapping[date, int]) -> dict[str, Any]:
    days = sorted(state)
    spells = tl._spells(state, days)
    switches = len(spells) - 1
    years_span = len(days) / 365.25
    cost = switches * SWITCH_COST / years_span if days else None
    by_year: dict[str, Any] = {}
    for y in sorted({d.year for d in days}):
        ss = [state[d] for d in days if d.year == y]
        by_year[str(y)] = {"labels": len(ss), "mean_state": round(statistics.fmean(ss), 4),
                           "judged": len(ss) >= MIN_YEAR_LABELS}
    drift = max((abs(v["mean_state"]) for v in by_year.values() if v["judged"]), default=0.0)
    per_state = {}
    for s in (1, -1):
        per_state[str(s)] = {"decisions": sum(1 for d in days if state[d] == s),
                             "spells": sum(1 for run in spells if state[run[0]] == s)}
    enough = all(v["decisions"] >= MIN_DECISIONS and v["spells"] >= MIN_SPELLS for v in per_state.values())
    gates = {"cost": cost is not None and cost < MAX_COST, "year_drift": drift < MAX_YEAR_DRIFT, "minimum": enough}
    return {"decisions": len(days), "first": str(days[0]) if days else None, "switches": switches,
            "switches_per_year": round(switches / years_span, 2) if days else None,
            "paper_cost_per_year": round(cost, 4) if cost is not None else None, "by_year": by_year,
            "largest_year_drift": round(drift, 4), "states": per_state, "gates": gates, "passes": all(gates.values())}


# --- the synthetic plant ------------------------------------------------------------------------------------------

def sharpe(state: Mapping[date, int | None], returns: Mapping[date, float], days: Sequence[date]) -> float | None:
    xs = [state[d] * returns[d] for d in days if state[d] is not None]
    n = len(xs)
    if n < 2:
        return None
    mean = math.fsum(xs) / n
    var = math.fsum(v * v for v in xs) / n - mean * mean     # population variance; statistics.pstdev is exact and slow
    return mean / math.sqrt(var) * math.sqrt(365) if var > 0 else None


def synthetic_returns(days: Sequence[date], window: int, rho: float, seed: int) -> dict[date, float]:
    rng = random.Random(f"{SPEC}|W{window}|returns|{rho}|{seed}")
    return {d: rng.gauss(0.0, 1.0) for d in days}


def null_states(state: Mapping[date, int], window: int) -> list[dict[date, int | None]]:
    days = sorted(state)
    return [tl.null_state(state, days, f"{SPEC}|W{window}", k) for k in range(NULLS)]


def plant_book(state: Mapping[date, int], nulls: Sequence[Mapping[date, int | None]], window: int, rho: float,
               seed: int) -> dict[str, Any]:
    days = sorted(state)
    r = synthetic_returns(days, window, rho, seed)
    planted = tl.planted_state(state, days, r, rho, seed)
    value = sharpe(planted, r, days)
    p = vm.null_p(value, [sharpe(n, r, days) for n in nulls])
    return {"rho": rho, "seed": seed, "annual_sharpe": value, "p": p, "good_share": tl.good_share(planted)}


def plant_reading(books: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_rho = {}
    for rho in PLANT_RHOS:
        rows = [b for b in books if b["rho"] == rho]
        seen = sum(1 for b in rows if b["p"] is not None and b["p"] <= ALPHA)
        by_rho[str(rho)] = {"seeds": len(rows), "p_at_alpha": seen,
                            "sees": bool(rows) and seen / len(rows) >= PLANT_SEES,
                            "mean_good_share": round(statistics.fmean(b["good_share"] for b in rows), 4) if rows else None}
    false_alarms = by_rho[str(0.0)]["p_at_alpha"]
    void = false_alarms > PLANT_FALSE_ALARMS
    smallest = None if void else min((rho for rho in PLANT_RHOS if rho > 0 and by_rho[str(rho)]["sees"]), default=None)
    return {"by_rho": by_rho, "false_alarms": false_alarms, "void": void, "smallest_seen": smallest,
            "passes": (not void) and by_rho[str(MUST_SEE)]["sees"], "alpha": round(ALPHA, 4)}


# --- the command --------------------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="flow_timing", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("flow-counts", "flow-plant"))
    parser.add_argument("--flows", type=Path, required=True, help="keyed daily sums (outside the repo)")
    parser.add_argument("--keys", type=Path, required=True, help="address keys and their known periods")
    parser.add_argument("--window", type=int, default=None)
    parser.add_argument("--counts", type=Path, default=None, help="the flow-counts report")
    args = parser.parse_args(argv)
    if not xsb._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    if args.command == "flow-plant":
        body = json.loads(args.counts.read_text()) if args.counts and args.counts.exists() else None
        if not body or "counts" not in body:
            print("refused: flow-plant needs --counts first", file=sys.stderr)
            return EXIT_USAGE
        left = [int(w) for w, c in body["counts"].items() if c["passes"]]
        if args.window not in left:
            print(f"refused: --window must be one of {left} (the windows the counts gate left)", file=sys.stderr)
            return EXIT_USAGE
        if body.get("inputs") != {"flows": _sha(args.flows), "keys": _sha(args.keys)}:
            print("refused: --flows/--keys are not the files the counts read", file=sys.stderr)
            return EXIT_USAGE
    try:
        keys, flows = load_keys(args.keys), load_flows(args.flows)
    except (OSError, ValueError, KeyError) as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return EXIT_BLOCKED
    if not flows or min(flows) > FIRST_DAY or max(flows) < LAST_DECISION - timedelta(days=1):
        print("BLOCKED: the flows do not cover the span", file=sys.stderr)
        return EXIT_BLOCKED
    report: dict[str, Any] = {"measured_at": timeutil.utc_now_iso(), "spec": SPEC, "command": args.command,
                              "inputs": {"flows": _sha(args.flows), "keys": _sha(args.keys)}}
    if args.command == "flow-counts":
        report["counts"] = {str(w): counts(states(flows, keys, w)) for w in WINDOWS}
    else:
        w = args.window
        state = states(flows, keys, w)
        nulls = null_states(state, w)
        books = [plant_book(state, nulls, w, rho, s) for rho in PLANT_RHOS for s in range(PLANT_SEEDS)]
        report["window"] = w
        report["books"] = books
        report["plant"] = plant_reading(books)
    print(json.dumps(report, indent=1, sort_keys=True, ensure_ascii=False, default=str))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
