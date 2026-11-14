"""The signal lab (STRATEGY_RESEARCH_REBUILD_V0.1, Thomas 2026-11-13): nothing after the span is read, a return is the
one a book would hold, scenarios look backwards only, a thin cell shows no number, and the rule that turns a cell into
a lead. Synthetic markets only."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, timedelta

import pytest

from scripts.research import cross_section_basket as xsb
from scripts.research import signal_lab as lab

SPAN = (date(2020, 1, 1), date(2021, 6, 30))
SYMBOLS = ["BTCUSDT"] + [f"C{i:02d}USDT" for i in range(13)]


def _unit(*parts) -> float:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:12], 16) / 16 ** 12


def _market(last: date, *, twist_after: date | None = None) -> xsb.Market:
    """Fourteen coins from 2019-06-01 to ``last`` on seeded random walks. ``twist_after`` rewrites every bar after
    that day with different numbers, to show that nothing earlier depends on them."""
    bars: dict[str, dict[date, xsb.Bar]] = {}
    for rank, symbol in enumerate(SYMBOLS):
        price, day, out = 100.0 + rank, date(2019, 6, 1), {}
        while day <= last:
            twist = "x" if twist_after is not None and day > twist_after else ""
            step = (_unit(symbol, day, twist) - 0.5) * 0.08
            opened, price = price, price * (1 + step)
            out[day] = xsb.Bar(open=opened, close=price, quote_volume=(1000.0 - 10 * rank) * (1 + _unit("v", symbol, day, twist)),
                               high=max(opened, price) * 1.01, low=min(opened, price) * 0.99)
            day += timedelta(days=1)
        bars[symbol] = out
    funding = {s: [(int((d - date(1970, 1, 1)).total_seconds() * 1000) + 8 * 3600 * 1000, (_unit("f", s, d) - 0.4) * 1e-4)
                   for d in sorted(days)] for s, days in bars.items()}
    return xsb.Market(bars=bars, funding=funding)


NAMES = {"momentum_30": lab.variables()["momentum_30"], "vol_30": lab.variables()["vol_30"],
         "funding_7": lab.variables()["funding_7"], **{k: v for k, v in list(lab.controls().items())[:2]}}


@pytest.fixture(scope="module")
def tiers(monkeypatch_module):
    monkeypatch_module.setattr(lab, "TIERS", {"top30": (0, 10), "rank31_100": (10, 14)})


@pytest.fixture(scope="module")
def monkeypatch_module():
    patch = pytest.MonkeyPatch()
    yield patch
    patch.undo()


def test_the_map_reads_nothing_after_the_spans_last_day(tiers):
    """The span ends 2021-06-30. A market that runs on to 2021-12 gives, once cut, exactly the map of a market that
    stops there — and of one whose later bars are different numbers altogether."""
    stops = lab.grid(lab.cut_market(_market(SPAN[1]), SPAN), NAMES, SPAN)
    runs_on = lab.grid(lab.cut_market(_market(date(2021, 12, 31)), SPAN), NAMES, SPAN)
    twisted = lab.grid(lab.cut_market(_market(date(2021, 12, 31), twist_after=SPAN[1]), SPAN), NAMES, SPAN)
    assert stops == runs_on == twisted
    assert any(not c["too_few"] for c in stops["cells"]), "the fixture is long enough to show cells"
    with pytest.raises(ValueError, match="cut it first"):
        lab.grid(_market(date(2021, 12, 31)), NAMES, SPAN)


def test_no_decision_reaches_past_the_span_and_a_long_horizon_does_not_overlap():
    weekly, monthly = lab.decisions(SPAN, 7), lab.decisions(SPAN, 28)
    assert all(d.weekday() == xsb.SUNDAY for d in weekly)
    assert max(weekly) + timedelta(days=8) <= SPAN[1] < max(weekly) + timedelta(days=15)
    assert max(monthly) + timedelta(days=29) <= SPAN[1]
    assert all((b - a).days == 28 for a, b in zip(monthly, monthly[1:]))
    # The registered span itself: the last return ends on or before 2024-12-31.
    assert max(lab.decisions(lab.SPAN, 7)) + timedelta(days=8) <= date(2024, 12, 31) == lab.SPAN[1]


def test_a_return_is_from_the_next_open_and_only_between_traded_days():
    day = date(2020, 3, 1)
    bars = {day + timedelta(days=k): xsb.Bar(open=100.0 + k, close=200.0 + k, quote_volume=5.0) for k in range(10)}
    market = xsb.Market(bars={"AUSDT": dict(bars)})
    assert lab.forward_return(market, "AUSDT", day, 7) == pytest.approx(108.0 / 101.0 - 1)   # opens of day+1 and day+8
    for frozen in (0, 1, 8):                 # the decision day, the entry day, the exit day
        stale = dict(bars)
        stale[day + timedelta(days=frozen)] = xsb.Bar(open=100.0, close=100.0, quote_volume=0.0)
        assert lab.forward_return(xsb.Market(bars={"AUSDT": stale}), "AUSDT", day, 7) is None
    assert lab.forward_return(market, "AUSDT", day, 28) is None, "no bar at the far end"


def test_a_scenario_label_is_read_from_that_day_and_earlier(tiers):
    full = lab.cut_market(_market(SPAN[1]), SPAN)
    members = lab.tier_members(full, SPAN)["top30"]
    days = lab.decisions(SPAN, 7)
    labels = lab.scenario_labels(full, members, days, SPAN)
    middle = days[len(days) // 2]
    early = (SPAN[0], middle)
    cut = lab.cut_market(_market(SPAN[1]), early)
    assert lab.scenario_labels(cut, lab.tier_members(cut, early)["top30"], [middle], early)[middle] == labels[middle]
    seen = {key for row in labels.values() for key in lab.scenario_keys(row)}
    assert "all" in seen and any(k.startswith("vol=") for k in seen) and any(k.startswith("heat=") for k in seen)
    assert lab.scenario_keys({}) == ["all"], "a decision too early for any label still counts in the whole"
    assert lab.scenario_keys({"trend": "bull", "vol": "high"}) == ["all", "trend=bull", "vol=high", "trend=bull|vol=high"]


def test_rank_correlation_and_the_tercile_spread():
    rising = [(float(i), float(i) * 2) for i in range(9)]
    assert lab.rank_correlation(rising) == pytest.approx(1.0)
    assert lab.rank_correlation([(a, -b) for a, b in rising]) == pytest.approx(-1.0)
    assert lab.rank_correlation([(1.0, 5.0)] * 9) is None, "no spread on a side is not a correlation of zero"
    assert lab.rank_correlation([(1.0, 1.0), (1.0, 2.0), (2.0, 3.0), (2.0, 4.0)]) == pytest.approx(0.8944, abs=1e-4)
    assert lab.tercile_spread(rising) == pytest.approx((12 + 14 + 16) / 3 - (0 + 2 + 4) / 3)


def test_a_thin_cell_shows_no_number_and_one_long_stretch_is_one_sample():
    assert lab.spells([0, 1, 2, 5, 6, 9]) == 3 and lab.spells([]) == 0
    steady = [(i, 0.1 + 0.01 * (i % 3), 0.002) for i in range(40)]
    assert lab.cell(steady, scenario="all")["too_few"] is False, "the whole span is not asked for spells"
    assert lab.cell(steady, scenario="trend=bull") == {"decisions": 40, "spells": 1, "too_few": True}
    broken = [(i * 3, 0.1 + 0.01 * (i % 3), 0.002) for i in range(40)]
    shown = lab.cell(broken, scenario="trend=bull")
    assert shown["too_few"] is False and shown["spells"] == 40 and shown["t"] > 2 and shown["positive_share"] == 1.0
    assert shown["tercile_spread_bps"] == 20.0
    assert lab.cell(broken[:lab.MIN_DECISIONS - 1], scenario="all")["too_few"] is True


def test_the_random_variables_are_fixed_by_their_names():
    market, day = xsb.Market(), date(2021, 1, 3)
    weekly, fixed = lab.controls()["control_week_00"][1], lab.controls()["control_fixed_00"][1]
    assert 0 <= weekly(market, "AUSDT", day) < 1
    assert weekly(market, "AUSDT", day) == weekly(market, "AUSDT", day) != weekly(market, "AUSDT", day + timedelta(days=7))
    assert fixed(market, "AUSDT", day) == fixed(market, "AUSDT", day + timedelta(days=7)) != fixed(market, "BUSDT", day)
    assert len(lab.controls()) == lab.CONTROLS_WEEKLY + lab.CONTROLS_FIXED
    assert not set(lab.controls()) & set(lab.variables())


def _cell(variable, scenario, t, *, ic=None, tier="top30", horizon=7):
    return {"variable": variable, "group": "g", "tier": tier, "horizon_days": horizon, "scenario": scenario,
            "decisions": 60, "spells": 9, "too_few": False, "t": t,
            "mean_rank_correlation": (0.05 if t > 0 else -0.05) if ic is None else ic, "positive_share": 0.6,
            "tercile_spread_bps": 10.0}


def test_a_lead_must_beat_every_random_variable_keep_its_rank_and_agree_with_its_own_whole(monkeypatch):
    monkeypatch.setattr(lab, "RESTING_AXES", frozenset())          # the rule itself, with every axis in use
    against = {"7": {"largest_abs_t": 3.0, "share_abs_t_over_2": 0.05}, "28": {"largest_abs_t": None, "share_abs_t_over_2": None}}
    cells = [
        _cell("steady", "all", 2.5), _cell("steady", "trend=bull", 3.4),                    # a lead
        _cell("steady", "trend=bear", -3.6),                                                # against its own whole
        _cell("flipping", "all", 2.1), _cell("flipping", "vol=high", 4.0),                  # does not keep its rank
        _cell("ordinary", "all", 1.0), _cell("ordinary", "heat=hot", 2.9),                  # inside chance
        _cell("orphan", "vol=low", 5.0),                                                    # no whole to agree with
        _cell("slow", "all", 3.1, horizon=28),                                              # no bar at that horizon
        *[_cell(f"extra{i}", s, 3.2 + i / 10) for i in range(4) for s in ("all", "heat=cool")],
    ]
    real = {"cells": cells, "variables": {
        **{f"{name}|top30": {"rank_persistence": 0.9} for name in ("steady", "ordinary", "orphan", "slow", *(f"extra{i}" for i in range(4)))},
        "flipping|top30": {"rank_persistence": 0.1}}}
    out = lab.candidates(real, against)
    met = {(c["variable"], c["scenario"]) for c in out["leads"]}
    assert out["met_the_rule"] == 1 + 8 and len(out["leads"]) == lab.CANDIDATE_CAP == 3
    assert met == {("extra3", "all"), ("extra3", "heat=cool"), ("extra2", "all")}, "largest first, then by name"
    every = lab.candidates({**real, "cells": cells[:9]}, against)
    assert [(c["variable"], c["scenario"]) for c in every["leads"]] == [("steady", "trend=bull")]
    assert lab.excess(cells[:9], against)["7"] == {"cells": 8, "abs_t_over_2": 7, "expected_from_random": 0.4}


def test_chance_is_what_the_random_variables_reached():
    cells = [_cell(f"control_week_{i:02d}", "all", t) for i, t in enumerate((0.3, -1.1, 2.4, -3.2, 0.9))]
    cells.append({**_cell("control_week_09", "vol=high", 9.0), "too_few": True})
    out = lab.chance(cells)
    assert out["7"] == {"cells": 5, "largest_abs_t": 3.2, "p95_abs_t": 2.4, "share_abs_t_over_2": 0.4}
    assert out["28"]["largest_abs_t"] is None


def test_the_baseline_pool_is_the_plain_mean_of_every_registered_definition():
    span = (date(2020, 1, 1), date(2020, 12, 31))
    calls = []

    def simulate(market, cadence, monthly, *, start, end, weigher):
        calls.append(weigher)
        scale = len(calls)
        return {"daily": {date(2020, m, 15): {"net": 0.01 * scale * (1 if m % 2 else -0.5)} for m in range(1, 13)}}

    out = lab.pool(xsb.Market(bars={"BTCUSDT": {date(2019, 12, 31): xsb.Bar(open=1.0, close=1.0, quote_volume=1.0)}}),
                   span, simulate=simulate)
    count = len(lab.fp.members())
    assert len(calls) == count == len(out["members"]) == 16
    mean_scale = sum(range(1, count + 1)) / count
    assert out["pool"]["sum_pct"] == pytest.approx(round(0.01 * mean_scale * (6 - 3) * 100, 2))
    assert out["pool"]["months"] == 12 and isinstance(out["pool"]["clears_entry"], bool)


def test_it_runs_in_the_sandbox_only(monkeypatch, capsys):
    monkeypatch.setattr(lab.xsb, "_in_sandbox", lambda: False)
    assert lab.main(["run"]) == lab.EXIT_USAGE
    capsys.readouterr()
    assert lab.main(["list"]) == lab.EXIT_OK
    listed = json.loads(capsys.readouterr().out)
    assert listed["span"] == ["2020-01-01", "2024-12-31"]
    assert listed["controls"] == lab.CONTROLS_WEEKLY + lab.CONTROLS_FIXED >= 2 * len(lab.variables()) - 4
    assert {"price_volume", "funding", "positioning", "depth", "spot", "coin_character", "trade_flow",
            "premium"} == set(listed["variables"].values())


def test_a_book_that_reads_bars_and_funding_alone_loads_nothing_else(monkeypatch, tmp_path):
    """The attached series are most of the memory. A span that runs to 2026 is loaded without them when the
    variable does not read them, and is still cut at the span's last day."""
    span = (date(2020, 1, 1), date(2021, 6, 30))
    monkeypatch.setattr(lab.xsb, "read_contract_types", lambda archive: ({"BTCUSDT": "PERPETUAL"}, None))
    monkeypatch.setattr(lab.xsb, "load_market", lambda archive, funding=True: _market(date(2021, 12, 31)))

    def never(*args, **kwargs):
        raise AssertionError("an attached series was loaded")

    for module, name in ((lab.ds, "load_daily"), (lab.ms, "load_daily"), (lab.sp, "attach"), (lab.sp, "months_on_disk")):
        monkeypatch.setattr(module, name, never)
    held = lab.hold(tmp_path, span, attach=False)
    assert lab.last_day(held) == span[1] and held.contract_types == {"BTCUSDT": "PERPETUAL"}
    assert not hasattr(held, "spot") and not hasattr(held, "metrics")
    monkeypatch.setattr(lab.xsb, "read_contract_types", lambda archive: None)
    assert lab.hold(tmp_path, span, attach=False) is None


def test_the_coin_character_variables_read_age_and_extreme_days():
    start = date(2021, 1, 1)
    bars, price = {}, 100.0
    for k in range(60):
        move = 0.4 if k in (50, 55) else 0.01
        opened, price = price, price * (1 + move)
        bars[start + timedelta(days=k)] = xsb.Bar(open=opened, close=price, quote_volume=10.0)
    market = xsb.Market(bars={"NEWUSDT": bars})
    day = start + timedelta(days=59)
    age, extreme = lab.variables()["listing_age"][1], lab.variables()["extreme_days_30"][1]
    assert age(market, "NEWUSDT", day) == 59.0 and age(market, "NEWUSDT", start - timedelta(days=1)) is None
    assert extreme(market, "NEWUSDT", day) == 2.0
    assert extreme(market, "NEWUSDT", start + timedelta(days=5)) is None, "too little history to count"
    old = xsb.Market(bars={"OLDUSDT": {date(2015, 1, 1): xsb.Bar(open=1.0, close=1.0, quote_volume=1.0)}})
    assert age(old, "OLDUSDT", day) == float(lab.AGE_CAP_DAYS)


def test_the_universe_axis_splits_weeks_by_the_share_of_wild_coins(tiers):
    full = lab.cut_market(_market(SPAN[1]), SPAN)
    members = lab.tier_members(full, SPAN)["top30"]
    labels = lab.scenario_labels(full, members, lab.decisions(SPAN, 7), SPAN)
    seen = {row.get("universe") for row in labels.values()}
    assert seen <= {"wild", "calm", None} and "calm" in seen, "the fixture's coins never move fifty percent in a day"
    assert lab.scenario_keys({"universe": "wild"}) == ["all", "universe=wild"]


# --- the monthly checks: is an axis worth having, and what on disk is not read ---------------------------------------

def test_an_axis_that_moves_a_variable_separates_and_a_shuffled_copy_of_it_does_not():
    days = [date(2021, 1, 3) + timedelta(days=7 * k) for k in range(120)]
    real = (["hot"] * 10 + ["cool"] * 10) * 6
    series = {("x", "top30"): {d: (0.2 if label == "hot" else -0.2) + (0.01 if k % 2 else -0.01)
                               for k, (d, label) in enumerate(zip(days, real))},
              ("flat", "top30"): {d: (0.05 if k % 2 else -0.05) for k, d in enumerate(days)}}
    actual = lab.separation(series, dict(zip(days, real)))
    fakes = [lab.separation(series, dict(zip(days, lab.shuffled_spells(real, f"s{k}")))) for k in range(50)]
    assert actual == pytest.approx((0.04 / 0.0401 + 0.0) / 2, rel=1e-3)
    assert all(f <= actual + 1e-12 for f in fakes) and sum(1 for f in fakes if f < actual * 0.9) > 25
    assert lab.separation(series, {d: None for d in days}) is None
    assert lab.separation(series, dict(zip(days, ["hot"] * 119 + ["cool"]))) is None, "a state too thin to compare"
    shuffled = lab.shuffled_spells(real, "s1")
    assert sorted(shuffled) == sorted(real) and lab.shuffled_spells(real, "s1") == shuffled


def test_the_axis_check_reads_every_axis_beside_its_placebos(tiers, monkeypatch):
    monkeypatch.setattr(lab, "PLACEBO_AXES", 8)
    monkeypatch.setattr(lab, "FINER_PLACEBOS", 5)
    out = lab.axis_check(lab.cut_market(_market(SPAN[1]), SPAN), NAMES, SPAN)
    assert set(out["axes"]) == {"trend", "vol", "dispersion", "heat", "universe", "breadth", *lab.CANDIDATE_AXES,
                                *lab.FINER_AXES, "funding_spread"}
    assert out["axes"]["funding_spread"]["level"] == round(0.05 / 14, 5), "the fourteenth axis checked"
    assert "level" not in out["axes"]["trend"] and out["axes"]["breadth"]["level"] == 0.05
    assert out["axes"]["month"]["level"] == round(0.05 / 6, 5) and isinstance(out["axes"]["month"]["beats_its_placebos"], bool)
    assert out["axes"]["leverage"]["separation"] is None and out["axes"]["leverage"]["beats_its_placebos"] is False, \
        "no positioning values attached: the leverage axis has no label and passes nothing"
    for axis, body in out["axes"].items():
        fine = axis in lab.FINER_AXES
        assert set(body) - {"level", "p", "beats_its_placebos"} == {
            "states", "separation", "placebo_mean", "placebo_p95", "placebos_at_or_above", "placebos",
            *(("parts", "merged", "split") if fine else ())}, axis
        assert ("level" in body) == (axis in lab.AXIS_ORDER), "only the axes checked since breadth carry a level"
        late = fine or axis in lab.LATE_AXES
        assert body["placebos"] <= (5 if late else 8) and sum(body["states"].values()) == out["decisions"], axis
    fine = out["axes"]["vol_fine"]
    assert fine["level"] == round(0.05 / 9, 5) and sum(n["decisions"] for n in fine["parts"].values()) \
        == out["decisions"] - out["axes"]["vol"]["states"]["None"], "the finer cut labels the weeks the coarse one does"
    monkeypatch.setattr(lab, "MIN_DECISIONS", 8)                 # the fixture is 78 weeks: a third of them is under 20
    again = lab.axis_check(lab.cut_market(_market(SPAN[1]), SPAN), NAMES, SPAN)
    fine = again["axes"]["vol_fine"]
    assert fine["split"] == ["vol"] and fine["placebos"] == 5 and set(fine["states"]) == {"None", "high", "mid", "low"}
    assert fine["separation"] is not None and 0 <= fine["placebos_at_or_above"] <= 5 and fine["p"] is not None
    monkeypatch.setattr(lab, "FINER_AXES", ())
    monkeypatch.setattr(lab, "LATE_AXES", ())
    without = lab.axis_check(lab.cut_market(_market(SPAN[1]), SPAN), NAMES, SPAN)
    assert without["axes"] == {axis: body for axis, body in again["axes"].items()
                               if axis not in lab.FINER_OF and axis != "funding_spread"}, \
        "checking the later axes changes no other axis's row"


def test_the_inventory_names_what_is_on_disk_and_the_kline_columns_no_loader_keeps(tmp_path, capsys, monkeypatch):
    import zipfile
    folder = tmp_path / "archive" / lab.xsb.KLINES_KIND / "BTCUSDT"
    folder.mkdir(parents=True)
    with zipfile.ZipFile(folder / "BTCUSDT-1d-2021-01.zip", "w") as z:
        z.writestr("x.csv", "1609459200000,1,2,0.5,1.5,10,1609545599999,15,7,4,6,0\n")
    (tmp_path / "archive" / lab.xsb.FUNDING_KIND / "BTCUSDT").mkdir(parents=True)
    (tmp_path / "archive-spot" / lab.xsb.KLINES_KIND / "BTCUSDT").mkdir(parents=True)
    out = lab.inventory(tmp_path)
    assert out["kinds"] == {f"archive/{lab.xsb.FUNDING_KIND}": 1, f"archive/{lab.xsb.KLINES_KIND}": 1,
                            f"archive-spot/{lab.xsb.KLINES_KIND}": 1}
    assert out["kline_columns_in_file"] == len(lab.KLINE_COLUMNS) == 12
    assert out["kline_columns_not_read"] == ["volume", "count", "taker_buy_volume", "taker_buy_quote_volume"]
    bar = lab.xsb.read_klines(folder)[date(2021, 1, 1)]
    assert (bar.open, bar.high, bar.low, bar.close, bar.quote_volume) == (1.0, 2.0, 0.5, 1.5, 15.0), "the five it keeps"
    monkeypatch.setattr(lab.xsb, "_in_sandbox", lambda: False)
    assert lab.main(["inventory", "--cache", str(tmp_path)]) == lab.EXIT_OK and "kline_columns_not_read" in capsys.readouterr().out
    assert lab.main(["axes", "--cache", str(tmp_path)]) == lab.EXIT_USAGE


def test_a_placebo_axis_leaves_the_unlabelled_decisions_unlabelled():
    real = [None] * 6 + ["hot"] * 5 + ["cool"] * 4 + [None] + ["hot"] * 3 + ["cool"] * 7
    for k in range(10):
        fake = lab.shuffled_spells(real, f"seed-{k}")
        assert [i for i, v in enumerate(fake) if v is None] == [i for i, v in enumerate(real) if v is None]
        assert sorted(v for v in fake if v) == sorted(v for v in real if v)
    assert any(lab.shuffled_spells(real, f"seed-{k}") != real for k in range(10))


# --- round 4: the kline columns nobody read, and the axis check one variable at a time -------------------------------

def _kline_zip(folder, name, rows):
    import zipfile
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(folder / name, "w") as z:
        z.writestr("x.csv", "open_time,open,high,low,close,volume,close_time,quote_volume,count,tbv,tbqv,ignore\n"
                   + "".join(",".join(map(str, r)) + "\n" for r in rows))


def _ms(day):
    return int((day - date(1970, 1, 1)).total_seconds() * 1000)


def test_the_trade_flow_reader_keeps_the_taker_share_and_the_size_of_a_trade(tmp_path):
    day = date(2021, 3, 1)
    _kline_zip(tmp_path / "AUSDT", "AUSDT-1d-2021-03.zip", [
        [_ms(day), 1, 2, 0.5, 1.5, 10, 0, 200.0, 40, 5, 120.0, 0],                        # 60% taker buys, 5 a trade
        [_ms(day + timedelta(days=1)), 1, 2, 0.5, 1.5, 0, 0, 0.0, 0, 0, 0.0, 0],            # no volume: left out
        [_ms(day + timedelta(days=2)), 1, 2, 0.5, 1.5, 10, 0, 100.0, 10, 5, 150.0, 0],      # taker above total: not a share
    ])
    assert lab.read_trade_flow(tmp_path / "AUSDT") == {day: (0.6, 5.0)}
    assert lab.read_trade_flow(tmp_path / "MISSING") == {}


def test_the_trade_flow_variables_average_the_month_and_compare_it_with_the_month_before():
    day = date(2021, 4, 30)
    flow = {day - timedelta(days=k): (0.55 if k < 30 else 0.45, 20.0 if k < 30 else 10.0) for k in range(60)}
    market = xsb.Market()
    market.trade_flow = {"AUSDT": flow, "THINUSDT": {day: (0.5, 1.0)}}
    v = lab.variables()
    assert v["taker_buy_share_30"][1](market, "AUSDT", day) == pytest.approx(0.55)
    assert v["trade_size_30"][1](market, "AUSDT", day) == pytest.approx(20.0)
    assert v["trade_size_growth_30"][1](market, "AUSDT", day) == pytest.approx(1.0), "twice the month before"
    for name in ("taker_buy_share_30", "trade_size_30", "trade_size_growth_30"):
        assert v[name][0] == "trade_flow" and v[name][1](market, "THINUSDT", day) is None
        assert v[name][1](xsb.Market(), "AUSDT", day) is None, "no trade-flow attached: no value, not a zero"


def test_trade_flow_is_attached_for_the_coins_the_tiers_hold_and_cut_at_the_spans_end(tiers, tmp_path, monkeypatch):
    full = lab.cut_market(_market(SPAN[1]), SPAN)
    held_symbols = {s for monthly in lab.tier_members(full, SPAN).values() for m in monthly.values() for s in m}
    asked = []

    def read(folder):
        asked.append(folder.name)
        return {SPAN[1]: (0.5, 2.0), SPAN[1] + timedelta(days=1): (0.9, 9.0)}

    monkeypatch.setattr(lab, "read_trade_flow", read)
    out = lab.attach_trade_flow(full, tmp_path, SPAN)
    assert set(asked) == held_symbols == set(out.trade_flow)
    assert all(list(days) == [SPAN[1]] for days in out.trade_flow.values()), "nothing after the span"
    assert lab.last_day(lab.cut_market(out, SPAN)) == SPAN[1]


def test_the_axis_check_by_variable_finds_the_one_an_axis_moves_and_counts_what_chance_gives(monkeypatch):
    days = [date(2021, 1, 3) + timedelta(days=7 * k) for k in range(120)]
    heat = (["hot"] * 10 + ["cool"] * 10) * 6
    series = {("moved", "top30"): {d: (0.2 if h == "hot" else -0.2) + (0.01 if k % 2 else -0.01) for k, (d, h) in enumerate(zip(days, heat))},
              ("still", "top30"): {d: 0.05 * (((k * 7919) % 13) - 6) / 6 for k, d in enumerate(days)}}
    monkeypatch.setattr(lab, "PLACEBO_AXES_BY_VARIABLE", 60)
    monkeypatch.setattr(lab, "ic_series", lambda market, names, span: (days, series))
    monkeypatch.setattr(lab, "tier_members", lambda market, span: {"top30": {}})
    monkeypatch.setattr(lab, "scenario_labels", lambda market, monthly, asked, span: {d: {"heat": h} for d, h in zip(days, heat)})
    out = lab.axis_by_variable(xsb.Market(), {}, SPAN)
    assert out["tests"] == 2 and out["expected_under_0.05_by_chance"] == 0.1
    first = out["smallest_p"][0]
    assert (first["variable"], first["axis"]) == ("moved", "heat") and first["p"] == pytest.approx(1 / 61, abs=1e-4)
    assert first["state_means"] == {"cool": pytest.approx(-0.2, abs=0.01), "hot": pytest.approx(0.2, abs=0.01)}
    assert [(t["variable"], t["axis"]) for t in out["survive_benjamini_hochberg"]] == [("moved", "heat")]
    assert len(out["all_tests"]) == 2


def test_a_run_may_map_one_group_alone_and_refuses_a_group_nobody_registered(monkeypatch, capsys):
    monkeypatch.setattr(lab.xsb, "_in_sandbox", lambda: True)
    assert lab.main(["run", "--only", "no_such_group"]) == lab.EXIT_USAGE
    assert "no registered variable is in group" in capsys.readouterr().err
    asked = {}

    def hold(cache, span=None, *, attach=True, flow=False):
        asked.update(attach=attach, flow=flow)
        return None

    monkeypatch.setattr(lab, "hold", hold)
    assert lab.main(["run", "--only", "trade_flow"]) == lab.EXIT_BLOCKED
    assert asked == {"attach": False, "flow": True}, "the trade-flow columns and nothing heavier"
    assert lab.main(["run", "--only", "depth"]) == lab.EXIT_BLOCKED and asked == {"attach": True, "flow": False}
    assert lab.main(["run", "--only", "spot"]) == lab.EXIT_BLOCKED and asked == {"attach": False, "flow": False}, \
        "the spot group loads its bars through the lighter loader"


def test_a_run_of_the_positioning_group_loads_its_values_alone_cut_at_the_spans_end(monkeypatch, capsys):
    asked = []
    span_end = lab.SPAN[1]
    late = span_end + timedelta(days=2)
    row = {"oi": 1.0, "ls": 0.5, "top": None, "taker": None}

    def hold(cache, span=None, *, attach=True, flow=False):
        asked.append((attach, flow))
        return None if len(asked) == 1 else lab.xsb.Market(bars={})

    monkeypatch.setattr(lab.xsb, "_in_sandbox", lambda: True)
    monkeypatch.setattr(lab, "hold", hold)
    monkeypatch.setattr(lab.ms, "load_daily", lambda cache: {"AUSDT": {span_end: row, late: row}})
    assert lab.main(["run", "--only", "positioning"]) == lab.EXIT_BLOCKED, "no contract types"
    held = lab.hold_positioning("cache")
    assert asked == [(False, False), (False, False)], "the bars and funding, without depth or spot"
    assert held.metrics == {"AUSDT": {span_end: row}}, "nothing after the span's last day"
    assert not hasattr(held, "depth") and not hasattr(held, "spot")
    monkeypatch.setattr(lab.ms, "load_daily", lambda cache: {})
    assert lab.hold_positioning("cache") is None, "no derived positioning values"
    capsys.readouterr()


def test_a_positioning_change_is_the_level_now_less_the_level_a_lookback_earlier_and_reads_nothing_later():
    day = date(2023, 6, 4)
    days = [day - timedelta(days=k) for k in range(-10, 80)]

    def world(later=0.0):
        """AUSDT's long share: 0.40 until 30 days before ``day``, 0.50 since; ``later`` is added after ``day``."""
        market = xsb.Market(bars={"AUSDT": {}, "BUSDT": {}})
        market.metrics = {"AUSDT": {d: {"oi": 1.0, "ls": (0.40 if d <= day - timedelta(days=30) else 0.50) + (later if d > day else 0.0),
                                        "top": 0.60, "taker": None} for d in days},
                          "BUSDT": {d: {"oi": 1.0, "ls": 0.5, "top": None, "taker": None} for d in days[:40]}}
        return market

    names = lab.variables()
    assert {n: names[n][0] for n in ("ls_change_30", "top_change_30", "taker_change_30")} == dict.fromkeys(
        ("ls_change_30", "top_change_30", "taker_change_30"), "positioning")
    change, level = names["ls_change_30"][1], names["ls_level_30"][1]
    assert level(world(), "AUSDT", day) == pytest.approx(0.50) and level(world(), "AUSDT", day - timedelta(days=30)) == pytest.approx(0.40)
    assert change(world(), "AUSDT", day) == pytest.approx(0.10), "the 30-day mean now less the 30-day mean thirty days ago"
    assert names["top_change_30"][1](world(), "AUSDT", day) == pytest.approx(0.0)
    assert names["taker_change_30"][1](world(), "AUSDT", day) is None, "no level, no change"
    assert change(world(), "BUSDT", day) is None, "a level missing at either end leaves no change"
    assert change(world(later=0.3), "AUSDT", day) == change(world(), "AUSDT", day), "what comes after the day changes nothing"
    assert "divergence_change_30" not in names and "oi_growth_change_30" not in names


def test_breadth_is_the_share_of_the_top_tier_up_over_the_week_split_at_its_own_earlier_median():
    start = date(2021, 1, 4)                                      # a Monday; decisions are the Sundays
    days = [start + timedelta(days=k) for k in range(60 * 7)]
    coins = [f"C{i:02d}USDT" for i in range(10)]

    def world(later=1.0):
        """Each week ``3 + (week % 5)`` of the ten coins rise and the rest fall; ``later`` scales moves after week 50."""
        bars = {s: {} for s in coins + ["BTCUSDT"]}
        price = dict.fromkeys(bars, 100.0)
        for k, day in enumerate(days):
            week = k // 7
            for i, s in enumerate(coins):
                move = (0.01 if i < 3 + week % 5 else -0.01) * (later if week > 50 else 1.0)
                bars[s][day] = xsb.Bar(open=price[s], close=price[s] * (1 + move), quote_volume=1e8)
                price[s] *= 1 + move
            bars["BTCUSDT"][day] = xsb.Bar(open=100.0, close=100.0, quote_volume=1e9)
        return xsb.Market(bars=bars, funding={s: [] for s in bars})

    span = (start, days[-1])
    monthly = {date(d.year, d.month, 1): coins for d in days}
    sundays = [d for d in days if d.weekday() == 6]
    labels = lab.scenario_labels(world(), monthly, sundays, span)
    late = [d for d in sundays if (d - start).days // 7 >= 40][:10]
    seen = {((d - start).days // 7) % 5: labels[d].get("breadth") for d in late}
    assert seen == {0: "thin", 1: "thin", 2: "thin", 3: "broad", 4: "broad"}, \
        "three, four or five of ten up is at or under the earlier weeks' median of five; six or seven is over it"
    assert "breadth" not in labels[sundays[2]], "no history to compare with yet"
    assert lab.scenario_keys({"breadth": "broad"}) == ["all", "breadth=broad"]
    twisted = lab.scenario_labels(world(later=-1.0), monthly, sundays, span)
    early = [d for d in sundays if (d - start).days // 7 <= 50]
    assert all(twisted[d].get("breadth") == labels[d].get("breadth") for d in early), "later weeks change no earlier label"
    assert any(twisted[d].get("breadth") != labels[d].get("breadth") for d in sundays if d not in early), "the twist is seen later"


def test_the_candidate_axes_are_labelled_from_the_past_held_to_stricter_levels_and_never_mapped():
    assert not set(lab.CANDIDATE_AXES) & set(lab.AXES)
    assert lab.AXIS_ORDER == ("breadth",) + lab.CANDIDATE_AXES + lab.FINER_AXES + ("funding_spread",)
    assert lab.scenario_keys({"breadth": "broad", "dominance": "btc_led", "month": "start", "funding_spread": "apart"}) == ["all", "breadth=broad"], \
        "an axis under check adds no cell to the map"
    levels = [lab.axis_level(axis) for axis in lab.AXIS_ORDER]
    assert levels == [0.05 / k for k in range(1, 15)] and lab.axis_level("trend") is None
    assert 1 / (lab.PLACEBO_AXES + 1) < levels[6], "200 placebos can resolve the strictest level of the first seven"
    assert 1 / (lab.FINER_PLACEBOS + 1) < levels[-1] < 1 / (lab.PLACEBO_AXES + 1), "the finer six need more than 200"
    start = date(2021, 1, 4)                                      # a Monday
    days = [start + timedelta(days=k) for k in range(72 * 7)]
    coins = [f"C{i:02d}USDT" for i in range(10)]

    def world(later=1.0, after=60):
        """Six-week blocks. Even blocks: BTC climbs, the coins sink together on one shared move, on double volume.
        Odd blocks: BTC falls, each coin moves on its own. Busy weeks get a little busier and quiet ones a little
        quieter as time goes, so no two weeks tie at the median.
        ``later`` scales every move after week ``after``."""
        bars = {s: {} for s in coins + ["BTCUSDT"]}
        price = dict.fromkeys(bars, 100.0)
        for k, day in enumerate(days):
            week = k // 7
            calm = (week // 6) % 2 == 0
            scale = later if week > after else 1.0
            shared = (_unit("shared", day) - 0.6) * 0.02
            for i, s in enumerate(coins):
                move = (shared if calm else (_unit(s, day) - 0.45) * 0.02) * scale
                bars[s][day] = xsb.Bar(open=price[s], close=price[s] * (1 + move),
                                       quote_volume=2e8 * (1 + week / 1000) if calm else 1e8 * (1 - week / 1000))
                price[s] *= 1 + move
            move = (0.004 if calm else -0.004) * scale
            bars["BTCUSDT"][day] = xsb.Bar(open=price["BTCUSDT"], close=price["BTCUSDT"] * (1 + move), quote_volume=1e9)
            price["BTCUSDT"] *= 1 + move
        return xsb.Market(bars=bars, funding={s: [] for s in bars})

    span = (start, days[-1])
    monthly = {date(d.year, d.month, 1): ["BTCUSDT"] + coins for d in days}
    sundays = [d for d in days if d.weekday() == 6]
    labels = lab.scenario_labels(world(), monthly, sundays, span)
    last_of = lambda block: sundays[block * 6 + 5]                # noqa: E731 - the block's last decision
    climbing, falling = labels[last_of(8)], labels[last_of(9)]
    assert {k: climbing[k] for k in ("dominance", "volume", "correlation", "drawdown")} == {
        "dominance": "btc_led", "volume": "busy", "correlation": "tight", "drawdown": "shallow"}
    assert {k: falling[k] for k in ("dominance", "volume", "correlation", "drawdown")} == {
        "dominance": "alt_led", "volume": "quiet", "correlation": "loose", "drawdown": "deep"}
    assert all(labels[d]["month"] == ("start" if d.day <= 7 else "rest") for d in sundays)
    assert "dominance" not in labels[sundays[3]], "no history to compare with yet"
    twisted = lab.scenario_labels(world(later=-3.0), monthly, sundays, span)
    early = [d for d in sundays if (d - start).days // 7 <= 60]
    assert all(twisted[d] == labels[d] for d in early), "later weeks change no earlier label"
    assert any(twisted[d] != labels[d] for d in sundays if d not in early), "the twist is seen later"
    # BTC is at its high at the end of the climb in weeks 60-65; then, instead of falling, it soars.
    soaring = lab.scenario_labels(world(later=-6.0, after=65), monthly, sundays, span)
    before = [d for d in sundays if (d - start).days // 7 <= 65]
    assert labels[before[-1]]["drawdown"] == "shallow"
    assert all(soaring[d] == labels[d] for d in before), "a higher high after the day deepens no earlier drawdown"


def test_leverage_is_open_interest_in_quote_terms_over_volume_and_has_no_label_without_positioning():
    start = date(2021, 1, 4)
    days = [start + timedelta(days=k) for k in range(60 * 7)]
    coins = [f"C{i:02d}USDT" for i in range(10)]

    def world(later=1.0, priced=100.0):
        """Ten coins at a flat price. Open interest, in contracts, is high in even six-week blocks and low in odd
        ones (a little more extreme as time goes, so nothing ties); ``later`` scales it after week 50."""
        bars = {s: {d: xsb.Bar(open=priced, close=priced, quote_volume=1e8) for d in days} for s in coins + ["BTCUSDT"]}
        market = xsb.Market(bars=bars, funding={s: [] for s in bars})
        market.metrics = {}
        for s in coins:
            market.metrics[s] = {}
            for k, d in enumerate(days):
                week = k // 7
                level = 2e6 * (1 + week / 1000) if (week // 6) % 2 == 0 else 1e6 * (1 - week / 1000)
                market.metrics[s][d] = {"oi": level * (later if week > 50 else 1.0), "ls": 0.5, "top": None, "taker": None}
        return market

    span = (start, days[-1])
    monthly = {date(d.year, d.month, 1): ["BTCUSDT"] + coins for d in days}
    sundays = [d for d in days if d.weekday() == 6]
    labels = lab.scenario_labels(world(), monthly, sundays, span)
    assert labels[sundays[8 * 6 + 5]]["leverage"] == "levered" and labels[sundays[7 * 6 + 5]]["leverage"] == "light"
    assert "leverage" not in labels[sundays[3]], "no history to compare with yet"
    bare = world()
    del bare.metrics
    assert all("leverage" not in row for row in lab.scenario_labels(bare, monthly, sundays, span).values()), \
        "without the positioning values there is no label"
    twisted = lab.scenario_labels(world(later=0.01), monthly, sundays, span)
    early = [d for d in sundays if (d - start).days // 7 <= 50]
    assert all(twisted[d].get("leverage") == labels[d].get("leverage") for d in early), "later weeks change no earlier label"
    assert any(twisted[d].get("leverage") != labels[d].get("leverage") for d in sundays if d not in early)
    # contracts times the close: the same contracts on a coin priced a hundredth as much are a hundredth of the leverage
    series = lambda market: lab.scenario_labels(market, monthly, sundays, span)        # noqa: E731
    assert series(world(priced=1.0)) == series(world()), "one price for every week moves every week alike: same labels"
    cheap_late = world()
    for s in coins:
        for d in days[50 * 7:]:
            cheap_late.bars[s][d] = xsb.Bar(open=1.0, close=1.0, quote_volume=1e8)
    late = [d for d in sundays if (d - start).days // 7 >= 52]
    assert all(series(cheap_late)[d]["leverage"] == "light" for d in late), \
        "the same contracts at a hundredth of the price are little open interest: the close is in the measure"


def test_the_registry_stays_inside_what_its_controls_and_the_sweeps_null_can_correct_for():
    from scripts.research import strategy_ladder as sl
    assert len(lab.variables()) == 49
    assert len(lab.controls()) == 94 >= 2 * len(lab.variables()) - 4, "the random variables grow with the real ones"
    assert 1 / (sl.SWEEP_NULLS + 1) < 0.10 / len(lab.variables()), "the sweep's null can correct for every variable"
    assert not 1 / (sl.SWEEP_NULLS_ROUND_6 + 1) < 0.10 / len(lab.variables()), "300 null books could not: thirty was their most"
    assert 1 / (sl.SWEEP_NULLS + 1) < 0.10 / 50 and not 1 / (sl.SWEEP_NULLS + 1) < 0.10 / 51, "500 reach fifty variables"


def test_a_position_is_the_level_against_the_coins_own_six_earlier_lookbacks_and_asks_for_no_later_day():
    asked = []
    series = {0: 5.0, 30: 1.0, 60: 2.0, 90: 3.0, 120: 2.0, 150: 1.0, 180: 3.0}
    day = date(2023, 6, 4)

    def level(market, symbol, on):
        asked.append(on)
        return series.get((day - on).days, 99.0 if on > day else None)

    value = lab._position(level)(None, "AUSDT", day)
    assert value == pytest.approx((5.0 - 2.0) / (4 / 6) ** 0.5), "now less the six earlier values' mean, over their spread"
    assert max(asked) == day and sorted(day - d for d in asked) == [timedelta(days=30 * k) for k in range(7)], \
        "the day itself and the six lookbacks before it: nothing later"
    series.pop(30), series.pop(60), series.pop(90)
    assert lab._position(level)(None, "AUSDT", day) is None, "three earlier values are too few"
    flat = lab._position(lambda market, symbol, on: 1.0)
    assert flat(None, "AUSDT", day) is None, "a level that never moved has no position"
    names = lab.variables()
    assert set(lab.POSITION_OF) == {"momentum_position", "funding_position", "ls_position", "top_position", "taker_position",
                                    "spot_share_position", "taker_buy_share_position", "trade_size_position"}
    market = _market(date(2021, 6, 30))
    decision = date(2021, 3, 7)
    for name, base in lab.POSITION_OF.items():
        group, position = names[f"{name}_30"]
        assert group == names[f"{base}_30"][0], name
        assert position(market, SYMBOLS[1], decision) == lab._position(names[f"{base}_30"][1])(market, SYMBOLS[1], decision), name
    twisted = _market(date(2021, 6, 30), twist_after=decision)
    for name in ("momentum_position_30", "funding_position_30", "residual_momentum_30"):
        assert names[name][1](market, SYMBOLS[1], decision) is not None
        assert names[name][1](twisted, SYMBOLS[1], decision) == names[name][1](market, SYMBOLS[1], decision), name


def test_residual_momentum_takes_the_coins_own_beta_times_the_reference_coins_month_off_its_month():
    names = lab.variables()
    market, day = _market(date(2021, 6, 30)), date(2021, 3, 7)
    coin = SYMBOLS[2]
    own, reference = xsb.trailing_return(market, coin, day, 30), xsb.trailing_return(market, "BTCUSDT", day, 30)
    beta = lab.bs._beta(market, coin, day, 30)
    assert names["residual_momentum_30"] == ("price_volume", lab._residual_momentum)
    assert names["residual_momentum_30"][1](market, coin, day) == pytest.approx(own - beta * reference)
    assert names["residual_momentum_30"][1](market, coin, day) != pytest.approx(names["momentum_30"][1](market, coin, day))
    ranks = lab._ranks([1.0, 3.0, 2.0])
    assert ranks == lab._ranks([1.0 - 7, 3.0 - 7, 2.0 - 7]), "one number off every coin moves no rank: only a per-coin loading can"


def test_every_registered_change_is_its_own_level_now_less_that_level_a_lookback_earlier():
    names = lab.variables()
    assert set(lab.CHANGE_OF) == {"ls_change", "top_change", "taker_change", "momentum_change", "funding_change",
                                  "spot_share_change", "taker_buy_share_change"}
    assert "illiquidity_change_30" not in names, "struck: its change is the change of volume and of the size of moves"
    market = _market(date(2021, 6, 30))
    day = date(2021, 3, 7)
    for name, base in lab.CHANGE_OF.items():
        group, change = names[f"{name}_30"]
        base_group, level = names[f"{base}_30"]
        assert group == base_group, name
        now, before = level(market, SYMBOLS[1], day), level(market, SYMBOLS[1], day - timedelta(days=30))
        expected = None if now is None or before is None else now - before
        assert change(market, SYMBOLS[1], day) == expected, name
    assert names["momentum_change_30"][1](market, SYMBOLS[1], day) is not None and names["funding_change_30"][1](market, SYMBOLS[1], day) is not None
    twisted = _market(date(2021, 6, 30), twist_after=day)
    for name in ("momentum_change_30", "funding_change_30"):
        assert names[name][1](twisted, SYMBOLS[1], day) == names[name][1](market, SYMBOLS[1], day), "nothing after the day is read"


def test_a_run_of_the_spot_group_loads_the_spot_bars_alone(monkeypatch, tmp_path):
    asked = []
    span_end = lab.SPAN[1]
    bar = xsb.Bar(open=1.0, close=1.0, quote_volume=1.0)

    def hold(cache, span=None, *, attach=True, flow=False):
        asked.append((attach, flow))
        return xsb.Market(bars={"AUSDT": {span_end: bar}})

    def never(*args, **kwargs):
        raise AssertionError("positioning or depth was loaded")

    monkeypatch.setattr(lab, "hold", hold)
    monkeypatch.setattr(lab.ms, "load_daily", never)
    monkeypatch.setattr(lab.ds, "load_daily", never)
    assert lab.hold_spot(tmp_path) is None, "no spot listing on file"
    folder = tmp_path / lab.sp.SPOT_DIR
    folder.mkdir()
    (folder / lab.ba.SYMBOLS_FILENAME).write_text(json.dumps(["AUSDT"]), encoding="utf-8")
    (folder / lab.sp.KLINES).mkdir()
    late = span_end + timedelta(days=2)
    monkeypatch.setattr(lab.xsb, "load_market", lambda path, funding=True: xsb.Market(bars={"AUSDT": {span_end: bar, late: bar}}))
    held = lab.hold_spot(tmp_path)
    assert asked == [(False, False), (False, False)]
    assert set(held.spot["AUSDT"]) == {span_end} and held.spot_of["AUSDT"] == ("AUSDT", 1.0), "cut at the span's end"
    assert not hasattr(held, "metrics") and not hasattr(held, "depth")
    seen = []
    monkeypatch.setattr(lab.xsb, "_in_sandbox", lambda: True)
    monkeypatch.setattr(lab, "hold_spot", lambda cache: seen.append("spot"))
    assert lab.main(["run", "--only", "spot"]) == lab.EXIT_BLOCKED and seen == ["spot"], "the spot group goes through it"


def test_the_by_variable_check_has_enough_placebos_for_a_real_effect_to_pass_the_correction():
    """The smallest p a test can show is one over the placebos plus one. It has to be under what Benjamini–Hochberg
    asks of the best of a few hundred tests, or nothing could ever pass (the first run, 2026-11-13: 200 placebos)."""
    tests, q = len(lab.variables()) * len(lab.TIERS) * len(lab.AXES), 0.10
    assert tests == 49 * 2 * 6
    assert 1 / (lab.PLACEBO_AXES_BY_VARIABLE + 1) < q / tests
    assert lab.PLACEBO_AXES_BY_VARIABLE == 6000 and 1 / (6000 + 1) < q / (50 * 2 * 6), "enough for fifty variables"
    assert not 1 / (5000 + 1) < q / tests, "the earlier 5000 no longer were"
    assert not 1 / (200 + 1) < q / tests


# --- D7: the universe's character by month ----------------------------------------------------------------------------

def test_the_universe_character_is_read_by_month_from_the_past_and_computes_no_return(tiers):
    span = (date(2020, 1, 1), date(2021, 6, 30))
    full = lab.cut_market(_market(span[1]), span)
    out = lab.universe_character(full, span)
    assert out["returns_computed"] is False and set(out["years"]) == {"2020", "2021"}
    month = out["months"]["2021-03"]
    assert set(month) == {"funding_abs_median_bps_week", "funding_abs_p90_bps_week", "wild_share",
                          "listing_age_median_days", "members", "overlap_with_previous_month"}
    assert month["members"] == 10 and 0.7 <= month["overlap_with_previous_month"] <= 1.0, "ten of fourteen coins, by volume"
    assert month["wild_share"] == 0.0 and month["funding_abs_p90_bps_week"] >= month["funding_abs_median_bps_week"] > 0
    assert 600 < month["listing_age_median_days"] < 680, "listed 2019-06-01"
    # Later bars, whatever they hold, change no earlier month.
    twisted = lab.universe_character(lab.cut_market(_market(date(2021, 12, 31), twist_after=span[1]), span), span)
    assert twisted == out
    flat = json.dumps(out)
    assert all(word not in flat for word in ("sharpe", "pnl", "net")), "character only"


def test_the_universe_command_stops_before_the_reserved_days(monkeypatch, capsys):
    asked = {}

    def hold(cache, span=None, *, attach=True, flow=False):
        asked.update(span=span, attach=attach)
        return None

    monkeypatch.setattr(lab.xsb, "_in_sandbox", lambda: True)
    monkeypatch.setattr(lab, "hold", hold)
    assert lab.main(["universe"]) == lab.EXIT_BLOCKED
    assert asked == {"span": (lab.SPAN[0], date(2026, 9, 30)), "attach": False}
    assert lab.CHARACTER_END < lab.bs.RESERVED_FROM
    monkeypatch.setattr(lab.xsb, "_in_sandbox", lambda: False)
    assert lab.main(["universe"]) == lab.EXIT_USAGE


def test_a_cell_on_a_resting_axis_is_shown_and_is_not_a_lead():
    """The five axes rest since 2026-11-13: two checks, none beat its placebos. Their cells stay on the map."""
    assert lab.RESTING_AXES == {"trend", "vol", "dispersion", "heat", "universe", "breadth"}, \
        "the first five rest on their checks; breadth is born at rest until it has had one"
    assert set(lab.AXES) == lab.RESTING_AXES
    against = {"7": {"largest_abs_t": 3.0, "share_abs_t_over_2": 0.05}, "28": {"largest_abs_t": None, "share_abs_t_over_2": None}}
    cells = [_cell("steady", "all", 3.5), _cell("steady", "trend=bull", 4.4), _cell("steady", "trend=bull|vol=low", 5.0)]
    real = {"cells": cells, "variables": {"steady|top30": {"rank_persistence": 0.9}}}
    out = lab.candidates(real, against)
    assert [(c["variable"], c["scenario"]) for c in out["leads"]] == [("steady", "all")] and out["met_the_rule"] == 1
    assert out["resting_axes"] == sorted(lab.RESTING_AXES)


def test_a_finer_label_cuts_an_axis_at_its_own_thirds_and_agrees_with_the_coarse_one(tiers):
    history = [float(k) for k in range(30)]
    cut = lambda value: lab._third_split(value, history, "high", "low")           # noqa: E731
    assert [cut(v) for v in (25.0, 20.0, 19.0, 15.0, 10.0, 9.0, 5.0)] == ["high", "high", "mid", "mid", "mid", "low", "low"]
    assert cut(None) is None and lab._third_split(5.0, history[:lab.REFERENCE_MIN - 1], "high", "low") is None
    market = lab.cut_market(_market(SPAN[1]), SPAN)
    days = lab.decisions(SPAN, 7)
    labels = lab.scenario_labels(market, lab.tier_members(market, SPAN)["top30"], days, SPAN)
    pairs = {"vol": ("high", "low"), "dispersion": ("wide", "narrow"), "heat": ("hot", "cool"),
             "universe": ("wild", "calm"), "breadth": ("broad", "thin")}
    seen = set()
    for row in labels.values():
        for axis, (high, low) in pairs.items():
            fine = row.get(f"{axis}_fine")
            assert (fine is None) == (axis not in row), "a finer label exists exactly where the coarse one does"
            assert fine in (None, high, low, "mid")
            if fine in (high, low):
                assert row[axis] == fine, "the upper third is above the median and the lower third is not"
            seen.add((axis, fine))
    assert {("vol", "mid"), ("vol", "high"), ("vol", "low")} <= seen
    assert lab.scenario_keys({"vol": "high", "vol_fine": "mid", "trend": "bull", "trend_fine": "bull_early"}) == [
        "all", "trend=bull", "vol=high", "trend=bull|vol=high"], "a finer axis adds no cell to the map"


def test_a_trend_spell_is_early_for_its_first_four_decisions_and_never_looks_ahead(tiers, monkeypatch):
    market = lab.cut_market(_market(SPAN[1]), SPAN)
    members = lab.tier_members(market, SPAN)["top30"]
    days = lab.decisions(SPAN, 7)

    def trend_of(blocks):
        """``blocks``: (state, weeks) in order from the first decision; every day of a week carries its state."""
        states = [state for state, weeks in blocks for _ in range(weeks)]
        by_day = {}
        for k, day in enumerate(days):
            if k < len(states) and states[k] is not None:
                by_day.update({day - timedelta(days=back): states[k] for back in range(7)})
        return lambda data, end: {"labels": by_day, "lagged_days": 0}

    blocks = [(None, 2), ("BULL", 6), ("BEAR", 3), ("BULL", 5), ("SIDEWAYS", 9)]
    monkeypatch.setattr(lab.rg, "regime_labels", trend_of(blocks))
    labels = lab.scenario_labels(market, members, days, SPAN)
    fine = [labels[d].get("trend_fine") for d in days[:25]]
    assert fine == ([None] * 2 + ["bull_early"] * 4 + ["bull_late"] * 2 + ["bear_early"] * 3
                    + ["bull_early"] * 4 + ["bull_late"] + ["sideways_early"] * 4 + ["sideways_late"] * 5)
    assert all(("trend_fine" in labels[d]) == ("trend" in labels[d]) for d in days)
    # The same first sixteen weeks, then the bull run ends at once instead of lasting five: nothing earlier moves.
    monkeypatch.setattr(lab.rg, "regime_labels", trend_of(blocks[:3] + [("BULL", 1), ("BEAR", 13)]))
    other = lab.scenario_labels(market, members, days, SPAN)
    assert [other[d].get("trend_fine") for d in days[:12]] == fine[:12], "a spell's later length marks no earlier week"
    assert [other[d].get("trend_fine") for d in days[11:18]] == ["bull_early"] + ["bear_early"] * 4 + ["bear_late"] * 2


def test_a_state_is_cut_finer_only_where_every_part_is_thick_enough():
    def run(*blocks):
        return [label for label, n in blocks for _ in range(n)]

    # Six bull spells of 4 early and 5 late weeks; bear comes twice, so its early part has two spells.
    trend = run(*([("bull_early", 4), ("bull_late", 5), ("sideways_early", 4), ("sideways_late", 3)] * 6),
                ("bear_early", 4), ("bear_late", 30), ("bull_early", 4), ("bear_early", 4), ("bear_late", 30), (None, 3))
    kept, gate = lab.finer_gate("trend_fine", trend)
    assert gate["parts"]["bull_early"] == {"decisions": 28, "spells": 7} and gate["parts"]["bear_early"] == {"decisions": 8, "spells": 2}
    assert gate["parts"]["sideways_late"] == {"decisions": 18, "spells": 6}
    assert gate["merged"] == ["bear", "sideways"] and gate["split"] == ["bull"]
    assert set(kept) == {"bull_early", "bull_late", "sideways", "bear", None} and kept.count(None) == 3
    assert [k is None for k in kept] == [label is None for label in trend], "the gate moves no label and names none"
    thick = run(*([("high", 3), ("mid", 2), ("low", 3), ("mid", 2)] * 8))
    kept, gate = lab.finer_gate("vol_fine", thick)
    assert kept == thick and gate["split"] == ["vol"] and gate["merged"] == []
    thin = run(("high", 30), ("mid", 8), ("low", 30), ("mid", 8), ("high", 30), ("mid", 8), ("low", 30), ("high", 30), ("low", 30))
    kept, gate = lab.finer_gate("vol_fine", thin)
    assert gate["parts"]["mid"] == {"decisions": 24, "spells": 3}, "enough decisions, too few spells"
    assert gate["split"] == [] and gate["merged"] == ["vol"] and set(kept) == {"vol"}


def test_an_axis_the_gate_leaves_uncut_is_not_checked(tiers, monkeypatch):
    monkeypatch.setattr(lab, "PLACEBO_AXES", 3)
    monkeypatch.setattr(lab, "FINER_PLACEBOS", 3)
    days = lab.decisions(SPAN, 7)                                # six weeks up, six weeks down: both states are thick
    trend = {day - timedelta(days=back): ("BULL" if (k // 6) % 2 == 0 else "BEAR") for k, day in enumerate(days) for back in range(7)}
    monkeypatch.setattr(lab.rg, "regime_labels", lambda data, end: {"labels": trend, "lagged_days": 0})
    before = lab.axis_check(lab.cut_market(_market(SPAN[1]), SPAN), NAMES, SPAN)["axes"]
    assert before["trend"]["separation"] is not None and before["trend_fine"]["merged"] == ["bear", "bull"], \
        "each late part is two weeks a spell: under twenty decisions, so no trend state is cut"
    assert before["trend_fine"]["states"] == before["trend"]["states"], "what the gate left is the coarse axis"
    monkeypatch.setattr(lab, "MIN_SPELLS", 10_000)
    out = lab.axis_check(lab.cut_market(_market(SPAN[1]), SPAN), NAMES, SPAN)["axes"]
    for axis in lab.FINER_AXES:
        assert out[axis]["split"] == [] and out[axis]["separation"] is None and out[axis]["placebos"] == 0, axis
        assert out[axis]["beats_its_placebos"] is False and out[axis]["p"] is None
    assert all(out[axis] == before[axis] for axis in out if axis not in lab.FINER_OF), "the gate is the finer axes' only"


def _flat_bar(close: float, up: float, down: float) -> xsb.Bar:
    return xsb.Bar(open=close, close=close, quote_volume=1.0, high=close * math.exp(up), low=close * math.exp(-down))


def test_the_estimated_spread_is_how_far_the_close_sits_from_the_middle_of_two_days_ranges():
    day = date(2021, 3, 31)
    days = [day - timedelta(days=k) for k in range(40)]
    market = lambda bar_of: xsb.Market(bars={"AUSDT": {d: bar_of(d) for d in days}})           # noqa: E731
    # Every day closes 0.01 under the middle of its range (0.01 up, 0.03 down): the estimate is twice that.
    assert lab._spread(market(lambda d: _flat_bar(100.0, 0.01, 0.03)), "AUSDT", day) == pytest.approx(0.02)
    assert lab._spread(market(lambda d: _flat_bar(100.0, 0.02, 0.02)), "AUSDT", day) == pytest.approx(0.0)
    # A close above one day's middle and below the next day's: the product is negative and the estimate is zero.
    swing = market(lambda d: _flat_bar(100.0, 0.01, 0.03) if d.toordinal() % 2 else _flat_bar(100.0, 0.03, 0.01))
    assert lab._spread(swing, "AUSDT", day) == 0.0
    later = market(lambda d: _flat_bar(100.0, 0.01, 0.03))
    later.bars["AUSDT"][day + timedelta(days=1)] = _flat_bar(100.0, 0.5, 0.0)
    assert lab._spread(later, "AUSDT", day) == pytest.approx(0.02), "the day after is not read"
    later.bars["AUSDT"][day] = _flat_bar(100.0, 0.5, 0.0)
    assert lab._spread(later, "AUSDT", day) != pytest.approx(0.02), "the day itself is"
    short = xsb.Market(bars={"AUSDT": {d: _flat_bar(100.0, 0.01, 0.03) for d in days[:20]}})
    assert lab._spread(short, "AUSDT", day) is None and lab._spread(short, "BUSDT", day) is None


def test_the_premium_is_read_to_the_spans_last_day_and_no_further(tmp_path):
    last = date(2021, 3, 31)
    folder = tmp_path / "binance" / "derivative" / "premium"
    folder.mkdir(parents=True)
    stamp = lambda d: int((d - date(1970, 1, 1)).total_seconds() * 1000)                         # noqa: E731
    rows = [json.dumps([stamp(last - timedelta(days=k)), "0", "0", "0", str(0.001 * (k + 1))]) for k in range(-5, 30)]
    (folder / "AUSDT.1d.jsonl").write_text("\n".join(rows + ["{torn", json.dumps([stamp(last), "x"])]) + "\n")
    held = lab.read_premium(tmp_path, ["AUSDT", "BUSDT"], last)
    assert set(held) == {"AUSDT"} and max(held["AUSDT"]) == last and len(held["AUSDT"]) == 30, "five later days are dropped"
    market = xsb.Market(bars={})
    assert lab._premium(market, "AUSDT", last) is None, "no premium attached"
    market.premium = held
    assert lab._premium(market, "AUSDT", last) == pytest.approx(0.001 * sum(range(1, 31)) / 30)
    assert lab._premium(market, "AUSDT", last + timedelta(days=11)) is None, "nineteen days in the window: too few"
    assert lab._premium(market, "AUSDT", last + timedelta(days=10)) == pytest.approx(0.001 * sum(range(1, 21)) / 20)


def test_a_candidate_that_orders_coins_as_its_kin_does_is_not_mapped_and_no_return_is_read(tiers, monkeypatch):
    market = lab.cut_market(_market(SPAN[1]), SPAN)
    vol, noise = lab.variables()["vol_30"][1], list(lab.controls().values())[0][1]
    monkeypatch.setattr(lab, "OVERLAP_CANDIDATES", {
        "copy": ("price_volume", vol, ("momentum_30", "vol_30")),
        "mirror": ("price_volume", lambda m, s, d: None if (v := vol(m, s, d)) is None else -v, ("vol_30",)),
        "other": ("price_volume", noise, ("vol_30", "momentum_30")),
        "blind": ("price_volume", lambda m, s, d: None, ("vol_30",))})

    def no_returns(*args, **kwargs):
        raise AssertionError("the overlap check reads no return")
    monkeypatch.setattr(lab, "forward_return", no_returns)
    copy, mirror = lab.overlap(market, "copy", SPAN), lab.overlap(market, "mirror", SPAN)
    assert copy["nearest_kin"] == "vol_30" and copy["mapped"] is False and copy["compared"] is True
    assert copy["tiers"]["top30"]["kin"]["vol_30"]["median_rank_correlation"] == 1.0
    assert mirror["tiers"]["top30"]["kin"]["vol_30"]["median_rank_correlation"] == -1.0 and mirror["mapped"] is False
    other = lab.overlap(market, "other", SPAN)
    assert other["mapped"] is True and other["threshold"] == 0.8
    assert all(abs(k["median_rank_correlation"]) < 0.8 for k in other["tiers"]["top30"]["kin"].values())
    assert other["tiers"]["top30"]["decisions_ranked"] == other["tiers"]["top30"]["kin"]["vol_30"]["decisions"] > 20
    blind = lab.overlap(market, "blind", SPAN)
    assert blind["compared"] is False and blind["mapped"] is False, "nothing compared is not a pass"
    assert other["tiers"]["top30"]["share_at_zero"] == 0.0 and blind["tiers"]["top30"]["share_at_zero"] is None
    registry = lab.variables()
    assert registry["spread_30"][0] == "price_volume" and registry["premium_30"][0] == "premium", \
        "both stood under the threshold on the real inputs (section 40) and joined the registry"
    monkeypatch.setattr(lab.xsb, "_in_sandbox", lambda: True)
    assert lab.main(["overlap", "--candidate", "nothing"]) == lab.EXIT_USAGE


def test_the_funding_spread_axis_reads_how_far_apart_the_weeks_funding_stands():
    start = date(2021, 1, 4)
    days = [start + timedelta(days=k) for k in range(72 * 7)]
    coins = [f"C{i:02d}USDT" for i in range(10)]
    stamp = lambda d: int((d - date(1970, 1, 1)).total_seconds() * 1000) + 3600 * 1000              # noqa: E731

    def world(later=1.0):
        """Six-week blocks: in even ones every coin pays the same funding, in odd ones they fan out, a little wider
        as time goes. ``later`` scales the fan after week 60."""
        bars = {s: {d: xsb.Bar(open=100.0, close=100.0, quote_volume=1e8, high=101.0, low=99.0) for d in days}
                for s in coins + ["BTCUSDT"]}
        funding = {s: [] for s in bars}
        for k, day in enumerate(days):
            week = k // 7
            fan = 0.0 if (week // 6) % 2 == 0 else 1e-4 * (1 + week / 100) * (later if week > 60 else 1.0)
            for i, s in enumerate(coins):
                funding[s].append((stamp(day), 1e-4 + fan * (i - 4.5)))
            funding["BTCUSDT"].append((stamp(day), 1e-4))
        return xsb.Market(bars=bars, funding=funding)

    span = (start, days[-1])
    monthly = {date(d.year, d.month, 1): ["BTCUSDT"] + coins for d in days}
    sundays = [d for d in days if d.weekday() == 6]
    labels = lab.scenario_labels(world(), monthly, sundays, span)
    last_of = lambda block: sundays[block * 6 + 5]                # noqa: E731 - the block's last decision
    assert labels[last_of(8)]["funding_spread"] == "together" and labels[last_of(9)]["funding_spread"] == "apart"
    assert "funding_spread" not in labels[sundays[3]], "no history to compare with yet"
    shrunk = lab.scenario_labels(world(later=0.0), monthly, sundays, span)     # the fan closes after week 60
    early = [d for d in sundays if (d - start).days // 7 <= 60]
    assert all(shrunk[d] == labels[d] for d in early), "later weeks change no earlier label"
    assert any(shrunk[d].get("funding_spread") != labels[d].get("funding_spread") for d in sundays if d not in early)



def test_a_run_of_the_premium_group_loads_the_premium_cut_at_the_span_and_nothing_else(monkeypatch, tmp_path, capsys):
    span = (date(2020, 1, 1), date(2021, 6, 30))
    monkeypatch.setattr(lab.xsb, "read_contract_types", lambda archive: ({"BTCUSDT": "PERPETUAL"}, None))
    monkeypatch.setattr(lab.xsb, "load_market", lambda archive, funding=True: _market(date(2021, 12, 31)))
    assert lab.hold_premium(tmp_path, span) is None, "no premium file: blocked, not an empty map"
    folder = tmp_path / "binance" / "derivative" / "premium"
    folder.mkdir(parents=True)
    stamp = lambda d: int((d - date(1970, 1, 1)).total_seconds() * 1000)                         # noqa: E731
    days = [date(2021, 6, 1) + timedelta(days=k) for k in range(60)]
    (folder / "C01USDT.1d.jsonl").write_text("".join(json.dumps([stamp(d), 0, 0, 0, 0.001]) + "\n" for d in days))
    held = lab.hold_premium(tmp_path, span)
    assert set(held.premium) == {"C01USDT"} and max(held.premium["C01USDT"]) == span[1] == lab.last_day(held)
    assert lab._premium(held, "C01USDT", span[1]) == pytest.approx(0.001) and not hasattr(held, "metrics")
    called = []
    monkeypatch.setattr(lab, "hold_premium", lambda cache: called.append(cache))
    monkeypatch.setattr(lab, "hold", lambda *a, **k: pytest.fail("the premium group loads through its own door"))
    monkeypatch.setattr(lab.xsb, "_in_sandbox", lambda: True)
    assert lab.main(["run", "--only", "premium", "--cache", str(tmp_path)]) == lab.EXIT_BLOCKED and called == [tmp_path]
    assert "BLOCKED" in capsys.readouterr().err



def test_the_two_late_levels_get_a_change_and_a_position_and_no_shape_gets_one():
    registry = lab.variables()
    assert {n: registry[n][0] for n in ("spread_change_30", "spread_position_30", "premium_change_30", "premium_position_30")} == {
        "spread_change_30": "price_volume", "spread_position_30": "price_volume",
        "premium_change_30": "premium", "premium_position_30": "premium"}
    assert not [n for n in registry if n.count("change") + n.count("position") + n.count("growth") > 1], "no shape of a shape"
    assert not [n for n in registry if n.startswith(("spread_", "premium_")) and not n.endswith("_30")], "thirty-day levels only"
    last = date(2021, 12, 31)
    days = [last - timedelta(days=k) for k in range(300)]
    market = xsb.Market(bars={})
    market.premium = {"AUSDT": {d: 0.001 * ((last - d).days // 30) for d in days}}      # one step down every thirty days
    level = lambda d: lab._premium(market, "AUSDT", d)                                    # noqa: E731
    assert registry["premium_change_30"][1](market, "AUSDT", last) == pytest.approx(level(last) - level(last - timedelta(days=30)))
    assert registry["premium_change_30"][1](market, "AUSDT", last) == pytest.approx(-0.001)
    past = [level(last - timedelta(days=30 * k)) for k in range(1, 7)]
    mean, spread = sum(past) / 6, (sum((v - sum(past) / 6) ** 2 for v in past) / 6) ** 0.5
    assert registry["premium_position_30"][1](market, "AUSDT", last) == pytest.approx((level(last) - mean) / spread)
    market.premium["AUSDT"][last + timedelta(days=1)] = 9.0
    assert registry["premium_change_30"][1](market, "AUSDT", last) == pytest.approx(-0.001), "the day after is not read"
    flat = xsb.Market(bars={"BUSDT": {d: _flat_bar(100.0, 0.02, 0.02) for d in days}})   # a spread of zero every month
    assert registry["spread_change_30"][1](flat, "BUSDT", last) == 0.0
    assert registry["spread_position_30"][1](flat, "BUSDT", last) is None, "no spread in its own history: no position"
