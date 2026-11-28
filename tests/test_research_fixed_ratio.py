"""L32, the fixed reward-to-risk trade book (STRATEGY_DISCOVERY_LOOP_V0.1 sections 126 and 127): a trade's three
ends on daily bars, the one entry family, the book's sizing and places, the time-shifted null, the planted win rate,
and the doors. Synthetic markets only; nothing here reads the cache."""

from __future__ import annotations

import json
import random
from datetime import date, timedelta

import pytest

from scripts.research import cross_section_basket as xsb
from scripts.research import fixed_ratio as fr

START = date(2021, 1, 1)
T = START + timedelta(days=29)             # the signal day in the flat markets below


def _bars(rows, start=START):
    return {start + timedelta(days=k): xsb.Bar(open=o, close=c, quote_volume=1e6, high=h, low=l)
            for k, (o, h, l, c) in enumerate(rows)}


def _flat(days, price=100.0, half=1.0):
    return [(price, price + half, price - half, price)] * days


def _after(*rows, half=1.0, tail=40):
    """Thirty flat days (true range 2 x ``half``, so the stop sits 5 x ``half`` from an entry at 100), then ``rows``
    from the entry day on, then flat days."""
    return _bars(_flat(30, half=half) + list(rows) + _flat(tail, half=half))


def test_a_trade_ends_at_the_target_the_stop_or_the_limit_and_a_day_touching_both_is_the_stop():
    entry = (100, 101, 99, 100)
    win = fr.trade(_after(entry, (100, 116, 99, 110)), T, 1, 3)
    assert (win["outcome"], win["exit"], win["entry_day"], win["exit_day"]) == ("win", 115.0, T + timedelta(days=1), T + timedelta(days=2))
    assert fr.r_multiple(win) == pytest.approx(3.0) and win["stop_frac"] == pytest.approx(0.05)
    assert fr.trade(_after(entry, (100, 116, 99, 110)), T, 1, 5)["outcome"] == "time", "the 1:5 target sits at 125"
    loss = fr.trade(_after(entry, (100, 101, 94, 96)), T, 1, 3)
    assert (loss["outcome"], loss["exit"], loss["tie"]) == ("loss", 95.0, False) and fr.r_multiple(loss) == pytest.approx(-1.0)
    both = fr.trade(_after(entry, (100, 116, 94, 110)), T, 1, 3)
    assert (both["outcome"], both["tie"]) == ("loss", True), "the order inside a day is not known: the stop"
    first_day = fr.trade(_after((100, 116, 94, 110)), T, 1, 3)
    assert (first_day["outcome"], first_day["tie"], first_day["exit_day"]) == ("loss", True, T + timedelta(days=1)), "the entry day counts"
    timed = fr.trade(_after(entry), T, 1, 3)
    assert (timed["outcome"], timed["exit_day"], fr.r_multiple(timed)) == ("time", T + timedelta(days=fr.HOLD_DAYS), 0.0)
    ended = fr.trade(_after(entry, (100, 101, 99, 102), tail=0), T, 1, 3)
    assert (ended["outcome"], ended["exit"], ended["exit_day"]) == ("time", 102, T + timedelta(days=2)), "the coin's last bar"


def test_an_open_beyond_the_stop_exits_at_the_open_and_one_beyond_the_target_is_paid_the_target():
    entry = (100, 101, 99, 100)
    gap = fr.trade(_after(entry, (90, 91, 89, 90)), T, 1, 3)
    assert (gap["outcome"], gap["exit"], gap["gap"]) == ("loss", 90, True) and fr.r_multiple(gap) == pytest.approx(-2.0)
    lucky = fr.trade(_after(entry, (120, 121, 119, 120)), T, 1, 3)
    assert (lucky["outcome"], lucky["exit"]) == ("win", 115.0), "no better than the target"


def test_a_short_is_the_mirror_and_a_target_at_or_under_zero_can_never_be_a_win():
    entry = (100, 101, 99, 100)
    win = fr.trade(_after(entry, (100, 101, 84, 90)), T, -1, 3)
    assert (win["outcome"], win["exit"]) == ("win", 85.0) and fr.r_multiple(win) == pytest.approx(3.0)
    loss = fr.trade(_after(entry, (100, 106, 99, 104)), T, -1, 3)
    assert (loss["outcome"], loss["exit"]) == ("loss", 105.0)
    wide = _after((100, 110, 90, 100), half=10.0)             # stop distance 50: a short's 1:3 target is at -50
    far = fr.trade(wide, T, -1, 3)
    assert far["unreachable"] and far["outcome"] == "time"
    assert fr.trade(_after((100, 125, 75, 100), half=25.0), T, 1, 3) is None, "a long whose stop is under zero is not opened"
    assert fr.trade(_after(tail=0), T, 1, 3) is None, "no next bar: no entry"


def test_the_entry_is_a_close_past_every_one_of_the_twenty_closes_before_it():
    quiet = _flat(25) + [(100, 103.5, 99.5, 103), (103, 104, 102, 103)]
    day = START + timedelta(days=25)
    side, width = fr.breakout(_bars(quiet), day)
    assert side == 1 and width == pytest.approx(3 / ((13 * 2 + 4) / 14))
    down = _flat(25) + [(100, 100.5, 96.5, 97), (97, 98, 96, 97)]
    assert fr.breakout(_bars(down), day)[0] == -1
    assert fr.breakout(_bars(_flat(27)), day) is None, "a close level with the highest is no breakout"
    assert fr.breakout(_bars(quiet[:-1]), day) is None, "no next bar to enter on"
    holed = _bars(quiet)
    del holed[START + timedelta(days=10)]
    assert fr.breakout(holed, day) is None, "a missing bar in the window"
    assert fr.breakout(_bars(quiet), day - timedelta(days=10)) is None, "under twenty closes before it"


def _market(coins, rows_by_coin=None, funding=None):
    rows_by_coin = rows_by_coin or {}
    return xsb.Market(bars={c: rows_by_coin.get(c) or _after((100, 102, 98, 100), half=2.0) for c in coins},
                      funding=funding or {})


def test_the_book_risks_one_percent_a_trade_holds_six_and_gives_a_freed_place_to_that_days_signals():
    coins = [f"C{k}USDT" for k in range(8)]
    stopped = _after((100, 102, 98, 100), (100, 101, 89, 90), half=2.0)       # C7 stops out on T + 2
    market = _market(coins, {"C7USDT": stopped})
    first = [(T, c, 1, 1.0 + k / 10) for k, c in enumerate(coins)]
    out = fr.book(market, [*first, (T + timedelta(days=1), "C6USDT", 1, 1.0), (T + timedelta(days=2), "C1USDT", 1, 9.0),
                           (T + timedelta(days=2), "C0USDT", -1, 0.5)], "top30", 3)
    took = [(t["symbol"], t["signal_day"]) for t in out["trades"]]
    assert [c for c, d in took if d == T] == coins[:1:-1], "the six widest breakouts, widest first"
    assert out["passed_over"] == {"coin_open": 1, "book_full": 3, "not_openable": 0}
    assert took[-1] == ("C1USDT", T + timedelta(days=2)), "the place C7's stop freed on T + 2 goes to T + 2's widest signal"
    late = out["trades"][-1]
    assert late["notional"] == pytest.approx(0.1) and late["stop_frac"] == pytest.approx(0.1), "1% / a 10% stop"
    assert late["entry_day"] == T + timedelta(days=3) and out["mean_open_at_entry"] == pytest.approx((1 + 2 + 3 + 4 + 5 + 6 + 6) / 7, abs=1e-3)


def test_the_days_net_is_price_less_cost_less_funding_on_a_budget_of_one():
    rows = _after((100, 102, 98, 100), (100, 101, 89, 90), half=2.0)          # entry 100, stop 90: a 1R loss
    stamp = lambda d: int((d - date(1970, 1, 1)).total_seconds() * 1000) + 8 * 3_600_000    # noqa: E731
    paid = {"AUSDT": [(stamp(T + timedelta(days=1)), 0.001), (stamp(T + timedelta(days=2)), 0.002)]}
    for tier, bps in (("top30", 8.0), ("rank31_100", 26.0)):
        out = fr.book(xsb.Market(bars={"AUSDT": rows}, funding=paid), [(T, "AUSDT", 1, 1.0)], tier, 3)
        made = out["trades"][0]
        assert made["outcome"] == "loss" and made["notional"] == pytest.approx(0.1)
        assert sum(out["net"].values()) == pytest.approx(0.1 * (-0.10 - 0.003 - 2 * bps / 10_000))
        assert made["cost_r"] == pytest.approx(2 * bps / 10_000 / 0.1) and made["funding_r"] == pytest.approx(-0.003 / 0.1)
    short = fr.book(xsb.Market(bars={"AUSDT": rows}, funding=paid), [(T, "AUSDT", -1, 1.0)], "top30", 3)
    assert short["trades"][0]["funding_r"] == pytest.approx(+0.003 / 0.1), "a short is paid the positive rate"
    tight = fr.book(xsb.Market(bars={"AUSDT": _after((100, 101, 99, 100))}), [(T, "AUSDT", 1, 1.0)], "top30", 3)
    assert tight["trades"][0]["notional"] == pytest.approx(1 / fr.MAX_OPEN), "a 5% stop would ask 20% of the budget: capped"


def test_signals_are_read_on_member_days_only_and_never_entered_after_the_span(monkeypatch):
    rows = _bars(_flat(25) + [(100, 103.5, 99.5, 103)] + _flat(40, price=103.0))
    market = xsb.Market(bars={"AUSDT": rows, "BUSDT": rows})
    span = (date(2021, 1, 1), date(2021, 2, 10))
    monkeypatch.setattr(fr.lab, "tier_members", lambda m, s: {"top30": {date(2021, 1, 1): ["AUSDT"], date(2021, 2, 1): ["BUSDT"]},
                                                              "rank31_100": {date(2021, 1, 1): [], date(2021, 2, 1): []}})
    days = fr.member_days(market, "top30", span)
    assert min(days["AUSDT"]) == START + timedelta(days=fr.BREAKOUT_DAYS) and max(days["AUSDT"]) == date(2021, 1, 31)
    assert min(days["BUSDT"]) == date(2021, 2, 1) and max(days["BUSDT"]) == date(2021, 2, 9), "the entry is inside the span"
    found = fr.signals(market, days)
    assert [(d, c, side) for d, c, side, _ in found] == [(START + timedelta(days=25), "AUSDT", 1)], "B was no member that day"
    assert fr.SPAN == fr.bs.DEV and fr.SPAN[1] < fr.bs.CONFIRM[0]


def test_the_null_moves_every_signal_by_one_draw_along_the_calendar_and_drops_what_lands_off_a_member_day():
    span = (START, START + timedelta(days=364))
    year = [START + timedelta(days=k) for k in range(365)]
    days = {"AUSDT": year, "BUSDT": year[:100]}
    real = sorted([(year[k], "AUSDT", 1 if k % 2 else -1, k / 10) for k in (3, 50, 51, 120)]
                  + [(year[k], "BUSDT", 1, 1000.0 + k) for k in (0, 7, 50, 99)])
    moved = fr.shifted(real, days, 4, "top30", span=span)
    assert moved == fr.shifted(real, days, 4, "top30", fr.places(days), span) != fr.shifted(real, days, 5, "top30", span=span)
    a_days = {round(w * 10): d for d, c, _, w in moved if c == "AUSDT"}
    steps = {(a_days[k] - year[k]).days % 365 for k in (3, 50, 51, 120)}
    assert len(a_days) == 4 and len(steps) == 1, "one draw for the whole book"
    step = steps.pop()
    assert fr.MIN_SHIFT <= step <= 365 - fr.MIN_SHIFT
    assert {side for _, c, side, w in moved if c == "AUSDT" and round(w * 10) % 2} == {1}, "sides and widths are carried"
    kept_b = sorted(round(w - 1000) for _, c, _, w in moved if c == "BUSDT")
    assert kept_b == sorted(k for k in (0, 7, 50, 99) if (k + step) % 365 < 100), "a signal landing where the coin is no member is dropped"
    assert all((d - year[round(w - 1000)]).days % 365 == step for d, c, _, w in moved if c == "BUSDT")
    together = [d for d, c, _, w in moved if (c, round(w * 10)) == ("AUSDT", 50) or (c, round(w - 1000)) == ("BUSDT", 50)]
    assert len(set(together)) <= 1, "signals of one day stay on one day"


def test_the_plant_drops_non_winning_entries_until_the_win_rate_is_the_size_asked(monkeypatch):
    moved = [(START + timedelta(days=k), "AUSDT", 1, 1.0) for k in range(4000)]
    monkeypatch.setattr(fr, "trade", lambda bars, day, side, ratio: None if (day - START).days % 40 == 39 else
                        {"outcome": "win" if (day - START).days % 4 == 0 else "loss" if (day - START).days % 4 < 3 else "time"})
    market = xsb.Market(bars={"AUSDT": {}})
    wins = lambda entries: sum(1 for d, *_ in entries if (d - START).days % 4 == 0) / len(entries)      # noqa: E731
    same, note = fr.planted(market, moved, 3, 1, lift=0.0)
    assert len(same) == 3900 and note["built"] and wins(same) == pytest.approx(0.2564, abs=1e-3), "size 0 is the null itself"
    lifted, note = fr.planted(market, moved, 3, 1, lift=0.08)
    assert note["built"] and wins(lifted) == pytest.approx(note["asked_win_rate"], abs=0.02) and note["asked_win_rate"] == pytest.approx(0.3364, abs=1e-3)
    named, note = fr.planted(market, moved, 3, 1, target=fr.THOMAS_LINE[3])
    assert note["built"] and wins(named) == pytest.approx(1 / 3, abs=0.02)
    assert {d for d, *_ in same if (d - START).days % 4 == 0} <= {d for d, *_ in named}, "every winning entry stays"
    under, note = fr.planted(market, moved, 5, 1, target=fr.THOMAS_LINE[5])
    assert not note["built"] and len(under) == 3900, "the null already wins more often than the line: nothing to build"
    assert fr.planted(market, moved, 3, 1, lift=0.08)[0] == lifted != fr.planted(market, moved, 3, 2, lift=0.08)[0]


def test_counts_show_no_profit_or_loss_and_stop_on_a_thin_year_or_too_many_ties():
    def made(n, year, tie=False):
        return [{"entry_day": date(year, 3, 1), "exit_day": date(year, 3, 9), "side": 1, "tie": tie, "gap": False,
                 "unreachable": False, "stop_frac": 0.1, "notional": 0.1, "outcome": "win"}] * n
    full = {"trades": made(40, 2021) + made(30, 2022) + made(30, 2023) + made(30, 2024), "passed_over": {}, "mean_open_at_entry": 4.0}
    out = fr.counted(full)
    assert out["trades"] == 130 and not out["stop"]["stops"] and out["by_year"]["2022"] == {"trades": 30, "long": 30, "short": 0}
    assert not any(word in json.dumps(out) for word in ("win", "loss", "net", "sharpe", "_r\"")), "counts carry no result"
    thin = fr.counted({**full, "trades": made(40, 2021) + made(29, 2022) + made(30, 2023) + made(30, 2024)})
    assert thin["stop"] == {"thin_years": ["2022"], "too_many_ties": False, "stops": True}
    tied = fr.counted({**full, "trades": full["trades"] + made(8, 2024, tie=True)})
    assert tied["tie_share"] > fr.MAX_TIE_SHARE and tied["stop"]["too_many_ties"] and tied["stop"]["stops"]


def test_the_measure_reads_every_year_and_a_time_out_is_no_win():
    rows = _after((100, 102, 98, 100), (100, 131, 99, 130), half=2.0)         # entry 100, stop 90, the 1:3 target 130
    out = fr.book(xsb.Market(bars={"AUSDT": rows, "BUSDT": _after((100, 102, 98, 100), half=2.0)}),
                  [(T, "AUSDT", 1, 2.0), (T, "BUSDT", 1, 1.0)], "top30", 3)
    got = fr.measured(out, (date(2020, 1, 1), date(2024, 12, 31)))
    assert got["ends"] == {"win": 1, "loss": 0, "time": 1} and got["win_rate"] == 0.5 and got["time_share"] == 0.5
    assert set(got["years_pct"]) == {"2020", "2021", "2022", "2023", "2024"} and got["years_pct"]["2022"] == 0.0
    assert got["mean_price_r"] == pytest.approx(1.5) and got["months"] == 60
    assert got["sum_pct"] == pytest.approx(100 * sum(out["net"].values()), abs=1e-3) and got["annual_pct"] == pytest.approx(got["sum_pct"] / 5, abs=0.01)


def test_the_drift_line_is_the_higher_of_the_entry_rule_and_the_nulls_and_an_unusable_cell_is_not_judged():
    assert fr.line([float(k) / 10 for k in range(100)]) == {"null_p95_annual_pct": 9.4, "line_annual_pct": 15.0, "usable": True}
    assert fr.line([float(k) / 4 for k in range(100)])["line_annual_pct"] == 23.5
    drift = fr.line([float(k) / 2 for k in range(100)])
    assert drift["null_p95_annual_pct"] == 47.0 and not drift["usable"]
    book = {"annual_sharpe": 3.0, "annual_pct": 60.0, "years_pct": {str(y): 60.0 for y in range(2020, 2025)}, "win_rate": 0.9}
    assert fr.verdict(book, [0.0] * 300, drift)["judged"] is False


def test_the_ladder_alone_passes_a_book_and_the_win_rate_judges_nothing():
    drift = fr.line([float(k) / 4 for k in range(100)])                        # the line is 23.5%
    years = {str(y): 30.0 for y in range(2020, 2025)}
    good = fr.verdict({"annual_sharpe": 1.4, "annual_pct": 30.0, "years_pct": years, "win_rate": 0.10}, [0.2] * 300, drift)
    assert good["passes"] and good["2c_beside_nulls"]["alpha"] == 0.05 / fr.CELLS, "a 10% win rate does not matter"
    assert not fr.verdict({"annual_sharpe": 1.4, "annual_pct": 20.0, "years_pct": years, "win_rate": 0.9}, [0.2] * 300, drift)["passes"], \
        "over the entry rule's 15% and under the drift line"
    assert not fr.verdict({"annual_sharpe": 1.4, "annual_pct": 30.0, "years_pct": years, "win_rate": 0.9}, [1.5] * 5 + [0.2] * 295, drift)["passes"], \
        "five nulls at or above it: p 6/301 is over 0.05/4"
    sour = {**years, "2022": -5.0, "2023": -5.0}
    assert not fr.verdict({"annual_sharpe": 1.4, "annual_pct": 30.0, "years_pct": sour, "win_rate": 0.9}, [0.2] * 300, drift)["passes"]


def test_a_size_is_seen_at_eighty_percent_and_nothing_is_read_when_size_zero_is_not_chance():
    def rows(size, passes, under, built=True):
        return [{"size": size, "built": built, "p": 0.01 if k < under else 0.5, "passes": k < passes} for k in range(fr.PLANT_SEEDS)]
    out = fr.seen(rows("lift_0.00", 1, 5) + rows("lift_0.05", 79, 90) + rows("thomas_line", 80, 95))
    assert out["size_zero_valid"] and out["sizes"]["thomas_line"]["seen"] and not out["sizes"]["lift_0.05"]["seen"]
    assert not fr.seen(rows("lift_0.00", 12, 20) + rows("thomas_line", 100, 100))["size_zero_valid"], "20 false alarms of 100"
    unbuilt = fr.seen(rows("lift_0.00", 1, 5) + rows("thomas_line", 0, 0, built=False))
    assert unbuilt["sizes"]["thomas_line"] == {"seeds": 100, "built": 0, "under_0.05": 0, "pass_door": 0, "share": None, "seen": False}


def test_each_stage_refuses_without_the_one_before_it_and_nothing_runs_outside_the_sandbox(monkeypatch, tmp_path, capsys):
    cell = ["--tier", "top30", "--ratio", "3"]
    monkeypatch.setattr(fr.xsb, "_in_sandbox", lambda: False)
    assert fr.main(["fixed-counts", *cell]) == fr.EXIT_USAGE
    monkeypatch.setattr(fr.xsb, "_in_sandbox", lambda: True)

    def put(name, body):
        (tmp_path / name).write_text(json.dumps(body))
        return str(tmp_path / name)
    ok = put("c.json", {"command": "fixed-counts", "tier": "top30", "ratio": 3, "counts": {"stop": {"stops": False}}})
    stopped = put("s.json", {"command": "fixed-counts", "tier": "top30", "ratio": 3, "counts": {"stop": {"stops": True}}})
    other = put("o.json", {"command": "fixed-counts", "tier": "top30", "ratio": 5, "counts": {"stop": {"stops": False}}})
    short = put("n.json", {"command": "fixed-null", "tier": "top30", "ratio": 3, "books": [{}] * 10})
    whole = put("w.json", {"command": "fixed-null", "tier": "top30", "ratio": 3, "books": [{}] * fr.NULLS})
    for counts in (None, stopped, other):
        assert fr.main(["fixed-null", *cell, *(["--counts", counts] if counts else [])]) == fr.EXIT_USAGE
    assert fr.main(["fixed-plant", *cell, "--counts", ok, "--null", short]) == fr.EXIT_USAGE
    assert fr.main(["fixed-real", *cell, "--counts", ok, "--null", whole]) == fr.EXIT_USAGE
    assert "the door is read before the real book" in capsys.readouterr().err
    monkeypatch.setattr(fr.lab, "hold", lambda cache, span, attach=True: None)
    assert fr.main(["fixed-counts", *cell, "--cache", str(tmp_path)]) == fr.EXIT_BLOCKED
    assert fr.main(["fixed-null", *cell, "--counts", ok, "--cache", str(tmp_path)]) == fr.EXIT_BLOCKED


def test_the_fixed_values_are_the_registered_ones():
    assert (fr.RATIOS, fr.STOP_ATR, fr.ATR_DAYS, fr.BREAKOUT_DAYS, fr.HOLD_DAYS, fr.RISK, fr.MAX_OPEN) == ((3, 5), 2.5, 14, 20, 30, 0.01, 6)
    assert fr.COST_BPS == {"top30": 8.0, "rank31_100": 26.0} and fr.CELLS == 4 and fr.NULLS == 300 and fr.PLANT_SEEDS == 100
    assert fr.THOMAS_LINE == {3: pytest.approx(1 / 3), 5: 0.20} and fr.LIFTS[0] == 0.0
    assert (fr.MIN_YEAR_TRADES, fr.MAX_TIE_SHARE, fr.MIN_SHIFT) == (30, 0.05, 30)


def test_the_four_stages_run_in_order_and_the_real_book_is_not_read_through_a_broken_plant_or_an_unusable_cell(monkeypatch, tmp_path, capsys):
    coins = [f"K{k}USDT" for k in range(8)]
    bars = {}
    for coin in coins:
        draw, price, day, own = random.Random(coin), 100.0, date(2019, 11, 1), {}
        while day <= date(2024, 12, 31):
            opened, price = price, price * (1 + draw.gauss(0, 0.04))
            own[day] = xsb.Bar(open=opened, close=price, quote_volume=1e6, high=max(opened, price) * 1.01, low=min(opened, price) * 0.99)
            day += timedelta(days=1)
        bars[coin] = own
    market = xsb.Market(bars=bars)
    months = {date(y, m, 1): coins for y in range(2020, 2025) for m in range(1, 13)}
    monkeypatch.setattr(fr.lab, "hold", lambda cache, span, attach=True: market)
    monkeypatch.setattr(fr.lab, "tier_members", lambda m, s: {"top30": months, "rank31_100": {k: [] for k in months}})
    monkeypatch.setattr(fr.xsb, "_in_sandbox", lambda: True)
    for name, value in (("NULLS", 6), ("PLANT_SEEDS", 5), ("MIN_YEAR_TRADES", 1)):
        monkeypatch.setattr(fr, name, value)
    cell = ["--tier", "top30", "--ratio", "3", "--cache", str(tmp_path)]

    def run(command, *more):
        code = fr.main([command, *cell, *more])
        printed = capsys.readouterr()
        path = tmp_path / f"{command}.json"
        if code == fr.EXIT_OK:
            path.write_text(printed.out)
        return code, printed, str(path)

    code, printed, counts = run("fixed-counts")
    body = json.loads(printed.out)
    assert code == fr.EXIT_OK and body["reads_profit_or_loss"] is False and not body["counts"]["stop"]["stops"]
    assert body["last_day_read"] == "2024-12-31" and "win" not in json.dumps(body["counts"])
    code, printed, null = run("fixed-null", "--counts", counts)
    body = json.loads(printed.out)
    assert code == fr.EXIT_OK and len(body["books"]) == 6 and body["reads_real_entries"] is False
    assert all(0 < b["signals_kept"] and b["trades"] > 0 for b in body["books"]) and body["break_even_before_cost"] == 0.25
    code, printed, plant = run("fixed-plant", "--counts", counts, "--null", null)
    body = json.loads(printed.out)
    assert code == fr.EXIT_OK and len(body["rows"]) == 5 * 5 and {r["size"] for r in body["rows"]} == {"lift_0.00", "lift_0.03", "lift_0.05", "lift_0.08", "thomas_line"}
    assert not body["seen"]["size_zero_valid"], "six nulls cannot put a seed under 0.05: size zero is not chance's number"
    code, printed, _ = run("fixed-real", "--counts", counts, "--null", null, "--plant", plant)
    assert code == fr.EXIT_BLOCKED and printed.out == "" and "the plant is broken" in printed.err, "nothing is read"
    monkeypatch.setattr(fr.sl, "SENS_FALSE_ALARM", (0, 10))
    code, printed, plant = run("fixed-plant", "--counts", counts, "--null", null)
    assert code == fr.EXIT_OK and json.loads(printed.out)["seen"]["size_zero_valid"]
    code, printed, _ = run("fixed-real", "--counts", counts, "--null", null, "--plant", plant)
    body = json.loads(printed.out)
    assert code == fr.EXIT_OK and body["reads_real_entries"] and body["verdict"]["judged"] and body["thomas_line"] == 0.3333
    assert {"annual_sharpe", "annual_pct", "win_rate", "mean_net_r", "ends", "years_pct"} <= set(body["book"])
    assert body["verdict"]["passes"] == bool(body["verdict"]["2a_entry"] and body["verdict"]["2b_later_years"]["passes"]
                                             and body["verdict"]["2c_beside_nulls"]["passes"] and body["verdict"]["3_stability"]["passes"])
    monkeypatch.setattr(fr.sl, "L6_UNUSABLE_PCT", -1e9)
    code, printed, _ = run("fixed-real", "--counts", counts, "--null", null, "--plant", plant)
    assert code == fr.EXIT_BLOCKED and printed.out == "" and "market's drift" in printed.err
