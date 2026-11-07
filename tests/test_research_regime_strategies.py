"""CRYPTO_REGIME_STRATEGY_SET_V0.1: the regime label, the holdout split, the engine and the two-stage reading."""

from __future__ import annotations

import io
import json
import math
import zipfile
from datetime import date, timedelta

import pytest

from runtime.mvp_runtime.crypto import cost
from scripts.research import cross_section_basket as xs
from scripts.research import regime_strategies as rs

UNIT = (cost.DEFAULT_TAKER_FEE_BPS + cost.DEFAULT_SLIPPAGE_BPS) / 10_000


def _days(start: date, n: int) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


def _market(prices: dict[str, dict[date, float]]) -> xs.Market:
    market = xs.Market()
    for symbol, series in prices.items():
        days = sorted(series)
        bars = {}
        for i, day in enumerate(days):
            prev = series[days[i - 1]] if i else series[day]
            hi, lo = max(prev, series[day]) * 1.01, min(prev, series[day]) * 0.99
            bars[day] = xs.Bar(open=prev, close=series[day], quote_volume=1.0, high=hi, low=lo)
        market.bars[symbol] = bars
    return market


def _monthly(symbols, start: date, n: int) -> dict[date, list[str]]:
    return {m: list(symbols) for m in xs.month_starts(start, start + timedelta(days=n))}


def _labels(regime: str, start: date, n: int) -> dict[date, str]:
    return {d: regime for d in _days(start - timedelta(days=5), n + 10)}


# --- the holdout split ------------------------------------------------------------------------------------

def test_the_seed_draws_exactly_the_six_quarters_the_record_names():
    assert rs.holdout_quarters() == [(2021, 3), (2022, 4), (2023, 2), (2024, 2), (2025, 2), (2026, 1)]
    assert rs.HOLDOUT_SEED == "CRYPTO_REGIME_STRATEGY_SET_V0.1/holdout"


def test_screening_never_comes_within_the_embargo_of_a_held_out_quarter():
    split = rs.segments()
    held = split["holdout"]
    assert held[0] == (date(2021, 7, 1), date(2021, 9, 30)) and held[-1] == (date(2026, 1, 1), date(2026, 3, 31))
    for a, b in split["screen"]:
        day = a
        while day <= b:
            assert all(day < s - timedelta(days=rs.EMBARGO_DAYS) or day > e + timedelta(days=rs.EMBARGO_DAYS)
                       for s, e in held), day
            day += timedelta(days=1)
    assert split["screen"][0][0] == rs.WINDOW_START and split["screen"][-1][1] == rs.WINDOW_END
    assert (date(2021, 6, 1), date(2021, 6, 30)) in split["embargo"]
    assert (date(2021, 10, 1), date(2021, 10, 30)) in split["embargo"]


def test_a_week_belongs_to_the_span_holding_its_monday_and_to_its_majority_regime():
    labels = {d: rs.BULL for d in _days(date(2024, 1, 1), 60)}
    for d in _days(date(2024, 1, 8), 3):          # Mon–Wed BEAR, Thu–Sun BULL: BULL holds 4 of 7
        labels[d] = rs.BEAR
    for d in _days(date(2024, 1, 15), 4):         # Mon–Thu BEAR: BEAR holds 4 of 7
        labels[d] = rs.BEAR
    weeks = rs.regime_weeks(labels, [(date(2024, 1, 3), date(2024, 1, 31))])
    assert date(2024, 1, 1) not in weeks            # its Monday is outside the span
    assert weeks[date(2024, 1, 8)] == rs.BULL and weeks[date(2024, 1, 15)] == rs.BEAR


# --- the regime label --------------------------------------------------------------------------------------

def test_a_new_label_needs_three_identical_readings_in_a_row(monkeypatch):
    raw = "BBSSBSSS"
    names = {"B": rs.BULL, "S": rs.SIDEWAYS}
    start = date(2021, 1, 1)
    monkeypatch.setattr(rs, "raw_regime", lambda data, day: names[raw[(day - start).days]])
    out = rs.regime_labels(None, start, start + timedelta(days=len(raw) - 1))
    assert [out["labels"][d][0] for d in _days(start, len(raw))] == list("BBBBBBBS")
    assert out["lagged_days"] == 4        # the S, S, S, S held as B


def _trend(n: int, rate: float) -> dict[date, float]:
    return {d: 100.0 * (1 + rate) ** i for i, d in enumerate(_days(date(2020, 1, 1), n))}


def test_the_raw_rule_reads_btc_trend_slope_and_breadth():
    day = date(2020, 1, 1) + timedelta(days=259)
    def regime(btc_rate, member_rates):
        prices = {rs.BTC: _trend(260, btc_rate), **{f"C{i}USDT": _trend(260, r) for i, r in enumerate(member_rates)}}
        data = rs.Data(_market(prices), _monthly(prices, date(2020, 1, 1), 260))
        return rs.raw_regime(data, day)
    assert regime(0.01, [0.01] * 5) == rs.BULL
    assert regime(-0.01, [-0.01] * 5) == rs.BEAR
    assert regime(0.01, [0.01, 0.01, -0.01, -0.01, -0.01]) == rs.SIDEWAYS    # breadth 3/6 < 0.55
    assert regime(-0.01, [0.01] * 5) == rs.SIDEWAYS


# --- the engine --------------------------------------------------------------------------------------------

def _wavy(n: int, symbols: int, start: date) -> dict[str, dict[date, float]]:
    return {f"S{i}USDT": {d: 100.0 * (1 + 0.003 * (i - 3)) ** k * (1.04 if k % (i + 2) == 0 else 1.0)
                          for k, d in enumerate(_days(start, n))} for i in range(symbols)}


def test_b03_plus_d03_is_the_v02_momentum_basket_under_the_same_mechanics():
    start = date(2021, 1, 1)
    prices = _wavy(150, 8, start)
    market = _market(prices)
    for i, s in enumerate(prices):
        market.funding[s] = [(int((xs.datetime(d.year, d.month, d.day, tzinfo=xs.timezone.utc)
                                   .timestamp() + 8 * 3600) * 1000), 0.0001 * (i - 3)) for d in prices[s]]
    monthly = _monthly(prices, start, 150)
    data = rs.Data(market, monthly)
    a, b = start + timedelta(days=40), start + timedelta(days=140)
    long_leg = rs.run(data, rs.Rules(data, _labels(rs.BULL, start, 150)).basket("B03"), a, b)["daily"]
    short_leg = rs.run(data, rs.Rules(data, _labels(rs.BEAR, start, 150)).basket("D03"), a, b)["daily"]
    basket = xs.simulate(market, "S-MOM30", monthly, start=a, end=b)["daily"]
    sunday = next(d for d in _days(a, 7) if d.weekday() == xs.SUNDAY)
    longs = rs.Rules(data, _labels(rs.BULL, start, 150)).basket("B03")(sunday, {})
    shorts = rs.Rules(data, _labels(rs.BEAR, start, 150)).basket("D03")(sunday, {})
    assert longs and all(w > 0 for w in longs.values()) and shorts and all(w < 0 for w in shorts.values())
    winners = sorted(prices, key=lambda s: -(prices[s][sunday] / prices[s][sunday - timedelta(days=30)]))
    assert set(longs) == set(winners[:len(longs)]) and set(shorts) == set(winners[-len(shorts):])
    compared = 0
    for day, row in basket.items():
        if day == b:
            continue                      # the segment's last day also pays the closing exit
        mine = long_leg.get(day, {"net": 0.0})["net"] + short_leg.get(day, {"net": 0.0})["net"]
        assert mine == pytest.approx(row["net"], abs=1e-12)
        compared += 1
    assert compared > 80


def test_a_segment_ends_flat_and_pays_the_exit():
    start = date(2021, 1, 1)
    prices = {"AUSDT": {d: 100.0 for d in _days(start, 20)}}
    data = rs.Data(_market(prices), _monthly(prices, start, 20))
    out = rs.run(data, lambda day, book: {"AUSDT": 1.0}, start + timedelta(days=2), start + timedelta(days=9))
    assert sum(r["cost"] for r in out["daily"].values()) == pytest.approx(2 * UNIT)
    assert sum(r["entries"] for r in out["daily"].values()) == 1


def test_a_book_entered_out_of_its_regime_never_opens():
    start = date(2021, 1, 1)
    falling = {"AUSDT": {d: 100.0 * 0.97 ** k for k, d in enumerate(_days(start, 60))}}
    data = rs.Data(_market(falling), _monthly(falling, start, 60))
    a, b = start + timedelta(days=10), start + timedelta(days=50)
    inside = rs.run(data, rs.Rules(data, _labels(rs.SIDEWAYS, start, 60)).positions("S12"), a, b)["daily"]
    outside = rs.run(data, rs.Rules(data, _labels(rs.BULL, start, 60)).positions("S12"), a, b)["daily"]
    assert sum(r["entries"] for r in inside.values()) > 0
    assert sum(r["entries"] for r in outside.values()) == 0


def test_s12_buys_three_down_closes_and_sells_three_up_closes():
    data = rs.Data(_market({"AUSDT": _trend(40, -0.01), "BUSDT": _trend(40, 0.01)}), {})
    rules = rs.Rules(data, {})
    day = date(2020, 1, 1) + timedelta(days=20)
    assert rs.e_s12(rules, "AUSDT", day)[0] == 1 and rs.e_s12(rules, "BUSDT", day)[0] == -1


def test_the_common_stop_closes_a_long_two_and_a_half_atrs_down():
    start = date(2021, 1, 1)
    series = {d: 100.0 for d in _days(start, 30)}
    for k, d in enumerate(_days(start + timedelta(days=20), 10)):
        series[d] = 100.0 - 4.0 * (k + 1)
    data = rs.Data(_market({"AUSDT": series}), {})
    rules = rs.Rules(data, {})
    p = rs.Position("AUSDT", 1, start + timedelta(days=19), atr=2.0)
    assert not rs.stopped(rules, "B01", p, start + timedelta(days=20))      # −4 vs −5
    assert rs.stopped(rules, "B01", p, start + timedelta(days=21))          # −8
    assert not rs.stopped(rules, "B02", p, start + timedelta(days=21))      # B02 trails at 3 ATR instead


def test_a_pair_puts_half_its_share_on_each_leg():
    book = [rs.Position("AUSDT", -1, date(2021, 1, 1), 1.0, hedge=rs.BTC), rs.Position("CUSDT", 1, date(2021, 1, 1), 1.0)]
    assert rs.weights_of(book) == {"AUSDT": -0.25, rs.BTC: 0.25, "CUSDT": 0.5}


def test_the_random_twin_keeps_days_legs_and_holding_spans():
    start = date(2021, 1, 1)
    prices = _wavy(30, 6, start)
    data = rs.Data(_market(prices), _monthly(prices, start, 30))
    path = {start + timedelta(days=i): ({"S0USDT": 0.5, "S1USDT": -0.5} if 3 <= i < 8 else {}) for i in range(20)}
    twin = rs.random_twin(data, path, "B01", "seed")
    drawn = [twin(d, {}) for d in sorted(path)]
    held = [w for w in drawn if w]
    assert len(held) == 5 and all(sum(abs(v) for v in w.values()) == pytest.approx(1.0) for w in held)
    assert all(w == held[0] for w in held)


def test_the_random_twin_keeps_each_legs_direction_so_market_drift_cancels():
    """A long strategy beats a coin-flip control whenever the market rises. The twin keeps the strategy's
    directions and weights and redraws only the symbols, so in a market where every member moves alike the
    twin earns what the strategy earns, every seed."""
    start = date(2021, 1, 1)
    prices = {f"S{i}USDT": {d: 100.0 * 1.01 ** k for k, d in enumerate(_days(start, 30))} for i in range(6)}
    data = rs.Data(_market(prices), _monthly(prices, start, 30))
    path = {start + timedelta(days=i): ({"S0USDT": 0.5, "S1USDT": 0.25, "S2USDT": -0.25} if 3 <= i < 12 else {})
            for i in range(25)}
    a, b = start + timedelta(days=2), start + timedelta(days=20)
    mine = rs.run(data, lambda day, book: dict(path.get(day, {})), a, b)["daily"]
    for k in range(8):
        twin = rs.random_twin(data, path, "B01", f"seed/{k}")
        for day in path:
            w = twin(day, {})
            # net exposure is kept (two legs drawn onto one symbol net out, as a book would)
            assert sum(w.values()) == pytest.approx(sum(path[day].values()))
        theirs = rs.run(data, twin, a, b)["daily"]
        assert sum(r["gross"] for r in theirs.values()) == pytest.approx(sum(r["gross"] for r in mine.values()))


# --- the reading -------------------------------------------------------------------------------------------

def _row(regime="BULL", weeks=20, mean=50.0, t=2.0, p=0.02, trades=40, vs=10.0, years=None):
    return {"regime": regime, "weeks": weeks, "mean_bps": mean, "t": t, "p_one_sided": p, "trades": trades,
            "vs_random_bps": vs, "years": years or {"2023": 1.0, "2024": 2.0}}


def test_screening_keeps_at_most_three_per_regime_by_t():
    rows = {f"B{i:02d}": _row(t=1.5 + i / 10) for i in range(1, 6)}
    rows["B06"] = _row(t=3.0, trades=29)                       # too few trades
    rows["B07"] = _row(t=3.0, vs=-1.0)                         # loses to random
    rows["B08"] = _row(t=1.49)
    assert rs.candidates(rows) == {rs.BULL: ["B05", "B04", "B03"], rs.SIDEWAYS: [], rs.BEAR: []}


def test_a_derived_candidate_carries_its_parents_p_values_once():
    rows = {"B03": _row(p=0.001), "D03": _row(regime="BEAR", p=0.002), "B01": _row(p=0.003)}
    fam = rs.family(rows)
    assert set(fam) == {"B03", "D03", "B01", "v0.1 S-MOM30", "v0.2 S-MOM30"}
    assert rs.family({"S04": _row(regime="SIDEWAYS")})["v0.1 S-MOM7"] == 0.28617573036292365
    assert set(rs.family({"D07": _row(regime="BEAR")})) == {"D07", "v0.2 S-CARRY"}


def test_below_the_floor_a_candidate_is_not_judgeable_and_leaves_the_family():
    rows = {"B01": _row(weeks=12, p=0.0001), "B02": _row(trades=19, p=0.0001), "B04": _row(p=0.001)}
    verdicts = rs.confirm(rows)
    assert verdicts == {"B01": "NOT_JUDGEABLE", "B02": "NOT_JUDGEABLE", "B04": "CONFIRMED"}
    assert set(rs.family(rows)) == {"B04"}
    # a derived candidate below the floor is no test, so it brings no prior p values either
    assert set(rs.family({"D03": _row(regime="BEAR", weeks=12), "B04": _row()})) == {"B04"}


def test_confirmation_needs_holm_random_and_a_majority_of_positive_years():
    assert rs.confirm({"B01": _row(p=0.2)})["B01"] == "NOT_CONFIRMED"
    assert rs.confirm({"B01": _row(vs=-1.0)})["B01"] == "NOT_CONFIRMED"
    assert rs.confirm({"B01": _row(years={"2023": 1.0, "2024": -2.0})})["B01"] == "NOT_CONFIRMED"
    # Holm at 0.10 over B03, B01 and S-MOM30's two priors: after 0.0016, B03 must clear 0.10/3
    assert rs.confirm({"B03": _row(p=0.033), "B01": _row(p=0.5)})["B03"] == "CONFIRMED"
    rows = {"B03": _row(p=0.034), "B01": _row(p=0.5)}
    assert rs.confirm(rows)["B03"] == "NOT_CONFIRMED"


def test_the_p_value_is_one_sided():
    up = rs.t_stats([0.01, 0.02, 0.015, 0.012])
    down = rs.t_stats([-0.01, -0.02, -0.015, -0.012])
    assert up["p_one_sided"] < 0.01 and down["p_one_sided"] > 0.99
    assert up["p_one_sided"] == pytest.approx(0.5 * math.erfc(up["t"] / math.sqrt(2)))


# --- the stage-1 record and the command line ----------------------------------------------------------------

def _stage_one(tmp_path, **over):
    record = {"spec": rs.SPEC, "stage": 1, "code_digest": rs.code_digest(),
              "holdout_quarters": [f"{y}Q{q}" for y, q in rs.holdout_quarters()],
              "candidates": {rs.BULL: ["B03"], rs.SIDEWAYS: [], rs.BEAR: ["D11", "D01"]}, **over}
    path = tmp_path / "stage1.json"
    path.write_text(json.dumps(record))
    return path


def test_stage_two_reads_only_this_tests_stage_one_record(tmp_path):
    assert rs.read_stage_one(_stage_one(tmp_path)) == {rs.BULL: ["B03"], rs.SIDEWAYS: [], rs.BEAR: ["D11", "D01"]}
    assert rs.read_stage_one(_stage_one(tmp_path, stage=2)) is None
    assert rs.read_stage_one(_stage_one(tmp_path, spec="X")) is None
    assert rs.read_stage_one(_stage_one(tmp_path, code_digest="0" * 16)) is None    # the code changed between stages
    assert rs.read_stage_one(_stage_one(tmp_path, holdout_quarters=["2021Q1"])) is None
    assert rs.read_stage_one(_stage_one(tmp_path, candidates={rs.BULL: ["D01"], rs.SIDEWAYS: [], rs.BEAR: []})) is None
    assert rs.read_stage_one(_stage_one(tmp_path, candidates={rs.BULL: ["B01", "B02", "B04", "B05"],
                                                              rs.SIDEWAYS: [], rs.BEAR: []})) is None


def test_the_stages_run_in_the_sandbox_and_stage_two_refuses_without_a_record(tmp_path, monkeypatch):
    assert rs.main(["stage1", "--archive", str(tmp_path)]) == rs.EXIT_USAGE
    monkeypatch.setattr(xs, "_in_sandbox", lambda: True)
    assert rs.main(["stage2", "--stage1", str(tmp_path / "none.json"), "--archive", str(tmp_path)]) == rs.EXIT_BLOCKED
    assert rs.main(["stage1", "--archive", str(tmp_path)]) == rs.EXIT_BLOCKED      # no contract snapshot


def test_open_interest_is_the_day_files_last_row(tmp_path):
    folder = tmp_path / rs.METRICS_DIR / "AUSDT"
    folder.mkdir(parents=True)
    text = ("create_time,symbol,sum_open_interest,x\n2022-06-01 00:00:00,AUSDT,10,1\n"
            "2022-06-01 23:55:00,AUSDT,12.5,1\n")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("AUSDT-metrics-2022-06-01.csv", text)
    (folder / "AUSDT-metrics-2022-06-01.zip").write_bytes(buffer.getvalue())
    assert rs.read_oi(tmp_path, ["AUSDT", "BUSDT"]) == {"AUSDT": {date(2022, 6, 1): 12.5}}


def test_every_strategy_of_the_record_is_registered_once_in_its_regime():
    assert len(rs.STRATEGIES) == 36
    for prefix, regime in (("B", rs.BULL), ("S", rs.SIDEWAYS), ("D", rs.BEAR)):
        ids = [f"{prefix}{i:02d}" for i in range(1, 13)]
        assert all(rs.STRATEGIES[i].regime == regime for i in ids)
    assert {s for s, v in rs.STRATEGIES.items() if "oi" in v.needs} == {"S11", "D11"}
    assert {s for s, v in rs.STRATEGIES.items() if v.kind == "basket"} == {"B03", "B07", "S04", "S09", "D03", "D07"}


def test_the_oi_plan_covers_each_members_months_from_four_days_before():
    monthly = {date(2021, 1, 1): ["AUSDT"], date(2021, 2, 1): ["AUSDT", "BUSDT"], date(2021, 3, 1): ["BUSDT"],
               date(2021, 5, 1): ["AUSDT"]}
    assert rs.oi_plan(monthly, start=date(2021, 1, 1), end=date(2021, 5, 10)) == [
        ("AUSDT", date(2020, 12, 28), date(2021, 2, 28)), ("AUSDT", date(2021, 4, 27), date(2021, 5, 10)),
        ("BUSDT", date(2021, 1, 28), date(2021, 3, 31))]
