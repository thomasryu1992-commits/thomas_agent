from __future__ import annotations

import random
from datetime import date, timedelta

import pytest

from scripts.research import cross_section_basket as xsb
from scripts.research import signal_lab as lab
from scripts.research import strategy_ladder as sl
from scripts.research import tree_search as ts


def test_the_inputs_are_the_eleven_unstruck_daily_bar_variables():
    names = ts.inputs()
    assert sorted(names) == ["illiquidity_30", "listing_age", "momentum_change_30", "momentum_position_30",
                             "residual_momentum_30", "spread_14", "spread_30", "spread_7", "spread_90",
                             "spread_position_30", "volume_growth_30"]
    registered = lab.variables()
    assert all(registered[n][0] in ts.INPUT_GROUPS for n in names)
    assert not set(names) & set(ts.struck()), "no struck or swept-out variable is an input"
    for group in ("funding", "positioning", "spot", "premium", "trade_flow", "hourly"):
        assert not any(registered[n][0] == group for n in names), "daily bars only (Thomas 2026-11-19, Q1)"


def test_every_struck_book_is_compared_in_one_market_or_named_as_without_files():
    groups = set(ts.struck().values())
    covered = set().union(*ts.OVERLAP_GROUPS.values())
    assert groups - covered == {"depth"}
    assert "momentum_30" in ts.struck() and "extreme_days_30" in ts.struck()


def test_the_null_inputs_are_fresh_fixed_draws_with_a_value_only_on_a_bar():
    a, b, plant = ts.null_inputs(0), ts.null_inputs(1), ts.null_inputs(0, ts.PLANT_SEED)
    assert len(a) == len(b) == 11 and not set(a) & set(ts.inputs())
    market = xsb.Market()
    market.bars["X"] = {date(2021, 3, 7): object(), date(2021, 3, 14): object()}
    read = a[sorted(a)[0]]
    assert read(market, "X", date(2021, 3, 7)) == read(market, "X", date(2021, 3, 14)), "fixed per coin"
    assert read(market, "X", date(2021, 3, 21)) is None, "no bar, no value"
    values = lambda reads: [r(market, "X", date(2021, 3, 7)) for _n, r in sorted(reads.items())]  # noqa: E731
    assert len(set(values(a) + values(b) + values(plant))) == 33, "every seed and the plant draw afresh"


def test_percentiles_share_ties_and_span_zero_to_one():
    p = ts.percentiles({"A": 1.0, "B": 2.0, "C": 2.0, "D": 5.0})
    assert p == pytest.approx({"A": 0.125, "B": 0.5, "C": 0.5, "D": 0.875})
    assert [ts.binned(v) for v in (0.0, 0.19, 0.2, 0.5, 0.99, 1.0)] == [0, 0, 1, 2, 4, 4]


def _rows(n, seed=1):
    rng = random.Random(seed)
    xs, ys = [], []
    for _ in range(n):
        x = tuple(rng.randrange(5) for _ in range(4))
        y = 0.5 + (0.3 if (x[0] >= 3 and x[1] < 2) else 0.0) + rng.gauss(0, 0.05)    # only the two together
        xs.append(x)
        ys.append(y)
    return xs, ys


def test_the_boosting_finds_an_interaction_and_is_deterministic():
    xs, ys = _rows(3000)
    model = ts.fit(xs, ys)
    assert model == ts.fit(xs, ys)
    inside = ts.predict(model, (4, 0, 2, 2))
    outside = [ts.predict(model, x) for x in ((4, 4, 2, 2), (0, 0, 2, 2), (0, 4, 2, 2))]
    assert inside > max(outside) + 0.1
    for tree in model[1]:
        assert tree[0] == "split" and all(child[0] in ("split", "leaf") for child in tree[3:])


def test_no_leaf_holds_fewer_than_the_minimum_rows():
    xs, ys = _rows(450)
    _base, trees = ts.fit(xs, ys)

    def sizes(node, idx):
        if node[0] == "leaf":
            return [len(idx)]
        return (sizes(node[3], [i for i in idx if xs[i][node[1]] < node[2]])
                + sizes(node[4], [i for i in idx if xs[i][node[1]] >= node[2]]))
    assert all(s >= ts.MIN_LEAF for t in trees for s in sizes(t, range(len(xs))))


class _Tier:
    def __init__(self, days, members):
        self.days, self._members = days, members

    def members(self, day):
        return self._members

    def forward(self, day):
        return {}


def _table(days, targets, *, missing=()):
    members = [f"S{i}" for i in range(60)]
    rng = random.Random(4)
    values = {(s, d): rng.random() for s in members for d in days}
    read = lambda market, s, d: values[(s, d)]     # noqa: E731
    market = xsb.Market()
    market.bars = {s: {d: object() for d in days if (s, d) not in missing} for s in members}
    return ts.Table(market, _Tier(days, members), {"x1": read, "x2": read}, forward=lambda d: targets[d])


def test_a_member_without_a_bar_is_not_a_row():
    days = [date(2021, 1, 3), date(2021, 1, 10)]
    targets = {d: {f"S{i}": float(i) for i in range(60)} for d in days}
    table = _table(days, targets, missing={("S5", days[1])})
    assert len(table.rows[days[0]]) == 60 and "S5" not in {s for s, _x, _y in table.rows[days[1]]}


def test_year_y_is_scored_by_a_model_that_saw_no_target_window_ending_after_y():
    days = [date(2020, 1, 5) + timedelta(days=7 * k) for k in range(5 * 52)]
    rng = random.Random(9)
    targets = {d: {f"S{i}": rng.random() for i in range(60)} for d in days}
    before = ts.walk_forward(_table(days, targets))
    moved = {d: ({s: -v for s, v in t.items()} if d + timedelta(days=8) > date(2022, 1, 1) else t)
             for d, t in targets.items()}
    after = ts.walk_forward(_table(days, moved))
    same = lambda y: {d: v for d, v in before.items() if d.year == y} == {d: v for d, v in after.items() if d.year == y}  # noqa: E731
    assert same(2021) and same(2022), "a window ending after 2022-01-01 enters no model before 2023's"
    assert not same(2023)
    assert min(before).year == 2021, "2020 decisions are rows, never scored"
    table = _table(days, targets)
    cut = max(d for d in table.rows if d + timedelta(days=8) <= date(2021, 1, 1))
    assert len(table.training(2021)[1]) == 60 * sum(1 for d in table.rows if d <= cut)


def test_the_book_is_read_on_the_48_months_of_2021_to_2024():
    assert len(ts.months()) == 48 and ts.months()[0] == "2021-01" and ts.months()[-1] == "2024-12"
    rows = {m: {"net": 0.01} for m in ts.months()}
    rows["2020-06"] = {"net": 5.0}
    report = ts.reading(rows)
    assert report["months"] == 48 and report["years_total_position_pct"]["2021"] == pytest.approx(6.0)


def test_the_null_p_counts_every_null_book():
    assert ts.null_p(1.0, [0.5] * 199 + [None]) == pytest.approx(1 / 201)
    assert ts.null_p(None, [0.0]) is None


def test_the_commands_refuse_outside_the_sandbox_and_without_their_inputs(monkeypatch, tmp_path):
    assert ts.main(["tree-time"]) == ts.EXIT_USAGE
    monkeypatch.setattr(ts.xsb, "_in_sandbox", lambda: True)
    monkeypatch.setenv("RESEARCH_CACHE", str(tmp_path))
    assert ts.main(["tree-null", "--seeds", "0:201"]) == ts.EXIT_USAGE
    assert ts.main(["tree-plant", "--seeds", "0:5"]) == ts.EXIT_USAGE
    assert ts.main(["tree-overlap"]) == ts.EXIT_USAGE
    nulls = tmp_path / "n.json"
    nulls.write_text('{"books": {%s}}' % ",".join(f'"{k}": {{"annual_sharpe": 0.1}}' for k in range(150)))
    assert ts.main(["tree-book", "--null", str(nulls), "--nulls", "200"]) == ts.EXIT_USAGE
    assert ts.main(["tree-book", "--null", str(nulls), "--nulls", "150"]) == ts.EXIT_USAGE, "needs the overlap first"
    bars, pos = tmp_path / "b.json", tmp_path / "p.json"
    bars.write_text('{"market": "bars", "passes": false}')
    pos.write_text('{"market": "positioning", "passes": true}')
    assert ts.main(["tree-book", "--null", str(nulls), "--nulls", "150", "--overlap", f"{bars},{pos}"]) == ts.EXIT_BLOCKED
    assert ts.main(["tree-book", "--null", str(nulls), "--nulls", "150", "--overlap", str(pos)]) == ts.EXIT_USAGE
    assert ts.main(["tree-time"]) == ts.EXIT_BLOCKED, "no contract types in an empty cache"


def test_the_tier_is_the_sweeps_and_the_cost_is_its_stressed_one():
    assert ts.TIER == sl.TIER_SWEEP_TIER == "rank31_100"
