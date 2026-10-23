"""Fill the research sandbox's cache with the venue's public series, inside an IP budget.

``docs/proposals/RESEARCH_SANDBOX_V0.1.md`` §4.2. Runs as the ``claude`` account with no key: candles,
funding and the mark / index / premium price series are public. They share the host's IP with the live
runtime, whose orders and account reads go to the same host, so the budget is what this module is:

- at most :data:`WEIGHT_PER_MINUTE` request weight in any 60 s, counted here;
- stop the run when the venue reports :data:`HEADER_STOP_WEIGHT` used in its own minute
  (``X-MBX-USED-WEIGHT-1M``, which counts the runtime's requests too);
- stop at the first 429 or 418 and never retry: a 418 is the venue banning the IP;
- never start, and stop, inside the factory window (:data:`FACTORY_WINDOW_UTC`).

Rows already cached are never asked for again. The first fill pages backward to the factory depth; later
fills page forward from the newest cached bar. Only closed bars are written, raw, one per line. 15m is
refused: its 96,000-bar frame does not fit the sandbox (§3).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime.crypto import market_data  # noqa: E402
from scripts.research import lake  # noqa: E402

BASE = "https://fapi.binance.com/fapi/v1/"
WEIGHT_PER_MINUTE = 600          # 25% of the venue's 2,400 per IP per minute
HEADER_STOP_WEIGHT = 1200        # 50%, counting everything else on this IP
FACTORY_WINDOW_UTC = (8, 9)      # [08:00, 09:00): the daily factory fires
PAGE = 1500                      # klines rows per call (the venue's cap)
DERIVATIVE_PAGE = market_data.DERIVATIVE_PAGE_LIMIT
FUNDING_PAGE = market_data.FUNDING_PAGE_LIMIT
ALLOWED_TIMEFRAMES = ("1h", "4h", "1d")
INTERVAL_MS = {tf: minutes * 60_000 for tf, minutes in market_data.TIMEFRAMES.items()}


class Stop(Exception):
    """The run must end now (rate limit, budget, window). Never retried."""


def kline_weight(limit: int) -> int:
    """The venue's weight for one klines-shaped call by ``limit``."""
    if limit < 100:
        return 1
    if limit < 500:
        return 2
    if limit <= 1000:
        return 5
    return 10


Fetch = Callable[[str], tuple[int, dict[str, str], str]]


def urllib_fetch(url: str) -> tuple[int, dict[str, str], str]:
    request = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, dict(response.headers), response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), ""


@dataclass
class Budget:
    """The IP budget. ``clock`` and ``sleep`` are injectable so the tests never wait or touch the network."""

    fetch: Fetch = urllib_fetch
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    per_minute: int = WEIGHT_PER_MINUTE
    header_stop: int = HEADER_STOP_WEIGHT
    spent: deque = field(default_factory=deque)
    requests: int = 0

    def _check_window(self) -> None:
        hour = time.gmtime(self.clock()).tm_hour
        if FACTORY_WINDOW_UTC[0] <= hour < FACTORY_WINDOW_UTC[1]:
            raise Stop("inside the factory window (08:00-09:00 UTC)")

    def get(self, path: str, params: dict[str, Any], *, weight: int) -> Any:
        self._check_window()
        now = self.clock()
        while self.spent and now - self.spent[0][0] >= 60:
            self.spent.popleft()
        while sum(w for _, w in self.spent) + weight > self.per_minute:
            self.sleep(max(0.5, 60 - (now - self.spent[0][0])))
            now = self.clock()
            while self.spent and now - self.spent[0][0] >= 60:
                self.spent.popleft()
        self.spent.append((now, weight))
        self.requests += 1
        status, headers, body = self.fetch(f"{BASE}{path}?{urllib.parse.urlencode(params)}")
        if status in (429, 418):
            raise Stop(f"venue answered {status}; stopping without retry")
        if status != 200:
            raise Stop(f"venue answered {status} for {path}")
        used = {k.lower(): v for k, v in headers.items()}.get("x-mbx-used-weight-1m")
        if used is not None and int(used) >= self.header_stop:
            raise Stop(f"the IP has used {used} weight this minute; stopping at {self.header_stop}")
        return json.loads(body)


def _append(path: Path, rows: Iterable[Any]) -> int:
    rows = list(rows)
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    return len(rows)


def _closed(rows: list[list[Any]], now_ms: int) -> list[list[Any]]:
    return [row for row in rows if isinstance(row, list) and len(row) > 6 and int(row[6]) < now_ms]


def fill_klines(budget: Budget, path: Path, *, endpoint: str, params: dict[str, Any], timeframe: str,
                depth: int, page: int = PAGE, since_ms: int | None = None) -> int:
    """Page a klines-shaped series into ``path``: backward to ``depth`` bars when empty, else forward.

    ``since_ms`` then extends the series BACKWARD from its oldest held bar until a bar at or before
    ``since_ms`` is held or the venue has nothing older (PRE_WINDOW_REPLAY_V0.1 §6). Same budget, same
    closed-bar rule; a held bar is never asked for again."""
    cached = lake._by_open_time(lake.read_rows(path))
    now_ms = int(budget.clock() * 1000)
    new: dict[int, list[Any]] = {}
    if cached:
        start = int(cached[-1][0]) + 1
        while True:
            rows = budget.get(endpoint, {**params, "limit": page, "startTime": start}, weight=kline_weight(page))
            rows = _closed(rows, now_ms)
            if not rows:
                break
            for row in rows:
                new[int(row[0])] = row
            start = int(rows[-1][0]) + 1
            if len(rows) < page - 1:
                break
    else:
        end: int | None = None
        while len(new) < depth:
            query = {**params, "limit": page, **({"endTime": end} if end is not None else {})}
            rows = _closed(budget.get(endpoint, query, weight=kline_weight(page)), now_ms)
            if not rows:
                break
            oldest = min(int(r[0]) for r in rows)
            for row in rows:
                new[int(row[0])] = row
            if end is not None and oldest >= end:
                break
            end = oldest - 1
    held = {int(r[0]) for r in cached}
    if since_ms is not None:
        known = held | set(new)
        end = (min(known) - 1) if known else None
        while end is not None and end >= since_ms:
            rows = _closed(budget.get(endpoint, {**params, "limit": page, "endTime": end},
                                      weight=kline_weight(page)), now_ms)
            rows = [r for r in rows if int(r[0]) <= end]
            if not rows:
                break
            for row in rows:
                new[int(row[0])] = row
            end = min(int(r[0]) for r in rows) - 1
    return _append(path, [new[k] for k in sorted(new) if k not in held])


def fill_funding(budget: Budget, path: Path, *, symbol: str,
                 records: int = market_data.DEFAULT_FUNDING_RECORDS, since_ms: int | None = None) -> int:
    held = {int(item["fundingTime"]) for item in lake.read_rows(path)
            if isinstance(item, dict) and "fundingTime" in item}
    new: dict[int, dict[str, Any]] = {}
    if held:
        start = max(held) + 1
        while True:
            rows = budget.get("fundingRate", {"symbol": symbol, "limit": FUNDING_PAGE, "startTime": start}, weight=1)
            rows = [r for r in rows if int(r["fundingTime"]) not in held]
            if not rows:
                break
            for row in rows:
                new[int(row["fundingTime"])] = row
            start = max(int(r["fundingTime"]) for r in rows) + 1
    else:
        end: int | None = None
        while len(new) < records:
            query = {"symbol": symbol, "limit": FUNDING_PAGE, **({"endTime": end} if end is not None else {})}
            rows = budget.get("fundingRate", query, weight=1)
            if not rows:
                break
            oldest = min(int(r["fundingTime"]) for r in rows)
            for row in rows:
                new[int(row["fundingTime"])] = row
            if end is not None and oldest >= end:
                break
            end = oldest - 1
    if since_ms is not None:
        known = held | set(new)
        end = (min(known) - 1) if known else None
        while end is not None and end >= since_ms:
            rows = budget.get("fundingRate", {"symbol": symbol, "limit": FUNDING_PAGE, "endTime": end}, weight=1)
            rows = [r for r in rows if int(r["fundingTime"]) <= end]
            if not rows:
                break
            for row in rows:
                new[int(row["fundingTime"])] = row
            end = min(int(r["fundingTime"]) for r in rows) - 1
    return _append(path, [new[k] for k in sorted(new) if k not in held])


def run(budget: Budget, cache: Path, *, symbols: Iterable[str], timeframes: Iterable[str],
        kinds: Iterable[str], say: Callable[[str], None] = print, since_ms: int | None = None) -> dict[str, int]:
    written: dict[str, int] = {}
    for symbol in symbols:
        for timeframe in timeframes:
            if timeframe not in ALLOWED_TIMEFRAMES:
                raise ValueError(f"{timeframe} is outside the sandbox ({', '.join(ALLOWED_TIMEFRAMES)})")
            depth = market_data.factory_candle_target(timeframe)
            key = f"klines {symbol} {timeframe}"
            written[key] = fill_klines(budget, lake.klines_path(cache, symbol, timeframe), endpoint="klines",
                                       params={"symbol": symbol, "interval": timeframe}, timeframe=timeframe,
                                       depth=depth, since_ms=since_ms)
            say(f"  {key}: +{written[key]}")
            for kind in kinds:
                param = market_data.DERIVATIVE_KLINE_SYMBOL_PARAMS[kind]
                key = f"{kind} {symbol} {timeframe}"
                written[key] = fill_klines(budget, lake.derivative_path(cache, kind, symbol, timeframe),
                                           endpoint=market_data.DERIVATIVE_KLINE_PATHS[kind],
                                           params={param: symbol, "interval": timeframe}, timeframe=timeframe,
                                           depth=depth, page=DERIVATIVE_PAGE, since_ms=since_ms)
                say(f"  {key}: +{written[key]}")
        key = f"funding {symbol}"
        written[key] = fill_funding(budget, lake.funding_path(cache, symbol), symbol=symbol, since_ms=since_ms)
        say(f"  {key}: +{written[key]}")
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fetch_public", description=__doc__.splitlines()[0])
    parser.add_argument("--cache", default=str(Path.home() / "research" / "cache"))
    parser.add_argument("--symbol", action="append", dest="symbols", default=None,
                        help="repeatable; default the cross-section universe")
    parser.add_argument("--timeframe", action="append", dest="timeframes", default=None,
                        help=f"repeatable; default {', '.join(ALLOWED_TIMEFRAMES)}")
    parser.add_argument("--kind", action="append", dest="kinds", default=None,
                        help="derivative price series (mark/index/premium); default all three")
    parser.add_argument("--since", default=None, metavar="YYYY-MM-DD",
                        help="also extend each series backward to this UTC date (PRE_WINDOW_REPLAY_V0.1, Thomas "
                             "2026-10-23 Q3: public data back to 2019)")
    args = parser.parse_args(argv)
    since_ms = None
    if args.since:
        try:
            since_ms = int(datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
        except ValueError:
            print(f"--since takes YYYY-MM-DD, not {args.since!r}", file=sys.stderr)
            return 2
    timeframes = args.timeframes or list(ALLOWED_TIMEFRAMES)
    refused = [tf for tf in timeframes if tf not in ALLOWED_TIMEFRAMES]
    if refused:
        print(f"refused timeframe(s): {', '.join(refused)}; the sandbox takes {', '.join(ALLOWED_TIMEFRAMES)}",
              file=sys.stderr)
        return 2
    budget = Budget()
    try:
        written = run(budget, Path(args.cache), symbols=args.symbols or market_data.CROSS_SECTION_UNIVERSE,
                      timeframes=timeframes, kinds=args.kinds or tuple(market_data.DERIVATIVE_KLINE_PATHS),
                      since_ms=since_ms)
    except Stop as exc:
        print(f"stopped after {budget.requests} request(s): {exc}", file=sys.stderr)
        return 3
    print(f"done: {budget.requests} request(s), {sum(written.values())} row(s) written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
