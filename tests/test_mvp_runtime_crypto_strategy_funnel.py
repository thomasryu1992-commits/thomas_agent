"""The strategy funnel (`crypto/strategy_funnel.py`): a report that must agree with the two things it
splits. The pool funnel is `promotable_backlog`'s chain, called through the same `refusal_axis`, so its
totals are the backlog's. The forward funnel's statuses are the board's. And it reads only.
"""

from __future__ import annotations

import json

from runtime.mvp_runtime.crypto import forward_cohort as fco
from runtime.mvp_runtime.crypto import pool, strategy_funnel
from runtime.mvp_runtime.crypto.promotion_backlog import BACKLOG_REFUSAL_AXES
from tests.test_mvp_runtime_crypto_forward_cohort import _frame, _install_cohort, _record, _walk
from tests.test_mvp_runtime_crypto_promotion_backlog import _candidate


def _store():
    """One lineage per outcome the fixtures can reach, over two timeframes and both directions."""
    rows = [
        _candidate("cand_ok"),
        _candidate("cand_member", family="mean_reversion", rule_hash="hash-member"),
        _candidate("cand_contradicted", family="breakout", holdout="CONTRADICTED"),
        _candidate("cand_thin", family="trend_following", timeframe="1h", holdout="INSUFFICIENT"),
        _candidate("cand_bare", family="momentum", stamped=False),
        _candidate("cand_fragile", family="carry", verdict="PROVISIONAL"),
        # A sibling re-mint of a lineage the pool already holds under another rule hash: the pool's
        # own entries seed `seen_lineages`, so the backlog charges it to `lineage_already_counted`.
        _candidate("cand_sibling", family="breakout_sibling"),
    ]
    rows[1]["strategy_spec"]["direction"] = "short"
    held = {"strategy_rule_hash": "hash-held-sibling", "strategy_spec": dict(rows[-1]["strategy_spec"])}
    return rows, {"active_strategies": [{"strategy_rule_hash": "hash-member"}, held]}


def test_the_pool_funnel_is_the_backlogs_partition():
    rows, active = _store()
    funnel = strategy_funnel.pool_funnel(rows, active)
    backlog = pool.promotable_backlog(candidates=rows, active_pool=active)
    assert {axis: funnel["outcomes"][axis] for axis in BACKLOG_REFUSAL_AXES} == backlog["refused"]
    assert funnel["outcomes"][strategy_funnel.PROMOTABLE] == backlog["count"] == 1
    assert funnel["outcomes"]["lineage_already_counted"] == 1
    assert funnel["lineages"] == backlog["candidates_read"] == len(rows)


def test_the_stages_run_down_to_the_promotable_count():
    rows, active = _store()
    funnel = strategy_funnel.pool_funnel(rows, active)
    left = [stage["remaining"] for stage in funnel["stages"]]
    assert left == sorted(left, reverse=True)
    assert left[-1] == funnel["outcomes"][strategy_funnel.PROMOTABLE]
    assert [stage["after"] for stage in funnel["stages"]] == list(BACKLOG_REFUSAL_AXES)


def test_every_facet_splits_the_same_lineages():
    rows, active = _store()
    funnel = strategy_funnel.pool_funnel(rows, active)
    for facet in strategy_funnel.FACETS:
        per_value = funnel["by"][facet]
        assert sum(sum(cells.values()) for cells in per_value.values()) == funnel["lineages"], facet
    assert funnel["by"]["timeframe"]["1h"] == {"holdout_insufficient": 1}
    assert funnel["by"]["direction"]["short"] == {"already_active": 1}


def test_a_spec_that_does_not_say_is_counted_under_unknown():
    assert strategy_funnel.facets_of({}) == {facet: strategy_funnel.UNKNOWN for facet in strategy_funnel.FACETS}


def test_the_forward_funnel_agrees_with_the_board(tmp_path):
    a, b = _record("cand_a"), _record("cand_b", family="breakout_twin", adx=21.0)
    _install_cohort(tmp_path, a, b)
    _walk(tmp_path, _frame(range(1, 12), stop_on={5, 9}))
    funnel = strategy_funnel.forward_funnel(tmp_path, [a, b])
    board = fco.board_summary(tmp_path)
    statuses = {key.split(":", 1)[1]: n for key, n in funnel["counts"].items() if key.startswith("status:")}
    assert statuses == board["status_counts"]
    maturities = {key.split(":", 1)[1]: n for key, n in funnel["counts"].items() if key.startswith("maturity:")}
    assert maturities == board["maturity_counts"] == {"EXPLORATORY": 2}
    assert sum(maturities.values()) == funnel["counts"]["members"]
    rows, active = _store()
    lines = strategy_funnel.render_text({"pool": strategy_funnel.pool_funnel(rows, active), "forward": funnel})
    assert "  maturity: EXPLORATORY 2" in lines
    assert funnel["counts"]["members"] == 2
    assert funnel["counts"]["members"] >= funnel["counts"].get("with_outcomes", 0) >= funnel["counts"].get("at_trade_floor", 0)


def test_no_frozen_cohort_is_said_not_guessed(tmp_path):
    assert strategy_funnel.forward_funnel(tmp_path, []) is None
    rows, active = _store()
    lines = strategy_funnel.render_text({"pool": strategy_funnel.pool_funnel(rows, active), "forward": None})
    assert "  no cohort frozen yet" in lines


def test_the_text_caps_each_facet_and_says_so():
    rows = [_candidate(f"cand_{n}", family=f"family_{n}") for n in range(strategy_funnel.TEXT_VALUES_PER_FACET + 3)]
    lines = strategy_funnel.render_text({"pool": strategy_funnel.pool_funnel(rows, {}), "forward": None})
    assert "    ... 3 more (--json lists every one)" in lines


def test_the_script_reads_only_and_prints_json(tmp_path, capsys):
    from scripts import strategy_funnel as script

    rows, _ = _store()
    pool.install_active_pool({"active_strategies": []}, root=tmp_path)
    pool.append_candidates([{k: v for k, v in r.items() if k not in ("record_sha256", "candidate_id")}
                            for r in rows[:1]], root=tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert script.main(["--json"], root=tmp_path) == 0
    out = json.loads(capsys.readouterr().out)
    assert set(out) == {"pool", "forward", "null", "trials", "families", "independent_bets", "judgement_rules"}
    assert out["forward"] is None and out["null"] is None
    assert out["trials"] is None
    assert out["independent_bets"] == {"cohort": None, "twins": None, "pool_forward": None,
                                       "trials": None, "trial_twins": None}
    assert out["judgement_rules"]["short"] and out["judgement_rules"]["version"] == "judgement_rules.v1"
    assert out["pool"]["lineages"] == 1
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_a_damaged_store_refuses_the_run(tmp_path, capsys):
    from scripts import strategy_funnel as script

    path = pool.candidates_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"candidate_id": "x", "record_sha256": "sha256:wrong"}\n', encoding="utf-8")
    assert script.main([], root=tmp_path) != 0
    assert "CANDIDATES_TAMPERED" in capsys.readouterr().err


# --- the daily board's funnel lines (2026-09-24) -------------------------------------------------

def test_past_holdout_counts_lineages_beyond_the_holdout_over_those_not_in_the_pool():
    rows, active = _store()
    funnel = strategy_funnel.pool_funnel(rows, active)
    # 15m: ok (promotable), fragile (verdict), sibling (lineage_already_counted) passed the holdout;
    # contradicted and bare did not; member is in the pool and not counted at all.
    assert strategy_funnel.past_holdout(funnel, "timeframe") == {"15m": (3, 5), "1h": (0, 1)}
    assert set(strategy_funnel.PAST_HOLDOUT) == {"verdict", "expectancy", "lineage_already_counted",
                                                 "unjudgeable", strategy_funnel.PROMOTABLE}


def test_the_board_prints_the_funnel_shortest_timeframe_first():
    from runtime.mvp_runtime.crypto.dashboard import render_status_text
    text = render_status_text({"strategy_funnel": {
        "timeframe": {"1d": (0, 580), "15m": (0, 334), "4h": (6, 1120), "1h": (0, 994)},
        "direction": {"short": (3, 1550), "long": (3, 1478)},
    }})
    assert "       퍼널 tf별 holdout 통과/계보 15m 0/334 · 1h 0/994 · 4h 6/1120 · 1d 0/580" in text
    assert "       퍼널 방향별 holdout 통과/계보 long 3/1478 · short 3/1550" in text


def test_no_funnel_prints_no_funnel_line():
    from runtime.mvp_runtime.crypto.dashboard import render_status_text
    assert "퍼널" not in render_status_text({"strategy_funnel": None})


def test_the_board_status_carries_the_funnel_from_the_store(tmp_path):
    from runtime.mvp_runtime.crypto.dashboard import build_status
    rows, _ = _store()
    pool.install_active_pool({"active_strategies": []}, root=tmp_path)
    pool.append_candidates([{k: v for k, v in r.items() if k not in ("record_sha256", "candidate_id")}
                            for r in rows[:1]], root=tmp_path)
    status = build_status(tmp_path, now="2026-07-20T00:00:00Z")
    assert set(status["strategy_funnel"]) == {"timeframe", "direction"}
    assert sum(judged for _, judged in status["strategy_funnel"]["timeframe"].values()) == 1


def test_the_text_reports_independent_bets_per_record_set():
    census = {"lineages": 3, "days": 10, "first_day": "2026-09-01", "last_day": "2026-09-10",
              "effective_bets": 1.2, "baseline_effective_bets": 2.6, "mean_corr_same_direction": 0.8,
              "mean_corr_opposite_direction": None, "by_direction": {"LONG": {"lineages": 3, "effective_bets": 1.2}},
              "top_same_direction_pairs": [{"a": "x", "b": "y", "corr": 0.9}]}
    rows, _ = _store()
    lines = strategy_funnel.render_text({"pool": strategy_funnel.pool_funnel(rows, {}), "forward": {
        "counts": {}, "by": {}}, "independent_bets": {"cohort": census, "twins": None, "pool_forward": None}})
    text = "\n".join(lines)
    assert "INDEPENDENT BETS" in text
    assert "cohort        3 lineages -> 1.2 bets (baseline 2.6; 2026-09-01..2026-09-10, 10d)" in text
    assert "most alike: x~y 0.9" in text and "twins         under two lineages with enough rows" in text


def test_the_twins_bet_census_reads_only_the_active_arms_rows(monkeypatch):
    """A superseded arm's rows stay in the store; counting them beside the v2 twins' would mix two
    populations paced differently (2026-09-25)."""
    from runtime.mvp_runtime.crypto import strategy_funnel as sf
    seen = []
    monkeypatch.setattr(sf, "active_null_ids", lambda root: frozenset({"null_v2_a"}))
    monkeypatch.setattr(sf, "read_null_outcomes", lambda root: [
        {"candidate_id": "null_a"}, {"candidate_id": "null_v2_a"}])
    monkeypatch.setattr(sf, "independence", lambda rows: seen.append(rows) or {"lineages": 0})
    sf._active_twin_bets(None)
    assert seen == [[{"candidate_id": "null_v2_a"}]]
    monkeypatch.setattr(sf, "active_null_ids", lambda root: frozenset())
    assert sf._active_twin_bets(None) is None


# --- the family exhaustion table (FAMILY_EXHAUSTION_V0.1 B, Thomas 2026-09-24) -----------------------------

from runtime.mvp_runtime.crypto import robustness  # noqa: E402

NOW_FX = "2026-10-25T09:00:00Z"


def _fx(cid, family, created, *, confirmed, oi=False, symbol="BTCUSDT", source=None):
    """A row the stubbed holdout reads as CONFIRMED (``tag C``) or not; ``oi`` makes its evidence INVALID."""
    feature = "open_interest_change_pct" if oi else "rsi"
    row = {"candidate_id": cid, "created_at_utc": created,
           "strategy_spec": {"strategy_family": family, "timeframe": "1h", "symbol_scope": [symbol],
                             "direction": "long", "entry_rules": {"conditions": [{"feature": feature}]}},
           "backtest_evidence": {"holdout": {"tag": "C" if confirmed else "X"}}}
    if source:
        row["rescored_from_candidate_id"] = source
    return row


def test_the_family_table_counts_mints_and_valid_confirmations(monkeypatch):
    monkeypatch.setattr(robustness, "holdout_status", lambda h: robustness.HOLDOUT_CONFIRMED
                        if h and h.get("tag") == "C" else robustness.HOLDOUT_CONTRADICTED)
    records = [
        _fx("a1", "breakout", "2026-10-20T08:00:00Z", confirmed=True),
        _fx("a2", "breakout", "2026-10-21T08:00:00Z", confirmed=True, symbol="ETHUSDT"),
        _fx("a3", "breakout", "2026-08-01T08:00:00Z", confirmed=True),              # confirmed, outside the window
        _fx("b1", "oi_squeeze_long", "2026-09-01T08:00:00Z", confirmed=True, oi=True),   # lookahead: INVALID
        _fx("b1r", "oi_squeeze_long", "2026-10-21T09:04:00Z", confirmed=False, source="b1"),  # its re-score
        _fx("c1", "trend_pullback", "2026-10-22T08:00:00Z", confirmed=False),
    ]
    table = strategy_funnel.family_exhaustion(records, None, now=NOW_FX)["by_family"]
    assert table["breakout"]["rows_window"] == 2 and table["breakout"]["confirmed_window"] == 2
    assert table["breakout"]["last_confirmed"] == "2026-10-21T08:00:00Z"
    assert table["breakout"]["confirmed_lineages"] == 2           # BTC and ETH; a3 shares BTC's lineage
    oi = table["oi_squeeze_long"]
    assert oi["confirmed_invalid"] == 1 and oi["confirmed_window"] == 0 and oi["last_confirmed"] is None
    assert oi["rows_window"] == 0, "a re-score is not a mint, and it dates from the 09-01 candidate it re-scored"
    assert table["trend_pullback"] == {**table["trend_pullback"], "rows_window": 1, "confirmed_window": 0}


def test_a_rescore_that_confirms_is_dated_by_its_source(monkeypatch):
    monkeypatch.setattr(robustness, "holdout_status", lambda h: robustness.HOLDOUT_CONFIRMED
                        if h and h.get("tag") == "C" else robustness.HOLDOUT_CONTRADICTED)
    records = [_fx("s", "oi_unwind_short", "2026-08-14T08:00:00Z", confirmed=True, oi=True),
               _fx("s_r", "oi_unwind_short", "2026-10-21T09:04:00Z", confirmed=True, source="s")]
    records += [_fx("t", "oi_squeeze_short", "2026-10-01T08:00:00Z", confirmed=False, oi=True),
                _fx("t_r", "oi_squeeze_short", "2026-10-21T09:04:00Z", confirmed=False, source="t")]
    table = strategy_funnel.family_exhaustion(records, None, now=NOW_FX)["by_family"]
    row = table["oi_unwind_short"]
    assert row["last_confirmed"] == "2026-08-14T08:00:00Z" and row["confirmed_window"] == 0
    assert row["confirmed_invalid"] == 1
    assert table["oi_squeeze_short"]["rows_window"] == 1          # one mint in the window; its re-score is not another


def test_the_family_table_is_printed_as_a_measurement(monkeypatch):
    monkeypatch.setattr(robustness, "holdout_status", lambda h: robustness.HOLDOUT_CONTRADICTED)
    section = strategy_funnel.family_exhaustion(
        [_fx("c1", "trend_pullback", "2026-10-22T08:00:00Z", confirmed=False)],
        {"by": {"strategy_family": {"trend_pullback": {"members": 3, "maturity:EXPLORATORY": 3}}}}, now=NOW_FX)
    lines = strategy_funnel._render_families(section)
    assert any("RETIRED_FAMILIES change" in line for line in lines)
    row = next(line for line in lines if line.strip().startswith("trend_pullback"))
    assert "3 (EXPLORATORY=3)" in row
