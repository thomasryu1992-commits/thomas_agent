"""Forward panel: sixteen closed basket definitions, as registered, read once on a window fixed in advance.

``docs/proposals/FORWARD_PANEL_V0.1.md`` (Thomas 2026-11-12, P1 – P4). The entry rule sends forward only what cleared
the past, so a definition that missed it never gets out-of-sample evidence, and nobody knows how much the past
measurement says about what follows. This reads all sixteen closed definitions once, with nothing chosen.

- **As registered.** Each member is its owner tool's candidate: sign, window, weights and the engine's costs
  unchanged. :data:`REGISTERED` is the fingerprint of those definitions on the day this was registered; a member
  that changes afterwards stops the run. The feature functions, the engine and the cost constants are not in that
  fingerprint; :data:`REGISTERED_SOURCES` records their files, and the report says whether they changed.
- **Not in the panel:** anything with an official read still ahead, and its relatives — M03 / M04 and their two
  inputs (M01, M02), the momentum basket and the trend book beside it (T01, T02), the carry screens. Computing those
  here would show a registered read early.
- **One read, after the window.** :data:`WINDOW` is 2026-12-01 – 2027-11-30. ``run`` refuses before
  :data:`READ_FROM` and until the klines reach the window's last day. Nothing computes these returns earlier; there
  is no daily record.
- **Three readings, fixed now, and no judgement.** Each member's annual Sharpe and return on total position; the
  equal-weight pool of all sixteen (a member without a basket on a day counts as zero); and the rank correlation
  between the development Sharpe (:data:`DEVELOPMENT_SHARPE`, from the owner documents) and the forward one. The
  report carries no pass, keep or verdict: it is a record.
- **Counts before returns.** ``run`` counts what each member can rank first; an unexplained unranked member in the
  tools that name causes (depth, spot share) stops it.

``fetch`` runs on the host and asks the owner tools' fetchers for the window's inputs; ``counts`` prints dates and
counts only; ``run`` is sandbox-only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
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
from scripts.research import depth_basket_search as ds  # noqa: E402
from scripts.research import flow_basket_search as fl  # noqa: E402
from scripts.research import metrics_basket_search as ms  # noqa: E402
from scripts.research import spot_share_search as sp  # noqa: E402

SPEC = "FORWARD_PANEL_V0.1"
WINDOW = (date(2026, 12, 1), date(2027, 11, 30))
READ_FROM = date(2027, 12, 8)             # a week after the window, for the archive to publish its last days
NOT_YET = "NOT_YET"
# name -> (the owner tool's registry, its feature list, the inputs it reads beyond bars and funding)
SOURCES: dict[str, tuple[Any, Any, str]] = {
    **{f"A{i:02d}": (bs, None, "bars") for i in range(1, 11)},
    "F01": (fl, ms.FEATURES, "metrics"), "F02": (fl, ms.FEATURES, "metrics"), "F03": (fl, ms.FEATURES, "metrics"),
    "D01": (ds, ds.FEATURES, "depth"), "D02": (ds, ds.FEATURES, "depth"),
    "S01": (sp, sp.FEATURES, "spot"),
}
# The annual Sharpe each member recorded on its development span (BASKET_SEARCH_PROTOCOL, FLOW_AND_TREND_SEARCH,
# DEPTH_BASKET_SEARCH, SPOT_SHARE_SEARCH). Read from those documents on 2026-11-12; never recomputed.
DEVELOPMENT_SHARPE: dict[str, float] = {
    "A01": 0.517, "A02": 0.229, "A03": 0.033, "A04": 0.702, "A05": 0.559, "A06": 0.716, "A07": 0.092, "A08": 0.107,
    "A09": 0.339, "A10": 0.178, "F01": -0.769, "F02": 0.782, "F03": 0.575, "D01": -1.652, "D02": 0.838, "S01": 0.455,
}
REGISTERED = "44c2622dbe2fcde4"            # ``definitions_fingerprint()`` on 2026-11-12, the day of registration
# The definitions name features; what those features compute lives in these files, with the engine and its costs.
# Their contents on the day of registration are recorded in the proposal (§7). A change does not stop the run —
# another change may touch them for its own reasons — but the report says so, and the read then names what changed.
SOURCE_FILES = (
    "scripts/research/cross_section_basket.py", "scripts/research/basket_search.py",
    "scripts/research/flow_basket_search.py", "scripts/research/metrics_basket_search.py",
    "scripts/research/depth_basket_search.py", "scripts/research/spot_share_search.py",
    "runtime/mvp_runtime/crypto/cost.py",
)
REGISTERED_SOURCES = "30def8e887d2fa0e"


def members() -> list[tuple[str, bs.Candidate, Any]]:
    return [(name, source.CANDIDATES[name], features) for name, (source, features, _) in SOURCES.items()]


def definitions_fingerprint() -> str:
    """What the sixteen definitions are, without their prose: features, weights, lookback, side, sizing."""
    body = {}
    for name, candidate, _ in members():
        entry = candidate.as_dict()
        entry.pop("why", None)
        body[name] = entry
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]


def sources_fingerprint(root: Path = ROOT) -> str:
    """The contents of :data:`SOURCE_FILES`, line endings normalised so the value is the same on every platform."""
    digest = hashlib.sha256()
    for name in SOURCE_FILES:
        digest.update(name.encode() + b"\0" + (root / name).read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return digest.hexdigest()[:16]


# --- reading -----------------------------------------------------------------------------------------------------

def _monthly(market: xsb.Market) -> dict[date, list[str]]:
    start, end = WINDOW
    monthly = xsb.universes(market, top_n=bs.TOP_N, start=date(start.year, start.month, 1) - timedelta(days=1), end=end)
    return {date(m.year, m.month, 1): v for m, v in monthly.items()}


def spearman(pairs: list[tuple[float, float]]) -> float | None:
    """Rank correlation, average ranks for ties; None under three pairs or with no spread on a side."""
    if len(pairs) < 3:
        return None

    def ranks(values: list[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        out, i = [0.0] * len(values), 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            for k in range(i, j + 1):
                out[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return out

    a, b = ranks([p[0] for p in pairs]), ranks([p[1] for p in pairs])
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va, vb = sum((x - ma) ** 2 for x in a), sum((y - mb) ** 2 for y in b)
    if va == 0 or vb == 0:
        return None
    return round(sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (va * vb) ** 0.5, 3)


def read(market: xsb.Market, *, simulate: Callable[..., Any] = xsb.simulate) -> dict[str, Any]:
    """The three readings, on a market already cut at the window's end with every member's inputs attached."""
    start, end = WINDOW
    monthly = _monthly(market)
    rows, daily = {}, {}
    for name, candidate, features in members():
        sim = simulate(market, bs.CADENCE, monthly, start=start, end=end, weigher=bs.weigher(candidate, features))
        sharpe, values = bs.annual_sharpe(sim["daily"], start, end)
        sum_pct = round(sum(values) * 100, 2)
        rows[name] = {"annual_sharpe": sharpe, "sum_pct": sum_pct, "months": len(values),
                      "total_position_annual_pct": bs.total_position_annual_pct(sum_pct, len(values)),
                      "development_annual_sharpe": DEVELOPMENT_SHARPE[name]}
        daily[name] = {day: row["net"] for day, row in sim["daily"].items()}
    days = sorted({day for series in daily.values() for day in series})
    pool = {day: {"net": sum(series.get(day, 0.0) for series in daily.values()) / len(daily)} for day in days}
    pool_sharpe, pool_values = bs.annual_sharpe(pool, start, end)
    pool_sum = round(sum(pool_values) * 100, 2)
    paired = [(DEVELOPMENT_SHARPE[n], r["annual_sharpe"]) for n, r in rows.items() if r["annual_sharpe"] is not None]
    return {"members": rows,
            "pool": {"name": "POOL-16", "rule": "equal weight of all sixteen, as registered; no basket counts as zero",
                     "annual_sharpe": pool_sharpe, "sum_pct": pool_sum, "months": len(pool_values),
                     "total_position_annual_pct": bs.total_position_annual_pct(pool_sum, len(pool_values))},
            "development_vs_forward": {"rank_correlation": spearman(paired), "members_compared": len(paired)},
            "judgement": None, "display_only": True}


def counts(market: xsb.Market, *, absent: set[str], served: Mapping[str, list[str]]) -> dict[str, Any]:
    """What each member can rank at each decision. Dates and counts only; ``unexplained_total`` gates the run."""
    out, unexplained = {}, 0
    for name, candidate, features in members():
        kind = SOURCES[name][2]
        if kind == "depth":
            body = ds.ranked_counts(market, candidate, WINDOW, absent)
        elif kind == "spot":
            body = sp.ranked_counts(market, candidate, WINDOW, served)
        else:
            monthly = _monthly(market)
            rows = []
            for decision in fl._decisions(WINDOW):
                picked = monthly.get(date(decision.year, decision.month, 1), ())
                ranked = len(bs.scores(market, picked, decision, candidate, features))
                rows.append((len(picked), ranked, xsb.side_size(ranked)))
            body = {"decisions": len(rows), "weeks_without_a_basket": sum(1 for _, _, side in rows if side == 0),
                    "fewest_ranked": min((r for _, r, _ in rows), default=0)}
        unexplained += int(body.get("unexplained_count", 0))
        out[name] = {k: body[k] for k in ("decisions", "weeks_without_a_basket", "fewest_ranked", "unranked_by_cause",
                                           "unexplained_count") if k in body}
    return {"window": [d.isoformat() for d in WINDOW], "members": out, "unexplained_total": unexplained}


# --- the host side ------------------------------------------------------------------------------------------------

def hold(cache: Path) -> tuple[xsb.Market, set[str], dict[str, list[str]]] | None:
    """The market cut at the window's end with metrics, depth and spot attached, or None when a piece is missing."""
    archive = cache / "archive"
    snapshot = xsb.read_contract_types(archive)
    try:
        listing = set(json.loads((cache / sp.SPOT_DIR / ba.SYMBOLS_FILENAME).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        listing = set()
    served = sp.read_served(cache)
    if snapshot is None or not listing or served is None:
        return None
    market = xsb.load_market(archive, funding=True)
    market.contract_types = snapshot[0]
    end = WINDOW[1]
    held = ds.cut(market, ds.load_daily(cache), end, ms.load_daily(cache))
    held = sp.attach(held, xsb.load_market(cache / sp.SPOT_DIR, funding=False), listing, end, sp.months_on_disk(cache))
    return held, ds._absent(cache), served


def reaches_window_end(market: xsb.Market) -> bool:
    bars = market.bars.get(bs.REFERENCE) or {}
    return bool(bars) and max(bars) >= WINDOW[1]


def fetch(cache: Path, *, fetcher: Callable[..., Any] = ba.fetch, fetch_monthly: Callable[..., Any] = ba.fetch_monthly,
          depth_fetch: Callable[..., int] = ds.fetch, spot_fetch: Callable[..., int] = sp.fetch,
          build_metrics: Callable[..., Any] = ms.build, build_depth: Callable[..., Any] = ds.build,
          quiet: Callable[[], bool] | None = None, say: Callable[[str], None] = print) -> int:
    """Ask the owner tools' fetchers for the window's inputs. Safe to rerun. Rebuilds the derived daily values
    from the files on disk, so the values kept from the closed searches are overwritten (copies are archived)."""
    from scripts.research import funding_basis_refresh as fbr

    quiet = quiet or (lambda: fbr.in_quiet(datetime.now(timezone.utc)))
    archive = cache / "archive"
    snapshot = xsb.read_contract_types(archive)
    try:
        listing = set(json.loads((cache / sp.SPOT_DIR / ba.SYMBOLS_FILENAME).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        listing = set()
    if snapshot is None or not listing:
        say(f"BLOCKED: no contract types under {archive} or no spot symbol list")
        return EXIT_BLOCKED
    market = xsb.load_market(archive, funding=False)
    market.contract_types = snapshot[0]
    if not reaches_window_end(market):
        say(f"{NOT_YET}: the perp klines do not reach {WINDOW[1]} yet (the monthly refresh brings them)")
        return EXIT_BLOCKED
    monthly = {m: v for m, v in _monthly(bs.cut(market, WINDOW[1])).items() if WINDOW[0] <= m <= WINDOW[1]}
    picked = sorted({s for v in monthly.values() for s in v})
    if quiet():
        say("HALTED: inside the quiet window 07:30–09:00 UTC; rerun the same command after 09:00 UTC")
        return EXIT_BLOCKED
    fetch_monthly(archive, kind=xsb.FUNDING_KIND, symbols=picked, start=WINDOW[0], end=WINDOW[1])
    for symbol, first, last in ms.fetch_plan(monthly, lookback=ms.PLAN_LOOKBACK, end=WINDOW[1]):
        if quiet():
            say("HALTED: inside the quiet window 07:30–09:00 UTC; rerun the same command after 09:00 UTC")
            return EXIT_BLOCKED
        fetcher(cache / "archive-metrics", symbols=[symbol], start=first, end=last)
    depth_plan = ms.fetch_plan(monthly, lookback=ds.PLAN_LOOKBACK, end=WINDOW[1])
    if depth_fetch(cache, depth_plan) != EXIT_OK:
        return EXIT_BLOCKED
    pairs: dict[str, tuple[date, date]] = {}
    for month, picked_in_month in monthly.items():
        for perp in picked_in_month:
            link = sp.pair_of(perp, listing)
            if link is None:
                continue
            first = month
            for _ in range(sp.PLAN_MONTHS_BEFORE):
                first = (first - timedelta(days=1)).replace(day=1)
            lo, hi = pairs.get(link[0], (first, WINDOW[1]))
            pairs[link[0]] = (min(lo, first), hi)
    if spot_fetch(cache, sorted((pair, lo, hi) for pair, (lo, hi) in pairs.items())) != EXIT_OK:
        return EXIT_BLOCKED
    build_metrics(cache, say=lambda _line: None)
    build_depth(cache, say=lambda _line: None)
    say(f"DONE forward panel inputs: {len(picked)} member symbol(s), {len(depth_plan)} depth span(s), {len(pairs)} spot pair(s)")
    return EXIT_OK


# --- the command line -----------------------------------------------------------------------------------------------

def main(argv: list[str] | None = None, *, today: date | None = None) -> int:
    parser = argparse.ArgumentParser(prog="forward_panel", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("list", "fetch", "counts", "run"))
    parser.add_argument("--cache", type=Path, default=None, help="default: $RESEARCH_CACHE")
    args = parser.parse_args(argv)
    cache = args.cache or Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache")))
    today = today or datetime.now(timezone.utc).date()
    fingerprint = definitions_fingerprint()
    if args.command == "list":
        print(json.dumps({"spec": SPEC, "window": [d.isoformat() for d in WINDOW], "read_from": READ_FROM.isoformat(),
                          "registered": REGISTERED, "definitions_now": fingerprint,
                          "registered_sources": REGISTERED_SOURCES, "sources_now": sources_fingerprint(),
                          "members": {name: {**{k: v for k, v in candidate.as_dict().items() if k != "why"},
                                             "development_annual_sharpe": DEVELOPMENT_SHARPE[name]}
                                      for name, candidate, _ in members()}}, indent=1))
        return EXIT_OK
    if fingerprint != REGISTERED:
        print(f"refused: a member's definition changed since registration ({fingerprint} != {REGISTERED})", file=sys.stderr)
        return EXIT_USAGE
    if args.command == "run" and not xsb._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    if args.command == "fetch" and xsb._in_sandbox():
        print("refused: fetch runs on the host, not in the sandbox", file=sys.stderr)
        return EXIT_USAGE
    if today < READ_FROM:
        print(f"{NOT_YET}: the window {WINDOW[0]}..{WINDOW[1]} is read from {READ_FROM}; nothing is computed before it",
              file=sys.stderr)
        return EXIT_BLOCKED
    if args.command == "fetch":
        return fetch(cache)
    held = hold(cache)
    if held is None:
        print("BLOCKED: contract types, the spot symbol list or the served-months file is missing (run `fetch`)", file=sys.stderr)
        return EXIT_BLOCKED
    market, absent, served = held
    if not reaches_window_end(market):
        print(f"{NOT_YET}: the perp klines do not reach {WINDOW[1]} yet", file=sys.stderr)
        return EXIT_BLOCKED
    counted = counts(market, absent=absent, served=served)
    if args.command == "counts":
        print(json.dumps(counted, indent=1))
        return EXIT_OK
    if counted["unexplained_total"]:
        print(f"BLOCKED: {counted['unexplained_total']} unranked member-decision(s) have no named cause; run `counts`",
              file=sys.stderr)
        return EXIT_BLOCKED
    report = {"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC, "registered": REGISTERED,
              "sources_changed_since_registration": sources_fingerprint() != REGISTERED_SOURCES,
              "window": [d.isoformat() for d in WINDOW], "counts": counted, **read(market)}
    print(json.dumps(report, indent=1, sort_keys=True, ensure_ascii=False))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
