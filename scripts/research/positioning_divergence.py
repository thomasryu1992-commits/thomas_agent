"""Positioning divergence: do the library's two positioning families, at their base parameters, clear an annual
Sharpe of 1.0 on the years the venue's archive holds?

``docs/proposals/POSITIONING_DIVERGENCE_V0.1.md`` (pre-registered; Thomas 2026-11-10). It runs in the research
sandbox only, reads the research cache and the archive ``metrics`` files, and writes nothing. It mints nothing:
``positioning_store.MINTING_DECIDED`` stays False.

- **Strategy (one, ``P``):** ``positioning_divergence_long`` and ``positioning_divergence_short`` exactly as
  ``template_space`` has held them since before this measurement: 1h, base parameters, one spec per symbol and
  family. Nothing is drawn, mutated or selected.
- **Symbols and span:** :data:`SYMBOLS`, calendar windows :data:`WINDOWS` (2023-01-01 to 2026-09-30). The archive
  starts 2021-12 for five of the six, but its two top-trader ratios are empty for most of 2022 (coverage run
  ``20261110T101845Z-3531809``, counts only), so 2022 is left out before any return is read.
- **Readings:** the archive's 5-minute ``metrics`` rows. The hourly reading the runtime's collector labels ``T`` is
  the archive row created at ``T − 5 min`` (verified on the overlap with the runtime's own store, 2026-09). A ratio
  ``r`` (long / short) becomes the long share ``r / (1 + r)``. Only those rows are read.
- **When a reading is known:** as in live. ``features._positioning_columns`` shows a reading labelled ``T`` from
  ``T + 1h``. A bar whose newest reading is older than ``feed_assembly.POSITIONING_MAX_AGE_HOURS`` carries none,
  the live rule (decision 28), so a missing archive day cannot trade on a stale reading.
- **Replay:** the runtime's own (``pre_window_replay._snapshot``, ``backtest.build_replay_frame``, ``_replay``),
  with each window built as a collector at its end would have held it, and the runtime's cost model. A trade
  counts in the window it opens in; a position open at a window's end is dropped (``_replay`` records closed
  trades only).
- **Portfolio and entry rule:** a month's return is the sum of net R of the trades dated in it, over all symbols
  and both families; a month without a trade is 0. The annual Sharpe of the span's months (monthly Sharpe × √12)
  must be at least :data:`ENTRY_SHARPE`. Everything else is shown, never judged.
- **Coverage first:** ``load`` prints dates and counts only. A symbol missing more than
  :data:`MAX_MISSING_SHARE` of its hourly readings, or a window that cannot be built, is ``BLOCKED``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import sys
from bisect import bisect_right
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from runtime.mvp_runtime.crypto import feed_assembly, features, generator, market_data, template_space  # noqa: E402
from runtime.mvp_runtime.crypto.backtest import _replay, build_replay_frame, unsuppliable_features  # noqa: E402
from runtime.mvp_runtime.crypto.strategy import StrategySpec  # noqa: E402
from runtime.mvp_runtime.errors import ToolBlocked, ToolError  # noqa: E402
from runtime.mvp_runtime.paths import repo_root  # noqa: E402
from scripts import binance_archive  # noqa: E402
from scripts.research import lake  # noqa: E402
from scripts.research import pre_window_replay as pwr  # noqa: E402

SPEC = "POSITIONING_DIVERGENCE_V0.1"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT")
TIMEFRAME = "1h"
FAMILIES = ("positioning_divergence_long", "positioning_divergence_short")
WINDOWS: tuple[tuple[str, str, str], ...] = (
    ("2023", "2023-01-01T00:00:00Z", "2024-01-01T00:00:00Z"),
    ("2024", "2024-01-01T00:00:00Z", "2025-01-01T00:00:00Z"),
    ("2025", "2025-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    ("2026", "2026-01-01T00:00:00Z", "2026-10-01T00:00:00Z"),
)
ENTRY_SHARPE = 1.0
MAX_MISSING_SHARE = 0.05                 # of a symbol's hourly readings over the span
ARCHIVE_LAG = timedelta(minutes=5)       # the reading labelled T is the archive row created at T − 5 min
PERIOD = "1h"
WARMUP_DAYS = 20                         # readings before a window: the frame's 400 warmup bars, with room
# The collector's three series and the archive column each one is (long / short ratios).
SERIES_COLUMNS = {
    "top_account": "count_toptrader_long_short_ratio",
    "top_position": "sum_toptrader_long_short_ratio",
    "global_account": "count_long_short_ratio",
}
PAIRED = ("top_position", "global_account")     # the divergence needs both at one reading (features)


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _day(value: str) -> date:
    return date.fromisoformat(value[:10])


def metrics_path(metrics: Path, symbol: str, day: date) -> Path:
    return metrics / "metrics" / symbol / f"{symbol}-metrics-{day.isoformat()}.zip"


def readings(metrics: Path, symbol: str, *, start: date, end: date) -> list[dict[str, Any]]:
    """The hourly readings labelled inside ``[start, end)``, in the positioning store's row shape.

    One row per series and hour. A row is made only from the archive row created exactly five minutes before the
    hour; a missing file, a missing row or a ratio that is not a positive number makes no row."""
    lo = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    hi = datetime(end.year, end.month, end.day, tzinfo=timezone.utc)
    out: list[dict[str, Any]] = []
    day = start - timedelta(days=1)                     # the 00:00 reading comes from the previous day's 23:55 row
    while day < end:
        path = metrics_path(metrics, symbol, day)
        day += timedelta(days=1)
        if not path.exists():
            continue
        for row in binance_archive.read_metrics_zip(path):
            try:
                created = datetime.fromisoformat(str(row.get("create_time"))).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            label = created + ARCHIVE_LAG
            if label.minute or label.second or not lo <= label < hi:
                continue
            for series, column in SERIES_COLUMNS.items():
                try:
                    ratio = float(row.get(column) or "")
                except ValueError:
                    continue
                if not math.isfinite(ratio) or ratio <= 0:
                    continue
                out.append({"symbol": symbol, "series": series, "period": PERIOD, "timestamp": _iso(label),
                            "long_ratio": ratio / (1 + ratio), "short_ratio": 1 / (1 + ratio)})
    return out


def paired_labels(rows: Iterable[Mapping[str, Any]]) -> list[str]:
    """The hours at which both legs of the divergence have a reading, oldest first."""
    seen: dict[str, set[str]] = {name: set() for name in PAIRED}
    for row in rows:
        if row.get("series") in seen:
            seen[str(row["series"])].add(str(row["timestamp"]))
    return sorted(set.intersection(*seen.values()))


def blank_stale(frame: Any, labels: Sequence[str]) -> int:
    """Clear the positioning columns on every bar whose newest known reading is too old (the live rule).

    A reading labelled ``T`` is known from ``T + 1h``; its age at a bar is the bar's open minus ``T``. Returns
    the bars cleared."""
    known = [timeutil.parse_iso(label) + timedelta(hours=1) for label in labels]
    limit = timedelta(hours=feed_assembly.POSITIONING_MAX_AGE_HOURS)
    cleared = 0
    for i, candle in enumerate(frame.candles):
        opened = timeutil.parse_iso(str(candle.get("open_time")))
        k = bisect_right(known, opened)
        if k and opened - (known[k - 1] - timedelta(hours=1)) <= limit:
            continue
        row = frame.rows[i]
        if any(row.get(col) is not None for col in features.POSITIONING_COLUMNS):
            frame.rows[i] = {**row, **{col: None for col in features.POSITIONING_COLUMNS}}
            cleared += 1
    return cleared


def specs(symbol: str) -> list[StrategySpec]:
    """The two families at their base parameters for one symbol. Nothing is drawn."""
    by_family = {t.family: t for t in template_space.TEMPLATES}
    out = []
    for family in FAMILIES:
        template = by_family[family]
        if family not in template_space.POSITIONING_FAMILIES or template.timeframe != TIMEFRAME:
            raise ValueError(f"{family} is not a {TIMEFRAME} positioning family in the library")
        raw = generator.build_spec_dict(template, dict(template.base_params), strategy_id=f"P-{family}",
                                        generation_id=SPEC, symbol=symbol)
        out.append(StrategySpec.from_dict(raw))
    return out


def simulate(root: Path, metrics: Path, *, symbols: Sequence[str] = SYMBOLS,
             say: Any = lambda *_a, **_k: None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(trades, windows)``: every closed trade as ``{symbol, family, day, r}``, and one row per
    (symbol, window) saying whether it was replayed."""
    trades: list[dict[str, Any]] = []
    windows: list[dict[str, Any]] = []
    for symbol in symbols:
        for label, start, end in WINDOWS:
            row = {"symbol": symbol, "window": label}
            span = timeutil.parse_iso(end) - timeutil.parse_iso(start)
            bars = int(span / timedelta(hours=1)) + pwr.WARMUP_BARS
            say(f"  {symbol} {label}", file=sys.stderr, flush=True)
            try:
                snapshot = pwr._snapshot(symbol, TIMEFRAME, end=end, bars=bars, root=root)
            except (ToolError, ToolBlocked) as exc:
                windows.append({**row, "status": f"no_data:{exc.reason_code}"})
                continue
            rows = readings(metrics, symbol, start=_day(start) - timedelta(days=WARMUP_DAYS), end=_day(end))
            snapshot["positioning"] = rows                 # the archive's readings, one source for the whole span
            frame = build_replay_frame(snapshot)
            cleared = blank_stale(frame, paired_labels(rows))
            window = pwr._window(frame, start)
            if window is None:
                windows.append({**row, "status": "no_window"})
                continue
            counts = {}
            for spec in specs(symbol):
                if unsuppliable_features(spec, window.rows):
                    windows.append({**row, "status": "unsuppliable"})
                    break
                outcomes, *_ = _replay(spec, window.rows, window.candles, cost=window.cost, funding=window.funding)
                counts[spec.strategy_family] = len(outcomes)
                trades += [{"symbol": symbol, "family": spec.strategy_family,
                            "day": str(o["created_at_utc"])[:10], "r": float(o["result_R"])} for o in outcomes]
            else:
                windows.append({**row, "status": "replayed", "trades": counts, "stale_bars_cleared": cleared})
            del frame, window, snapshot
    return trades, windows


def span_months() -> list[str]:
    first, last = _day(WINDOWS[0][1]), _day(WINDOWS[-1][2]) - timedelta(days=1)
    out, year, month = [], first.year, first.month
    while (year, month) <= (last.year, last.month):
        out.append(f"{year:04d}-{month:02d}")
        year, month = year + (month == 12), month % 12 + 1
    return out


def _annual(monthly: Sequence[float]) -> float | None:
    if len(monthly) < 2:
        return None
    sd = statistics.stdev(monthly)
    return None if sd == 0 else round(statistics.fmean(monthly) / sd * math.sqrt(12), 3)


def measure(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The entry verdict over the span's months (a month without a trade is 0) and the shown-only splits."""
    months = span_months()
    trades = [t for t in trades if str(t["day"])[:7] in months]      # a trade dated outside the span is not counted

    def monthly(rows: Iterable[Mapping[str, Any]]) -> list[float]:
        total = dict.fromkeys(months, 0.0)
        for t in rows:
            total[str(t["day"])[:7]] += float(t["r"])
        return [total[m] for m in months]

    def block(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        values = monthly(rows)
        r = [float(t["r"]) for t in rows]
        return {"trades": len(r), "mean_r": round(statistics.fmean(r), 4) if r else None,
                "sum_r": round(sum(r), 3), "annual_sharpe": _annual(values),
                "months_with_trades": sum(1 for v in values if v != 0)}

    whole = block(trades)
    values = monthly(trades)
    years = sorted({m[:4] for m in months})
    return {
        "strategy": "P", "months": len(months), "first_month": months[0], "last_month": months[-1], **whole,
        "entry_pass": whole["annual_sharpe"] is not None and whole["annual_sharpe"] >= ENTRY_SHARPE,
        "negative_months": sum(1 for v in values if v < 0),
        "worst_month": {"month": months[values.index(min(values))], "sum_r": round(min(values), 3)},
        "years": {y: round(sum(v for m, v in zip(months, values) if m.startswith(y)), 3) for y in years},
        "by_family": {f: block([t for t in trades if t["family"] == f]) for f in FAMILIES},
        "by_symbol": {s: block([t for t in trades if t["symbol"] == s]) for s in sorted({t["symbol"] for t in trades})},
    }


def coverage(metrics: Path, symbols: Sequence[str] = SYMBOLS) -> dict[str, Any]:
    """What the archive holds for the span, as dates and counts: no ratio leaves this function."""
    start = _day(WINDOWS[0][1]) - timedelta(days=WARMUP_DAYS)
    end = _day(WINDOWS[-1][2])
    expected = (end - start).days * 24
    digest = hashlib.sha256()
    out: dict[str, Any] = {}
    for symbol in symbols:
        rows = readings(metrics, symbol, start=start, end=end)
        for row in rows:
            digest.update(f"{symbol}|{row['series']}|{row['timestamp']}|{row['long_ratio']!r}".encode())
        paired = paired_labels(rows)
        days = [start + timedelta(days=k) for k in range((end - start).days)]
        absent = [d.isoformat() for d in days if not metrics_path(metrics, symbol, d).exists()]
        out[symbol] = {
            "expected_hours": expected, "paired_hours": len(paired),
            "missing_share": round(1 - len(paired) / expected, 4),
            "first_reading": paired[0] if paired else None, "last_reading": paired[-1] if paired else None,
            "missing_days": len(absent), "missing_days_first": absent[:10],
            "by_series": {name: sum(1 for r in rows if r["series"] == name) for name in SERIES_COLUMNS},
        }
    return {"span": [start.isoformat(), end.isoformat()], "input_digest": digest.hexdigest()[:16], "symbols": out}


def short_symbols(cover: Mapping[str, Any]) -> list[str]:
    return sorted(s for s, c in cover["symbols"].items() if c["missing_share"] > MAX_MISSING_SHARE)


def _in_sandbox() -> bool:
    return getattr(market_data.select_market_data_collector, "__module__", "") == lake.__name__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="positioning_divergence", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("load", "run"),
                        help="load: coverage and the input digest only, no returns; run: the measurement")
    parser.add_argument("--metrics", type=Path, default=None, help="default: $RESEARCH_CACHE/archive-metrics")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not _in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    cache = Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache")))
    metrics = args.metrics or cache / "archive-metrics"
    cover = coverage(metrics)
    head = {"spec": SPEC, "symbols": list(SYMBOLS), "families": list(FAMILIES), "timeframe": TIMEFRAME,
            "windows": [w[0] for w in WINDOWS], "entry_sharpe": ENTRY_SHARPE, "coverage": cover}
    short = short_symbols(cover)
    if args.command == "load":
        print(json.dumps({**head, "short": short}, indent=1, sort_keys=True))
        return EXIT_BLOCKED if short else EXIT_OK
    if short:
        print(f"BLOCKED: more than {MAX_MISSING_SHARE:.0%} of the hourly readings are missing for "
              f"{','.join(short)}", file=sys.stderr)
        return EXIT_BLOCKED
    trades, windows = simulate(repo_root(), metrics, say=print)
    failed = [w for w in windows if w["status"] != "replayed"]
    if failed:
        print("BLOCKED: " + "; ".join(f"{w['symbol']} {w['window']} {w['status']}" for w in failed), file=sys.stderr)
        return EXIT_BLOCKED
    result = measure(trades)
    report = {"measured_at": timeutil.utc_now_iso(), **pwr.provenance(), **head, "replays": windows,
              "strategies": {"P": result}, "overall": "ENTRY_PASS" if result["entry_pass"] else "ENTRY_FAIL"}
    if args.json:
        print(json.dumps(report, indent=1, sort_keys=True))
        return EXIT_OK
    print(f"P: annual SR {result['annual_sharpe']} over {result['months']} months "
          f"({result['first_month']}..{result['last_month']}), {result['trades']} trades, mean {result['mean_r']}R "
          f"-> {report['overall']} | years {result['years']} | worst {result['worst_month']}")
    for family, row in result["by_family"].items():
        print(f"  {family}: {row['trades']} trades, mean {row['mean_r']}R, SR {row['annual_sharpe']}")
    print(f"input {cover['input_digest']}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
