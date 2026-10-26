"""The rebalancing candidate report (REBALANCING_PROPOSAL §2.7, RB2'). Synthetic listings and candles only."""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.holdings import allocation as al
from runtime.mvp_runtime.holdings import candidates as cd
from runtime.mvp_runtime.holdings import toss_account
from runtime.mvp_runtime.holdings.rebalance import OVERSEAS, TAX_FREE, TAXED_DIVIDEND

LISTINGS = {
    "KOSPI": [
        {"symbol": "069500", "name": "KODEX 200"},
        {"symbol": "102110", "name": "TIGER 200"},
        {"symbol": "122630", "name": "KODEX 레버리지"},
        {"symbol": "252670", "name": "KODEX 200선물인버스2X"},
        {"symbol": "114260", "name": "KODEX 국고채3년"},
        {"symbol": "305080", "name": "TIGER 미국채10년선물"},
        {"symbol": "453850", "name": "ACE 미국30년국채액티브(H)"},
        {"symbol": "308620", "name": "KODEX 미국채10년선물"},
        {"symbol": "999901", "name": "TEST 미국채10년(H)"},
        {"symbol": "411060", "name": "ACE KRX금현물"},
        {"symbol": "132030", "name": "KODEX 골드선물(H)"},
        {"symbol": "999902", "name": "TEST 200 커버드콜"},
    ],
    "NYSE": [
        {"symbol": "VT", "name": "뱅가드 토탈 월드 스탁 ETF", "englishName": "Vanguard Total World Stock ETF"},
        {"symbol": "SPY", "name": "SPDR S&P 500 ETF", "englishName": "SPDR S&P 500 ETF Trust"},
        {"symbol": "SPXL", "name": "디렉시온 S&P500 3배", "englishName": "Direxion Daily S&P 500 Bull 3X"},
        {"symbol": "GLD", "name": "SPDR 골드", "englishName": "SPDR Gold Shares"},
    ],
}


def _keys(shortlists, key):
    return sorted(r["symbol"] for r in shortlists[key])


def test_each_class_shortlist_matches_its_decided_exposure_and_drops_leverage_inverse_and_covered_calls():
    s = cd.pool(LISTINGS)
    assert _keys(s, "domestic_equity") == ["069500", "102110"]
    assert _keys(s, "bonds_kr3y") == ["114260"]
    assert _keys(s, "bonds_us7_10y") == ["305080", "308620", "999901"]   # 30-year is not 7-10y
    assert _keys(s, "gold") == ["132030", "411060"]                      # a US-listed gold ETF is not Q3's
    assert _keys(s, "global_equity") == ["SPY", "VT"]                    # SPXL: 3X out


def _candles(close, volume, n=20):
    return [{"closePrice": str(close), "volume": str(volume)} for _ in range(n)]


def _ranked(info=None, **over):
    s = cd.pool(LISTINGS)
    bars = {"069500": _candles(40_000, 5_000_000), "102110": _candles(40_000, 1_000_000),
            "114260": _candles(100_000, 50_000), "305080": _candles(10_000, 100_000),
            "308620": _candles(10_000, 300_000), "999901": _candles(10_000, 900_000),
            "411060": _candles(15_000, 400_000), "132030": _candles(15_000, 100_000),
            "VT": _candles(120, 3_000_000), "SPY": _candles(600, 60_000_000)}
    bars.update(over)
    return cd.rank(s, info=info or {}, candles=bars, fees={"KR": 0.00015, "US": 0.001}, usd_krw=1_400.0)


def test_shortlists_rank_by_liquidity_with_us_converted_at_the_rate():
    r = _ranked()
    assert [c.instrument for c in r["domestic_equity"]] == ["toss:domestic:069500", "toss:domestic:102110"]
    spy = r["global_equity"][0]
    assert spy.instrument == "toss:overseas:SPY" and spy.avg_value_krw == pytest.approx(600 * 60_000_000 * 1_400)
    assert spy.fee_rate == 0.001 and r["domestic_equity"][0].fee_rate == 0.00015


def test_tax_kind_and_currency_exposure_follow_the_decided_tables():
    r = _ranked()
    assert {c.tax_kind for c in r["domestic_equity"]} == {TAX_FREE}
    assert {c.tax_kind for c in r["global_equity"]} == {OVERSEAS} and {c.fx for c in r["global_equity"]} == {"USD"}
    assert {c.tax_kind for c in r["bonds_kr3y"] + r["gold"]} == {TAXED_DIVIDEND}
    us = {c.instrument: c for c in r["bonds_us7_10y"]}
    assert us["toss:domestic:308620"].fx == "USD"
    assert us["toss:domestic:999901"].fx == "hedged" and "D-A decided unhedged" in us["toss:domestic:999901"].note
    assert r["bonds_kr3y"][0].fx == "KRW"


@pytest.mark.parametrize("detail", [{"status": "DELISTED"}, {"leverageFactor": "2.0"}, {"leverageFactor": "-1.0"},
                                    {"leverageFactor": "x"}])
def test_an_inactive_or_leveraged_listing_is_dropped(detail):
    r = _ranked(info={"069500": detail})
    assert "toss:domestic:069500" not in [c.instrument for c in r["domestic_equity"]]


def test_no_candles_ranks_last_and_is_never_in_the_example():
    r = _ranked(**{"069500": []})
    assert r["domestic_equity"][-1].instrument == "toss:domestic:069500" and r["domestic_equity"][-1].days == 0
    assert "069500" not in "\n".join(cd._example(r))


def test_the_example_holds_three_korean_3y_plus_one_unhedged_us_treasury_for_the_decided_quarter():
    bars = {f"K{i}": _candles(100_000, 10_000 * (i + 1)) for i in range(4)}
    listings = {**LISTINGS, "KOSPI": LISTINGS["KOSPI"] + [{"symbol": f"K{i}", "name": f"X{i} 국고채3년"} for i in range(4)]}
    s = cd.pool(listings)
    r = cd.rank(s, info={}, candles={**bars, "305080": _candles(10_000, 100_000), "308620": _candles(10_000, 300_000),
                                     "999901": _candles(10_000, 900_000)},
                fees={}, usd_krw=None)
    line = next(x for x in cd._example(r) if f" {al.BONDS}=" in x)
    ids = line.split("=", 1)[1].split(",")
    assert ids[:3] == ["toss:domestic:K3", "toss:domestic:K2", "toss:domestic:K1"]
    assert ids[3] == "toss:domestic:308620"              # the most liquid UNHEDGED one, not the hedged 999901
    assert len(ids) == 4


def test_the_render_says_terminal_only_and_leaves_the_expense_ratio_to_thomas():
    text = "\n".join(cd.render(_ranked(), fetched_at="2026-10-26T12:00:00Z", usd_krw=1_400.0))
    assert text.startswith("REBALANCE CANDIDATES - terminal only")
    assert "expense ratio: not in Toss's API" in text and "choosing is Thomas's" in text
    assert "[domestic_equity] KOSPI 200 ETF — 2 matched" in text


# --- the feed's four new reads: GETs only, with the parameters the spec names ---------------------------------

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


def test_the_new_reads_ask_for_active_etfs_daily_candles_and_the_accounts_rates(feed):
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
    from runtime.mvp_runtime.errors import ToolError
    with pytest.raises(ToolError):
        feed.stock_info([str(i) for i in range(toss_account.STOCKS_PAGE_LIMIT + 1)])
    assert feed.calls == []


# --- the script: paced reads, and a refusal where the key is not --------------------------------------------

class _StubFeed:
    def __init__(self):
        self.calls = []

    def commission_rates(self, **_):
        self.calls.append("fees")
        return [{"marketCountry": "KR", "commissionRate": "0.00015"}, {"marketCountry": "US", "commissionRate": "x"}]

    def usd_krw_rate(self, **_):
        return 1_400.0

    def listed_etfs(self, market, **_):
        self.calls.append(f"list:{market}")
        return LISTINGS.get(market, [])

    def stock_info(self, symbols, **_):
        self.calls.append(f"info:{len(symbols)}")
        return [{"symbol": s, "status": "ACTIVE"} for s in symbols]

    def daily_candles(self, symbol, *, count, **_):
        self.calls.append(f"bars:{symbol}")
        return _candles(10_000, 1_000, n=count)


def test_the_script_reads_each_market_once_and_paces_every_read():
    from scripts import rebalance_candidates as script
    feed, slept = _StubFeed(), []
    ranked, rate = script.collect(feed, timeout=5, sleep=slept.append)
    markets = [c for c in feed.calls if c.startswith("list:")]
    assert markets == [f"list:{m}" for m in (*cd.KR_MARKETS, *cd.US_MARKETS)]
    bars = [c for c in feed.calls if c.startswith("bars:")]
    assert len(bars) == len({r["symbol"] for rows in cd.pool(LISTINGS).values() for r in rows})
    assert slept.count(script.PAUSE_SECONDS["listing"]) == len(markets)
    assert slept.count(script.PAUSE_SECONDS["candles"]) == len(bars)
    assert rate == 1_400.0 and ranked["global_equity"][0].fee_rate is None   # an unreadable US rate stays unknown


def test_the_script_refuses_where_the_toss_key_is_not(monkeypatch, capsys):
    from scripts import rebalance_candidates as script
    monkeypatch.setattr(script, "select_holdings_feed", lambda: object())
    assert script.main([]) == script.EXIT_BLOCKED
    assert "thomas-scheduler-maint" in capsys.readouterr().out
