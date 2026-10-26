"""This year's realised overseas gain, rebuilt from closed orders (REBALANCING_PROPOSAL RB3', §2.5).
Synthetic histories; every expected value is worked by hand in the comment beside it."""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.holdings import overseas_gain as og

RATES = {"2026-03-03": 1300.0, "2026-05-05": 1400.0, "2026-06-01": 1000.0, "2026-06-02": 1000.0,
         "2026-06-03": 1000.0, "2025-12-30": 1000.0}


def _order(side, quantity, amount, *, settled, symbol="VT", commission="0", tax="0", filled=None,
           currency="USD"):
    filled = filled or f"{settled}T01:00:00Z"
    return {"side": side, "currency": currency, "symbol": symbol, "orderedAt": filled,
            "execution": {"filledQuantity": str(quantity), "filledAmount": str(amount), "commission": commission,
                          "tax": tax, "filledAt": filled, "settlementDate": settled}}


def _gain(orders, *, held=None, truncated=False, rates=RATES, current=None, year=2026):
    return og.realised(orders, truncated=truncated, year=year, rates=rates, current_rate=current, held=held or {})


def test_one_buy_and_one_full_sell():
    # cost (1000 + 1) × 1300 = 1,301,300; proceeds (1200 − 1.2 − 0.8) × 1400 = 1,677,200; gain 375,900.
    out = _gain([_order("BUY", 10, 1000, settled="2026-03-03", commission="1"),
                 _order("SELL", 10, 1200, settled="2026-05-05", commission="1.2", tax="0.8")])
    assert out.status == "ok" and out.fifo_krw == pytest.approx(375_900) == out.moving_average_krw
    assert out.sells_counted == 1 and out.approx_fx_fills == 0


def test_fifo_and_the_moving_average_differ_after_a_partial_sell():
    # Buys 10 @ 100 and 10 @ 200 (rate 1000); sell 10 for 3000. FIFO cost 1,000,000 → gain 2,000,000;
    # moving-average cost 3,000,000 × ½ = 1,500,000 → gain 1,500,000. Left: 10 sh at an average 150 USD.
    orders = [_order("BUY", 10, 1000, settled="2026-06-01"), _order("BUY", 10, 2000, settled="2026-06-02"),
              _order("SELL", 10, 3000, settled="2026-06-03")]
    out = _gain(orders, held={"VT": (10.0, 150.0)})
    assert out.status == "ok"
    assert out.fifo_krw == pytest.approx(2_000_000) and out.moving_average_krw == pytest.approx(1_500_000)


def test_only_sells_settled_this_year_count():
    # A sell filled on 12-28 settles on 12-30 (2025): last year's. The 2026 sell's gain is 500 × 1000.
    orders = [_order("BUY", 20, 2000, settled="2025-12-30", filled="2025-12-26T01:00:00Z"),
              _order("SELL", 10, 1500, settled="2025-12-30", filled="2025-12-28T01:00:00Z"),
              _order("SELL", 10, 1500, settled="2026-06-03", filled="2026-06-01T01:00:00Z")]
    out = _gain(orders)
    assert out.status == "ok" and out.sells_counted == 1 and out.fifo_krw == pytest.approx(500_000)


def test_a_sell_filled_this_year_but_settled_next_is_not_this_years():
    orders = [_order("BUY", 10, 1000, settled="2026-06-01"),
              _order("SELL", 10, 1500, settled="2027-01-04", filled="2026-12-30T01:00:00Z")]
    assert _gain(orders, rates={**RATES, "2027-01-04": 1000.0}).sells_counted == 0


def test_an_unsettled_sell_takes_its_shares_and_counts_no_gain():
    orders = [_order("BUY", 10, 1000, settled="2026-06-01"),
              _order("SELL", 10, 1500, settled="", filled="2026-06-02T01:00:00Z")]
    out = _gain(orders, current=1000.0)
    assert out.status == "ok" and out.unsettled_sells == 1 and out.sells_counted == 0 and out.fifo_krw == 0


def test_a_day_before_recording_uses_the_current_rate_and_is_counted():
    # The buy's day has no recorded rate: 1000 × 1100 (current). Proceeds 1500 × 1000. Gain 400,000.
    orders = [_order("BUY", 10, 1000, settled="2026-01-02"), _order("SELL", 10, 1500, settled="2026-06-03")]
    out = _gain(orders, current=1100.0)
    assert out.status == "ok" and out.approx_fx_fills == 1 and out.fifo_krw == pytest.approx(400_000)


@pytest.mark.parametrize(("case", "orders", "held", "kwargs"), [
    ("truncated", [_order("BUY", 10, 1000, settled="2026-06-01")], {"VT": (10.0, 100.0)}, {"truncated": True}),
    ("first buy missing", [_order("BUY", 10, 1000, settled="2026-06-01")], {"VT": (20.0, 100.0)}, {}),
    ("sell before any buy", [_order("SELL", 10, 1000, settled="2026-06-01")], {}, {}),
    ("price disagrees", [_order("BUY", 10, 1000, settled="2026-06-01")], {"VT": (10.0, 120.0)}, {}),
    ("held elsewhere no more", [_order("BUY", 10, 1000, settled="2026-06-01")], {}, {}),
    ("no rate at all", [_order("BUY", 10, 1000, settled="2026-01-02")], {"VT": (10.0, 100.0)}, {}),
])
def test_a_doubtful_history_is_refused_never_a_partial_number(case, orders, held, kwargs):
    out = _gain(orders, held=held, **kwargs)
    assert out.status == "refused" and out.fifo_krw is None and out.reason, case


def test_domestic_orders_are_not_part_of_the_overseas_gain():
    orders = [_order("BUY", 10, 1000, settled="2026-06-01"), _order("SELL", 10, 1500, settled="2026-06-03"),
              _order("SELL", 5, 500_000, settled="2026-06-03", symbol="005930", currency="KRW")]
    assert _gain(orders).fifo_krw == pytest.approx(500_000)


# --- the fire's daily rebuild, and what `--rebalance` takes from it -------------------------------------------

import json  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from runtime.mvp_runtime.holdings import store  # noqa: E402
from runtime.mvp_runtime.holdings.model import Holding  # noqa: E402

FIRE = "2026-06-03T03:00:00Z"            # 12:00 KST, 2026-06-03


class _Feed:
    def __init__(self, orders, *, fail=False):
        self.orders, self.fail, self.calls = orders, fail, []

    def closed_orders(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise OSError("read failed")
        return self.orders, False


def _snapshot(rate=1000.0, quantity=10.0, price=100.0):
    held = () if not quantity else (Holding("overseas", "VT", "VT", quantity, 1500.0, 500.0, "USD", price),)
    return SimpleNamespace(usd_krw_rate=rate, holdings=held)


def _estimate(tmp_path):
    return json.loads(store.tax_estimate_path(tmp_path).read_text(encoding="utf-8"))


def test_the_fire_records_the_days_rate_once_and_rebuilds_the_gain_once_a_day(tmp_path):
    orders = [_order("BUY", 20, 2000, settled="2026-06-01"), _order("SELL", 10, 1500, settled="2026-06-03")]
    feed = _Feed(orders)
    store.refresh_tax_estimate(feed, _snapshot(rate=1000.0), now=FIRE, root=tmp_path)
    store.refresh_tax_estimate(feed, _snapshot(rate=1234.0), now="2026-06-03T05:00:00Z", root=tmp_path)
    body = _estimate(tmp_path)
    assert body["fx_rates"] == {"2026-06-03": 1000.0}                     # the first fire of the day
    assert feed.calls == [{"date_from": None, "date_to": None, "max_pages": store.TAX_ESTIMATE_MAX_PAGES}]
    gain = body["overseas_gain"]
    # Buy day 06-01 has no recorded rate: 2000 × 1000 (current) for 20 sh; sell 1500 × 1000. FIFO cost 1,000,000.
    assert gain["status"] == "ok" and gain["fifo_krw"] == 500_000 and gain["computed_on"] == "2026-06-03"
    assert gain["approx_fx_fills"] == 1 and "VT" not in json.dumps(body)       # amounts only, no symbol


def test_a_failed_read_keeps_the_last_block_and_still_records_the_rate(tmp_path):
    store.refresh_tax_estimate(_Feed([_order("BUY", 10, 1000, settled="2026-06-01")]), _snapshot(),
                               now="2026-06-02T03:00:00Z", root=tmp_path)
    store.refresh_tax_estimate(_Feed([], fail=True), _snapshot(rate=1010.0), now=FIRE, root=tmp_path)
    body = _estimate(tmp_path)
    assert body["overseas_gain"]["computed_on"] == "2026-06-02" and body["fx_rates"]["2026-06-03"] == 1010.0


@pytest.mark.parametrize(("block", "used", "said"), [
    ({"status": "ok", "year": 2026, "computed_on": "2026-06-03", "fifo_krw": 700_000, "moving_average_krw": 600_000,
      "sells_counted": 1, "unsettled_sells": 0, "approx_fx_fills": 2}, 700_000.0, "FIFO (method unconfirmed"),
    ({"status": "refused", "year": 2026, "computed_on": "2026-06-03", "reason": "rebuilt quantity disagrees",
      "fifo_krw": None}, None, "refused (rebuilt quantity"),
    ({"status": "ok", "year": 2026, "computed_on": "2026-05-30", "fifo_krw": 700_000}, None, "too old"),
    (None, None, "not computed yet"),
])
def test_rebalance_takes_only_a_fresh_ok_fifo_gain(block, used, said):
    gain, note = store._overseas_gain_for(None if block is None else {"overseas_gain": block}, now=FIRE)
    assert gain == used and said in note
