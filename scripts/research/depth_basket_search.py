"""Depth basket search: does the shape of the order book rank next week's returns across coins?

``docs/proposals/DEPTH_BASKET_SEARCH_V0.1.md`` (Thomas 2026-11-11: "호가 불균형 탐색"). The engine, the universe and
the entry rule are the earlier searches'; the data is new: the public archive's daily ``bookDepth`` files (about
every 30 seconds, the cumulative notional within ±1%..±5% of the mid; a negative percentage is the bid side).

- **Daily value** (host, ``build``): from the rows stamped that UTC day only, per snapshot the imbalance
  ``(bid − ask) / (bid + ask)`` within ±1% and within ±5%, and the two-sided notional within ±1%. A day's value is
  the mean imbalance and the median depth, when at least ``stressed_coin_depth.MIN_SNAPSHOTS`` snapshots carry both
  sides.
- **Features:** the mean imbalance over the lookback days, and the growth of depth (the last seven days' mean depth
  over the seven days ending thirty days earlier). A decision at a day's close reads that day and earlier only.
- **One hypothesis per attempt, not a grid.** :data:`BUDGET` attempts, each a different hypothesis with its
  direction and reason fixed before its run. A candidate's neighbours (another band, another lookback) are run in
  the same call and shown only: a pass whose neighbour falls under :data:`FRAGILE_BELOW` is reported as fragile.
- **Span:** :data:`DEV`, inside development (``basket_search.check_development_span``); the files start 2023-01-01.
- **Entry rule:** ``basket_search.clears_entry``. No confirm command: a pass earns a question about a forward screen.
- **Commands:** ``plan``, ``fetch``, ``build`` on the host; ``counts`` and ``explore`` (sandbox only for ``explore``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from scripts import binance_archive as ba  # noqa: E402
from scripts.research import basket_search as bs  # noqa: E402
from scripts.research import cross_section_basket as xsb  # noqa: E402
from scripts.research import metrics_basket_search as ms  # noqa: E402
from scripts.research import stressed_coin_depth as sd  # noqa: E402

SPEC = "DEPTH_BASKET_SEARCH_V0.1"
DEV = (date(2023, 2, 1), date(2024, 12, 31))
FILES_FROM = date(2023, 1, 1)             # the archive's first bookDepth day
BUDGET = 2
GROWTH_GAP_DAYS = 30                      # depth growth compares the last week with the week ending this long ago
GROWTH_WINDOW_DAYS = 7
PLAN_LOOKBACK = GROWTH_GAP_DAYS + GROWTH_WINDOW_DAYS - 1     # the earliest day a feature reads before a member month
FRAGILE_BELOW = 0.5                       # a passing candidate whose shown neighbour's annual Sharpe is under this
REFERENCE = "M04"
COLUMNS = ("imb1", "imb5", "depth1")
Daily = dict[str, dict[date, dict[str, float | None]]]

# The attempts, in the order they were run. Each is added in its own commit, run once, and recorded before the next.
CANDIDATES: dict[str, bs.Candidate] = {
}
# What is run beside a candidate and shown only: the same hypothesis at another band and another lookback.
NEIGHBOURS: dict[str, dict[str, bs.Candidate]] = {
}


# --- the daily values (host) ---------------------------------------------------------------------------------

def daily_values(rows: Iterable[Mapping[str, str]], day: date) -> dict[str, float | None]:
    """One UTC day's values from its snapshots; None where fewer than the floor carry both sides."""
    snaps: dict[str, dict[float, float]] = {}
    for row in rows:
        stamp = str(row.get("timestamp"))
        if stamp[:10] != day.isoformat():
            continue                                    # a row stamped another day is not this day's
        try:
            pct, notional = float(row["percentage"]), float(row["notional"])
        except (KeyError, TypeError, ValueError):
            continue
        if abs(pct) in (1.0, 5.0) and notional > 0:
            snaps.setdefault(stamp, {})[pct] = notional
    imb1, imb5, depth1 = [], [], []
    for sides in snaps.values():
        if -1.0 in sides and 1.0 in sides:
            bid, ask = sides[-1.0], sides[1.0]
            imb1.append((bid - ask) / (bid + ask))
            depth1.append(bid + ask)
        if -5.0 in sides and 5.0 in sides:
            bid, ask = sides[-5.0], sides[5.0]
            imb5.append((bid - ask) / (bid + ask))
    enough = lambda values: len(values) >= sd.MIN_SNAPSHOTS  # noqa: E731
    return {"imb1": statistics.fmean(imb1) if enough(imb1) else None,
            "imb5": statistics.fmean(imb5) if enough(imb5) else None,
            "depth1": statistics.median(depth1) if enough(depth1) else None}


def derived_dir(cache: Path) -> Path:
    return cache / "derived" / "depth-daily"


def fingerprint(cache: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(derived_dir(cache).glob("*.jsonl")):
        digest.update(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n".encode())
    return digest.hexdigest()[:16]


def build(cache: Path, *, say: Callable[[str], None] = print) -> int:
    """Write each symbol's daily values from the bookDepth zips on disk. Host only; values, not returns."""
    source = cache / sd.DEPTH_DIR / ba.BOOK_DEPTH
    written = 0
    for folder in sorted(p for p in source.iterdir() if p.is_dir()):
        lines = []
        for path in sorted(folder.glob("*.zip")):
            try:
                day = date.fromisoformat(path.stem[-10:])
            except ValueError:
                continue
            values = daily_values(sd._read_zip(path) or (), day)
            lines.append(json.dumps([day.isoformat(), *(values[name] for name in COLUMNS)]))
        out = derived_dir(cache) / f"{folder.name}.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
        written += len(lines)
    say(f"built {written} symbol-day(s) under {derived_dir(cache)}; fingerprint {fingerprint(cache)}")
    return written


def load_daily(cache: Path) -> Daily:
    out: Daily = {}
    for path in sorted(derived_dir(cache).glob("*.jsonl")):
        rows = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                day, *values = json.loads(line)
                rows[date.fromisoformat(day)] = dict(zip(COLUMNS, values))
        out[path.stem] = rows
    return out


# --- the features ----------------------------------------------------------------------------------------------

def cut(market: xsb.Market, depth: Daily, end: date, metrics: ms.Daily | None = None) -> xsb.Market:
    """The market, the depth values and (for the overlap with M04) the metrics as they stood at ``end``'s close."""
    held = ms.cut(market, metrics or {}, end)
    held.depth = {s: {d: v for d, v in days.items() if d <= end} for s, days in depth.items()}
    return held


def _window(market: xsb.Market, symbol: str, last: date, length: int, column: str) -> list[float]:
    days = getattr(market, "depth", {}).get(symbol) or {}
    values = [(days.get(last - timedelta(days=k)) or {}).get(column) for k in range(length)]
    kept = [v for v in values if v is not None]
    return kept if len(kept) >= math.ceil(length * bs.MIN_SHARE) else []


def _level(column: str):
    def feature(market, symbol, day, lookback):
        values = _window(market, symbol, day, lookback, column)
        return statistics.fmean(values) if values else None
    return feature


def _depth_growth(market, symbol, day, lookback):
    now = _window(market, symbol, day, GROWTH_WINDOW_DAYS, "depth1")
    then = _window(market, symbol, day - timedelta(days=lookback), GROWTH_WINDOW_DAYS, "depth1")
    if not now or not then or statistics.fmean(then) <= 0:
        return None
    return statistics.fmean(now) / statistics.fmean(then) - 1


FEATURES: dict[str, bs.Feature] = {
    "imbalance_1": _level("imb1"), "imbalance_5": _level("imb5"), "depth_growth": _depth_growth,
}


# --- counts, overlap, the run ----------------------------------------------------------------------------------

def _monthly(market: xsb.Market, span: tuple[date, date]) -> dict[date, list[str]]:
    start, end = span
    monthly = xsb.universes(market, top_n=bs.TOP_N, start=date(start.year, start.month, 1) - timedelta(days=1), end=end)
    return {date(m.year, m.month, 1): v for m, v in monthly.items()}


def _decisions(span: tuple[date, date]) -> list[date]:
    return [day - timedelta(days=1) for day in xsb.held_days(*span)
            if xsb.is_rebalance_day(bs.CADENCE, day - timedelta(days=1))]


def _reads(candidate: bs.Candidate, decision: date) -> list[tuple[date, str]]:
    """The (day, column) cells a candidate's features read for one decision."""
    cells: list[tuple[date, str]] = []
    for name, _ in candidate.features:
        if name == "depth_growth":
            for anchor in (decision, decision - timedelta(days=candidate.lookback)):
                cells += [(anchor - timedelta(days=k), "depth1") for k in range(GROWTH_WINDOW_DAYS)]
        else:
            column = "imb1" if name == "imbalance_1" else "imb5"
            cells += [(decision - timedelta(days=k), column) for k in range(candidate.lookback)]
    return cells


def ranked_counts(market: xsb.Market, candidate: bs.Candidate, span: tuple[date, date], absent: set[str],
                  ) -> dict[str, Any]:
    """How many members each decision can rank, and why the others cannot. Dates and counts only.

    A cause is named only when it is one of: the archive does not serve the day (``missing.json``), the day is
    before the archive's first bookDepth day, or the file holds a partial day. Anything else is ``UNEXPLAINED``."""
    monthly = _monthly(market, span)
    rows, causes, odd = [], Counter(), []
    for decision in _decisions(span):
        members = monthly.get(date(decision.year, decision.month, 1), ())
        ranked = set(bs.scores(market, members, decision, candidate, FEATURES))
        rows.append((len(members), len(ranked), xsb.side_size(len(ranked))))
        for symbol in members:
            if symbol in ranked:
                continue
            days = getattr(market, "depth", {}).get(symbol) or {}
            found = set()
            for day, column in _reads(candidate, decision):
                row = days.get(day)
                if row is not None and row.get(column) is not None:
                    continue
                if day < FILES_FROM:
                    found.add("before the archive's first day")
                elif row is not None:
                    found.add("partial day")
                elif ba.daily_name(symbol, ba.BOOK_DEPTH, day) in absent:
                    found.add("not served")
                else:
                    found.add("UNEXPLAINED")
            key = " + ".join(sorted(found)) or "UNEXPLAINED (every cell present)"
            causes[key] += 1
            if "UNEXPLAINED" in key:
                odd.append((symbol, decision.isoformat()))
    return {"features": [n for n, _ in candidate.features], "lookback": candidate.lookback, "decisions": len(rows),
            "members_ranked_side_weeks": [[*key, n] for key, n in sorted(Counter(rows).items())],
            "weeks_without_a_basket": sum(1 for _, _, side in rows if side == 0),
            "unranked_by_cause": dict(causes.most_common()), "unexplained": odd[:20], "unexplained_count": len(odd)}


def reference_overlap(market: xsb.Market, candidate: bs.Candidate, span: tuple[date, date]) -> dict[str, Any]:
    """The share of the candidate's legs the reference holds on the same side; chance is about a fifth."""
    monthly = _monthly(market, span)
    mine, theirs = bs.weigher(candidate, FEATURES), bs.weigher(ms.CANDIDATES[REFERENCE], ms.FEATURES)
    shares = []
    for decision in _decisions(span):
        members = monthly.get(date(decision.year, decision.month, 1), ())
        a, b = mine(market, members, decision, bs.CADENCE), theirs(market, members, decision, bs.CADENCE)
        if a and b:
            shares.append(sum(1 for s, w in a.items() if s in b and (b[s] > 0) == (w > 0)) / len(a))
    return {"reference": REFERENCE, "decisions_compared": len(shares), "chance_level": 0.2,
            "mean_share_of_legs_on_the_references_side": round(sum(shares) / len(shares), 3) if shares else None}


def explore(market: xsb.Market, name: str) -> dict[str, Any]:
    candidate = CANDIDATES[name]
    body = bs.measure(market, candidate, DEV, FEATURES)
    body["total_position_annual_pct"] = bs.total_position_annual_pct(body["sum_pct"], body["months"])
    body["entry_return_total_pct"] = bs.ENTRY_RETURN_TOTAL_PCT
    body["pass"] = bs.clears_entry(body)
    shown = {}
    for label, neighbour in NEIGHBOURS.get(name, {}).items():
        other = bs.measure(market, neighbour, DEV, FEATURES)
        shown[label] = {"definition": neighbour.as_dict(), "annual_sharpe": other["annual_sharpe"],
                        "sum_pct": other["sum_pct"]}
    sharpes = [v["annual_sharpe"] for v in shown.values()]
    body["shown_only_neighbours"] = shown
    body["fragile"] = bool(body["pass"] and any(s is None or s < FRAGILE_BELOW for s in sharpes))
    body["overlap"] = reference_overlap(market, candidate, DEV)
    return body


# --- the host side ---------------------------------------------------------------------------------------------

def fetch_plan(market: xsb.Market) -> list[tuple[str, date, date]]:
    """Every member month of the development span and the :data:`PLAN_LOOKBACK` days before it, from the archive's
    first bookDepth day."""
    monthly = xsb.universes(market, top_n=bs.TOP_N, start=date(DEV[0].year, DEV[0].month, 1) - timedelta(days=1), end=DEV[1])
    plan = ms.fetch_plan(monthly, lookback=PLAN_LOOKBACK, end=DEV[1])
    return [(s, max(first, FILES_FROM), last) for s, first, last in plan if last >= FILES_FROM]


def fetch(cache: Path, plan: list[tuple[str, date, date]], *, fetcher: Callable[..., Any] = ba.fetch,
          quiet: Callable[[], bool] | None = None, say: Callable[[str], None] = print,
          sleep: Callable[[float], None] = time.sleep, retries: int = 6) -> int:
    """Resumable: a file on disk is never asked for again; a network error is retried, never recorded as missing."""
    from scripts.research import funding_basis_refresh as fbr

    quiet = quiet or (lambda: fbr.in_quiet(datetime.now(timezone.utc)))
    total = {"kept": 0, "present": 0, "missing": 0}
    for number, (symbol, first, last) in enumerate(plan, 1):
        for attempt in range(retries):
            if quiet():
                say("HALTED: inside the quiet window 07:30–09:00 UTC; rerun the same command after 09:00 UTC")
                return EXIT_BLOCKED
            try:
                tally = fetcher(cache / sd.DEPTH_DIR, symbols=[symbol], start=first, end=last, kind=ba.BOOK_DEPTH)[symbol]
                break
            except Exception as exc:  # noqa: BLE001 — network or checksum: wait and ask again
                say(f"  {symbol} {first}..{last} attempt {attempt + 1}: {type(exc).__name__}: {exc}")
                sleep(20 * (attempt + 1))
        else:
            say(f"HALTED: {symbol} {first}..{last} failed {retries} times; rerun the same command to resume")
            return EXIT_BLOCKED
        for key in total:
            total[key] += tally[key]
        if tally["kept"] and number % 10 == 0:
            say(f"[{number}/{len(plan)}] {symbol}: total {total}")
    say(f"DONE bookDepth: {len(plan)} span(s); kept {total['kept']}, present {total['present']}, "
        f"not served {total['missing']}")
    return EXIT_OK


def _absent(cache: Path) -> set[str]:
    try:
        missing = json.loads((cache / sd.DEPTH_DIR / ba.MISSING_FILENAME).read_text())
    except (OSError, ValueError):
        return set()
    return {name for names in missing.values() for name in names}


# --- the command line ------------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="depth_basket_search", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("list", "plan", "fetch", "build", "counts", "explore"))
    parser.add_argument("--candidate", default=None)
    parser.add_argument("--cache", type=Path, default=None, help="default: $RESEARCH_CACHE")
    args = parser.parse_args(argv)
    cache = args.cache or Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache")))
    if args.command == "list":
        print(json.dumps({"budget": BUDGET, "development_span": [d.isoformat() for d in DEV],
                          "candidates": {k: c.as_dict() for k, c in CANDIDATES.items()},
                          "neighbours": {k: {n: c.as_dict() for n, c in v.items()} for k, v in NEIGHBOURS.items()}},
                         indent=1, ensure_ascii=False))
        return EXIT_OK
    if args.command == "explore" and not xsb._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    if args.command in ("fetch", "build") and xsb._in_sandbox():
        print(f"refused: {args.command} runs on the host, not in the sandbox", file=sys.stderr)
        return EXIT_USAGE
    if args.command in ("counts", "explore"):
        if args.candidate not in CANDIDATES:
            print(f"refused: no candidate {args.candidate!r}; registered: {', '.join(CANDIDATES) or 'none'}", file=sys.stderr)
            return EXIT_USAGE
        if len(CANDIDATES) > BUDGET:
            print(f"refused: {len(CANDIDATES)} candidates are over the budget of {BUDGET}", file=sys.stderr)
            return EXIT_USAGE
        if not {n for n, _ in CANDIDATES[args.candidate].features} <= set(FEATURES):
            print("refused: the candidate names a feature outside this search's list", file=sys.stderr)
            return EXIT_USAGE
    try:
        bs.check_development_span(DEV)
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if args.command == "build":
        build(cache)
        return EXIT_OK
    archive = cache / "archive"
    snapshot = xsb.read_contract_types(archive)
    if snapshot is None:
        print(f"BLOCKED: no readable {xsb.CONTRACT_TYPES_FILENAME} under {archive}", file=sys.stderr)
        return EXIT_BLOCKED
    market = xsb.load_market(archive, funding=args.command == "explore")
    market.contract_types = snapshot[0]
    if args.command in ("plan", "fetch"):
        plan = fetch_plan(bs.cut(market, DEV[1]))
        if args.command == "fetch":
            return fetch(cache, plan)
        print(json.dumps({"spans": len(plan), "symbols": len({s for s, _, _ in plan}),
                          "symbol_days": sum((b - a).days + 1 for _, a, b in plan)}, indent=1))
        return EXIT_OK
    depth = load_daily(cache)
    if not depth:
        print(f"BLOCKED: no derived depth values under {derived_dir(cache)} (run `build` on the host first)", file=sys.stderr)
        return EXIT_BLOCKED
    held = cut(market, depth, DEV[1], ms.load_daily(cache))    # before anything is computed
    if args.command == "counts":
        print(json.dumps({"span": [d.isoformat() for d in DEV], "fingerprint": fingerprint(cache),
                          **ranked_counts(held, CANDIDATES[args.candidate], DEV, _absent(cache))}, indent=1))
        return EXIT_OK
    report = {"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC, "candidate": args.candidate,
              "definition": CANDIDATES[args.candidate].as_dict(), "entry_sharpe": bs.ENTRY_SHARPE,
              "depth_fingerprint": fingerprint(cache), **explore(held, args.candidate)}
    print(json.dumps(report, indent=1, sort_keys=True, ensure_ascii=False))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
