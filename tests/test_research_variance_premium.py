"""The variance premium index proxy (VARIANCE_PREMIUM_V0.1, Thomas 2026-11-10): non-overlapping blocks, the payoff,
which days each side reads, and the rule. Synthetic data only."""

from __future__ import annotations

import json
import math
from datetime import date, timedelta

import pytest

from scripts.research import iv_rv_observe as ivrv
from scripts.research import variance_premium as vp

D0 = date(2022, 1, 1)


def _index(start: date, days: int, step) -> dict[date, float]:
    """Daily closes whose log return on day k is ``step(k)``."""
    out, level = {}, 100.0
    for k in range(days):
        if k:
            level *= math.exp(step(k))
        out[start + timedelta(days=k)] = level
    return out


def _vol(step: float) -> float:
    return 100 * math.sqrt(365 * step * step)


def test_entries_are_thirty_days_apart_and_the_last_horizon_ends_inside_the_span():
    days = vp.entry_days()
    assert days[0] == vp.START == date(2021, 3, 24) and vp.HORIZON_DAYS == 30
    assert all((b - a).days == 30 for a, b in zip(days, days[1:]))
    assert days[-1] + timedelta(days=30) <= vp.END < days[-1] + timedelta(days=60)
    assert len(days) == 67
    assert vp.entry_days(D0, D0 + timedelta(days=59)) == [D0]


def test_the_payoff_is_a_variance_swap_sold_at_the_implied_level():
    assert vp.payoff(60.0, 40.0) == pytest.approx((3600 - 1600) / 120)
    assert vp.payoff(40.0, 60.0) == pytest.approx((1600 - 3600) / 80)
    assert vp.payoff(50.0, 50.0) == 0


def test_implied_is_the_entry_days_close_and_realized_is_the_thirty_days_after_it():
    quiet, wild = 0.01, 0.04
    index = _index(D0 - timedelta(days=40), 111, lambda k: wild if k <= 40 else quiet)   # wild BEFORE the entry
    dvol = {D0: 50.0, D0 + timedelta(days=30): 90.0}
    got = vp.blocks(dvol, index, start=D0, end=D0 + timedelta(days=59))
    assert list(got) == [D0]
    assert got[D0] == pytest.approx(vp.payoff(50.0, _vol(quiet)))        # not the wild vol, not the later DVOL
    assert ivrv.realized_vol(index, D0) == pytest.approx(_vol(quiet))


def test_a_block_without_its_closes_blocks_and_is_never_skipped():
    index = _index(D0, 61, lambda k: 0.01)
    with pytest.raises(vp.DataGap):
        vp.blocks({D0: 50.0}, index, start=D0, end=D0 + timedelta(days=60))             # second entry has no DVOL
    short = {d: v for d, v in index.items() if d != D0 + timedelta(days=17)}
    with pytest.raises(vp.DataGap):
        vp.blocks({D0: 50.0}, short, start=D0, end=D0 + timedelta(days=30))             # one index close missing
    with pytest.raises(vp.DataGap):
        vp.blocks({D0: 0.0}, index, start=D0, end=D0 + timedelta(days=30))


def test_the_portfolio_is_the_mean_of_the_currencies_and_they_must_share_every_block():
    a = {D0: 4.0, D0 + timedelta(days=30): -2.0}
    b = {D0: 2.0, D0 + timedelta(days=30): 6.0}
    assert vp.portfolio({"BTC": a, "ETH": b}) == {D0: 3.0, D0 + timedelta(days=30): 2.0}
    with pytest.raises(vp.DataGap):
        vp.portfolio({"BTC": a, "ETH": {D0: 2.0}})


def _blocks(values):
    return {D0 + timedelta(days=30 * k): v for k, v in enumerate(values)}


@pytest.mark.parametrize("values,passes", [([3.0, 4.0, 2.5, 3.5, 3.0, 4.0], True), ([6.0, -5.0, 6.0, -5.0, 6.0, -5.0], False),
                                           ([4.0, -2.0, 3.0, -2.0, 4.0, -2.0], False)])   # annual SR 0.93: under 1.0
def test_the_rule_is_an_annual_sharpe_of_one_on_thirty_day_blocks(values, passes):
    out = vp.measure({"BTC": _blocks(values), "ETH": _blocks(values)})
    mean = sum(values) / len(values)
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))
    assert out["annual_sharpe"] == pytest.approx(round(mean / sd * math.sqrt(365 / 30), 3))
    assert vp.ENTRY_SHARPE == 1.0 and out["entry_pass"] is passes and (out["annual_sharpe"] >= 1.0) is passes
    assert out["blocks"] == 6 and out["positive_blocks"] == sum(1 for v in values if v > 0)


def test_the_split_and_the_worst_block_are_shown():
    early = {date(2023, 11, 1): 5.0, date(2023, 12, 1): 3.0}
    late = {date(2024, 1, 1): -4.0, date(2024, 2, 1): 1.0}
    out = vp.measure({"BTC": {**early, **late}, "ETH": {**early, **late}})
    assert out["before_split"]["blocks"] == 2 and out["from_split"]["blocks"] == 2
    assert out["worst_block"] == {"entry": "2024-01-01", "vol_points": -4.0}
    assert out["years"] == {"2023": 8.0, "2024": -3.0} and out["max_drawdown_vol_points"] == -4.0


def test_coverage_names_dates_and_counts_and_no_volatility():
    index = _index(vp.START, 200, lambda k: 0.0123)
    dvol = {d: 61.2345 for d in vp.entry_days()[:3]}
    cover = vp.coverage({"BTC": (dvol, index)})
    assert cover["entries"] == 67 and cover["BTC"]["dvol_days"] == 3
    assert cover["BTC"]["entries_without_dvol"][0] == vp.entry_days()[3].isoformat()
    assert len(cover["BTC"]["entries_without_index"]) == 67 - 6          # 200 closes cover six full horizons
    blob = json.dumps(cover)
    assert "61.2345" not in blob and "0.0123" not in blob


def test_it_refuses_outside_the_sandbox(monkeypatch, capsys):
    monkeypatch.setattr(vp.xsb, "_in_sandbox", lambda: False)
    assert vp.main(["load"]) == vp.EXIT_USAGE
    assert "sandbox" in capsys.readouterr().err
