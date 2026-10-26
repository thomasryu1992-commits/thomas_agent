"""Rebalancing candidates, picked automatically (REBALANCING_PROPOSAL §2.7; RB2'' Thomas 2026-10-26).
Synthetic listings and candles only."""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime import operator as operator_mod
from runtime.mvp_runtime import scheduler
from runtime.mvp_runtime.errors import OperatorBlocked, ToolError
from runtime.mvp_runtime.holdings import allocation as al
from runtime.mvp_runtime.holdings import candidates as cd
from runtime.mvp_runtime.holdings import classification as cl
from runtime.mvp_runtime.holdings import store
from runtime.mvp_runtime.holdings import toss_account
from runtime.mvp_runtime.holdings.rebalance import OVERSEAS, TAX_FREE, TAXED_DIVIDEND

LISTINGS = {
    "KOSPI": [
        {"symbol": "069500", "name": "KODEX 200"},
        {"symbol": "102110", "name": "TIGER 200"},
        {"symbol": "148020", "name": "RISE 200"},
        {"symbol": "152100", "name": "PLUS 200"},
        {"symbol": "122630", "name": "KODEX 레버리지"},
        {"symbol": "252670", "name": "KODEX 200선물인버스2X"},
        {"symbol": "999903", "name": "TEST 200고배당"},
        {"symbol": "114260", "name": "KODEX 국고채3년"},
        {"symbol": "305080", "name": "TIGER 미국채10년선물"},
        {"symbol": "453850", "name": "ACE 미국30년국채액티브(H)"},
        {"symbol": "308620", "name": "KODEX 미국채10년선물"},
        {"symbol": "999901", "name": "TEST 미국채10년(H)"},
        {"symbol": "411060", "name": "ACE KRX금현물"},
        {"symbol": "132030", "name": "KODEX 골드선물(H)"},
        {"symbol": "999904", "name": "TEST 금광산기업"},
        {"symbol": "999902", "name": "TEST 200 커버드콜"},
    ],
    "NYSE": [
        {"symbol": "VT", "name": "뱅가드 토탈 월드 스탁 ETF", "englishName": "Vanguard Total World Stock ETF"},
        {"symbol": "ACWI", "name": "아이셰어즈 MSCI ACWI ETF", "englishName": "iShares MSCI ACWI ETF"},
        {"symbol": "VEU", "name": "뱅가드 FTSE 올월드 ex-US", "englishName": "Vanguard FTSE All-World ex-US ETF"},
        {"symbol": "SPY", "name": "SPDR S&P 500 ETF", "englishName": "SPDR S&P 500 ETF Trust"},
        {"symbol": "GLD", "name": "SPDR 골드", "englishName": "SPDR Gold Shares"},
    ],
}


def _keys(shortlists, key):
    return sorted(r["symbol"] for r in shortlists[key])


def test_each_class_shortlist_matches_its_decided_exposure_and_nothing_else():
    s = cd.pool(LISTINGS)
    assert _keys(s, "domestic_equity") == ["069500", "102110", "148020", "152100"]   # no leverage, 고배당, 커버드콜
    assert _keys(s, "bonds_kr3y") == ["114260"]
    assert _keys(s, "bonds_us7_10y") == ["305080", "308620", "999901"]              # 30-year is not 7-10y
    assert _keys(s, "gold") == ["132030", "411060"]                                 # no miners; no US-listed gold
    assert _keys(s, "global_equity") == ["ACWI", "VT"]        # S&P 500 is US-only; ex-US is not global


def _candles(close, volume, n=20):
    return [{"closePrice": str(close), "volume": str(volume)} for _ in range(n)]


BARS = {"069500": _candles(40_000, 5_000_000), "102110": _candles(40_000, 1_000_000),
        "148020": _candles(40_000, 300_000), "152100": _candles(40_000, 100_000),
        "114260": _candles(100_000, 50_000), "305080": _candles(10_000, 100_000),
        "308620": _candles(10_000, 300_000), "999901": _candles(10_000, 900_000),
        "411060": _candles(15_000, 400_000), "132030": _candles(15_000, 100_000),
        "VT": _candles(120, 3_000_000), "ACWI": _candles(120, 2_000_000)}


def _ranked(info=None, **over):
    return cd.rank(cd.pool(LISTINGS), info=info or {}, candles={**BARS, **over},
                   fees={"KR": 0.00015, "US": 0.001}, usd_krw=1_400.0)


def test_shortlists_rank_by_liquidity_with_us_converted_at_the_rate():
    r = _ranked()
    assert [c.instrument for c in r["domestic_equity"]][:2] == ["toss:domestic:069500", "toss:domestic:102110"]
    vt = r["global_equity"][0]
    assert vt.instrument == "toss:overseas:VT" and vt.avg_value_krw == pytest.approx(120 * 3_000_000 * 1_400)
    assert vt.fee_rate == 0.001 and r["domestic_equity"][0].fee_rate == 0.00015


def test_tax_kind_and_currency_exposure_follow_the_decided_tables():
    r = _ranked()
    assert {c.tax_kind for c in r["domestic_equity"]} == {TAX_FREE}
    assert {c.tax_kind for c in r["global_equity"]} == {OVERSEAS} and {c.fx for c in r["global_equity"]} == {"USD"}
    assert {c.tax_kind for c in r["bonds_kr3y"] + r["gold"]} == {TAXED_DIVIDEND}
    us = {c.instrument: c for c in r["bonds_us7_10y"]}
    assert us["toss:domestic:308620"].fx == "USD"
    assert us["toss:domestic:999901"].fx == "hedged" and "D-A decided unhedged" in us["toss:domestic:999901"].note


@pytest.mark.parametrize("detail", [{"status": "DELISTED"}, {"leverageFactor": "2.0"}, {"leverageFactor": "-1.0"},
                                    {"leverageFactor": "x"}])
def test_an_inactive_or_leveraged_listing_is_dropped(detail):
    r = _ranked(info={"069500": detail})
    assert "toss:domestic:069500" not in [c.instrument for c in r["domestic_equity"]]


def test_the_pick_is_the_three_most_traded_and_bonds_are_three_korean_3y_plus_one_unhedged_us():
    bars = {f"K{i}": _candles(100_000, 10_000 * (i + 1)) for i in range(4)}
    listings = {**LISTINGS, "KOSPI": LISTINGS["KOSPI"] + [{"symbol": f"K{i}", "name": f"X{i} 국고채3년"} for i in range(4)]}
    r = cd.rank(cd.pool(listings), info={}, candles={**BARS, **bars}, fees={}, usd_krw=1_400.0)
    picks = cd.auto_picks(r)
    assert picks[al.DOMESTIC_EQUITY] == ["toss:domestic:069500", "toss:domestic:102110", "toss:domestic:148020"]
    # 114260 trades 5.0bn/day, K3 4.0bn, K2 3.0bn, K1 2.0bn; then the most-traded UNHEDGED US (not the hedged 999901)
    assert picks[al.BONDS] == ["toss:domestic:114260", "toss:domestic:K3", "toss:domestic:K2",
                               "toss:domestic:308620"]
    assert picks[al.GLOBAL_EQUITY] == ["toss:overseas:VT", "toss:overseas:ACWI"]


def test_a_row_with_no_trading_value_is_never_picked():
    picks = cd.auto_picks(_ranked(**{"069500": []}))
    assert "toss:domestic:069500" not in picks[al.DOMESTIC_EQUITY]


def test_no_korean_3y_means_no_bond_pick_rather_than_a_us_only_one():
    r = _ranked(**{"114260": []})
    assert cd.auto_picks(r)[al.BONDS] == []


def test_the_render_says_terminal_only_and_what_the_fire_picks():
    text = "\n".join(cd.render(_ranked(), fetched_at="2026-10-26T12:00:00Z", usd_krw=1_400.0))
    assert text.startswith("REBALANCE CANDIDATES - terminal only")
    assert "not in Toss's API, so not a criterion" in text and "picks these automatically" in text


# --- the feed's four reads: GETs only, with the parameters the spec names -----------------------------------

@pytest.fixture
def feed(monkeypatch):
    f = toss_account.TossHoldingsFeed.__new__(toss_account.TossHoldingsFeed)
    f.calls = []
    f._account = (1, "12345678901")

    def fake_get(path, params=None, *, account, what, timeout_seconds):
        f.calls.append((path, params, account))
        if path == toss_account.CANDLES_PATH:
            return {"candles": [{"closePrice": "1", "volume": "1"}], "nextBefore": None}
        return [{"symbol": "A"}]

    monkeypatch.setattr(f, "_get", fake_get, raising=False)
    return f


def test_the_reads_ask_for_active_etfs_daily_candles_and_the_accounts_rates(feed):
    feed.listed_etfs("KOSPI")
    feed.stock_info(["A", "B"])
    feed.daily_candles("A", count=20)
    feed.commission_rates()
    assert feed.calls == [
        (toss_account.STOCKS_ALL_PATH, {"market": "KOSPI", "status": "ACTIVE", "securityType": "ETF"}, False),
        (toss_account.STOCKS_PATH, {"symbols": "A,B"}, False),
        (toss_account.CANDLES_PATH, {"symbol": "A", "interval": "1d", "count": "20"}, False),
        (toss_account.COMMISSIONS_PATH, None, True),
    ]


def test_stock_info_refuses_more_than_one_page(feed):
    with pytest.raises(ToolError):
        feed.stock_info([str(i) for i in range(toss_account.STOCKS_PAGE_LIMIT + 1)])
    assert feed.calls == []


# --- the paced reads, and the terminal view --------------------------------------------------------------------

class _StubFeed:
    def __init__(self, status=None, bars=None):
        self.calls, self.status, self.bars = [], status or {}, bars if bars is not None else BARS

    def commission_rates(self, **_):
        self.calls.append("fees")
        return [{"marketCountry": "KR", "commissionRate": "0.00015"}, {"marketCountry": "US", "commissionRate": "x"}]

    def usd_krw_rate(self, **_):
        return 1_400.0

    def listed_etfs(self, market, **_):
        self.calls.append(f"list:{market}")
        return LISTINGS.get(market, [])

    def stock_info(self, symbols, **_):
        self.calls.append(f"info:{','.join(symbols)}")
        return [{"symbol": s, "status": self.status.get(s, "ACTIVE")} for s in symbols]

    def daily_candles(self, symbol, *, count, **_):
        self.calls.append(f"bars:{symbol}")
        return self.bars.get(symbol, [])

    def screens(self):
        return sum(1 for c in self.calls if c == "list:KOSPI")


def test_collect_reads_each_market_once_and_paces_every_read():
    feed, slept = _StubFeed(), []
    ranked, rate = cd.collect(feed, timeout=5, sleep=slept.append)
    markets = [c for c in feed.calls if c.startswith("list:")]
    assert markets == [f"list:{m}" for m in (*cd.KR_MARKETS, *cd.US_MARKETS)]
    bars = [c for c in feed.calls if c.startswith("bars:")]
    assert len(bars) == len({r["symbol"] for rows in cd.pool(LISTINGS).values() for r in rows})
    assert slept.count(cd.PAUSE_SECONDS["listing"]) == len(markets)
    assert slept.count(cd.PAUSE_SECONDS["candles"]) == len(bars)
    assert rate == 1_400.0 and ranked["global_equity"][0].fee_rate is None   # an unreadable US rate stays unknown


def test_the_script_refuses_where_the_toss_key_is_not(monkeypatch, capsys):
    from scripts import rebalance_candidates as script
    monkeypatch.setattr(script, "select_holdings_feed", lambda: object())
    assert script.main([]) == script.EXIT_BLOCKED
    assert "thomas-scheduler-maint" in capsys.readouterr().out


# --- RB2'': the fire's automatic pick, Thomas's override, and the notice ----------------------------------------

OCT = "2026-10-27T01:00:00Z"
NOV = "2026-11-03T01:00:00Z"


def _refresh(feed, tmp_path, now=OCT):
    return store.refresh_auto_candidates(feed, now=now, root=tmp_path, sleep=lambda _s: None)


def _auto(tmp_path):
    return json.loads(store.auto_candidates_path(tmp_path).read_text(encoding="utf-8"))


def test_the_fire_picks_once_a_month_and_the_calculator_buys_into_it(tmp_path):
    feed = _StubFeed()
    assert _refresh(feed, tmp_path) == "monthly" and feed.screens() == 1
    assert _refresh(feed, tmp_path, now="2026-10-28T01:00:00Z") is None and feed.screens() == 1   # same month
    assert _refresh(feed, tmp_path, now=NOV) == "monthly" and feed.screens() == 2
    eff = store.effective_candidates(tmp_path)
    assert eff[al.DOMESTIC_EQUITY] == ["toss:domestic:069500", "toss:domestic:102110", "toss:domestic:148020"]
    assert eff[al.BONDS][-1] == "toss:domestic:308620"


def test_a_pick_that_stops_trading_is_replaced_at_the_next_fire(tmp_path):
    _refresh(_StubFeed(), tmp_path)
    feed = _StubFeed(status={"069500": "DELISTED"}, bars={**BARS, "069500": []})
    assert _refresh(feed, tmp_path, now="2026-10-27T02:00:00Z") == "untradable" and feed.screens() == 1
    assert "toss:domestic:069500" not in store.effective_candidates(tmp_path)[al.DOMESTIC_EQUITY]


def test_a_row_the_answer_leaves_out_is_not_a_delisting(tmp_path):
    _refresh(_StubFeed(), tmp_path)

    class Quiet(_StubFeed):
        def stock_info(self, symbols, **_):
            self.calls.append("info")
            return []

    feed = Quiet()
    assert _refresh(feed, tmp_path, now="2026-10-27T02:00:00Z") is None and feed.screens() == 0


def test_thomas_override_wins_its_class_and_is_never_screened_over(tmp_path):
    cl.set_candidates(store.state_dir(tmp_path), al.GOLD, ["toss:domestic:132030"], now=OCT)
    _refresh(_StubFeed(), tmp_path)
    assert al.GOLD not in _auto(tmp_path)["candidates"]
    assert store.effective_candidates(tmp_path)[al.GOLD] == ["toss:domestic:132030"]
    # His pick stops trading: told, not replaced, and no rescreen for it alone.
    feed = _StubFeed(status={"132030": "DELISTED"})
    assert _refresh(feed, tmp_path, now="2026-10-27T02:00:00Z") is None and feed.screens() == 0
    assert _auto(tmp_path)["manual_untradable"] == ["toss:domestic:132030"]
    token, text = store.candidate_notice(tmp_path)
    assert "수동 지정 후보 중 거래 불가 1개" in text


def test_an_override_set_after_the_pick_wins_at_once_without_waiting_for_a_screen(tmp_path):
    _refresh(_StubFeed(), tmp_path)
    assert _auto(tmp_path)["candidates"][al.GOLD]                       # the fire picked gold first
    cl.set_candidates(store.state_dir(tmp_path), al.GOLD, ["toss:domestic:132030"], now=OCT)
    assert store.effective_candidates(tmp_path)[al.GOLD] == ["toss:domestic:132030"]
    cl.set_candidates(store.state_dir(tmp_path), al.GOLD, [], now=OCT)  # withdrawn: the automatic pick again
    assert store.effective_candidates(tmp_path)[al.GOLD] == _auto(tmp_path)["candidates"][al.GOLD]


def test_a_class_the_screen_finds_nothing_for_keeps_its_last_pick(tmp_path):
    _refresh(_StubFeed(), tmp_path)
    before = _auto(tmp_path)["candidates"][al.GOLD]
    _refresh(_StubFeed(bars={k: v for k, v in BARS.items() if k not in ("411060", "132030")}), tmp_path, now=NOV)
    body = _auto(tmp_path)
    assert body["candidates"][al.GOLD] == before and body["kept"] == [al.GOLD]


def test_the_notice_is_counts_only_and_told_once(tmp_path):
    _refresh(_StubFeed(), tmp_path)
    token, text = store.candidate_notice(tmp_path)
    assert "리밸런싱 후보 자동 선정(월 점검) — 자산군 4개" in text
    assert "toss:" not in text and not any(s in text for s in ("069500", "VT", "KODEX"))
    store.mark_candidates_told(token, now=OCT, root=tmp_path)
    assert store.candidate_notice(tmp_path) is None


def test_the_scheduler_marks_the_notice_only_after_delivery(monkeypatch, tmp_path):
    _refresh(_StubFeed(), tmp_path)
    monkeypatch.setattr(store, "refresh", lambda **_kw: {"status": "holdings snapshot: refreshed", "alert": None,
                                                        "candidate_alert": store.candidate_notice(tmp_path)})
    monkeypatch.setattr(store, "blocked_check", lambda **_kw: {})
    monkeypatch.setattr(operator_mod, "select_operator_channel", lambda **_kw: "channel")

    def down(channel, text, **_kw):
        raise OperatorBlocked("CHANNEL_DOWN", "down")

    def fire():
        schedule = scheduler.Schedule(schedule_id="schedule_holdings_test", kind=scheduler.KIND_HOLDINGS,
                                      request="", interval_seconds=3600, enabled=True, created_by="test",
                                      created_at=OCT, next_run_at=OCT)
        return scheduler._execute(schedule, now=OCT, ledger=None, working_memory=None, programization=None,
                                  repo_root=tmp_path, executor=lambda **_: {})

    monkeypatch.setattr(operator_mod, "notify_operator", down)
    assert "candidates not sent:CHANNEL_DOWN" in fire() and store.candidate_notice(tmp_path) is not None
    sent = []
    monkeypatch.setattr(operator_mod, "notify_operator", lambda channel, text, **_kw: sent.append(text))
    assert "candidates told" in fire() and len(sent) == 1 and store.candidate_notice(tmp_path) is None
