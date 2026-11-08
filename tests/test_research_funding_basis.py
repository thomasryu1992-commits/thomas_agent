"""The funding-basis measurement (FUNDING_BASIS_V0.1, Thomas 2026-11-08): the carry earns funding and nothing else
when the basis is flat, the price leg cancels, the settlement boundary rule, liquidation, the reset, B-POS's
entry and exit, seasoning, the one-month forward protocol and the gate. Nothing here reads a real cache."""

from __future__ import annotations

import json
import math
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from runtime.mvp_runtime.crypto import cost
from scripts.research import funding_basis as fb

D0 = date(2021, 3, 1)
ENTRY_COST = (fb.SPOT_FEE_BPS + cost.DEFAULT_SLIPPAGE_BPS + cost.DEFAULT_TAKER_FEE_BPS
              + cost.DEFAULT_SLIPPAGE_BPS) / 10_000


def _ms(day: date, hour: int = 0) -> int:
    return int(datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc).timestamp() * 1000)


def _series(n: int, *, index=lambda i: 100.0, premium=lambda i: 0.0, rate=lambda day, hour: 0.0001,
            high=None, first: date | None = None) -> fb.Series:
    """Day ``D0 - 1`` .. ``D0 + n - 1``; settlements every 8 hours; listed long before (seasoned)."""
    days, settlements = {}, []
    for i in range(-1, n):
        day = D0 + timedelta(days=i)
        px = index(i)
        mark = px * (1 + premium(i))
        days[day] = fb.Day(index=px, premium=premium(i), mark=mark, mark_high=high(i) if high else mark)
        for hour in (0, 8, 16):
            settlements.append((_ms(day, hour), rate(day, hour)))
    return fb.Series(days=days, settlements=sorted(settlements), first_archived=first or fb.ARCHIVE_FIRST_DAY,
                     name="TESTUSDT")


def _run(series: fb.Series, n: int, strategy: str = "B-ALL") -> fb.Leg:
    return fb.simulate_symbol(strategy, series, D0, D0 + timedelta(days=n - 1))


def test_a_flat_basis_earns_the_funding_on_twice_the_notional_and_pays_entry_once() -> None:
    leg = _run(_series(10), 10)
    # day 0: the 08:00 and 16:00 settlements (the 00:00 one is the entry instant); later days: all three
    assert leg.returns[D0] == pytest.approx((2 * 0.0001 - ENTRY_COST) / 2)
    for i in range(1, 10):
        assert leg.returns[D0 + timedelta(days=i)] == pytest.approx(3 * 0.0001 / 2)
    assert leg.entries == 1 and leg.exits == 0


def test_the_price_leg_cancels_when_the_premium_is_constant() -> None:
    leg = _run(_series(30, index=lambda i: 100.0 * (1.02 ** max(i, 0)), premium=lambda i: 0.001,
                       rate=lambda d, h: 0.0), 15)
    assert leg.resets == 0                     # 1.02**14 < 1.5
    total = sum(leg.returns.values())
    assert total == pytest.approx(-ENTRY_COST / 2, abs=1e-12)


def test_a_premium_that_narrows_is_profit_for_the_short_perp() -> None:
    leg = _run(_series(3, premium=lambda i: 0.002 if i < 1 else 0.0, rate=lambda d, h: 0.0), 3)
    # entry at the 0.2% premium, closed at 0: V = 1 - 1/1.002 on capital 2
    assert sum(leg.returns.values()) == pytest.approx(((1 - 1 / 1.002) - ENTRY_COST) / 2)


def test_a_settlement_at_the_entry_or_exit_instant_is_not_earned() -> None:
    big = 0.01

    def rate(day: date, hour: int) -> float:
        return big if hour == 0 else 0.0
    # B-POS enters only on a positive trailing mean: give it one by the 08:00 / 16:00 path instead
    leg = _run(_series(3, rate=rate), 3)
    # day 0: entered at 00:00 -> not earned; day 1 and 2: held across 00:00 -> earned
    assert leg.funding[D0] == 0.0
    assert leg.funding[D0 + timedelta(days=1)] == pytest.approx(big / 2)


def test_b_pos_exits_at_the_open_after_three_negative_days_and_skips_that_settlement() -> None:
    def rate(day: date, hour: int) -> float:
        return 0.0002 if day < D0 + timedelta(days=10) else -0.0005
    leg = _run(_series(20, rate=rate), 20, "B-POS")
    assert leg.entries == 1 and leg.exits == 1
    # at D0+11's open the last 3 days hold six +0.0002 and three -0.0005 settlements: mean < 0, exit; the
    # -0.0005 settlement at that open is not earned (the position is not open on both sides of it)
    held = [d for d, f in leg.funding.items() if f != 0.0]
    assert max(held) == D0 + timedelta(days=10)
    assert leg.returns[D0 + timedelta(days=11)] == pytest.approx(-fb._costs(1.0, 1.0) / 2)


def test_b_pos_stays_out_while_the_trailing_rate_is_below_its_entry_threshold() -> None:
    leg = _run(_series(20, rate=lambda d, h: fb.POS_ENTER_RATE / 2), 20, "B-POS")
    assert leg.entries == 0 and all(v == 0.0 for v in leg.returns.values())


def test_a_mark_high_at_the_liquidation_ratio_loses_the_whole_perp_margin() -> None:
    leg = _run(_series(5, rate=lambda d, h: 0.0, high=lambda i: 100.0 * fb.LIQUIDATION_RATIO if i == 2 else 100.0),
               5)
    assert leg.liquidations == 1
    spot_exit = (fb.SPOT_FEE_BPS + cost.DEFAULT_SLIPPAGE_BPS) / 10_000
    assert leg.returns[D0 + timedelta(days=2)] == pytest.approx((-1.0 - spot_exit) / 2)
    # B-ALL re-enters at the next open
    assert leg.entries == 2


def test_a_close_at_the_reset_ratio_pays_a_round_trip_and_re_anchors() -> None:
    leg = _run(_series(4, index=lambda i: 150.0 if i >= 1 else 100.0, rate=lambda d, h: 0.0), 4)
    assert leg.resets == 1
    assert leg.returns[D0 + timedelta(days=1)] == pytest.approx(
        -(fb._costs(1.5, 1.5) + fb._costs(1.0, 1.0)) / 2)


def test_seasoning_counts_ninety_days_from_listing_unless_listed_before_the_archive() -> None:
    late = _series(5, first=date(2021, 1, 1))
    assert late.seasoned_from() == date(2021, 1, 1) + timedelta(days=fb.SEASONING_DAYS)
    assert _series(5).seasoned_from() == fb.ARCHIVE_FIRST_DAY


def test_a_missing_bar_while_holding_blocks() -> None:
    s = _series(5)
    del s.days[D0 + timedelta(days=2)]
    with pytest.raises(fb.DataGap):
        _run(s, 5)


def test_the_forward_protocol_confirms_a_steady_positive_month_and_not_a_noisy_or_flat_one() -> None:
    assert fb.forward_verdict([0.0003] * 30, "s")["confirmed"] is True
    assert fb.forward_verdict([0.0] * 30, "s")["confirmed"] is False
    noisy = [0.02 if i % 2 else -0.0195 for i in range(30)]
    assert fb.forward_verdict(noisy, "s")["confirmed"] is False


def test_the_bootstrap_is_seeded() -> None:
    values = [0.001 * math.sin(i) + 0.0002 for i in range(31)]
    assert fb.block_bootstrap_p(values, "a") == fb.block_bootstrap_p(values, "a")


def test_the_gate_needs_the_share_over_the_whole_span_and_the_recent_window() -> None:
    months = [f"2024-{m:02d}" for m in range(1, 13)]
    all_yes = {m: {"confirmed": True} for m in months}
    assert fb.gate(all_yes, months)["pass"] is True
    four_of_five = {m: {"confirmed": i % 5 != 0} for i, m in enumerate(months)}
    assert fb.gate(four_of_five, months)["share"] == pytest.approx(9 / 12)
    assert fb.gate(four_of_five, months)["pass"] is False


def test_measure_reports_both_strategies_and_months_only_when_complete() -> None:
    s = _series(70)
    report = fb.measure({"TESTUSDT": s}, start=D0, end=D0 + timedelta(days=60))
    assert set(report["strategies"]) == set(fb.STRATEGIES)
    months = report["strategies"]["B-ALL"]["months"]
    assert list(months) == ["2021-03", "2021-04"]          # 2021-05-01 alone is not a complete month
    assert report["strategies"]["B-ALL"]["gate"]["full"]["confirmed"] == 2


def test_read_series_reads_the_cache_layout(tmp_path: Path) -> None:
    from scripts.research import lake
    for kind in ("index", "premium", "mark"):
        path = lake.derivative_path(tmp_path, kind, "TESTUSDT", "1d")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as fh:
            for i in range(3):
                t = _ms(D0 + timedelta(days=i))
                close = {"index": 100.0, "premium": 0.001, "mark": 100.1}[kind]
                fh.write(json.dumps([t, "0", str(close + 1), "0", str(close), "0", t + 86_399_999]) + "\n")
    funding = tmp_path / "archive" / "fundingRate" / "TESTUSDT"
    funding.mkdir(parents=True)
    with zipfile.ZipFile(funding / "TESTUSDT-fundingRate-2021-03.zip", "w") as z:
        z.writestr("f.csv", "calc_time,funding_interval_hours,last_funding_rate\n"
                   f"{_ms(D0) + 6},8,0.0001\n{_ms(D0, 8)},8,0.0002\n")
    klines = tmp_path / "archive" / "klines-1d" / "TESTUSDT"
    klines.mkdir(parents=True)
    with zipfile.ZipFile(klines / "TESTUSDT-1d-2021-03.zip", "w") as z:
        z.writestr("k.csv", f"{_ms(D0)},1,2,0.5,1.5,10,0,20\n")
    s = fb.read_series(tmp_path, "TESTUSDT")
    assert s is not None
    assert s.days[D0] == fb.Day(index=100.0, premium=0.001, mark=100.1, mark_high=101.1)
    assert s.settlements == [(_ms(D0), 0.0001), (_ms(D0, 8), 0.0002)]     # 00:00:00.006 rounds to the hour
    assert s.first_archived == D0
