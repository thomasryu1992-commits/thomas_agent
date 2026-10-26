"""G6-buy (Thomas 2026-10-26): the Toss buy-order adapter and the approved-scenario executor.
No network: a fake urlopen for the adapter, fake feeds and adapters for the executor."""

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
from runtime.mvp_runtime.holdings import autoinvest as ai
from runtime.mvp_runtime.holdings import store
from runtime.mvp_runtime.holdings import toss_account, toss_orders

# --- the adapter ------------------------------------------------------------------------------------------------


def _feed():
    feed = toss_account.TossHoldingsFeed.__new__(toss_account.TossHoldingsFeed)
    feed._authorization, feed._account = None, (7, "12345678901")
    feed._token = ("TOKEN-A", time.monotonic() + 3600)
    feed.issued = 0

    def issue(*, timeout_seconds):
        feed.issued += 1
        feed._token = ("TOKEN-B", time.monotonic() + 3600)
        return "TOKEN-B"

    feed._issue_token = issue
    return feed


class _Http:
    """Answers each POST in turn: a dict is a 200 body, an int is an HTTP error with ``code``, an exception
    is raised."""

    def __init__(self, *answers):
        self.answers, self.sent = list(answers), []

    def __call__(self, request, timeout=None):
        self.sent.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
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


def _orders(monkeypatch, http):
    monkeypatch.setenv(toss_orders.TOSS_ORDERS_ENV, toss_orders.BUY_ONLY)
    monkeypatch.setattr(toss_orders.urllib.request, "urlopen", http)
    feed = _feed()
    return toss_orders.select_buy_orders(feed), feed


def test_without_the_opt_in_nothing_capable_is_built_and_no_order_can_be_placed(monkeypatch):
    http = _Http()
    monkeypatch.setattr(toss_orders.urllib.request, "urlopen", http)
    orders = toss_orders.select_buy_orders(_feed())
    assert isinstance(orders, toss_orders.NoBuyOrders) and not orders.enabled
    with pytest.raises(ToolError) as refused:
        orders.buy_kr("069500", 1, client_order_id="c1")
    assert refused.value.reason_code == toss_orders.ORDER_REFUSED and http.sent == []


@pytest.mark.parametrize("value", ["", "BUY_ONLY_", "real", "sell"])
def test_any_other_value_is_not_the_opt_in(monkeypatch, value):
    monkeypatch.setenv(toss_orders.TOSS_ORDERS_ENV, value)
    assert isinstance(toss_orders.select_buy_orders(_feed()), toss_orders.NoBuyOrders)


def test_the_one_post_is_a_market_buy_of_whole_shares_with_its_idempotency_key(monkeypatch):
    http = _Http({"result": {"orderId": "OID-1", "clientOrderId": "ord_x"}})
    orders, _ = _orders(monkeypatch, http)
    assert orders.buy_kr("069500", 2, client_order_id="ord_x") == "OID-1"
    (request,) = http.sent
    assert request.get_method() == "POST" and urllib.parse.urlparse(request.full_url).path == toss_account.ORDERS_PATH
    assert json.loads(request.data) == {"clientOrderId": "ord_x", "symbol": "069500", "side": "BUY",
                                        "orderType": "MARKET", "timeInForce": "DAY", "quantity": "2"}
    assert request.headers["X-tossinvest-account"] == "7" and request.headers["Authorization"] == "Bearer TOKEN-A"


def test_the_egress_recheck_reads_the_opt_in_again_before_the_post(monkeypatch):
    http = _Http({"result": {"orderId": "OID-1"}})
    orders, _ = _orders(monkeypatch, http)
    monkeypatch.delenv(toss_orders.TOSS_ORDERS_ENV)
    with pytest.raises(SafetyGateBlocked):
        orders.buy_kr("069500", 1, client_order_id="c1")
    assert http.sent == []


@pytest.mark.parametrize(("symbol", "quantity"), [("069500", 0), ("069500", 1.5), ("069500", True), ("VT", 1),
                                                  ("06950", 1), ("", 1)])
def test_only_whole_shares_of_a_domestic_code(monkeypatch, symbol, quantity):
    http = _Http()
    orders, _ = _orders(monkeypatch, http)
    with pytest.raises(ToolError) as refused:
        orders.buy_kr(symbol, quantity, client_order_id="c1")
    assert refused.value.reason_code == toss_orders.ORDER_REFUSED and http.sent == []


def test_the_adapter_has_no_way_to_sell_modify_or_cancel():
    public = [n for n in dir(toss_orders.TossBuyOrders) if not n.startswith("_")]
    assert sorted(public) == ["buy_kr", "enabled"] and toss_orders.SIDE == "BUY"


@pytest.mark.parametrize(("answer", "kind"), [
    ((422, "insufficient-buying-power"), toss_orders.OrderRejected),
    ((400, "invalid-request"), toss_orders.OrderRejected),
    ((409, "opposite-pending-order-exists"), toss_orders.OrderRejected),
    ((429, "rate-limit-exceeded"), toss_orders.OrderRejected),
    ((409, "request-in-progress"), toss_orders.OrderUnknown),
    ((500, "internal-error"), toss_orders.OrderUnknown),
    (TimeoutError(), toss_orders.OrderUnknown),
    (urllib.error.URLError("reset"), toss_orders.OrderUnknown),
    ({"result": {}}, toss_orders.OrderUnknown),
])
def test_a_rejection_is_told_apart_from_an_unknown_outcome(monkeypatch, answer, kind):
    orders, _ = _orders(monkeypatch, _Http(answer))
    with pytest.raises(kind):
        orders.buy_kr("069500", 1, client_order_id="c1")


def test_a_revoked_token_is_reissued_once_and_the_same_order_resent(monkeypatch):
    http = _Http((401, "token-revoked"), {"result": {"orderId": "OID-2"}})
    orders, feed = _orders(monkeypatch, http)
    assert orders.buy_kr("069500", 1, client_order_id="c9") == "OID-2"
    assert feed.issued == 1 and [json.loads(r.data)["clientOrderId"] for r in http.sent] == ["c9", "c9"]
    assert http.sent[1].headers["Authorization"] == "Bearer TOKEN-B"


# --- the executor -----------------------------------------------------------------------------------------------

CANDIDATES = {"domestic_equity": ["toss:domestic:069500"], "bonds": ["toss:domestic:114260"],
              "gold": ["toss:domestic:411060"]}
PRICES = {"069500": 85_995.0, "114260": 62_355.0, "411060": 23_765.0}
B = "069500=1,114260=1,411060=2"                     # Thomas's scenario B (2026-10-26)
APPROVED = "2026-10-26T15:00:00Z"
OPEN = "2026-10-27T01:00:00Z"                        # 10:00 KST, a Tuesday
EARLY = "2026-10-27T00:02:00Z"                       # 09:02 KST: inside the first five minutes
LATE = "2026-10-27T06:10:00Z"                        # 15:10 KST: inside the last thirty


class _Feed:
    def __init__(self, cash=200_000.0, prices=None, session=True, status=None, kind="ETF"):
        self.cash, self.prices, self.session, self.status, self.kind = cash, prices or PRICES, session, status, kind

    def kr_market_day(self, date, **_):
        if not self.session:
            return {"date": date, "integrated": None}
        return {"date": date, "integrated": {"regularMarket": {"startTime": "09:00:00", "endTime": "15:30:00"}}}

    def stock_info(self, symbols, **_):
        return [{"symbol": s, "status": (self.status or {}).get(s, "ACTIVE"), "securityType": self.kind} for s in symbols]

    def last_prices(self, symbols, **_):
        return {s: self.prices[s] for s in symbols if s in self.prices}

    def commission_rates(self, **_):
        return [{"marketCountry": "KR", "commissionRate": "0.00015"}]

    def cash_krw(self, **_):
        return self.cash


class _Orders:
    enabled = True

    def __init__(self, fail_at=None, error=None):
        self.calls, self.fail_at, self.error = [], fail_at, error

    def buy_kr(self, symbol, quantity, *, client_order_id, **_):
        self.calls.append((symbol, quantity, client_order_id))
        if self.fail_at is not None and len(self.calls) - 1 == self.fail_at:
            raise self.error
        return f"OID-{symbol}"


def _approve(tmp_path, spec=B, max_krw=200_000):
    return ai.approve(tmp_path, spec, max_krw=max_krw, candidates=CANDIDATES, now=APPROVED)


def test_scenario_b_is_placed_once_inside_the_session(tmp_path):
    _approve(tmp_path)
    orders = _Orders()
    assert ai.run(_Feed(), orders, tmp_path, now=EARLY)["status"] == "outside_session" and orders.calls == []
    assert ai.run(_Feed(), orders, tmp_path, now=OPEN)["status"] == "executed:3"
    assert [(s, q) for s, q, _ in orders.calls] == [("069500", 1), ("114260", 1), ("411060", 2)]
    states = [(r["instrument"].split(":")[2], r["state"]) for r in ai.journal(tmp_path)]
    assert states == [("069500", "intent"), ("069500", "accepted"), ("114260", "intent"), ("114260", "accepted"),
                      ("411060", "intent"), ("411060", "accepted")]
    assert ai.run(_Feed(), orders, tmp_path, now="2026-10-27T02:00:00Z")["status"] == "none" and len(orders.calls) == 3
    token, text = ai.notice(tmp_path)
    assert "3건 접수" in text and not any(x in text for x in ("069500", "toss:", "KODEX", "85"))


def test_a_whole_share_order_under_50000_is_placed(tmp_path):
    _approve(tmp_path, spec="411060=2", max_krw=60_000)          # 47,530 KRW: RB4''
    orders = _Orders()
    assert ai.run(_Feed(), orders, tmp_path, now=OPEN)["status"] == "executed:1"


@pytest.mark.parametrize("now", [EARLY, LATE])
def test_nothing_is_placed_at_the_edges_of_the_session(tmp_path, now):
    _approve(tmp_path)
    orders = _Orders()
    ai.run(_Feed(), orders, tmp_path, now=now)
    assert orders.calls == []


def test_a_closed_market_day_places_nothing(tmp_path):
    _approve(tmp_path)
    orders = _Orders()
    assert ai.run(_Feed(session=False), orders, tmp_path, now=OPEN)["status"] == "outside_session"
    assert orders.calls == []


def test_with_the_order_path_off_it_holds_and_says_so_once(tmp_path):
    _approve(tmp_path)
    assert ai.run(_Feed(), toss_orders.NoBuyOrders(), tmp_path, now=OPEN)["status"] == "hold:orders_off"
    token, text = ai.notice(tmp_path)
    assert "MVP_TOSS_ORDERS" in text
    ai.mark_told(tmp_path, token, now=OPEN)
    ai.run(_Feed(), toss_orders.NoBuyOrders(), tmp_path, now="2026-10-27T02:00:00Z")
    assert ai.notice(tmp_path) is None


def test_short_buying_power_holds_the_whole_scenario_and_the_next_trading_day_tries_again(tmp_path):
    _approve(tmp_path)
    orders = _Orders()
    assert ai.run(_Feed(cash=150_000.0), orders, tmp_path, now=OPEN)["status"] == "hold:insufficient_buying_power"
    assert orders.calls == [] and json.loads(ai.scenario_path(tmp_path).read_text())["shortfall_krw"] > 0
    assert ai.run(_Feed(), orders, tmp_path, now="2026-10-27T02:00:00Z")["status"] == "done_today"
    assert ai.run(_Feed(), orders, tmp_path, now="2026-10-28T01:00:00Z")["status"] == "executed:3"


@pytest.mark.parametrize("broken", ["kr_market_day", "stock_info", "last_prices", "cash_krw"])
def test_a_read_that_fails_holds_and_is_told_never_a_half_order(tmp_path, broken):
    _approve(tmp_path)
    feed, orders = _Feed(), _Orders()

    def fail(*_a, **_kw):
        raise ToolError("TOOL_TRANSPORT", "down")

    setattr(feed, broken, fail)
    assert ai.run(feed, orders, tmp_path, now=OPEN)["status"] == "hold:read_failed" and orders.calls == []
    assert "토스 조회에 실패" in ai.notice(tmp_path)[1]
    # A calendar that could not be read consumed no day; the next fire that day looks again.
    if broken == "kr_market_day":
        assert ai.run(_Feed(), orders, tmp_path, now="2026-10-27T02:00:00Z")["status"] == "executed:3"


def test_the_daily_cap_counts_every_scenario_that_day(tmp_path):
    ai._append(tmp_path, {"at": OPEN, "day": "2026-10-27", "scenario_id": "old", "client_order_id": "x",
                          "instrument": "toss:domestic:069500", "quantity": 4, "est_krw": 400_000, "state": "intent"})
    _approve(tmp_path, spec="114260=2", max_krw=200_000)            # ~124,729 KRW: 400k + 125k > 500k
    orders = _Orders()
    assert ai.run(_Feed(), orders, tmp_path, now=OPEN)["status"] == "hold:over_daily_cap" and orders.calls == []


def test_an_estimate_over_the_approved_cap_holds(tmp_path):
    _approve(tmp_path, max_krw=150_000)
    orders = _Orders()
    assert ai.run(_Feed(), orders, tmp_path, now=OPEN)["status"] == "hold:over_scenario_cap" and orders.calls == []


def test_a_rejection_stops_the_rest_and_never_resends(tmp_path):
    _approve(tmp_path)
    orders = _Orders(fail_at=1, error=toss_orders.OrderRejected(422, "insufficient-buying-power"))
    assert ai.run(_Feed(), orders, tmp_path, now=OPEN)["status"] == "stopped:1"
    assert [s for s, _, _ in orders.calls] == ["069500", "114260"]
    assert ai.run(_Feed(), orders, tmp_path, now="2026-10-28T01:00:00Z")["status"] == "none" and len(orders.calls) == 2
    assert "insufficient-buying-power" in ai.notice(tmp_path)[1]


def test_an_unknown_outcome_stops_and_is_said_so(tmp_path):
    _approve(tmp_path)
    orders = _Orders(fail_at=0, error=toss_orders.OrderUnknown("timeout or dropped connection"))
    assert ai.run(_Feed(), orders, tmp_path, now=OPEN)["status"] == "unknown:0" and len(orders.calls) == 1
    assert "다시 보내지 않습니다" in ai.notice(tmp_path)[1]


def test_an_intent_with_no_outcome_is_never_resent(tmp_path):
    """A crash between the intent line and the POST: the next attempt sends nothing."""
    body = _approve(tmp_path)
    from runtime.read_only_kernel import integrity
    cid = integrity.short_id("ord", f"{body['scenario_id']}|toss:domestic:069500")
    ai._append(tmp_path, {"at": OPEN, "day": "2026-10-26", "scenario_id": body["scenario_id"], "client_order_id": cid,
                          "instrument": "toss:domestic:069500", "quantity": 1, "est_krw": 86_000, "state": "intent"})
    orders = _Orders()
    assert ai.run(_Feed(), orders, tmp_path, now=OPEN)["status"] == "unknown:0" and orders.calls == []


@pytest.mark.parametrize("feed", [_Feed(status={"114260": "DELISTED"}), _Feed(kind="STOCK")])
def test_anything_but_an_active_etf_stops_before_any_order(tmp_path, feed):
    _approve(tmp_path)
    orders = _Orders()
    assert ai.run(feed, orders, tmp_path, now=OPEN)["status"] == "stopped:not_an_active_etf" and orders.calls == []


def test_an_unapproved_instrument_a_typo_or_a_cap_over_the_day_is_refused_before_writing(tmp_path):
    for spec, cap in (("005930=1", 100_000), ("069501=1", 100_000), ("069500=0", 100_000), ("VT=1", 100_000),
                      ("069500=1", 600_000), ("069500=1,069500=1", 100_000)):
        with pytest.raises(ToolError) as refused:
            ai.approve(tmp_path, spec, max_krw=cap, candidates=CANDIDATES, now=APPROVED)
        assert refused.value.reason_code == ai.AUTOINVEST_REFUSED, spec
    assert not ai.scenario_path(tmp_path).exists()


def test_an_unexecuted_scenario_expires_after_seven_days(tmp_path):
    _approve(tmp_path)
    assert ai.run(_Feed(), _Orders(), tmp_path, now="2026-11-02T16:00:00Z")["status"] == "expired"
    assert "만료" in ai.notice(tmp_path)[1]


# --- the scheduler: a step after a refresh that landed, told after delivery -----------------------------------

def test_the_scheduler_runs_the_step_after_a_refresh_and_marks_its_notice_after_delivery(monkeypatch, tmp_path):
    _approve(store.state_dir(tmp_path))
    monkeypatch.setattr(store, "refresh", lambda **_kw: {"status": "holdings snapshot: refreshed", "alert": None})
    monkeypatch.setattr(store, "blocked_check", lambda **_kw: {})
    calls = []

    def fake_run(*, now, root):
        calls.append(now)
        return {"status": "executed:3", "alert": ai.notice(store.state_dir(root)) or ("tok", "자동 매수 3건 접수")}

    monkeypatch.setattr(store, "run_autoinvest", fake_run)
    told = []
    monkeypatch.setattr(store, "mark_autoinvest_told", lambda token, **_kw: told.append(token))
    monkeypatch.setattr(operator_mod, "select_operator_channel", lambda **_kw: "channel")

    def fire():
        schedule = scheduler.Schedule(schedule_id="schedule_holdings_test", kind=scheduler.KIND_HOLDINGS,
                                      request="", interval_seconds=3600, enabled=True, created_by="test",
                                      created_at=OPEN, next_run_at=OPEN)
        return scheduler._execute(schedule, now=OPEN, ledger=None, working_memory=None, programization=None,
                                  repo_root=tmp_path, executor=lambda **_: {})

    def down(channel, text, **_kw):
        raise OperatorBlocked("CHANNEL_DOWN", "down")

    monkeypatch.setattr(operator_mod, "notify_operator", down)
    status = fire()
    assert "autoinvest executed:3" in status and "autoinvest not sent:CHANNEL_DOWN" in status and told == []
    monkeypatch.setattr(operator_mod, "notify_operator", lambda channel, text, **_kw: None)
    assert "autoinvest told" in fire() and len(told) == 1


@pytest.mark.parametrize(("value", "capable"), [(None, False), ("buy_only", True)])
def test_the_store_step_hands_this_fires_feed_and_the_gated_adapter(monkeypatch, tmp_path, value, capable):
    feed = _feed()
    monkeypatch.setattr(store, "_feed", lambda: feed)
    if value:
        monkeypatch.setenv(toss_orders.TOSS_ORDERS_ENV, value)
    seen = []
    monkeypatch.setattr(ai, "run", lambda f, orders, state_dir, *, now: seen.append((f, orders)) or {"status": "x"})
    store.run_autoinvest(now=OPEN, root=tmp_path)
    ((f, orders),) = seen
    assert f is feed and isinstance(orders, toss_orders.TossBuyOrders) is capable


def test_a_refresh_that_did_not_land_runs_no_order_step(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "refresh", lambda **_kw: {"status": "holdings snapshot: not persisted (X)", "alert": None})
    monkeypatch.setattr(store, "blocked_check", lambda **_kw: {})
    monkeypatch.setattr(store, "run_autoinvest", lambda **_kw: pytest.fail("the order step ran"))
    schedule = scheduler.Schedule(schedule_id="schedule_holdings_test", kind=scheduler.KIND_HOLDINGS, request="",
                                  interval_seconds=3600, enabled=True, created_by="test", created_at=OPEN,
                                  next_run_at=OPEN)
    scheduler._execute(schedule, now=OPEN, ledger=None, working_memory=None, programization=None,
                       repo_root=tmp_path, executor=lambda **_: {})
