"""The venue contract sentinel (crypto PR4a, Thomas decisions 43-46).

What these pin is what makes a PASS worth reading:
- only an expectation measured at this venue can fail the contract; a hypothesis is recorded, never
  judged — and a venue that could not be asked is never a verdict either way;
- the -4120 probe is the request that measured the migration, frozen, not today's Algo shape, and it
  reaches the literal ``/fapi/v1/order/test``;
- the sentinel asks through its validator and its reads only — no order, no cancel — and measures
  that the validator left no order: none under its entry test's own id, none resting;
- it backs off: the first answer that says the venue could not be asked stops the run, and a fire the
  venue already rate limited is not run;
- a run that could not decide never erases a decided record, a FAIL is asked again on the next fire,
  and a record that cannot prove itself is refused, not read;
- it stays out of the API breaker and out of the execution stage.

The answers the JUDGED checks expect were seen at this venue: the 2026-09-19 exchangeInfo (numbers
copied from the public payload), the 2026-08-03 -4120, the 2026-09-02 leverage. The failure codes
(-1003, -1021, -2015, -1104, 503) are stand-ins for the failure modes they name, and the observed
checks' answers here are placeholders — what the host answers is exactly what 4a exists to record.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from runtime.mvp_runtime.crypto import account_store, live_execution, market_data, paper
from runtime.mvp_runtime.crypto import venue_contract as vc
from runtime.mvp_runtime.errors import ToolError

NOW = "2026-09-19T07:10:00Z"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "DOGEUSDT"]
PRICES = {"BTCUSDT": 60000.0, "ETHUSDT": 2500.0, "BNBUSDT": 600.0, "SOLUSDT": 150.0, "DOGEUSDT": 0.2}
ORDER_TYPES = ["LIMIT", "MARKET", "STOP", "STOP_MARKET", "TAKE_PROFIT", "TAKE_PROFIT_MARKET",
               "TRAILING_STOP_MARKET"]


def _symbol_row(symbol, tick, step, min_qty, max_qty, market_max, notional, up, down):
    """One symbol as the public exchangeInfo listed it on 2026-09-19, reduced to what is read."""
    return {
        "symbol": symbol, "status": "TRADING", "contractType": "PERPETUAL",
        "quoteAsset": "USDT", "marginAsset": "USDT",
        # The migrated conditional types are still listed: exchangeInfo cannot see the migration.
        "orderTypes": list(ORDER_TYPES), "timeInForce": ["GTC", "IOC", "FOK", "GTX", "GTD"],
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": tick},
            {"filterType": "LOT_SIZE", "stepSize": step, "minQty": min_qty, "maxQty": max_qty},
            {"filterType": "MARKET_LOT_SIZE", "stepSize": step, "minQty": min_qty, "maxQty": market_max},
            {"filterType": "MIN_NOTIONAL", "notional": notional},
            {"filterType": "PERCENT_PRICE", "multiplierUp": up, "multiplierDown": down},
        ],
    }


def _exchange_info():
    return {"symbols": [
        _symbol_row("BTCUSDT", "0.10", "0.001", "0.001", "1000", "120", "50", "1.0500", "0.9500"),
        _symbol_row("ETHUSDT", "0.01", "0.001", "0.001", "10000", "2000", "20", "1.0500", "0.9500"),
        _symbol_row("BNBUSDT", "0.010", "0.01", "0.01", "100000", "2000", "5", "1.0500", "0.9500"),
        _symbol_row("DOGEUSDT", "0.000010", "1", "1", "300000000", "30000000", "5", "1.1000", "0.9000"),
        _symbol_row("SOLUSDT", "0.0100", "0.01", "0.01", "1000000", "80000", "5", "1.0500", "0.9500"),
    ]}


MOVED = {"accepted": False, "code": -4120,
         "msg": "Order type not supported for this endpoint. Please use the Algo Order API endpoints instead."}


class _Adapter:
    """The live adapter's surface as the sentinel may use it."""

    network_egress = True

    def __init__(self, *, conditional=MOVED, hedge=False, resting=(), algo_resting=(), fail=(),
                 found=None, raise_on=None):
        self.calls: list[tuple] = []
        self.fetched: list[tuple] = []      # (symbol, id, algo): GET /fapi/v1/order looks up per symbol
        self.conditional = conditional
        self.hedge = hedge
        self.resting = list(resting)
        self.algo_resting = list(algo_resting)
        self.fail = set(fail)
        self.found = found or {}          # client id -> the order the venue would return
        self.raise_on = raise_on or {}    # call name -> the exception it raises

    def _maybe_fail(self, name):
        if name in self.raise_on:
            raise self.raise_on[name]
        if name in self.fail:
            raise ToolError("ORDER_TRANSPORT", "live order request failed or timed out")

    def validate_order(self, request, *, timeout_seconds=10):
        self.calls.append(("validate_order", dict(request)))
        self._maybe_fail("validate_order")
        if request.get("type") == "STOP_MARKET" and not request.get("algoType"):
            return dict(self.conditional)
        return {"accepted": True, "code": None, "msg": None}

    def position_mode(self, *, timeout_seconds=10):
        self.calls.append(("position_mode",))
        self._maybe_fail("position_mode")
        return self.hedge

    def fetch_order(self, symbol, client_order_id, *, timeout_seconds=10, algo=False):
        self.calls.append(("fetch_order", client_order_id, algo))
        self.fetched.append((symbol, client_order_id, algo))
        self._maybe_fail("fetch_order")
        return self.found.get(client_order_id)

    def open_orders(self, symbol=None, *, timeout_seconds=10):
        self.calls.append(("open_orders", symbol))
        self._maybe_fail("open_orders")
        return list(self.resting)

    def algo_open_orders(self, symbol=None, *, timeout_seconds=10):
        self.calls.append(("algo_open_orders", symbol))
        self._maybe_fail("algo_open_orders")
        return list(self.algo_resting)


class _Collector:
    def __init__(self, payload=None, rate_limited=None):
        self.payload = payload if payload is not None else _exchange_info()
        self.rate_limited = rate_limited

    def exchange_info(self, *, timeout_seconds):
        return self.payload


def _snapshot(as_of="2026-09-19T07:00:00Z", **leverage):
    return {"as_of": as_of, "configured_leverage": {s: 5.0 for s in SYMBOLS} | leverage}


@pytest.fixture(autouse=True)
def _prices(monkeypatch):
    monkeypatch.setattr(market_data, "read_reference_quote",
                        lambda symbol, **_: {"price": PRICES[symbol], "reason": None})


def _run(adapter=None, *, collector=None, snapshot=None, clock=None, symbols=SYMBOLS):
    kwargs = {"clock": clock} if clock is not None else {}
    return vc.run_checks(symbols=symbols, adapter=adapter or _Adapter(), collector=collector or _Collector(),
                         now=NOW, root=None, snapshot=snapshot if snapshot is not None else _snapshot(), **kwargs)


def _by_id(checks):
    return {c["check"]: c for c in checks}


# --- the judge ----------------------------------------------------------------------------------

def test_the_measured_venue_passes_every_judged_check_and_the_hypotheses_are_only_recorded():
    checks = _run()
    assert vc.judge(checks) == vc.STATUS_PASS
    by_id = _by_id(checks)
    assert {cid: by_id[cid]["result"] for cid in vc.JUDGED_CHECKS} == dict.fromkeys(vc.JUDGED_CHECKS, "PASS")
    assert {cid: by_id[cid]["result"] for cid in vc.OBSERVED_CHECKS} == dict.fromkeys(vc.OBSERVED_CHECKS, "OBSERVED")
    assert [c["check"] for c in checks][-2:] == [vc.CHECK_ENTRY_LEFT_NO_ORDER, vc.CHECK_NOTHING_RESTING]


def test_a_hypothesis_can_never_fail_the_contract_even_when_a_record_calls_it_judged():
    checks = _run()
    forged = [dict(c, result="FAIL", judged=True) if c["check"] in vc.OBSERVED_CHECKS else c for c in checks]
    assert vc.judge(forged) == vc.STATUS_PASS


def test_one_judged_failure_fails_and_one_unanswered_judged_check_decides_nothing():
    checks = _run()
    failed = [dict(c, result="FAIL") if c["check"] == vc.CHECK_LEVERAGE else c for c in checks]
    assert vc.judge(failed) == vc.STATUS_FAIL
    unasked = [dict(c, result="UNVERIFIED") if c["check"] == vc.CHECK_POSITION_MODE else c for c in checks]
    assert vc.judge(unasked) == vc.STATUS_UNVERIFIED
    assert vc.judge([c for c in checks if c["check"] != vc.CHECK_EXCHANGE_INFO]) == vc.STATUS_UNVERIFIED


# --- exchangeInfo -------------------------------------------------------------------------------

def test_exchange_info_records_the_price_band_the_filter_reader_does_not_use():
    check = vc.check_exchange_info(_exchange_info(), SYMBOLS)
    assert check["result"] == "PASS"
    symbols = check["observed"]["symbols"]
    assert symbols["BTCUSDT"]["percent_price"] == {"up": 1.05, "down": 0.95}
    assert symbols["DOGEUSDT"]["percent_price"] == {"up": 1.1, "down": 0.9}
    assert symbols["BTCUSDT"]["tick_size"] == 0.1 and symbols["BTCUSDT"]["min_notional"] == 50.0
    # Still listed 2026-09-19, which is exactly why this check could not have caught 2026-08-02.
    assert "STOP_MARKET" in symbols["BTCUSDT"]["order_types"]


@pytest.mark.parametrize("change,problem", [
    ({"status": "BREAK"}, "status 'BREAK'"),
    ({"contractType": "CURRENT_QUARTER"}, "contractType"),
    ({"marginAsset": "BTC"}, "marginAsset"),
    ({"orderTypes": ["MARKET", "STOP_MARKET"]}, "no order type LIMIT"),
    ({"timeInForce": ["IOC"]}, "no GTC"),
    ({"filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.10"}]}, "filters:"),
])
def test_a_listing_this_runtime_cannot_trade_on_fails(change, problem):
    payload = _exchange_info()
    payload["symbols"][0].update(change)
    check = vc.check_exchange_info(payload, SYMBOLS)
    assert check["result"] == "FAIL"
    assert any(problem in p for p in check["observed"]["problems"]["BTCUSDT"])


def test_an_unlisted_symbol_and_a_shapeless_payload_fail_but_an_unread_one_is_unverified():
    assert vc.check_exchange_info(_exchange_info(), ["XRPUSDT"])["observed"]["problems"] == {"XRPUSDT": ["not listed"]}
    assert vc.check_exchange_info({"rateLimits": []}, SYMBOLS)["result"] == "FAIL"
    unread = vc.check_exchange_info(None, SYMBOLS, failure={"error": "TOOL_TRANSPORT"})
    assert unread["result"] == "UNVERIFIED"


# --- the -4120 ----------------------------------------------------------------------------------

def test_the_probe_is_the_measured_shape_and_reaches_the_literal_validator_path():
    probe = vc.legacy_conditional_probe("BTCUSDT", stop_price=54000.0, client_id="TAI_VC_C_0123456789abcdef")
    assert tuple(probe) == vc.LEGACY_CONDITIONAL_PROBE_KEYS
    assert "algoType" not in probe and probe["closePosition"] == "true" and probe["type"] == "STOP_MARKET"
    assert live_execution.is_algo_request(probe) is False

    adapter = live_execution.BinanceFuturesOrderAdapter.__new__(live_execution.BinanceFuturesOrderAdapter)
    sent = []

    def signed(method, path, params, *, timeout_seconds):
        sent.append((method, path))
        return live_execution._SignedAnswer({"code": -4120, "msg": MOVED["msg"]}, -4120, 400)

    adapter._signed_request = signed
    assert adapter.validate_order(probe) == {"accepted": False, "code": -4120, "msg": MOVED["msg"],
                                             "http_status": 400}
    # The literal, not the constant: a constant re-pointed at the order endpoint would pass a test that
    # compares the path with itself, while the hourly entry test became a real MARKET BUY (review of #902).
    assert sent == [("POST", "/fapi/v1/order/test")]
    assert live_execution.ORDER_TEST_PATH == "/fapi/v1/order/test"


@pytest.mark.parametrize("answer,result", [
    (MOVED, "PASS"),
    ({"accepted": True, "code": None, "msg": None}, "FAIL"),              # the order API takes it again
    ({"accepted": False, "code": -1104, "msg": "Not all sent parameters were read"}, "FAIL"),
    ({"accepted": False, "code": -1003, "msg": "Too many requests"}, "UNVERIFIED"),   # could not ask
    ({"accepted": False, "code": -1021, "msg": "Timestamp outside recvWindow"}, "UNVERIFIED"),
    ({"accepted": False, "code": -2015, "msg": "Invalid API-key"}, "UNVERIFIED"),
    ({"accepted": False, "code": -1010, "msg": "unlisted", "http_status": 503}, "UNVERIFIED"),  # any 5xx
    ({"accepted": False, "code": -1010, "msg": "unlisted", "http_status": 429}, "UNVERIFIED"),
    ({"accepted": None, "code": None, "msg": None, "supported": False}, "UNVERIFIED"),
])
def test_only_a_business_answer_judges_the_migration(answer, result):
    assert vc.check_conditional_refused(answer)["result"] == result


def test_the_validator_answer_carries_its_http_status_only_when_the_venue_sent_one():
    adapter = live_execution.BinanceFuturesOrderAdapter.__new__(live_execution.BinanceFuturesOrderAdapter)
    adapter._signed_request = lambda m, p, params, *, timeout_seconds: ({"code": -2021, "msg": "x"}, -2021)
    assert adapter.validate_order({"symbol": "ETHUSDT"}) == {"accepted": False, "code": -2021, "msg": "x"}


def test_a_probe_that_could_not_be_sent_is_unverified():
    assert vc.check_conditional_refused(None, failure={"error": "ORDER_TRANSPORT"})["result"] == "UNVERIFIED"
    checks = _run(_Adapter(fail={"validate_order"}))
    assert _by_id(checks)[vc.CHECK_CONDITIONAL_REFUSED]["result"] == "UNVERIFIED"
    assert vc.judge(checks) == vc.STATUS_UNVERIFIED


# --- backing off --------------------------------------------------------------------------------

def test_a_rate_limit_answer_stops_the_run_at_once():
    adapter = _Adapter(conditional={"accepted": False, "code": -1003, "msg": "Too many requests"})
    checks = _run(adapter)
    assert [c[0] for c in adapter.calls] == ["validate_order"]
    by_id = _by_id(checks)
    assert by_id[vc.CHECK_POSITION_MODE]["observed"]["error"] == vc.RUN_STOPPED
    assert by_id[vc.CHECK_POSITION_MODE]["observed"]["after"]["venue_code"] == -1003
    assert vc.judge(checks) == vc.STATUS_UNVERIFIED


def test_a_raised_venue_failure_stops_the_run_and_a_business_refusal_does_not():
    banned = ToolError("ORDER_REJECTED", "banned", data={"venue_code": -1003, "http_status": 418})
    adapter = _Adapter(raise_on={"position_mode": banned})
    _run(adapter)
    assert [c[0] for c in adapter.calls] == ["validate_order", "position_mode"]
    business = ToolError("ORDER_REJECTED", "bad parameter", data={"venue_code": -1102, "http_status": 400})
    adapter = _Adapter(raise_on={"position_mode": business})
    checks = _run(adapter)
    assert adapter.calls[-1][0] == "algo_open_orders"
    assert _by_id(checks)[vc.CHECK_POSITION_MODE]["observed"] == {"error": "ORDER_REJECTED", "venue_code": -1102,
                                                                  "http_status": 400}


def test_a_fire_the_venue_already_rate_limited_asks_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(vc, "_registered_symbols", lambda root, now: list(SYMBOLS))
    adapter = _Adapter()
    line = vc.refresh_verification(collector=_Collector(rate_limited=ToolError("TOOL_RATE_LIMITED", "429")),
                                   now=NOW, root=tmp_path, adapter=adapter)
    assert line == "venue contract: not verified (rate_limited_this_fire)" and adapter.calls == []


def test_a_rate_limit_latched_mid_run_stops_the_signed_calls_too():
    collector = _Collector()
    adapter = _Adapter()

    def latch_then_answer(*_, **__):
        collector.rate_limited = ToolError("TOOL_RATE_LIMITED", "429")
        return {"accepted": False, "code": -4120, "msg": MOVED["msg"]}

    adapter.validate_order = lambda request, *, timeout_seconds=10: (
        adapter.calls.append(("validate_order", dict(request))) or latch_then_answer())
    checks = _run(adapter, collector=collector)
    assert [c[0] for c in adapter.calls] == ["validate_order"]
    assert _by_id(checks)[vc.CHECK_POSITION_MODE]["observed"]["after"] == {"error": "MARKET_DATA_RATE_LIMITED"}


# --- leverage and position mode -----------------------------------------------------------------

def test_leverage_at_or_below_the_backtests_passes_and_above_fails():
    check = vc.check_leverage(_snapshot(SOLUSDT=3.0), SYMBOLS, now=NOW, max_leverage=5.0)
    assert check["result"] == "PASS"
    above = vc.check_leverage(_snapshot(DOGEUSDT=20.0), SYMBOLS, now=NOW, max_leverage=5.0)
    assert above["result"] == "FAIL" and "DOGEUSDT 20x" in above["detail"]
    assert above["observed"]["above"] == ["DOGEUSDT"]
    assert paper.ASSUMED_LEVERAGE == 5   # decision 45's bound is the backtests' own number


@pytest.mark.parametrize("snapshot", [
    None,
    _snapshot(as_of="2026-09-19T06:20:00Z", DOGEUSDT=20.0),            # 50 minutes: judged in neither direction
    _snapshot(as_of="2026-09-19T06:20:00Z"),
    {"as_of": "2026-09-19T07:00:00Z", "degraded": True, "configured_leverage": {}},
    {"as_of": "2026-09-19T07:00:00Z", "configured_leverage": {"BTCUSDT": 5.0}},            # others unreported
])
def test_leverage_the_account_did_not_currently_state_is_unverified(snapshot):
    assert vc.check_leverage(snapshot, SYMBOLS, now=NOW, max_leverage=5.0)["result"] == "UNVERIFIED"
    assert vc.LEVERAGE_SNAPSHOT_MAX_AGE_SECONDS == 3 * account_store.REFRESH_AFTER_SECONDS


def test_hedge_mode_fails_and_an_unanswered_mode_is_unverified_even_on_a_404():
    assert _by_id(_run(_Adapter(hedge=True)))[vc.CHECK_POSITION_MODE]["result"] == "FAIL"
    assert vc.check_position_mode(None, failure={"error": "ORDER_TRANSPORT", "http_status": 404})["result"] == "UNVERIFIED"
    assert vc.check_position_mode("false")["result"] == "UNVERIFIED"


def test_the_position_mode_read_never_reads_an_unclear_answer_as_one_way():
    adapter = live_execution.BinanceFuturesOrderAdapter.__new__(live_execution.BinanceFuturesOrderAdapter)
    answers = iter([({"dualSidePosition": False}, None), ({"dualSidePosition": True}, None), ({}, None),
                    ({"code": -2015, "msg": "Invalid API-key"}, -2015)])
    sent = []

    def signed(method, path, params, *, timeout_seconds):
        sent.append((method, path, dict(params)))
        body, code = next(answers)
        return live_execution._SignedAnswer(body, code, 401 if code else None)

    adapter._signed_request = signed
    assert adapter.position_mode() is False
    assert adapter.position_mode() is True
    with pytest.raises(ToolError) as malformed:
        adapter.position_mode()
    assert malformed.value.reason_code == "ORDER_MALFORMED_RESULT"
    with pytest.raises(ToolError) as refused:
        adapter.position_mode()
    assert refused.value.data == {"venue_code": -2015, "http_status": 401}
    assert {(m, p) for m, p, _ in sent} == {("GET", "/fapi/v1/positionSide/dual")}


# --- what the sentinel sends, and what it leaves ------------------------------------------------

def test_the_sentinel_asks_only_through_the_validator_and_the_reads():
    adapter = _Adapter()
    _run(adapter)
    assert {call[0] for call in adapter.calls} == {
        "validate_order", "position_mode", "fetch_order", "open_orders", "algo_open_orders"}
    sent_ids = [c[1].get("newClientOrderId") or c[1].get("clientAlgoId") for c in adapter.calls if c[0] == "validate_order"]
    queried_ids = [c[1] for c in adapter.calls if c[0] == "fetch_order"]
    assert sent_ids and all(i.startswith(vc.SENTINEL_ID_PREFIX) for i in sent_ids + queried_ids)
    assert all(live_execution.CLIENT_ORDER_ID_PATTERN.match(i) for i in sent_ids + queried_ids)
    # One-way mode and every entry request are the runtime's own: no positionSide, built by the builder.
    entries = [c[1] for c in adapter.calls if c[0] == "validate_order" and c[1].get("type") == "MARKET"]
    assert [e["symbol"] for e in entries] == SYMBOLS
    assert all("positionSide" not in e and e["reduceOnly"] is False for e in entries)
    # The entry test's own id is asked for afterwards, on the order API, not the algo one.
    assert ("fetch_order", entries[0]["newClientOrderId"], False) in adapter.calls


def test_the_module_imports_and_calls_nothing_that_can_place_or_cancel():
    source = Path(vc.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    called = {node.func.attr for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and isinstance(node.func.value, ast.Name) and node.func.value.id == "adapter"}
    assert called == {"validate_order", "position_mode", "fetch_order", "open_orders", "algo_open_orders"}
    forbidden = {"submit", "cancel_order", "submit_and_reconcile", "place_bracket_leg", "_signed_request",
                 "execute_live_entry", "ApiErrorRecordingAdapter"}
    names = ({n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
             | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
             | {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names})
    assert not names & forbidden, sorted(names & forbidden)


def test_an_entry_test_that_left_an_order_fails_although_nothing_rests():
    """A filled order rests nowhere: only its own id finds it (review of #902)."""
    probe_adapter = _Adapter()
    _run(probe_adapter)
    entry_id = next(c[1]["newClientOrderId"] for c in probe_adapter.calls
                    if c[0] == "validate_order" and c[1].get("type") == "MARKET")
    adapter = _Adapter(found={entry_id: {"status": "FILLED", "clientOrderId": entry_id}})
    checks = _run(adapter)
    check = _by_id(checks)[vc.CHECK_ENTRY_LEFT_NO_ORDER]
    assert check["result"] == "FAIL" and check["observed"]["status"] == "FILLED"
    assert _by_id(checks)[vc.CHECK_NOTHING_RESTING]["result"] == "PASS"
    assert vc.judge(checks) == vc.STATUS_FAIL


def test_an_entry_test_never_sent_has_no_order_to_find_and_one_that_failed_locally_is_still_asked_for():
    not_sent = vc.check_entry_left_no_order(None, sent=False)
    assert not_sent["result"] == "PASS" and not_sent["observed"] == {"sent": False}
    adapter = _Adapter(fail={"validate_order"})
    _run(adapter)
    # The probe's transport failure stops the run before any entry test: nothing sent, nothing asked.
    assert "fetch_order" not in [c[0] for c in adapter.calls]
    # A call made that failed without stopping the run counts as sent, and its id is asked for.
    odd = _Adapter(raise_on={"validate_order": ValueError("the builder refused it")})
    odd.conditional = MOVED
    checks = _run(odd)
    assert _by_id(checks)[vc.CHECK_ENTRY_TEST]["observed"]["symbols"]["BTCUSDT"]["sent"] is True
    assert any(c[0] == "fetch_order" and c[2] is False for c in odd.calls)


def test_an_order_the_validator_left_resting_fails_and_the_runtimes_own_legs_do_not():
    own = [{"clientOrderId": "TAI_BTCUSDT_TP_764f36ae2ac555eeeb", "symbol": "BTCUSDT"}]
    assert _by_id(_run(_Adapter(resting=own)))[vc.CHECK_NOTHING_RESTING]["result"] == "PASS"
    left = _run(_Adapter(algo_resting=[{"clientAlgoId": "TAI_VC_C_b60d4eca1c4f5e7d", "symbol": "BTCUSDT"}]))
    check = _by_id(left)[vc.CHECK_NOTHING_RESTING]
    assert check["result"] == "FAIL" and check["observed"]["sentinel_left"] == ["TAI_VC_C_b60d4eca1c4f5e7d"]
    assert vc.judge(left) == vc.STATUS_FAIL
    unread = _run(_Adapter(fail={"algo_open_orders"}))
    assert _by_id(unread)[vc.CHECK_NOTHING_RESTING]["result"] == "UNVERIFIED"


def test_the_hypotheses_record_what_the_venue_answered():
    by_id = _by_id(_run())
    entry = by_id[vc.CHECK_ENTRY_TEST]["observed"]["symbols"]["BTCUSDT"]
    assert entry == {"sent": True, "quantity": 0.001, "accepted": True, "code": None, "msg": None}
    legs = by_id[vc.CHECK_TARGET_TEST]["observed"]["legs"]
    assert (legs["LONG_TP"]["side"], legs["SHORT_TP"]["side"]) == ("SELL", "BUY")
    assert legs["LONG_TP"]["price_over_reference"] == pytest.approx(1.10)
    assert by_id[vc.CHECK_ALGO_QUERY]["observed"] == {"answer": "not_found"}


def test_the_target_request_is_the_runtimes_reduce_only_limit_beyond_the_band():
    adapter = _Adapter()
    _run(adapter)
    limits = [c[1] for c in adapter.calls if c[0] == "validate_order" and c[1].get("type") == "LIMIT"]
    assert [(r["side"], r["price"]) for r in limits] == [("SELL", 66000.0), ("BUY", 54000.0)]
    assert all(r["reduceOnly"] is True and r["timeInForce"] == "GTC" for r in limits)


def test_a_failure_keeps_the_venue_code_and_the_http_status():
    failure = vc._failure(ToolError("ORDER_REJECTED", "x", data={"venue_code": -2013, "http_status": 400}))
    assert failure == {"error": "ORDER_REJECTED", "venue_code": -2013, "http_status": 400}
    assert vc._failure(ToolError("ORDER_TRANSPORT", "x")) == {"error": "ORDER_TRANSPORT"}


# --- the budget ---------------------------------------------------------------------------------

class _Clock:
    """Seconds pass only when the adapter is called."""

    def __init__(self, per_call, adapter):
        self.t = 0.0
        self.per_call = per_call
        self.adapter = adapter
        self.seen = 0

    def __call__(self):
        if len(self.adapter.calls) > self.seen:
            self.t += self.per_call * (len(self.adapter.calls) - self.seen)
            self.seen = len(self.adapter.calls)
        return self.t


@pytest.mark.parametrize("per_call", [3.0, 3.9])
def test_a_venue_answering_within_its_timeout_always_reaches_a_decision(per_call):
    adapter = _Adapter()
    checks = _run(adapter, clock=_Clock(per_call, adapter))
    assert vc.judge(checks) == vc.STATUS_PASS
    entries = _by_id(checks)[vc.CHECK_ENTRY_TEST]["observed"]["symbols"]
    assert any(v == {"error": "RUN_BUDGET_SPENT"} for v in entries.values())
    assert [c[0] for c in adapter.calls][-3:] == ["fetch_order", "open_orders", "algo_open_orders"]


def test_a_call_starts_only_with_its_full_timeout_left():
    budget = vc._Budget(lambda: 0.0, 30.0)
    assert budget.timeout(reserve=vc.FINAL_RESERVE_SECONDS) == vc.CALL_TIMEOUT_SECONDS
    assert budget.timeout(reserve=26.5) is None     # 3.5s left: not enough for a full call
    assert budget.timeout(reserve=26.0) == vc.CALL_TIMEOUT_SECONDS


def test_a_budget_spent_before_the_judged_checks_decides_nothing():
    adapter = _Adapter()
    checks = _run(adapter, clock=_Clock(20.0, adapter))
    assert vc.judge(checks) == vc.STATUS_UNVERIFIED
    assert _by_id(checks)[vc.CHECK_POSITION_MODE]["observed"] == {"error": vc.RUN_BUDGET_SPENT}


# --- the two files ------------------------------------------------------------------------------

@pytest.fixture
def _scope(monkeypatch):
    monkeypatch.setattr(vc, "_registered_symbols", lambda root, now: list(SYMBOLS))


def _write_snapshot(root, as_of="2026-09-19T07:00:00Z", **leverage):
    path = account_store.snapshot_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_snapshot(as_of=as_of, **leverage)), encoding="utf-8")


def _refresh(root, adapter, **kwargs):
    return vc.refresh_verification(collector=_Collector(), now=kwargs.pop("now", NOW), root=root, adapter=adapter,
                                   **kwargs)


def test_a_decided_run_is_recorded_and_reads_back_usable(tmp_path, _scope):
    _write_snapshot(tmp_path)
    assert _refresh(tmp_path, _Adapter()) == "venue contract: PASS"
    record = vc.read_verification(tmp_path)
    assert record["status"] == "PASS" and record["contract_version"] == vc.CONTRACT_VERSION
    assert record["not_verified"] == list(vc.NOT_VERIFIED)
    status = vc.verification_status(tmp_path, now="2026-09-19T12:00:00Z")
    assert status["usable"] is True and status["stale"] is False and status["symbols"] == SYMBOLS
    mark = vc.read_refresh_mark(tmp_path)
    assert (mark["outcome"], mark["decided_status"]) == (vc.OUTCOME_DECIDED, "PASS")


def test_the_attempt_is_marked_before_the_venue_is_asked(tmp_path, _scope, monkeypatch):
    seen = {}

    def run_checks(**_):
        seen["mark"] = vc.read_refresh_mark(tmp_path)
        return []

    monkeypatch.setattr(vc, "run_checks", run_checks)
    _refresh(tmp_path, _Adapter())
    assert seen["mark"] == {"attempted_at": NOW, "outcome": vc.OUTCOME_STARTED}


def test_a_run_that_could_not_ask_keeps_the_decided_record_and_moves_only_the_mark(tmp_path, _scope):
    _write_snapshot(tmp_path)
    _refresh(tmp_path, _Adapter())
    before = vc.contract_path(tmp_path).read_text(encoding="utf-8")
    line = _refresh(tmp_path, _Adapter(fail={"position_mode", "open_orders"}), now="2026-09-19T08:10:00Z")
    assert line.startswith("venue contract: UNVERIFIED")
    assert vc.contract_path(tmp_path).read_text(encoding="utf-8") == before
    mark = vc.read_refresh_mark(tmp_path)
    assert mark["outcome"] == vc.OUTCOME_INCOMPLETE and mark["attempted_at"] == "2026-09-19T08:10:00Z"
    assert mark["decided_status"] == "PASS"


def test_a_violation_overwrites_a_pass_at_once_and_is_asked_again_on_the_next_fire(tmp_path, _scope):
    _write_snapshot(tmp_path)
    _refresh(tmp_path, _Adapter())
    _write_snapshot(tmp_path, as_of="2026-09-19T08:05:00Z")
    assert _refresh(tmp_path, _Adapter(hedge=True), now="2026-09-19T08:10:00Z") == "venue contract: FAIL (position_mode)"
    status = vc.verification_status(tmp_path, now="2026-09-19T08:11:00Z")
    assert (status["status"], status["usable"], status["failed_checks"]) == ("FAIL", False, ["position_mode"])
    mark = vc.read_refresh_mark(tmp_path)
    assert vc.is_due(mark, "2026-09-19T08:25:00Z") is True       # the next fire, not an hour later
    assert vc.is_due(mark, "2026-09-19T08:15:00Z") is False
    # A later run that cannot decide still leaves the FAIL standing, and still asks sooner.
    _refresh(tmp_path, _Adapter(fail={"position_mode"}), now="2026-09-19T08:25:00Z")
    assert vc.is_due(vc.read_refresh_mark(tmp_path), "2026-09-19T08:40:00Z") is True


def test_a_symbol_failure_is_told_apart_from_an_account_failure(tmp_path, _scope):
    payload = _exchange_info()
    payload["symbols"][3]["status"] = "BREAK"                       # DOGEUSDT
    _write_snapshot(tmp_path, SOLUSDT=10.0)
    vc.refresh_verification(collector=_Collector(payload), now=NOW, root=tmp_path, adapter=_Adapter())
    status = vc.verification_status(tmp_path, now=NOW)
    assert status["symbol_failures"] == {"DOGEUSDT": ["exchange_info"], "SOLUSDT": ["configured_leverage"]}
    assert set(status["failed_checks"]) == {"exchange_info", "configured_leverage"}


def test_nothing_is_asked_without_live_trading_or_a_budget(tmp_path, monkeypatch):
    adapter = _Adapter()
    adapter.network_egress = False
    monkeypatch.setattr(vc, "_registered_symbols", lambda root, now: list(SYMBOLS))
    assert _refresh(tmp_path, adapter) == "venue contract: not verified (live_trading_not_opted_in)"
    monkeypatch.setattr(vc, "_registered_symbols", lambda root, now: [])
    assert _refresh(tmp_path, _Adapter()) == "venue contract: not verified (no_registered_symbols)"
    assert adapter.calls == [] and not vc.contract_path(tmp_path).exists()


def test_without_a_live_opt_in_the_selected_adapter_is_inert_and_asked_nothing(tmp_path, _scope):
    # conftest strips every gate opt-in: the real selector hands back the dry-run adapter.
    line = vc.refresh_verification(collector=_Collector(), now=NOW, root=tmp_path)
    assert line == "venue contract: not verified (live_trading_not_opted_in)"


def test_the_symbols_are_the_valid_budgets_allowlist(tmp_path):
    from runtime.mvp_runtime.crypto import live_budget

    assert vc._registered_symbols(tmp_path, NOW) == []
    record = live_budget.build_live_trading_budget_record(
        caps=dict(max_order_notional_usdt=60.0, absolute_max_notional_usdt=200.0, max_daily_order_count=2,
                  max_open_notional_usdt=120.0, daily_loss_limit_usdt=20.0),
        symbol_allowlist=["BTCUSDT", "ETHUSDT"], registered_by="thomas", registered_at="2026-08-30T09:19:11Z")
    live_budget.write_registered_budget(record, root=tmp_path)
    assert vc._registered_symbols(tmp_path, NOW) == ["BTCUSDT", "ETHUSDT"]


def test_a_budget_past_its_window_names_no_symbols(tmp_path):
    """Only a VALID budget authorizes a live order, so only a valid one names what to verify."""
    from runtime.read_only_kernel import integrity
    from runtime.mvp_runtime.crypto import live_budget

    body = {
        "schema_version": "live_trading_budget.v0.1", "budget_id": "budget_0123456789abcdef0123",
        "venue": "binance_futures", "symbol_allowlist": ["BTCUSDT"],
        "caps": {"max_order_notional_usdt": 60.0, "absolute_max_notional_usdt": 200.0,
                 "max_daily_order_count": 2, "max_open_notional_usdt": 120.0, "daily_loss_limit_usdt": 20.0},
        "valid_from": "2026-07-25T00:00:00Z", "valid_until": "2026-08-25T00:00:00Z",
        "registered_by": "thomas", "registered_at": "2026-07-25T00:00:00Z",
    }
    body["record_sha256"] = integrity.sha256_record(body)
    path = live_budget.budget_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")
    assert vc._registered_symbols(tmp_path, NOW) == []


def test_the_refresh_never_raises(tmp_path, monkeypatch, _scope):
    def boom(**_):
        raise RuntimeError("anything at all")

    monkeypatch.setattr(vc, "run_checks", boom)
    assert _refresh(tmp_path, _Adapter()) == "venue contract: not verified (RuntimeError)"
    assert vc.read_refresh_mark(tmp_path)["outcome"] == vc.OUTCOME_ERROR


@pytest.mark.parametrize("mark,due", [
    (None, True),
    ({"attempted_at": "2026-09-19T06:16:00Z"}, False),
    ({"attempted_at": "2026-09-19T06:15:00Z"}, True),
    ({"attempted_at": "2026-09-19T09:00:00Z"}, True),                     # dated in the future
    ({"attempted_at": "garbage"}, True),
    ({"attempted_at": "2026-09-19T07:01:00Z", "decided_status": "FAIL"}, False),
    ({"attempted_at": "2026-09-19T07:00:00Z", "decided_status": "FAIL"}, True),
    ({"attempted_at": "2026-09-19T07:00:00Z", "decided_status": "PASS", "decided_version": vc.CONTRACT_VERSION},
     False),
])
def test_asked_about_hourly_on_a_fifteen_minute_fire_and_every_fire_while_failing(mark, due):
    assert vc.is_due(mark, NOW) is due
    assert vc.REFRESH_AFTER_SECONDS < 3600 and vc.RETRY_AFTER_FAIL_SECONDS < 15 * 60


_DECIDED = {"decided_status": "PASS", "decided_version": vc.CONTRACT_VERSION, "decided_symbols": ["BTCUSDT"]}


@pytest.mark.parametrize("decided,symbols,sooner", [
    (_DECIDED, ["BTCUSDT"], False),
    (_DECIDED, [" btcusdt "], False),                                   # the doors' own coverage rule
    (_DECIDED, [], False),                                              # no valid budget, nothing to cover
    (_DECIDED, ["BTCUSDT", "ETHUSDT"], True),                           # the budget gained a symbol
    ({**_DECIDED, "decided_version": "binance_futures_contract.v0"}, ["BTCUSDT"], True),
    ({"decided_status": "PASS"}, [], True),                             # a mark from before PR4b: once
    ({**_DECIDED, "decided_status": "FAIL"}, ["BTCUSDT"], True),
    ({"decided_status": None}, ["BTCUSDT"], False),                     # nothing decided keeps the hour
], ids=["covered", "normalized", "no-budget", "budget-gained", "other-version", "pre-pr4b-mark", "fail",
        "undecided"])
def test_a_record_that_refuses_what_the_next_ask_can_let_through_is_asked_at_the_next_fire(decided, symbols,
                                                                                           sooner):
    """Review of #903: a deploy that bumps the contract version refuses every entry, and a budget that
    gains a symbol refuses that symbol's, until the next decided run — so the next fire asks, as it
    does on a FAIL. A run that decided nothing keeps the hour (decision 44)."""
    mark = {"attempted_at": "2026-09-19T06:55:00Z", **decided}          # the fire before NOW
    assert vc.is_due(mark, NOW, symbols=symbols) is sooner
    assert vc.is_due({**mark, "attempted_at": "2026-09-19T07:05:00Z"}, NOW, symbols=symbols) is False


def test_the_refresh_marks_what_the_record_was_verified_under_and_the_fire_asks_when_it_falls_short(
        tmp_path, monkeypatch, _scope):
    _write_snapshot(tmp_path)
    _refresh(tmp_path, _Adapter())
    mark = vc.read_refresh_mark(tmp_path)
    assert (mark["decided_status"], mark["decided_version"], mark["decided_symbols"]) == \
        ("PASS", vc.CONTRACT_VERSION, SYMBOLS)
    next_fire = "2026-09-19T07:25:00Z"
    assert vc.refresh_due(tmp_path, next_fire) is False
    monkeypatch.setattr(vc, "_registered_symbols", lambda root, now: [*SYMBOLS, "XRPUSDT"])
    assert vc.refresh_due(tmp_path, next_fire) is True, "the budget gained a symbol"
    monkeypatch.setattr(vc, "_registered_symbols", lambda root, now: list(SYMBOLS))
    monkeypatch.setattr(vc, "CONTRACT_VERSION", "binance_futures_contract.v9")     # the deploy that bumps it
    assert vc.entry_refusal(vc.entry_fact(tmp_path), symbol="BTCUSDT", at=next_fire)["reason_code"] \
        == vc.ENTRY_CONTRACT_VERSION
    assert vc.refresh_due(tmp_path, next_fire) is True, "another version"


def test_the_fire_s_cadence_question_never_raises(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("anything at all")

    monkeypatch.setattr(vc, "_registered_symbols", boom)
    assert vc.refresh_due(tmp_path, NOW) is True, "a question it cannot answer is due"
    monkeypatch.setattr(vc, "read_refresh_mark", boom)
    assert vc.refresh_due(tmp_path, NOW) is True


def test_the_refresh_never_raises_on_a_record_it_cannot_read_back(tmp_path, monkeypatch, _scope):
    """The mark reads the decided record after the run; an unexpected exception there (a permission
    error from `Path.is_file` on 3.12) is a mark without a decided status, not a raise in the fire."""
    def boom(root=None):
        raise PermissionError("scripted")

    _write_snapshot(tmp_path)
    monkeypatch.setattr(vc, "read_verification", boom)
    assert _refresh(tmp_path, _Adapter()) == "venue contract: PASS"
    assert vc.read_refresh_mark(tmp_path)["decided_status"] is None


def test_only_a_decided_verification_can_be_recorded():
    with pytest.raises(ToolError) as refused:
        vc.build_record(status=vc.STATUS_UNVERIFIED, checks=_run(), symbols=SYMBOLS, now=NOW)
    assert refused.value.reason_code == vc.VENUE_CONTRACT_INVALID


# --- the verified read --------------------------------------------------------------------------

def _stored(tmp_path, record):
    path = vc.contract_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")


def test_a_pass_stands_six_hours_and_only_for_this_contract_version(tmp_path):
    from runtime.read_only_kernel import integrity

    record = vc.build_record(status="PASS", checks=_run(), symbols=SYMBOLS, now=NOW)
    _stored(tmp_path, record)
    assert vc.verification_status(tmp_path, now="2026-09-19T13:10:00Z")["usable"] is True
    assert vc.verification_status(tmp_path, now="2026-09-19T13:10:01Z")["stale"] is True
    assert vc.verification_status(tmp_path, now="2026-09-19T07:06:00Z")["usable"] is True   # clocks disagree a little
    assert vc.verification_status(tmp_path, now="2026-09-19T07:04:00Z")["usable"] is False  # not ten minutes ahead
    older = dict(record, contract_version="binance_futures_contract.v0")
    older["record_sha256"] = integrity.sha256_record({k: v for k, v in older.items() if k != "record_sha256"})
    _stored(tmp_path, older)
    status = vc.verification_status(tmp_path, now=NOW)
    assert (status["version_current"], status["usable"]) == (False, False)


def test_a_record_that_cannot_prove_itself_is_refused(tmp_path):
    from runtime.read_only_kernel import integrity

    failed = vc.build_record(status="FAIL", checks=_run(_Adapter(hedge=True)), symbols=SYMBOLS, now=NOW)
    _stored(tmp_path, dict(failed, status="PASS"))
    with pytest.raises(ToolError) as tampered:
        vc.verification_status(tmp_path, now=NOW)
    assert tampered.value.reason_code == vc.VENUE_CONTRACT_TAMPERED

    invalid = dict(failed, status="MAYBE")
    invalid["record_sha256"] = integrity.sha256_record({k: v for k, v in invalid.items() if k != "record_sha256"})
    _stored(tmp_path, invalid)
    with pytest.raises(ToolError) as schema:
        vc.read_verification(tmp_path)
    assert schema.value.reason_code == vc.VENUE_CONTRACT_INVALID

    vc.contract_path(tmp_path).write_text("{not json", encoding="utf-8")
    with pytest.raises(ToolError) as unreadable:
        vc.read_verification(tmp_path)
    assert unreadable.value.reason_code == vc.VENUE_CONTRACT_UNREADABLE


def test_none_recorded_is_not_usable_and_raises_nothing(tmp_path):
    status = vc.verification_status(tmp_path, now=NOW)
    assert (status["recorded"], status["usable"], status["symbols"]) == (False, False, [])


def test_a_damaged_mark_neither_stops_the_asking_nor_breaks_a_reader(tmp_path):
    path = vc.refresh_mark_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"attempted_at": NOW, "outcome": vc.OUTCOME_DECIDED, "checks": ["x"]}),
                    encoding="utf-8")
    mark = vc.read_refresh_mark(tmp_path)
    assert "checks" not in mark and vc.status_line(mark) == "venue contract: None"
    assert vc.status_line({"outcome": vc.OUTCOME_INCOMPLETE, "status": "UNVERIFIED", "checks": ["x", None]}) == (
        "venue contract: UNVERIFIED")
    path.write_text("[1, 2]", encoding="utf-8")
    assert vc.read_refresh_mark(tmp_path) is None and vc.is_due(None, NOW) is True


# --- what it is not -----------------------------------------------------------------------------

def test_the_record_is_never_execution_stage_evidence():
    """Decision 3: `/order/test` does not stand in for the signed testnet cycle."""
    repo = Path(vc.__file__).resolve().parents[3]
    for rel in ("runtime/mvp_runtime/crypto/execution_stage.py", "scripts/register_execution_stage.py",
                "runtime/mvp_runtime/crypto/testnet_evidence.py"):
        text = (repo / rel).read_text(encoding="utf-8")
        assert "venue_contract" not in text, rel


def test_the_sentinel_is_not_a_door_the_api_breaker_counts():
    """Decision 27 counts the money path's signed calls; the sentinel asks the raw adapter."""
    from runtime.mvp_runtime.crypto import live_order

    assert "position_mode" not in live_order.API_ADAPTER_CALLS
    assert "select_live_api_breaker" not in Path(vc.__file__).read_text(encoding="utf-8")


# --- the board, the fire and the CLI ------------------------------------------------------------

def test_the_readiness_board_shows_the_last_decision_and_the_last_attempt(tmp_path, _scope):
    from runtime.mvp_runtime.crypto import live_readiness

    _write_snapshot(tmp_path)
    _refresh(tmp_path, _Adapter(hedge=True))
    board = live_readiness._venue_contract(tmp_path, now="2026-09-19T07:20:00Z")
    detail = live_readiness._venue_contract_detail(board, uncovered=[])
    assert "FAIL (failed: position_mode)" in detail and "not usable" in detail and "last attempt" in detail
    assert "every mainnet entry is refused" in detail
    data = live_readiness.readiness_data({"venue_contract": board})["venue_contract"]
    assert (data["usable"], data["status"], data["failed_checks"], data["symbols"]) == (
        False, "FAIL", ["position_mode"], SYMBOLS)
    vc.contract_path(tmp_path).write_text("{", encoding="utf-8")
    assert "UNREADABLE (VENUE_CONTRACT_UNREADABLE)" in live_readiness._venue_contract_detail(
        live_readiness._venue_contract(tmp_path, now=NOW), uncovered=[])
    assert "none recorded" in live_readiness._venue_contract_detail({}, uncovered=[])


def test_the_rendered_board_carries_the_row(tmp_path):
    """PR4b: a check row, and a failing one while nothing is recorded — the doors refuse on it."""
    from runtime.mvp_runtime.crypto import live_readiness

    text = live_readiness.render_readiness_text(live_readiness.build_readiness(root=tmp_path, now=NOW))
    line = next(row for row in text.splitlines() if "venue_contract" in row)
    assert line.startswith("[FAIL] venue_contract") and "none recorded" in line


def test_the_pipeline_fire_asks_after_the_account_refresh_with_its_own_collector_about_hourly(tmp_path, monkeypatch):
    from runtime.mvp_runtime.control import ControlStore
    from runtime.mvp_runtime.crypto.market_data import PerRunFeedCache
    from runtime.mvp_runtime.scheduler import KIND_CRYPTO, ScheduleStore, build_schedule, run_due

    order: list[str] = []
    asked: list[tuple] = []

    def account_refresh(*, now, root, **_):
        order.append("account")
        return "account snapshot: stub"

    def venue_refresh(*, collector, now, root, **_):
        order.append("venue")
        asked.append((type(collector), now, root))
        vc._write_json(vc.refresh_mark_path(root), {"attempted_at": now, "outcome": vc.OUTCOME_DECIDED,
                                                    "status": "PASS", "decided_status": "PASS",
                                                    "decided_version": vc.CONTRACT_VERSION,
                                                    "decided_symbols": ["BTCUSDT"]},
                       code="VENUE_CONTRACT_MARK_LOCKED", label="test mark")
        return "venue contract: PASS"

    monkeypatch.setattr(account_store, "refresh_snapshot", account_refresh)
    monkeypatch.setattr(vc, "refresh_verification", venue_refresh)
    # The cadence is what is under test here; the notice that follows has its own (PR4b-2).
    monkeypatch.setattr(vc, "notice", lambda root=None, *, now: {"changed": False, "text": "", "state": {}})
    store = ScheduleStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.add(build_schedule(kind=KIND_CRYPTO, request="", interval_seconds=900, created_by="op",
                             now="2026-07-22T11:00:00Z"))
    statuses = []
    for now in ("2026-07-22T13:00:00Z", "2026-07-22T13:15:00Z", "2026-07-22T13:30:00Z",
                "2026-07-22T13:45:00Z", "2026-07-22T14:00:00Z"):
        summary = run_due(store, now=now, control_store=ControlStore(tmp_path), ledger=None, repo_root=tmp_path)
        statuses.append(summary["results"][0]["status"])
    assert [a[1] for a in asked] == ["2026-07-22T13:00:00Z", "2026-07-22T14:00:00Z"]
    assert all(a[0] is PerRunFeedCache and a[2] == tmp_path for a in asked)
    assert order[:2] == ["account", "venue"]
    assert statuses[0].endswith("account snapshot: stub venue contract: PASS")
    assert "venue contract" not in statuses[1]


def test_the_pipeline_fire_asks_at_every_fire_while_the_budget_names_a_symbol_the_record_does_not(
        tmp_path, monkeypatch):
    """The fire hands the cadence the budget's symbols as they are now (review of #903)."""
    from runtime.mvp_runtime.control import ControlStore
    from runtime.mvp_runtime.scheduler import KIND_CRYPTO, ScheduleStore, build_schedule, run_due

    asked: list[str] = []

    def venue_refresh(*, collector, now, root, **_):
        asked.append(now)
        vc._write_json(vc.refresh_mark_path(root), {"attempted_at": now, "outcome": vc.OUTCOME_INCOMPLETE,
                                                    "status": "UNVERIFIED", "decided_status": "PASS",
                                                    "decided_version": vc.CONTRACT_VERSION,
                                                    "decided_symbols": ["BTCUSDT"]},
                       code="VENUE_CONTRACT_MARK_LOCKED", label="test mark")
        return "venue contract: UNVERIFIED"

    monkeypatch.setattr(account_store, "refresh_snapshot", lambda **_: "account snapshot: stub")
    monkeypatch.setattr(vc, "refresh_verification", venue_refresh)
    monkeypatch.setattr(vc, "_registered_symbols", lambda root, now: ["BTCUSDT", "ETHUSDT"])
    store = ScheduleStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.add(build_schedule(kind=KIND_CRYPTO, request="", interval_seconds=900, created_by="op",
                             now="2026-07-22T11:00:00Z"))
    fires = ("2026-07-22T13:00:00Z", "2026-07-22T13:15:00Z", "2026-07-22T13:30:00Z")
    for now in fires:
        run_due(store, now=now, control_store=ControlStore(tmp_path), ledger=None, repo_root=tmp_path)
    assert asked == list(fires)


def test_the_cli_shows_and_runs_as_the_fire_would(tmp_path, monkeypatch, capsys, _scope):
    from scripts import venue_contract as cli

    from runtime.mvp_runtime import timeutil

    assert cli.main(["--root", str(tmp_path)]) == 0
    assert "venue contract: none recorded" in capsys.readouterr().out

    _write_snapshot(tmp_path, as_of=timeutil.utc_now_iso())
    monkeypatch.setattr("runtime.mvp_runtime.crypto.market_data.select_market_data_collector",
                        lambda **_: _Collector())
    monkeypatch.setattr(cli, "assert_not_foreign_root_run", lambda root: None)
    real = vc.refresh_verification

    def run(adapter):
        monkeypatch.setattr(vc, "refresh_verification",
                            lambda **kw: real(**{**kw, "adapter": adapter}))
        return cli.main(["--run", "--root", str(tmp_path)])

    assert run(_Adapter()) == 0
    assert "venue contract: PASS" in capsys.readouterr().out
    assert run(_Adapter(hedge=True)) == cli.EXIT_FAIL
    assert "not usable: mainnet entries are refused" in capsys.readouterr().out     # PR4b
    assert run(_Adapter(fail={"position_mode"})) == 2        # not decided by this run
    assert "NOT verified by this run" in capsys.readouterr().err

    def refuse(root):
        raise ToolError("STATE_ROOT_FOREIGN_OWNER", "run as the service user")

    monkeypatch.setattr(cli, "assert_not_foreign_root_run", refuse)
    assert cli.main(["--run", "--root", str(tmp_path)]) == 2


# --- the entry doors' reading (PR4b, Thomas decision 46) ----------------------------------------

def test_entry_fact_carries_the_judged_fields_and_never_raises(tmp_path, monkeypatch):
    from tests._helpers import record_venue_contract

    assert vc.entry_fact(tmp_path) == {"recorded": False}
    record = record_venue_contract(tmp_path, SYMBOLS, verified_at=NOW)
    assert vc.entry_fact(tmp_path) == {"recorded": True, **{f: record[f] for f in vc.ENTRY_FACT_FIELDS}}
    vc.contract_path(tmp_path).write_text(json.dumps({**record, "symbols": ["DOGEUSDT"]}), encoding="utf-8")
    assert vc.entry_fact(tmp_path) == {"recorded": True, "error": vc.VENUE_CONTRACT_TAMPERED}
    vc.contract_path(tmp_path).write_text("{", encoding="utf-8")
    assert vc.entry_fact(tmp_path) == {"recorded": True, "error": vc.VENUE_CONTRACT_UNREADABLE}

    def _broken(root=None):
        raise RuntimeError("scripted")

    monkeypatch.setattr(vc, "read_verification", _broken)
    assert vc.entry_fact(tmp_path) == {"recorded": True, "error": "RuntimeError"}


def test_entry_refusal_names_the_first_reason_in_a_fixed_order():
    fact = {"recorded": True, "status": "FAIL", "contract_version": "binance_futures_contract.v0",
            "verified_at": None, "symbols": [], "failed_checks": ["exchange_info"]}
    assert vc.entry_refusal(fact, symbol="BTCUSDT", at=NOW)["reason_code"] == vc.ENTRY_CONTRACT_VERSION
    fact["contract_version"] = vc.CONTRACT_VERSION
    refusal = vc.entry_refusal(fact, symbol="BTCUSDT", at=NOW)
    assert (refusal["reason_code"], refusal["failed_checks"]) == (vc.ENTRY_CONTRACT_NOT_PASS, ["exchange_info"])
    fact["status"] = vc.STATUS_PASS
    assert vc.entry_refusal(fact, symbol="BTCUSDT", at=NOW)["reason_code"] == vc.ENTRY_CONTRACT_STALE
    fact["verified_at"] = NOW
    assert vc.entry_refusal(fact, symbol="BTCUSDT", at=NOW)["reason_code"] == vc.ENTRY_CONTRACT_SYMBOL
    assert vc.entry_refusal(fact, symbol=None, at=NOW) is None, "the board judges the record alone"
    fact["symbols"] = ["BTCUSDT"]
    assert vc.entry_refusal(fact, symbol="BTCUSDT", at=NOW) is None
    assert vc.entry_refusal(fact, symbol="", at=NOW)["reason_code"] == vc.ENTRY_CONTRACT_SYMBOL
    assert vc.entry_refusal(fact, symbol="BTCUSDT", at=None)["reason_code"] == vc.ENTRY_CONTRACT_STALE


@pytest.mark.parametrize("failed,at,usable", [
    ((), NOW, True),
    (("position_mode",), NOW, False),
    ((), "2026-09-19T13:10:00Z", True),
    ((), "2026-09-19T13:10:01Z", False),
    ((), "2026-09-19T07:04:59Z", False),
], ids=["pass", "fail", "six-hours", "six-hours-and-a-second", "dated-past-the-skew"])
def test_the_board_and_the_doors_judge_with_one_function(tmp_path, failed, at, usable):
    from tests._helpers import record_venue_contract

    record_venue_contract(tmp_path, SYMBOLS, verified_at=NOW, failed=failed)
    assert vc.verification_status(tmp_path, now=at)["usable"] is usable
    assert (vc.entry_refusal(vc.entry_fact(tmp_path), symbol=None, at=at) is None) is usable


def test_an_age_is_never_an_exception():
    for stamp, now in ((NOW, None), (None, NOW), ("yesterday", NOW), (NOW, "tomorrow"), (NOW, 7)):
        assert vc._age_seconds(stamp, now) is None


def test_coverage_is_one_rule_that_ignores_case_and_space_and_covers_no_empty_symbol():
    assert vc.covers(["BTCUSDT"], " btcusdt ") and vc.covers((" ethusdt ",), "ETHUSDT")
    assert not vc.covers(["", "BTCUSDT"], "") and not vc.covers([" "], "  ")
    assert not vc.covers("BTCUSDT", "BTCUSDT") and not vc.covers(None, "BTCUSDT")
    assert not vc.covers({"BTCUSDT": 1}, "BTCUSDT") and not vc.covers(["BTCUSDT"], None)


def test_the_entry_id_asked_for_is_the_first_symbol_the_entry_test_was_sent_for(monkeypatch):
    """PR4b-2: a first symbol skipped for want of a price left nothing under its id; the next one's
    request went out, so its id is the one asked for — and an order found there fails the run."""
    monkeypatch.setattr(market_data, "read_reference_quote", lambda symbol, **_: {
        "price": None if symbol == "BTCUSDT" else PRICES[symbol], "reason": None})
    adapter = _Adapter()
    checks = _run(adapter)
    entries = _by_id(checks)[vc.CHECK_ENTRY_TEST]["observed"]["symbols"]
    assert "skipped" in entries["BTCUSDT"] and entries["ETHUSDT"]["sent"] is True
    eth_id = next(c[1]["newClientOrderId"] for c in adapter.calls if c[0] == "validate_order"
                  and c[1].get("type") == "MARKET" and c[1].get("symbol") == "ETHUSDT")
    assert [f for f in adapter.fetched if f[2] is False] == [("ETHUSDT", eth_id, False)]
    check = _by_id(checks)[vc.CHECK_ENTRY_LEFT_NO_ORDER]
    assert check["result"] == "PASS"
    assert check["observed"] == {"sent": True, "symbol": "ETHUSDT", "answer": "not_found"}
    left = _run(_Adapter(found={eth_id: {"status": "FILLED", "clientOrderId": eth_id}}))
    assert _by_id(left)[vc.CHECK_ENTRY_LEFT_NO_ORDER]["result"] == "FAIL"


# --- the operator notice (PR4b-2) ----------------------------------------------------------------

def _told(root, now):
    """One notice as the fire delivers it: the mark moves only when something was said."""
    result = vc.notice(root, now=now)
    if result["changed"]:
        vc.write_notice_mark(result["state"], root=root)
    return result


def test_the_first_notice_is_a_first_report_and_a_reading_that_holds_is_quiet(tmp_path):
    from tests._helpers import record_venue_contract

    record_venue_contract(tmp_path, SYMBOLS, verified_at=NOW)
    first = _told(tmp_path, NOW)
    assert first["changed"] is True and first["state"]["reading"] == vc.READING_USABLE
    assert first["text"].startswith("CRYPTO VENUE CONTRACT - first report")
    assert "the stage and every other door still apply" in first["text"]
    # The next hour's PASS: the doors answer the same, so the operator hears nothing.
    record_venue_contract(tmp_path, SYMBOLS, verified_at="2026-09-19T08:10:00Z")
    quiet = vc.notice(tmp_path, now="2026-09-19T08:11:00Z")
    assert (quiet["changed"], quiet["text"]) == (False, "")


def test_a_pass_that_turns_fail_is_told_with_what_failed_and_so_is_the_recovery(tmp_path):
    from tests._helpers import record_venue_contract

    record_venue_contract(tmp_path, SYMBOLS, verified_at=NOW)
    _told(tmp_path, NOW)
    record_venue_contract(tmp_path, SYMBOLS, verified_at="2026-09-19T07:25:00Z", failed=("position_mode",))
    fail = _told(tmp_path, "2026-09-19T07:26:00Z")
    assert fail["changed"] is True and fail["state"]["reading"] == vc.ENTRY_CONTRACT_NOT_PASS
    assert fail["text"].startswith("CRYPTO VENUE CONTRACT NOT USABLE - mainnet entries refused")
    assert "failed: position_mode" in fail["text"] and f"was      : USABLE (told at {NOW})" in fail["text"]
    assert vc.notice(tmp_path, now="2026-09-19T07:41:00Z")["changed"] is False     # still FAIL: quiet
    record_venue_contract(tmp_path, SYMBOLS, verified_at="2026-09-19T07:55:00Z")
    back = _told(tmp_path, "2026-09-19T07:56:00Z")
    assert back["text"].startswith("CRYPTO VENUE CONTRACT USABLE - mainnet entries are backed again")


def test_a_pass_that_goes_stale_is_told_without_any_new_record(tmp_path):
    from tests._helpers import record_venue_contract

    record_venue_contract(tmp_path, SYMBOLS, verified_at=NOW)
    _told(tmp_path, NOW)
    assert vc.notice(tmp_path, now="2026-09-19T13:10:00Z")["changed"] is False       # six hours: still usable
    stale = vc.notice(tmp_path, now="2026-09-19T13:10:01Z")
    assert stale["changed"] is True and stale["state"]["reading"] == vc.ENTRY_CONTRACT_STALE
    assert "STALE - no usable PASS within six hours of this fire" in stale["text"]


def test_a_reason_that_changes_while_refusing_is_told_and_a_damaged_record_is_named(tmp_path):
    from tests._helpers import record_venue_contract

    record_venue_contract(tmp_path, SYMBOLS, verified_at=NOW, failed=("position_mode",))
    _told(tmp_path, NOW)
    vc.contract_path(tmp_path).write_text("{", encoding="utf-8")
    damaged = vc.notice(tmp_path, now=NOW)
    assert damaged["changed"] is True and damaged["state"]["reading"] == vc.ENTRY_CONTRACT_UNREADABLE
    assert damaged["text"].startswith("CRYPTO VENUE CONTRACT - still not usable, for another reason")
    assert "record   : unreadable (VENUE_CONTRACT_UNREADABLE)" in damaged["text"]


def test_a_mark_nobody_can_read_makes_the_next_notice_a_first_report(tmp_path):
    from tests._helpers import record_venue_contract

    record_venue_contract(tmp_path, SYMBOLS, verified_at=NOW)
    _told(tmp_path, NOW)
    vc.notice_mark_path(tmp_path).write_text("not json", encoding="utf-8")
    again = vc.notice(tmp_path, now=NOW)
    assert again["changed"] is True and again["text"].startswith("CRYPTO VENUE CONTRACT - first report")


def test_every_notice_is_ascii():
    readings = [vc.READING_USABLE, *sorted(vc.ENTRY_CONTRACT_CODES)]
    for reading in readings:
        current = {"reading": reading, "status": "FAIL", "verified_at": NOW, "contract_version": vc.CONTRACT_VERSION,
                   "failed_checks": ["position_mode"], "error": None}
        for previous in (None, {"reading": vc.READING_USABLE, "announced_at": NOW},
                         {"reading": vc.ENTRY_CONTRACT_STALE, "announced_at": NOW}):
            assert vc.render_notice(current, previous).isascii(), (reading, previous)


def test_the_pipeline_fire_tells_the_operator_once_on_the_edge_and_again_after_a_failed_send(tmp_path, monkeypatch):
    """After its own ask, the fire says what the next fire's doors will read — once. An undelivered
    notice leaves the mark where it was, so the next fire sends it (the breaker watch's posture)."""
    from runtime.mvp_runtime import operator as operator_mod
    from runtime.mvp_runtime.control import ControlStore
    from runtime.mvp_runtime.scheduler import KIND_CRYPTO, ScheduleStore, build_schedule, run_due
    from tests._helpers import record_venue_contract

    sent: list[str] = []
    down = {"transport": True}

    def notify(channel, text, *, repo_root=None):
        if down["transport"]:
            raise RuntimeError("scripted transport failure")
        sent.append(text)

    monkeypatch.setattr(account_store, "refresh_snapshot", lambda **_: "account snapshot: stub")
    monkeypatch.setattr(vc, "refresh_verification", lambda **_: "venue contract: PASS")
    monkeypatch.setattr(operator_mod, "select_operator_channel", lambda **_: "channel")
    monkeypatch.setattr(operator_mod, "notify_operator", notify)
    record_venue_contract(tmp_path, ["BTCUSDT"], verified_at="2026-07-22T12:59:00Z")
    store = ScheduleStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.add(build_schedule(kind=KIND_CRYPTO, request="", interval_seconds=900, created_by="op",
                             now="2026-07-22T11:00:00Z"))

    def fire(now):
        summary = run_due(store, now=now, control_store=ControlStore(tmp_path), ledger=None, repo_root=tmp_path)
        return summary["results"][0]["status"]

    assert fire("2026-07-22T13:00:00Z").endswith(
        "venue contract: PASS venue contract notice not sent (RuntimeError)")
    waiting = vc.read_notice_mark(tmp_path)
    assert "reading" not in waiting and [r["reading"] for r in waiting["undelivered"]] == ["USABLE"]
    assert sent == []
    down["transport"] = False
    assert fire("2026-07-22T13:15:00Z").endswith("venue contract: PASS venue contract notice sent (USABLE)")
    assert len(sent) == 1 and sent[0].startswith("CRYPTO VENUE CONTRACT - first report")
    assert "missed" not in sent[0], "a retry of the same reading reads as the original"
    assert "venue contract notice" not in fire("2026-07-22T13:30:00Z") and len(sent) == 1
    told = vc.read_notice_mark(tmp_path)
    assert told["announced_at"] == "2026-07-22T13:15:00Z" and "undelivered" not in told, "a quiet fire writes nothing"


# --- which entry-test id is asked for (review of #904) --------------------------------------------

REFUSED = {"accepted": False, "code": -4131,
           "msg": "The counterparty's best price does not meet the PERCENT_PRICE filter limit."}
FILLED = {"status": "FILLED"}


class _EntryAnswers(_Adapter):
    """The MARKET entry test answered per symbol as scripted (an answer, or an exception to raise);
    every other request as the plain double does."""

    def __init__(self, answers=None, **kwargs):
        super().__init__(**kwargs)
        self.answers = answers or {}

    def validate_order(self, request, *, timeout_seconds=10):
        scripted = self.answers.get(request.get("symbol")) if request.get("type") == "MARKET" else None
        if scripted is None:
            return super().validate_order(request, timeout_seconds=timeout_seconds)
        self.calls.append(("validate_order", dict(request)))
        if isinstance(scripted, BaseException):
            raise scripted
        return dict(scripted)


@pytest.mark.parametrize("btc", [REFUSED, ValueError("could not encode the request")],
                         ids=["venue-refused", "adapter-raised"])
def test_the_id_asked_for_is_the_one_the_venue_accepted_so_a_filled_order_is_found(btc):
    """BTC's entry test was refused, or failed in a way the venue never answered; ETH's was accepted —
    on a path that drifted to the order endpoint, ETH's is the MARKET BUY that filled."""
    eth_id = vc._sentinel_id("E", NOW, "ETHUSDT")
    adapter = _EntryAnswers({"BTCUSDT": btc}, found={eth_id: {**FILLED, "clientOrderId": eth_id}})
    checks = _run(adapter)
    assert [f for f in adapter.fetched if f[2] is False] == [("ETHUSDT", eth_id, False)]
    assert _by_id(checks)[vc.CHECK_ENTRY_LEFT_NO_ORDER]["result"] == "FAIL"
    assert vc.judge(checks) == vc.STATUS_FAIL


def test_a_builder_refusal_is_not_sent_and_the_next_symbol_s_id_is_asked_for(monkeypatch):
    from runtime.mvp_runtime.crypto import live_execution

    real = live_execution.build_order_request

    def build(intent):
        if intent.get("symbol") == "BTCUSDT" and intent.get("order_type_exchange") == "MARKET":
            raise ToolError("MALFORMED_INTENT", "scripted builder refusal")
        return real(intent)

    monkeypatch.setattr(live_execution, "build_order_request", build)
    eth_id = vc._sentinel_id("E", NOW, "ETHUSDT")
    adapter = _Adapter(found={eth_id: {**FILLED, "clientOrderId": eth_id}})
    checks = _run(adapter)
    assert _by_id(checks)[vc.CHECK_ENTRY_TEST]["observed"]["symbols"]["BTCUSDT"] == \
        {"sent": False, "error": "MALFORMED_INTENT"}
    assert not any(c[0] == "validate_order" and c[1].get("symbol") == "BTCUSDT" and c[1].get("type") == "MARKET"
                   for c in adapter.calls)
    assert [f for f in adapter.fetched if f[2] is False] == [("ETHUSDT", eth_id, False)]
    assert _by_id(checks)[vc.CHECK_ENTRY_LEFT_NO_ORDER]["result"] == "FAIL"


def test_entry_tests_the_venue_refused_leave_no_id_worth_asking_for():
    adapter = _EntryAnswers({symbol: REFUSED for symbol in SYMBOLS})
    check = _by_id(_run(adapter))[vc.CHECK_ENTRY_LEFT_NO_ORDER]
    assert [f for f in adapter.fetched if f[2] is False] == []
    assert check["result"] == "PASS" and check["observed"] == {"sent": False}
    assert "accepted or left unanswered" in check["detail"]


@pytest.mark.parametrize("btc", [ToolError("ORDER_TRANSPORT", "live order request failed or timed out"),
                                 {"accepted": False, "code": None, "msg": "Service unavailable", "http_status": 503}],
                         ids=["transport", "5xx"])
def test_an_entry_test_left_unanswered_is_the_one_asked_for(btc):
    """No answer of the venue's, or a 5xx an order endpoint gives when it does not know either: the
    request may have landed. It also stops the run, so the query is not made and the check cannot pass."""
    adapter = _EntryAnswers({"BTCUSDT": btc})
    checks = _run(adapter)
    entries = _by_id(checks)[vc.CHECK_ENTRY_TEST]["observed"]["symbols"]
    assert entries["BTCUSDT"]["sent"] is True and entries["ETHUSDT"]["error"] == vc.RUN_STOPPED
    check = _by_id(checks)[vc.CHECK_ENTRY_LEFT_NO_ORDER]
    assert check["result"] == "UNVERIFIED"
    assert (check["observed"]["sent"], check["observed"]["symbol"], check["observed"]["error"]) == \
        (True, "BTCUSDT", vc.RUN_STOPPED)


# --- the notice: what the review of #904 pinned ----------------------------------------------------

def _pipeline(tmp_path, monkeypatch, *, request="", refresh=None, notify=None):
    """A crypto pipeline schedule on a temp root, fired at a given time; the fire's status comes back."""
    from runtime.mvp_runtime import operator as operator_mod
    from runtime.mvp_runtime.control import ControlStore
    from runtime.mvp_runtime.scheduler import KIND_CRYPTO, ScheduleStore, build_schedule, run_due

    monkeypatch.setattr(account_store, "refresh_snapshot", lambda **_: "account snapshot: stub")
    monkeypatch.setattr(vc, "refresh_verification", refresh or (lambda **_: "venue contract: PASS"))
    if notify is not None:
        monkeypatch.setattr(operator_mod, "select_operator_channel", lambda **_: "channel")
        monkeypatch.setattr(operator_mod, "notify_operator", notify)
    store = ScheduleStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.add(build_schedule(kind=KIND_CRYPTO, request=request, interval_seconds=900, created_by="op",
                             now="2026-07-22T11:00:00Z"))

    def fire(now):
        summary = run_due(store, now=now, control_store=ControlStore(tmp_path), ledger=None, repo_root=tmp_path)
        return summary["results"][0]["status"]

    return fire


def test_a_change_the_channel_did_not_take_is_told_even_after_the_reading_returned(tmp_path, monkeypatch):
    """USABLE told; a FAIL the next fire could not deliver; a PASS at the fire after. That FAIL refused
    the entries of the fire between, so the operator hears of it."""
    from tests._helpers import record_venue_contract

    sent, down = [], {"on": False}

    def notify(channel, text, **_):
        if down["on"]:
            raise RuntimeError("scripted transport failure")
        sent.append(text)

    answers = {"2026-07-22T13:00:00Z": (), "2026-07-22T13:15:00Z": ("position_mode",), "2026-07-22T13:30:00Z": ()}

    def refresh(*, collector, now, root):
        record_venue_contract(root, ["BTCUSDT"], verified_at=now, failed=answers[now])
        return "venue contract: stub"

    fire = _pipeline(tmp_path, monkeypatch, refresh=refresh, notify=notify)
    fire("2026-07-22T13:00:00Z")
    down["on"] = True
    assert fire("2026-07-22T13:15:00Z").endswith("venue contract notice not sent (RuntimeError)")
    down["on"] = False
    assert fire("2026-07-22T13:30:00Z").endswith("venue contract notice sent (USABLE)")
    assert len(sent) == 2
    assert sent[1].startswith("CRYPTO VENUE CONTRACT - a change was not delivered when it happened")
    assert ("  missed   : LIVE_ENTRY_VENUE_CONTRACT_NOT_PASS at 2026-07-22T13:15:00Z (failed: position_mode)"
            " - not delivered then") in sent[1]
    assert "undelivered" not in vc.read_notice_mark(tmp_path)


def test_a_fail_that_names_other_checks_is_told_and_one_that_holds_is_not(tmp_path):
    from tests._helpers import record_venue_contract

    record_venue_contract(tmp_path, ["BTCUSDT"], verified_at=NOW, failed=("configured_leverage",))
    _told(tmp_path, NOW)
    record_venue_contract(tmp_path, ["BTCUSDT"], verified_at="2026-09-19T07:25:00Z", failed=("exchange_info",))
    other = _told(tmp_path, "2026-09-19T07:26:00Z")
    assert other["changed"] is True
    assert other["text"].startswith("CRYPTO VENUE CONTRACT - still not usable, for another reason")
    assert "failed: exchange_info" in other["text"]
    record_venue_contract(tmp_path, ["BTCUSDT"], verified_at="2026-09-19T07:40:00Z", failed=("exchange_info",))
    assert vc.notice(tmp_path, now="2026-09-19T07:41:00Z")["changed"] is False


@pytest.mark.parametrize("body", ["[]", '"USABLE"', "1", "null"])
def test_a_mark_that_is_not_an_object_reads_as_never_told(tmp_path, body):
    from tests._helpers import record_venue_contract

    record_venue_contract(tmp_path, SYMBOLS, verified_at=NOW)
    path = vc.notice_mark_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    result = vc.notice(tmp_path, now=NOW)
    assert result["changed"] is True and result["text"].startswith("CRYPTO VENUE CONTRACT - first report")


def test_the_single_symbol_fire_also_tells_after_its_own_ask(tmp_path, monkeypatch):
    from tests._helpers import record_venue_contract

    sent = []
    record_venue_contract(tmp_path, ["BTCUSDT"], verified_at="2026-07-22T12:59:00Z")
    fire = _pipeline(tmp_path, monkeypatch, request="ETHUSDT 4h", notify=lambda channel, text, **_: sent.append(text))
    assert fire("2026-07-22T13:00:00Z").endswith("venue contract: PASS venue contract notice sent (USABLE)")
    assert len(sent) == 1


def test_a_notice_sent_but_not_marked_never_stops_the_fire_and_is_sent_again(tmp_path, monkeypatch):
    """Loud, not silent: every fire sends it again until the mark can be written."""
    from runtime.mvp_runtime.errors import PersistenceError
    from tests._helpers import record_venue_contract

    sent = []

    def refuse(state, *, root=None):
        raise PersistenceError("VENUE_CONTRACT_NOTICE_LOCKED", "scripted: the lock cannot be opened")

    record_venue_contract(tmp_path, ["BTCUSDT"], verified_at="2026-07-22T12:59:00Z")
    fire = _pipeline(tmp_path, monkeypatch, notify=lambda channel, text, **_: sent.append(text))
    monkeypatch.setattr(vc, "write_notice_mark", refuse)
    for now in ("2026-07-22T13:00:00Z", "2026-07-22T13:15:00Z"):
        assert fire(now).endswith("venue contract notice sent (USABLE), mark not written (PersistenceError)")
    assert len(sent) == 2


def test_without_an_operator_registration_the_notice_says_why_and_is_kept(tmp_path, monkeypatch):
    """The real channel selection: no registration is the channel's typed refusal, named on the status
    line; the change waits on the mark."""
    from tests._helpers import record_venue_contract

    record_venue_contract(tmp_path, ["BTCUSDT"], verified_at="2026-07-22T12:59:00Z")
    fire = _pipeline(tmp_path, monkeypatch)
    for now in ("2026-07-22T13:00:00Z", "2026-07-22T13:15:00Z"):
        assert fire(now).endswith("venue contract notice not sent (REGISTRATION_MISSING)")
    mark = vc.read_notice_mark(tmp_path)
    assert "reading" not in mark and [r["reading"] for r in mark["undelivered"]] == ["USABLE"]
