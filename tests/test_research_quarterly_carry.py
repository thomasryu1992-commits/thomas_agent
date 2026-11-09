"""Quarterly cash-and-carry (QUARTERLY_CARRY_V0.1, Thomas 2026-11-09): the contract calendar, equal-quantity
sizing, the exit at the open of delivery day, the roll, Q-POS's cost threshold, the fail-closed gap and the cache
reader. Nothing here reads a real cache."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.research import cross_section_basket as xsb
from scripts.research import quarterly_carry as q

D1 = date(2021, 3, 26)        # BTCUSDT_210326
D2 = date(2021, 6, 25)        # BTCUSDT_210625


def _days(first: date, last: date):
    day = first
    while day <= last:
        yield day
        day += timedelta(days=1)


def _bar(close: float, high: float | None = None) -> xsb.Bar:
    return xsb.Bar(open=close, close=close, quote_volume=1.0, high=close if high is None else high, low=close)


def _contract(delivery: date, first: date, price, *, skip=(), highs=None) -> q.Contract:
    bars = {d: _bar(price(d), (highs or {}).get(d)) for d in _days(first, delivery - timedelta(days=1))
            if d not in skip}
    return q.Contract(symbol=f"BTCUSDT_{delivery:%y%m%d}", delivery=delivery, bars=bars)


def _base(spot, contracts, first: date, last: date, *, skip=()) -> q.Base:
    return q.Base(name="BTCUSDT", spot={d: _bar(spot(d)) for d in _days(first, last) if d not in skip},
                  contracts=contracts)


def _total(leg: q.Leg) -> float:
    return sum(leg.returns.values()) * q.CAPITAL


def test_the_calendar_is_every_quarters_last_friday() -> None:
    assert q.delivery_of("BTCUSDT_210326") == D1 == q.last_friday(2021, 3)
    assert q.last_friday(2027, 3) == date(2027, 3, 26)
    assert q.quarter_ends(D1, date(2022, 3, 25)) == [D1, D2, date(2021, 9, 24), date(2021, 12, 31),
                                                      date(2022, 3, 25)]
    with pytest.raises(ValueError):
        q.delivery_of("ICPUSDT_SETTLED")


def test_a_missing_quarter_blocks_instead_of_promoting_the_next_contract() -> None:
    q.check_quarters("BTCUSDT", [D1, D2, date(2021, 9, 24)])
    with pytest.raises(q.MissingContract, match="2021-06-25"):
        q.check_quarters("BTCUSDT", [D1, date(2021, 9, 24)])


def test_equal_quantity_earns_the_locked_basis_whatever_the_price_does() -> None:
    first = D1 - timedelta(days=60)
    spot = lambda d: 100.0 + 0.6 * (d - first).days                       # +36% by delivery, under the reset
    basis = lambda d: 2.0 * (D1 - timedelta(days=1) - d).days / 59           # 2.0 at entry, 0 at the last close
    contract = _contract(D1, first, lambda d: spot(d) + basis(d))
    base = _base(spot, [contract], first, D1)
    leg = q.simulate_base("Q-ALL", base, first + timedelta(days=1), D1)
    s0, f0 = spot(first), spot(first) + basis(first)
    s1, f1 = spot(D1 - timedelta(days=1)), spot(D1 - timedelta(days=1))
    expected = (f0 - s0) / s0 - q._costs(1.0, f0 / s0) - q._costs(s1 / s0, f1 / s0)
    assert leg.entries == 1 and leg.deliveries == 1 and leg.liquidations == 0 and leg.resets == 0
    assert _total(leg) == pytest.approx(expected, abs=1e-12)


def test_the_position_closes_at_the_open_of_delivery_day_and_rolls_to_the_next_contract() -> None:
    first = D1 - timedelta(days=30)
    c1 = _contract(D1, first, lambda d: 101.0)
    c2 = _contract(D2, first, lambda d: 102.0)
    base = _base(lambda d: 100.0, [c1, c2], first, D1 + timedelta(days=10))
    leg = q.simulate_base("Q-ALL", base, first + timedelta(days=1), D1 + timedelta(days=10))
    assert leg.entries == 2 and leg.deliveries == 1
    roll = leg.returns[D1] * q.CAPITAL
    assert roll == pytest.approx(-q._costs(1.0, 1.01) - q._costs(1.0, 1.02))   # out of c1, into c2, same open


def test_the_delivery_days_bar_is_never_read(tmp_path: Path) -> None:
    folder = tmp_path / "archive" / "klines-1d" / "BTCUSDT_210326"
    rows = [(d, 101.0, 101.0) for d in _days(D1 - timedelta(days=20), D1 - timedelta(days=1))]
    rows.append((D1, 101.0, 10_000.0))                                       # the partial bar: a liquidating high
    _write_month(folder, "BTCUSDT_210326-1d-2021-03.zip", rows)
    _write_month(tmp_path / "archive-spot" / "klines-1d" / "BTCUSDT", "BTCUSDT-1d-2021-03.zip",
                 [(d, 100.0, 100.0) for d in _days(D1 - timedelta(days=20), D1 + timedelta(days=5))])
    base = q.read_base(tmp_path, "BTCUSDT")
    assert base is not None and max(base.contracts[0].bars) == D1 - timedelta(days=1)
    assert D1 not in base.contracts[0].bars


def test_no_position_opens_with_less_than_a_week_to_delivery_and_no_further_contract_stands_in() -> None:
    late = D1 - timedelta(days=q.MIN_DAYS_TO_DELIVERY - 1)
    c1 = _contract(D1, D1 - timedelta(days=40), lambda d: 101.0)
    c2 = _contract(D2, late - timedelta(days=5), lambda d: 102.0, skip={late - timedelta(days=1)})
    base = _base(lambda d: 100.0, [c1, c2], D1 - timedelta(days=40), D2)
    assert base.front(late) is c2                                            # c1 is too close to delivery
    leg = q.simulate_base("Q-ALL", base, late, late)
    assert leg.entries == 0 and leg.returns == {late: 0.0}                   # c2 has no close at late − 1


def test_q_pos_opens_only_when_the_locked_basis_beats_the_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    round_trip = 2 * q._costs(1.0, 1.0)
    first = D1 - timedelta(days=30)
    thin = _base(lambda d: 100.0, [_contract(D1, first, lambda d: 100.0 * (1 + round_trip * 0.9))], first, D1)
    wide = _base(lambda d: 100.0, [_contract(D1, first, lambda d: 100.0 * (1 + round_trip * 1.1))], first, D1)
    start = first + timedelta(days=1)
    assert q.simulate_base("Q-POS", thin, start, D1).entries == 0
    assert q.simulate_base("Q-ALL", thin, start, D1).entries == 1
    assert q.simulate_base("Q-POS", wide, start, D1).entries == 1
    monkeypatch.setattr(q, "SPOT_FEE_BPS", q.SPOT_FEE_BPS + 20)               # the threshold is the cost model
    assert q.simulate_base("Q-POS", wide, start, D1).entries == 0


def test_q_pos_holds_to_delivery_once_open() -> None:
    first = D1 - timedelta(days=30)
    collapse = first + timedelta(days=10)
    contract = _contract(D1, first, lambda d: 102.0 if d < collapse else 99.0)   # backwardation after entry
    base = _base(lambda d: 100.0, [contract], first, D1)
    leg = q.simulate_base("Q-POS", base, first + timedelta(days=1), D1)
    assert leg.entries == 1 and leg.deliveries == 1


def test_a_missing_bar_while_held_blocks() -> None:
    first = D1 - timedelta(days=30)
    hole = first + timedelta(days=10)
    base = _base(lambda d: 100.0, [_contract(D1, first, lambda d: 101.0, skip={hole})], first, D1)
    with pytest.raises(q.DataGap, match="BTCUSDT_210326"):
        q.simulate_base("Q-ALL", base, first + timedelta(days=1), D1)
    base = _base(lambda d: 100.0, [_contract(D1, first, lambda d: 101.0)], first, D1, skip={hole})
    with pytest.raises(q.DataGap, match="spot"):
        q.simulate_base("Q-ALL", base, first + timedelta(days=1), D1)


def test_liquidation_loses_the_margin_and_sells_the_spot() -> None:
    first = D1 - timedelta(days=30)
    hit = first + timedelta(days=5)
    contract = _contract(D1, first, lambda d: 101.0, highs={hit: 101.0 * q.LIQUIDATION_RATIO})
    base = _base(lambda d: 100.0, [contract], first, D1)
    leg = q.simulate_base("Q-ALL", base, first + timedelta(days=1), hit)
    assert leg.liquidations == 1
    assert leg.returns[hit] * q.CAPITAL == pytest.approx(-q.MARGIN - q._costs(1.0, 0.0))


def test_a_spot_rally_resets_the_position_in_the_same_contract() -> None:
    first = D1 - timedelta(days=30)
    jump = first + timedelta(days=5)
    spot = lambda d: 150.0 if d >= jump else 100.0
    base = _base(spot, [_contract(D1, first, lambda d: spot(d) + 1.0)], first, D1)
    leg = q.simulate_base("Q-ALL", base, first + timedelta(days=1), D1)
    assert leg.resets == 1 and leg.entries == 1 and leg.deliveries == 1


def test_a_steady_carry_passes_the_entry_floor_and_names_the_window() -> None:
    first = date(2021, 1, 1)
    deliveries = q.quarter_ends(D1, date(2022, 3, 25))
    contracts = [_contract(dl, first, lambda d, dl=dl: 100.0 + 2.0 * (dl - d).days / 90 + (0.01 if d.day % 2 else 0))
                 for dl in deliveries]
    report = q.measure({"BTCUSDT": _base(lambda d: 100.0, contracts, first, date(2022, 3, 31))},
                       start=first + timedelta(days=1), end=date(2022, 2, 28))
    assert report["overall"] == "ENTRY_PASS"
    assert report["screen_window"] == ["2027-01-01", "2027-04-30"]
    assert report["strategies"]["Q-ALL"]["entry_pass"] is True


def test_coverage_carries_dates_and_counts_never_a_price() -> None:
    first = D1 - timedelta(days=30)
    base = _base(lambda d: 12345.0, [_contract(D1, first, lambda d: 23456.0)], first, D1)
    text = json.dumps(q.coverage({"BTCUSDT": base}))
    assert "12345" not in text and "23456" not in text
    assert json.loads(text)["BTCUSDT"]["first_eligible_day"] == (first + timedelta(days=1)).isoformat()


def test_it_refuses_outside_the_sandbox() -> None:
    assert q.main(["load"]) != 0


def _write_month(folder: Path, name: str, rows) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    for day, close, high in rows:
        ms = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)
        buf.write(f"{ms},{close},{high},{close},{close},1,{ms + 86_399_999},1,1,1,1,0\n")
    with zipfile.ZipFile(folder / name, "w") as zf:
        zf.writestr(name[:-4] + ".csv", buf.getvalue())
