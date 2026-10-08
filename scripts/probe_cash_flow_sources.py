"""H6a: can the read-only credentials see each cash-flow history? One read each, counts only.

    docker exec -u 10001 thomas-scheduler-maint python -m scripts.probe_cash_flow_sources

``docs/proposals/PORTFOLIO_CASH_FLOW_LEDGER_V0.1.md`` H6a (Thomas 2026-10-08). For every Binance history
in ``binance_wallet.FLOW_SOURCES`` and Toss's closed-order list, it prints PASS with a row count, or the
refusal's reason code and the venue's own numeric code — never a row, an amount, an asset or a symbol.
For Toss it also says whether the fields H6b would reconcile by are present on the rows it saw.

Read-only: every request is a GET through the feeds' own allowlists. It writes nothing. It needs the
lane's gates and keys, so it runs in scheduler-maint. The Toss read issues a token, which revokes the
lane's; the lane reissues once on its next fire, as after ``holdings_board --full``. A source that does
not open is not fixed by widening a key's permissions (D-H6-10): it is required or declared unused.
"""

from __future__ import annotations

import sys
import time
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_OK, force_utf8_io  # noqa: E402
from runtime.mvp_runtime.errors import MvpRuntimeError  # noqa: E402
from runtime.mvp_runtime.holdings import binance_wallet, toss_account  # noqa: E402

DAYS = 7
TOSS_FIELDS = ("filledAmount", "commission", "tax", "filledAt", "settlementDate")


def _refusal(exc: MvpRuntimeError) -> str:
    extra = " ".join(f"{name}={getattr(exc, name)}" for name in ("status", "code") if getattr(exc, name, None))
    return f"FAIL {exc.reason_code}" + (f" ({extra})" if extra else "")


def probe_binance(feed, *, now_ms: int) -> list[str]:
    lines = []
    for source, (_path, _fixed, _window, days) in binance_wallet.FLOW_SOURCES.items():
        window = min(DAYS, days) * 86_400_000
        try:
            rows = feed.flow_history(source, start_ms=now_ms - window, end_ms=now_ms)
        except MvpRuntimeError as exc:
            lines.append(f"binance {source:26}: {_refusal(exc)}")
            continue
        lines.append(f"binance {source:26}: PASS rows={len(rows)} (last {min(DAYS, days)} days)")
    return lines


def probe_toss(feed, *, today) -> list[str]:
    try:
        rows, truncated = feed.closed_orders(date_from=(today - timedelta(days=30)).isoformat(),
                                             date_to=today.isoformat())
    except MvpRuntimeError as exc:
        return [f"toss    {'closed_orders':26}: {_refusal(exc)}"]
    present = {name: sum(1 for row in rows if name in (row.get("execution") or {})) for name in TOSS_FIELDS}
    return [f"toss    {'closed_orders':26}: PASS rows={len(rows)} (last 30 days){' TRUNCATED' if truncated else ''}",
            "toss    execution fields present  : " + ", ".join(f"{k} {v}/{len(rows)}" for k, v in present.items())]


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    wallet = binance_wallet.select_wallet_feed()
    if not isinstance(wallet, binance_wallet.BinanceWalletFeed):
        print("binance : wallet gate off in this process (run it in scheduler-maint)")
    else:
        for line in probe_binance(wallet, now_ms=int(time.time() * 1000)):
            print(line)
    toss = toss_account.select_holdings_feed()
    if not isinstance(toss, toss_account.TossHoldingsFeed):
        print("toss    : account gate off in this process (run it in scheduler-maint)")
    else:
        today = timeutil.parse_iso(timeutil.utc_now_iso()).date()
        for line in probe_toss(toss, today=today):
            print(line)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
