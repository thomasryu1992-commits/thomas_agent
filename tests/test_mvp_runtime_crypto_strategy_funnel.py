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
    assert set(out) == {"pool", "forward", "null", "judgement_rules"}
    assert out["forward"] is None and out["null"] is None
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
