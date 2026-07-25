"""LP5.3 tests — the venue's own trading rules. Pure parsing, no socket.

LP5.2 shipped a sizer that refused every call because it had no filters to size against.
These tests cover the read that ends that, and the properties that make it safe: the
numbers come from the payload or not at all, a MARKET order is bound by the stricter of
the two lot filters, a non-TRADING symbol is refused outright, and every failure degrades
to "no filters" (which refuses downstream) rather than to a plausible-looking default.

The payload shapes here are the venue's documented shapes. They are fixtures, not an
authority: the module must read whatever it is given, never carry these numbers itself.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import live_filters as lf
from runtime.mvp_runtime.errors import ToolError


def _payload(symbol="BTCUSDT", *, status="TRADING", filters=None, extra_symbols=()):
    rows = [{
        "symbol": symbol,
        "status": status,
        "filters": list(filters) if filters is not None else [
            {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "1000"},
            {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "120"},
            {"filterType": "MIN_NOTIONAL", "notional": "5"},
        ],
    }]
    rows.extend(extra_symbols)
    return {"symbols": rows}


# --- the happy path ------------------------------------------------------------

def test_the_documented_shape_parses():
    filters, reason = lf.parse_symbol_filters(_payload(), "BTCUSDT")
    assert reason is None
    assert filters is not None and filters.valid()
    assert filters.step_size == 0.001
    assert filters.min_qty == 0.001
    assert filters.min_notional == 5.0
    assert filters.tick_size == 0.1


def test_the_right_symbol_is_picked_out_of_many():
    other = {"symbol": "ETHUSDT", "status": "TRADING", "filters": [
        {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
        {"filterType": "LOT_SIZE", "stepSize": "0.01", "minQty": "0.01", "maxQty": "10000"},
        {"filterType": "MIN_NOTIONAL", "notional": "20"},
    ]}
    filters, _ = lf.parse_symbol_filters(_payload(extra_symbols=[other]), "ETHUSDT")
    assert filters is not None
    assert (filters.step_size, filters.min_notional, filters.tick_size) == (0.01, 20.0, 0.01)


# --- the MARKET_LOT_SIZE property (a real venue semantic, not a nicety) ---------

def test_the_stricter_lot_filter_binds_a_market_order():
    """LP5 entries and closes are MARKET orders, which the venue binds by MARKET_LOT_SIZE
    *as well as* LOT_SIZE. Taking the larger step and minimum means the size is legal under
    both, instead of being discovered illegal as a venue rejection."""
    filters, _ = lf.parse_symbol_filters(_payload(filters=[
        {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "1000"},
        {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.01", "minQty": "0.05", "maxQty": "120"},
        {"filterType": "MIN_NOTIONAL", "notional": "5"},
    ]), "BTCUSDT")
    assert filters is not None
    assert filters.step_size == 0.01   # the larger step
    assert filters.min_qty == 0.05     # the larger minimum
    assert filters.max_qty == 120.0    # the smaller maximum


def test_market_lot_size_is_optional():
    filters, _ = lf.parse_symbol_filters(_payload(filters=[
        {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "1000"},
        {"filterType": "MIN_NOTIONAL", "notional": "5"},
    ]), "BTCUSDT")
    assert filters is not None and filters.max_qty == 1000.0


# --- refusals ------------------------------------------------------------------

def test_a_symbol_the_venue_does_not_list_refuses():
    filters, reason = lf.parse_symbol_filters(_payload(), "DOGEUSDT")
    assert filters is None and reason == lf.FILTERS_SYMBOL_UNKNOWN


@pytest.mark.parametrize("status", ["BREAK", "SETTLING", "PENDING_TRADING", "CLOSE", ""])
def test_a_symbol_that_is_not_trading_refuses(status):
    """A halted or delisted symbol must not be sized, let alone ordered."""
    filters, reason = lf.parse_symbol_filters(_payload(status=status), "BTCUSDT")
    assert filters is None and reason == lf.FILTERS_SYMBOL_NOT_TRADING


@pytest.mark.parametrize("drop", ["PRICE_FILTER", "LOT_SIZE", "MIN_NOTIONAL"])
def test_a_missing_required_filter_refuses(drop):
    full = [
        {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "1000"},
        {"filterType": "MIN_NOTIONAL", "notional": "5"},
    ]
    kept = [f for f in full if f["filterType"] != drop]
    filters, reason = lf.parse_symbol_filters(_payload(filters=kept), "BTCUSDT")
    assert filters is None and reason == lf.FILTERS_INCOMPLETE


@pytest.mark.parametrize("bad", ["0", "", "oops", None])
def test_a_zero_or_unparseable_increment_is_unknown_not_unrestricted(bad):
    """A step of zero would make flooring a division by zero and a minimum of zero would
    let dust through. Both mean 'not known', and not-known refuses."""
    filters, reason = lf.parse_symbol_filters(_payload(filters=[
        {"filterType": "PRICE_FILTER", "tickSize": bad},
        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "1000"},
        {"filterType": "MIN_NOTIONAL", "notional": "5"},
    ]), "BTCUSDT")
    assert filters is None and reason == lf.FILTERS_INCOMPLETE


@pytest.mark.parametrize("payload", [None, {}, {"symbols": "nope"}, [], "text", 42])
def test_a_malformed_payload_never_crashes(payload):
    filters, reason = lf.parse_symbol_filters(payload, "BTCUSDT")
    assert filters is None and reason == lf.FILTERS_INCOMPLETE


def test_a_malformed_filter_entry_is_ignored_not_fatal():
    filters, _ = lf.parse_symbol_filters(_payload(filters=[
        "not a mapping", {"no": "filterType"},
        {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "1000"},
        {"filterType": "MIN_NOTIONAL", "notional": "5"},
    ]), "BTCUSDT")
    assert filters is not None


# --- the read: degrade, never block --------------------------------------------

class _Collector:
    def __init__(self, payload=None, error=None):
        self._payload, self._error = payload, error
        self.calls = 0

    def exchange_info(self, *, timeout_seconds: int):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._payload


def test_reading_through_a_collector_returns_parsed_filters():
    collector = _Collector(_payload())
    filters, reason = lf.read_symbol_filters(collector, "BTCUSDT")
    assert reason is None and filters is not None
    assert collector.calls == 1


def test_a_collector_without_the_capability_degrades():
    """The mock market-data collector has no exchange_info ON PURPOSE — a mock that
    answered here would hand live sizing invented lot steps."""
    class NoCapability:
        pass

    filters, reason = lf.read_symbol_filters(NoCapability(), "BTCUSDT")
    assert filters is None and reason == lf.FILTERS_UNAVAILABLE


def test_the_real_mock_collector_has_no_exchange_info():
    from runtime.mvp_runtime.crypto.market_data import MockMarketDataCollector

    filters, reason = lf.read_symbol_filters(MockMarketDataCollector(), "BTCUSDT")
    assert filters is None and reason == lf.FILTERS_UNAVAILABLE


def test_a_failed_read_degrades_rather_than_raising():
    filters, reason = lf.read_symbol_filters(
        _Collector(error=ToolError("TOOL_TRANSPORT", "boom")), "BTCUSDT"
    )
    assert filters is None and reason == lf.FILTERS_DEGRADED


def test_a_read_that_returns_the_wrong_symbol_reports_the_parse_reason():
    filters, reason = lf.read_symbol_filters(_Collector(_payload()), "ETHUSDT")
    assert filters is None and reason == lf.FILTERS_SYMBOL_UNKNOWN


def test_the_real_collector_exposes_the_capability():
    """The gated Binance collector is the one thing that can answer — and only behind the
    existing binance_futures grant, which it re-asserts at egress."""
    from runtime.mvp_runtime.crypto.market_data import BinanceFuturesCollector

    collector = BinanceFuturesCollector()
    assert callable(getattr(collector, "exchange_info", None))
    with pytest.raises(Exception) as excinfo:
        collector.exchange_info(timeout_seconds=1)
    # No authorization was supplied, so it refuses BEFORE opening a socket.
    assert getattr(excinfo.value, "reason_code", "") != "TOOL_TRANSPORT"
