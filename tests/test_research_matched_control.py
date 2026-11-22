"""L34, the matched control (STRATEGY_DISCOVERY_LOOP_V0.1 sections 134 and 135): the pools by stop distance, a
control set that keeps the real book's days and sides, the plant, the structure door, the check that the real book
is the one section 132 counted, and the stages. Synthetic markets only."""

from __future__ import annotations

import json
import random
import zipfile
from datetime import date, timedelta

import pytest

from scripts.research import cross_section_basket as xsb
from scripts.research import matched_control as mc
from scripts.research import two_outcome as tw

T = date(2021, 3, 1)


def _one(stop, outcome="loss", side=1, day=T, held=30):
    return {"side": side, "entry_day": day + timedelta(days=1), "entry": 100.0, "stop_frac": stop, "tie": False, "gap": False,
            "unreachable": False, "broken": False, "open_differs": False, "exit_day": day + timedelta(days=2),
            "exit": 100.0 * (1 + side * (3 * stop if outcome == "win" else -stop)), "outcome": outcome, "hours_held": held, "funding": 0.0}


def _table(stops, wins=(), days=(T,)):
    return {(coin, day, side): _one(stop, "win" if coin in wins else "loss", side, day)
            for coin, stop in stops.items() for day in days for side in (1, -1)}


def _real(coin, stop, side=1, day=T):
    return {**_one(stop, side=side, day=day), "symbol": coin, "signal_day": day, "notional": min(tw.RISK / stop, 1 / tw.MAX_OPEN)}


STOPS = {"A": 0.040, "B": 0.041, "C": 0.038, "D": 0.050, "E": 0.030, "F": 0.045, "G": 0.100, "H": 0.039}


def test_a_pool_is_the_five_members_of_the_day_nearest_in_stop_distance_never_the_trades_own_coin():
    made = _table(STOPS)
    made[("C", T, 1)] = None                                               # cannot be opened long that day
    members = {T: sorted(STOPS)}
    near = mc.pools(made, [_real("A", 0.040), _real("A", 0.040, side=-1), _real("G", 0.100)], members)
    assert near[0] == ["B", "H", "F", "D", "E"], "nearest first; A itself and the unopenable C are out"
    assert near[1] == ["B", "H", "C", "F", "D"], "the short side can open C"
    assert near[2] == ["D", "F", "B", "A", "H"] and mc.pools(made, [_real("A", 0.04, day=T + timedelta(days=9))], members) == [[]]


def test_a_control_set_keeps_each_trades_day_and_side_never_draws_a_coin_twice_a_day_and_may_overlap_across_days():
    days = (T, T + timedelta(days=1))
    made = _table(STOPS, days=days)
    members = {d: sorted(STOPS) for d in days}
    trades = [_real("A", 0.040), _real("B", 0.041, side=-1), _real("H", 0.039), _real("A", 0.040, day=days[1])]
    near = mc.pools(made, trades, members)
    one = mc.control(made, trades, near, 3, "top30", 3)
    assert [(d, s) for _, d, s in one] == [(t["signal_day"], t["side"]) for t in trades], "same days, same sides, same order"
    assert all(coin != t["symbol"] and coin in pool for (coin, _, _), t, pool in zip(one, trades, near))
    assert len({coin for coin, d, _ in one if d == T}) == 3, "no coin twice on one day"
    assert one == mc.control(made, trades, near, 3, "top30", 3)
    assert len({tuple(mc.control(made, trades, near, seed, "top30", 3)) for seed in range(30)}) > 5, "the seed is the draw"
    crowded = [_real("A", 0.040)] * 7
    short = mc.control(made, crowded, mc.pools(made, crowded, members), 1, "top30", 3)
    assert len(short) == 5 and len({c for c, *_ in short}) == 5, "a pool of five is used up: the other trades have no control"


def test_the_plant_prefers_a_winning_coin_when_the_pool_has_one_and_lambda_zero_is_the_plain_draw():
    made = _table(STOPS, wins={"B"})
    members = {T + timedelta(days=k): sorted(STOPS) for k in range(400)}
    made = _table(STOPS, wins={"B"}, days=tuple(members))
    trades = [_real("A", 0.040, day=d) for d in members]
    near = mc.pools(made, trades, members)
    rate = lambda picks: sum(1 for c, *_ in picks) and sum(1 for c, *_ in picks if c == "B") / len(picks)      # noqa: E731
    plain = rate(mc.control(made, trades, near, 5, "top30", 3))
    assert plain == pytest.approx(0.2, abs=0.06), "one winner among five, drawn evenly"
    assert rate(mc.control(made, trades, near, 5, "top30", 3, 1.0)) == 1.0, "always the winner when there is one"
    half = rate(mc.control(made, trades, near, 5, "top30", 3, 0.5))
    assert plain < half < 1.0 and half == pytest.approx(0.6, abs=0.08)
    none = _table(STOPS, days=tuple(members))
    assert rate(mc.control(none, trades, near, 5, "top30", 3, 1.0)) == pytest.approx(0.2, abs=0.06), \
        "no winner in the pool: an even draw, so B is drawn a fifth of the time"


def test_a_matched_trade_is_booked_as_the_real_book_books_its_own():
    made = _table({"B": 0.04, "C": 0.01}, wins={"B"})
    out = mc.settled(made, [("B", T, 1), ("C", T, -1)], "rank31_100")
    won, capped = out["trades"]
    assert won["notional"] == pytest.approx(0.0625) and capped["notional"] == pytest.approx(1 / tw.MAX_OPEN)
    cost = 2 * 26 / 10_000
    assert out["net"][T + timedelta(days=2)] == pytest.approx(0.0625 * (0.12 - cost) + (1 / 6) * (-0.01 - cost))
    assert won["cost_r"] == pytest.approx(cost / 0.04) and out["mean_open_at_entry"] is None
    shaped = mc.shape(out, 4)
    assert shaped["matched_share"] == 0.5 and shaped["capped_share"] == 0.5 and shaped["time_out_share"] == 0.0
    assert (shaped["stacked_share"], shaped["most_open"]) == (0.0, 2)
    again = mc.settled(_table({"B": 0.04}, days=(T, T + timedelta(days=1), T + timedelta(days=5))),
                       [("B", T, 1), ("B", T + timedelta(days=1), 1), ("B", T + timedelta(days=5), 1)], "top30")
    assert mc.overlap(again["trades"]) == {"stacked_share": pytest.approx(1 / 3), "most_open": 2}, \
        "the second trade opens on a coin the set still holds; the third does not"


def test_the_structure_door_has_four_lines_and_each_one_alone_stops_the_cell():
    real = {"trades": 1000, "stop_median": 0.040, "capped_share": 0.02, "median_hours_held": 17}
    good = {"matched_share": 0.99, "stop_median": 0.041, "capped_share": 0.03, "time_out_share": 0.01, "median_hours_held": 30,
            "stacked_share": 0.2, "most_open": 14}
    assert mc.structure(real, [good] * 3)["ok"]
    for key, value, line in (("matched_share", 0.94, "matched"), ("stop_median", 0.045, "stop_distance"),
                             ("capped_share", 0.08, "capped"), ("time_out_share", 0.06, "time_outs")):
        out = mc.structure(real, [{**good, key: value}] * 3)
        assert not out["ok"] and out["inside"][line] is False and sum(out["inside"].values()) == 3
    loose = mc.structure(real, [{**good, "median_hours_held": 300, "stacked_share": 0.9, "most_open": 80}] * 3)
    assert loose["ok"] and loose["control_median"]["most_open"] == 80, "how long the controls last and how they overlap is written down, not judged"


def test_the_real_book_must_be_the_one_section_132_counted():
    want = mc.L33_COUNTS[("top30", 3)]
    counts = {"trades": want["trades"], "time_out_share": want["time_out_share"],
              "by_year": {y: {"trades": n, "long": 0, "short": 0} for y, n in want["by_year"].items()}}
    assert mc.same_book(counts, "top30", 3) and not mc.same_book(counts, "top30", 5)
    assert not mc.same_book({**counts, "trades": want["trades"] + 1}, "top30", 3)
    assert not mc.same_book({**counts, "time_out_share": 0.0040}, "top30", 3)
    assert {k: v["trades"] for k, v in mc.L33_COUNTS.items()} == {("top30", 3): 3550, ("top30", 5): 2742, ("rank31_100", 3): 3816, ("rank31_100", 5): 2901}
    assert all(sum(v["by_year"].values()) == v["trades"] for v in mc.L33_COUNTS.values())


def test_the_plant_is_read_at_the_sets_win_rate_and_the_door_is_held_to_twelve():
    def rows(size, passes, under, rate):
        return [{"size": size, "p": 0.01 if k < under else 0.5, "passes": k < passes, "win_rate": rate} for k in range(mc.PLANT_SEEDS)]
    base = rows("lambda_0.00", 0, 5, 0.25) + rows("lambda_0.10", 40, 70, 0.30)
    out = mc.seen(base + rows("lambda_0.15", 85, 95, 0.34) + rows("lambda_0.20", 99, 100, 0.38), 1 / 3)
    assert out["size_zero_valid"] and out["smallest_size_at_the_line"] == "lambda_0.15" and out["sees_the_line"]
    assert not mc.seen(base + rows("lambda_0.15", 79, 95, 0.34), 1 / 3)["sees_the_line"]
    assert not mc.seen(base, 1 / 3)["plant_reaches_the_line"]
    assert not mc.seen(rows("lambda_0.00", 9, 30, 0.25) + rows("lambda_0.15", 100, 100, 0.40), 1 / 3)["sees_the_line"]
    high = mc.seen(rows("lambda_0.00", 0, 5, 0.22) + rows("lambda_0.05", 20, 40, 0.24) + rows("lambda_0.40", 100, 100, 0.40), 0.20)
    assert high["controls_already_at_the_line"] and high["sees_the_line"] is None and high["plant_reaches_the_line"] is None, \
        "another member already wins a fifth of the time: the line is not above the control, and the plant cannot ask"
    assert high["smallest_size_at_the_line"] is None and not out["controls_already_at_the_line"]
    drift = mc.fr.line([float(k) / 10 for k in range(100)])
    book = {"annual_sharpe": 1.4, "annual_pct": 30.0, "years_pct": {str(y): 30.0 for y in range(2020, 2025)}, "win_rate": 0.1}
    assert mc.verdict(book, [1.5] + [0.2] * 599, drift)["passes"], "one control set at or above it: p 2/601 is under 0.05/12"
    out = mc.verdict(book, [1.5] * 2 + [0.2] * 598, drift)
    assert not out["passes"] and out["2c_beside_controls"]["alpha"] == 0.05 / 12


def _zip(folder, name, rows):
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(folder / name, "w") as z:
        z.writestr("x.csv", "".join(",".join(map(str, r)) + "\n" for r in rows))


def test_the_stages_run_in_order_on_the_counted_book_and_refuse_through_the_structure_door_or_a_broken_plant(monkeypatch, tmp_path, capsys):
    coins = [f"K{k}USDT" for k in range(7)]
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
    monkeypatch.setattr(mc.lab, "hold", lambda cache, span, attach=True: market)
    monkeypatch.setattr(mc.fr.lab, "tier_members", lambda m, s: {"top30": months, "rank31_100": {k: [] for k in months}})
    monkeypatch.setattr(mc.xsb, "_in_sandbox", lambda: True)
    monkeypatch.setattr(mc, "SETS", 6)
    monkeypatch.setattr(mc, "PLANT_SEEDS", 5)
    monkeypatch.setattr(mc.fr, "MIN_YEAR_TRADES", 1)
    cell = ["--tier", "top30", "--ratio", "3", "--cache", str(tmp_path)]

    def run(command, *more):
        code = mc.main([command, *cell, *more])
        printed = capsys.readouterr()
        path = tmp_path / f"{command}.json"
        if code == mc.EXIT_OK:
            path.write_text(printed.out)
        return code, printed, str(path)

    code, printed, _ = run("match-counts")
    assert code == mc.EXIT_BLOCKED and printed.out == "" and "not the one section 132.1 counted" in printed.err
    days = mc.fr.member_days(market, "top30", mc.SPAN)
    tally = tw.counted(tw.book(tw.table(market, tmp_path / "archive", days, 3, mc.SPAN), mc.fr.signals(market, days), "top30"))
    monkeypatch.setitem(mc.L33_COUNTS, ("top30", 3), {"trades": tally["trades"], "time_out_share": tally["time_out_share"],
                                                      "by_year": {y: v["trades"] for y, v in tally["by_year"].items()}})
    code, printed, counts = run("match-counts")
    body = json.loads(printed.out)
    assert code == mc.EXIT_OK and body["reads_profit_or_loss"] is False and body["real_trades"] == tally["trades"]
    assert body["last_hour_read"] == "2024-12-31T23:00Z" and body["pool_sizes"]["5"] == tally["trades"], "six other members every day"
    monkeypatch.setattr(mc, "MIN_MATCHED", 2.0)
    code, printed, shaped = run("match-structure", "--counts", counts)
    body = json.loads(printed.out)
    assert code == mc.EXIT_OK and body["reads_profit_or_loss"] is False and not body["structure"]["ok"] and "win" not in json.dumps(body)
    assert mc.main(["match-null", *cell, "--counts", counts, "--structure", shaped]) == mc.EXIT_USAGE
    assert "inside every line" in capsys.readouterr().err
    for name, value in (("MIN_MATCHED", 0.0), ("STOP_BAND", 9.0), ("CAPPED_BAND", 9.0), ("MAX_TIME_SHARE", 1.0)):
        monkeypatch.setattr(mc, name, value)
    code, printed, shaped = run("match-structure", "--counts", counts)
    assert code == mc.EXIT_OK and json.loads(printed.out)["structure"]["ok"]
    code, printed, null = run("match-null", "--counts", counts, "--structure", shaped)
    body = json.loads(printed.out)
    assert code == mc.EXIT_OK and len(body["sets"]) == 6 and body["reads_real_book"] is False
    assert all(s["trades"] == tally["trades"] for s in body["sets"]), "every real trade has its control"
    code, printed, plant = run("match-plant", "--counts", counts, "--structure", shaped, "--null", null)
    body = json.loads(printed.out)
    assert code == mc.EXIT_OK and len(body["rows"]) == 5 * len(mc.LAMBDAS) and not body["seen"]["size_zero_valid"]
    rates = {s: v["win_rate_median"] for s, v in body["seen"]["sizes"].items()}
    assert rates["lambda_0.40"] > rates["lambda_0.00"], "the plant lifts the set's win rate"
    code, printed, _ = run("match-real", "--counts", counts, "--structure", shaped, "--null", null, "--plant", plant)
    assert code == mc.EXIT_BLOCKED and printed.out == "" and "the plant is broken" in printed.err
    monkeypatch.setattr(mc.sl, "SENS_FALSE_ALARM", (0, 10))
    code, printed, plant = run("match-plant", "--counts", counts, "--structure", shaped, "--null", null)
    assert code == mc.EXIT_OK and json.loads(printed.out)["seen"]["size_zero_valid"]
    code, printed, _ = run("match-real", "--counts", counts, "--structure", shaped, "--null", null, "--plant", plant)
    body = json.loads(printed.out)
    assert code == mc.EXIT_OK and body["reads_real_book"] and body["book"]["trades"] == tally["trades"]
    assert body["verdict"]["2c_beside_controls"]["alpha"] == 0.05 / 12 and body["thomas_line"] == 0.3333
    monkeypatch.setattr(mc.sl, "L6_UNUSABLE_PCT", -1e9)
    code, printed, _ = run("match-real", "--counts", counts, "--structure", shaped, "--null", null, "--plant", plant)
    assert code == mc.EXIT_BLOCKED and printed.out == "" and "day and side it shares" in printed.err
    monkeypatch.setattr(mc.xsb, "_in_sandbox", lambda: False)
    assert mc.main(["match-counts", *cell]) == mc.EXIT_USAGE


def test_the_fixed_values_are_the_registered_ones():
    assert (mc.DIVISOR, mc.NEAREST, mc.SETS, mc.PLANT_SEEDS) == (12, 5, 600, 100) and mc.RATIOS == (3, 5)
    assert mc.LAMBDAS == (0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40)
    assert (mc.MIN_MATCHED, mc.STOP_BAND, mc.CAPPED_BAND, mc.MAX_TIME_SHARE) == (0.95, 0.10, 0.05, 0.05)
    assert (tw.STOP_ATR, tw.RISK, tw.MAX_OPEN, tw.HOLD_HOURS) == (0.5, 0.0025, 6, 720), "the real book's values are L33's"
