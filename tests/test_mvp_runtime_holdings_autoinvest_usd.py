"""G6 §2g (Thomas 2026-10-27): global equity bought in dollars. The draft spends the USD cash Thomas exchanged and
names the exchange still to make; the executor places a US market order by amount in the US regular session.
No network: fake feeds and order adapters."""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.holdings import allocation as al
from runtime.mvp_runtime.holdings import autoinvest as ai
from runtime.mvp_runtime.holdings import autoinvest_proposal as ap
from runtime.mvp_runtime.holdings import toss_account

DOM, KR3, US10, GOLD, WORLD = ("toss:domestic:069500", "toss:domestic:114260", "toss:domestic:308620",
                               "toss:domestic:411060", "toss:overseas:VT")
CANDIDATES = {al.GLOBAL_EQUITY: [WORLD], al.DOMESTIC_EQUITY: [DOM], al.BONDS: [KR3, US10], al.GOLD: [GOLD]}
PRICES = {"069500": 40_000.0, "114260": 60_000.0, "308620": 10_000.0, "411060": 20_000.0}
RATE = 1_400.0
# NAV 1,000,000 with only global equity short: 200k against its 300k target.
CLASSES = {al.GLOBAL_EQUITY: 200_000.0, al.DOMESTIC_EQUITY: 100_000.0, al.BONDS: 370_000.0, al.GOLD: 130_000.0,
           al.COIN: 25_000.0, al.CASH: 175_000.0}
POSITIONS = [{"instrument": KR3, "asset_class": al.BONDS, "value_krw": 277_500.0},
             {"instrument": US10, "asset_class": al.BONDS, "value_krw": 92_500.0}]
CURRENCIES = {US10: "usd"}
KST_DAY = "2026-10-28T01:00:00Z"                 # 10:00 KST Wednesday: the domestic session
US_OPEN = "2026-10-28T15:00:00Z"                 # 00:00 KST Thursday: inside the US regular session of 10-28
US_LATE = "2026-10-28T19:30:00Z"                 # 04:30 KST: within 70 minutes of the 05:00 close
US_NEXT = "2026-10-29T15:00:00Z"                 # the next US session


def _rows():
    return ap.slots(class_krw=CLASSES, positions=POSITIONS, currencies=CURRENCIES, candidates=CANDIDATES,
                    us_bonds=[US10])[0]


# --- the draft --------------------------------------------------------------------------------------------------

def test_the_dollar_slot_spends_usd_cash_first():
    result = ap.draft(_rows(), prices=PRICES, cash_krw=200_000.0, fee_rate=0.0, cash_usd=100.0, usd_krw=RATE)
    assert [(i["slot"], i.get("amount_usd")) for i in result["items"]] == [("global_equity", "71.42")]
    assert result["exchange_krw"] == 0 and result["estimate_krw"] == 100_000          # ten 10,000 steps, then target
    assert result["max_krw"] == int(200_000 + 100_000 / 0.97)


def test_without_dollars_the_draft_names_the_exchange_and_orders_nothing_for_it():
    result = ap.draft(_rows(), prices=PRICES, cash_krw=200_000.0, fee_rate=0.0, cash_usd=0.0, usd_krw=RATE)
    assert result["items"] == [] and result["status"] == "exchange_only"
    assert (result["exchange_krw"], result["exchange_usd"]) == (100_000, 71.43)


def test_a_partial_dollar_balance_is_spent_and_the_rest_named_as_an_exchange():
    result = ap.draft(_rows(), prices=PRICES, cash_krw=200_000.0, fee_rate=0.0, cash_usd=30.0, usd_krw=RATE)
    # 30 x 1,400 x 0.97 = 40,740 of dollars, then 59,260 of the KRW budget to exchange.
    assert [i.get("amount_usd") for i in result["items"]] == ["29.10"]
    assert result["exchange_krw"] == 59_260


def test_the_exchange_comes_out_of_the_krw_budget_so_krw_shares_still_fit_after_it():
    classes = {**CLASSES, al.DOMESTIC_EQUITY: 40_000.0, al.CASH: 235_000.0}         # domestic 60k short too
    rows = ap.slots(class_krw=classes, positions=POSITIONS, currencies=CURRENCIES, candidates=CANDIDATES,
                    us_bonds=[US10])[0]
    result = ap.draft(rows, prices=PRICES, cash_krw=120_000.0, fee_rate=0.0, cash_usd=0.0, usd_krw=RATE)
    krw = sum(i["est_krw"] for i in result["items"] if "quantity" in i)
    assert krw + result["exchange_krw"] <= 120_000 * 0.97                              # one KRW pool, never over


def test_the_us_fee_is_paid_from_the_step():
    result = ap.draft(_rows(), prices=PRICES, cash_krw=0.0, fee_rate=0.0, cash_usd=200.0, usd_krw=RATE,
                      us_fee_rate=0.25)
    # Each 10,000 of exposure costs 12,500; the dollars ordered are the exposure, the fee rides on top.
    assert result["items"][0]["amount_usd"] == f"{100_000 / RATE - 0.005:.2f}"


# --- the fire and Thomas's terminal -----------------------------------------------------------------------------

class _Feed:
    def __init__(self, cash=200_000.0, usd=0.0):
        self.cash, self.usd = cash, usd

    def cash_krw(self, **_): return self.cash
    def cash_usd(self, **_): return self.usd
    def usd_krw_rate(self, **_): return RATE

    def kr_market_day(self, date, **_):
        return {"date": date, "integrated": {"regularMarket": {"startTime": "09:00:00", "endTime": "15:30:00"}}}

    def last_prices(self, symbols, **_): return {s: PRICES[s] for s in symbols if s in PRICES}
    def commission_rates(self, **_): return [{"marketCountry": "KR", "commissionRate": "0"},
                                             {"marketCountry": "US", "commissionRate": "0"}]
    def stock_info(self, symbols, **_): return [{"symbol": s, "name": s} for s in symbols]


def _board():
    return {"allocation": {"complete": True, "classes": {c: {"krw": v} for c, v in CLASSES.items()}}}


def _refresh(state, feed, now=KST_DAY):
    return ap.refresh(feed, state, now=now, board=_board(), positions=POSITIONS, currencies=CURRENCIES,
                      candidates=CANDIDATES, us_bonds=[US10])


def test_an_exchange_only_draft_is_told_without_an_amount_and_cannot_be_approved(tmp_path):
    assert _refresh(tmp_path, _Feed(usd=0.0)) == "exchange_only"
    text = ap.notice(tmp_path)[1]
    assert "달러 환전이 필요" in text and "원" not in text.replace("보유 자산", "") and "$" not in text
    assert any(line.startswith("exchange    : ~100,000 KRW") for line in ap.render(tmp_path))
    with pytest.raises(ToolError, match="no drafted"):
        ap.approve(tmp_path, now=KST_DAY, candidates=CANDIDATES)


def test_a_draft_with_orders_and_an_exchange_tells_both_without_an_amount(tmp_path):
    assert _refresh(tmp_path, _Feed(usd=30.0)) == "drafted"
    text = ap.notice(tmp_path)[1]
    assert "미국 금액 주문 1건" in text and "달러 환전이 필요" in text
    assert "원" not in text.replace("보유 자산", "") and "$" not in text and "59" not in text


def test_a_dollar_draft_is_approved_into_a_us_amount_item(tmp_path):
    assert _refresh(tmp_path, _Feed(usd=100.0)) == "drafted"
    assert "미국 금액 주문 1건" in ap.notice(tmp_path)[1]
    scenario = ap.approve(tmp_path, now=KST_DAY, candidates=CANDIDATES)
    assert scenario["items"] == [{"instrument": WORLD, "amount_usd": "71.42", "asset_class": al.GLOBAL_EQUITY}]


@pytest.mark.parametrize("spec", ["VT=$10", "toss:overseas:vt=$10", "toss:overseas:VT=$1.234", "toss:overseas:VT=$0",
                                  "toss:domestic:069500=$10"])
def test_a_malformed_us_spec_is_refused(spec):
    with pytest.raises(ToolError):
        ai.parse_spec(spec)


def test_a_us_spec_is_read_as_dollars_and_cents():
    assert ai.parse_spec("toss:overseas:VT=$10.5,069500=1") == [
        {"instrument": WORLD, "amount_usd": "10.50"}, {"instrument": DOM, "quantity": 1}]


# --- the executor -----------------------------------------------------------------------------------------------

class _UsFeed:
    """A Toss stand-in with a US regular session of 10-28 (22:30-05:00 KST) and of 10-29."""

    def __init__(self, usd=100.0, fills=None, status="ACTIVE"):
        self.usd, self.fills, self.status, self.calendar = usd, fills or {}, status, []

    def us_regular_sessions(self, date, **_):
        self.calendar.append(date)
        return [("2026-10-28", "2026-10-28T22:30:00+09:00", "2026-10-29T05:00:00+09:00"),
                ("2026-10-29", "2026-10-29T22:30:00+09:00", "2026-10-30T05:00:00+09:00")]

    def kr_market_day(self, date, **_):
        return {"date": date, "integrated": {"regularMarket": {"startTime": "09:00:00", "endTime": "15:30:00"}}}

    def stock_info(self, symbols, **_):
        return [{"symbol": s, "status": self.status, "securityType": "ETF"} for s in symbols]

    def last_prices(self, symbols, **_): return {s: PRICES[s] for s in symbols if s in PRICES}
    def asks(self, symbol, **_): return [(PRICES[symbol], 100)]
    def usd_krw_rate(self, **_): return RATE
    def cash_usd(self, **_): return self.usd
    def cash_krw(self, **_): return 500_000.0
    def commission_rates(self, **_): return [{"marketCountry": "KR", "commissionRate": "0"},
                                             {"marketCountry": "US", "commissionRate": "0"}]

    def fill_state(self, order_id, **_):
        return self.fills.get(order_id, {"status": "PENDING", "filled": 0, "filled_amount": 0})


class _Orders:
    enabled = True

    def __init__(self):
        self.calls = []

    def buy_kr(self, symbol, quantity, *, price, client_order_id, **_):
        self.calls.append(("kr", symbol, quantity, client_order_id))
        return f"K{len(self.calls)}"

    def buy_us_amount(self, symbol, amount_usd, *, client_order_id, **_):
        self.calls.append(("us", symbol, amount_usd, client_order_id))
        return f"U{len(self.calls)}"


def _approve(tmp_path, spec="toss:overseas:VT=$71.42", max_krw=150_000):
    return ai.approve(tmp_path, spec, max_krw=max_krw, candidates=CANDIDATES, now="2026-10-28T00:00:00Z")


def test_a_us_amount_is_placed_once_inside_the_us_session_and_done_when_it_fills(tmp_path):
    _approve(tmp_path)
    orders, feed = _Orders(), _UsFeed()
    assert ai.run(feed, orders, tmp_path, now=KST_DAY)["status"] == "outside_session" and orders.calls == []
    assert ai.run(feed, orders, tmp_path, now=US_LATE)["status"] == "outside_session"     # past close - 70 min
    assert ai.run(feed, orders, tmp_path, now=US_OPEN)["status"] == "ordered:1"
    ((kind, symbol, amount, cid),) = orders.calls
    assert (kind, symbol, amount) == ("us", "VT", "71.42")
    rows = ai.journal(tmp_path)
    assert [r["state"] for r in rows] == ["intent", "accepted"] and rows[0]["amount_usd"] == "71.42"
    assert rows[0]["est_krw"] == round(71.42 * RATE)
    assert ai.run(feed, orders, tmp_path, now="2026-10-28T16:00:00Z")["status"] == "waiting"
    assert len(orders.calls) == 1                                                      # open: nothing beside it
    feed.fills = {"U1": {"status": "FILLED", "filled": 0.53, "filled_amount": 71.4}}
    assert ai.run(feed, orders, tmp_path, now="2026-10-28T17:00:00Z")["status"] == ai.EXECUTED
    item = ai._read(tmp_path)["items"][0]
    assert item["done"] and item["spent_usd"] == 71.4 and "open" not in item


def test_a_us_order_closed_with_nothing_filled_is_placed_again_next_session(tmp_path):
    _approve(tmp_path)
    orders, feed = _Orders(), _UsFeed()
    ai.run(feed, orders, tmp_path, now=US_OPEN)
    feed.fills = {"U1": {"status": "CANCELED", "filled": 0, "filled_amount": 0}}
    assert ai.run(feed, orders, tmp_path, now="2026-10-28T17:00:00Z")["status"] == "outside_session"
    assert "다음 미국 정규장" in ai.notice(tmp_path)[1]
    assert len(orders.calls) == 1                                                      # same session: not again
    assert ai.run(feed, orders, tmp_path, now=US_NEXT)["status"] == "ordered:1"
    assert orders.calls[1][3] != orders.calls[0][3]                                    # a new session, a new id


def test_too_few_dollars_holds_and_places_nothing(tmp_path):
    _approve(tmp_path)
    orders = _Orders()
    assert ai.run(_UsFeed(usd=50.0), orders, tmp_path, now=US_OPEN)["status"] == "hold:insufficient_usd"
    assert orders.calls == [] and "환전" in ai.notice(tmp_path)[1]


def test_the_scenario_cap_counts_the_us_order_in_krw(tmp_path):
    _approve(tmp_path, max_krw=90_000)                                                 # 71.42 x 1,400 = 99,988
    orders = _Orders()
    assert ai.run(_UsFeed(), orders, tmp_path, now=US_OPEN)["status"] == "hold:over_scenario_cap"
    assert orders.calls == []


def test_a_mixed_scenario_buys_each_side_in_its_own_session_and_closes_when_both_are_done(tmp_path):
    _approve(tmp_path, spec="069500=1,toss:overseas:VT=$71.42", max_krw=150_000)
    orders, feed = _Orders(), _UsFeed()
    assert ai.run(feed, orders, tmp_path, now=KST_DAY)["status"] == "ordered:1"        # the domestic share by day
    assert ai.run(feed, orders, tmp_path, now=US_OPEN)["status"] == "ordered:1"        # the dollars by night
    assert [c[0] for c in orders.calls] == ["kr", "us"]
    feed.fills = {"K1": {"status": "FILLED", "filled": 1, "filled_amount": 40_000}}
    assert ai.run(feed, orders, tmp_path, now="2026-10-28T16:00:00Z")["status"] == "waiting"
    feed.fills["U2"] = {"status": "FILLED", "filled": 0.5, "filled_amount": 71.0}
    assert ai.run(feed, orders, tmp_path, now="2026-10-28T17:00:00Z")["status"] == ai.EXECUTED


def test_the_domestic_check_counts_an_open_us_order_without_failing(tmp_path):
    _approve(tmp_path, spec="069500=1,toss:overseas:VT=$71.42", max_krw=120_000)
    orders, feed = _Orders(), _UsFeed()
    ai.run(feed, orders, tmp_path, now=US_OPEN)                                        # US first, still open
    # 99,988 open + 40,000 for the share is over 120,000: the domestic side holds rather than crashing.
    assert ai.run(feed, orders, tmp_path, now="2026-10-29T01:00:00Z")["status"] == "hold:over_scenario_cap"


def test_the_terminal_status_shows_a_us_item(tmp_path):
    _approve(tmp_path)
    ai.run(_UsFeed(), _Orders(), tmp_path, now=US_OPEN)
    text = "\n".join(ai.render_status(tmp_path))
    assert "toss:overseas:VT $71.42 (open)" in text and "$71.42 ~99,988 KRW U1" in text


# --- the feed's reads -------------------------------------------------------------------------------------------

def _feed(answers):
    feed = toss_account.TossHoldingsFeed.__new__(toss_account.TossHoldingsFeed)
    feed._account, seen = (1, "x"), []

    def get(path, params=None, **_kw):
        seen.append((path, params))
        return answers[path]

    feed._get = get
    return feed, seen


def test_the_us_calendar_yields_each_regular_session_and_skips_a_closed_day():
    feed, seen = _feed({toss_account.US_CALENDAR_PATH: {
        "previousBusinessDay": {"date": "2026-10-27", "regularMarket": {"startTime": "a", "endTime": "b"}},
        "today": {"date": "2026-10-28", "regularMarket": None},
        "nextBusinessDay": {"date": "2026-10-29", "regularMarket": {"startTime": "c", "endTime": "d"}}}})
    assert feed.us_regular_sessions("2026-10-28") == [("2026-10-27", "a", "b"), ("2026-10-29", "c", "d")]
    assert seen == [(toss_account.US_CALENDAR_PATH, {"date": "2026-10-28"})]


def test_an_order_detail_carries_the_filled_amount_and_usd_cash_is_the_usd_buying_power():
    feed, seen = _feed({f"{toss_account.ORDERS_PATH}/U1": {
        "status": "FILLED", "execution": {"filledQuantity": "0.5", "filledAmount": "71.40"}},
        toss_account.BUYING_POWER_PATH: {"cashBuyingPower": "123.45"}})
    assert feed.fill_state("U1") == {"status": "FILLED", "filled": 0.5, "filled_amount": 71.4}
    assert feed.cash_usd() == 123.45 and seen[-1] == (toss_account.BUYING_POWER_PATH, {"currency": "USD"})
    assert not any("buy" in n.lower() and n != "cash_usd" for n in dir(toss_account.TossHoldingsFeed)
                   if not n.startswith("_") and "buying" not in n.lower())
    json.dumps(seen)
