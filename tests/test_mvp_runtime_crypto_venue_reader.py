"""The venue contract holds an adapter that cannot place or cancel an order (crypto refactor plan PR-15,
finding S-2; ``docs/proposals/CRYPTO_REFACTOR_AND_MODULARIZATION_PLAN_V0.1.md``).

The sentinel runs inside the trading fire and asks the venue with the order key: whether it would accept
a request (``/order/test``, which creates nothing), the position mode, and the orders it holds. It used to
hold the whole order adapter to do so, so its capability was wider than its use, and only the egress
roster stood between it and a ``submit``. It now holds :class:`BinanceFuturesVenueReader`, the order
adapter's read-and-validate half. What is pinned here:

- the reader's surface is exactly those reads and the validation, and the order adapter is the reader
  plus ``submit`` and ``cancel_order`` and nothing else;
- the reader's selector is the order adapter's gate: inert without the opt-in, the reader with it;
- the refresh, given no adapter, selects the reader and never the order adapter.

No socket is opened and no key is read: constructing either adapter touches neither, and the checks are
replaced where the refresh would call them.
"""

from __future__ import annotations

from runtime.mvp_runtime.crypto import live_execution as lx
from runtime.mvp_runtime.crypto import venue_contract as vc
from runtime.mvp_runtime.crypto.live_pnl import LIVE_TRADING_ENV, REAL_LIVE_TRADING

NOW = "2026-10-01T03:00:00Z"
READS = {"validate_order", "position_mode", "fetch_order", "open_orders", "algo_open_orders"}


def _public(cls):
    return {name for name in vars(cls) if not name.startswith("_") and callable(getattr(cls, name))}


def test_the_reader_validates_and_reads_and_has_no_way_to_place_or_cancel():
    assert _public(lx.BinanceFuturesVenueReader) == READS
    assert not hasattr(lx.BinanceFuturesVenueReader, "submit")
    assert not hasattr(lx.BinanceFuturesVenueReader, "cancel_order")


def test_the_order_adapter_is_the_reader_plus_submit_and_cancel():
    assert issubclass(lx.BinanceFuturesOrderAdapter, lx.BinanceFuturesVenueReader)
    assert _public(lx.BinanceFuturesOrderAdapter) == {"submit", "cancel_order"}
    for name in READS | {"_signed_request", "_assert", "__init__"}:
        assert getattr(lx.BinanceFuturesOrderAdapter, name) is getattr(lx.BinanceFuturesVenueReader, name), name


def test_without_the_opt_in_the_reader_selector_is_inert(tmp_path, monkeypatch):
    monkeypatch.delenv(LIVE_TRADING_ENV, raising=False)
    reader = lx.select_venue_reader(now=NOW, root=tmp_path)
    assert isinstance(reader, lx.DryRunOrderAdapter)
    assert reader.network_egress is False


def test_with_the_opt_in_the_reader_selector_builds_the_reader_on_the_order_adapters_gate(tmp_path, monkeypatch):
    monkeypatch.setenv(LIVE_TRADING_ENV, REAL_LIVE_TRADING)
    reader = lx.select_venue_reader(now=NOW, root=tmp_path)
    assert type(reader) is lx.BinanceFuturesVenueReader
    assert reader.network_egress is True
    assert reader.provider_id == lx.select_order_adapter(now=NOW, root=tmp_path).provider_id


def test_the_refresh_selects_the_reader_and_never_the_order_adapter(tmp_path, monkeypatch):
    monkeypatch.setenv(LIVE_TRADING_ENV, REAL_LIVE_TRADING)
    monkeypatch.setattr(vc, "_registered_symbols", lambda root, now: ["BTCUSDT"])

    def _order_adapter(**kw):
        raise AssertionError("the venue contract selected the order adapter")

    monkeypatch.setattr(lx, "select_order_adapter", _order_adapter)
    held = []

    def _checks(*, adapter, **kw):
        held.append(adapter)
        return []

    monkeypatch.setattr(vc, "run_checks", _checks)

    class _Collector:
        rate_limited = None

    vc.refresh_verification(collector=_Collector(), now=NOW, root=tmp_path)

    [adapter] = held
    assert type(adapter) is lx.BinanceFuturesVenueReader
