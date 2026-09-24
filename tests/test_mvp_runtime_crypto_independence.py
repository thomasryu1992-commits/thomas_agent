"""Independent bets over forward records (PORTFOLIO_INDEPENDENCE_V0.1, decided 2026-09-24): a
measurement, pure and deterministic, with a shuffled-days baseline beside every figure."""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import independence as ind


def _rows(cid, pnl_by_day, *, direction="LONG"):
    return [{"candidate_id": cid, "created_at_utc": f"2026-09-{day:02d}T00:00:00Z", "result_R": r,
             "direction": direction} for day, r in pnl_by_day.items()]


PATTERN = {1: 1.0, 3: -1.0, 4: 2.0, 7: -1.0, 9: 1.5, 12: -0.5, 15: 1.0, 18: -1.0}


def test_copies_of_one_series_are_one_bet():
    rows = [r for cid in ("a", "b", "c") for r in _rows(cid, PATTERN)]
    census = ind.independence(rows)
    assert census["lineages"] == 3 and census["effective_bets"] == pytest.approx(1.0)
    assert census["mean_corr_same_direction"] == pytest.approx(1.0)
    # the baseline scrambles each copy's days separately, so it sees more than one bet
    assert census["baseline_effective_bets"] > 1.5


def test_series_that_never_trade_on_the_same_day_are_nearly_independent():
    rows = (_rows("a", {1: 1.0, 2: -1.0, 3: 1.0, 4: -1.0, 5: 1.0})
            + _rows("b", {11: 1.0, 12: -1.0, 13: 1.0, 14: -1.0, 15: 1.0}, direction="SHORT"))
    census = ind.independence(rows)
    assert census["effective_bets"] > 1.9
    assert census["mean_corr_same_direction"] is None and census["by_direction"]["SHORT"]["effective_bets"] is None


def test_a_lineage_under_the_row_floor_is_left_out_and_under_two_is_none():
    rows = _rows("a", PATTERN) + _rows("b", {1: 1.0, 2: 1.0})
    assert ind.daily_series(rows)["ids"] == ["a"]
    assert ind.independence(rows) is None


def test_the_census_is_deterministic_and_names_the_most_alike_same_direction_pairs():
    rows = [r for cid in ("a", "b") for r in _rows(cid, PATTERN)] + _rows(
        "c", {k: -v for k, v in PATTERN.items()}, direction="SHORT")
    first, second = ind.independence(rows), ind.independence(rows)
    assert first == second
    assert first["top_same_direction_pairs"] == [{"a": "a", "b": "b", "corr": 1.0}]
    assert first["mean_corr_opposite_direction"] == pytest.approx(-1.0)


def test_a_flat_series_correlates_with_nothing():
    assert ind.correlation([0.0, 0.0, 0.0], [1.0, -1.0, 2.0]) == 0.0
