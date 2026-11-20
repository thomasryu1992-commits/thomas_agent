from __future__ import annotations

import json
from datetime import date

import pytest

from scripts.research import composite as cp
from scripts.research import cross_section_basket as xsb
from scripts.research import signal_lab as lab
from scripts.research import strategy_ladder as sl


def test_the_components_are_every_unstruck_variable_with_a_recorded_side():
    registered = lab.variables()
    unstruck = {n for n in registered if not (n in sl.TIER_SWEEP_OUT or lab.no_book(n, cp.TIER))}
    assert set(cp.COMPONENTS) == unstruck - {"top_change_30", "top_position_30"}, "the two have no 31-100 sweep book"
    assert len(cp.COMPONENTS) == 41 and set(cp.COMPONENTS.values()) == {"high", "low"}
    assert not set(cp.COMPONENTS) & set(cp.struck())
    assert all(cp.load_of(registered[n][0]) for n in cp.COMPONENTS), "every component has a load"


def test_the_score_is_the_equal_weight_mean_of_signed_ranks():
    tables = {"premium_7": {"2025-01-05": {"A": 0.2, "B": 0.8}}, "illiquidity_30": {"2025-01-05": {"A": 0.9}}}
    read = cp.composite(tables, ["premium_7", "illiquidity_30"])
    day = date(2025, 1, 5)
    assert read(None, "A", day) == pytest.approx(((1 - 0.2) + 0.9) / 2), "premium_7 is low-side, illiquidity high"
    assert read(None, "B", day) == pytest.approx(((1 - 0.8) + 0.5) / 2), "a missing component counts as the middle"
    assert read(None, "C", day) is None, "no component value: not ranked"


def test_percentiles_share_ties_and_centre_the_ranks():
    assert cp.percentiles({"a": 1.0, "b": 2.0}) == {"a": 0.25, "b": 0.75}
    assert cp.percentiles({"a": 1.0, "b": 1.0, "c": 3.0}) == pytest.approx({"a": 1 / 3, "b": 1 / 3, "c": 5 / 6})


def test_the_counts_keep_components_by_coverage_alone():
    files = [{"span": "confirm", "ranks": {"coverage": {n: 0.9 for n in cp.COMPONENTS}}}]
    files[0]["ranks"]["coverage"]["premium_7"] = 0.79
    out = cp.counts(files)
    assert "premium_7" in out["left_out"] and len(out["kept"]) == 40 and out["stop"]["stops"] is False
    short = [{"span": "confirm", "ranks": {"coverage": {"premium_7": 0.9}}}]
    assert cp.counts(short)["stop"]["stops"] is True, "a component without a file stops the counts"
    with pytest.raises(ValueError):
        cp.counts([{"span": "search", "ranks": {"coverage": {}}}])


def test_the_ranks_files_merge_by_span_and_never_twice():
    a = {"span": "search", "ranks": {"variables": {"premium_7": {}}}}
    b = {"span": "search", "ranks": {"variables": {"premium_7": {}}}}
    with pytest.raises(ValueError):
        cp.merged([a, b], "search")
    with pytest.raises(ValueError):
        cp.merged([a], "confirm")
    assert cp.merged([a], "search") == {"premium_7": {}}


def test_the_struck_ranks_rebuild_the_same_book_order():
    tables = {"momentum_30": {"2021-01-03": {"A": 0.1, "B": 0.5, "C": 0.9}}}
    read = cp.table_read(tables, "momentum_30")
    values = {s: read(None, s, date(2021, 1, 3)) for s in "ABC"}
    assert sorted(values, key=values.get) == ["A", "B", "C"]


def test_the_read_refuses_out_of_order_before_its_date_and_unregistered(monkeypatch, tmp_path):
    assert cp.main(["composite-counts"]) == cp.EXIT_USAGE
    monkeypatch.setattr(cp.xsb, "_in_sandbox", lambda: True)
    monkeypatch.setenv("RESEARCH_CACHE", str(tmp_path))

    def put(name, body):
        path = tmp_path / name
        path.write_text(json.dumps(body))
        return str(path)
    ok = put("c.json", {"counts": {"stop": {"stops": False}, "kept": ["premium_7"]}})
    passed = put("o.json", {"passes": True, "blocked": False})
    blocked = put("b.json", {"passes": False, "blocked": True})
    closed = put("x.json", {"passes": False, "blocked": False})
    assert cp.main(["composite-ranks", "--load", "bars"]) == cp.EXIT_USAGE
    assert cp.main(["composite-overlap"]) == cp.EXIT_USAGE, "needs the counts"
    assert cp.main(["composite-read", "--counts", ok, "--overlap", blocked]) == cp.EXIT_BLOCKED
    assert cp.main(["composite-read", "--counts", ok, "--overlap", closed]) == cp.EXIT_BLOCKED
    assert cp.main(["composite-read", "--counts", ok, "--overlap", passed], today=date(2026, 12, 12)) == cp.EXIT_USAGE
    registered = sl.CONFIRM_READS
    assert registered[3] == {**registered[3], "read": 4, "candidate": "L31"}, "read 4 is registered for L31"
    monkeypatch.setattr(sl, "CONFIRM_READS", registered[:3])
    assert cp.main(["composite-read", "--counts", ok, "--overlap", passed], today=date(2026, 12, 13)) == cp.EXIT_USAGE, \
        "read 4 must be registered for L31 in CONFIRM_READS first"
    monkeypatch.setattr(sl, "CONFIRM_READS", registered)
    monkeypatch.setattr(sl, "CONFIRM_DONE", {**sl.CONFIRM_DONE, 4: "a run"})
    assert cp.main(["composite-read", "--counts", ok, "--overlap", passed], today=date(2026, 12, 13)) == cp.EXIT_USAGE, \
        "a spent read 4 is refused"
    monkeypatch.setattr(sl, "CONFIRM_DONE", {k: v for k, v in sl.CONFIRM_DONE.items() if k != 4})
    assert cp.main(["composite-read", "--counts", ok, "--overlap", passed], today=date(2026, 12, 13)) == cp.EXIT_BLOCKED, \
        "registered and dated: it reaches the cache (empty here)"
    assert cp.main(["composite-ranks", "--load", "bars", "--span", "search"]) == cp.EXIT_BLOCKED


def test_the_read_is_the_fourth_at_its_bar():
    assert cp.READ_NUMBER == 4 and sl.confirm_bar(4) == pytest.approx(1.483, abs=1e-3)
    assert cp.CONFIRM == (date(2025, 1, 1), date(2026, 9, 30))
