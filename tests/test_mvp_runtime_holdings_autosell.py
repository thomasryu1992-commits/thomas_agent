"""G6-sell (Thomas 2026-11-11): the Toss sell adapter, the drafted sell scenario and its executor.
No network: a fake urlopen for the adapter, fake feeds and adapters for the rest. Every clock is handed in."""

from __future__ import annotations

import io
import json
import time
import urllib.error
import urllib.parse

import pytest

from runtime.mvp_runtime import operator as operator_mod
from runtime.mvp_runtime import scheduler
from runtime.mvp_runtime.errors import OperatorBlocked, SafetyGateBlocked, ToolError
from runtime.mvp_runtime.holdings import allocation as al
from runtime.mvp_runtime.holdings import autoinvest as ai
from runtime.mvp_runtime.holdings import autoinvest_proposal as ap
from runtime.mvp_runtime.holdings import autosell as sell
from runtime.mvp_runtime.holdings import classification, rebalance, store, toss_account, toss_orders

DOM, KR3, US10, GOLD, WORLD = ("toss:domestic:069500", "toss:domestic:114260", "toss:domestic:308620",
                               "toss:domestic:411060", "toss:overseas:VT")
CANDIDATES = {al.GLOBAL_EQUITY: [WORLD], al.DOMESTIC_EQUITY: [DOM], al.BONDS: [KR3, US10], al.GOLD: [GOLD]}
# NAV 10,000,000 at target: global 3.0M, domestic 1.0M, bonds 3.7M, gold 1.3M, coin 0.25M, cash 0.75M.
AT_TARGET = {al.GLOBAL_EQUITY: 3_000_000.0, al.DOMESTIC_EQUITY: 1_000_000.0, al.BONDS: 3_700_000.0,
             al.GOLD: 1_300_000.0, al.COIN: 250_000.0, al.CASH: 750_000.0}
# Gold 700k over (outside its band), bonds 700k under.
GOLD_OVER = {**AT_TARGET, al.GOLD: 2_000_000.0, al.BONDS: 3_000_000.0}
GOLD_HELD = [{"instrument": GOLD, "asset_class": al.GOLD, "quantity": 100, "value_krw": 2_000_000.0,
              "pnl_krw": 200_000.0}]
PRICES = {"069500": 40_000.0, "114260": 60_000.0, "308620": 10_000.0, "411060": 20_000.0}
EARLY = "2026-11-30T23:30:00Z"                   # 08:30 KST on 2026-12-01, before the session
OPEN = "2026-12-01T01:00:00Z"                    # 10:00 KST, Tuesday: December's first weekday
LATER = "2026-12-01T02:00:00Z"
NEXT_DAY = "2026-12-02T01:00:00Z"
WEEKEND = "2026-11-01T01:00:00Z"                 # a Sunday: November's check day is Monday the 2nd


# --- the adapter ------------------------------------------------------------------------------------------------

def _toss_feed():
    feed = toss_account.TossHoldingsFeed.__new__(toss_account.TossHoldingsFeed)
    feed._authorization, feed._account = None, (7, "12345678901")
    feed._token = ("TOKEN-A", time.monotonic() + 3600)
    feed._issue_token = lambda *, timeout_seconds: "TOKEN-B"
    return feed


class _Http:
    """Answers each POST in turn: a dict is a 200 body, a (status, code) pair an HTTP error."""

    def __init__(self, *answers):
        self.answers, self.sent = list(answers), []

    def __call__(self, request, timeout=None):
        self.sent.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, tuple):
            status, code = answer
            raise urllib.error.HTTPError(request.full_url, status, "x", {},
                                         io.BytesIO(json.dumps({"error": {"code": code}}).encode()))
        return _Body(answer)


class _Body(io.BytesIO):
    def __init__(self, body):
        super().__init__(json.dumps(body).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _gate(monkeypatch, value, http=None):
    if value is None:
        monkeypatch.delenv(toss_orders.TOSS_ORDERS_ENV, raising=False)
    else:
        monkeypatch.setenv(toss_orders.TOSS_ORDERS_ENV, value)
    http = http or _Http()
    monkeypatch.setattr(toss_orders.urllib.request, "urlopen", http)
    return http


@pytest.mark.parametrize("value", [None, "", "buy_only", "sell", "sell_only", "buy_sell_", "real"])
def test_only_buy_sell_builds_a_sell_adapter_and_nothing_else_can_post_a_sell(monkeypatch, value):
    http = _gate(monkeypatch, value)
    orders = toss_orders.select_sell_orders(_toss_feed())
    assert isinstance(orders, toss_orders.NoSellOrders) and not orders.enabled
    with pytest.raises(ToolError) as refused:
        orders.sell_kr("069500", 1, price=40_000, client_order_id="c1")
    assert refused.value.reason_code == toss_orders.ORDER_REFUSED and http.sent == []


def test_under_buy_only_the_buy_adapter_has_no_sell(monkeypatch):
    _gate(monkeypatch, "buy_only")
    orders = toss_orders.select_buy_orders(_toss_feed())
    assert isinstance(orders, toss_orders.TossBuyOrders) and not hasattr(orders, "sell_kr")
    assert sorted(n for n in dir(toss_orders.TossSellOrders) if not n.startswith("_")) == ["enabled", "sell_kr"]
    assert sorted(n for n in dir(toss_orders.NoSellOrders) if not n.startswith("_")) == ["enabled", "sell_kr"]


def test_the_one_sell_post_is_a_limit_day_sell_of_whole_domestic_shares(monkeypatch):
    http = _gate(monkeypatch, "buy_sell", _Http({"result": {"orderId": "OID-S"}}))
    orders = toss_orders.select_sell_orders(_toss_feed())
    assert orders.sell_kr("411060", 3, price=19_990, client_order_id="sord_x") == "OID-S"
    (request,) = http.sent
    assert request.get_method() == "POST" and urllib.parse.urlparse(request.full_url).path == toss_account.ORDERS_PATH
    assert json.loads(request.data) == {"clientOrderId": "sord_x", "symbol": "411060", "side": "SELL",
                                        "orderType": "LIMIT", "timeInForce": "DAY", "quantity": "3", "price": "19990"}


def test_buy_sell_keeps_the_buy_adapter_working(monkeypatch):
    http = _gate(monkeypatch, "buy_sell", _Http({"result": {"orderId": "OID-B"}}))
    orders = toss_orders.select_buy_orders(_toss_feed())
    assert isinstance(orders, toss_orders.TossBuyOrders)
    assert orders.buy_kr("069500", 1, price=40_000, client_order_id="c1") == "OID-B"
    assert json.loads(http.sent[0].data)["side"] == "BUY"


@pytest.mark.parametrize(("built", "now", "select", "call"), [
    ("buy_sell", "buy_only", toss_orders.select_sell_orders, "sell_kr"),     # the sell side is withdrawn
    ("buy_sell", None, toss_orders.select_sell_orders, "sell_kr"),
    ("buy_sell", "buy_only", toss_orders.select_buy_orders, "buy_kr"),       # a changed value takes a restart
    ("buy_only", "buy_sell", toss_orders.select_buy_orders, "buy_kr"),
])
def test_a_value_changed_under_a_running_adapter_is_refused_at_the_post(monkeypatch, built, now, select, call):
    http = _gate(monkeypatch, built, _Http({"result": {"orderId": "OID-1"}}))
    orders = select(_toss_feed())
    assert orders.enabled
    _gate(monkeypatch, now, http)
    with pytest.raises(SafetyGateBlocked):
        getattr(orders, call)("069500", 1, price=40_000, client_order_id="c1")
    assert http.sent == []


def test_each_adapter_posts_its_own_side_only(monkeypatch):
    http = _gate(monkeypatch, "buy_sell", _Http({"result": {"orderId": "OID-1"}}, {"result": {"orderId": "OID-2"}}))
    feed = _toss_feed()
    body = {"clientOrderId": "c", "symbol": "069500", "orderType": "LIMIT", "timeInForce": "DAY", "quantity": "1",
            "price": "1"}
    for orders, other in ((toss_orders.select_buy_orders(feed), "SELL"), (toss_orders.select_sell_orders(feed), "BUY")):
        with pytest.raises(ToolError) as refused:
            orders._send({**body, "side": other}, timeout_seconds=1)
        assert refused.value.reason_code == toss_orders.ORDER_REFUSED
    assert http.sent == []


def test_a_buy_authorization_opens_no_sell(monkeypatch):
    http = _gate(monkeypatch, "buy_sell", _Http({"result": {"orderId": "OID-1"}}))
    feed = _toss_feed()
    buy = toss_orders.select_buy_orders(feed)
    forged = toss_orders.TossSellOrders(buy._authorization, feed)
    with pytest.raises(SafetyGateBlocked):
        forged.sell_kr("069500", 1, price=40_000, client_order_id="c1")
    assert http.sent == []


@pytest.mark.parametrize(("symbol", "quantity", "price"), [("069500", 0, 1), ("069500", 1.5, 1), ("069500", True, 1),
                                                           ("VT", 1, 1), ("069500", 1, 0), ("069500", 1, 10.5)])
def test_a_sell_is_whole_shares_of_a_domestic_code_at_a_whole_price(monkeypatch, symbol, quantity, price):
    http = _gate(monkeypatch, "buy_sell")
    with pytest.raises(ToolError) as refused:
        toss_orders.select_sell_orders(_toss_feed()).sell_kr(symbol, quantity, price=price, client_order_id="c1")
    assert refused.value.reason_code == toss_orders.ORDER_REFUSED and http.sent == []


def test_a_rejected_sell_is_told_apart_from_an_unknown_one(monkeypatch):
    _gate(monkeypatch, "buy_sell", _Http((422, "insufficient-quantity"), (500, "")))
    orders = toss_orders.select_sell_orders(_toss_feed())
    with pytest.raises(toss_orders.OrderRejected) as rejected:
        orders.sell_kr("069500", 1, price=40_000, client_order_id="c1")
    assert rejected.value.toss_code == "insufficient-quantity"
    with pytest.raises(toss_orders.OrderUnknown):
        orders.sell_kr("069500", 1, price=40_000, client_order_id="c2")


def test_the_feed_reads_the_bid_side_highest_first_and_the_shares_held(monkeypatch):
    feed = _toss_feed()
    answers = {toss_account.ORDERBOOK_PATH: {"asks": [{"price": "101", "volume": "5"}],
                                             "bids": [{"price": "99", "volume": "3"}, {"price": "100", "volume": "2"},
                                                      {"price": "x", "volume": "1"}]},
               toss_account.HOLDINGS_PATH: {"items": [{"marketCountry": "KR", "symbol": "069500", "quantity": "7"},
                                                      {"marketCountry": "US", "symbol": "VT", "quantity": "1.5"},
                                                      {"marketCountry": "KR", "symbol": "411060", "quantity": None}]}}
    monkeypatch.setattr(feed, "_get", lambda path, *a, **kw: answers[path])
    monkeypatch.setattr(feed, "_resolve_account", lambda **_kw: (7, "x"))
    assert feed.bids("069500") == [(100.0, 2.0), (99.0, 3.0)]
    assert feed.domestic_quantities() == {"069500": 7.0}
    answers[toss_account.ORDERBOOK_PATH] = {"asks": [{"price": "101", "volume": "5"}]}
    assert feed.bids("069500") == []                     # no bid side named: an empty side, which holds the day


# --- the draft: the plan's sell lines this path may place ---------------------------------------------------------

def _plan(class_krw=GOLD_OVER, positions=GOLD_HELD, tax_overrides=None):
    rows = [rebalance.Position(p["instrument"], p["asset_class"], p["quantity"], p["value_krw"], p["pnl_krw"])
            for p in positions]
    return rebalance.plan(class_krw=class_krw, positions=rows, complete=True, candidates=CANDIDATES,
                          tax_overrides=tax_overrides, overseas_gain_ytd_krw=0.0)


def _slots(class_krw=GOLD_OVER, positions=GOLD_HELD, currencies=None):
    return ap.slots(class_krw=class_krw, positions=positions, currencies=currencies or {}, candidates=CANDIDATES,
                    us_bonds=[US10])[0]


def test_a_sell_line_is_cut_to_the_days_cap_and_its_gain_and_tax_are_those_of_what_is_kept():
    plan = _plan()
    (line,) = [o for o in plan.orders if o["side"] == "SELL"]
    assert (line["quantity"], line["amount_krw"], line["tax_kind"]) == (35, 700_000, rebalance.TAXED_DIVIDEND)
    result = sell.draft(plan, slot_rows=_slots())
    (item,) = result["items"]
    # 485,000 of room at 20,000 a share: 24 shares. A tenth of it is gain, taxed at 15.4%.
    assert (item["instrument"], item["quantity"], item["est_krw"]) == (GOLD, 24, 480_000)
    assert (item["gain_krw"], item["est_tax_krw"], item["tax_kind"]) == (48_000, 7_392, rebalance.TAXED_DIVIDEND)
    assert result["estimate_krw"] == 480_000 <= sell.DAILY_SELL_CAP_KRW and result["left_out"]["over_cap"] == 1
    assert result["status"] == "drafted"


def test_a_tax_free_line_comes_first_and_the_cap_is_shared_in_the_plans_order():
    classes = {**AT_TARGET, al.DOMESTIC_EQUITY: 1_300_000.0, al.GOLD: 2_000_000.0, al.BONDS: 2_700_000.0}
    held = GOLD_HELD + [{"instrument": DOM, "asset_class": al.DOMESTIC_EQUITY, "quantity": 32.5,
                         "value_krw": 1_300_000.0, "pnl_krw": 130_000.0}]
    result = sell.draft(_plan(classes, held), slot_rows=_slots(classes, held))
    assert [(i["instrument"], i["quantity"], i["tax_kind"], i["est_tax_krw"]) for i in result["items"]] == [
        (DOM, 7, rebalance.TAX_FREE, 0),                 # 300k over at 40,000: 7 whole shares, tax-free, step 2
        (GOLD, 10, rebalance.TAXED_DIVIDEND, 3_080)]     # what the cap has left: 205k, 10 shares
    assert result["estimate_krw"] == 480_000


def test_overseas_and_manual_lines_stay_thomas_whatever_tax_kind_is_named():
    classes = {**AT_TARGET, al.GLOBAL_EQUITY: 4_000_000.0, al.COIN: 1_000_000.0, al.BONDS: 1_950_000.0}
    held = [{"instrument": WORLD, "asset_class": al.GLOBAL_EQUITY, "quantity": 20.0, "value_krw": 4_000_000.0,
             "pnl_krw": 0.0}]
    plan = _plan(classes, held, tax_overrides={WORLD: rebalance.TAX_FREE})
    sells = [o for o in plan.orders if o["side"] == "SELL"]
    assert {o["instrument"] for o in sells} == {WORLD, f"{al.COIN}:manual"}
    assert next(o for o in sells if o["instrument"] == WORLD)["tax_kind"] == rebalance.TAX_FREE
    result = sell.draft(plan, slot_rows=_slots(classes, held))
    assert result["items"] == [] and result["status"] == "nothing_to_sell"
    assert (result["left_out"]["overseas"], result["left_out"]["manual"]) == (1, 1)


def test_a_domestic_holding_named_as_an_overseas_tax_kind_is_left_out():
    result = sell.draft(_plan(tax_overrides={GOLD: rebalance.OVERSEAS}), slot_rows=_slots())
    assert result["items"] == [] and result["left_out"]["tax_kind"] >= 1


BONDS_OVER = {**AT_TARGET, al.BONDS: 4_500_000.0, al.GOLD: 500_000.0}
OTHER_KR = "toss:domestic:148070"                 # a Korean bond that is not the slot's candidate


def _bonds(kr=KR3, kr_pnl=-50_000.0, us_pnl=100_000.0):
    return [{"instrument": kr, "asset_class": al.BONDS, "quantity": 40, "value_krw": 2_000_000.0, "pnl_krw": kr_pnl},
            {"instrument": US10, "asset_class": al.BONDS, "quantity": 250, "value_krw": 2_500_000.0, "pnl_krw": us_pnl}]


@pytest.mark.parametrize("kr", [KR3, OTHER_KR])
def test_a_sell_never_takes_a_buy_slot_below_its_target(kr):
    """Bonds are over as a class, but the Korean slot is under its three quarters, so the buy draft is filling
    it. The plan sells the Korean bond (the smaller gain ratio). The sell side yields, whether the bond is the
    slot's candidate or another bond the slot holds."""
    held = _bonds(kr)
    plan = _plan(BONDS_OVER, held)
    assert [o["instrument"] for o in plan.orders if o["side"] == "SELL"] == [kr]
    slots = _slots(BONDS_OVER, held, currencies={US10: "usd"})
    assert {s["key"] for s in slots if s["asset_class"] == al.BONDS} == {"bonds_kr3y", sell.US_BOND_SLOT}
    kr_slot = next(s for s in slots if s["key"] == "bonds_kr3y")
    assert kr_slot["held_krw"] < kr_slot["target_krw"]
    result = sell.draft(plan, slot_rows=slots, currencies={US10: "usd"})
    assert result["items"] == [] and result["left_out"]["kept_for_buy_slot"] == 1


def test_the_slot_over_its_target_is_the_one_that_sells():
    held = _bonds(kr_pnl=100_000.0, us_pnl=-50_000.0)             # now the US bond has the smaller gain ratio
    plan = _plan(BONDS_OVER, held)
    assert [o["instrument"] for o in plan.orders if o["side"] == "SELL"] == [US10]
    slots = _slots(BONDS_OVER, held, currencies={US10: "usd"})
    result = sell.draft(plan, slot_rows=slots, currencies={US10: "usd"})
    # The US slot holds 2.5M against 925k: the plan's 800k fits it, and the day's cap cuts it to 48 shares.
    assert [(i["instrument"], i["quantity"], i["est_krw"]) for i in result["items"]] == [(US10, 48, 480_000)]
    # Without Thomas's USD entry the bond counts in the Korean slot, which is under target: it is kept.
    assert sell.draft(plan, slot_rows=slots)["items"] == []


def test_a_coin_alone_breach_drafts_no_toss_sell():
    """Q2 execution B: BTC alone outside its band, gold 1pp over target inside its band. Before B the plan sold
    the gold; now the only line is the manual coin one, which this path never places."""
    classes = {**AT_TARGET, al.COIN: 360_000.0, al.GOLD: 1_400_000.0, al.CASH: 640_000.0}
    plan = _plan(classes, [{**GOLD_HELD[0], "quantity": 70, "value_krw": 1_400_000.0}])
    assert plan.scope == "coin_only"
    result = sell.draft(plan, slot_rows=_slots(classes, GOLD_HELD))
    assert result["items"] == [] and result["left_out"]["manual"] == 1


def test_a_line_under_fifty_thousand_is_not_placed():
    classes = {**AT_TARGET, al.GOLD: 2_000_000.0, al.DOMESTIC_EQUITY: 1_460_000.0, al.BONDS: 2_540_000.0}
    held = GOLD_HELD + [{"instrument": DOM, "asset_class": al.DOMESTIC_EQUITY, "quantity": 36.5,
                         "value_krw": 1_460_000.0, "pnl_krw": 0.0}]
    result = sell.draft(_plan(classes, held), slot_rows=_slots(classes, held))
    # Domestic takes 440k (11 shares); 45k is left for gold: two shares, 40k, under RB4's minimum.
    assert [(i["instrument"], i["quantity"]) for i in result["items"]] == [(DOM, 11)]
    assert result["left_out"]["over_cap"] == 1


# --- the fire's half of drafting ------------------------------------------------------------------------------------

class _Feed:
    """A Toss stand-in. The bid book has one level per symbol at ``bid`` (default: the last price) with ``depth``
    shares; ``fills`` maps an order id to what ``fill_state`` answers; ``held`` is the shares held now."""

    def __init__(self, session=True, status=None, kind="ETF", bid=None, depth=1_000, fills=None, books=None,
                 held=None, prices=None):
        self.session, self.status, self.kind, self.bid = session, status, kind, bid or {}
        self.depth, self.fills, self.books, self.prices = depth, fills or {}, books or {}, prices or PRICES
        self.held = {"411060": 100.0, "069500": 30.0} if held is None else held
        self.reads = []

    def kr_market_day(self, date, **_):
        self.reads.append("calendar")
        regular = {"startTime": "09:00:00", "endTime": "15:30:00"} if self.session else None
        return {"date": date, "integrated": {"regularMarket": regular} if regular else None}

    def stock_info(self, symbols, **_):
        return [{"symbol": s, "name": f"ETF {s}", "status": (self.status or {}).get(s, "ACTIVE"),
                 "securityType": self.kind} for s in symbols]

    def last_prices(self, symbols, **_):
        return {s: self.prices[s] for s in symbols if s in self.prices}

    def bids(self, symbol, **_):
        if symbol in self.books:
            return self.books[symbol]
        return [(self.bid.get(symbol, self.prices[symbol]), self.depth)]

    def domestic_quantities(self, **_):
        return dict(self.held)

    def fill_state(self, order_id, **_):
        return self.fills.get(order_id, {"status": "PENDING", "filled": 0})


def _refresh(state, feed=None, now=OPEN, plan=None, slot_rows=None):
    return sell.refresh(feed or _Feed(), state, now=now, plan=plan or _plan(),
                        slot_rows=_slots() if slot_rows is None else slot_rows, as_of=now)


def test_the_months_check_drafts_once_with_a_counts_only_notice(tmp_path):
    assert _refresh(tmp_path) == "drafted"
    body = sell.read_proposal(tmp_path)
    assert body["checked_month"] == "2026-12" and body["items"][0]["name"] == "ETF 411060"
    token, text = sell.proposal_notice(tmp_path)
    assert "1종목 24주" in text and "--autosell-approve" in text
    assert not any(x in text for x in ("411060", "toss:", "ETF", "480", "20,000", "gold", "금"))
    sell.mark_proposal_told(tmp_path, token, now=OPEN)
    assert sell.proposal_notice(tmp_path) is None
    # The month is spent: nothing is drafted again, even on another trading day.
    assert _refresh(tmp_path, now=NEXT_DAY) == "checked_this_month"
    assert sell.read_proposal(tmp_path)["made_at"] == OPEN


def test_a_month_with_no_band_outside_is_checked_and_says_nothing(tmp_path):
    assert _refresh(tmp_path, plan=_plan(AT_TARGET, [])) == "not_due"
    assert sell.read_proposal(tmp_path)["verdict"] == "not_due" and sell.proposal_notice(tmp_path) is None
    # A band that leaves mid-month waits for the next monthly check (the decided rule checks monthly).
    assert _refresh(tmp_path, now=NEXT_DAY) == "checked_this_month"


def _failing_calendar():
    feed = _Feed()

    def fail(*_a, **_kw):
        raise ToolError("PROVIDER_UNAVAILABLE", "down")

    feed.kr_market_day = fail
    return feed


@pytest.mark.parametrize(("kwargs", "word"), [
    ({"plan": rebalance.Plan(status="refused", reason="stale")}, "plan_refused"),
    ({"feed": _Feed(session=False)}, "not_trading_day"),
    ({"feed": _failing_calendar()}, None),
])
def test_only_a_verdict_spends_the_month(tmp_path, kwargs, word):
    if word is None:
        with pytest.raises(ToolError):
            _refresh(tmp_path, **kwargs)
    else:
        assert _refresh(tmp_path, **kwargs) == word
    assert not sell.proposal_path(tmp_path).exists()
    assert _refresh(tmp_path, now=NEXT_DAY) == "drafted"           # the next fire that can judge still drafts


def test_nothing_is_drafted_beside_an_active_sell_scenario(tmp_path):
    _refresh(tmp_path)
    sell.approve(tmp_path, now=LATER, positions=GOLD_HELD)
    sell.proposal_path(tmp_path).unlink()
    assert _refresh(tmp_path, now=NEXT_DAY) == "scenario_active" and not sell.proposal_path(tmp_path).exists()


# --- Thomas's approval ----------------------------------------------------------------------------------------------

def test_the_draft_becomes_thomas_scenario_once(tmp_path):
    _refresh(tmp_path)
    scenario = sell.approve(tmp_path, now=LATER, positions=GOLD_HELD)
    assert scenario["status"] == sell.PENDING and scenario["max_krw"] == sell.DAILY_SELL_CAP_KRW
    assert [(i["instrument"], i["quantity"]) for i in scenario["items"]] == [(GOLD, 24)]
    assert sell._read(tmp_path)["scenario_id"] == scenario["scenario_id"]
    with pytest.raises(ToolError, match="already"):
        sell.approve(tmp_path, now=LATER, positions=GOLD_HELD)
    shown = "\n".join(sell.render(tmp_path))
    assert "SELL x24" in shown and "est. tax 7,392" in shown and "approved    : already" in shown


def test_a_missing_stale_or_unbacked_draft_is_refused(tmp_path):
    with pytest.raises(ToolError, match="no drafted"):
        sell.approve(tmp_path, now=LATER, positions=GOLD_HELD)
    _refresh(tmp_path)
    with pytest.raises(ToolError, match="older than 4 days"):
        sell.approve(tmp_path, now="2026-12-05T01:00:01Z", positions=GOLD_HELD)
    with pytest.raises(ToolError, match="no positions"):
        sell.approve(tmp_path, now=LATER, positions=None)
    with pytest.raises(ToolError, match="more shares than"):
        sell.approve(tmp_path, now=LATER, positions=[{**GOLD_HELD[0], "quantity": 23.9}])
    assert sell._read(tmp_path) == {}


@pytest.mark.parametrize("change", [{"instrument": WORLD}, {"quantity": 1.5}, {"quantity": 0},
                                    {"tax_kind": rebalance.OVERSEAS}, {"est_krw": 500_001}])
def test_a_draft_file_that_is_not_a_domestic_whole_share_sell_inside_the_cap_is_refused(tmp_path, change):
    _refresh(tmp_path)
    body = sell.read_proposal(tmp_path)
    body["items"][0].update(change)
    sell.proposal_path(tmp_path).write_text(json.dumps(body), encoding="utf-8")
    held = GOLD_HELD + [{"instrument": WORLD, "asset_class": al.GLOBAL_EQUITY, "quantity": 50, "value_krw": 1.0,
                         "pnl_krw": 0.0}]
    with pytest.raises(ToolError) as refused:
        sell.approve(tmp_path, now=LATER, positions=held)
    assert refused.value.reason_code == sell.AUTOSELL_REFUSED and sell._read(tmp_path) == {}


# --- the executor ---------------------------------------------------------------------------------------------------

class _Orders:
    enabled = True

    def __init__(self, fail_at=None, error=None):
        self.calls, self.fail_at, self.error = [], fail_at, error

    def sell_kr(self, symbol, quantity, *, price, client_order_id, **_):
        self.calls.append((symbol, quantity, price, client_order_id))
        if self.fail_at is not None and len(self.calls) - 1 == self.fail_at:
            raise self.error
        return f"OID-{symbol}-{len(self.calls)}"


class _Off:
    enabled = False


def _approved(tmp_path, plan=None, slot_rows=None, positions=GOLD_HELD):
    _refresh(tmp_path, plan=plan, slot_rows=slot_rows)
    return sell.approve(tmp_path, now=OPEN, positions=positions)


def _run(tmp_path, feed=None, orders=None, now=OPEN, buy=None):
    return sell.run(feed or _Feed(), orders if orders is not None else _Orders(), tmp_path, now=now,
                    buy_scenario={} if buy is None else buy)["status"]


def _filled(orders, status="FILLED", filled=None):
    return {f"OID-{c[0]}-{n}": {"status": status, "filled": c[1] if filled is None else filled}
            for n, c in enumerate(orders.calls, 1)}


def test_the_scenario_is_placed_as_limit_sells_at_the_bid_and_closes_when_all_fill(tmp_path):
    _approved(tmp_path)
    orders = _Orders()
    assert _run(tmp_path, orders=orders, now=EARLY) == "outside_session" and orders.calls == []
    assert _run(tmp_path, _Feed(bid={"411060": 19_990}), orders) == "ordered:1"
    assert [(s, q, p) for s, q, p, _ in orders.calls] == [("411060", 24, 19_990)]
    rows = sell.journal(tmp_path)
    assert [(r["state"], r["side"]) for r in rows] == [("intent", "SELL"), ("accepted", "SELL")]
    assert ai.journal(tmp_path) == []                    # the buy journal reads every row as a buy: none lands there
    token, text = sell.notice(tmp_path)
    assert "지정가 1건 주문" in text and not any(x in text for x in ("411060", "toss:", "19", "479"))
    assert _run(tmp_path, orders=orders, now=LATER) == "waiting" and len(orders.calls) == 1
    assert _run(tmp_path, _Feed(fills=_filled(orders)), orders, now=LATER) == "executed"
    assert "1종목 전량 체결" in sell.notice(tmp_path)[1]
    assert _run(tmp_path, orders=orders, now=NEXT_DAY) == "none" and len(orders.calls) == 1
    shown = "\n".join(sell.render_status(tmp_path))
    assert "executed" in shown and "SELL toss:domestic:411060 x24 @ 19,990" in shown


def test_the_price_is_the_highest_bid_at_which_the_book_covers_the_quantity(tmp_path):
    _approved(tmp_path)
    orders = _Orders()
    book = {"411060": [(20_000.0, 10.0), (19_950.0, 10.0), (19_900.0, 10.0)]}
    assert _run(tmp_path, _Feed(books=book), orders) == "ordered:1"
    assert orders.calls[0][2] == 19_900                  # 24 shares are covered at the third level


@pytest.mark.parametrize(("feed", "reason"), [
    (_Feed(depth=23), "book_too_thin"),
    (_Feed(books={"411060": []}), "book_too_thin"),
    (_Feed(bid={"411060": 19_599}), "price_guard"),       # more than 2% under the last 20,000
    (_Feed(held={"411060": 23.0}), "holding_short"),
    (_Feed(held={}), "holding_short"),
    (_Feed(prices={"069500": 40_000.0}), "price_unreadable"),
])
def test_a_miss_holds_the_whole_scenario_for_the_day_and_the_next_trading_day_tries_again(tmp_path, feed, reason):
    _approved(tmp_path)
    orders = _Orders()
    assert _run(tmp_path, feed, orders) == f"hold:{reason}" and orders.calls == []
    assert "자동 매도 보류" in sell.notice(tmp_path)[1]
    assert _run(tmp_path, orders=orders, now=LATER) == "done_today" and orders.calls == []
    assert _run(tmp_path, orders=orders, now=NEXT_DAY) == "ordered:1"


def test_a_bid_two_percent_under_the_last_is_still_placed(tmp_path):
    _approved(tmp_path)
    assert _run(tmp_path, _Feed(bid={"411060": 19_600})) == "ordered:1"


def test_it_never_sells_what_the_buy_scenario_is_buying(tmp_path):
    _approved(tmp_path)
    orders = _Orders()
    buying = {"status": ai.PENDING, "items": [{"instrument": GOLD, "quantity": 2}]}
    assert _run(tmp_path, orders=orders, buy=buying) == "hold:buying_same_instrument" and orders.calls == []
    done = {"status": ai.EXECUTED, "items": [{"instrument": GOLD, "quantity": 2, "filled": 2}]}
    assert _run(tmp_path, orders=orders, now=NEXT_DAY, buy=done) == "ordered:1"


def test_an_unreadable_buy_scenario_holds_the_sell(tmp_path):
    _approved(tmp_path)
    orders = _Orders()
    assert sell.run(_Feed(), orders, tmp_path, now=OPEN, buy_scenario=None)["status"] == "hold:buy_scenario_unreadable"
    assert orders.calls == []


def test_with_the_sell_path_off_it_holds_and_says_so_once(tmp_path):
    _approved(tmp_path)
    assert _run(tmp_path, orders=_Off()) == "hold:orders_off"
    token, _text = sell.notice(tmp_path)
    sell.mark_told(tmp_path, token, now=OPEN)
    assert _run(tmp_path, orders=_Off(), now=LATER) == "hold:orders_off" and sell.notice(tmp_path) is None


@pytest.mark.parametrize("broken", ["kr_market_day", "stock_info", "last_prices", "bids", "domestic_quantities"])
def test_a_read_that_fails_holds_and_never_half_orders(tmp_path, broken):
    _approved(tmp_path)
    feed, orders = _Feed(), _Orders()

    def fail(*_a, **_kw):
        raise ToolError("PROVIDER_UNAVAILABLE", "down")

    setattr(feed, broken, fail)
    assert _run(tmp_path, feed, orders) == "hold:read_failed" and orders.calls == []


def test_a_closed_market_day_places_nothing(tmp_path):
    _approved(tmp_path)
    orders = _Orders()
    assert _run(tmp_path, _Feed(session=False), orders) == "outside_session" and orders.calls == []


def test_the_days_cap_counts_what_the_sell_journal_already_sent_today(tmp_path):
    _approved(tmp_path)
    sell._append(tmp_path, {"day": "2026-12-01", "state": "intent", "est_krw": 30_000, "client_order_id": "other"})
    orders = _Orders()
    assert _run(tmp_path, orders=orders) == "hold:over_daily_cap" and orders.calls == []


def test_a_price_that_rose_past_the_approved_cap_holds(tmp_path):
    _approved(tmp_path)
    orders = _Orders()
    feed = _Feed(prices={"411060": 21_000.0})            # 24 x 21,000 = 504,000
    assert _run(tmp_path, feed, orders) == "hold:over_scenario_cap" and orders.calls == []


def test_an_order_cancelled_at_the_close_leaves_its_remainder_for_the_next_trading_day(tmp_path):
    _approved(tmp_path)
    orders = _Orders()
    assert _run(tmp_path, orders=orders) == "ordered:1"
    part = _Feed(fills=_filled(orders, status="CANCELED", filled=10), held={"411060": 90.0})
    assert _run(tmp_path, part, orders, now=NEXT_DAY) == "ordered:1"
    assert [(q, p) for _s, q, p, _c in orders.calls] == [(24, 20_000), (14, 20_000)]
    assert sell._read(tmp_path)["items"][0]["filled"] == 10


def test_a_partial_fill_still_open_places_nothing_beside_it(tmp_path):
    _approved(tmp_path)
    orders = _Orders()
    _run(tmp_path, orders=orders)
    partial = _Feed(fills=_filled(orders, status="PARTIALLY_FILLED", filled=5))
    assert _run(tmp_path, partial, orders, now=NEXT_DAY) == "waiting" and len(orders.calls) == 1


@pytest.mark.parametrize(("error", "status", "state"), [
    (toss_orders.OrderRejected(422, "insufficient-quantity", {"status": 422}), "stopped:0", "rejected"),
    (toss_orders.OrderUnknown("timeout"), "unknown:0", "unknown"),
    (ToolError(toss_orders.ORDER_REFUSED, "off"), "stopped:0", "rejected"),
])
def test_a_rejection_or_an_unknown_outcome_stops_and_is_never_resent(tmp_path, error, status, state):
    _approved(tmp_path)
    orders = _Orders(fail_at=0, error=error)
    assert _run(tmp_path, orders=orders) == status
    assert [r["state"] for r in sell.journal(tmp_path)] == ["intent", state]
    assert _run(tmp_path, orders=orders, now=NEXT_DAY) == "none" and len(orders.calls) == 1
    assert "자동 매도" in sell.notice(tmp_path)[1]


def test_an_intent_with_no_outcome_is_never_resent(tmp_path):
    scenario = _approved(tmp_path)
    from runtime.read_only_kernel import integrity

    cid = integrity.short_id("sord", f"{scenario['scenario_id']}|{GOLD}|2026-12-01")
    sell._append(tmp_path, {"day": "2026-12-01", "state": "intent", "est_krw": 1, "client_order_id": cid})
    orders = _Orders()
    assert _run(tmp_path, orders=orders) == "unknown:0" and orders.calls == []


@pytest.mark.parametrize("feed", [_Feed(status={"411060": "SUSPENDED"}), _Feed(kind="STOCK")])
def test_anything_but_an_active_etf_stops_before_any_order(tmp_path, feed):
    _approved(tmp_path)
    orders = _Orders()
    assert _run(tmp_path, feed, orders) == "stopped:not_an_active_etf" and orders.calls == []


def test_an_unfinished_scenario_expires_after_seven_days(tmp_path):
    _approved(tmp_path)
    orders = _Orders()
    assert _run(tmp_path, orders=orders, now="2026-12-08T01:00:00Z") == "expired" and orders.calls == []
    assert "만료" in sell.notice(tmp_path)[1]


def test_no_approval_replaces_a_scenario_with_an_open_order(tmp_path):
    _approved(tmp_path)
    _run(tmp_path)
    body = sell.read_proposal(tmp_path)
    body.pop("approved_scenario")
    sell.proposal_path(tmp_path).write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(ToolError, match="open order"):
        sell.approve(tmp_path, now=LATER, positions=GOLD_HELD)


# --- the store's steps and the gate ---------------------------------------------------------------------------------

def _store_feed():
    feed = toss_account.TossHoldingsFeed.__new__(toss_account.TossHoldingsFeed)
    stub = _Feed()
    for name in ("kr_market_day", "stock_info", "last_prices", "bids", "domestic_quantities", "fill_state"):
        setattr(feed, name, getattr(stub, name))
    return feed


def _fire_files(tmp_path, now=OPEN, classes=GOLD_OVER, held=GOLD_HELD):
    state = store.state_dir(tmp_path)
    state.mkdir(parents=True, exist_ok=True)
    board = {"as_of": now, "allocation": {"complete": True, "classes": {c: {"krw": v} for c, v in classes.items()}}}
    store.local_path(tmp_path).write_text(json.dumps(board), encoding="utf-8")
    store.positions_path(tmp_path).write_text(json.dumps({"as_of": now, "positions": held}), encoding="utf-8")
    store.auto_candidates_path(tmp_path).write_text(json.dumps({"candidates": CANDIDATES, "us_bonds": [US10]}),
                                                    encoding="utf-8")
    return state


@pytest.mark.parametrize("value", [None, "buy_only"])
def test_with_the_gate_closed_nothing_is_drafted_told_or_written(monkeypatch, tmp_path, value):
    state = _fire_files(tmp_path)
    before = sorted(p.name for p in state.iterdir())
    monkeypatch.setattr(store, "_feed", _store_feed)
    _gate(monkeypatch, value)
    assert store.refresh_autosell_proposal(now=OPEN, root=tmp_path) == {"status": "off", "alert": None}
    assert store.run_autosell(now=OPEN, root=tmp_path) == {"status": "off", "alert": None}
    assert sorted(p.name for p in state.iterdir()) == before


def test_behind_buy_sell_the_store_drafts_from_the_plan_the_terminal_shows(monkeypatch, tmp_path):
    state = _fire_files(tmp_path)
    monkeypatch.setattr(store, "_feed", _store_feed)
    _gate(monkeypatch, "buy_sell")
    result = store.refresh_autosell_proposal(now=OPEN, root=tmp_path)
    assert result["status"] == "drafted" and result["alert"][1].startswith("[보유 자산] 매도 시나리오 준비")
    (item,) = sell.read_proposal(state)["items"]
    shown = store.load_rebalance(now=OPEN, root=tmp_path)
    assert item["instrument"] == GOLD and f"SELL {GOLD}" in shown and "35 sh" in shown and item["quantity"] == 24
    assert store.refresh_autosell_proposal(now=LATER, root=tmp_path)["status"] == "checked_this_month"
    # Thomas approves against the fire's own positions, and the fire's step hands the gated sell adapter.
    scenario = store.approve_autosell(now=LATER, root=tmp_path)
    assert scenario["status"] == sell.PENDING
    seen = []
    monkeypatch.setattr(sell, "run", lambda f, orders, d, *, now, buy_scenario: seen.append((orders, buy_scenario))
                        or {"status": "x"})
    store.run_autosell(now=LATER, root=tmp_path)
    ((orders, buying),) = seen
    assert isinstance(orders, toss_orders.TossSellOrders) and buying == {}


def test_the_store_places_a_bond_in_its_slot_by_thomas_currency_entry(monkeypatch, tmp_path):
    held = _bonds(kr_pnl=100_000.0, us_pnl=-50_000.0)
    state = _fire_files(tmp_path, classes=BONDS_OVER, held=held)
    classification.apply(state, assign={US10: al.BONDS}, currency={US10: "usd"}, now=OPEN)
    monkeypatch.setattr(store, "_feed", _store_feed)
    _gate(monkeypatch, "buy_sell")
    assert store.refresh_autosell_proposal(now=OPEN, root=tmp_path)["status"] == "drafted"
    assert [(i["instrument"], i["quantity"]) for i in sell.read_proposal(state)["items"]] == [(US10, 48)]


def test_before_the_months_check_day_nothing_is_read(monkeypatch, tmp_path):
    _fire_files(tmp_path, now=WEEKEND)
    monkeypatch.setattr(store, "_feed", _store_feed)
    _gate(monkeypatch, "buy_sell")
    monkeypatch.setattr(store, "rebalance_plan", lambda **_kw: pytest.fail("the plan was read before the check day"))
    assert store.refresh_autosell_proposal(now=WEEKEND, root=tmp_path)["status"] == "before_check_day"


def test_a_stale_fire_spends_no_month(monkeypatch, tmp_path):
    state = _fire_files(tmp_path, now="2026-11-30T12:00:00Z")           # the board is 13 hours old at OPEN
    monkeypatch.setattr(store, "_feed", _store_feed)
    _gate(monkeypatch, "buy_sell")
    assert store.refresh_autosell_proposal(now=OPEN, root=tmp_path)["status"] == "plan_refused"
    assert not sell.proposal_path(state).exists()


def test_a_pending_scenario_is_still_looked_at_once_the_gate_closes(monkeypatch, tmp_path):
    state = _fire_files(tmp_path)
    monkeypatch.setattr(store, "_feed", _store_feed)
    _gate(monkeypatch, "buy_sell")
    store.refresh_autosell_proposal(now=OPEN, root=tmp_path)
    store.approve_autosell(now=LATER, root=tmp_path)
    _gate(monkeypatch, "buy_only")
    result = store.run_autosell(now=LATER, root=tmp_path)
    assert result["status"] == "hold:orders_off" and "매도 경로가 꺼져" in result["alert"][1]
    assert sell.journal(state) == []


def _fire(tmp_path):
    schedule = scheduler.Schedule(schedule_id="schedule_holdings_test", kind=scheduler.KIND_HOLDINGS, request="",
                                  interval_seconds=3600, enabled=True, created_by="test", created_at=OPEN,
                                  next_run_at=OPEN)
    return scheduler._execute(schedule, now=OPEN, ledger=None, working_memory=None, programization=None,
                              repo_root=tmp_path, executor=lambda **_: {})


def test_the_scheduler_runs_both_sell_steps_and_marks_each_notice_after_delivery(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "refresh", lambda **_kw: {"status": "holdings snapshot: refreshed", "alert": None})
    monkeypatch.setattr(store, "blocked_check", lambda **_kw: {})
    monkeypatch.setattr(store, "run_autoinvest", lambda **_kw: {"status": "none", "alert": None})
    monkeypatch.setattr(store, "refresh_autoinvest_proposal", lambda **_kw: {"status": "x", "alert": None})
    monkeypatch.setattr(store, "run_autosell", lambda **_kw: {"status": "ordered:1", "alert": ("t1", "자동 매도 1건")})
    monkeypatch.setattr(store, "refresh_autosell_proposal",
                        lambda **_kw: {"status": "drafted", "alert": ("t2", "매도 시나리오 준비")})
    told = []
    monkeypatch.setattr(store, "mark_autosell_told", lambda token, **_kw: told.append(token))
    monkeypatch.setattr(store, "mark_sell_proposal_told", lambda token, **_kw: told.append(token))
    monkeypatch.setattr(operator_mod, "select_operator_channel", lambda **_kw: "channel")

    def down(channel, text, **_kw):
        raise OperatorBlocked("CHANNEL_DOWN", "down")

    monkeypatch.setattr(operator_mod, "notify_operator", down)
    status = _fire(tmp_path)
    assert "autosell ordered:1" in status and "autosell not sent:CHANNEL_DOWN" in status
    assert "autosell proposal drafted" in status and "autosell proposal not sent:CHANNEL_DOWN" in status and told == []
    monkeypatch.setattr(operator_mod, "notify_operator", lambda channel, text, **_kw: None)
    status = _fire(tmp_path)
    assert "autosell told" in status and "autosell proposal told" in status and told == ["t1", "t2"]


def test_a_closed_gate_leaves_the_fires_status_line_as_it_was(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "refresh", lambda **_kw: {"status": "holdings snapshot: refreshed", "alert": None})
    monkeypatch.setattr(store, "blocked_check", lambda **_kw: {})
    monkeypatch.setattr(store, "run_autoinvest", lambda **_kw: {"status": "none", "alert": None})
    monkeypatch.setattr(store, "refresh_autoinvest_proposal", lambda **_kw: {"status": "x", "alert": None})
    assert "autosell" not in _fire(tmp_path)


def test_a_failing_sell_step_does_not_stop_the_fire(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "refresh", lambda **_kw: {"status": "holdings snapshot: refreshed", "alert": None})
    monkeypatch.setattr(store, "blocked_check", lambda **_kw: {})
    monkeypatch.setattr(store, "run_autoinvest", lambda **_kw: {"status": "none", "alert": None})
    monkeypatch.setattr(store, "refresh_autoinvest_proposal", lambda **_kw: {"status": "x", "alert": None})

    def boom(**_kw):
        raise ToolError("BOOM", "x")

    monkeypatch.setattr(store, "run_autosell", boom)
    monkeypatch.setattr(store, "refresh_autosell_proposal", boom)
    status = _fire(tmp_path)
    assert "autosell error:BOOM" in status and "autosell proposal error:BOOM" in status


# --- Thomas's terminal ----------------------------------------------------------------------------------------------

def test_the_board_command_approves_a_sell_draft_only_on_y_at_a_terminal(monkeypatch, tmp_path, capsys):
    from scripts import holdings_board

    state = _fire_files(tmp_path)
    monkeypatch.setattr(store, "_feed", _store_feed)
    _gate(monkeypatch, "buy_sell")
    store.refresh_autosell_proposal(now=OPEN, root=tmp_path)
    monkeypatch.setattr(holdings_board, "state_dir", lambda: state)
    monkeypatch.setattr(holdings_board, "assert_not_foreign_root_run", lambda: None)
    monkeypatch.setattr("runtime.mvp_runtime.holdings.store.approve_autosell",
                        lambda *, now: sell.approve(state, now=now, positions=GOLD_HELD))
    monkeypatch.setattr(holdings_board.timeutil, "utc_now_iso", lambda: LATER)      # the wall clock is never read
    monkeypatch.setattr(holdings_board, "_interactive", lambda: False)
    assert holdings_board.main(["--autosell-approve"]) == holdings_board.EXIT_BLOCKED
    monkeypatch.setattr(holdings_board, "_interactive", lambda: True)
    monkeypatch.setattr(holdings_board, "_answer", lambda _prompt: "n")
    assert holdings_board.main(["--autosell-approve"]) == holdings_board.EXIT_BLOCKED and sell._read(state) == {}
    monkeypatch.setattr(holdings_board, "_answer", lambda _prompt: "y")
    assert holdings_board.main(["--autosell-approve"]) == holdings_board.EXIT_OK
    assert sell._read(state)["status"] == sell.PENDING and "SELL x24" in capsys.readouterr().out
    monkeypatch.setattr(holdings_board, "_answer", lambda _prompt: pytest.fail("asked with nothing to approve"))
    assert holdings_board.main(["--autosell-approve"]) == holdings_board.EXIT_BLOCKED
    assert holdings_board.main(["--autosell-status"]) == holdings_board.EXIT_OK
    assert "AUTOSELL - terminal only" in capsys.readouterr().out
