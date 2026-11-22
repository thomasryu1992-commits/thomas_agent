"""L33, the two-outcome trade book (STRATEGY_DISCOVERY_LOOP_V0.1 sections 130 and 131): a trade's ends on hourly
bars, the hourly reader that stops at the span, the trade table, the book, the null that keeps every signal, the
structure door, the plant read at the book's win rate, and the stages. Synthetic markets only."""

from __future__ import annotations

import json
import random
import zipfile
from datetime import date, timedelta

import pytest

from scripts.research import cross_section_basket as xsb
from scripts.research import two_outcome as tw

START = date(2021, 1, 1)
T = START + timedelta(days=29)             # the signal day; the entry is the first hour of T + 1
H0 = tw.hour_of(T + timedelta(days=1))
QUIET = (100.0, 100.5, 99.5, 100.0)


def _daily(days=40, half=4.0):
    """Flat daily bars at 100 with a true range of 2 x ``half``: the stop sits ``half`` from an entry at 100."""
    return {START + timedelta(days=k): xsb.Bar(open=100.0, close=100.0, quote_volume=1e6, high=100.0 + half, low=100.0 - half)
            for k in range(days)}


def _hours(*rows, tail=800, first=H0):
    return {first + k: row for k, row in enumerate([*rows, *[QUIET] * tail])}


def test_a_trade_ends_at_the_target_or_the_stop_on_hourly_bars_and_an_hour_touching_both_is_the_stop():
    win = tw.trade(_hours(QUIET, (100, 113, 99.5, 112.5)), _daily(), T, 1, 3)
    assert (win["outcome"], win["exit"], win["hours_held"], win["exit_day"]) == ("win", 112.0, 2, T + timedelta(days=1))
    assert win["stop_frac"] == pytest.approx(0.04) and not win["open_differs"]
    assert tw.trade(_hours(QUIET, (100, 113, 99.5, 112.5)), _daily(), T, 1, 5)["outcome"] == "time", "the 1:5 target is at 120"
    loss = tw.trade(_hours(QUIET, (100, 100.5, 95, 96.5)), _daily(), T, 1, 3)
    assert (loss["outcome"], loss["exit"], loss["tie"]) == ("loss", 96.0, False)
    both = tw.trade(_hours((100, 113, 95, 110)), _daily(), T, 1, 3)
    assert (both["outcome"], both["tie"], both["hours_held"]) == ("loss", True, 1), "the entry hour counts, and both is the stop"
    gap = tw.trade(_hours(QUIET, (90, 91, 89, 90)), _daily(), T, 1, 3)
    assert (gap["outcome"], gap["exit"], gap["gap"]) == ("loss", 90, True)
    lucky = tw.trade(_hours(QUIET, (130, 131, 129, 130)), _daily(), T, 1, 3)
    assert (lucky["outcome"], lucky["exit"]) == ("win", 112.0), "no better than the target"
    short = tw.trade(_hours(QUIET, (100, 100.5, 87, 88)), _daily(), T, -1, 3)
    assert (short["outcome"], short["exit"]) == ("win", 88.0)
    assert tw.trade(_hours(QUIET, (100, 104.5, 99.5, 104)), _daily(), T, -1, 3)["outcome"] == "loss"


def test_the_limit_is_a_back_door_a_broken_path_is_a_time_out_and_the_hourly_open_must_be_the_daily_open():
    timed = tw.trade(_hours(), _daily(), T, 1, 3)
    assert (timed["outcome"], timed["hours_held"], timed["broken"]) == ("time", tw.HOLD_HOURS, False)
    assert timed["exit_day"] == T + timedelta(days=30) and timed["exit"] == 100.0
    broken = tw.trade(_hours(QUIET, (100, 100.5, 99.5, 101), tail=0), _daily(), T, 1, 3)
    assert (broken["outcome"], broken["broken"], broken["exit"], broken["hours_held"]) == ("time", True, 101, 2)
    assert tw.trade({}, _daily(), T, 1, 3) is None, "no hourly bar at the entry: not opened"
    assert tw.trade(_hours(), _daily(days=30), T, 1, 3) is None, "no daily bar for the entry day"
    off = tw.trade(_hours((100.5, 101, 100, 100.5)), _daily(), T, 1, 3)
    assert off["open_differs"] and off["entry"] == 100.5


def test_funding_is_counted_from_the_entry_stamp_through_the_hour_the_trade_ends_in():
    rows = [((H0 - 8) * tw.HOUR_MS, 0.5), (H0 * tw.HOUR_MS, 0.001), ((H0 + 8) * tw.HOUR_MS, 0.002), ((H0 + 16) * tw.HOUR_MS, 0.004)]
    funding = tw.stamps(rows)
    assert tw.paid(funding, H0, H0 + 8) == pytest.approx(0.003) and tw.paid(funding, H0 + 1, H0 + 7) == 0.0
    path = _hours(*[QUIET] * 8, (100, 113, 99.5, 112))                    # the target in the hour of the second stamp
    long = tw.trade(path, _daily(), T, 1, 3, funding)
    assert long["hours_held"] == 9 and long["funding"] == pytest.approx(0.003), "a long pays the entry stamp and the exit hour's"
    assert tw.trade(_hours(*[QUIET] * 8, (100, 100.5, 87, 88)), _daily(), T, -1, 3, funding)["funding"] == pytest.approx(-0.003)


def _zip(folder, name, rows):
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(folder / name, "w") as z:
        z.writestr("x.csv", "open_time,open,high,low,close,volume,close_time,quote_volume\n"
                   + "".join(",".join(map(str, r)) + "\n" for r in rows))


def test_the_hourly_reader_reads_no_file_and_no_row_after_the_span(tmp_path):
    last = date(2021, 1, 31)
    end = tw.hour_of(date(2021, 2, 1))
    _zip(tmp_path, "AUSDT-1h-2021-01.zip", [[(end - 2) * tw.HOUR_MS, 1, 2, 0.5, 1.5, 0, 0, 0], [(end - 1) * tw.HOUR_MS * 1000, 2, 3, 1, 2.5, 0, 0, 0],
                                            [end * tw.HOUR_MS, 9, 9, 9, 9, 0, 0, 0], [(end - 3) * tw.HOUR_MS, 0, 0, 0, 0, 0, 0, 0]])
    _zip(tmp_path, "AUSDT-1h-2021-02.zip", [[(end + 5) * tw.HOUR_MS, 7, 7, 7, 7, 0, 0, 0]])
    got = tw.read_hours(tmp_path, last)
    assert got == {end - 2: (1.0, 2.0, 0.5, 1.5), end - 1: (2.0, 3.0, 1.0, 2.5)}, "microsecond stamps read; a zero bar, a later row and a later file are not"
    assert tw.read_hours(tmp_path / "MISSING", last) == {}
    assert tw.SPAN == tw.bs.DEV and tw.SPAN[1] < tw.bs.CONFIRM[0]


def test_the_table_holds_both_sides_of_every_member_day_and_reads_one_coin_at_a_time(tmp_path, monkeypatch):
    bars = _daily()
    span = (START, START + timedelta(days=39))
    rows = [[(H0 + k) * tw.HOUR_MS, 100, 100.5, 99.5, 100, 0, 0, 0] for k in range(48)]
    _zip(tmp_path / tw.HOURLY_KIND / "AUSDT", "AUSDT-1h-2021-01.zip", rows)
    read = []
    original = tw.read_hours
    monkeypatch.setattr(tw, "read_hours", lambda folder, last: read.append(folder.name) or original(folder, last))
    info = {}
    made = tw.table(xsb.Market(bars={"AUSDT": bars, "BUSDT": bars}), tmp_path, {"AUSDT": [T], "BUSDT": [T]}, 3, span, info)
    assert set(made) == {(c, T, s) for c in ("AUSDT", "BUSDT") for s in (1, -1)} and read == ["AUSDT", "BUSDT"]
    assert info == {"last_hour_read": H0 + 47}, "the last hour any coin's bars reached"
    assert made[("AUSDT", T, 1)]["broken"] and made[("AUSDT", T, -1)]["side"] == -1 and made[("BUSDT", T, 1)] is None


def _made(coins, ends=None):
    """A trade table for signals at T: every coin stops out two days later unless ``ends`` says otherwise."""
    ends = ends or {}
    out = {}
    for c in coins:
        for side in (1, -1):
            one = {"side": side, "entry_day": T + timedelta(days=1), "entry": 100.0, "stop_frac": 0.04, "tie": False, "gap": False,
                   "unreachable": False, "broken": False, "open_differs": False, "exit_day": T + timedelta(days=3),
                   "exit": 100.0 - 4.0 * side, "outcome": "loss", "hours_held": 50, "funding": 0.0, **ends.get((c, side), {})}
            for day in (T, T + timedelta(days=1), T + timedelta(days=2)):
                out[(c, day, side)] = {**one, "entry_day": day + timedelta(days=1), "exit_day": max(one["exit_day"], day + timedelta(days=1))}
    return out


def test_the_book_risks_a_quarter_percent_books_the_result_on_the_day_it_ends_and_frees_the_place_that_day():
    coins = [f"C{k}USDT" for k in range(8)]
    made = _made(coins, {("C7USDT", 1): {"exit_day": T + timedelta(days=1), "exit": 112.0, "outcome": "win", "funding": 0.001}})
    made[("C0USDT", T + timedelta(days=1), -1)] = None
    first = [(T, c, 1, 1.0 + k / 10) for k, c in enumerate(coins)]
    out = tw.book(made, [*first, (T + timedelta(days=1), "C6USDT", 1, 5.0), (T + timedelta(days=1), "C1USDT", 1, 2.0),
                         (T + timedelta(days=1), "C0USDT", -1, 9.0)], "top30")
    took = [(t["symbol"], t["signal_day"]) for t in out["trades"]]
    assert [c for c, d in took if d == T] == coins[:1:-1], "the six widest, widest first"
    assert took[-1] == ("C1USDT", T + timedelta(days=1)), "C7 ended on T + 1: its place goes to T + 1's signals"
    assert out["passed_over"] == {"coin_open": 1, "book_full": 2, "not_openable": 1}
    won = out["trades"][0]
    assert won["symbol"] == "C7USDT" and won["notional"] == pytest.approx(0.0625), "0.25% / a 4% stop"
    assert out["net"][T + timedelta(days=1)] == pytest.approx(0.0625 * (0.12 - 0.001 - 2 * 8 / 10_000))
    assert won["cost_r"] == pytest.approx(2 * 8 / 10_000 / 0.04) and won["funding_r"] == pytest.approx(-0.001 / 0.04)
    assert set(out["net"]) == {T + timedelta(days=1), T + timedelta(days=3)}, "nothing is booked before a trade ends"
    tight = tw.book({("AUSDT", T, 1): {**made[("C1USDT", T, 1)], "stop_frac": 0.01}}, [(T, "AUSDT", 1, 1.0)], "top30")
    assert tight["trades"][0]["notional"] == pytest.approx(1 / tw.MAX_OPEN), "a 1% stop would ask a quarter of the budget: capped"


def test_the_null_keeps_every_signal_one_draw_a_book_and_gives_a_homeless_one_to_a_member_of_that_day():
    span = (START, START + timedelta(days=364))
    year = [START + timedelta(days=k) for k in range(365)]
    days = {"AUSDT": year, "BUSDT": year[:100], "CUSDT": year, "DUSDT": year[200:]}
    real = sorted([(year[k], "AUSDT", 1 if k % 2 else -1, k / 10) for k in (3, 50, 51, 120)]
                  + [(year[k], "BUSDT", 1, 1000.0 + k) for k in (0, 7, 50, 99)])
    moved, fate = tw.shifted(real, days, 4, "top30", span=span)
    again, _ = tw.shifted(real, days, 4, "top30", tw.fr.places(days), span, tw.members_by_day(tw.fr.places(days)))
    assert moved == again != tw.shifted(real, days, 5, "top30", span=span)[0]
    assert len(moved) == len(real) and fate == {"kept_on_own_coin": fate["kept_on_own_coin"], "given_to_a_member": len(real) - fate["kept_on_own_coin"], "dropped": 0}
    assert sorted((side, w) for _, _, side, w in moved) == sorted((side, w) for _, _, side, w in real), "sides and widths stay"
    steps = {(d - year[round(w * 10)]).days % 365 for d, c, _, w in moved if w < 1000}
    assert len(steps) == 1 and tw.MIN_SHIFT <= next(iter(steps)) <= 365 - tw.MIN_SHIFT, "one draw for the whole book"
    step = steps.pop()
    for d, c, _, w in moved:
        if w >= 1000:
            assert (d - year[round(w - 1000)]).days % 365 == step and d in tw.fr.places(days)[c], "the day is the shifted day, the coin a member"
            assert c == "BUSDT" or (round(w - 1000) + step) % 365 >= 100, "only a homeless signal changes coin"
    assert len({(d, c) for d, c, _, _ in moved}) == len(moved), "no coin gets two signals a day"
    alone, fate = tw.shifted([(year[0], "BUSDT", 1, 1.0)], {"BUSDT": year[:10]}, 1, "top30", span=span)
    assert alone == [] and fate["dropped"] == 1, "no member that day: dropped"
    two = [START + timedelta(days=k) for k in range(730)]
    wide = (START, START + timedelta(days=729))
    both = [(two[40], "AUSDT", 1, 1.0), (two[400], "AUSDT", -1, 2.0), (two[729], "AUSDT", 1, 3.0)]
    turned, _ = tw.shifted(both, {"AUSDT": two}, 7, "top30", span=wide)
    assert sorted(d.year for d, *_ in turned) == [2021, 2022, 2022], "each signal turns round its own calendar year"
    assert len({(d - o).days % 365 for (d, *_), (o, *_) in zip(sorted(turned, key=lambda e: e[3]), both)}) == 1


def test_the_structure_door_holds_the_nulls_to_the_real_books_trades_long_share_and_open_count():
    real = {"trades": 1000, "long_share": 0.55, "mean_open_at_entry": 4.0}
    near = [{"trades": 950, "long_share": 0.52, "mean_open_at_entry": 3.8}] * 3
    assert tw.structure(real, near)["ok"]
    for key, value in (("trades", 880), ("long_share", 0.49), ("mean_open_at_entry", 3.5)):
        out = tw.structure(real, [{**near[0], key: value}] * 3)
        assert not out["ok"] and out["inside"][key] is False and sum(out["inside"].values()) == 2


def test_counts_stop_on_time_outs_ties_or_a_differing_open_and_carry_no_result():
    def trades(n, year, **more):
        return [{"entry_day": date(year, 3, 1), "exit_day": date(year, 3, 2), "side": 1, "tie": False, "gap": False, "unreachable": False,
                 "broken": False, "open_differs": False, "stop_frac": 0.04, "notional": 0.0625, "outcome": "loss", "hours_held": 30, **more}] * n
    full = {"trades": [t for y in range(2020, 2025) for t in trades(40, y)], "passed_over": {}, "mean_open_at_entry": 4.0}
    out = tw.counted(full)
    assert not out["stop"]["stops"] and out["time_out_share"] == 0.0 and out["stop_pct"]["median"] == 4.0 and out["long_share"] == 1.0
    assert out["capped_notional_share"] == 0.0, "0.25% over a 4% stop is 6.25% of the budget: this book's unit, not L32's 1%"
    held_back = tw.counted({**full, "trades": full["trades"] + trades(10, 2024, stop_frac=0.01, notional=1 / tw.MAX_OPEN)})
    assert held_back["capped_notional_share"] == pytest.approx(10 / 210, abs=1e-4), "a 1% stop asks 25% and is capped"
    assert not any(word in json.dumps(out) for word in ("win", "loss", "net", "sharpe", "_r\"")), "counts carry no result"
    timed = tw.counted({**full, "trades": full["trades"] + trades(12, 2024, outcome="time")})
    assert timed["time_out_share"] > tw.MAX_TIME_SHARE and timed["stop"]["too_many_time_outs"] and timed["stop"]["stops"]
    assert tw.counted({**full, "trades": full["trades"] + trades(1, 2024, open_differs=True)})["stop"]["stops"]
    assert tw.counted({**full, "trades": full["trades"] + trades(12, 2024, tie=True)})["stop"]["too_many_ties"]
    assert tw.counted({**full, "trades": full["trades"][40:]})["stop"]["thin_years"] == []
    assert tw.counted({**full, "trades": full["trades"][:120]})["stop"]["thin_years"] == ["2023", "2024"]


def test_the_plant_is_read_at_the_books_win_rate():
    def rows(size, passes, under, rate):
        return [{"size": size, "built": True, "p": 0.01 if k < under else 0.5, "passes": k < passes, "win_rate": rate} for k in range(tw.PLANT_SEEDS)]
    base = rows("lift_0.00", 0, 4, 0.24) + rows("lift_0.05", 30, 60, 0.29) + rows("lift_0.08", 70, 90, 0.32)
    out = tw.seen(base + rows("lift_0.12", 85, 99, 0.35) + rows("lift_0.16", 99, 100, 0.39), 1 / 3)
    assert out["size_zero_valid"] and out["smallest_size_at_the_line"] == "lift_0.12" and out["sees_the_line"]
    assert out["sizes"]["lift_0.12"]["book_win_rate_median"] == 0.35
    weak = tw.seen(base + rows("lift_0.12", 79, 99, 0.35), 1 / 3)
    assert weak["plant_reaches_the_line"] and not weak["sees_the_line"]
    short = tw.seen(base, 1 / 3)
    assert not short["plant_reaches_the_line"] and short["smallest_size_at_the_line"] is None and not short["sees_the_line"]
    assert not tw.seen(rows("lift_0.00", 5, 30, 0.24) + rows("lift_0.12", 100, 100, 0.40), 1 / 3)["sees_the_line"], "size zero is not chance"


def test_the_null_door_counts_l32s_cells_too():
    drift = tw.fr.line([float(k) / 10 for k in range(100)])
    years = {str(y): 30.0 for y in range(2020, 2025)}
    book = {"annual_sharpe": 1.4, "annual_pct": 30.0, "years_pct": years, "win_rate": 0.1}
    assert tw.verdict(book, [0.2] * 600, drift)["passes"] and tw.verdict(book, [0.2] * 600, drift)["2c_beside_nulls"]["alpha"] == 0.05 / 8
    assert tw.verdict(book, [1.5] * 2 + [0.2] * 598, drift)["passes"], "p 3/601 is under 0.05/8"
    assert not tw.verdict(book, [1.5] * 3 + [0.2] * 597, drift)["passes"], "p 4/601 is over it"
    assert not tw.verdict({**book, "annual_sharpe": 0.99}, [0.2] * 600, drift)["passes"]


def test_the_stages_run_in_order_and_refuse_through_a_stopped_count_an_outside_structure_or_a_broken_plant(monkeypatch, tmp_path, capsys):
    coins = [f"K{k}USDT" for k in range(4)]
    bars, first, last = {}, tw.hour_of(date(2019, 11, 1)), tw.hour_of(date(2025, 1, 1))
    for coin in coins:
        draw, price, daily, by_month = random.Random(coin), 100.0, {}, {}
        for hour in range(first, last):
            opened, price = price, price * (1 + draw.gauss(0, 0.008))
            high, low = max(opened, price) * 1.002, min(opened, price) * 0.998
            day = date(1970, 1, 1) + timedelta(days=hour // 24)
            by_month.setdefault(day.strftime("%Y-%m"), []).append([hour * tw.HOUR_MS, opened, high, low, price, 0, 0, 0])
            o, h, l, _ = daily.get(day, (opened, high, low, price))
            daily[day] = (o, max(h, high), min(l, low), price)
        for month, rows in by_month.items():
            _zip(tmp_path / "archive" / tw.HOURLY_KIND / coin, f"{coin}-1h-{month}.zip", rows)
        bars[coin] = {d: xsb.Bar(open=o, close=c, quote_volume=1e6, high=h, low=l) for d, (o, h, l, c) in daily.items()}
    market = xsb.Market(bars=bars)
    months = {date(y, m, 1): coins for y in range(2020, 2025) for m in range(1, 13)}
    monkeypatch.setattr(tw.lab, "hold", lambda cache, span, attach=True: market)
    monkeypatch.setattr(tw.fr.lab, "tier_members", lambda m, s: {"top30": months, "rank31_100": {k: [] for k in months}})
    monkeypatch.setattr(tw.xsb, "_in_sandbox", lambda: True)
    monkeypatch.setattr(tw, "NULLS", 6)
    monkeypatch.setattr(tw, "PLANT_SEEDS", 5)
    monkeypatch.setattr(tw.fr, "MIN_YEAR_TRADES", 1)
    cell = ["--tier", "top30", "--ratio", "3", "--cache", str(tmp_path)]

    def run(command, *more):
        code = tw.main([command, *cell, *more])
        printed = capsys.readouterr()
        path = tmp_path / f"{command}.json"
        if code == tw.EXIT_OK:
            path.write_text(printed.out)
        return code, printed, str(path)

    assert tw.main(["two-structure", *cell]) == tw.EXIT_USAGE and "needs this cell's --counts" in capsys.readouterr().err
    code, printed, counts = run("two-counts")
    body = json.loads(printed.out)
    assert code == tw.EXIT_OK and body["reads_profit_or_loss"] is False and not body["counts"]["stop"]["stops"]
    assert body["table"]["hourly_open_differs"] == 0 and body["counts"]["time_out_share"] <= tw.MAX_TIME_SHARE and "win" not in json.dumps(body["counts"])
    monkeypatch.setattr(tw, "STRUCTURE_BAND", {"trades": 0.0, "long_share": 0.0, "mean_open_at_entry": 0.0})
    code, printed, shaped = run("two-structure", "--counts", counts)
    body = json.loads(printed.out)
    assert code == tw.EXIT_OK and body["reads_profit_or_loss"] is False and not body["structure"]["ok"]
    fate = body["signals_a_book"]
    assert fate["dropped"] < 5 and fate["kept_on_own_coin"] > 100 * fate["dropped"], \
        "only a signal moved onto the span's last day, which nobody can enter on, is dropped"
    assert tw.main(["two-null", *cell, "--counts", counts, "--structure", shaped]) == tw.EXIT_USAGE
    assert "the null is another book" in capsys.readouterr().err
    monkeypatch.setattr(tw, "STRUCTURE_BAND", {"trades": 9.0, "long_share": 9.0, "mean_open_at_entry": 9.0})
    code, printed, shaped = run("two-structure", "--counts", counts)
    assert code == tw.EXIT_OK and json.loads(printed.out)["structure"]["ok"]
    code, printed, null = run("two-null", "--counts", counts, "--structure", shaped)
    body = json.loads(printed.out)
    assert code == tw.EXIT_OK and len(body["books"]) == 6 and all(b["trades"] > 0 for b in body["books"]) and body["reads_real_entries"] is False
    code, printed, plant = run("two-plant", "--counts", counts, "--structure", shaped, "--null", null)
    body = json.loads(printed.out)
    assert code == tw.EXIT_OK and len(body["rows"]) == 5 * len(tw.LIFTS) and not body["seen"]["size_zero_valid"]
    code, printed, _ = run("two-real", "--counts", counts, "--structure", shaped, "--null", null, "--plant", plant)
    assert code == tw.EXIT_BLOCKED and printed.out == "" and "the plant is broken" in printed.err
    monkeypatch.setattr(tw.sl, "SENS_FALSE_ALARM", (0, 10))
    code, printed, plant = run("two-plant", "--counts", counts, "--structure", shaped, "--null", null)
    assert code == tw.EXIT_OK and json.loads(printed.out)["seen"]["size_zero_valid"]
    code, printed, _ = run("two-real", "--counts", counts, "--structure", shaped, "--null", null, "--plant", plant)
    body = json.loads(printed.out)
    assert code == tw.EXIT_OK and body["reads_real_entries"] and body["thomas_line"] == 0.3333 and body["last_day_read"] == "2024-12-31"
    assert body["last_hour_read"] == "2024-12-31T23:00Z", "the span's last hour"
    assert {"annual_sharpe", "annual_pct", "win_rate", "time_share", "mean_net_r", "ends", "years_pct"} <= set(body["book"])
    assert body["verdict"]["2c_beside_nulls"]["alpha"] == 0.05 / 8
    monkeypatch.setattr(tw.sl, "L6_UNUSABLE_PCT", -1e9)
    code, printed, _ = run("two-real", "--counts", counts, "--structure", shaped, "--null", null, "--plant", plant)
    assert code == tw.EXIT_BLOCKED and printed.out == "" and "market's drift" in printed.err
    monkeypatch.setattr(tw.xsb, "_in_sandbox", lambda: False)
    assert tw.main(["two-counts", *cell]) == tw.EXIT_USAGE


def test_the_fixed_values_are_the_registered_ones():
    assert (tw.RATIOS, tw.STOP_ATR, tw.HOLD_HOURS, tw.RISK, tw.MAX_OPEN, tw.DIVISOR) == ((3, 5), 0.5, 720, 0.0025, 6, 8)
    assert tw.COST_BPS == {"top30": 8.0, "rank31_100": 26.0} and tw.NULLS == 600 and tw.PLANT_SEEDS == 100
    assert tw.LIFTS == (0.0, 0.03, 0.05, 0.08, 0.12, 0.16, 0.20) and tw.THOMAS_LINE == {3: pytest.approx(1 / 3), 5: 0.20}
    assert (tw.MAX_TIE_SHARE, tw.MAX_TIME_SHARE, tw.MIN_YEAR_TRADES, tw.MIN_SHIFT) == (0.05, 0.05, 30, 30)
    assert tw.STRUCTURE_BAND == {"trades": 0.10, "long_share": 0.05, "mean_open_at_entry": 0.10}
