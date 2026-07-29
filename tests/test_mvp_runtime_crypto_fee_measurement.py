"""Measuring what the venue actually charged, per fill, instead of deriving it by hand.

`cost.DEFAULT_TAKER_FEE_BPS` is 5.0 because someone divided one window's commission by an
estimated notional on 2026-07-26 (0.1291 USDT over ~258 USDT of fills). That worked because
every fill in the window was a taker fill — all four canaries are MARKET orders. It stops
working the moment a maker fill exists, because `/fapi/v1/income` reports COMMISSION as a
per-window total with no leg attribution, so a maker exit's fee lands in the same bucket as
the taker entry's.

`cost.DEFAULT_MAKER_FEE_BPS` is therefore Binance's PUBLISHED rate, not a measurement, and its
docstring says so along with the reason it matters: if the real rate is higher, the backtest
reports an edge better than reality. `/fapi/v1/userTrades` carries `commission`,
`commissionAsset`, `quoteQty` and a `maker` boolean per fill — the split that closes it.

The load-bearing behaviour under test is what happens when there is NOTHING to measure. As of
2026-07-28 the maker side has a sample of zero, and "no maker fill has happened yet" must not
render as "the maker fee is 0.0 bps".
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import account as account_mod
from runtime.mvp_runtime.crypto.account import (
    MAKER,
    TAKER,
    USER_TRADES_PATH,
    NoAccountFeed,
    measure_fee_rates,
    read_fee_rates,
    render_fee_rates,
)
from runtime.mvp_runtime.errors import ToolError


def _fill(*, maker: bool, quote_qty: str, commission: str, asset: str = "USDT") -> dict:
    """One `/fapi/v1/userTrades` row, in the venue's own shape (strings, not floats)."""
    return {
        "symbol": "BTCUSDT", "id": 1, "orderId": 1, "side": "BUY",
        "price": "64500.0", "qty": "0.001", "quoteQty": quote_qty,
        "commission": commission, "commissionAsset": asset,
        "maker": maker, "buyer": True, "time": 1_753_000_000_000,
    }


# --- the arithmetic ---------------------------------------------------------------

def test_the_taker_rate_comes_out_of_the_fills_that_paid_it():
    """5 bps of 100 USDT is 0.05 USDT — the published standard rate, now derived rather
    than assumed."""
    measured = measure_fee_rates([_fill(maker=False, quote_qty="100.0", commission="0.05")])
    assert measured[TAKER].rate_bps == pytest.approx(5.0)
    assert measured[TAKER].fills == 1
    assert measured[TAKER].unmeasurable_reason == ""


def test_the_two_sides_are_measured_separately():
    """The whole reason for this endpoint. A window holding both legs gives income one
    commission total; here it gives two rates."""
    measured = measure_fee_rates([
        _fill(maker=False, quote_qty="100.0", commission="0.05"),
        _fill(maker=True, quote_qty="100.0", commission="0.02"),
    ])
    assert measured[TAKER].rate_bps == pytest.approx(5.0)
    assert measured[MAKER].rate_bps == pytest.approx(2.0)


def test_the_side_split_follows_the_venue_not_an_order_type_this_code_chose():
    """`maker` is the venue's own boolean on every row. Nothing here infers the side from
    an order type, so a LIMIT that crossed the spread and paid taker is counted as taker."""
    measured = measure_fee_rates([
        _fill(maker=False, quote_qty="50.0", commission="0.025"),
        _fill(maker=False, quote_qty="50.0", commission="0.025"),
    ])
    assert measured[TAKER].fills == 2 and measured[MAKER].fills == 0


def test_the_rate_is_notional_weighted_not_an_average_of_rates():
    """A big fill and a small one at different rates: the answer is what the account paid
    overall, which is the figure a cost model needs."""
    measured = measure_fee_rates([
        _fill(maker=False, quote_qty="1000.0", commission="0.50"),   # 5 bps
        _fill(maker=False, quote_qty="10.0", commission="0.01"),     # 10 bps
    ])
    # 0.51 / 1010 * 10000 = 5.0495..., not the 7.5 an unweighted mean would give.
    assert measured[TAKER].rate_bps == pytest.approx(5.0495, abs=1e-3)


def test_a_maker_rebate_is_reported_as_negative_not_clamped():
    """Some tiers pay the maker side. Clamping would hide the single result that would most
    change how `cost.apply_cost_model` is written."""
    measured = measure_fee_rates([_fill(maker=True, quote_qty="100.0", commission="-0.01")])
    assert measured[MAKER].rate_bps == pytest.approx(-1.0)


# --- refusing to invent a number --------------------------------------------------

def test_no_maker_fill_is_an_absent_rate_not_a_zero_one():
    """The live state on 2026-07-28: every order this account has placed is a MARKET canary.

    A zero here would read as "this venue charges no maker fee" and would silently satisfy
    the "replace the published rate with a measurement" obligation with a fabrication.
    """
    measured = measure_fee_rates([_fill(maker=False, quote_qty="100.0", commission="0.05")])
    assert measured[MAKER].rate_bps is None
    assert measured[MAKER].fills == 0
    assert "no maker fill" in measured[MAKER].unmeasurable_reason


def test_an_empty_history_measures_neither_side():
    measured = measure_fee_rates([])
    assert measured[MAKER].rate_bps is None and measured[TAKER].rate_bps is None


def test_a_commission_paid_in_bnb_is_named_never_divided():
    """A BNB-paid fee is a real discount, not a bug — but it is not in the notional's units,
    and converting it would need a BNB price this function does not have."""
    measured = measure_fee_rates([
        _fill(maker=False, quote_qty="100.0", commission="0.0007", asset="BNB"),
    ])
    assert measured[TAKER].rate_bps is None
    assert "BNB" in measured[TAKER].unmeasurable_reason
    assert measured[TAKER].commission_assets == ("BNB",)


def test_a_mixed_commission_asset_refuses_rather_than_using_the_usdt_subset():
    """Dropping the BNB fills and dividing the USDT ones by the full notional would understate
    the rate; dividing by the USDT subset's notional would silently change what was measured."""
    measured = measure_fee_rates([
        _fill(maker=False, quote_qty="100.0", commission="0.05"),
        _fill(maker=False, quote_qty="100.0", commission="0.0007", asset="BNB"),
    ])
    assert measured[TAKER].rate_bps is None
    assert measured[TAKER].commission_assets == ("BNB", "USDT")


def test_fills_with_no_notional_refuse():
    measured = measure_fee_rates([_fill(maker=True, quote_qty="0", commission="0.0")])
    assert measured[MAKER].rate_bps is None
    assert "notional" in measured[MAKER].unmeasurable_reason


def test_a_non_list_history_is_not_a_measurement():
    """Fail-closed on a shape that never came from the endpoint."""
    measured = measure_fee_rates(None)
    assert measured[MAKER].rate_bps is None and measured[TAKER].rate_bps is None


# --- the read path ----------------------------------------------------------------

class _StubFeed:
    feed_id = "stub"
    feed_version = "stub-1"
    network_egress = True

    def __init__(self, rows=None, error=None):
        self._rows, self._error = rows, error
        self.calls: list[tuple] = []

    def account_snapshot(self, *, timeout_seconds: int):
        return None

    def fill_history(self, symbol, *, start_ms, timeout_seconds):
        self.calls.append((symbol, start_ms, timeout_seconds))
        if self._error is not None:
            raise self._error
        return self._rows


def test_the_read_measures_and_records_without_copying_the_venues_book(monkeypatch):
    feed = _StubFeed([_fill(maker=True, quote_qty="100.0", commission="0.02")])
    monkeypatch.setattr(account_mod, "select_account_feed", lambda **kw: feed)

    measured, record = read_fee_rates("BTCUSDT", days=7, now_ms=7 * 86_400_000)
    assert measured[MAKER].rate_bps == pytest.approx(2.0)
    assert record["operation"] == "fee_rate_measurement"
    assert record["read_only"] is True and record["external_action"] is False
    assert record["measured"][MAKER]["rate_bps"] == pytest.approx(2.0)
    # Rates and counts, never the fills themselves: an order id and an exact price belong to
    # the venue's book, and this record is evidence that a measurement happened.
    assert "orderId" not in json_dumps(record) and "64500" not in json_dumps(record)
    assert feed.calls == [("BTCUSDT", 0, 10)]


def test_an_unconfigured_feed_is_not_configured_rather_than_degraded(monkeypatch):
    """No feed is a normal state — the same posture `read_account` already takes."""
    monkeypatch.setattr(account_mod, "select_account_feed", lambda **kw: NoAccountFeed())
    measured, record = read_fee_rates("BTCUSDT")
    assert measured is None
    assert record["configured"] is False and not record.get("degraded")


def test_a_transport_failure_degrades_and_never_raises(monkeypatch):
    feed = _StubFeed(error=ToolError("TOOL_TRANSPORT", "boom"))
    monkeypatch.setattr(account_mod, "select_account_feed", lambda **kw: feed)

    measured, record = read_fee_rates("BTCUSDT")
    assert measured is None
    assert record["degraded"] is True
    assert record["error_reason_code"] == "TOOL_TRANSPORT"


def test_could_not_look_and_looked_at_nothing_are_different_answers(monkeypatch):
    """The distinction that decides whether an operator retries or accepts the result."""
    monkeypatch.setattr(account_mod, "select_account_feed", lambda **kw: _StubFeed([]))
    measured, record = read_fee_rates("BTCUSDT")
    assert measured is not None, "an account with no fills was still successfully read"
    assert record["configured"] is True and not record.get("degraded")
    assert measured[TAKER].rate_bps is None


def test_the_inert_feed_answers_none_not_an_empty_history():
    """Otherwise a machine with no grant would report the same thing as a live account that
    has never traded."""
    assert NoAccountFeed().fill_history("BTCUSDT", start_ms=0, timeout_seconds=1) is None


# --- the operator surface ---------------------------------------------------------

def test_the_board_states_the_sample_size_next_to_every_rate():
    """A bps figure with no fill count behind it is how the 2026-07-26 taker reading came to
    be quoted as settled."""
    measured = measure_fee_rates([
        _fill(maker=False, quote_qty="100.0", commission="0.05"),
        _fill(maker=True, quote_qty="100.0", commission="0.02"),
    ])
    text = render_fee_rates(measured, symbol="BTCUSDT", days=30)
    assert "5.0000 bps" in text and "2.0000 bps" in text
    assert "1 fill(s)" in text


def test_the_board_says_why_a_missing_rate_is_missing():
    measured = measure_fee_rates([_fill(maker=False, quote_qty="100.0", commission="0.05")])
    text = render_fee_rates(measured, symbol="BTCUSDT", days=30)
    assert "no maker fill" in text
    assert "0.0000 bps" not in text, "an absent maker rate must never render as a zero one"


def test_the_endpoint_is_the_per_fill_one():
    """Guards the one thing that makes this measurable at all: `/fapi/v1/income` cannot
    attribute a commission to a leg, and swapping the path back would look harmless."""
    assert USER_TRADES_PATH == "/fapi/v1/userTrades"


def json_dumps(value) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str)
