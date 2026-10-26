"""The rebalancing candidate report (REBALANCING_PROPOSAL §2.7, RB2'') — Thomas's terminal only, optional.

    docker exec -it thomas-scheduler-maint python -m scripts.rebalance_candidates

The container that holds the Toss key (``MVP_TOSS_ACCOUNT``) runs it. It lists, per asset class, the ETFs Toss
trades that fit the decided exposure, with liquidity, commission, tax kind and currency exposure, and what the
holdings fire picks from them on its own once a month (RB2''). Nothing needs doing after it: it is a view.
``holdings_board --approve-candidates`` overrides a class only if Thomas wants another list.

It reads listings and market data only, never holdings. The output is raw market data, and Toss's terms
allow the investor's own use only, so it never goes to a chat, a model prompt or a channel. It issues its
own Toss token, which revokes scheduler-maint's; the lane reissues once on its next fire.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, force_utf8_io  # noqa: E402
from runtime.mvp_runtime.errors import MvpRuntimeError  # noqa: E402
from runtime.mvp_runtime.holdings import candidates  # noqa: E402
from runtime.mvp_runtime.holdings.toss_account import TossHoldingsFeed, select_holdings_feed  # noqa: E402

collect = candidates.collect
PAUSE_SECONDS = candidates.PAUSE_SECONDS


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
        print(f"candidates : refused ({getattr(exc, 'reason_code', type(exc).__name__)})")
        return EXIT_BLOCKED
    print("\n".join(candidates.render(ranked, fetched_at=timeutil.utc_now_iso(), usd_krw=usd_krw)))
    print("note       : this read issued its own Toss token, which revoked scheduler-maint's; "
          "the lane reissues once on its next fire")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
