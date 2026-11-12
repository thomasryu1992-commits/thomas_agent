"""Spot share basket: does the share of a coin's volume that trades on spot rank next week's returns?

``docs/proposals/SPOT_SHARE_SEARCH_V0.1.md`` (Thomas 2026-11-12, ``SEARCH_CANDIDATE_SURVEY_V0.1`` V1: once, with a
condition). The engine, universe, cadence, costs and entry rule are ``basket_search``'s; only the feature is new.

- **One attempt.** :data:`BUDGET` is 1. The hypothesis and its direction were fixed before any return was read:
  a coin whose perpetual volume dwarfs its spot volume is where leverage is crowding, so buy the HIGH spot share and
  sell the LOW. A flipped sign or another window is a new attempt, and there is no slot for one.
- **The neighbour is shown only.** The same hypothesis on :data:`NEIGHBOUR_LOOKBACK` days is run beside it; a pass
  whose neighbour's annual Sharpe is under :data:`FRAGILE_BELOW` is written down as fragile.
- **Independence from M04 is part of the rule.** S01 comes from the same "less crowded side" idea as M04, which
  already cleared these years. So a pass counts as a candidate of its own only if its legs stand on M04's side less
  than :data:`OVERLAP_LIMIT` of the time (chance is about a fifth), over at least :data:`MIN_COMPARED` decisions.
- **A day counts only if it is the same asset.** ``spot_pair`` matches by ticker, and a ticker can be reused (the
  old LUNA and the new one). A day enters the window only when the perp's close is within :data:`PRICE_BAND` of the
  spot's close (times 1000 for a ``1000``-prefixed perp whose spot pair is unprefixed).
- **Every unranked member has a named cause**, or nothing runs: no spot pair, the perp is younger than the window,
  the archive does not serve the spot month, the day is outside the spot pair's own first and last day, or the
  ticker is another asset.
- **Span:** :data:`basket_search.DEV`, the same 60 months as the ten price attempts. The universe has under four
  members until 2020-04, so the first weeks hold no basket; that is the span's own shape, not a choice made here.

``plan`` / ``fetch`` run on the host (public archive, spot 1d klines); ``counts`` prints dates and counts only;
``explore`` runs in the research sandbox, once.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from scripts import binance_archive as ba  # noqa: E402
from scripts.research import basket_search as bs  # noqa: E402
from scripts.research import cross_section_basket as xsb  # noqa: E402
from scripts.research import funding_basis_v02 as fb  # noqa: E402
from scripts.research import metrics_basket_search as ms  # noqa: E402

SPEC = "SPOT_SHARE_SEARCH_V0.1"
DEV = bs.DEV
BUDGET = 1
LOOKBACK = 30
NEIGHBOUR_LOOKBACK = 90
FRAGILE_BELOW = 0.5
PRICE_BAND = 0.10                         # a day counts when the perp's close is within this of the spot's close
REFERENCE = "M04"
OVERLAP_LIMIT = 0.40                      # legs on the reference's side at or above this: not a candidate of its own
MIN_COMPARED = 52                         # decisions the overlap needs before it can clear a candidate
PLAN_MONTHS_BEFORE = 4                    # spot months fetched before a member month (the 90-day neighbour's window)
SPOT_DIR = "archive-spot"
KLINES = "klines-1d"
SERVED_FILENAME = "served.json"

# The attempt. It was registered with the rules, before any spot file was fetched for it and before any return.
CANDIDATES: dict[str, bs.Candidate] = {
    "S01": bs.Candidate(why="선물 거래가 현물보다 유난히 많은 코인은 레버리지 투기가 몰린 곳으로 본다: 지난 30일의 현물 거래대금 비중(현물 ÷ (현물 + 무기한))이 높은 쪽을 사고 낮은 쪽을 판다.", features=(("spot_share", 1.0),), lookback=LOOKBACK),
}
# Run beside the candidate and shown only: the same hypothesis on a longer window.
NEIGHBOURS: dict[str, dict[str, bs.Candidate]] = {
    "S01": {"lookback_90": bs.Candidate(why="S01과 같은 가설을 90일 창으로.", features=(("spot_share", 1.0),), lookback=NEIGHBOUR_LOOKBACK)},
}


# --- the spot side ---------------------------------------------------------------------------------------------

def derived_dir(cache: Path) -> Path:
    return cache / "derived" / "spot-share"


def pair_of(perp: str, spot_listing: set[str]) -> tuple[str, float] | None:
    """The spot pair a perp is compared with and the price multiple between them, or None when there is no pair."""
    pair = fb.spot_pair(perp, spot_listing)
    if pair is None:
        return None
    for prefix in fb.SPOT_PREFIXES:
        if perp.startswith(prefix) and pair == perp[len(prefix):]:
            return pair, float(prefix)
    return pair, 1.0


def attach(held: xsb.Market, spot: xsb.Market, spot_listing: set[str], end: date) -> xsb.Market:
    """Hang the spot bars (cut at ``end``) and each perp's pair on a market already cut at ``end``."""
    held.spot = {s: {d: b for d, b in bars.items() if d <= end} for s, bars in spot.bars.items()}
    held.spot_of = {perp: pair_of(perp, spot_listing) for perp in held.bars}
    return held


def _days(market: xsb.Market, symbol: str, day: date, lookback: int) -> tuple[list[tuple[float, float]], Counter]:
    """The window's usable days as (spot quote volume, perp quote volume), and why the others are not usable."""
    why: Counter = Counter()
    link = getattr(market, "spot_of", {}).get(symbol)
    if link is None:
        return [], Counter({"no pair": lookback})
    pair, multiple = link
    perp, spot = market.bars.get(symbol) or {}, getattr(market, "spot", {}).get(pair) or {}
    kept = []
    for k in range(lookback):
        d = day - timedelta(days=k)
        a, b = perp.get(d), spot.get(d)
        if a is None:
            why["no perp bar"] += 1
        elif b is None:
            why["no spot bar"] += 1
        elif a.close <= 0 or b.close <= 0 or abs(a.close / (multiple * b.close) - 1) > PRICE_BAND:
            why["another asset"] += 1
        else:
            kept.append((b.quote_volume, a.quote_volume))
    return kept, why


def _spot_share(market, symbol, day, lookback):
    kept, _ = _days(market, symbol, day, lookback)
    if len(kept) < math.ceil(lookback * bs.MIN_SHARE):
        return None
    spot, perp = sum(s for s, _ in kept), sum(p for _, p in kept)
    return spot / (spot + perp) if spot + perp > 0 else None


FEATURES: dict[str, bs.Feature] = {"spot_share": _spot_share}


# --- counts, overlap, the run ----------------------------------------------------------------------------------

def _monthly(market: xsb.Market, span: tuple[date, date]) -> dict[date, list[str]]:
    start, end = span
    monthly = xsb.universes(market, top_n=bs.TOP_N, start=date(start.year, start.month, 1) - timedelta(days=1), end=end)
    return {date(m.year, m.month, 1): v for m, v in monthly.items()}


def _decisions(span: tuple[date, date]) -> list[date]:
    return [day - timedelta(days=1) for day in xsb.held_days(*span)
            if xsb.is_rebalance_day(bs.CADENCE, day - timedelta(days=1))]


def _cause(market: xsb.Market, symbol: str, day: date, lookback: int, served: Mapping[str, list[str]]) -> str:
    """Why one member cannot be ranked at one decision. Anything that is not one of the named causes is UNEXPLAINED."""
    link = getattr(market, "spot_of", {}).get(symbol)
    if link is None:
        return "no spot pair"
    pair = link[0]
    _, why = _days(market, symbol, day, lookback)
    need = math.ceil(lookback * bs.MIN_SHARE)
    if lookback - why["no perp bar"] < need:
        return "the perp is younger than the window"
    spot = getattr(market, "spot", {}).get(pair) or {}
    first, last = (min(spot), max(spot)) if spot else (None, None)
    found = set()
    for k in range(lookback):
        d = day - timedelta(days=k)
        if d not in (market.bars.get(symbol) or {}) or d in spot:
            continue
        if f"{d:%Y-%m}" not in set(served.get(pair, ())):
            found.add("the archive does not serve the spot month")
        elif first is not None and (d < first or d > last):
            found.add("outside the spot pair's first and last day")
        else:
            found.add("UNEXPLAINED")
    if why["another asset"]:
        found.add("another asset under the same ticker")
    return " + ".join(sorted(found)) or "UNEXPLAINED (every day usable)"


def ranked_counts(market: xsb.Market, candidate: bs.Candidate, span: tuple[date, date],
                  served: Mapping[str, list[str]]) -> dict[str, Any]:
    """How many members each decision can rank, and why the others cannot. Dates and counts only."""
    monthly = _monthly(market, span)
    rows, causes, odd = [], Counter(), []
    for decision in _decisions(span):
        members = monthly.get(date(decision.year, decision.month, 1), ())
        ranked = set(bs.scores(market, members, decision, candidate, FEATURES))
        rows.append((len(members), len(ranked), xsb.side_size(len(ranked))))
        for symbol in members:
            if symbol in ranked:
                continue
            cause = _cause(market, symbol, decision, candidate.lookback, served)
            causes[cause] += 1
            if "UNEXPLAINED" in cause:
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
    share = round(sum(shares) / len(shares), 3) if shares else None
    enough = len(shares) >= MIN_COMPARED
    return {"reference": REFERENCE, "decisions_compared": len(shares), "minimum_compared": MIN_COMPARED,
            "chance_level": 0.2, "limit": OVERLAP_LIMIT, "mean_share_of_legs_on_the_references_side": share,
            "independent": bool(share < OVERLAP_LIMIT) if enough and share is not None else None}


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
    # a forward slot may be asked for only on a pass that is neither fragile nor the reference seen again
    body["forward_slot_may_be_asked"] = bool(body["pass"] and not body["fragile"]
                                             and body["overlap"]["independent"] is True)
    return body


# --- the host side ---------------------------------------------------------------------------------------------

def fetch_plan(market: xsb.Market, spot_listing: set[str]) -> list[tuple[str, date, date]]:
    """Each member's spot pair, from :data:`PLAN_MONTHS_BEFORE` months before its first member month to its last."""
    spans: dict[str, tuple[date, date]] = {}
    for month, members in _monthly(market, DEV).items():
        if not DEV[0] <= month <= DEV[1]:
            continue
        for perp in members:
            link = pair_of(perp, spot_listing)
            if link is None:
                continue
            first = month
            for _ in range(PLAN_MONTHS_BEFORE):
                first = (first - timedelta(days=1)).replace(day=1)
            last = min(date(month.year + (month.month == 12), month.month % 12 + 1, 1) - timedelta(days=1), DEV[1])
            lo, hi = spans.get(link[0], (first, last))
            spans[link[0]] = (min(lo, first), max(hi, last))
    return sorted((pair, lo, hi) for pair, (lo, hi) in spans.items())


def fetch(cache: Path, plan: list[tuple[str, date, date]], *, fetcher: Callable[..., Any] = ba.fetch_monthly,
          lister: Callable[..., Any] = ba.listing, quiet: Callable[[], bool] | None = None,
          say: Callable[[str], None] = print) -> int:
    """Fetch every planned spot month the archive's own listing holds, and write down which months it serves."""
    from datetime import datetime, timezone

    from scripts.research import funding_basis_refresh as fbr

    quiet = quiet or (lambda: fbr.in_quiet(datetime.now(timezone.utc)))
    total = {"kept": 0, "present": 0, "missing": 0}
    served: dict[str, list[str]] = {}
    for number, (pair, first, last) in enumerate(plan, 1):
        if quiet():
            say("HALTED: inside the quiet window 07:30–09:00 UTC; rerun the same command after 09:00 UTC")
            return EXIT_BLOCKED
        tally = fetcher(cache / SPOT_DIR, kind=KLINES, symbols=[pair], start=first, end=last, market="spot")[pair]
        _, keys = lister(ba.monthly_folder(KLINES, pair, "spot"))
        months = sorted({f"{m[0]:04d}-{m[1]:02d}" for m in (ba._month_of(k.rsplit("/", 1)[-1]) for k in keys
                                                           if k.endswith(".zip")) if m})
        served[pair] = months
        for key in total:
            total[key] += tally[key]
        if number % 20 == 0:
            say(f"[{number}/{len(plan)}] {pair}: total {total}")
    out = derived_dir(cache) / SERVED_FILENAME
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(served, indent=0, sort_keys=True), encoding="utf-8")
    say(f"DONE spot {KLINES}: {len(plan)} pair(s); kept {total['kept']}, present {total['present']}; "
        f"served months written to {out}")
    return EXIT_OK


def read_served(cache: Path) -> dict[str, list[str]] | None:
    try:
        return json.loads((derived_dir(cache) / SERVED_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def fingerprint(cache: Path, pairs: list[str]) -> str:
    """The spot files the run reads, by name and size, and the served-months file."""
    digest = hashlib.sha256()
    for pair in sorted(pairs):
        for path in sorted((cache / SPOT_DIR / KLINES / pair).glob("*.zip")):
            digest.update(f"{pair}/{path.name}:{path.stat().st_size}\n".encode())
    served = derived_dir(cache) / SERVED_FILENAME
    digest.update(served.read_bytes() if served.exists() else b"")
    return digest.hexdigest()[:16]


# --- the command line ------------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="spot_share_search", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("list", "plan", "fetch", "counts", "explore"))
    parser.add_argument("--candidate", default=None)
    parser.add_argument("--cache", type=Path, default=None, help="default: $RESEARCH_CACHE")
    args = parser.parse_args(argv)
    cache = args.cache or Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache")))
    if args.command == "list":
        print(json.dumps({"budget": BUDGET, "development_span": [d.isoformat() for d in DEV],
                          "overlap_limit": OVERLAP_LIMIT, "minimum_compared": MIN_COMPARED,
                          "candidates": {k: c.as_dict() for k, c in CANDIDATES.items()},
                          "neighbours": {k: {n: c.as_dict() for n, c in v.items()} for k, v in NEIGHBOURS.items()}},
                         indent=1, ensure_ascii=False))
        return EXIT_OK
    if args.command == "explore" and not xsb._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    if args.command == "fetch" and xsb._in_sandbox():
        print("refused: fetch runs on the host, not in the sandbox", file=sys.stderr)
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
    archive = cache / "archive"
    snapshot = xsb.read_contract_types(archive)
    try:
        spot_listing = set(json.loads((cache / SPOT_DIR / ba.SYMBOLS_FILENAME).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        spot_listing = set()
    if snapshot is None or not spot_listing:
        print(f"BLOCKED: no readable {xsb.CONTRACT_TYPES_FILENAME} under {archive}, or no spot symbol list", file=sys.stderr)
        return EXIT_BLOCKED
    market = xsb.load_market(archive, funding=args.command == "explore")
    market.contract_types = snapshot[0]
    if args.command in ("plan", "fetch"):
        plan = fetch_plan(bs.cut(market, DEV[1]), spot_listing)
        if args.command == "fetch":
            return fetch(cache, plan)
        print(json.dumps({"pairs": len(plan), "first_month": min(a for _, a, _ in plan).isoformat(),
                          "last_month": max(b for _, _, b in plan).isoformat()}, indent=1))
        return EXIT_OK
    served = read_served(cache)
    if served is None:
        print(f"BLOCKED: no {SERVED_FILENAME} under {derived_dir(cache)} (run `fetch` on the host first)", file=sys.stderr)
        return EXIT_BLOCKED
    held = attach(ms.cut(market, ms.load_daily(cache), DEV[1]), xsb.load_market(cache / SPOT_DIR, funding=False),
                  spot_listing, DEV[1])                              # before anything is computed
    pairs = sorted({link[0] for link in held.spot_of.values() if link})
    if args.command == "counts":
        print(json.dumps({"span": [d.isoformat() for d in DEV], "fingerprint": fingerprint(cache, pairs),
                          **ranked_counts(held, CANDIDATES[args.candidate], DEV, served)}, indent=1))
        return EXIT_OK
    report = {"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC, "candidate": args.candidate,
              "definition": CANDIDATES[args.candidate].as_dict(), "entry_sharpe": bs.ENTRY_SHARPE,
              "inputs_fingerprint": fingerprint(cache, pairs), **explore(held, args.candidate)}
    print(json.dumps(report, indent=1, sort_keys=True, ensure_ascii=False))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
