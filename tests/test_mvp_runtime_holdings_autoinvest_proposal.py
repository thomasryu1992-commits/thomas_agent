"""G6 §2e (Thomas 2026-10-27): the holdings fire drafts the buy scenario; Thomas confirms it on his terminal.
No network: fake feeds; the store and scheduler halves run over a temporary state root."""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime import operator as operator_mod
from runtime.mvp_runtime import scheduler
from runtime.mvp_runtime.errors import OperatorBlocked, ToolError
from runtime.mvp_runtime.holdings import allocation as al
from runtime.mvp_runtime.holdings import autoinvest as ai
from runtime.mvp_runtime.holdings import autoinvest_proposal as ap
from runtime.mvp_runtime.holdings import classification, store, toss_account

DOM, KR3, US10, GOLD, WORLD = ("toss:domestic:069500", "toss:domestic:114260", "toss:domestic:308620",
                               "toss:domestic:411060", "toss:overseas:VT")
CANDIDATES = {al.GLOBAL_EQUITY: [WORLD], al.DOMESTIC_EQUITY: [DOM, "toss:domestic:102110"],
              al.BONDS: [KR3, "toss:domestic:148070", US10], al.GOLD: [GOLD]}
PRICES = {"069500": 40_000.0, "114260": 60_000.0, "308620": 10_000.0, "411060": 20_000.0}
# NAV 1,000,000: domestic 50k under its 100k target, bonds 300k all Korean (US quarter 92.5k short), gold 30k short.
CLASSES = {al.GLOBAL_EQUITY: 300_000.0, al.DOMESTIC_EQUITY: 50_000.0, al.BONDS: 300_000.0, al.GOLD: 100_000.0,
           al.COIN: 25_000.0, al.CASH: 225_000.0}
POSITIONS = [{"instrument": KR3, "asset_class": al.BONDS, "quantity": 5, "value_krw": 300_000.0, "pnl_krw": 0.0}]
OPEN = "2026-10-28T01:00:00Z"                    # 10:00 KST, a Wednesday
LATER = "2026-10-28T02:00:00Z"
NEXT_DAY = "2026-10-29T01:00:00Z"


RATE = 1_400.0


class _Feed:
    def __init__(self, cash=200_000.0, session=True, usd=0.0, rate=RATE):
        self.cash, self.session, self.reads, self.usd, self.rate = cash, session, [], usd, rate

    def cash_krw(self, **_):
        self.reads.append("cash")
        return self.cash

    def cash_usd(self, **_):
        self.reads.append("usd")
        return self.usd

    def usd_krw_rate(self, **_):
        return self.rate

    def kr_market_day(self, date, **_):
        regular = {"startTime": "09:00:00", "endTime": "15:30:00"} if self.session else None
        return {"date": date, "integrated": {"regularMarket": regular} if regular else None}

    def last_prices(self, symbols, **_):
        self.reads.append(("prices", tuple(symbols)))
        return {s: PRICES[s] for s in symbols if s in PRICES}

    def commission_rates(self, **_):
        return [{"marketCountry": "KR", "commissionRate": "0"}, {"marketCountry": "US", "commissionRate": "0"}]

    def stock_info(self, symbols, **_):
        return [{"symbol": s, "name": f"ETF {s}", "status": "ACTIVE", "securityType": "ETF"} for s in symbols]


def _slots(class_krw=CLASSES, positions=POSITIONS, currencies=None, candidates=CANDIDATES, us_bonds=(US10,)):
    return ap.slots(class_krw=class_krw, positions=positions, currencies=currencies or {}, candidates=candidates,
                    us_bonds=us_bonds)


def _board(complete=True):
    return {"allocation": {"complete": complete, "classes": {c: {"krw": v} for c, v in CLASSES.items()}}}


def _refresh(state, feed=None, now=OPEN, board=None, positions=POSITIONS):
    return ap.refresh(feed or _Feed(), state, now=now, board=board or _board(), positions=positions,
                      currencies={}, candidates=CANDIDATES, us_bonds=[US10])


# --- the pure half ----------------------------------------------------------------------------------------------

def test_shares_go_one_at_a_time_to_the_largest_gap_and_never_past_a_target():
    rows, notes = _slots()
    result = ap.draft(rows, prices=PRICES, cash_krw=200_000.0, fee_rate=0.0)
    assert result["budget_krw"] == 194_000 and result["max_krw"] == 200_000            # Q1: cash less 3%
    assert {i["slot"]: i["quantity"] for i in result["items"]} == {
        "bonds_us7_10y": 9, "domestic_equity": 2, "gold": 1}                            # Q2 worked by hand
    assert result["estimate_krw"] == 190_000
    assert "bonds_kr3y" not in {i["slot"] for i in result["items"]}                    # over its ¾ target
    assert notes == [] and result["exchange_krw"] == 0                               # global equity is at target


def test_once_every_slot_is_at_target_the_rest_of_the_cash_stays_cash():
    rows = [{"key": "gold", "asset_class": al.GOLD, "instrument": GOLD, "currency": "krw", "target_krw": 110_000.0, "held_krw": 100_000.0},
            {"key": "domestic_equity", "asset_class": al.DOMESTIC_EQUITY, "instrument": DOM, "currency": "krw",
             "target_krw": 50_000.0,
             "held_krw": 60_000.0}]
    result = ap.draft(rows, prices=PRICES, cash_krw=200_000.0, fee_rate=0.0)
    assert [(i["slot"], i["quantity"]) for i in result["items"]] == [("gold", 1)] and result["estimate_krw"] == 20_000


def test_bonds_split_three_quarters_and_a_quarter_and_usd_entries_count_as_the_us_quarter():
    rows, _ = _slots(positions=[*POSITIONS, {"instrument": US10, "asset_class": al.BONDS, "value_krw": 50_000.0}],
                     class_krw={**CLASSES, al.BONDS: 350_000.0}, currencies={US10: "usd"})
    bonds = {r["key"]: r for r in rows if r["asset_class"] == al.BONDS}
    assert bonds["bonds_kr3y"]["target_krw"] == pytest.approx(1_050_000 * 0.37 * 0.75)
    assert bonds["bonds_us7_10y"]["target_krw"] == pytest.approx(1_050_000 * 0.37 * 0.25)
    assert (bonds["bonds_kr3y"]["held_krw"], bonds["bonds_us7_10y"]["held_krw"]) == (300_000.0, 50_000.0)
    assert bonds["bonds_kr3y"]["instrument"] == KR3 and bonds["bonds_us7_10y"]["instrument"] == US10


def test_each_class_buys_its_first_candidate_and_a_list_without_the_us_pick_stays_one_slot():
    rows, notes = _slots(candidates={**CANDIDATES, al.BONDS: [KR3, "toss:domestic:148070"], al.GOLD: []})
    assert {r["key"]: (r["instrument"], r["currency"]) for r in rows} == {
        "global_equity": (WORLD, "usd"), "domestic_equity": (DOM, "krw"), "bonds": (KR3, "krw")}
    assert notes == ["bonds_unsplit", "gold:no_candidate"]


def test_a_budget_under_fifty_thousand_drafts_nothing_and_an_unpriced_slot_is_skipped():
    rows, _ = _slots()
    assert ap.draft(rows, prices=PRICES, cash_krw=51_000.0, fee_rate=0.0)["status"] == "below_minimum"
    result = ap.draft(rows, prices={k: v for k, v in PRICES.items() if k != "308620"}, cash_krw=200_000.0, fee_rate=0.0)
    assert result["unpriced"] == ["global_equity", "bonds_us7_10y"]                   # no rate: no dollar slot either
    assert all(i["slot"] not in result["unpriced"] for i in result["items"])


def test_the_fee_counts_against_the_budget():
    rows = [{"key": "gold", "asset_class": al.GOLD, "instrument": GOLD, "currency": "krw", "target_krw": 1e9, "held_krw": 0.0}]
    # 97,000 budget: four 20,000 shares at a 25% fee is 100,000, three is 75,000.
    assert ap.draft(rows, prices=PRICES, cash_krw=100_000.0, fee_rate=0.25)["items"][0]["quantity"] == 3


def test_the_daily_cap_bounds_both_the_budget_and_the_scenario_cap():
    rows = [{"key": "gold", "asset_class": al.GOLD, "instrument": GOLD, "currency": "krw", "target_krw": 1e9, "held_krw": 0.0}]
    result = ap.draft(rows, prices=PRICES, cash_krw=2_000_000.0, fee_rate=0.0)
    assert result["max_krw"] == ai.DAILY_CAP_KRW and result["estimate_krw"] <= ai.DAILY_CAP_KRW * 0.97


# --- the fire's half --------------------------------------------------------------------------------------------

def test_a_trading_day_gets_one_draft_with_a_counts_only_notice(tmp_path):
    feed = _Feed()
    assert _refresh(tmp_path, feed) == "drafted"
    body = ap.read(tmp_path)
    assert body["made_on"] == "2026-10-28" and body["max_krw"] == 200_000 and body["items"][0]["name"]
    token, text = ap.notice(tmp_path)
    assert text.startswith("[보유 자산] 매수 시나리오 준비 — 3종목 12주")
    assert "toss:" not in text and "069500" not in text and "원" not in text.split("—")[1]   # H3: counts only
    assert _refresh(tmp_path, feed, now=LATER) == "drafted_today" and feed.reads.count("cash") == 1
    ap.mark_told(tmp_path, token, now=LATER)
    assert ap.notice(tmp_path) is None


def test_an_unapproved_draft_is_replaced_the_next_trading_day(tmp_path):
    _refresh(tmp_path)
    first = ap.read(tmp_path)["proposal_id"]
    assert _refresh(tmp_path, _Feed(cash=150_000.0), now=NEXT_DAY) == "drafted"
    assert ap.read(tmp_path)["proposal_id"] != first and ap.notice(tmp_path) is not None


@pytest.mark.parametrize("status", [ai.PENDING, ai.UNKNOWN])
def test_nothing_is_drafted_while_a_scenario_is_pending_or_unknown(tmp_path, status):
    ai._write(tmp_path, {"status": status, "items": []})
    feed = _Feed()
    assert _refresh(tmp_path, feed) == "scenario_active" and feed.reads == [] and ap.read(tmp_path) == {}


def test_nothing_is_drafted_beside_an_open_order(tmp_path):
    ai._write(tmp_path, {"status": ai.STOPPED, "items": [{"instrument": GOLD, "quantity": 1, "open": {"order_id": "x"}}]})
    assert _refresh(tmp_path) == "scenario_active"


@pytest.mark.parametrize(("kwargs", "word"), [
    ({"board": {"allocation": {"complete": False, "classes": {}}}}, "nav_incomplete"),
    ({"positions": None}, "nav_incomplete"),
    ({"feed": _Feed(cash=51_000.0)}, "below_minimum"),
    ({"feed": _Feed(cash=None)}, "cash_unreadable"),
    ({"feed": _Feed(session=False)}, "not_trading_day"),
])
def test_each_reason_to_wait_drafts_nothing(tmp_path, kwargs, word):
    assert _refresh(tmp_path, **kwargs) == word and ap.read(tmp_path) == {}


# --- Thomas's terminal ------------------------------------------------------------------------------------------

def test_the_draft_becomes_thomas_scenario_once_and_the_executor_takes_it(tmp_path):
    _refresh(tmp_path)
    assert any("x9" in line for line in ap.render(tmp_path))
    scenario = ap.approve(tmp_path, now=LATER, candidates=CANDIDATES)
    assert scenario["status"] == ai.PENDING and scenario["max_krw"] == 200_000
    assert {i["instrument"]: i["quantity"] for i in scenario["items"]} == {DOM: 2, US10: 9, GOLD: 1}
    with pytest.raises(ToolError, match="already"):
        ap.approve(tmp_path, now=LATER, candidates=CANDIDATES)
    assert _refresh(tmp_path, now=NEXT_DAY) == "scenario_active"        # the approved one is now pending


def test_a_stale_or_missing_draft_is_refused(tmp_path):
    with pytest.raises(ToolError, match="no drafted"):
        ap.approve(tmp_path, now=LATER, candidates=CANDIDATES)
    _refresh(tmp_path)
    with pytest.raises(ToolError, match="older than 4 days"):
        ap.approve(tmp_path, now="2026-11-02T02:00:00Z", candidates=CANDIDATES)


def test_no_approval_replaces_a_scenario_with_an_open_order(tmp_path):
    ai.approve(tmp_path, "411060=1", max_krw=30_000, candidates=CANDIDATES, now=OPEN)
    body = ai._read(tmp_path)
    body["items"][0]["open"] = {"order_id": "OID", "quantity": 1, "price": 20_000, "day": "2026-10-28"}
    ai._write(tmp_path, body)
    with pytest.raises(ToolError, match="open order"):
        ai.approve(tmp_path, "069500=1", max_krw=50_000, candidates=CANDIDATES, now=LATER)
    assert ai._read(tmp_path)["items"][0]["open"]["order_id"] == "OID"          # tracking kept


def test_the_board_command_approves_only_on_y_at_a_terminal(monkeypatch, tmp_path):
    from scripts import holdings_board

    _refresh(store.state_dir(tmp_path))
    monkeypatch.setattr(holdings_board, "state_dir", lambda: store.state_dir(tmp_path))
    monkeypatch.setattr(holdings_board, "assert_not_foreign_root_run", lambda: None)
    monkeypatch.setattr("runtime.mvp_runtime.holdings.store.effective_candidates", lambda root=None: CANDIDATES)
    monkeypatch.setattr(holdings_board, "_interactive", lambda: False)
    assert holdings_board.main(["--autoinvest-approve"]) == holdings_board.EXIT_BLOCKED
    monkeypatch.setattr(holdings_board, "_interactive", lambda: True)
    monkeypatch.setattr(holdings_board, "_answer", lambda _prompt: "n")
    assert holdings_board.main(["--autoinvest-approve"]) == holdings_board.EXIT_BLOCKED
    assert ai._read(store.state_dir(tmp_path)) == {}
    monkeypatch.setattr(holdings_board, "_answer", lambda _prompt: "y")
    assert holdings_board.main(["--autoinvest-approve"]) == holdings_board.EXIT_OK
    assert ai._read(store.state_dir(tmp_path))["status"] == ai.PENDING
    # The draft is spent: running it again asks nothing (2026-10-27: "proposal: none" still asked for y).
    monkeypatch.setattr(holdings_board, "_answer", lambda _prompt: pytest.fail("asked with nothing to approve"))
    assert holdings_board.main(["--autoinvest-approve"]) == holdings_board.EXIT_BLOCKED


def test_with_no_draft_the_board_asks_nothing(monkeypatch, tmp_path):
    from scripts import holdings_board

    monkeypatch.setattr(holdings_board, "state_dir", lambda: store.state_dir(tmp_path))
    monkeypatch.setattr(holdings_board, "assert_not_foreign_root_run", lambda: None)
    monkeypatch.setattr(holdings_board, "_interactive", lambda: True)
    monkeypatch.setattr(holdings_board, "_answer", lambda _prompt: pytest.fail("asked with nothing to approve"))
    assert holdings_board.main(["--autoinvest-approve"]) == holdings_board.EXIT_BLOCKED


def test_a_byte_outside_utf8_is_a_no_not_a_crash(monkeypatch):
    """2026-10-27: a y typed in a Korean input mode reached input() outside UTF-8 and raised UnicodeDecodeError."""
    import io
    import types

    from scripts import holdings_board

    monkeypatch.setattr(holdings_board.sys, "stdin", types.SimpleNamespace(buffer=io.BytesIO(b"\xa4\xcb\n")))
    assert holdings_board._answer("approve? ") != "y"
    monkeypatch.setattr(holdings_board.sys, "stdin", types.SimpleNamespace(buffer=io.BytesIO(b" Y\n")))
    assert holdings_board._answer("approve? ") == "y"


# --- G6 §2f: what the path bought is entered in the class it was bought for --------------------------------------

def _journal(state, *rows):
    state.mkdir(parents=True, exist_ok=True)
    with ai.journal_path(state).open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def test_bought_instruments_are_entered_in_their_class_and_bonds_name_their_currency(tmp_path):
    _journal(tmp_path,
             {"state": "accepted", "instrument": DOM},                                   # before the class was kept
             {"state": "accepted", "instrument": KR3},
             {"state": "accepted", "instrument": US10, "asset_class": al.BONDS},
             {"state": "rejected", "instrument": GOLD, "asset_class": al.GOLD},          # never bought
             {"state": "intent", "instrument": "toss:domestic:233740", "asset_class": al.DOMESTIC_EQUITY})
    entered = ai.classify_bought(tmp_path, candidates=CANDIDATES, us_bonds=[US10], now=OPEN)
    assert entered == sorted([DOM, KR3, US10])
    loaded = classification.load(tmp_path)
    assert {i: loaded.entries[i] for i in entered} == {DOM: al.DOMESTIC_EQUITY, KR3: al.BONDS, US10: al.BONDS}
    assert dict(loaded.currencies) == {KR3: "krw", US10: "usd"}
    raw = json.loads(classification.path(tmp_path).read_text(encoding="utf-8"))["entries"]
    assert {row["source"] for row in raw.values()} == {"g6_buy"}
    version = loaded.mapping_version
    assert ai.classify_bought(tmp_path, candidates=CANDIDATES, us_bonds=[US10], now=LATER) == []
    assert classification.load(tmp_path).mapping_version == version                  # nothing new, no new version


def test_an_entry_that_exists_is_never_changed_and_an_unknown_class_is_left_for_thomas(tmp_path):
    classification.apply(tmp_path, assign={DOM: al.GLOBAL_EQUITY})                    # Thomas's own choice
    _journal(tmp_path, {"state": "accepted", "instrument": DOM},
             {"state": "accepted", "instrument": "toss:domestic:999999"})               # in no candidate list
    assert ai.classify_bought(tmp_path, candidates=CANDIDATES, us_bonds=[], now=OPEN) == []
    assert dict(classification.load(tmp_path).entries) == {DOM: al.GLOBAL_EQUITY}


def test_approval_keeps_the_class_the_instrument_is_bought_for(tmp_path):
    ai.approve(tmp_path, "411060=1", max_krw=30_000, candidates=CANDIDATES, now=OPEN)
    assert ai._read(tmp_path)["items"][0]["asset_class"] == al.GOLD


# --- the store and the scheduler --------------------------------------------------------------------------------

def _toss_feed():
    feed = toss_account.TossHoldingsFeed.__new__(toss_account.TossHoldingsFeed)
    stub = _Feed()
    for name in ("cash_krw", "cash_usd", "usd_krw_rate", "kr_market_day", "last_prices", "commission_rates",
                 "stock_info"):
        setattr(feed, name, getattr(stub, name))
    return feed


def test_the_store_step_drafts_over_the_fires_own_files(monkeypatch, tmp_path):
    state = store.state_dir(tmp_path)
    state.mkdir(parents=True)
    store.local_path(tmp_path).write_text(json.dumps(_board()), encoding="utf-8")
    store.positions_path(tmp_path).write_text(json.dumps({"positions": POSITIONS}), encoding="utf-8")
    store.auto_candidates_path(tmp_path).write_text(json.dumps({"candidates": CANDIDATES, "us_bonds": [US10]}),
                                                    encoding="utf-8")
    monkeypatch.setattr(store, "_feed", _toss_feed)
    result = store.refresh_autoinvest_proposal(now=OPEN, root=tmp_path)
    assert result["status"] == "drafted" and result["alert"][1].startswith("[보유 자산] 매수 시나리오 준비")
    assert {i["slot"] for i in ap.read(state)["items"]} == {"bonds_us7_10y", "domestic_equity", "gold"}


def test_the_store_step_needs_a_toss_feed_and_a_readable_classification(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "_feed", lambda: object())
    assert store.refresh_autoinvest_proposal(now=OPEN, root=tmp_path)["status"] == "no_feed"
    monkeypatch.setattr(store, "_feed", _toss_feed)
    monkeypatch.setattr(classification, "load", lambda _d: (_ for _ in ()).throw(ToolError("X", "bad")))
    assert store.refresh_autoinvest_proposal(now=OPEN, root=tmp_path)["status"] == "classification_unreadable"


def _fire(tmp_path):
    schedule = scheduler.Schedule(schedule_id="schedule_holdings_test", kind=scheduler.KIND_HOLDINGS, request="",
                                  interval_seconds=3600, enabled=True, created_by="test", created_at=OPEN,
                                  next_run_at=OPEN)
    return scheduler._execute(schedule, now=OPEN, ledger=None, working_memory=None, programization=None,
                              repo_root=tmp_path, executor=lambda **_: {})


def test_the_scheduler_tells_the_draft_and_marks_it_only_after_delivery(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "refresh", lambda **_kw: {"status": "holdings snapshot: refreshed", "alert": None})
    monkeypatch.setattr(store, "blocked_check", lambda **_kw: {})
    monkeypatch.setattr(store, "run_autoinvest", lambda **_kw: {"status": "none", "alert": None})
    monkeypatch.setattr(store, "refresh_autoinvest_proposal",
                        lambda **_kw: {"status": "drafted", "alert": ("ap_1", "[보유 자산] 매수 시나리오 준비 — 3종목 12주.")})
    told = []
    monkeypatch.setattr(store, "mark_proposal_told", lambda token, **_kw: told.append(token))
    monkeypatch.setattr(operator_mod, "select_operator_channel", lambda **_kw: "channel")

    def down(channel, text, **_kw):
        raise OperatorBlocked("CHANNEL_DOWN", "down")

    monkeypatch.setattr(operator_mod, "notify_operator", down)
    status = _fire(tmp_path)
    assert "autoinvest proposal drafted" in status and "autoinvest proposal not sent:CHANNEL_DOWN" in status
    assert told == []
    monkeypatch.setattr(operator_mod, "notify_operator", lambda channel, text, **_kw: None)
    assert "autoinvest proposal told" in _fire(tmp_path) and told == ["ap_1"]


def test_a_failing_draft_does_not_stop_the_fire_or_the_order_step(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "refresh", lambda **_kw: {"status": "holdings snapshot: refreshed", "alert": None})
    monkeypatch.setattr(store, "blocked_check", lambda **_kw: {})
    ran = []
    monkeypatch.setattr(store, "run_autoinvest", lambda **_kw: ran.append(1) or {"status": "none", "alert": None})
    monkeypatch.setattr(store, "refresh_autoinvest_proposal",
                        lambda **_kw: (_ for _ in ()).throw(ToolError("HOLDINGS_X", "boom")))
    assert "autoinvest proposal error:HOLDINGS_X" in _fire(tmp_path) and ran == [1]
