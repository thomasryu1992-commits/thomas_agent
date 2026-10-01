"""A live position names the cycle that opened it (crypto refactor plan J-5.1).

``build_live_position`` has always had a ``cycle_id`` field, and only the signed testnet cycle filled
it. The autonomous path booked ``None``, because the cycle's id was computed at the end of the cycle,
after the live leg had sent and booked. The id is a function of the context and the fire's time
alone (``cycle.cycle_id_for``), so the cycle now computes it first and hands it down: cycle →
``live_route.run_live_leg`` → ``live_leg.execute_live_entry`` → the position. What is pinned here:

- the cycle hands the live leg exactly the id its own record carries, and that id is computed the way
  it always was, so ledger rows before and after this change key the same;
- a position the leg opens carries it, and a caller that does not say books ``None``, as before;
- it is not part of the sealed intent and not part of the position id: an order and a position are
  the same whatever cycle id rides along.
"""

from __future__ import annotations

from runtime.mvp_runtime.crypto import live_route, pre_order_gate
from runtime.mvp_runtime.crypto.cycle import cycle_id_for
from runtime.mvp_runtime.crypto.live_position import build_live_position, list_open_live_positions
from runtime.read_only_kernel import integrity
from tests.test_mvp_runtime_crypto_cycle import FakeExchangeCollector, _always_spec, _cycle, _install_pool
from tests.test_mvp_runtime_crypto_live_route import BAR_00, _Venue, _wire_whole_leg

OPEN_AT = "2026-07-28T04:05:00Z"


def test_the_cycle_id_is_computed_as_it_always_was():
    """The ledger keys on it: a row written before J-5.1 and one written after name a context the same."""
    assert cycle_id_for("BTCUSDT", "4h", "2026-10-01T07:14:00Z") == integrity.short_id(
        "crypto_cycle", {"symbol": "BTCUSDT", "timeframe": "4h", "at": "2026-10-01T07:14:00Z"})


def test_the_cycle_hands_the_live_leg_the_id_its_record_carries(tmp_path, monkeypatch):
    from runtime.mvp_runtime.crypto import cycle as cycle_mod

    seen: dict[str, object] = {}

    def _capture(**kw):
        seen.update(kw)
        return {"live_route_status": "DISABLED", "live_opened": None, "live_settled": None,
                "live_reason_codes": [], "halt": False}

    monkeypatch.setattr(cycle_mod, "run_live_leg", _capture)
    _install_pool(tmp_path, _always_spec())
    record = _cycle(tmp_path, FakeExchangeCollector())

    assert seen["cycle_id"] == record["cycle_id"]
    assert record["cycle_id"] == cycle_id_for(record["symbol"], record["timeframe"], seen["now"])


def _open_one(tmp_path, monkeypatch, **extra):
    """Open one real-path position through the whole leg (fake venue), passing ``extra`` to the leg."""
    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    real = live_route.run_live_leg
    monkeypatch.setattr(live_route, "run_live_leg", lambda **kw: real(**kw, **extra))
    opened = run(OPEN_AT, BAR_00)
    assert opened["live_route_status"] == live_route.ROUTE_OPENED, opened["live_reason_codes"]
    [position] = list_open_live_positions(tmp_path)
    return position


def test_a_position_the_leg_opens_names_the_cycle_that_opened_it(tmp_path, monkeypatch):
    position = _open_one(tmp_path, monkeypatch, cycle_id="cyc-opened-it")
    assert position["cycle_id"] == "cyc-opened-it"


def test_a_caller_that_does_not_say_books_the_position_as_before(tmp_path, monkeypatch):
    position = _open_one(tmp_path, monkeypatch)
    assert position["cycle_id"] is None


def test_the_cycle_id_is_neither_sealed_nor_part_of_the_position_id():
    assert "cycle_id" not in pre_order_gate.INTENT_BOUND_FIELDS
    terms = dict(symbol="BTCUSDT", direction="LONG", quantity=0.001, entry_price=65000.0,
                 stop_loss=64000.0, take_profit=67000.0, opened_at=OPEN_AT,
                 entry_client_order_id="c-1", entry_exchange_order_id="e-1")
    with_id = build_live_position(**terms, cycle_id="cyc-a")
    without = build_live_position(**terms)
    assert with_id["position_id"] == without["position_id"]
    assert {k for k in with_id if with_id[k] != without[k]} == {"cycle_id"}
