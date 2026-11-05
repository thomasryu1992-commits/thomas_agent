"""CRYPTO_REGIME_STRATEGY_SET_V0.2: the labels, S01R, the excess the selection layer reads and the verdicts."""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from scripts.research import cross_section_basket as xs
from scripts.research import regime_strategies as rs
from scripts.research import regime_strategies_v02 as v2


def _days(start: date, n: int) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


def _market(prices: dict[str, dict[date, float]]) -> xs.Market:
    market = xs.Market()
    for symbol, series in prices.items():
        days = sorted(series)
        bars = {}
        for i, day in enumerate(days):
            prev = series[days[i - 1]] if i else series[day]
            bars[day] = xs.Bar(open=prev, close=series[day], quote_volume=1.0,
                               high=max(prev, series[day]) * 1.01, low=min(prev, series[day]) * 0.99)
        market.bars[symbol] = bars
    return market


def _monthly(symbols, start: date, n: int) -> dict[date, list[str]]:
    return {m: list(symbols) for m in xs.month_starts(start, start + timedelta(days=n))}


def _wavy(n: int, symbols: int, start: date) -> dict[str, dict[date, float]]:
    """Each symbol drifts and swings on its own period, so bands break both ways."""
    return {f"S{i}USDT": {d: 100.0 * (1 + 0.004 * (i - 3)) ** k * (1 + 0.12 * math.sin(k / (3 + i)))
                          for k, d in enumerate(_days(start, n))} for i in range(symbols)}


def _labels(regime: str, start: date, n: int) -> dict[date, str]:
    return {d: regime for d in _days(start - timedelta(days=5), n + 10)}


# --- the labels ----------------------------------------------------------------------------------------

def test_the_shared_confirmation_loop_gives_v01s_labels_for_v01s_rule():
    start = date(2020, 1, 1)
    btc = {d: 100.0 * (1.004 ** k if k < 300 else 1.004 ** 300 * 0.995 ** (k - 300)) for k, d in enumerate(_days(start, 520))}
    prices = {rs.BTC: btc, **_wavy(520, 5, start)}
    data = rs.Data(_market(prices), _monthly(prices, start, 520))
    end = start + timedelta(days=519)
    expected = rs.regime_labels(data, start=start, end=end)["labels"]
    assert v2.labels_from(rs.raw_regime, data, start, end) == expected
    assert len(set(expected.values())) >= 2


def test_btc_only_never_reads_the_members():
    start = date(2020, 1, 1)
    up = {rs.BTC: {d: 100.0 * 1.003 ** k for k, d in enumerate(_days(start, 260))}}
    data = rs.Data(_market(up), {})
    data.members = lambda day: (_ for _ in ()).throw(AssertionError("breadth read"))   # type: ignore[assignment]
    day = start + timedelta(days=259)
    assert v2.raw_btc_only(data, day) == rs.BULL
    down = rs.Data(_market({rs.BTC: {d: 100.0 * 0.997 ** k for k, d in enumerate(_days(start, 260))}}), {})
    assert v2.raw_btc_only(down, day) == rs.BEAR
    assert v2.raw_sma200(down, day) == rs.BEAR


# --- S01R --------------------------------------------------------------------------------------------------

def test_the_handed_in_position_loop_is_v01s_loop():
    start = date(2021, 1, 1)
    prices = _wavy(160, 6, start)
    data = rs.Data(_market(prices), _monthly(prices, start, 160))
    labels = _labels(rs.SIDEWAYS, start, 160)
    a, b = start + timedelta(days=30), start + timedelta(days=150)
    ours = rs.run(data, v2.positions(v2.Rules(data, labels), "S01", rs.e_s01, rs.x_middle_band), a, b)
    theirs = rs.run(data, rs.Rules(data, labels).positions("S01"), a, b)
    assert sum(r["entries"] for r in theirs["daily"].values()) > 0
    assert ours == theirs


def test_s01r_enters_against_s01_and_leaves_when_the_close_comes_back_to_the_middle():
    start = date(2021, 1, 1)
    prices = _wavy(160, 6, start)
    data = rs.Data(_market(prices), _monthly(prices, start, 160))
    rules = v2.Rules(data, _labels(rs.SIDEWAYS, start, 160))
    hits = [(s, d) for d in _days(start + timedelta(days=25), 130) for s in prices if rs.e_s01(rules, s, d)]
    assert hits
    for s, d in hits:
        assert v2.e_s01r(rules, s, d)[0] == -rs.e_s01(rules, s, d)[0]
    symbol, day = next((s, d) for s, d in hits if v2.e_s01r(rules, s, d)[0] == 1)   # a long above the upper band
    p = rs.Position(symbol, 1, day, 1.0)
    assert not v2.x_back_to_middle(rules, p, day)                 # above the band is above the middle: stays
    assert rs.x_middle_band(rules, p, day)                        # S01's own exit would have left at once
    later = next(d for d in _days(day, 60) if data.at(symbol, "close", d) <= data.at(symbol, "sma20", d))
    assert v2.x_back_to_middle(rules, p, later)


def test_s01r_is_a_sideways_strategy_that_spot_can_measure():
    assert v2.regime_of("S01R") == rs.SIDEWAYS and v2.needs("S01R") == ()
    assert v2.spot_blocked(["B08", "D10", "S01R"]) == []
    assert v2.spot_blocked(["S05", "B08"]) == ["S05"]


# --- the excess the selection layer reads ------------------------------------------------------------------

def test_in_a_market_that_moves_as_one_the_excess_is_zero():
    start = date(2021, 1, 1)
    prices = {f"S{i}USDT": {d: 100.0 * (1.01 ** k) * (1 + 0.1 * math.sin(k / 5)) for k, d in enumerate(_days(start, 160))}
              for i in range(6)}
    data = rs.Data(_market(prices), _monthly(prices, start, 160))
    labels = _labels(rs.SIDEWAYS, start, 160)
    spans = [(start + timedelta(days=30), start + timedelta(days=150))]
    weeks = rs.regime_weeks(labels, spans)
    row = v2.measure_selection(data, labels, "S01R", spans, weeks, seeds=4)
    assert row["trades"] > 0 and row["own_mean_bps"] != 0
    assert row["excess_mean_bps"] == pytest.approx(0.0, abs=1e-6)


def test_the_spot_run_reads_no_return_after_its_window():
    start = date(2021, 1, 1)
    prices = {rs.BTC: {d: 100.0 * 1.002 ** k for k, d in enumerate(_days(start, 260))}, **_wavy(260, 6, start)}
    end = start + timedelta(days=230)

    def result(shock: float):
        shocked = {s: {d: v * (shock if d > end + timedelta(days=1) else 1.0) for d, v in series.items()}
                   for s, series in prices.items()}
        data = rs.Data(_market(shocked), _monthly(shocked, start, 260))
        labels = v2.labels_from(v2.raw_btc_only, data, start, start + timedelta(days=259))
        return v2.confirm_spot(data, labels, [(start + timedelta(days=210), end)], seeds=2)
    assert result(1.0) == result(3.0)


# --- the verdicts --------------------------------------------------------------------------------------------

def _row(p=0.01, mean=50.0, weeks=20, trades=30, years=None):
    return {"weeks": weeks, "trades": trades, "p_one_sided": p, "excess_mean_bps": mean,
            "excess_years_bps": years or {"2018": 10.0, "2019": 5.0}}


def test_the_holm_family_carries_the_spot_holdouts_p():
    assert v2.selection_family({"S1": _row()}) == {"S1": 0.01, "spot-holdout S-MOM30": 0.20803269693879048}
    # four p values: the smallest must clear 0.10/4 (three alone would let 0.03 through at 0.10/3)
    rows = {"S1": _row(p=0.03), "S2": _row(p=0.5), "S3": _row(p=0.6)}
    assert v2.selection_verdicts(rows)["S1"] == "SELECTION_NOT_CONFIRMED"
    rows["S1"] = _row(p=0.024)
    assert v2.selection_verdicts(rows)["S1"] == "SELECTION_CONFIRMED"


def test_selection_needs_every_judged_year_positive_and_the_floor():
    assert v2.selection_verdicts({"S1": _row(years={"2018": 10.0, "2019": -1.0})})["S1"] == "SELECTION_NOT_CONFIRMED"
    assert v2.selection_verdicts({"S1": _row(years={"2019": 10.0})})["S1"] == "SELECTION_CONFIRMED"
    assert v2.selection_verdicts({"S1": _row(mean=-1.0)})["S1"] == "SELECTION_NOT_CONFIRMED"
    assert v2.selection_verdicts({"S1": _row(weeks=12)})["S1"] == "NOT_JUDGEABLE"
    assert v2.selection_verdicts({"S1": _row(trades=19)})["S1"] == "NOT_JUDGEABLE"
    assert set(v2.selection_family({"S1": _row(trades=19)})) == {"spot-holdout S-MOM30"}


def test_timing_is_a_one_sided_welch_test_at_ten_percent():
    bull = [0.03, 0.01, 0.04, 0.02, 0.05, 0.00, 0.03] * 2
    bear = [-0.01, 0.00, -0.02, 0.01, -0.03, -0.01] * 3
    w = v2.welch(bull, bear)
    se = math.sqrt(__import__("statistics").variance(bull) / len(bull) + __import__("statistics").variance(bear) / len(bear))
    assert w["t"] == pytest.approx((sum(bull) / len(bull) - sum(bear) / len(bear)) / se)
    assert w["p_one_sided"] == pytest.approx(0.5 * math.erfc(w["t"] / math.sqrt(2)))
    assert v2.welch(bear, bull)["p_one_sided"] > 0.99
    row = {"bull": {"weeks": 14}, "bear": {"weeks": 18}, "diff_bps": 100.0, "p_one_sided": 0.09}
    assert v2.timing_verdict(row) == "TIMING_CONFIRMED"
    assert v2.timing_verdict({**row, "p_one_sided": 0.11}) == "TIMING_NOT_CONFIRMED"
    assert v2.timing_verdict({**row, "bull": {"weeks": 12}}) == "NOT_JUDGEABLE"


def test_the_spot_constants_are_the_records():
    assert (v2.SPOT_START, v2.SPOT_END) == (date(2018, 3, 5), date(2019, 12, 29))
    assert v2.SPOT_START.weekday() == 0 and v2.SPOT_END.weekday() == 6
    assert v2.SPOT_UNIVERSE_HASH == "3690d0e61fedf3e8" and v2.SPOT_FEE_BPS == 10.0
    assert v2.SELECTION == {"S1": ("B08", rs.BULL), "S2": ("D10", rs.BEAR), "S3": ("S01R", rs.SIDEWAYS)}
    assert v2.TIMING_LABEL == "btc_only" and v2.TIMING_ALPHA == 0.10


def test_both_commands_refuse_outside_the_sandbox(monkeypatch, capsys):
    monkeypatch.setattr(xs, "_in_sandbox", lambda: False)
    for command in ("stage0", "confirm-spot"):
        assert v2.main([command]) == v2.EXIT_USAGE
    assert "sandbox" in capsys.readouterr().err


def test_confirm_spot_blocks_on_a_universe_that_is_not_the_recorded_one(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(xs, "_in_sandbox", lambda: True)
    monkeypatch.setattr(v2, "load_spot", lambda archive: (None, "0000000000000000"))
    assert v2.main(["confirm-spot", "--spot-archive", str(tmp_path)]) == v2.EXIT_BLOCKED
    assert "3690d0e61fedf3e8" in capsys.readouterr().err


def test_the_confirmation_loop_matches_v01s_on_a_flickering_reading(monkeypatch):
    start = date(2020, 1, 1)
    days = _days(start, 400)
    script = {d: (rs.BULL, rs.SIDEWAYS, rs.BEAR, None)[int.from_bytes(
        __import__("hashlib").sha256(d.isoformat().encode()).digest()[:1], "big") % (4 if k % 7 else 1)]
        for k, d in enumerate(days)}
    data = rs.Data(_market({rs.BTC: {d: 100.0 for d in days}}), {})
    monkeypatch.setattr(rs, "raw_regime", lambda data, day: script.get(day))
    expected = rs.regime_labels(data, start=start, end=days[-1])["labels"]
    assert v2.labels_from(lambda data, day: script.get(day), data, start, days[-1]) == expected
    assert len({v for v in expected.values()}) == 3


def test_s01r_holds_a_long_inside_the_band_until_the_close_reaches_the_middle():
    start = date(2021, 1, 1)
    prices = _wavy(200, 6, start)
    data = rs.Data(_market(prices), _monthly(prices, start, 200))
    labels = _labels(rs.SIDEWAYS, start, 200)
    path: dict = {}
    rs.run(data, rs.recording(v2.decider(v2.Rules(data, labels), "S01R"), path),
           start + timedelta(days=30), start + timedelta(days=190))
    # a long is still held on a day its close is back inside the band, above the middle: S01's exit would have
    # closed it, and the entry (close above the upper band) would not have re-opened it
    inside = [(d, s) for d, w in path.items() for s, v in w.items() if v > 0
              and data.at(s, "sma20", d) < data.at(s, "close", d) <= data.at(s, "bb_up", d)]
    assert inside


def test_a_position_leaves_at_the_thirty_day_hold():
    start = date(2021, 1, 1)
    prices = {"S0USDT": {d: 100.0 * 1.001 ** k for k, d in enumerate(_days(start, 120))}}
    data = rs.Data(_market(prices), _monthly(prices, start, 120))
    day0 = start + timedelta(days=40)
    entry = lambda r, s, d: (1, 1.0, None, {}) if d == day0 else None   # noqa: E731
    decide = v2.positions(v2.Rules(data, _labels(rs.SIDEWAYS, start, 120)), "S01R", entry, lambda r, p, d: False)
    held = [d for d in _days(start + timedelta(days=20), 90) if decide(d, {"S0USDT": 1.0}).get("S0USDT")]
    assert held[0] == day0 and held[-1] == day0 + timedelta(days=rs.MAX_HOLD_DAYS - 1)
