"""The rebalancing calculator (REBALANCING_PROPOSAL_V0.1 RB1–RB6, Thomas 2026-10-26). Synthetic portfolios only."""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.holdings import allocation as al
from runtime.mvp_runtime.holdings import rebalance as rb
from runtime.mvp_runtime.holdings.rebalance import Position

NAV = 100_000_000.0


def _on_target(**over):
    """Class totals at target (engine margin left out), with ``over`` moving value between classes."""
    krw = {c: NAV * al.TARGET_PCT[c] / 100 for c in rb.PLANNED}
    krw.update(over)
    return krw


def test_inside_every_band_nothing_is_due():
    out = rb.plan(class_krw=_on_target(), positions=[], complete=True)
    assert out.status == "not_due" and out.orders == []


def test_cash_has_no_band_so_cash_alone_off_target_is_not_due():
    # Cash 10 (target 7.5, +2.5pp), bonds 34.5 (−2.5, inside ±5): a cash band of 0.25×7.5 would call this due.
    out = rb.plan(class_krw=_on_target(cash=10_000_000.0, bonds=34_500_000.0), positions=[], complete=True)
    assert out.status == "not_due" and "cash" not in out.drift


def test_a_partial_nav_is_refused_whatever_the_drift():
    out = rb.plan(class_krw=_on_target(bonds=20_000_000.0), positions=[], complete=False)
    assert out.status == "refused" and out.orders == []


def _equity_heavy():
    # equity 46 (band ±5 → outside), bonds 31, the rest on target; NAV stays 100M.
    krw = _on_target(global_equity=36_000_000.0, bonds=31_000_000.0)
    assert sum(krw.values()) == pytest.approx(NAV)
    positions = [
        Position("toss:domestic:KRSTOCK", al.DOMESTIC_EQUITY, 100, 10_000_000.0, 2_000_000.0),
        Position("toss:domestic:KRWORLD", al.GLOBAL_EQUITY, 1_000, 20_000_000.0, 4_000_000.0),
        Position("toss:overseas:USWORLD", al.GLOBAL_EQUITY, 50, 16_000_000.0, 1_000_000.0),
        Position("toss:domestic:KRBOND", al.BONDS, 3_100, 31_000_000.0, 100_000.0),
        Position("toss:domestic:KRGOLD", al.GOLD, 1_300, 13_000_000.0, 500_000.0),
    ]
    return krw, positions


def test_one_class_outside_returns_every_class_to_target():
    krw, positions = _equity_heavy()
    out = rb.plan(class_krw=krw, positions=positions, complete=True,
                  candidates={al.BONDS: ["toss:domestic:KRBOND", "toss:domestic:KRBOND2"]})
    assert out.status == "due" and out.drift["equity"]["outside"]
    assert all(abs(v) < 0.6 for c, v in out.after.items() if c != al.CASH), out.after   # within rounding
    sells = [o for o in out.orders if o["side"] == "SELL"]
    assert sells and all(o["asset_class"] == al.GLOBAL_EQUITY for o in sells)


def test_sells_follow_the_tax_order_cheapest_first():
    krw, positions = _equity_heavy()
    # Make the overseas holding the cheaper one by gain ratio; the domestic-listed foreign ETF is taxed 15.4% (step 4)
    out = rb.plan(class_krw=krw, positions=positions, complete=True, overseas_gain_ytd_krw=0.0,
                  candidates={al.BONDS: ["toss:domestic:KRBOND"]})
    steps = [o["step"] for o in out.orders if o["side"] == "SELL"]
    assert steps == sorted(steps) and steps[0] == rb.STEP[rb.OVERSEAS]      # 3 before 4
    first = next(o for o in out.orders if o["side"] == "SELL")
    assert first["instrument"] == "toss:overseas:USWORLD" and first["est_tax_krw"] == 0   # inside the deduction


def test_unknown_overseas_gain_is_taxed_in_the_estimate_and_said():
    krw, positions = _equity_heavy()
    out = rb.plan(class_krw=krw, positions=positions, complete=True,
                  candidates={al.BONDS: ["toss:domestic:KRBOND"]})
    overseas = next(o for o in out.orders if o["instrument"] == "toss:overseas:USWORLD")
    assert overseas["est_tax_krw"] == round(overseas["gain_krw"] * 0.22)
    assert any("overseas gain is unknown" in n for n in out.notes)


def test_domestic_orders_are_whole_shares_and_overseas_are_amounts():
    krw, positions = _equity_heavy()
    out = rb.plan(class_krw=krw, positions=positions, complete=True, overseas_gain_ytd_krw=0.0,
                  candidates={al.BONDS: ["toss:domestic:KRBOND"]})
    for order in out.orders:
        if order["whole_shares"] and order["quantity"] is not None:
            assert float(order["quantity"]).is_integer()
    overseas = [o for o in out.orders if o["instrument"].startswith("toss:overseas")]
    assert overseas and not overseas[0]["whole_shares"]


def test_a_domestic_sell_and_buy_round_down_to_whole_shares_at_an_uneven_price():
    # Only domestic holdings, at prices that do not divide the amounts: 7,000 sh for 36M, 3,000 sh for 31M.
    krw = _on_target(global_equity=36_000_000.0, bonds=31_000_000.0)
    positions = [Position("toss:domestic:KRWORLD", al.GLOBAL_EQUITY, 7_000, 36_000_000.0, 0.0),
                 Position("toss:domestic:KRBOND", al.BONDS, 3_000, 31_000_000.0, 0.0)]
    out = rb.plan(class_krw=krw, positions=positions, complete=True, candidates={al.BONDS: ["toss:domestic:KRBOND"]})
    sell = next(o for o in out.orders if o["side"] == "SELL")
    buy = next(o for o in out.orders if o["side"] == "BUY")
    assert sell["quantity"] == 6_000_000.0 // (36_000_000.0 / 7_000)                 # 1,166 sh, not 1,166.67
    assert sell["amount_krw"] == round(sell["quantity"] * 36_000_000.0 / 7_000) < 6_000_000
    assert buy["quantity"] == 6_000_000.0 // (31_000_000.0 / 3_000)                  # 580 sh
    assert all(o["whole_shares"] for o in (sell, buy))


def test_orders_under_50000_are_carried_not_placed():
    # equity 45.5 (outside), gold 40,000 short of target (under the minimum), cash takes up the rest.
    krw = _on_target(global_equity=35_500_000.0, gold=12_960_000.0, cash=2_040_000.0)
    assert sum(krw.values()) == pytest.approx(NAV)
    positions = [Position("toss:domestic:KRWORLD", al.GLOBAL_EQUITY, 1_000, 35_500_000.0, 0.0),
                 Position("toss:domestic:KRGOLD", al.GOLD, 1_296, 12_960_000.0, 0.0)]
    out = rb.plan(class_krw=krw, positions=positions, complete=True,
                  candidates={al.GOLD: ["toss:domestic:KRGOLD"]})
    assert out.carried and all(o["amount_krw"] < rb.MIN_ORDER_KRW for o in out.carried)
    assert all(o["amount_krw"] >= rb.MIN_ORDER_KRW for o in out.orders)


def test_buys_spread_across_the_approved_candidates_smallest_first():
    krw = _on_target(global_equity=36_000_000.0, bonds=31_000_000.0)
    positions = [Position("toss:domestic:KRWORLD", al.GLOBAL_EQUITY, 1_000, 36_000_000.0, 0.0),
                 Position("toss:domestic:BONDA", al.BONDS, 2_600, 26_000_000.0, 0.0),
                 Position("toss:domestic:BONDB", al.BONDS, 500, 5_000_000.0, 0.0)]
    out = rb.plan(class_krw=krw, positions=positions, complete=True,
                  candidates={al.BONDS: ["toss:domestic:BONDA", "toss:domestic:BONDB"]})
    bond_buys = [o for o in out.orders if o["side"] == "BUY" and o["asset_class"] == al.BONDS]
    assert [o["instrument"] for o in bond_buys] == ["toss:domestic:BONDB"]      # the smaller one is filled


def test_a_class_without_approved_candidates_is_not_bought():
    krw, positions = _equity_heavy()
    out = rb.plan(class_krw=krw, positions=positions, complete=True)
    assert al.BONDS in out.not_bought and not any(o["side"] == "BUY" and o["asset_class"] == al.BONDS
                                                    for o in out.orders)


def test_coin_is_manual_and_the_engine_is_left_out():
    krw = _on_target(coin=6_000_000.0, cash=4_000_000.0)
    krw[al.ENGINE_MARGIN] = 9_000_000.0
    out = rb.plan(class_krw=krw, positions=[Position("binance:BTC", al.COIN, 0.05, 6_000_000.0, 0.0)],
                  complete=True)
    assert "engine_margin" not in out.drift and out.nav_krw == pytest.approx(sum(
        v for c, v in krw.items() if c != al.ENGINE_MARGIN))
    coin = [o for o in out.orders if o["asset_class"] == al.COIN]
    assert coin and all(o["manual"] for o in coin)


@pytest.mark.parametrize(("instrument", "asset_class", "expected"), [
    ("toss:domestic:A", al.DOMESTIC_EQUITY, rb.TAX_FREE),
    ("toss:domestic:A", al.GLOBAL_EQUITY, rb.TAXED_DIVIDEND),
    ("toss:domestic:A", al.BONDS, rb.TAXED_DIVIDEND),
    ("toss:domestic:A", al.GOLD, rb.TAXED_DIVIDEND),
    ("toss:overseas:A", al.GLOBAL_EQUITY, rb.OVERSEAS),
])
def test_tax_kind_follows_market_and_class(instrument, asset_class, expected):
    assert rb.tax_kind(Position(instrument, asset_class, 1, 1.0, 0.0)) == expected


def test_an_override_names_the_tax_kind():
    position = Position("toss:domestic:A", al.GLOBAL_EQUITY, 1, 1.0, 0.0)
    assert rb.tax_kind(position, {"toss:domestic:A": rb.TAX_FREE}) == rb.TAX_FREE


# --- wiring: the local positions file, Thomas's candidates, the terminal render -----------------------------

import json  # noqa: E402

from runtime.mvp_runtime.errors import ToolError  # noqa: E402
from runtime.mvp_runtime.holdings import classification as cl  # noqa: E402
from runtime.mvp_runtime.holdings import store  # noqa: E402

AS_OF = "2026-10-26T09:00:00Z"
NOW = "2026-10-26T09:30:00Z"


def _local(tmp_path, *, as_of=AS_OF, positions_as_of=AS_OF, complete=True, global_equity=36_000_000.0,
           bonds=31_000_000.0):
    krw = _on_target(global_equity=global_equity, bonds=bonds)
    classes = {name: {"krw": krw.get(name, 0.0)} for name in al.CLASSES}
    state = store.state_dir(tmp_path)
    state.mkdir(parents=True, exist_ok=True)
    store.local_path(tmp_path).write_text(json.dumps({"as_of": as_of, "allocation": {
        "complete": complete, "classes": classes}}), encoding="utf-8")
    store.positions_path(tmp_path).write_text(json.dumps({"as_of": positions_as_of, "positions": [
        {"instrument": "toss:domestic:WORLD", "asset_class": al.GLOBAL_EQUITY, "quantity": 3600,
         "value_krw": global_equity, "pnl_krw": 1_000_000.0},
        {"instrument": "toss:domestic:BOND", "asset_class": al.BONDS, "quantity": 3100, "value_krw": bonds,
         "pnl_krw": 0.0}]}), encoding="utf-8")


def test_the_terminal_render_carries_the_plan_over_one_fire(tmp_path):
    _local(tmp_path)
    cl.set_candidates(store.state_dir(tmp_path), al.BONDS, ["toss:domestic:BOND"], now=NOW)
    text = store.load_rebalance(now=NOW, root=tmp_path)
    assert text.startswith("REBALANCE - terminal only") and "rebalance due" in text
    assert "SELL toss:domestic:WORLD" in text and "BUY  toss:domestic:BOND" in text


@pytest.mark.parametrize("case", ["positions_from_another_fire", "partial", "stale"])
def test_the_render_refuses_rather_than_plan_on_a_doubtful_fire(tmp_path, case):
    _local(tmp_path, positions_as_of="2026-10-26T08:00:00Z" if case == "positions_from_another_fire" else AS_OF,
           complete=case != "partial", as_of="2026-10-26T02:00:00Z" if case == "stale" else AS_OF)
    if case == "stale":
        store.positions_path(tmp_path).write_text(store.positions_path(tmp_path).read_text().replace(
            AS_OF, "2026-10-26T02:00:00Z"), encoding="utf-8")
    text = store.load_rebalance(now=NOW, root=tmp_path)
    assert "refused" in text and "SELL" not in text


def test_candidates_are_kept_beside_the_entries_and_both_survive_each_other(tmp_path):
    state = store.state_dir(tmp_path)
    cl.apply(state, assign={"toss:domestic:BOND": "bonds"}, now=NOW)
    cl.set_candidates(state, al.BONDS, ["toss:domestic:BOND", "toss:domestic:BOND2"], now=NOW)
    cl.apply(state, assign={"toss:overseas:WORLD": "global_equity"}, now=NOW)
    loaded = cl.load(state)
    assert loaded.candidates == {al.BONDS: ["toss:domestic:BOND", "toss:domestic:BOND2"]}
    assert set(loaded.entries) == {"toss:domestic:BOND", "toss:overseas:WORLD"}
    cl.set_candidates(state, al.BONDS, [], now=NOW)
    assert cl.load(state).candidates == {} and set(cl.load(state).entries) == set(loaded.entries)


@pytest.mark.parametrize(("asset_class", "ids"), [
    (al.BONDS, ["toss:domestic:A", "toss:domestic:B", "toss:domestic:C", "toss:domestic:D", "toss:domestic:E"]),
    (al.BONDS, ["toss:domestic:A", "toss:domestic:A"]),
    (al.BONDS, ["binance:BTC"]),
    (al.CASH, ["toss:domestic:A"]),
    (al.COIN, ["toss:domestic:A"]),
])
def test_a_candidate_list_outside_the_rule_is_refused_before_writing(tmp_path, asset_class, ids):
    state = store.state_dir(tmp_path)
    with pytest.raises(ToolError) as refused:
        cl.set_candidates(state, asset_class, ids, now=NOW)
    assert refused.value.reason_code == cl.CLASSIFICATION_REFUSED and not cl.path(state).exists()
