"""Fill the research cache with Deribit's DVOL (30-day implied volatility index), keyless and paced.

``docs/proposals/RESEARCH_DATA_SOURCES_V0.1.md`` (Thomas 2026-11-10, Q1: Deribit public market data, the research
sandbox only) and ``RESEARCH_SANDBOX_V0.1.md`` §4.2. It runs on the host as the ``claude`` account, like
``fetch_public``; the sandbox itself has no network. The runtime never reads this source.

- **What:** daily DVOL bars ``[open_time_ms, open, high, low, close]`` for :data:`CURRENCIES`, raw, one per line,
  oldest first, under ``<cache>/deribit/dvol/<CUR>.1D.jsonl``.
- **Only closed days:** a bar is written once its day has ended (UTC). Rows already cached are never asked for again;
  a later run asks only for the days after the newest cached bar.
- **Pace:** at least :data:`MIN_INTERVAL_S` between requests, no key, no retry. Any status other than 200, or an
  ``error`` in the answer, stops the run. Never inside the factory window (``fetch_public.FACTORY_WINDOW_UTC``).
- **Output:** dates and counts only.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.research import fetch_public  # noqa: E402

BASE = "https://www.deribit.com/api/v2/public/"
CURRENCIES = ("BTC", "ETH")
FIRST_DAY = date(2021, 3, 1)            # DVOL's daily history starts 2021-03-24 (stage 1, 2026-11-10)
MIN_INTERVAL_S = 1.0
DAY_MS = 86_400_000
Stop = fetch_public.Stop


def dvol_path(cache: Path, currency: str) -> Path:
    return cache / "deribit" / "dvol" / f"{currency}.1D.jsonl"


def _ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)


@dataclass
class Pace:
    """One request at a time, :data:`MIN_INTERVAL_S` apart. ``fetch``, ``clock`` and ``sleep`` are injectable."""

    fetch: fetch_public.Fetch = fetch_public.urllib_fetch
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    min_interval: float = MIN_INTERVAL_S
    last: float | None = None
    requests: int = 0

    def get(self, method: str, params: dict[str, Any]) -> Any:
        hour = time.gmtime(self.clock()).tm_hour
        if fetch_public.FACTORY_WINDOW_UTC[0] <= hour < fetch_public.FACTORY_WINDOW_UTC[1]:
            raise Stop("inside the factory window (08:00-09:00 UTC)")
        if self.last is not None:
            wait = self.min_interval - (self.clock() - self.last)
            if wait > 0:
                self.sleep(wait)
        self.last = self.clock()
        self.requests += 1
        status, _headers, body = self.fetch(f"{BASE}{method}?{urllib.parse.urlencode(params)}")
        if status != 200:
            raise Stop(f"Deribit answered {status} for {method}; stopping without retry")
        answer = json.loads(body)
        if "error" in answer or "result" not in answer:
            raise Stop(f"Deribit answered an error for {method}; stopping without retry")
        return answer["result"]


def _cached_times(path: Path) -> list[int]:
    if not path.exists():
        return []
    return [int(json.loads(line)[0]) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def fill_dvol(pace: Pace, path: Path, *, currency: str, now_ms: int) -> int:
    """Append the closed daily DVOL bars after the newest cached one. Returns the rows written."""
    today = now_ms - now_ms % DAY_MS
    cached = _cached_times(path)
    start = (max(cached) + DAY_MS) if cached else _ms(FIRST_DAY)
    end = today - 1                                      # today's bar is still open
    if start > end:
        return 0
    rows: dict[int, list[Any]] = {}
    cursor = end
    while True:
        result = pace.get("get_volatility_index_data", {"currency": currency, "start_timestamp": start,
                                                         "end_timestamp": cursor, "resolution": "1D"})
        data = result.get("data") or []
        for row in data:
            if not (isinstance(row, list) and len(row) == 5):
                raise Stop(f"DVOL {currency}: a row is not [time, open, high, low, close]; the shape changed")
            t = int(row[0])
            if start <= t and t + DAY_MS <= today:
                rows[t] = row
        nxt = result.get("continuation")
        if not data or not nxt or int(nxt) >= cursor:
            break
        cursor = int(nxt)
    return fetch_public._append(path, (rows[t] for t in sorted(rows)))


def run(pace: Pace, cache: Path, *, currencies: Iterable[str], now_ms: int,
        say: Callable[[str], None] = print) -> dict[str, int]:
    written: dict[str, int] = {}
    for currency in currencies:
        path = dvol_path(cache, currency)
        written[currency] = fill_dvol(pace, path, currency=currency, now_ms=now_ms)
        times = _cached_times(path)
        span = "" if not times else (f" ({datetime.fromtimestamp(min(times) / 1000, tz=timezone.utc):%Y-%m-%d}.."
                                     f"{datetime.fromtimestamp(max(times) / 1000, tz=timezone.utc):%Y-%m-%d},"
                                     f" {len(times)} day(s) cached)")
        say(f"  dvol {currency}: +{written[currency]}{span}")
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fetch_deribit", description=__doc__.splitlines()[0])
    parser.add_argument("--cache", default=str(Path.home() / "research" / "cache"))
    parser.add_argument("--currency", action="append", dest="currencies", default=None,
                        help=f"repeatable; default {', '.join(CURRENCIES)}")
    args = parser.parse_args(argv)
    refused = [c for c in (args.currencies or []) if c not in CURRENCIES]
    if refused:
        print(f"refused currency: {', '.join(refused)}; DVOL is read for {', '.join(CURRENCIES)}", file=sys.stderr)
        return 2
    pace = Pace()
    try:
        written = run(pace, Path(args.cache), currencies=args.currencies or CURRENCIES,
                      now_ms=int(time.time() * 1000))
    except Stop as exc:
        print(f"stopped after {pace.requests} request(s): {exc}", file=sys.stderr)
        return 3
    print(f"done: {pace.requests} request(s), {sum(written.values())} row(s) written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
