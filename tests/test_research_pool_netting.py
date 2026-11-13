"""Netting stage 1 (NETTING_STAGE1_DERIVED_BOOK_V0.1, Thomas 2026-11-13): only the allowed fields survive the load,
the three weightings on hand-built books, what counts as netted volume, and the sandbox door. Synthetic rows only."""

from __future__ import annotations

import json

import pytest

from scripts.research import pool_netting as pn

SECRET = 987654.321          # a result that must never reach a report


def _row(who, symbol, direction, opened, closed, *, per_r=50.0, seeded=False):
    """A forward-book row shaped like the lake's, carrying the fields the computation may not read. ``per_r`` is
    the notional per 1R it should come out with: the entry price is 100 and the risk is whatever gives that."""
    return json.dumps({
        "candidate_id": who, "symbol": symbol, "timeframe": "1h", "direction": direction,
        "opened_at_utc": f"2026-10-01T{opened:02d}:00:00Z", "created_at_utc": f"2026-10-01T{closed:02d}:00:00Z",
        "seeded": seeded, "entry_price": 100.0, "risk": 100.0 / per_r,
        "result_R": SECRET, "gross_result_R": SECRET, "win_loss": "WIN", "exit_price": SECRET,
        "close_reason": "TARGET", "fee_cost_r": SECRET})


def _all(book, variant):
    return pn.measure(pn.load(book))[variant]["all_symbols"]


def test_only_the_allowed_fields_survive_the_load_and_no_result_reaches_the_report():
    assert set(pn.ALLOWED) == {"candidate_id", "symbol", "timeframe", "direction", "opened_at_utc",
                               "created_at_utc", "seeded", "entry_price", "risk"}
    row = _row("A", "BTCUSDT", "LONG", 0, 10)
    assert set(json.loads(row, object_pairs_hook=pn._only_allowed)) == set(pn.ALLOWED)
    positions = pn.load([row])
    # The two added fields are used once, for their ratio, and do not travel on.
    assert set(positions[0]) == {"who", "symbol", "sign", "open", "close", "seeded", "per_r"}
    assert positions[0]["per_r"] == pytest.approx(50.0)
    flat = json.dumps(pn.report(positions))
    assert str(SECRET) not in flat and "987654" not in flat


@pytest.mark.parametrize("patch, reason", [
    ({"entry_price": None}, "left_out_no_entry_price_or_risk"),
    ({"risk": 0}, "left_out_no_entry_price_or_risk"),
    ({"risk": True}, "left_out_no_entry_price_or_risk"),
    ({"entry_price": float("nan")}, "left_out_no_entry_price_or_risk"),
    ({"direction": "FLAT"}, "left_out_no_direction"),
    ({"created_at_utc": "2026-10-01T00:00:00Z"}, "left_out_no_time_held"),
])
def test_a_row_that_cannot_be_sized_is_left_out_by_name(patch, reason):
    row = {**json.loads(_row("A", "BTCUSDT", "LONG", 0, 10)), **patch}
    positions, seen = pn.read([json.dumps(row)])
    assert positions == [] and seen[reason] == 1 and seen["rows"] == 1


# One bet: three longs on one symbol, A 0–12, B 2–8, C 4–6, each 50 of notional per 1R.
THREE_LONGS = [_row("A", "BTCUSDT", "LONG", 0, 12), _row("B", "BTCUSDT", "LONG", 2, 8),
               _row("C", "BTCUSDT", "LONG", 4, 6)]


def test_as_today_every_strategy_is_one_r_and_risk_is_the_count():
    v0 = _all(THREE_LONGS, "v0")
    assert v0["open_risk_r"]["max"] == 3.0 and v0["largest_bet_r"] == 3.0
    assert v0["notional_in_1r_money"]["gross"]["max"] == 150.0
    assert v0["resize_orders"] == {"count": 0, "turnover_in_1r_money": 0.0}


def test_under_the_equal_split_a_bet_is_always_one_r_and_the_others_are_resized():
    v1 = _all(THREE_LONGS, "v1")
    assert v1["open_risk_r"]["max"] == 1.0 and v1["largest_bet_r"] == 1.0
    assert v1["notional_in_1r_money"]["gross"]["max"] == pytest.approx(50.0)
    # B's entry halves A; C's entry cuts A and B to a third; C's exit and B's exit grow them back.
    assert v1["resize_orders"]["count"] == 1 + 2 + 2 + 1
    assert v1["resize_orders"]["turnover_in_1r_money"] == pytest.approx(25 + 2 * (25 - 50 / 3) + 2 * (25 - 50 / 3) + 25)


def test_under_the_share_fixed_at_entry_nothing_is_resized_and_the_bet_can_pass_one_r():
    v2 = _all(THREE_LONGS, "v2")
    assert v2["resize_orders"] == {"count": 0, "turnover_in_1r_money": 0.0}
    assert v2["largest_bet_r"] == pytest.approx(1 + 1 / 2 + 1 / 3, abs=1e-4)
    slices = v2["order_sizes_usdt"]["strategy_slice_at_entry"]["equity_1000"]
    assert slices["count"] == 3
    # 1R is 1% of equity: A enters with 50 x 10 USDT, B with half of that, C with a third.
    assert (slices["p90"], slices["median"], slices["p10"]) == (500.0, 250.0, pytest.approx(166.67))


def test_strategies_entering_at_the_same_instant_count_each_other():
    together = [_row("A", "BTCUSDT", "LONG", 0, 6), _row("B", "BTCUSDT", "LONG", 0, 6)]
    assert _all(together, "v2")["largest_bet_r"] == 1.0
    assert _all(together, "v1")["resize_orders"]["count"] == 0


def test_opposite_positions_that_only_overlap_save_no_order_and_lower_the_net():
    """A long from 0 to 10 and a short from 4 to 8: four orders with netting and four without."""
    crossed = [_row("A", "BTCUSDT", "LONG", 0, 10), _row("B", "BTCUSDT", "SHORT", 4, 8)]
    for variant in pn.VARIANTS:
        out = _all(crossed, variant)
        assert out["netted_turnover_in_1r_money"] == 0.0 and out["fill_cost_saved_r"] == 0.0
        assert out["turnover_in_1r_money"] == 200.0
        assert out["notional_in_1r_money"]["gross"]["max"] == 100.0
        assert out["notional_in_1r_money"]["net"]["max"] == 50.0, "the net is largest while A is alone"
        assert out["open_risk_r"]["max"] == 2.0, "two bets, one per direction, in every weighting"


def test_volume_is_netted_only_where_opposite_changes_share_an_instant():
    """A long closes at 6 as another strategy's long opens at 6: a sell and a buy that never reach the market."""
    handover = [_row("A", "BTCUSDT", "LONG", 0, 6), _row("B", "BTCUSDT", "LONG", 6, 12)]
    out = _all(handover, "v0")
    assert out["turnover_in_1r_money"] == 200.0 and out["netted_turnover_in_1r_money"] == 100.0
    assert out["netted_share_of_turnover"] == 0.5
    assert out["fill_cost_saved_r"] == pytest.approx(100.0 * pn._fill_cost_fraction())
    # The same two an hour apart net nothing.
    apart = [_row("A", "BTCUSDT", "LONG", 0, 6), _row("B", "BTCUSDT", "LONG", 7, 12)]
    assert _all(apart, "v0")["netted_turnover_in_1r_money"] == 0.0
    # And a long and a short entering together net their entries and their exits.
    both = [_row("A", "BTCUSDT", "LONG", 0, 6), _row("B", "BTCUSDT", "SHORT", 0, 6)]
    assert _all(both, "v0")["netted_share_of_turnover"] == 1.0


def test_netting_is_per_symbol_and_never_across_symbols():
    two = [_row("A", "BTCUSDT", "LONG", 0, 6), _row("B", "ETHUSDT", "SHORT", 0, 6)]
    out = _all(two, "v0")
    assert out["netted_turnover_in_1r_money"] == 0.0
    assert out["notional_in_1r_money"]["net"]["max"] == 100.0 == out["notional_in_1r_money"]["gross"]["max"]


def test_a_strategys_second_row_inside_its_first_is_one_holder():
    doubled = [_row("A", "BTCUSDT", "LONG", 0, 10), _row("A", "BTCUSDT", "LONG", 2, 4), _row("A", "BTCUSDT", "LONG", 10, 12)]
    measured = pn.measure(pn.load(doubled))
    assert measured["rows_set_aside_as_one_strategys_second_position"] == 1 and measured["positions"] == 2
    assert measured["v0"]["all_symbols"]["open_risk_r"]["max"] == 1.0


def test_order_sizes_are_read_against_the_order_limit_at_both_assumed_equities():
    small = [_row("A", "BTCUSDT", "LONG", 0, 6, per_r=5.0)]          # 5 x 10 USDT = 50 at 1,000; 500 at 10,000
    sizes = _all(small, "v0")["order_sizes_usdt"]["strategy_slice_at_entry"]
    assert sizes["equity_1000"]["median"] == 50.0 and sizes["equity_1000"]["share_over_order_limit"] == 0.0
    assert sizes["equity_10000"]["median"] == 500.0 and sizes["equity_10000"]["share_over_order_limit"] == 1.0
    assert pn.RISK_PER_TRADE_FRACTION == 0.01, "the live leg's own fraction, imported, not typed again"


def test_the_fill_cost_is_half_the_cost_models_round_trip():
    from runtime.mvp_runtime.crypto import cost

    assert pn._fill_cost_fraction() == pytest.approx(cost.round_trip_cost_r("SHORT", 1.0, 1.0) / 2)


def test_the_report_gives_everything_with_the_seeded_rows_and_without_them():
    book = [_row("A", "BTCUSDT", "LONG", 0, 10), _row("B", "BTCUSDT", "LONG", 2, 8, seeded=True)]
    out = pn.report(pn.load(book))
    assert out["seeded_positions"] == 1 and out["display_only"] is True
    assert out["with_seeded"]["v0"]["all_symbols"]["open_risk_r"]["max"] == 2.0
    assert out["without_seeded"]["v0"]["all_symbols"]["open_risk_r"]["max"] == 1.0


def test_it_runs_in_the_sandbox_only_and_reads_the_pools_book(monkeypatch, capsys, tmp_path):
    (tmp_path / pn.SOURCE).write_text("\n".join(THREE_LONGS) + "\n", encoding="utf-8")
    monkeypatch.setattr(pn.xsb, "_in_sandbox", lambda: False)
    assert pn.main(["run", "--lake", str(tmp_path)]) == pn.EXIT_USAGE
    monkeypatch.setattr(pn.xsb, "_in_sandbox", lambda: True)
    assert pn.main(["run", "--lake", str(tmp_path / "nowhere")]) == pn.EXIT_BLOCKED
    capsys.readouterr()
    assert pn.main(["run", "--lake", str(tmp_path)]) == pn.EXIT_OK
    out = json.loads(capsys.readouterr().out)
    assert out["spec"] == pn.SPEC and out["rows"]["kept"] == 3 and out["fields_read"] == list(pn.ALLOWED)
    assert out["with_seeded"]["v1"]["all_symbols"]["largest_bet_r"] == 1.0
