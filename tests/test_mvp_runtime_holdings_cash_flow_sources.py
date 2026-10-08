"""H6a — the cash-flow history reads (Thomas 2026-10-08, PORTFOLIO_CASH_FLOW_LEDGER_V0.1.md D-H6-3).

Every new request is a signed GET inside the feed's own allowlist, a window longer than the venue
documents is refused before a socket opens, the venue's response shapes parse, Toss's closed-order list
pages by cursor, and the probe prints counts and codes only — never a row.
"""

from __future__ import annotations

import io
import json
import urllib.parse

import pytest

from runtime.mvp_runtime.errors import ToolBlocked, ToolError
from runtime.mvp_runtime.holdings import binance_wallet, toss_account
from scripts import probe_cash_flow_sources as probe

NOW_MS = 1_760_000_000_000
DAY = 86_400_000


class _Response(io.BytesIO):
    def __init__(self, body):
        super().__init__(json.dumps(body).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def wallet(monkeypatch):
    monkeypatch.setenv(binance_wallet.BINANCE_WALLET_ENV, binance_wallet.BINANCE_WALLET_ON)
    monkeypatch.setenv(binance_wallet.API_KEY_ENV, "k-DONOTLEAK")
    monkeypatch.setenv(binance_wallet.API_SECRET_ENV, "s-DONOTLEAK")
    feed = binance_wallet.select_wallet_feed()
    assert isinstance(feed, binance_wallet.BinanceWalletFeed)
    return feed


def _venue(monkeypatch, answers):
    sent = []

    def fake(request, timeout=None):
        parsed = urllib.parse.urlparse(request.full_url)
        sent.append((request.get_method(), parsed.path, urllib.parse.parse_qs(parsed.query)))
        return _Response(answers[parsed.path])

    monkeypatch.setattr(binance_wallet.urllib.request, "urlopen", fake)
    return sent


def test_every_history_is_in_the_allowlist_and_none_moves_money():
    for path, _fixed, _window, days in binance_wallet.FLOW_SOURCES.values():
        assert (binance_wallet.BINANCE_BASE_URL, path) in binance_wallet.ALLOWED_REQUESTS
        assert 1 <= days < 90
    paths = {path for path, *_ in binance_wallet.FLOW_SOURCES.values()}
    assert paths == {"/sapi/v1/capital/deposit/hisrec", "/sapi/v1/capital/withdraw/history", "/sapi/v1/fiat/orders",
                     "/sapi/v1/fiat/payments", "/sapi/v1/pay/transactions", "/sapi/v1/asset/transfer"}
    names = {name for name in dir(binance_wallet.BinanceWalletFeed) if not name.startswith("_")}
    assert not {n for n in names if any(w in n for w in ("withdraw", "transfer", "order", "subscribe", "redeem"))
                and n != "flow_history"}


@pytest.mark.parametrize("source, body", [
    ("crypto_deposit", [{"amount": "1"}]),
    ("fiat_deposit", {"code": "000000", "data": [{"x": 1}], "total": 1}),
    ("pay", {"code": "000000", "data": [{"x": 1}]}),
    ("transfer_futures_to_spot", {"total": 1, "rows": [{"x": 1}]}),
    ("transfer_spot_to_futures", {"total": 0}),
])
def test_each_history_is_a_signed_get_and_its_shape_parses(monkeypatch, wallet, source, body):
    path, fixed, (start_name, end_name), _days = binance_wallet.FLOW_SOURCES[source]
    sent = _venue(monkeypatch, {path: body})
    rows = wallet.flow_history(source, start_ms=NOW_MS - DAY, end_ms=NOW_MS)
    assert len(rows) == (0 if body == {"total": 0} else 1)
    method, sent_path, query = sent[0]
    assert method == "GET" and sent_path == path and "signature" in query
    assert query[start_name] == [str(NOW_MS - DAY)] and query[end_name] == [str(NOW_MS)]
    for key, value in fixed.items():
        assert query[key] == [str(value)]


def test_a_window_past_the_documented_limit_or_an_unknown_source_never_opens_a_socket(monkeypatch, wallet):
    sent = _venue(monkeypatch, {})
    with pytest.raises(ToolError):
        wallet.flow_history("crypto_withdraw", start_ms=NOW_MS - 91 * DAY, end_ms=NOW_MS)
    with pytest.raises(ToolError):
        wallet.flow_history("transfer_futures_to_spot", start_ms=NOW_MS - 8 * DAY, end_ms=NOW_MS)
    with pytest.raises(ToolBlocked):
        wallet.flow_history("sub_account", start_ms=NOW_MS - DAY, end_ms=NOW_MS)
    assert sent == []


def test_an_unparseable_history_is_malformed(monkeypatch, wallet):
    path = binance_wallet.FLOW_SOURCES["pay"][0]
    _venue(monkeypatch, {path: {"code": "000000"}})
    with pytest.raises(ToolError) as exc:
        wallet.flow_history("pay", start_ms=NOW_MS - DAY, end_ms=NOW_MS)
    assert exc.value.reason_code == "MALFORMED_RESULT"


# --- Toss --------------------------------------------------------------------------------------

def test_toss_closed_orders_is_a_get_that_pages_by_cursor(monkeypatch):
    feed = toss_account.TossHoldingsFeed.__new__(toss_account.TossHoldingsFeed)
    feed._account = (1, "123")
    calls = []
    pages = iter([{"orders": [{"orderId": "a"}], "nextCursor": "c1", "hasNext": True},
                  {"orders": [{"orderId": "b"}], "nextCursor": None, "hasNext": False}])

    def fake_get(path, params=None, *, account, what, timeout_seconds):
        calls.append((path, dict(params or {}), account))
        return next(pages)

    monkeypatch.setattr(feed, "_get", fake_get)
    monkeypatch.setattr(feed, "_resolve_account", lambda **_kw: (1, "123"))
    rows, truncated = feed.closed_orders(date_from="2026-09-08", date_to="2026-10-08")
    assert [r["orderId"] for r in rows] == ["a", "b"] and truncated is False
    assert all(path == toss_account.ORDERS_PATH and account for path, _p, account in calls)
    assert calls[0][1]["status"] == "CLOSED" and "cursor" not in calls[0][1] and calls[1][1]["cursor"] == "c1"


def test_toss_closed_orders_says_when_its_pages_ran_out(monkeypatch):
    feed = toss_account.TossHoldingsFeed.__new__(toss_account.TossHoldingsFeed)
    monkeypatch.setattr(feed, "_resolve_account", lambda **_kw: (1, "123"))
    monkeypatch.setattr(feed, "_get", lambda *a, **k: {"orders": [{}], "nextCursor": "x", "hasNext": True})
    rows, truncated = feed.closed_orders(date_from="d", date_to="d", max_pages=2)
    assert len(rows) == 2 and truncated is True


def test_the_toss_feed_still_has_no_order_writing_method():
    names = {name for name in dir(toss_account.TossHoldingsFeed) if not name.startswith("_")}
    assert not {n for n in names if any(w in n for w in ("place", "create", "cancel", "modify", "submit"))}


# --- the probe prints counts, never a row ----------------------------------------------------------

def test_the_probe_prints_counts_and_codes_only():
    class Feed:
        def flow_history(self, source, **_kw):
            if source == "pay":
                raise ToolError("BINANCE_WALLET_FORBIDDEN", "x")
            return [{"coin": "ZZTOP", "amount": "123.456", "address": "addr-DONOTLEAK"}]

    lines = probe.probe_binance(Feed(), now_ms=NOW_MS)
    text = "\n".join(lines)
    assert "pay" in text and "FAIL BINANCE_WALLET_FORBIDDEN" in text and "PASS rows=1" in text
    for leak in ("ZZTOP", "123.456", "addr-DONOTLEAK"):
        assert leak not in text

    class Toss:
        def closed_orders(self, **_kw):
            return [{"symbol": "005930", "execution": {"filledAmount": "700000", "commission": "1400"}}], False

    from datetime import date
    toss_text = "\n".join(probe.probe_toss(Toss(), today=date(2026, 10, 8)))
    assert "PASS rows=1" in toss_text and "filledAmount 1/1" in toss_text and "settlementDate 0/1" in toss_text
    assert "005930" not in toss_text and "700000" not in toss_text
