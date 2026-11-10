from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from scripts.research import premium_convergence as pc

D0 = date(2021, 3, 1)
H = pc.HOUR_MS


def _series(premiums: dict[int, float | None], *, days: int = 3, index: float = 100.0,
            mark_high: dict[int, float] | None = None, settlements: dict[int, float] | None = None,
            index_at: dict[int, float] | None = None, drop: set[int] = frozenset(),
            listed: date = date(2020, 1, 1)) -> pc.Series:
    """Hour ``k`` counts from D0's 00:00 open; the bar before it (k = -1) is there too. Premium 0 by default."""
    hours = {}
    for k in range(-1, days * 24):
        if k in drop:
            continue
        idx = (index_at or {}).get(k, index)
        hours[pc._ms(D0) + k * H] = pc.Hour(index=idx, premium=premiums.get(k, 0.0), mark=idx,
                                             mark_high=(mark_high or {}).get(k, idx))
    sett = {pc._ms(D0) + k * H: r for k, r in (settlements or {}).items()}
    return pc.Series(hours=hours, settlements=sett or {pc._ms(D0) - 10 * H: 0.0}, first_archived=listed, name="X")


def _run(series: pc.Series, days: int = 3) -> pc.Leg:
    return pc.simulate_symbol(series, D0, D0 + timedelta(days=days - 1))


def test_threshold_is_the_round_trip_of_both_legs_in_the_cost_model():
    spot = (pc.SPOT_FEE_BPS + pc.cost.DEFAULT_SLIPPAGE_BPS) / 10_000
    perp = (pc.cost.DEFAULT_TAKER_FEE_BPS + pc.cost.DEFAULT_SLIPPAGE_BPS) / 10_000
    assert pc.threshold() == pytest.approx(2 * (spot + perp))
    assert pc.threshold() == pytest.approx(0.0042)


def test_a_premium_above_the_round_trip_opens_and_its_return_to_zero_closes_with_the_convergence():
    p = 0.01
    leg = _run(_series({4: p, 5: p, 6: p}))          # premium closes at 0 again from hour 7 on
    assert (leg.entries, leg.exits) == (1, 1)
    # opened at hour 5's open (close of 4), marked at 5 and 6 (no move), closed at hour 8's open (close of 7, premium 0)
    gain = 1 - 1 / (1 + p)                            # S/S0 − P/P0 with S = P = 100 at the close
    expected = gain - pc._costs(1.0, 1.0) - pc._costs(1.0, 1 / (1 + p))
    assert sum(leg.returns.values()) * pc.CAPITAL == pytest.approx(expected)
    assert leg.held_hours == 3


def test_a_premium_between_zero_and_the_round_trip_keeps_the_position():
    leg = _run(_series({4: 0.01, 5: 0.002, 6: 0.002}))   # below the threshold, still above 0
    assert (leg.entries, leg.exits, leg.held_hours) == (1, 1, 3)


def test_a_premium_at_or_below_the_round_trip_does_not_open():
    leg = _run(_series({4: pc.threshold(), 5: pc.threshold() * 0.99}))
    assert leg.entries == 0
    assert all(v == 0 for v in leg.returns.values())


def test_a_dearer_cost_model_raises_the_threshold_and_the_same_premium_stays_out(monkeypatch):
    s = _series({4: 0.005})
    assert _run(s).entries == 1
    monkeypatch.setattr(pc, "SPOT_FEE_BPS", 20.0)
    assert pc.threshold() > 0.005
    assert _run(s).entries == 0


def test_the_decision_reads_the_previous_close_only():
    leg = _run(_series({30: 0.02}), days=2)           # one spike, in hour 30's close
    assert leg.returns[D0] == 0                       # nothing moves before it
    assert (leg.entries, leg.exits, leg.held_hours) == (1, 1, 1)   # opened at 31's open, closed at 32's
    expected = (1 - 1 / 1.02) - pc._costs(1.0, 1.0) - pc._costs(1.0, 1 / 1.02)
    assert leg.returns[D0 + timedelta(days=1)] * pc.CAPITAL == pytest.approx(expected)


def test_funding_counts_only_while_open_on_both_sides_of_the_settlement():
    p = 0.01
    rates = {5: 0.001, 6: 0.002, 8: 0.004}           # 5 = entry instant, 6 = held, 8 = exit instant
    leg = _run(_series({4: p, 5: p, 6: p}, settlements=rates))
    assert sum(leg.funding.values()) * pc.CAPITAL == pytest.approx(0.002 * 100 / (100 * (1 + p)))


def test_a_missing_premium_hour_neither_opens_nor_closes():
    flat = _run(_series({4: None, 5: 0.0}))
    assert flat.entries == 0
    held = _run(_series({4: 0.01, 5: 0.01, 6: None, 7: 0.01}))   # hour 7's open has no premium to read
    assert held.entries == 1 and held.exits == 1 and held.bridged_hours == 1
    bridged = _run(_series({4: 0.01, 5: None, 6: None, 7: None, 8: 0.0}))
    assert bridged.entries == 1 and bridged.exits == 1


def test_a_held_position_blocks_on_a_long_premium_gap_or_any_missing_index_bar():
    with pytest.raises(pc.DataGap):
        _run(_series({4: 0.01, 5: None, 6: None, 7: None, 8: None}))
    with pytest.raises(pc.DataGap):
        _run(_series({4: 0.01, 5: 0.01}, drop={6}))
    assert _run(_series({}, drop={6})).entries == 0     # flat: a missing bar is just skipped


def test_a_perp_listed_after_the_archive_start_is_not_traded_while_seasoning():
    late = _series({4: 0.05}, listed=D0 - timedelta(days=10))
    assert pc.simulate_symbol(late, D0, D0 + timedelta(days=2)).returns == {}
    assert late.first_day(D0) == D0 - timedelta(days=10) + timedelta(days=pc.SEASONING_DAYS)
    old = _series({4: 0.05}, listed=date(2019, 9, 8))
    assert old.first_day(D0) == D0


def test_liquidation_loses_the_margin_and_sells_spot_at_the_close():
    leg = _run(_series({4: 0.01}, mark_high={5: 100 * pc.LIQUIDATION_RATIO}))   # opened at 5, liquidated in 5
    assert (leg.liquidations, leg.entries, leg.exits) == (1, 1, 0)
    assert sum(leg.returns.values()) * pc.CAPITAL == pytest.approx(-pc._costs(1.0, 1.0) - 1.0 - pc._costs(1.0, 0.0))


def test_a_reset_closes_and_reopens_both_legs():
    idx = {k: 160.0 for k in range(6, 72)}
    leg = _run(_series({k: 0.01 for k in range(4, 72)}, index_at=idx))
    assert leg.resets == 1 and leg.entries == 1


def test_the_portfolio_is_the_mean_over_symbols_trading_that_day_with_idle_days_zero():
    legs = {"A": pc.Leg(returns={D0: 0.02, D0 + timedelta(1): 0.0}), "B": pc.Leg(returns={D0 + timedelta(1): 0.04})}
    assert pc.portfolio(legs) == {D0: 0.02, D0 + timedelta(1): 0.02}


def test_the_entry_rule_passes_a_steady_series_and_fails_a_noisy_one(monkeypatch):
    def fake(series, start, end):
        leg = pc.Leg()
        d = start
        while d <= end:
            leg.returns[d] = series.first_archived.day * 1e-5 * (1 if d.month % 2 else (-3 if series.name == "N" else 0.5))
            d += timedelta(1)
        return leg
    monkeypatch.setattr(pc, "simulate_symbol", fake)
    steady = pc.Series(hours={}, settlements={}, first_archived=date(2020, 1, 1), name="S")
    noisy = pc.Series(hours={}, settlements={}, first_archived=date(2020, 1, 1), name="N")
    good = pc.measure_strategy("PC", {"S": steady}, start=date(2021, 1, 1), end=date(2022, 12, 31))
    bad = pc.measure_strategy("PC", {"N": noisy}, start=date(2021, 1, 1), end=date(2022, 12, 31))
    assert good["entry_pass"] and good["months"] == 24
    assert not bad["entry_pass"]
    assert pc.measure({"S": steady}, start=date(2021, 1, 1), end=date(2022, 12, 31))["overall"] == "ENTRY_PASS"


def test_read_series_keeps_an_hour_whose_premium_bar_is_missing(tmp_path, monkeypatch):
    t0 = pc._ms(D0)
    for kind in ("index", "mark", "premium"):
        path = pc.lake.derivative_path(tmp_path, kind, "XUSDT", "1h")
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [[t0 + k * H, "1", "2", "0.5", "1.5", "0"] for k in range(3) if not (kind == "premium" and k == 1)]
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    monkeypatch.setattr(pc.xsb, "read_funding", lambda folder: [(t0, 0.0001)])
    monkeypatch.setattr(pc.xsb, "read_klines", lambda folder: {date(2020, 1, 1): object()})
    s = pc.read_series(tmp_path, "XUSDT")
    assert s is not None and len(s.hours) == 3
    assert s.hours[t0 + H].premium is None and s.hours[t0].premium == 1.5 and s.hours[t0].mark_high == 2.0


def test_load_output_carries_no_price_or_premium():
    s = _series({4: 0.0123456})
    out = json.dumps({"input_digest": pc.input_digest({"X": s}),
                      "coverage": pc.coverage({"X": s}, start=D0, end=D0 + timedelta(days=2))})
    assert "0.0123456" not in out and "100.0" not in out and "1.0123456" not in out
    cov = json.loads(out)["coverage"]["X"]
    assert cov["missing_premium_count"] == 0 and cov["missing_index_or_mark_count"] == 0


def test_the_input_digest_moves_with_any_bar():
    a = _series({4: 0.01})
    b = _series({4: 0.0100001})
    assert pc.input_digest({"X": a}) != pc.input_digest({"X": b})


def test_it_refuses_outside_the_sandbox(monkeypatch, capsys):
    monkeypatch.setattr(pc.xsb, "_in_sandbox", lambda: False)
    assert pc.main(["load"]) == pc.EXIT_USAGE
    assert "sandbox" in capsys.readouterr().err
