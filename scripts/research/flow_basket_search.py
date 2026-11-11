"""Flow basket search: the two metrics columns the metrics search never ran, as weekly cross-section baskets.

``docs/proposals/FLOW_AND_TREND_SEARCH_V0.1.md`` part A (Thomas 2026-11-11: "순서대로 전부"). This reopens the feature
list of ``METRICS_BASKET_SEARCH_V0.1``, whose record said its fifth attempt would not be used; the reopening is
recorded there and here. The engine, the universe and the daily values are that search's.

- **Features:** the top traders' long share and the taker buy share only (:data:`ALLOWED`). M04's two features and
  the divergence (which carries the all-account share) are out: a pass must not be M04 screened twice.
- **Span:** ``metrics_basket_search.DEV_SHORT`` (the columns are mostly empty before 2023-02). Nothing after
  2024-12-31 is read: the confirm span is spent and the days from 2026-10-01 are reserved.
- **Budget:** :data:`BUDGET` attempts, each registered with its direction and reason before its one run. A sign
  flipped after a result is a new attempt. The first to clear the entry rule ends the search.
- **Entry rule:** ``basket_search.clears_entry``: annual Sharpe at 1.0 and at least 15% a year net on total position.
- **No confirm command.** A pass earns a question to Thomas about a forward screen, nothing else.
- **Shown with a result:** how many of the candidate's legs sit on M04's side on the same decisions.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from scripts.research import basket_search as bs  # noqa: E402
from scripts.research import cross_section_basket as xsb  # noqa: E402
from scripts.research import metrics_basket_search as ms  # noqa: E402

SPEC = "FLOW_AND_TREND_SEARCH_V0.1"
DEV = ms.DEV_SHORT
BUDGET = 3
ALLOWED = ("top_level", "taker_level")
REFERENCE = "M04"                         # the screened candidate a pass is compared with

# The attempts, in the order they were run. Each is added in its own commit, run once, and recorded before the next.
CANDIDATES: dict[str, bs.Candidate] = {
}


def _monthly(market: xsb.Market, span: tuple[date, date]) -> dict[date, list[str]]:
    start, end = span
    monthly = xsb.universes(market, top_n=bs.TOP_N, start=date(start.year, start.month, 1) - timedelta(days=1), end=end)
    return {date(m.year, m.month, 1): v for m, v in monthly.items()}


def _decisions(span: tuple[date, date]) -> list[date]:
    return [day - timedelta(days=1) for day in xsb.held_days(*span)
            if xsb.is_rebalance_day(bs.CADENCE, day - timedelta(days=1))]


def ranked_counts(market: xsb.Market, features: tuple[str, ...], span: tuple[date, date]) -> dict[str, Any]:
    """How many members each decision can rank on ``features``. Dates and counts only."""
    monthly = _monthly(market, span)
    probe = bs.Candidate(why="count", features=tuple((name, 1.0) for name in features))
    rows = []
    for decision in _decisions(span):
        members = monthly.get(date(decision.year, decision.month, 1), ())
        ranked = len(bs.scores(market, members, decision, probe, ms.FEATURES))
        rows.append((len(members), ranked, xsb.side_size(ranked)))
    return {"features": list(features), "decisions": len(rows),
            "members_ranked_side_weeks": [[*key, n] for key, n in sorted(Counter(rows).items())],
            "weeks_without_a_basket": sum(1 for _, _, side in rows if side == 0)}


def reference_overlap(market: xsb.Market, candidate: bs.Candidate, span: tuple[date, date]) -> dict[str, Any]:
    """On decisions where both hold a basket: the share of the candidate's legs that the reference holds on the same
    side. Positions only; a random basket of a fifth a side lands near a fifth of a fifth."""
    monthly = _monthly(market, span)
    mine, theirs = bs.weigher(candidate, ms.FEATURES), bs.weigher(ms.CANDIDATES[REFERENCE], ms.FEATURES)
    shares = []
    for decision in _decisions(span):
        members = monthly.get(date(decision.year, decision.month, 1), ())
        a, b = mine(market, members, decision, bs.CADENCE), theirs(market, members, decision, bs.CADENCE)
        if a and b:
            shares.append(sum(1 for s, w in a.items() if s in b and (b[s] > 0) == (w > 0)) / len(a))
    return {"reference": REFERENCE, "decisions_compared": len(shares),
            "mean_share_of_legs_on_the_references_side": round(sum(shares) / len(shares), 3) if shares else None}


def explore(market: xsb.Market, candidate: bs.Candidate) -> dict[str, Any]:
    body = bs.measure(market, candidate, DEV, ms.FEATURES)
    body["total_position_annual_pct"] = bs.total_position_annual_pct(body["sum_pct"], body["months"])
    body["entry_return_total_pct"] = bs.ENTRY_RETURN_TOTAL_PCT
    body["pass"] = bs.clears_entry(body)
    body["overlap"] = reference_overlap(market, candidate, DEV)
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="flow_basket_search", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("list", "counts", "explore"))
    parser.add_argument("--candidate", default=None)
    parser.add_argument("--cache", type=Path, default=None, help="default: $RESEARCH_CACHE")
    args = parser.parse_args(argv)
    cache = args.cache or Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache")))
    if args.command == "list":
        print(json.dumps({"budget": BUDGET, "development_span": [d.isoformat() for d in DEV], "allowed": list(ALLOWED),
                          "candidates": {k: c.as_dict() for k, c in CANDIDATES.items()}}, indent=1, ensure_ascii=False))
        return EXIT_OK
    if args.command == "explore":
        if not xsb._in_sandbox():
            print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
            return EXIT_USAGE
        if args.candidate not in CANDIDATES:
            print(f"refused: no candidate {args.candidate!r}; registered: {', '.join(CANDIDATES) or 'none'}", file=sys.stderr)
            return EXIT_USAGE
        if len(CANDIDATES) > BUDGET:
            print(f"refused: {len(CANDIDATES)} candidates are over the budget of {BUDGET}", file=sys.stderr)
            return EXIT_USAGE
        if not {name for name, _ in CANDIDATES[args.candidate].features} <= set(ALLOWED):
            print(f"refused: this search reads only {', '.join(ALLOWED)}", file=sys.stderr)
            return EXIT_USAGE
    try:
        bs.check_development_span(DEV)
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return EXIT_USAGE
    archive = cache / "archive"
    snapshot = xsb.read_contract_types(archive)
    daily = ms.load_daily(cache)
    if snapshot is None or not daily:
        print(f"BLOCKED: no contract types under {archive} or no derived metrics under {cache}", file=sys.stderr)
        return EXIT_BLOCKED
    market = xsb.load_market(archive, funding=args.command == "explore")
    market.contract_types = snapshot[0]
    market = ms.cut(market, daily, DEV[1])               # before anything is computed
    if args.command == "counts":
        print(json.dumps({"span": [d.isoformat() for d in DEV],
                          "counts": [ranked_counts(market, (name,), DEV) for name in ALLOWED]
                          + [ranked_counts(market, ALLOWED, DEV)]}, indent=1))
        return EXIT_OK
    report = {"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC, "candidate": args.candidate,
              "definition": CANDIDATES[args.candidate].as_dict(), "entry_sharpe": bs.ENTRY_SHARPE,
              **explore(market, CANDIDATES[args.candidate])}
    print(json.dumps(report, indent=1, sort_keys=True, ensure_ascii=False))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
