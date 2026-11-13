"""The signal lab (STRATEGY_RESEARCH_REBUILD_V0.1, Thomas 2026-11-13): nothing after the span is read, a return is the
one a book would hold, scenarios look backwards only, a thin cell shows no number, and the rule that turns a cell into
a lead. Synthetic markets only."""

from __future__ import annotations

import hashlib
import json
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


def test_a_lead_must_beat_every_random_variable_keep_its_rank_and_agree_with_its_own_whole():
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
    assert {"price_volume", "funding", "positioning", "depth", "spot", "coin_character", "trade_flow"} == set(listed["variables"].values())


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
    out = lab.axis_check(lab.cut_market(_market(SPAN[1]), SPAN), NAMES, SPAN)
    assert set(out["axes"]) == {"trend", "vol", "dispersion", "heat", "universe"}
    for axis, body in out["axes"].items():
        assert set(body) == {"states", "separation", "placebo_mean", "placebo_p95", "placebos_at_or_above", "placebos"}
        assert body["placebos"] <= 8 and sum(body["states"].values()) == out["decisions"], axis


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
    monkeypatch.setattr(lab, "PLACEBO_AXES", 60)
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
    assert lab.main(["run", "--only", "spot"]) == lab.EXIT_BLOCKED and asked == {"attach": True, "flow": False}

