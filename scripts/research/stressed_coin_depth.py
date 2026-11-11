"""Stressed-coin depth: how much sits within one percent of the mid on days a coin's funding settles many times.

``docs/proposals/STRESSED_COIN_COST_V0.1.md`` (Thomas 2026-11-11: "기본값대로"). The engine charges every coin and day
the same slippage (``cost.DEFAULT_SLIPPAGE_BPS``). M04's locked-span sum came from coins whose funding settled nine or
more times a day, and that assumption was never looked at there. This measures depth and the implied impact of an
order of a stated size; it prints no return, no Sharpe and no verdict, and nothing here is ever applied to a
recorded sum: a cost-adjusted M04 would be a second judgement of a spent span.

- **Member-days, not legs.** The selection is by universe membership and settlement count over
  :data:`SPAN` (``basket_search.CONFIRM``): every member-day with at least :data:`STRESSED_SETTLEMENTS` funding
  settlements, and as many member-days with exactly :data:`CONTROL_SETTLEMENTS`, drawn by a seeded order. M04's own
  positions are not read; the diagnostic's door on that span is closed.
- **Source:** the public archive's daily ``bookDepth`` files: about every 30 seconds, the cumulative quantity and
  notional within ±1%..±5% of the mid. The narrowest band is one percent, so a 3 bps spread cannot be seen here.
- **Per member-day:** the median and the tenth percentile, over the day's snapshots, of the notional within one
  percent on the thinner side (the smaller of bid and ask at each snapshot).
- **Impact [assumption]:** with the book spread evenly across that one percent, an order of size ``Q`` against depth
  ``D`` fills on average ``(Q / D) × 50`` bps from the mid; ``Q > D`` is reported as over 100 bps.
- **Commands:** ``plan`` (host: the member-days), ``fetch`` (host: the files, through ``binance_archive.fetch``),
  ``measure`` (sandbox only).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from scripts import binance_archive as ba  # noqa: E402
from scripts.research import basket_search as bs  # noqa: E402
from scripts.research import cross_section_basket as xsb  # noqa: E402

SPEC = "STRESSED_COIN_COST_V0.1"
SPAN = bs.CONFIRM                         # 2025-01-01..2026-09-30: membership and settlement counts only
STRESSED_SETTLEMENTS = 9                  # the diagnostic's "nine or more a day" class
CONTROL_SETTLEMENTS = 3                   # the ordinary eight-hour schedule
ORDER_SIZES_USD = (1_000, 10_000, 50_000)  # per leg (C3)
BAND_PCT = 1.0                            # the narrowest band the files carry
HALF_BAND_BPS = BAND_PCT * 100 / 2        # an evenly spread band fills, on average, half way in
OVER_BPS = BAND_PCT * 100                 # an order larger than the band's depth: reported as over this
MIN_SNAPSHOTS = 1000                      # of about 2,700 a day; fewer is a partial day and is not measured
STRESSED, CONTROL = "stressed", "control"
DEPTH_DIR = "archive-depth"


def member_days(market: xsb.Market, span: tuple[date, date] = SPAN) -> dict[str, list[tuple[str, date]]]:
    """``{class: [(symbol, day)]}``: the stressed member-days, and a seeded control sample of the same size."""
    start, end = span
    monthly = xsb.universes(market, top_n=bs.TOP_N, start=date(start.year, start.month, 1) - timedelta(days=1), end=end)
    stressed: list[tuple[str, date]] = []
    ordinary: list[tuple[str, date]] = []
    counted: dict[str, Counter] = {}
    for first, members in monthly.items():
        for symbol in members:
            if symbol not in counted:
                counted[symbol] = Counter(xsb._utc_day(t) for t, _ in market.funding.get(symbol, ()))
            for day, n in counted[symbol].items():
                if not (start <= day <= end and day.year == first.year and day.month == first.month):
                    continue
                if n >= STRESSED_SETTLEMENTS:
                    stressed.append((symbol, day))
                elif n == CONTROL_SETTLEMENTS:
                    ordinary.append((symbol, day))
    order = lambda pair: hashlib.sha256(f"{SPEC}|control|{pair[0]}|{pair[1]}".encode()).hexdigest()  # noqa: E731
    control = sorted(sorted(ordinary, key=order)[:len(stressed)])
    return {STRESSED: sorted(stressed), CONTROL: control}


def spans(pairs: Iterable[tuple[str, date]]) -> list[tuple[str, date, date]]:
    """Runs of consecutive days per symbol, so one fetch call covers a run."""
    out: list[tuple[str, date, date]] = []
    for symbol, day in sorted(set(pairs)):
        if out and out[-1][0] == symbol and day - out[-1][2] == timedelta(days=1):
            out[-1] = (symbol, out[-1][1], day)
        else:
            out.append((symbol, day, day))
    return out


def depth_path(cache: Path, symbol: str, day: date) -> Path:
    return ba.local_path(cache / DEPTH_DIR, ba.BOOK_DEPTH, symbol, ba.daily_name(symbol, ba.BOOK_DEPTH, day))


def thin_side(rows: Iterable[Mapping[str, str]]) -> list[float]:
    """Per snapshot, the smaller of the bid-side and ask-side notional within :data:`BAND_PCT` of the mid."""
    sides: dict[str, dict[float, float]] = {}
    for row in rows:
        try:
            pct, notional = float(row["percentage"]), float(row["notional"])
        except (KeyError, TypeError, ValueError):
            continue
        if abs(pct) == BAND_PCT and notional > 0:
            sides.setdefault(row["timestamp"], {})[pct] = notional
    return [min(both.values()) for both in sides.values() if len(both) == 2]


def impact_bps(size: float, depth: float) -> float:
    """Average fill distance from the mid, in bps, for ``size`` against ``depth`` spread evenly over the band;
    :data:`OVER_BPS` when the order is larger than the band holds."""
    return OVER_BPS if size > depth else size / depth * HALF_BAND_BPS


def _quantile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(q * len(ordered))))]


def day_depth(rows: Iterable[Mapping[str, str]]) -> dict[str, float] | None:
    """``{"median", "p10"}`` of the thin-side notional over the day's snapshots; None for a partial day."""
    values = thin_side(rows)
    if len(values) < MIN_SNAPSHOTS:
        return None
    return {"median": statistics.median(values), "p10": _quantile(values, 0.10)}


def summarise(depths: Sequence[Mapping[str, float]]) -> dict[str, Any]:
    """One class: the spread of the member-days' depths and of the impact at each order size."""
    if not depths:
        return {"member_days_measured": 0}
    out: dict[str, Any] = {"member_days_measured": len(depths)}
    for key in ("median", "p10"):
        values = [d[key] for d in depths]
        out[f"depth_usd_at_day_{key}"] = {"median": round(statistics.median(values)), "p10": round(_quantile(values, 0.10)),
                                         "p01": round(_quantile(values, 0.01))}
        impact = {}
        for size in ORDER_SIZES_USD:
            bps = [impact_bps(size, v) for v in values]
            impact[str(size)] = {"median_bps": round(statistics.median(bps), 2), "p90_bps": round(_quantile(bps, 0.90), 2),
                                 "p99_bps": round(_quantile(bps, 0.99), 2),
                                 "share_over_band": round(sum(1 for b in bps if b >= OVER_BPS) / len(bps), 4)}
        out[f"impact_at_day_{key}"] = impact
    return out


def measure(chosen: Mapping[str, Sequence[tuple[str, date]]], read: Callable[[str, date], Iterable[Mapping[str, str]] | None],
            ) -> dict[str, Any]:
    """The report body. ``read`` returns a member-day's rows, or None when its file is not on disk."""
    classes: dict[str, Any] = {}
    for label, pairs in chosen.items():
        depths, no_file, partial = [], 0, 0
        for symbol, day in pairs:
            rows = read(symbol, day)
            if rows is None:
                no_file += 1
                continue
            depth = day_depth(rows)
            if depth is None:
                partial += 1
            else:
                depths.append(depth)
        classes[label] = {"member_days_planned": len(pairs), "no_file": no_file, "partial_day": partial,
                          "symbols": len({s for s, _ in pairs}), **summarise(depths)}
    return {"spec": SPEC, "span": [d.isoformat() for d in SPAN], "display_only": True,
            "band_pct": BAND_PCT, "order_sizes_usd": list(ORDER_SIZES_USD),
            "engine_slippage_bps": xsb.cost.DEFAULT_SLIPPAGE_BPS, "classes": classes}


def _read_zip(path: Path) -> list[dict[str, str]] | None:
    import csv
    import io
    import zipfile

    if not path.exists():
        return None
    with zipfile.ZipFile(path) as archive:
        with archive.open(archive.namelist()[0]) as handle:
            return list(csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8")))


def _load(cache: Path) -> xsb.Market | None:
    archive = cache / "archive"
    snapshot = xsb.read_contract_types(archive)
    if snapshot is None:
        print(f"BLOCKED: no readable {xsb.CONTRACT_TYPES_FILENAME} under {archive}", file=sys.stderr)
        return None
    market = xsb.load_market(archive)
    market.contract_types = snapshot[0]
    return bs.cut(market, SPAN[1])


def fetch(cache: Path, chosen: Mapping[str, Sequence[tuple[str, date]]], *, fetcher: Callable[..., Any] = ba.fetch,
          quiet: Callable[[], bool] | None = None, say: Callable[[str], None] = print) -> int:
    from scripts.research import funding_basis_refresh as fbr

    quiet = quiet or (lambda: fbr.in_quiet(datetime.now(timezone.utc)))
    total = {"kept": 0, "present": 0, "missing": 0}
    plan = spans(pair for pairs in chosen.values() for pair in pairs)
    for symbol, first, last in plan:
        if quiet():
            say("HALTED: inside the quiet window 07:30–09:00 UTC; rerun the same command after 09:00 UTC")
            return EXIT_BLOCKED
        tally = fetcher(cache / DEPTH_DIR, symbols=[symbol], start=first, end=last, kind=ba.BOOK_DEPTH)[symbol]
        for key in total:
            total[key] += tally[key]
    say(f"bookDepth: {len(plan)} span(s); kept {total['kept']}, present {total['present']}, not served {total['missing']}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="stressed_coin_depth", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("plan", "fetch", "measure"))
    parser.add_argument("--cache", type=Path, default=None, help="default: $RESEARCH_CACHE")
    args = parser.parse_args(argv)
    cache = args.cache or Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache")))
    if args.command == "measure" and not xsb._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    if args.command == "fetch" and xsb._in_sandbox():
        print("refused: fetch runs on the host, not in the sandbox", file=sys.stderr)
        return EXIT_USAGE
    market = _load(cache)
    if market is None:
        return EXIT_BLOCKED
    chosen = member_days(market)
    if args.command == "plan":
        print(json.dumps({label: {"member_days": len(pairs), "symbols": len({s for s, _ in pairs}),
                                  "first": str(min(d for _, d in pairs)) if pairs else None,
                                  "last": str(max(d for _, d in pairs)) if pairs else None}
                          for label, pairs in chosen.items()}, indent=1))
        return EXIT_OK
    if args.command == "fetch":
        return fetch(cache, chosen)
    report = {"measured_at": timeutil.utc_now_iso(), **xsb.provenance(),
              **measure(chosen, lambda symbol, day: _read_zip(depth_path(cache, symbol, day)))}
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
