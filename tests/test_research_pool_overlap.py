"""The pool overlap measurement (POOL_OVERLAP_MEASUREMENT_V0.1, Thomas 2026-11-12): only the allowed fields survive
the load, the seven counts on a hand-built book, and the sandbox door. Synthetic rows only."""

from __future__ import annotations

import json

import pytest

from scripts.research import pool_overlap as po

SECRET = 987654.321          # a result that must never reach a report


def _row(who, symbol, direction, opened, closed, *, timeframe="1h", seeded=False):
    """A forward-book row shaped like the lake's, carrying the fields the measurement may not read."""
    return json.dumps({
        "candidate_id": who, "symbol": symbol, "timeframe": timeframe, "direction": direction,
        "opened_at_utc": f"2026-10-01T{opened:02d}:00:00Z", "created_at_utc": f"2026-10-01T{closed:02d}:00:00Z",
        "seeded": seeded, "result_R": SECRET, "gross_result_R": SECRET, "win_loss": "WIN", "entry_price": SECRET,
        "exit_price": SECRET, "close_reason": "TARGET", "fee_cost_r": SECRET, "risk": SECRET})


# BTC: A long 0–10, B long 4–8 (another timeframe), C short 6–12.  ETH: D long 2–6.  SOL: E short 20–22 (seeded).
BOOK = [
    _row("A", "BTCUSDT", "LONG", 0, 10),
    _row("B", "BTCUSDT", "LONG", 4, 8, timeframe="4h"),
    _row("C", "BTCUSDT", "SHORT", 6, 12),
    _row("D", "ETHUSDT", "LONG", 2, 6),
    _row("E", "SOLUSDT", "SHORT", 20, 22, seeded=True),
]


def test_only_the_allowed_fields_survive_the_load_and_no_result_reaches_the_report():
    assert po.ALLOWED == ("candidate_id", "symbol", "timeframe", "direction", "opened_at_utc", "created_at_utc", "seeded")
    kept = json.loads(BOOK[0], object_pairs_hook=po._only_allowed)
    assert set(kept) == set(po.ALLOWED)                                   # the result, prices and reason are gone at parse
    positions = po.load(BOOK)
    assert len(positions) == 5 and set(positions[0]) == {"who", "symbol", "timeframe", "sign", "open", "close", "seeded"}
    flat = json.dumps(po.report(positions))
    assert "987654" not in flat and "WIN" not in flat and "TARGET" not in flat
    assert po.SOURCE == "forward_outcomes.jsonl" and "cohort" not in po.SOURCE          # the pool's book, never the cohort's
    broken = ['{not json', json.dumps({"symbol": "BTCUSDT"}),                # no strategy, no times
              _row("Z", "BTCUSDT", "SIDEWAYS", 1, 2), _row("Z", "BTCUSDT", "LONG", 5, 3)]   # no direction; closes before it opens
    positions, rows = po.read(broken + BOOK + [json.dumps({"candidate_id": "T", "symbol": "BTCUSDT", "direction": "LONG",
                                                             "opened_at_utc": "2026-10-02T00:00:00Z",
                                                             "created_at_utc": "2026-10-02T01:00:00Z"}), "", "[1, 2]"])
    assert rows == {"rows": 11, "kept": 6, "kept_without_a_timeframe": 1, "left_out_unreadable_line": 1,
                    "left_out_no_strategy_or_symbol": 2, "left_out_no_direction": 1,
                    "left_out_closes_before_it_opens": 1}                 # every row is kept or named
    assert positions[-1]["timeframe"] is None and po.load(broken) == []
    assert po.read([json.dumps({"candidate_id": "T", "symbol": "BTCUSDT", "direction": "LONG"})])[1][
        "left_out_no_open_or_close_time"] == 1


def test_the_counts_on_a_hand_built_symbol():
    btc = po.symbol_counts([p for p in po.load(BOOK) if p["symbol"] == "BTCUSDT"])
    # 0–4 one open, 4–6 two, 6–8 three, 8–10 two, 10–12 one: 12 hours in all
    assert btc["share_of_time_by_open_count"] == {"1": round(6 / 12, 4), "2": round(4 / 12, 4), "3+": round(2 / 12, 4)}
    assert btc["most_open_at_once"] == 3 and btc["strategies"] == 3 and btc["positions"] == 3
    assert btc["overlapped_share_of_time"] == round(6 / 12, 4)
    inside = btc["inside_overlap"]
    assert inside["same_direction_share"] == round(2 / 6, 4)              # only 4–6 is all long
    assert inside["mixed_direction_share"] == round(4 / 6, 4)
    assert inside["several_timeframes_share"] == round(4 / 6, 4)          # B's 4h is in from 4 to 8
    # net over gross: 4–6 is 2/2, 6–8 is |1+1-1|/3, 8–10 is |1-1|/2
    assert inside["mean_net_over_gross"] == pytest.approx((2 * 1 + 2 * (1 / 3) + 2 * 0) / 6, abs=1e-4)
    assert btc["entries"] == {"alone": 1, "behind_same_direction": 1, "against_opposite_direction": 1, "into_mixed": 0,
                              "together_same_direction": 0, "together_not_same_direction": 0,
                              "share_arriving_while_another_was_open": round(2 / 3, 4),
                              "share_opening_together_with_another": 0.0}
    assert btc["one_strategy_overlapping_itself"] == 0
    assert btc["span"] == ["2026-10-01T00:00:00Z", "2026-10-01T12:00:00Z"]


def test_a_strategy_counts_once_however_many_of_its_rows_touch():
    twice = po.symbol_counts(po.load([_row("A", "BTCUSDT", "LONG", 0, 10), _row("A", "BTCUSDT", "LONG", 5, 7)]))
    assert twice["one_strategy_overlapping_itself"] == 1                  # reported, and not counted as another strategy
    assert twice["entries"]["alone"] == 2
    assert twice["share_of_time_by_open_count"] == {"1": 1.0} and twice["overlapped_share_of_time"] == 0.0
    assert twice["most_open_at_once"] == 1
    whole = po.portfolio_counts(po.load([_row("A", "BTCUSDT", "LONG", 0, 10), _row("A", "BTCUSDT", "LONG", 5, 7),
                                         _row("A", "ETHUSDT", "LONG", 5, 7)]))
    assert whole["most_open_at_once"] == 2                                # once per symbol, not once in all


def test_entries_at_the_same_instant_are_together_and_neither_is_behind_the_other():
    rows = [_row("A", "BTCUSDT", "LONG", 0, 5), _row("B", "BTCUSDT", "LONG", 0, 6), _row("C", "BTCUSDT", "LONG", 8, 9),
            _row("D", "BTCUSDT", "SHORT", 8, 9), _row("E", "BTCUSDT", "LONG", 3, 4)]
    entries = po.symbol_counts(po.load(rows))["entries"]
    assert entries["together_same_direction"] == 2 and entries["together_not_same_direction"] == 2
    assert entries["behind_same_direction"] == 1 and entries["alone"] == 0
    assert entries["share_arriving_while_another_was_open"] == 0.2
    assert entries["share_opening_together_with_another"] == 0.8
    # one already in decides the bucket, whoever else arrives at that instant
    late = po.symbol_counts(po.load([_row("A", "BTCUSDT", "SHORT", 0, 9), _row("B", "BTCUSDT", "LONG", 2, 5),
                                     _row("C", "BTCUSDT", "LONG", 2, 5)]))["entries"]
    assert late["against_opposite_direction"] == 2 and late["together_same_direction"] == 0
    mixed = po.symbol_counts(po.load([_row("A", "BTCUSDT", "SHORT", 0, 9), _row("D", "BTCUSDT", "LONG", 1, 9),
                                      _row("B", "BTCUSDT", "LONG", 2, 5), _row("C", "BTCUSDT", "LONG", 2, 5)]))["entries"]
    assert mixed["into_mixed"] == 2
    own = po.symbol_counts(po.load([_row("A", "BTCUSDT", "LONG", 0, 5), _row("A", "BTCUSDT", "LONG", 0, 6)]))["entries"]
    assert own["alone"] == 2                                              # its own second row is not another strategy


def test_a_missing_timeframe_is_not_a_second_timeframe():
    rows = [_row("A", "BTCUSDT", "LONG", 0, 5), json.dumps({"candidate_id": "B", "symbol": "BTCUSDT", "direction": "LONG",
            "opened_at_utc": "2026-10-01T01:00:00Z", "created_at_utc": "2026-10-01T04:00:00Z"})]
    assert po.symbol_counts(po.load(rows))["inside_overlap"]["several_timeframes_share"] == 0.0


def test_an_entry_into_both_directions_is_mixed():
    rows = [_row("A", "BTCUSDT", "LONG", 0, 10), _row("B", "BTCUSDT", "SHORT", 1, 10), _row("C", "BTCUSDT", "LONG", 2, 3)]
    assert po.symbol_counts(po.load(rows))["entries"]["into_mixed"] == 1


def test_two_shorts_together_are_as_one_sided_as_two_longs():
    shorts = po.symbol_counts(po.load([_row("A", "BTCUSDT", "SHORT", 0, 10), _row("B", "BTCUSDT", "SHORT", 2, 8)]))
    assert shorts["inside_overlap"]["mean_net_over_gross"] == 1.0         # the size of the net, not its sign
    assert shorts["inside_overlap"]["same_direction_share"] == 1.0


def test_all_symbols_together_count_positions_and_symbol_direction_groups():
    whole = po.portfolio_counts([p for p in po.load(BOOK) if not p["seeded"]])
    # 0–2: 1 position, 1 group. 2–4: 2, 2. 4–6: 3 positions (A, B, D), 2 groups. 6–8: 3 (A, B, C), 2 groups.
    # 8–10: 2 (A, C), 2 groups. 10–12: 1, 1.
    assert whole["most_open_at_once"] == 3 and whole["most_groups_at_once"] == 2
    assert whole["share_of_time_by_open_positions"] == {"1": round(4 / 12, 4), "2": round(4 / 12, 4), "3": round(4 / 12, 4)}
    assert whole["share_of_time_by_symbol_direction_groups"] == {"1": round(4 / 12, 4), "2": round(8 / 12, 4)}


def test_the_report_gives_every_count_with_the_seeded_rows_and_without_them():
    report = po.report(po.load(BOOK))
    assert report["seeded_positions"] == 1 and report["display_only"] is True
    assert set(report["with_seeded"]["by_symbol"]) == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
    assert set(report["without_seeded"]["by_symbol"]) == {"BTCUSDT", "ETHUSDT"}
    assert report["with_seeded"]["positions"] == 5 and report["without_seeded"]["positions"] == 4
    assert report["without_seeded"]["span"] == ["2026-10-01T00:00:00Z", "2026-10-01T12:00:00Z"]
    # the gap between BTC's last close (12) and SOL's seeded position (20) is time with nothing open
    assert report["with_seeded"]["all_symbols"]["share_of_time_by_open_positions"]["0"] == round(8 / 22, 4)
    flat = json.dumps(report).lower()
    assert all(word not in flat for word in ("sharpe", "result", "pnl", "verdict", '"pass"'))


def test_it_runs_in_the_sandbox_only_and_reads_the_pools_book(monkeypatch, capsys, tmp_path):
    assert po.main(["run", "--lake", str(tmp_path)]) == po.EXIT_USAGE
    assert "sandbox" in capsys.readouterr().err
    monkeypatch.setattr(po.xsb, "_in_sandbox", lambda: True)
    monkeypatch.delenv("RESEARCH_LAKE", raising=False)
    assert po.main(["run"]) == po.EXIT_BLOCKED                           # no lake given
    assert po.main(["run", "--lake", str(tmp_path)]) == po.EXIT_BLOCKED  # no book there
    (tmp_path / "forward_cohort_outcomes.jsonl").write_text("\n".join(BOOK), encoding="utf-8")
    assert po.main(["run", "--lake", str(tmp_path)]) == po.EXIT_BLOCKED  # the cohort's file is not the source
    (tmp_path / po.SOURCE).write_text("{broken\n", encoding="utf-8")
    assert po.main(["run", "--lake", str(tmp_path)]) == po.EXIT_BLOCKED  # nothing readable
    capsys.readouterr()
    (tmp_path / po.SOURCE).write_text("\n".join(BOOK) + "\n", encoding="utf-8")
    monkeypatch.setenv("RESEARCH_LAKE", str(tmp_path))
    assert po.main(["run"]) == po.EXIT_OK
    out = json.loads(capsys.readouterr().out)
    assert out["spec"] == "POOL_OVERLAP_MEASUREMENT_V0.1" and out["fields_read"] == list(po.ALLOWED)
    assert out["with_seeded"]["strategies"] == 5 and out["lake_snapshot"] == tmp_path.name
    assert out["rows"] == {"rows": 5, "kept": 5, "kept_without_a_timeframe": 0} and out["closed_positions_only"] is True
