"""Metrics basket search: the basket search's protocol on exchange metrics instead of price characteristics.

``docs/proposals/METRICS_BASKET_SEARCH_V0.1.md`` (Thomas 2026-11-10: "진행"). The engine, the rule and the locked
span are ``basket_search``'s; only the feature list and the development spans differ. ``explore`` and ``confirm`` run
in the research sandbox and write nothing; ``build`` runs on the host and writes only the derived daily file.

- **Source:** the archive's daily ``metrics`` files (5-minute rows), already in the research cache.
- **Daily value:** the mean of a UTC day's rows, per column, when at least :data:`MIN_ROWS` of them carry a value.
  A long/short ratio ``r`` becomes the long share ``r / (1 + r)``. Open interest is in contracts, never in quote
  value, so no price change is ranked.
- **Features (:data:`FEATURES`):** open-interest growth, the long share of all accounts, the long share of the top
  traders' positions, their difference, and the taker buy share. A decision at a day's close reads that day and
  earlier days only.
- **Spans:** the top-trader and taker columns are mostly empty in 2022, so a candidate that reads one of them is
  developed on :data:`DEV_SHORT`; one that reads only open interest and the all-account share on :data:`DEV_LONG`.
  ``confirm`` reads ``basket_search.CONFIRM`` once, for :data:`FINAL`.
- **Budget:** :data:`BUDGET` attempts. The first candidate to clear ``basket_search.ENTRY_SHARPE`` on its
  development span is the final one.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from scripts import binance_archive  # noqa: E402
from scripts.research import basket_search as bs  # noqa: E402
from scripts.research import cross_section_basket as xsb  # noqa: E402

SPEC = "METRICS_BASKET_SEARCH_V0.1"
DEV_LONG = (date(2022, 1, 1), date(2024, 12, 31))
DEV_SHORT = (date(2023, 2, 1), date(2024, 12, 31))
CONFIRM = bs.CONFIRM
BUDGET = 5
MIN_ROWS = 144                           # half of a day's 288 five-minute rows
LONG_SPAN_FEATURES = frozenset({"oi_growth", "ls_level"})
FINAL: str | None = None
# daily column -> (archive column, whether it is a long/short ratio to turn into a long share)
COLUMNS = {
    "oi": ("sum_open_interest", False),
    "ls": ("count_long_short_ratio", True),
    "top": ("sum_toptrader_long_short_ratio", True),
    "taker": ("sum_taker_long_short_vol_ratio", True),
}
Daily = dict[str, dict[date, dict[str, float | None]]]


def daily_values(rows: Iterable[Mapping[str, str]], day: date) -> dict[str, float | None]:
    """One UTC day's value per column from its 5-minute rows; None where fewer than :data:`MIN_ROWS` have one."""
    seen: dict[str, list[float]] = {name: [] for name in COLUMNS}
    for row in rows:
        if str(row.get("create_time"))[:10] != day.isoformat():
            continue                                   # a row stamped another day is not this day's
        for name, (column, ratio) in COLUMNS.items():
            try:
                value = float(row.get(column) or "")
            except ValueError:
                continue
            if math.isfinite(value) and value > 0:
                seen[name].append(value / (1 + value) if ratio else value)
    return {name: (statistics.fmean(v) if len(v) >= MIN_ROWS else None) for name, v in seen.items()}


def derived_path(cache: Path, symbol: str) -> Path:
    return cache / "derived" / "metrics-daily" / f"{symbol}.jsonl"


def build(cache: Path, *, say: Any = print) -> int:
    """Write each symbol's daily values from the archive zips. Host only; values, not returns."""
    source = cache / "archive-metrics" / "metrics"
    written = 0
    for folder in sorted(p for p in source.iterdir() if p.is_dir()):
        lines = []
        for path in sorted(folder.glob("*.zip")):
            try:
                day = date.fromisoformat(path.stem[-10:])
            except ValueError:
                continue
            values = daily_values(binance_archive.read_metrics_zip(path), day)
            lines.append(json.dumps([day.isoformat(), *(values[name] for name in COLUMNS)]))
        out = derived_path(cache, folder.name)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
        written += len(lines)
    say(f"built {written} symbol-day(s) under {derived_path(cache, 'X').parent}")
    return written


def load_daily(cache: Path) -> Daily:
    out: Daily = {}
    folder = derived_path(cache, "X").parent
    for path in sorted(folder.glob("*.jsonl")) if folder.exists() else []:
        days = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                days[date.fromisoformat(row[0])] = dict(zip(COLUMNS, row[1:]))
        out[path.stem] = days
    return out


def cut(market: xsb.Market, daily: Daily, end: date) -> xsb.Market:
    """The market and the metrics as they stood at ``end``'s close, the metrics attached for the features."""
    held = bs.cut(market, end)
    held.metrics = {s: {d: v for d, v in days.items() if d <= end} for s, days in daily.items()}
    return held


def _window(market: xsb.Market, symbol: str, day: date, lookback: int, column: str) -> list[float]:
    days = getattr(market, "metrics", {}).get(symbol) or {}
    values = [(days.get(day - timedelta(days=k)) or {}).get(column) for k in range(lookback)]
    kept = [v for v in values if v is not None]
    return kept if len(kept) >= math.ceil(lookback * bs.MIN_SHARE) else []


def _oi_growth(market, symbol, day, lookback):
    days = getattr(market, "metrics", {}).get(symbol) or {}
    now, then = (days.get(day) or {}).get("oi"), (days.get(day - timedelta(days=lookback)) or {}).get("oi")
    return None if now is None or then is None else now / then - 1


def _level(column: str):
    def feature(market, symbol, day, lookback):
        values = _window(market, symbol, day, lookback, column)
        return statistics.fmean(values) if values else None
    return feature


def _divergence(market, symbol, day, lookback):
    days = getattr(market, "metrics", {}).get(symbol) or {}
    gaps = []
    for k in range(lookback):
        row = days.get(day - timedelta(days=k)) or {}
        if row.get("top") is not None and row.get("ls") is not None:
            gaps.append(row["top"] - row["ls"])
    return statistics.fmean(gaps) if len(gaps) >= math.ceil(lookback * bs.MIN_SHARE) else None


FEATURES: dict[str, bs.Feature] = {
    "oi_growth": _oi_growth, "ls_level": _level("ls"), "top_level": _level("top"),
    "divergence": _divergence, "taker_level": _level("taker"),
}

# The attempts, in the order they were run (docs/proposals/METRICS_BASKET_SEARCH_V0.1.md, "탐색 기록").
CANDIDATES: dict[str, bs.Candidate] = {
    "M01": bs.Candidate(why="쏠림 역행: 전체 계정의 롱 비중이 낮은 코인을 사고 높은 코인을 판다. 개인 계정이 몰린 쪽이 진다는 가설.", features=(("ls_level", 1.0),), long_low=True),
}


def dev_span(candidate: bs.Candidate) -> tuple[date, date]:
    names = {name for name, _ in candidate.features}
    return DEV_LONG if names <= LONG_SPAN_FEATURES else DEV_SHORT


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="metrics_basket_search", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("list", "build", "explore", "confirm"))
    parser.add_argument("--candidate", default=None)
    parser.add_argument("--cache", type=Path, default=None, help="default: $RESEARCH_CACHE")
    args = parser.parse_args(argv)
    cache = args.cache or Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache")))
    if args.command == "list":
        print(json.dumps({"budget": BUDGET, "final": FINAL, "candidates": {
            k: {**c.as_dict(), "development_span": [d.isoformat() for d in dev_span(c)]}
            for k, c in CANDIDATES.items()}}, indent=1, ensure_ascii=False))
        return EXIT_OK
    if args.command == "build":
        build(cache)
        return EXIT_OK
    if not xsb._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    if args.candidate not in CANDIDATES:
        print(f"refused: no candidate {args.candidate!r}; registered: {', '.join(CANDIDATES) or 'none'}", file=sys.stderr)
        return EXIT_USAGE
    if len(CANDIDATES) > BUDGET:
        print(f"refused: {len(CANDIDATES)} candidates are over the budget of {BUDGET}", file=sys.stderr)
        return EXIT_USAGE
    if args.command == "confirm" and args.candidate != FINAL:
        print(f"refused: the locked span is read once, for the final candidate ({FINAL}), not for "
              f"{args.candidate}", file=sys.stderr)
        return EXIT_USAGE
    candidate = CANDIDATES[args.candidate]
    if not {name for name, _ in candidate.features} <= set(FEATURES):
        print("refused: the candidate names a feature outside this protocol's list", file=sys.stderr)
        return EXIT_USAGE
    archive = cache / "archive"
    snapshot = xsb.read_contract_types(archive)
    daily = load_daily(cache)
    if snapshot is None or not daily:
        print(f"BLOCKED: no contract types under {archive} or no derived metrics under {cache} "
              "(run `build` on the host first)", file=sys.stderr)
        return EXIT_BLOCKED
    span = dev_span(candidate) if args.command == "explore" else CONFIRM
    market = xsb.load_market(archive)
    market.contract_types = snapshot[0]
    market = cut(market, daily, span[1])               # before anything is computed
    report = {"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC, "command": args.command,
              "candidate": args.candidate, "definition": candidate.as_dict(), "entry_sharpe": bs.ENTRY_SHARPE,
              **bs.measure(market, candidate, span, FEATURES)}
    print(json.dumps(report, indent=1, sort_keys=True, ensure_ascii=False))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
