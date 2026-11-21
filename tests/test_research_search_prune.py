"""L24 (SEARCH_THEN_PRUNE_V0.1): the combination chosen on earlier years, read beside the same search on noise."""

from __future__ import annotations

import json
import random
from datetime import date, timedelta

import pytest

from scripts.research import cross_section_basket as xsb
from scripts.research import search_prune as sp
from scripts.research import strategy_ladder as sl

COINS = [f"C{i:02d}USDT" for i in range(20)]


class FakeView:
    """A tier with weekly decisions over the span, twenty coins and a forward week per coin and decision. A book's
    'Sharpe' is the summed score-weighted forward return, so a better score reads higher."""

    def __init__(self, seed: int = 1, members=COINS) -> None:
        rng = random.Random(seed)
        start = date(2020, 1, 5)
        self.ic_days = [start + timedelta(days=7 * k) for k in range(261)]
        self._members = list(members)
        self.book_days = list(self.ic_days)
        self.market = None                      # the top tier's noise needs no bar
        self.fwd = {d: {s: rng.gauss(0.0, 0.05) for s in self._members} for d in self.ic_days}
        self.reports: list[dict] = []

    def turnover(self, read):
        return 0.0

    def members(self, day):
        return tuple(self._members)

    def days(self):
        return list(self.ic_days)

    def forward(self, day):
        return self.fwd.get(day, {})

    def report(self, read):
        total = {}
        for d in self.ic_days:
            for s in self._members:
                v = read(None, s, d)
                if v is not None:
                    total[str(d.year)] = total.get(str(d.year), 0.0) + v * self.fwd[d][s]
        value = round(sum(total.values()), 6) if total else None
        out = {"annual_sharpe": value, "years_total_position_pct": {y: round(v, 4) for y, v in total.items()},
               "clears_entry": False, "monthly": [v for _, v in sorted(total.items())]}
        self.reports.append(out)
        return out


def _ranks(view: FakeView, names: dict) -> dict:
    """``names``: {name: function(forward, rng) -> value}."""
    rng = random.Random(7)
    return {n: {d: sp.centred_ranks({s: f(view.fwd[d][s], rng) for s in view._members}) for d in view.ic_days}
            for n, f in names.items()}


PLANTED = {"good": lambda f, rng: f + rng.gauss(0, 0.05),
           "bad": lambda f, rng: -f + rng.gauss(0, 0.05),
           **{f"noise{k}": (lambda f, rng: rng.gauss(0, 1)) for k in range(8)}}


def test_centred_ranks_spread_over_minus_one_to_one_and_ties_share_a_rank():
    assert sp.centred_ranks({}) == {}
    assert sp.centred_ranks({"a": 5.0}) == {"a": 0.0}
    assert sp.centred_ranks({"a": 1.0, "b": 2.0, "c": 3.0}) == {"a": -1.0, "b": 0.0, "c": 1.0}
    tied = sp.centred_ranks({"a": 1.0, "b": 1.0, "c": 3.0, "d": 4.0})
    assert tied["a"] == tied["b"] == pytest.approx(-2 / 3) and tied["d"] == 1.0


def test_a_year_learns_only_from_weeks_that_end_before_it():
    days = [date(2020, 1, 5) + timedelta(days=7 * k) for k in range(261)]
    ics = {d: (0.1 if d.year < 2022 else -5.0) for d in days}
    ics.update({d: 0.1 + 0.01 * (k % 3) for k, d in enumerate(days) if d.year < 2022})
    learned = sp.trained(ics, 2022)
    assert learned["mean"] > 0 and learned["n"] == sum(1 for d in days if d + timedelta(days=8) <= date(2021, 12, 31))
    late = date(2021, 12, 26)
    assert late in ics and late + timedelta(days=8) > date(2021, 12, 31), "a week running into the year is left out"
    assert sp.trained({d: 0.1 for d in days[:10]}, 2022) is None, "under twenty decisions: no reading"
    assert sp.trained({d: 0.1 for d in days[:40]}, 2022) is None, "no spread: no t"


def test_the_configurations_pick_by_t_and_sign_and_are_eighteen_over_three_tiers():
    stats = {"a": {"mean": 0.02, "t": 3.0}, "b": {"mean": -0.03, "t": -4.0}, "c": {"mean": 0.01, "t": 1.0}, "d": None}
    assert sp.choose(stats, ("top", 2)) == {"b": -1.0, "a": 1.0}
    assert sp.choose(stats, ("top", 20)) == {"b": -1.0, "a": 1.0, "c": 1.0}, "fewer eligible than k: all of them"
    assert sp.choose(stats, ("t2", None)) == {"b": -1.0, "a": 1.0}
    assert sp.choose(stats, ("weighted", None)) == {"a": 0.02, "b": -0.03, "c": 0.01}
    assert len(sp.CONFIGS) * len(sp.TIERS) == 18 and sp.TRAINED_YEARS == (2022, 2023, 2024)


def test_a_score_needs_half_its_components_and_exists_only_in_traded_years():
    day, early = date(2022, 3, 6), date(2021, 3, 7)
    ranks = {"a": {day: {"X": 1.0, "Y": -1.0}, early: {"X": 1.0}}, "b": {day: {"X": -1.0}}, "c": {day: {"X": 1.0}}}
    out = sp.scores(ranks, {2022: {"a": 1.0, "b": -1.0, "c": 1.0}}, [day, early])
    assert early not in out, "2021 is never traded"
    assert out[day]["X"] == pytest.approx((1 + 1 + 1) / 3)
    assert "Y" not in out[day], "one of three components is under half"
    weighted = sp.scores(ranks, {2022: {"a": 0.03, "b": -0.01}}, [day])
    assert weighted[day]["X"] == pytest.approx((0.03 * 1 + -0.01 * -1) / 0.04)
    assert sp.scores(ranks, {2022: {}}, [day]) == {}, "no component: no book that year"


def test_the_search_finds_a_planted_variable_on_both_sides_and_never_reads_the_year_it_trades():
    view = FakeView()
    ranks = _ranks(view, PLANTED)
    found = sp.search(view, ranks, sorted(ranks))
    for year in ("2022", "2023", "2024"):
        assert found["top3"]["chosen"][year]["good"] == 1.0 and found["top3"]["chosen"][year]["bad"] == -1.0
    assert found["top3"]["annual_sharpe"] > 0
    assert set(found) == {"top3", "top5", "top10", "top20", "t2", "weighted"}
    # Rewrite everything from 2023 on: what 2023 chose cannot move.
    later = FakeView()
    for d in later.ic_days:
        later.fwd[d] = dict(view.fwd[d]) if d.year < 2023 else {s: random.Random(str(d) + s).gauss(0, 0.05) for s in COINS}
    moved = {n: {d: (r if d.year < 2023 else sp.centred_ranks({s: random.random() for s in COINS})) for d, r in per.items()}
             for n, per in ranks.items()}
    again = sp.search(later, moved, sorted(moved))
    for config in found:
        assert again[config]["chosen"]["2023"] == found[config]["chosen"]["2023"], config
        assert again[config]["chosen"]["2022"] == found[config]["chosen"]["2022"], config


def test_the_noise_search_draws_fresh_variables_every_repeat_seeded_by_tier_and_repeat():
    view = FakeView()
    one = sp.noise(view, "top30", 6, range(0, 3))
    two = sp.noise(view, "top30", 6, range(0, 3))
    assert one == two and one["repeats"] == [0, 3] and [r["repeat"] for r in one["rows"]] == [0, 1, 2]
    assert set(one["rows"][0]["configs"]) == {sp.config_name(c) for c in sp.CONFIGS}
    assert one["rows"][0]["scored_book_days"]["top3"][1] > 0
    day, coin = view.ic_days[0], COINS[0]
    a, b = sp.noise_names("top30", 0, 6), sp.noise_names("top30", 1, 6)
    assert [f(None, coin, day) for f in a.values()] != [f(None, coin, day) for f in b.values()], "fresh every repeat"
    market = xsb.Market(bars={coin: {day: xsb.Bar(1.0, 1.0, 1.0)}}, funding={})
    assert sp.noise_variable("top30", 0, 0, "fixed")(market, coin, day) != \
        sp.noise_variable("rank31_100", 0, 0, "fixed")(market, coin, day), "seeded by tier too"
    with pytest.raises(ValueError):
        sp.noise(view, "top30", 6, range(199, 201))


def test_the_noise_trades_as_slowly_as_the_tiers_own_null():
    day, later = date(2022, 1, 2), date(2022, 1, 9)
    top = sp.noise_names("top30", 0, 6)
    weekly = [f for n, f in top.items() if int(n[-2:]) < 3]
    fixed = [f for n, f in top.items() if int(n[-2:]) >= 3]
    assert all(f(None, "A", day) != f(None, "A", later) for f in weekly), "the top tier: half redrawn weekly"
    assert all(f(None, "A", day) == f(None, "A", later) for f in fixed), "and half fixed per coin, as its sweep null"
    market = xsb.Market(bars={"A": {day: xsb.Bar(1.0, 1.0, 1.0), later: xsb.Bar(1.0, 1.0, 1.0)}}, funding={})
    for tier in ("rank31_100", "rank101_liquid"):
        names = sp.noise_names(tier, 0, 6)
        assert all(f(market, "A", day) == f(market, "A", later) is not None for f in names.values()), tier
        assert all(f(market, "B", day) is None for f in names.values()), "no bar, no value (TierSweep.fixed)"


def test_noise_chunks_merge_only_into_every_repeat_once():
    chunk = lambda a, b, usable=("x",): {"tier": "top30", "drawn_per_repeat": 1, "usable": list(usable),  # noqa: E731
                                         "rows": [{"repeat": r, "configs": {}} for r in range(a, b)]}
    merged = sp.merge_noise([chunk(100, 200), chunk(0, 100)])
    assert merged["repeats"] == 200 and [r["repeat"] for r in merged["rows"]] == list(range(200))
    for bad in ([chunk(0, 100)], [chunk(0, 120), chunk(100, 200)], [chunk(0, 100), chunk(50, 150)],
                [chunk(0, 100), chunk(100, 200, ("y",))]):
        with pytest.raises(ValueError):
            sp.merge_noise(bad)


def _runs(real_best: dict, null_bests: list):
    noise = [{"tier": t, "repeats": len(null_bests), "rows": [{"configs": {"top3": (v if t == "top30" else None)}}
                                                               for v in null_bests]} for t in sp.TIERS]
    real = [{"tier": t, "configs": {"top3": {"annual_sharpe": real_best.get(t), "report": {"monthly": [0.01, 0.02, -0.01, 0.0, 0.03, 0.01],
                                                                                            "clears_entry": False}}}}
            for t in sp.TIERS]
    return noise, real


def test_the_reading_ranks_the_real_best_among_the_noise_bests_over_every_tier():
    noise, real = _runs({"top30": 0.4, "rank31_100": 0.9, "rank101_liquid": 0.1}, [k / 10 for k in range(20)])
    out = sp.reading(noise, real)
    assert out["best"]["tier"] == "rank31_100" and out["best_annual_sharpe"] == 0.9
    assert out["p"] == pytest.approx((1 + 11) / 21, abs=1e-4), "0.9 .. 1.9: eleven noise bests at or above"
    assert not out["passes_noise"]
    noise, real = _runs({"top30": 2.5}, [0.1 * k for k in range(20)])
    passed = sp.reading(noise, real)
    assert passed["p"] == pytest.approx(1 / 21, abs=1e-4) and passed["passes_noise"]
    with pytest.raises(ValueError):
        sp.reading(noise[:2], real)


def test_the_shown_numbers_move_the_right_way():
    rng = random.Random(3)
    monthly = [0.02 + rng.gauss(0, 0.03) for _ in range(36)]
    few, many = sp.deflated_sharpe(monthly, [0.5, 1.0, 1.5], 3), sp.deflated_sharpe(monthly, [0.5, 1.0, 1.5], 300)
    assert few is not None and many is not None and many < few, "more tries, less left"
    steady = {"a": [0.02] * 18 + [0.01] * 18, "b": [-0.01] * 36, "c": [0.0] * 36}
    steady["a"] = [v + 0.001 * (k % 2) for k, v in enumerate(steady["a"])]
    assert sp.overfit_probability(steady)["pbo"] == 0.0, "the best everywhere is never below the middle"
    assert sp.overfit_probability({"a": [0.1] * 12}) is None


def test_material_strikes_what_the_record_forbids_on_each_tier():
    top, mid, lower = sp.material("top30"), sp.material("rank31_100"), sp.material("rank101_liquid")
    for tier in (top, mid, lower):
        assert not {"vol_30", "spread_change_30", "trade_size_position_30"} & set(tier), "S3"
        assert not {n for n, g in tier.items() if g == "depth"}
    assert {"funding_30", "taker_buy_share_30", "momentum_30", "oi_growth_30"}.isdisjoint(mid)
    assert {"momentum_7", "funding_30", "ls_level_30"}.isdisjoint(lower)
    assert {"momentum_30", "funding_30", "ls_level_30"} <= set(top)
    assert (len(top), len(mid), len(lower)) == (47, 38, 40), "L24's material as measured: the later windows add nothing"
    assert all(sp.struck(f"spot_taker_share_{w}", tier) for w in (7, 14, 90) for tier in sp.TIERS)


def test_ranks_101_on_use_only_material_at_the_coverage_floor():
    coverage = {"spot_share_30": {"cells": 79, "with_bar": 100}, "momentum_change_30": {"cells": 80, "with_bar": 100},
                "listing_age": {"cells": 0, "with_bar": 0}}
    assert sp.usable(coverage, "rank101_liquid") == ["momentum_change_30"]
    assert sp.usable(coverage, "top30") == ["momentum_change_30", "spot_share_30"], "no floor on the top tiers"


def test_values_are_read_for_every_member_on_every_day_and_counted():
    days = [date(2022, 1, 2), date(2022, 1, 9)]

    bars = {"A": {d: xsb.Bar(1.0, 1.0, 1.0) for d in days}, "B": {days[1]: xsb.Bar(1.0, 1.0, 1.0)}}

    class Tiny:
        market = xsb.Market(bars=bars, funding={})

        def days(self):
            return days

        def members(self, day):
            return ("A", "B")

    out = sp.values(Tiny(), {"x": lambda m, s, d: 1.0 if d in m.bars[s] else None})
    assert out["values"]["x"] == {"2022-01-02": {"A": 1.0}, "2022-01-09": {"A": 1.0, "B": 1.0}}
    assert out["coverage"]["x"] == {"cells": 3, "with_bar": 3} and out["members"]["2022-01-02"] == ["A", "B"]


def _file(tmp_path, name, tier, group, values, members=None):
    path = tmp_path / name
    path.write_text(json.dumps({"tier": tier, "group": group, "members": members or {"2022-01-02": ["A"]},
                                "values": values, "coverage": {n: {"cells": 1, "with_bar": 1} for n in values}}))
    return path


def test_a_tier_loads_once_per_group_with_one_set_of_members(tmp_path):
    groups = sp.material_groups("top30")
    files = [_file(tmp_path, f"{g}.json", "top30", g, {f"{g}_x": {"2022-01-02": {"A": 1.0}}}) for g in groups]
    loaded = sp.load_tier(files, "top30", lambda n: n.startswith("spot_x"))
    assert set(loaded["ranks"]) == {"spot_x"} and len(loaded["coverage"]) == len(groups)
    with pytest.raises(ValueError):
        sp.load_tier(files[:-1], "top30", lambda n: True)
    with pytest.raises(ValueError):
        sp.load_tier(files + [_file(tmp_path, "dup.json", "top30", "spot", {"spot_x": {}})], "top30", lambda n: True)
    with pytest.raises(ValueError):
        sp.load_tier(files[:-1] + [_file(tmp_path, "m.json", "top30", groups[-1], {"c": {}}, {"2022-01-02": ["B"]})],
                     "top30", lambda n: True)
    with pytest.raises(ValueError):
        sp.load_tier([_file(tmp_path, "t.json", "rank31_100", "spot", {})], "top30", lambda n: True)


def test_the_real_search_stays_locked_without_its_decision_and_its_recorded_noise(monkeypatch, tmp_path):
    monkeypatch.setattr(sp, "REAL_SEARCH_DECIDED", False)
    assert sp.main(["real", "--tier", "top30", "--values", "x.json", "--noise", "n.json", "--cache", str(tmp_path)]) == sp.EXIT_BLOCKED
    monkeypatch.setattr(sp, "REAL_SEARCH_DECIDED", True)
    assert sp.main(["real", "--tier", "top30", "--values", "x.json", "--cache", str(tmp_path)]) == sp.EXIT_USAGE
    assert sp.main(["noise", "--tier", "top30", "--cache", str(tmp_path)]) == sp.EXIT_USAGE
    assert sp.main(["values", "--tier", "top30", "--only", "depth", "--cache", str(tmp_path)]) == sp.EXIT_USAGE


def test_the_top_tiers_book_reads_as_the_ladder_measures_it(monkeypatch):
    # L9T needs a reference coin and volatility history; the arithmetic under test is the reading, so L1 stands in.
    monkeypatch.setitem(sl.BOOKS, sl.SWEEP_LOGIC, sl.book)
    start, end = date(2022, 1, 1), date(2022, 12, 31)
    days = [start - timedelta(days=60) + timedelta(days=k) for k in range((end - start).days + 61)]
    rng = random.Random(5)
    bars = {}
    for i in range(40):
        price, out = 100.0, {}
        for d in days:
            move = rng.gauss(0, 0.02)
            out[d] = xsb.Bar(open=price, close=price * (1 + move), quote_volume=1e9 - i * 1e6)
            price *= 1 + move
        bars[f"C{i:02d}USDT"] = out
    row = [(int((days[0] - date(1970, 1, 1)).total_seconds() * 1000) + 3600 * 1000, 0.0)]
    market = xsb.Market(bars=bars, funding={s: list(row) for s in bars})
    read = lambda m, s, d: (hash((s, d.month)) % 97) / 97.0  # noqa: E731
    view = sp.View(market, "top30", (start, end))
    mine = view.report(read)
    theirs = sl.measure(market, sl.BOOKS[sl.SWEEP_LOGIC](read, lambda _d: 1), (start, end),
                        simulate=sl.ACCOUNTING[sl.SWEEP_ACCOUNTING])
    assert mine["annual_sharpe"] is not None and mine["months"] == 12
    for key in ("annual_sharpe", "total_position_annual_pct", "clears_entry", "years_total_position_pct", "months", "sum_pct"):
        assert mine[key] == theirs[key], key
    # The same book read from the tabulated ranks: every day the book reads is in the table.
    table = sp.rank_table(sp.values(view, {"x": read})["values"])["x"]
    tabled = view.report(lambda m, s, d: table.get(d, {}).get(s))
    for key in ("annual_sharpe", "total_position_annual_pct", "years_total_position_pct", "months"):
        assert tabled[key] == mine[key], key
    assert sp.members_differ(view, sp.values(view, {"x": read})["members"]) == []
    assert sp.members_differ(view, {**sp.values(view, {"x": read})["members"], "2030-01-06": []}) == ["2030-01-06"]


# --- the planted door (section 71) ----------------------------------------------------------------------------------

def test_a_planted_variable_at_size_zero_ranks_as_the_noise_it_replaced():
    view = FakeView()
    pool = sp.pool_names("top30", 0, 6)
    fixed = [n for i, n in enumerate(sorted(pool)) if i in sp.planted_indices("top30", 6, 3)]
    made = {n: sp.planted_variable(view, pool[n], 0.0) for n in fixed}
    assert sp.tabulate(view, made) == sp.tabulate(view, {n: pool[n] for n in fixed})
    ran = sp.plant(view, "top30", 6, range(0, 1), sizes=(0.0,), counts=(1, 3))
    plain = sp.search(view, sp.tabulate(view, pool), sorted(pool))
    assert ran["rows"][0]["sizes"]["0"]["configs"] == {c: v["annual_sharpe"] for c, v in plain.items()}


def test_planted_variables_replace_only_fixed_noise_and_never_enter_the_registry():
    assert sp.planted_indices("top30", 47, 5) == [42, 43, 44, 45, 46]
    assert all(sp.noise_kind("top30", 47, i) == "fixed" for i in sp.planted_indices("top30", 47, 5))
    with pytest.raises(ValueError):
        sp.planted_indices("top30", 6, 5)
    names = set(sp.pool_names("top30", 0, 47))
    assert not names & set(sp.lab.variables()) and not names & set(sp.material("top30"))
    assert sp.PLANT_TIER == "top30" and sp.PLANT_SEEDS == 50 and sp.PLANT_COUNTS == (1, 5)
    assert sp.PLANT_SIZES == (0.0, 0.02, 0.03, 0.04, 0.05, 0.065, 0.08, 0.10)


def test_the_planted_pools_seed_space_is_apart_from_the_recorded_noise():
    day, coin = date(2022, 1, 2), "AUSDT"
    recorded = sp.noise_variable("top30", 0, 0, "fixed")(None, coin, day)
    assert recorded == int(xsb.random_key("L24|noise|top30|0|0|fixed", date(2000, 1, 1), coin)[:12], 16) / 16 ** 12, \
        "the recorded noise's seeds do not move"
    pool = [f(None, coin, day) for f in sp.pool_names("top30", 0, 6).values()]
    noise = [f(None, coin, day) for f in sp.noise_names("top30", 0, 6).values()]
    assert not set(pool) & set(noise)


def test_a_planted_effect_is_found_and_lifts_the_search():
    view = FakeView()
    ran = sp.plant(view, "top30", 10, range(0, 2), sizes=(0.0, 0.6), counts=(1, 3))
    for row in ran["rows"]:
        strong = row["sizes"]["0.6"]
        assert strong["3"]["planted_mean_ic"] > 0.4 and strong["3"]["planted_chosen_years"] == 3
        assert strong["3"]["best_sharpe"] > row["sizes"]["0"]["best_sharpe"]
        assert row["sizes"]["0"]["planted_chosen_years"] == 0, "size 0 plants nothing"
        assert row["single"]["0.6"]["mean_ic"] > 0.4 and row["single"]["0.6"]["annual_sharpe"] > 0
    with pytest.raises(ValueError):
        sp.plant(view, "top30", 10, range(49, 51))


def test_the_replay_names_recorded_repeats_that_no_longer_match():
    view = FakeView()
    recorded = sp.noise(view, "top30", 6, range(0, 3))
    assert sp.replay(view, "top30", 6, recorded) == []
    recorded["rows"][1]["configs"] = {**recorded["rows"][1]["configs"], "top3": 9.9}
    assert sp.replay(view, "top30", 6, recorded) == [1]


def _plant_rows(best_by_size):
    """``best_by_size``: {size: [best per seed]} for count 1; count 5 is the same."""
    rows = []
    for seed in range(sp.PLANT_SEEDS):
        sizes = {"0": {"best_sharpe": best_by_size[0.0][seed]}}
        for size, bests in best_by_size.items():
            if size:
                cell = {"best_sharpe": bests[seed], "clears_entry": bests[seed] > 1.0, "later_years": True,
                        "planted_chosen_years": 3, "planted_mean_ic": size, "total_position_annual_pct": 20.0,
                        "turnover": 0.5}
                sizes[sp.size_key(size)] = {"1": cell, "5": cell}
        rows.append({"seed": seed, "sizes": sizes,
                     "single": {sp.size_key(s): {"annual_sharpe": 0.5, "total_position_annual_pct": 5.0, "mean_ic": s}
                                for s in best_by_size if s}})
    return rows


def test_the_planted_reading_uses_the_readings_p_and_brackets_at_eighty_percent():
    nulls = [k / 100 for k in range(200)]                 # 0.00 .. 1.99: p < 0.05 needs a best above 1.89
    noise = [{"tier": t, "repeats": 200, "rows": [{"configs": {"top3": (v if t == "top30" else None)}} for v in nulls]}
             for t in sp.TIERS]
    n = sp.PLANT_SEEDS
    best = {0.0: [0.5] * (n - 3) + [1.95] * 3, 0.02: [1.0] * n, 0.03: [1.95] * 30 + [1.0] * 20, 0.04: [1.95] * 40 + [1.0] * 10,
            0.05: [1.95] * n}
    sizes = tuple(best)
    merged = {"tier": "top30", "drawn_per_seed": 47, "usable": [], "replay_differs": [], "rows": _plant_rows(best)}
    out = sp.plant_reading(merged, noise, sizes=sizes)
    assert out["doors"] == {"replay_differs": [], "replay_holds": True, "false_alarms_at_zero": 3, "false_alarms_within": True}
    table = out["by_count"]["1"]["sizes"]
    assert table["0.02"]["passes"] == 0.0 and table["0.03"]["passes"] == 0.6 and table["0.04"]["passes"] == 0.8
    assert sp.noise_p(1.95, sorted(nulls)) == pytest.approx((1 + 5) / 201) and (1 + 5) / 201 < sp.NOISE_ALPHA
    assert sp.noise_p(1.89, sorted(nulls)) == pytest.approx((1 + 11) / 201)
    assert out["by_count"]["5"]["bracket"] == {"below": "0.03", "reaches": "0.04"}
    best[0.0] = [1.95] * 8 + [0.5] * (n - 8)
    loud = sp.plant_reading({**merged, "replay_differs": [2], "rows": _plant_rows(best)}, noise, sizes=sizes)
    assert not loud["doors"]["false_alarms_within"] and not loud["doors"]["replay_holds"]
    with pytest.raises(ValueError):
        sp.merge_plant([{"tier": "top30", "drawn_per_seed": 47, "usable": [], "rows": _plant_rows(best)[:-1]}])
