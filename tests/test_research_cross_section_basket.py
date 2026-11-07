"""The cross-section basket measurement (MARKET_NEUTRAL_CROSS_SECTION_V0.1, Thomas 2026-11-04): the point-in-time
universe, the next-open execution, the cost and funding signs, the forced close of a delisted leg, the seeded
random baseline and the pre-registered reading. Nothing here touches the network or a real archive."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date, datetime, timedelta, timezone

import pytest

from runtime.mvp_runtime.crypto import cost
from scripts import selection_evidence
from scripts.research import cross_section_basket as xs

UNIT = (cost.DEFAULT_TAKER_FEE_BPS + cost.DEFAULT_SLIPPAGE_BPS) / 10_000


def _market(prices: dict[str, dict[date, float]], volumes: dict[str, float] | None = None) -> xs.Market:
    """Bars whose open is the previous close (so a day's open-to-next-open is close-to-close)."""
    market = xs.Market()
    for symbol, series in prices.items():
        days = sorted(series)
        bars = {}
        for i, day in enumerate(days):
            prev = series[days[i - 1]] if i else series[day]
            bars[day] = xs.Bar(open=prev, close=series[day], quote_volume=(volumes or {}).get(symbol, 1.0))
        market.bars[symbol] = bars
    return market


def _days(start: date, n: int) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


def _flat(start: date, n: int, price: float = 100.0) -> dict[date, float]:
    return {d: price for d in _days(start, n)}


# --- reading the archive -----------------------------------------------------------------------------------

def _zip_bytes(text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("x.csv", text)
    return buffer.getvalue()


def _ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)


def test_klines_are_read_with_or_without_a_header_and_in_either_time_unit(tmp_path):
    folder = tmp_path / xs.KLINES_KIND / "BTCUSDT"
    folder.mkdir(parents=True)
    old = f"{_ms(date(2020, 1, 1))},7189.4,7260,7170,7197.5,568,0,409678760.5,101871,1,1,0\n"
    new = ("open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,"
           "taker_buy_quote_volume,ignore\n"
           f"{_ms(date(2026, 10, 1)) * 1000},83576.8,85265,83136,84829.6,143164,0,12034305316.9,1,1,1,0\n")
    (folder / "BTCUSDT-1d-2020-01.zip").write_bytes(_zip_bytes(old))
    (folder / "BTCUSDT-1d-2026-10.zip").write_bytes(_zip_bytes(new))
    bars = xs.read_klines(folder)
    assert bars[date(2020, 1, 1)] == xs.Bar(open=7189.4, close=7197.5, quote_volume=409678760.5, high=7260.0, low=7170.0)
    assert bars[date(2026, 10, 1)].close == 84829.6


def test_funding_is_read_and_summed_per_utc_day(tmp_path):
    folder = tmp_path / xs.FUNDING_KIND / "BTCUSDT"
    folder.mkdir(parents=True)
    text = "calc_time,funding_interval_hours,last_funding_rate\n" + "".join(
        f"{_ms(date(2020, 1, 1)) + h * 3_600_000 + 1},8,0.0001\n" for h in (0, 8, 16, 24))
    (folder / "BTCUSDT-fundingRate-2020-01.zip").write_bytes(_zip_bytes(text))
    market = xs.Market(bars={"BTCUSDT": {}}, funding={"BTCUSDT": xs.read_funding(folder)})
    assert xs.funding_on(market, "BTCUSDT", date(2020, 1, 1)) == (pytest.approx(0.0003), True)
    assert xs.funding_on(market, "BTCUSDT", date(2020, 1, 2)) == (pytest.approx(0.0001), True)
    assert xs.funding_on(market, "ETHUSDT", date(2020, 1, 1)) == (0.0, False)


# --- the universe ---------------------------------------------------------------------------------------

def test_the_universe_reads_only_what_was_known_before_the_month():
    start = date(2021, 1, 1)
    prices = {s: _flat(start, 200) for s in ("AUSDT", "BUSDT", "CUSDT")}
    market = _market(prices, volumes={"AUSDT": 3.0, "BUSDT": 2.0, "CUSDT": 1.0})
    month = date(2021, 6, 1)
    before = xs.universe_at(market, month, top_n=2)
    # Make C the largest by far, but only on and after the month's first day: the pick must not move.
    for day in _days(month, 30):
        market.bars["CUSDT"][day] = xs.Bar(100.0, 100.0, 1e9)
    assert xs.universe_at(market, month, top_n=2) == before == ["AUSDT", "BUSDT"]


def test_age_trading_and_delisting_decide_membership():
    start = date(2021, 1, 1)
    prices = {"OLDUSDT": _flat(start, 300), "NEWUSDT": _flat(date(2021, 9, 1), 60),
              "GONEUSDT": _flat(start, 150)}           # last bar 2021-05-30
    market = _market(prices)
    assert xs.universe_at(market, date(2021, 5, 1)) == ["GONEUSDT", "OLDUSDT"]
    assert "GONEUSDT" not in xs.universe_at(market, date(2021, 7, 1))   # no bar the day before
    assert "NEWUSDT" not in xs.universe_at(market, date(2021, 10, 1))   # listed under 90 days ago


def test_named_symbols_join_beyond_the_top_n_only_when_eligible():
    start = date(2021, 1, 1)
    prices = {s: _flat(start, 300) for s in ("AUSDT", "BUSDT", "CUSDT")}
    prices["YOUNGUSDT"] = _flat(date(2021, 9, 1), 40)
    market = _market(prices, volumes={"AUSDT": 3.0, "BUSDT": 2.0, "CUSDT": 1.0, "YOUNGUSDT": 9.0})
    month = date(2021, 10, 1)
    assert xs.universe_at(market, month, top_n=1) == ["AUSDT"]
    assert xs.universe_at(market, month, top_n=1, named=["CUSDT", "YOUNGUSDT"]) == ["AUSDT", "CUSDT"]


@pytest.mark.parametrize("members,k", [(3, 0), (4, 2), (9, 2), (10, 2), (15, 3), (22, 4)])
def test_the_basket_is_a_fifth_a_side_and_never_below_two(members, k):
    assert xs.side_size(members) == k


# --- the signal, the execution and the costs ------------------------------------------------------------

def test_the_decision_reads_nothing_after_its_close():
    start = date(2021, 1, 1)
    prices = {f"S{i}USDT": {d: 100.0 + i * (j % 3) for j, d in enumerate(_days(start, 60))} for i in range(5)}
    market = _market(prices)
    decision = date(2021, 2, 1)
    members = sorted(prices)
    before = xs.target_weights(market, members, decision, "S-MOM7")
    for symbol in members:
        for day in _days(decision + timedelta(days=1), 20):
            market.bars[symbol][day] = xs.Bar(1.0, 1e6 if symbol == members[0] else 1.0, 1.0)
    assert xs.target_weights(market, members, decision, "S-MOM7") == before


def test_momentum_buys_winners_and_reversal_buys_losers():
    start = date(2021, 1, 1)
    growth = {"AUSDT": 1.04, "BUSDT": 1.02, "CUSDT": 1.0, "DUSDT": 0.98}
    prices = {s: {d: 100.0 * g ** i for i, d in enumerate(_days(start, 40))} for s, g in growth.items()}
    market = _market(prices)
    day = date(2021, 2, 1)
    mom = xs.target_weights(market, sorted(growth), day, "S-MOM7")
    rev = xs.target_weights(market, sorted(growth), day, "S-REV")
    assert mom == {"AUSDT": 0.5, "BUSDT": 0.5, "CUSDT": -0.5, "DUSDT": -0.5}
    assert rev == {s: -w for s, w in mom.items()}


def test_execution_is_at_the_next_open_and_costs_every_unit_traded():
    start = date(2021, 1, 1)
    growth = {"AUSDT": 1.01, "BUSDT": 1.0, "CUSDT": 1.0, "DUSDT": 0.99}
    prices = {s: {d: 100.0 * g ** i for i, d in enumerate(_days(start, 30))} for s, g in growth.items()}
    market = _market(prices)
    monthly = {date(2021, 1, 1): sorted(growth)}
    # 2021-01-10 is a Sunday: the weekly signal decides at its close and trades at Monday's open.
    run = xs.simulate(market, "S-MOM7", monthly, start=date(2021, 1, 9), end=date(2021, 1, 12))
    assert date(2021, 1, 10) not in run["daily"]
    monday = run["daily"][date(2021, 1, 11)]
    assert monday["cost"] == pytest.approx(2 * UNIT)          # from flat: one unit long, one unit short
    expected = 0.5 * (0.01 + 0.0) - 0.5 * (0.0 - 0.01)          # long A,B; short C,D
    assert monday["gross"] == pytest.approx(expected)
    assert run["daily"][date(2021, 1, 12)]["cost"] == 0.0        # weekly: no trade on Tuesday


def test_longs_pay_and_shorts_receive_positive_funding():
    start = date(2021, 1, 1)
    prices = {s: _flat(start, 30) for s in ("AUSDT", "BUSDT", "CUSDT", "DUSDT")}
    market = _market(prices)
    day = date(2021, 1, 11)
    lo = _ms(day)
    market.funding = {"AUSDT": [(lo, 0.001)], "DUSDT": [(lo, 0.002)]}
    weights = xs.target_weights(market, sorted(prices), day - timedelta(days=1), "S-MOM7")
    assert weights["AUSDT"] > 0 > weights["DUSDT"]
    run = xs.simulate(market, "S-MOM7", {date(2021, 1, 1): sorted(prices)}, start=day, end=day)
    # The long pays 0.5 × 0.001; the short receives 0.5 × 0.002.
    assert run["daily"][day]["funding"] == pytest.approx(-0.0005 + 0.001)
    assert run["funding_missing_leg_days"] == 2


def test_a_leg_whose_series_ends_is_closed_at_its_last_close_and_counted():
    start = date(2021, 1, 1)
    prices = {s: _flat(start, 30) for s in ("AUSDT", "BUSDT", "CUSDT")}
    prices["GONEUSDT"] = {d: 100.0 for d in _days(start, 11)}       # last bar 2021-01-11
    prices["GONEUSDT"][date(2021, 1, 11)] = 50.0                      # it collapses on its last day
    market = _market(prices)
    monthly = {date(2021, 1, 1): sorted(prices)}
    run = xs.simulate(market, "S-MOM7", monthly, start=date(2021, 1, 11), end=date(2021, 1, 13))
    weights = xs.target_weights(market, sorted(prices), date(2021, 1, 10), "S-MOM7")
    w = weights.get("GONEUSDT", 0.0)
    assert w != 0.0
    assert run["forced_closes"]["count"] == 1
    assert run["forced_closes"]["net"] == pytest.approx(w * (50.0 / 100.0 - 1) - abs(w) * UNIT)
    assert run["daily"][date(2021, 1, 12)]["gross"] == pytest.approx(0.0)  # flat afterwards, not re-weighted


def test_the_random_baseline_is_seeded():
    members = [f"S{i}USDT" for i in range(10)]
    start = date(2021, 1, 1)
    market = _market({s: _flat(start, 40) for s in members})
    day = date(2021, 2, 1)
    one = xs.target_weights(market, members, day, "S-REV", seed="xs-basket|1")
    assert one == xs.target_weights(market, members, day, "S-REV", seed="xs-basket|1")
    assert one != xs.target_weights(market, members, day, "S-REV", seed="xs-basket|2")


def test_the_sensitivity_run_doubles_slippage_for_the_lower_volume_half_only():
    start = date(2021, 1, 1)
    vols = {"AUSDT": 4.0, "BUSDT": 3.0, "CUSDT": 2.0, "DUSDT": 1.0}
    growth = {"AUSDT": 1.01, "BUSDT": 0.99, "CUSDT": 1.01, "DUSDT": 0.99}
    prices = {s: {d: 100.0 * g ** i for i, d in enumerate(_days(start, 30))} for s, g in growth.items()}
    market = _market(prices, volumes=vols)
    monthly = {date(2021, 1, 1): sorted(vols)}
    day = date(2021, 1, 11)
    run = xs.simulate(market, "S-MOM7", monthly, start=day, end=day, slippage_low_multiplier=2.0)
    high, low = UNIT, (cost.DEFAULT_TAKER_FEE_BPS + 2 * cost.DEFAULT_SLIPPAGE_BPS) / 10_000
    assert run["daily"][day]["cost"] == pytest.approx(0.5 * 2 * high + 0.5 * 2 * low)


# --- the reading ---------------------------------------------------------------------------------------------

def test_weeks_run_monday_to_sunday_and_years_count_by_monday():
    daily = {date(2021, 1, 4): {"net": 0.001}, date(2021, 1, 10): {"net": 0.002}, date(2021, 1, 11): {"net": -0.001}}
    weeks = xs.weekly(daily)
    assert weeks == {date(2021, 1, 4): pytest.approx(0.003), date(2021, 1, 11): pytest.approx(-0.001)}
    assert xs.positive_years(weeks)[2021] is True


@pytest.mark.parametrize("holm_pass,mean,diff,years,expected", [
    (True, 5.0, 1.0, 5, "LEAD"),
    (True, 5.0, 1.0, 4, "NULL"),        # too few positive years
    (True, 5.0, -1.0, 7, "NULL"),       # not ahead of the random basket
    (False, 5.0, 1.0, 7, "NULL"),
    (True, -5.0, -1.0, 0, "ANTI"),
    (False, -5.0, -1.0, 0, "NULL"),
])
def test_the_pre_registered_reading(holm_pass, mean, diff, years, expected):
    assert xs.reading(holm_pass=holm_pass, mean_bps=mean, diff_mean_bps=diff, years_positive=years) == expected


@pytest.mark.parametrize("pvalues", [[0.01, 0.04, 0.2], [0.03, 0.04, 0.05], [0.2, 0.01, 0.06], [0.5, 0.5, 0.5]])
def test_holm_is_the_decided_rule(pvalues):
    assert xs.holm(pvalues, xs.HOLM_ALPHA) == selection_evidence.holm(pvalues, xs.HOLM_ALPHA)


def test_the_window_ends_on_the_last_bar_the_october_file_holds():
    assert xs.END == date(2026, 10, 30) and xs.START == date(2020, 1, 1)
    assert xs.YEARS == tuple(range(2020, 2027))


def test_run_refuses_outside_the_sandbox(tmp_path, capsys):
    assert xs.main(["run", "--archive", str(tmp_path)]) == xs.EXIT_USAGE
    assert "research sandbox only" in capsys.readouterr().err


def test_a_tradfi_perp_is_left_out_however_large_and_a_symbol_the_snapshot_lacks_is_kept():
    start = date(2026, 1, 1)
    prices = {s: _flat(start, 200) for s in ("BTCUSDT", "XAUUSDT", "GONECOINUSDT")}
    market = _market(prices, volumes={"BTCUSDT": 2.0, "XAUUSDT": 9.0, "GONECOINUSDT": 1.0})
    market.contract_types = {"BTCUSDT": "PERPETUAL", "XAUUSDT": "TRADIFI_PERPETUAL"}
    month = date(2026, 6, 1)
    assert xs.universe_at(market, month) == ["BTCUSDT", "GONECOINUSDT"]
    assert xs.universe_at(market, month, named=["XAUUSDT"]) == ["BTCUSDT", "GONECOINUSDT"]   # naming it is no way in


def _snapshot(archive, kinds=None):
    archive.mkdir(parents=True, exist_ok=True)
    (archive / xs.CONTRACT_TYPES_FILENAME).write_text(json.dumps(
        {"fetched_at": "2026-11-04T06:30:00Z", "contract_types": kinds or {"AUSDT": "PERPETUAL"}}))


@pytest.mark.parametrize("command", ["universe", "run"])
@pytest.mark.parametrize("body", [None, "{", '{"fetched_at": "x", "contract_types": {}}'])
def test_both_commands_stop_blocked_without_a_readable_contract_type_snapshot(tmp_path, capsys, monkeypatch,
                                                                              command, body):
    monkeypatch.setattr(xs, "_in_sandbox", lambda: True)
    if body is not None:
        (tmp_path / xs.CONTRACT_TYPES_FILENAME).write_text(body)
    assert xs.main([command, "--archive", str(tmp_path)]) == xs.EXIT_BLOCKED
    assert xs.CONTRACT_TYPES_FILENAME in capsys.readouterr().err


def test_universe_lists_the_months_and_every_member(tmp_path, capsys):
    _snapshot(tmp_path, {"AUSDT": "PERPETUAL", "EUSDT": "TRADIFI_PERPETUAL"})
    for symbol in ("AUSDT", "BUSDT", "CUSDT", "DUSDT", "EUSDT"):
        folder = tmp_path / xs.KLINES_KIND / symbol
        folder.mkdir(parents=True)
        rows = "".join(f"{_ms(d)},1,1,1,1,1,0,5,1,1,1,0\n" for d in _days(date(2019, 9, 1), 200))
        (folder / f"{symbol}-1d-2019-09.zip").write_bytes(_zip_bytes(rows))
    assert xs.main(["universe", "--archive", str(tmp_path)]) == xs.EXIT_OK
    out = capsys.readouterr().out
    assert "2020-01-01 k=2 AUSDT BUSDT CUSDT DUSDT" in out
    assert "members ever: 4" in out
    assert "left out (contract types 2026-11-04T06:30:00Z): 1" in out


# --- test v0.2: carry and the wider momentum (CROSS_SECTION_CARRY_AND_WIDE_MOMENTUM_V0.1) ---------------------

def _funding_market(rates_by_symbol: dict[str, dict[date, float]], start: date, n: int) -> xs.Market:
    market = _market({s: _flat(start, n) for s in rates_by_symbol})
    market.funding = {s: [(_ms(d) + 8 * 3_600_000, r) for d, r in rates.items()]
                      for s, rates in rates_by_symbol.items() if rates}
    return market


def test_carry_longs_the_lowest_funding_and_shorts_the_highest_over_the_seven_days_to_the_decision():
    start, day = date(2021, 1, 1), date(2021, 1, 20)
    window = _days(day - timedelta(days=6), 7)
    rates = {"AUSDT": {d: 0.0003 for d in window}, "BUSDT": {d: 0.0001 for d in window},
             "CUSDT": {d: -0.0001 for d in window}, "DUSDT": {d: -0.0003 for d in window}}
    # Outside the window: a huge rate the day before it and the day after the decision must not count.
    rates["DUSDT"][day - timedelta(days=7)] = 1.0
    rates["DUSDT"][day + timedelta(days=1)] = 1.0
    market = _funding_market(rates, start, 40)
    assert xs.trailing_funding(market, "DUSDT", day, 7) == pytest.approx(-0.0021)
    weights = xs.target_weights(market, sorted(rates), day, "S-CARRY")
    assert weights == {"DUSDT": 0.5, "CUSDT": 0.5, "AUSDT": -0.5, "BUSDT": -0.5}
    lows = min(rates, key=lambda s: xs.trailing_funding(market, s, day, 7))
    assert weights[lows] > 0


def test_a_member_without_funding_in_the_window_sits_the_rebalance_out_and_is_counted():
    start, day = date(2021, 1, 1), date(2021, 1, 10)       # a Sunday: the weekly decision
    window = _days(day - timedelta(days=6), 7)
    rates = {s: {d: 0.0001 * i for d in window} for i, s in enumerate(("AUSDT", "BUSDT", "CUSDT", "DUSDT"))}
    rates["EUSDT"] = {}
    market = _funding_market(rates, start, 30)
    assert "EUSDT" not in xs.target_weights(market, sorted(rates), day, "S-CARRY")
    run = xs.simulate(market, "S-CARRY", {date(2021, 1, 1): sorted(rates)}, start=date(2021, 1, 11),
                      end=date(2021, 1, 11))
    assert run["unranked_member_rebalances"] == 1


@pytest.mark.parametrize("new_p,passing", [
    ([0.019, 0.5], {0}),             # the smallest of five: 0.10/5 = 0.02
    ([0.021, 0.5], set()),           # just over it, and the step-down stops there
    ([0.019, 0.024], {0, 1}),        # second smallest: 0.10/4 = 0.025
    ([0.019, 0.026], {0}),
    ([None, 0.019], {1}),            # an untestable signal is no member of the family
])
def test_the_v02_family_carries_the_three_v01_p_values(new_p, passing):
    assert xs.family_holm(xs.TESTS["v0.2"]["prior_p"], new_p, xs.HOLM_ALPHA) == passing


def test_each_test_selects_its_own_universe_size_and_signals_and_v01_is_the_default(tmp_path, monkeypatch):
    assert xs.TESTS["v0.1"] == {"top_n": 10, "signals": ("S-REV", "S-MOM7", "S-MOM30"), "prior_p": {}}
    assert xs.TESTS["v0.2"]["top_n"] == 30 and xs.TESTS["v0.2"]["signals"] == ("S-CARRY", "S-MOM30")
    # The v0.1 result as recorded in MARKET_NEUTRAL_CROSS_SECTION_V0.1 "결과" (run 20261104T070402Z-164942).
    assert xs.TESTS["v0.2"]["prior_p"] == {"v0.1 S-REV": 0.13569777593264654,
                                           "v0.1 S-MOM7": 0.28617573036292365,
                                           "v0.1 S-MOM30": 0.12249320636703333}
    seen = {}
    monkeypatch.setattr(xs, "universes", lambda market, named=(), top_n=xs.TOP_N, **_w: seen.setdefault("top_n", top_n) and {})
    monkeypatch.setattr(xs, "_in_sandbox", lambda: True)
    _snapshot(tmp_path)
    folder = tmp_path / xs.KLINES_KIND / "AUSDT"
    folder.mkdir(parents=True)
    (folder / "AUSDT-1d-2020-01.zip").write_bytes(_zip_bytes(f"{_ms(date(2020, 1, 1))},1,1,1,1,1,0,5,1,1,1,0\n"))
    for argv, top_n in ((["universe"], 10), (["universe", "--test", "v0.2"], 30)):
        seen.clear()
        xs.main([*argv, "--archive", str(tmp_path)])
        assert seen["top_n"] == top_n


def test_the_v02_measurement_reports_its_family_the_correlation_and_the_mean_without_funding():
    start = date(2019, 9, 1)
    days = _days(start, 560)
    symbols = [f"S{i}USDT" for i in range(6)]
    prices = {s: {d: 100.0 * (1 + 0.002 * i) ** k * (1.02 if k % (i + 2) == 0 else 1.0)
                  for k, d in enumerate(days)} for i, s in enumerate(symbols)}
    market = _market(prices, volumes={s: float(10 - i) for i, s in enumerate(symbols)})
    market.funding = {s: [(_ms(d) + 8 * 3_600_000, 0.0001 * (i - 2) * (1 + (k % 3))) for k, d in enumerate(days)]
                      for i, s in enumerate(symbols)}
    report = xs.measure(market, seeds=1, test="v0.2")
    assert report["test"] == "v0.2" and report["top_n"] == 30
    assert list(report["holm_family"]) == ["v0.1 S-REV", "v0.1 S-MOM7", "v0.1 S-MOM30", "v0.2 S-CARRY",
                                           "v0.2 S-MOM30"]
    assert set(report["signals"]) == {"S-CARRY", "S-MOM30"}
    assert report["weekly_correlation"] is not None and -1 <= report["weekly_correlation"] <= 1
    carry = report["signals"]["S-CARRY"]
    assert carry["ex_funding_mean_bps"] is not None
    assert carry["ex_funding_mean_bps"] != carry["mean_bps"]          # carry earns funding by construction
    assert carry["funding_bps_per_day"] > 0


# --- the spot holdout (CROSS_SECTION_SPOT_HOLDOUT_V0.1) ----------------------------------------------------------

def test_the_spot_holdout_is_v02s_lead_on_untouched_spot_history_at_spot_fees():
    spec = xs.TESTS["spot-holdout"]
    assert spec["signals"] == ("S-MOM30",) and spec["top_n"] == 30 and spec["market"] == "spot"
    assert spec["start"] == date(2017, 8, 1) and spec["end"] == date(2019, 12, 31)
    assert spec["end"] < xs.START                          # no day the futures tests read
    assert spec["fee_bps"] == 10.0 and spec["prior_p"] == {}


def test_spot_pairs_on_a_dollar_a_fiat_or_a_leveraged_token_are_no_members():
    listed = ["BTCUSDT", "ETHUSDT", "USDCUSDT", "TUSDUSDT", "EURUSDT", "BNBBULLUSDT", "ETHBEARUSDT",
              "BTCUPUSDT", "BTCDOWNUSDT", "JUPUSDT", "SUPERUSDT"]
    kinds = xs.spot_kinds(listed)
    coins = sorted(s for s, k in kinds.items() if k == xs.SPOT_COIN)
    # JUP and SUPER end in UP but "J" and "SUPE" are no listed bases: they stay coins.
    assert coins == ["BTCUSDT", "ETHUSDT", "JUPUSDT", "SUPERUSDT"]
    start = date(2018, 1, 1)
    market = _market({s: _flat(start, 200) for s in listed}, volumes={"USDCUSDT": 99.0, "BTCUSDT": 1.0})
    market.contract_types = kinds
    assert "USDCUSDT" not in xs.universe_at(market, date(2018, 6, 1))


@pytest.mark.parametrize("mean,p1,diff,years,expected", [
    (50.0, 0.04, 10.0, {2018: True, 2019: True}, "HOLDOUT_CONFIRMED"),
    (50.0, 0.06, 10.0, {2018: True, 2019: True}, "HOLDOUT_NOT_CONFIRMED"),     # one-sided p over 0.05
    (50.0, 0.04, -1.0, {2018: True, 2019: True}, "HOLDOUT_NOT_CONFIRMED"),     # no better than random
    (50.0, 0.04, 10.0, {2018: True, 2019: False}, "HOLDOUT_NOT_CONFIRMED"),    # a judged year negative
    (-50.0, 0.04, 10.0, {2018: True, 2019: True}, "HOLDOUT_NOT_CONFIRMED"),
])
def test_the_holdout_confirms_only_when_every_pre_registered_condition_holds(mean, p1, diff, years, expected):
    assert xs.holdout_reading(mean_bps=mean, p_one_sided=p1, diff_mean_bps=diff, judged_years=years) == expected


def test_the_one_sided_p_halves_a_positive_mean_and_complements_a_negative_one():
    assert xs.one_sided_p(10.0, 0.08) == pytest.approx(0.04)
    assert xs.one_sided_p(-10.0, 0.08) == pytest.approx(0.96)


def test_the_spot_measurement_runs_in_its_own_window_at_its_own_fee_without_funding():
    symbols = [f"S{i}USDT" for i in range(6)]
    days = _days(date(2017, 5, 1), 1000)
    prices = {s: {d: 100.0 * (1 + 0.001 * i) ** k for k, d in enumerate(days)} for i, s in enumerate(symbols)}
    market = _market(prices, volumes={s: float(10 - i) for i, s in enumerate(symbols)})
    market.contract_types = xs.spot_kinds(symbols)
    report = xs.measure(market, seeds=1, test="spot-holdout")
    row = report["signals"]["S-MOM30"]
    assert set(row["by_year"]) == {"2017", "2018", "2019"}
    assert row["funding_bps_per_day"] == 0.0 and row["ex_funding_mean_bps"] == row["mean_bps"]
    assert row["reading"] in {"HOLDOUT_CONFIRMED", "HOLDOUT_NOT_CONFIRMED"} and "p_one_sided" in row
    assert report["overall"] in {"HOLDOUT_CONFIRMED", "HOLDOUT_NOT_CONFIRMED"}
    run = xs.simulate(market, "S-MOM30", xs.universes(market, top_n=30, start=date(2017, 8, 1),
                                                      end=date(2019, 12, 31)),
                      start=date(2017, 8, 1), end=date(2019, 12, 31), fee_bps=10.0)
    first = min(d for d, r in run["daily"].items() if r["cost"] > 0)
    assert run["daily"][first]["cost"] == pytest.approx(2 * (10.0 + cost.DEFAULT_SLIPPAGE_BPS) / 10_000)
    assert max(run["daily"]) <= date(2019, 12, 31)


def test_the_spot_holdout_reads_usdt_pairs_only_even_when_btc_pairs_sit_in_the_archive(tmp_path, capsys):
    for symbol in ("BTCUSDT", "ETHUSDT", "LTCUSDT", "BNBUSDT", "ETHBTC", "XRPBTC"):
        folder = tmp_path / xs.KLINES_KIND / symbol
        folder.mkdir(parents=True)
        rows = "".join(f"{_ms(d)},1,1,1,1,1,0,5,1,1,1,0\n" for d in _days(date(2017, 8, 1), 400))
        (folder / f"{symbol}-1d-2017-08.zip").write_bytes(_zip_bytes(rows))
    assert xs.main(["universe", "--test", "spot-holdout", "--json", "--archive", str(tmp_path)]) == xs.EXIT_OK
    assert json.loads(capsys.readouterr().out)["members_ever"] == ["BNBUSDT", "BTCUSDT", "ETHUSDT", "LTCUSDT"]


def test_the_report_names_the_fee_the_test_charged_not_the_runtime_default(tmp_path, monkeypatch, capsys):
    # The spot holdout charged 10 bps but its report said 5 (CROSS_SECTION_SPOT_HOLDOUT_V0.1 결과, run
    # 20261104T102324Z-304513): the provenance line read the runtime default instead of the test's fee.
    monkeypatch.setattr(xs, "_in_sandbox", lambda: True)
    monkeypatch.setattr(xs, "measure", lambda *a, **k: {})
    _snapshot(tmp_path)
    folder = tmp_path / xs.KLINES_KIND / "BTCUSDT"
    folder.mkdir(parents=True)
    (folder / "BTCUSDT-1d-2018-01.zip").write_bytes(_zip_bytes(f"{_ms(date(2018, 1, 1))},1,1,1,1,1,0,5,1,1,1,0\n"))
    for test, taker in (("spot-holdout", 10.0), ("v0.2", cost.DEFAULT_TAKER_FEE_BPS)):
        assert xs.main(["run", "--test", test, "--json", "--archive", str(tmp_path)]) == xs.EXIT_OK
        costs = json.loads(capsys.readouterr().out)["costs_bps"]
        assert costs == {"taker": taker, "slippage": cost.DEFAULT_SLIPPAGE_BPS}
