"""The rebalancing candidate report (REBALANCING_PROPOSAL §2.7, RB2') — Thomas's terminal only.

    docker exec -it thomas-scheduler-maint python -m scripts.rebalance_candidates

The container that holds the Toss key (``MVP_TOSS_ACCOUNT``) runs it. It lists, per asset class, the ETFs Toss
trades that fit the decided exposure, with liquidity, commission, tax kind and currency exposure. Thomas
picks three or four per class and approves them with ``holdings_board --approve-candidates``.

It reads listings and market data only, never holdings. The output is raw market data, and Toss's terms
allow the investor's own use only, so it never goes to a chat, a model prompt or a channel. It issues its
own Toss token, which revokes scheduler-maint's; the lane reissues once on its next fire.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, force_utf8_io  # noqa: E402
from runtime.mvp_runtime.errors import MvpRuntimeError  # noqa: E402
from runtime.mvp_runtime.holdings import candidates  # noqa: E402
from runtime.mvp_runtime.holdings.toss_account import (  # noqa: E402
    STOCKS_PAGE_LIMIT, TossHoldingsFeed, select_holdings_feed,
)

# Toss's per-group limits (overview "Rate Limits", 2026-10-26): STOCK_ALL 1/s, STOCK 5/s, MARKET_DATA_CHART 20/s.
PAUSE_SECONDS = {"listing": 1.1, "info": 0.25, "candles": 0.06}


def collect(feed: TossHoldingsFeed, *, timeout: int, sleep=time.sleep) -> tuple[dict, float | None]:
    """Every read the report needs, paced under Toss's limits. Returns the ranked shortlists and the rate."""
    fees = {}
    for row in feed.commission_rates(timeout_seconds=timeout):
        try:
            fees[str(row.get("marketCountry"))] = float(row.get("commissionRate"))
        except (TypeError, ValueError):
            continue
    usd_krw = feed.usd_krw_rate(timeout_seconds=timeout)
    listings = {}
    for market in (*candidates.KR_MARKETS, *candidates.US_MARKETS):
        listings[market] = feed.listed_etfs(market, timeout_seconds=timeout)
        sleep(PAUSE_SECONDS["listing"])
    shortlists = candidates.pool(listings)
    symbols = sorted({row["symbol"] for rows in shortlists.values() for row in rows})
    info = {}
    for start in range(0, len(symbols), STOCKS_PAGE_LIMIT):
        for row in feed.stock_info(symbols[start:start + STOCKS_PAGE_LIMIT], timeout_seconds=timeout):
            info[str(row.get("symbol"))] = row
        sleep(PAUSE_SECONDS["info"])
    bars = {}
    for symbol in symbols:
        bars[symbol] = feed.daily_candles(symbol, count=candidates.LIQUIDITY_DAYS, timeout_seconds=timeout)
        sleep(PAUSE_SECONDS["candles"])
    return candidates.rank(shortlists, info=info, candles=bars, fees=fees, usd_krw=usd_krw), usd_krw


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--timeout", type=int, default=10)
    args = parser.parse_args(argv)
    feed = select_holdings_feed()
    if not isinstance(feed, TossHoldingsFeed):
        print("candidates : the Toss feed is not configured here (run it in thomas-scheduler-maint)")
        return EXIT_BLOCKED
    try:
        ranked, usd_krw = collect(feed, timeout=args.timeout)
    except MvpRuntimeError as exc:
        print(f"candidates : refused ({getattr(exc, 'reason_code', type(exc).__name__)}); nothing was approved")
        return EXIT_BLOCKED
    print("\n".join(candidates.render(ranked, fetched_at=timeutil.utc_now_iso(), usd_krw=usd_krw)))
    print("note       : this read issued its own Toss token, which revoked scheduler-maint's; "
          "the lane reissues once on its next fire")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
