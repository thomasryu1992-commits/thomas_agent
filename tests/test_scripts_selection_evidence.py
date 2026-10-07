"""`scripts/selection_evidence.py`: the store's selection against coin flips, read-only (2026-10-07)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from scripts import selection_evidence as se


def test_the_economic_family_groups_templates_as_the_throughput_analysis_did():
    assert se.economic_family("oi_squeeze_long") == "open_interest"
    assert se.economic_family("bollinger_breakdown_short") == "breakout(bollinger)"  # before `breakdown`
    assert se.economic_family("breakdown_short") == "breakout"
    assert se.economic_family("trend_pullback+volatility_expansion_long") == se.FUSED
    assert se.economic_family("mined:bb_percent_b+ma50") == se.EXPERIMENTAL
    assert se.economic_family("xs_reversion_long") == "cross_sectional"
    assert se.economic_family("") == "unknown"
    assert se.economic_family("something_new") == "other:something_new"


# --- null-control --------------------------------------------------------------------------------

def _m(cid, family, *, edge, trades, real=0.0, null=0.0, status="measured"):
    return {"candidate_id": cid, "strategy_family": family, "status": status, "trades": trades,
            "edge_vs_null": edge, "real_r_per_trade": real, "null_r_per_trade_median": null}


def _fire(at, symbol, timeframe, *measurements):
    return {"kind": "crypto_null_control", "trace_id": f"NULLCTL-{at}-{symbol}",
            "record": {"created_at": at, "cells": [
                {"symbol": symbol, "timeframe": timeframe, "measurements": list(measurements)}]}}


def test_only_the_newest_fire_of_each_cell_is_read():
    old = _fire("2026-10-05T04:00:00Z", "BTCUSDT", "4h", _m("a", "breakout", edge=9.0, trades=10))
    new = _fire("2026-10-06T04:00:00Z", "BTCUSDT", "4h", _m("a", "breakout", edge=-0.1, trades=12))
    other = _fire("2026-10-04T04:00:00Z", "ETHUSDT", "4h", _m("a", "breakout", edge=0.3, trades=8))
    cells = se.newest_cells([new, old, other, {"kind": "something_else", "record": {}}])
    assert set(cells) == {("BTCUSDT", "4h"), ("ETHUSDT", "4h")}
    assert cells[("BTCUSDT", "4h")][0] == "2026-10-06T04:00:00Z"


def test_a_pooled_spec_is_one_row_weighted_by_its_legs_trades_and_lineages_fold_tweaks():
    cells = se.newest_cells([
        _fire("t", "BTCUSDT", "4h", _m("a", "breakout", edge=0.4, trades=10), _m("b", "breakout", edge=-0.2, trades=5),
              _m("c", "breakout", edge=1.0, trades=0), _m("d", "breakout", edge=5.0, trades=20, status="too_few_trades")),
        _fire("t", "ETHUSDT", "4h", _m("a", "breakout", edge=-0.2, trades=30)),
    ])
    pooled = {"strategy_family": "breakout", "symbol_scope": ["BTCUSDT", "ETHUSDT"], "timeframe": "4h"}
    specs = {"a": pooled, "b": pooled}
    rows = {r["candidate_id"]: r for r in se.spec_edges(cells, specs)}
    assert set(rows) == {"a", "b"}                       # no trades and not-measured are left out
    assert rows["a"]["legs"] == 2 and rows["a"]["trades"] == 40
    assert rows["a"]["edge_vs_null"] == pytest.approx((0.4 * 10 - 0.2 * 30) / 40)
    (lineage,) = se.lineage_edges(list(rows.values()))   # same family, scope, timeframe: one bet
    assert lineage["specs"] == 2
    assert lineage["edge_vs_null"] == pytest.approx((rows["a"]["edge_vs_null"] + rows["b"]["edge_vs_null"]) / 2)
    (summary,) = se.summarize(list(rows.values()), "timeframe")
    assert (summary["count"], summary["ahead"]) == (2, 0)


def test_the_null_control_command_reads_the_ledger_archive_included(tmp_path, capsys):
    ledger = tmp_path / ".runtime_governance_state" / "runtime_ledger"
    (ledger / "archive").mkdir(parents=True)
    now_stamp = se.timeutil.utc_now_iso()
    (ledger / "archive" / "records.2099-01-01T000000Z.jsonl").write_text(
        json.dumps(_fire(now_stamp, "BTCUSDT", "1h", _m("a", "oi_squeeze_long", edge=0.5, trades=12))) + "\n")
    (ledger / "records.jsonl").write_text(
        json.dumps(_fire(now_stamp, "ETHUSDT", "4h", _m("b", "breakout", edge=-0.3, trades=15))) + "\n")
    assert se.main(["null-control"], root=tmp_path) == 0
    out = capsys.readouterr().out
    assert "direction only" in out and "open_interest" in out and "BTCUSDT 1h" in out
    assert "(2 cells)" in out
    assert se.main(["null-control", "--json"], root=tmp_path) == 0
    evidence = json.loads(capsys.readouterr().out)
    assert {g["timeframe"]: g["ahead"] for g in evidence["by_timeframe_lineages"]} == {"1h": 1, "4h": 0}


# --- families: the BH dry run --------------------------------------------------------------------

def test_bh_is_step_up_and_rejects_nothing_on_nothing():
    assert se.benjamini_hochberg([0.01, 0.04, 0.03, 0.5], 0.10) == {0, 1, 2}
    # 0.06 alone misses its own rank-1 line (0.05) and still passes: the larger rank carries it
    assert se.benjamini_hochberg([0.06, 0.07], 0.10) == {0, 1}
    assert se.benjamini_hochberg([0.2, 0.9], 0.10) == set()
    assert se.benjamini_hochberg([], 0.10) == set()


def test_the_p_value_is_read_off_the_day_clustered_interval():
    summary = {"mean_diff_r": 0.5, "ci_low_r": 0.5 - 1.96 * 0.25, "ci_high_r": 0.5 + 1.96 * 0.25}
    assert se.two_sided_p(summary) == pytest.approx(math.erfc(2 / math.sqrt(2)))
    assert se.two_sided_p({"mean_diff_r": 0.5, "ci_low_r": None, "ci_high_r": None}) is None
    assert se.two_sided_p(None) is None


def _pairs(n, *, diff, noise, start=0):
    """``n`` pairs on ``n`` distinct days: member ``diff ± noise``, twin 0."""
    return [([(f"d{start + i:03d}", diff + (noise if i % 2 else -noise))], [(f"d{start + i:03d}", 0.0)])
            for i in range(n)]


def test_a_consistent_family_passes_a_thin_one_is_shown_untested_and_swaps_calibrate():
    groups = {"open_interest": _pairs(20, diff=1.0, noise=0.5),
              "breakout": _pairs(20, diff=0.0, noise=0.5, start=100),
              "funding": _pairs(3, diff=2.0, noise=0.1, start=200)}           # three days: under the floor
    stage = {g["family"]: g for g in se.family_stage(groups, q=0.10, min_days=10)}
    assert stage["open_interest"]["bh_pass"] is True
    assert stage["breakout"]["bh_pass"] is False
    assert stage["funding"]["bh_pass"] is None and stage["funding"]["days"] == 3
    cal = se.calibration(groups, q=0.10, min_days=10, draws=60, seed=7)
    assert cal == se.calibration(groups, q=0.10, min_days=10, draws=60, seed=7)   # seeded: replays
    assert 0.0 <= cal["any_pass_rate"] < 0.5        # swapping signs destroys the consistent edge


def test_the_calibration_centres_first_so_an_edge_calibrates_as_its_absence():
    # Swapping raw pairs that all lead by 1R makes a ±1R spread the data never had, and the stage then
    # passes the swaps too often: the uncentred gate withheld the families that lead (Thomas 2026-10-07).
    flat = {"open_interest": _pairs(16, diff=0.0, noise=0.8), "breakout": _pairs(16, diff=0.0, noise=0.8, start=100)}
    edge = {**flat, "open_interest": _pairs(16, diff=1.0, noise=0.8)}
    run = lambda groups: se.calibration(groups, q=0.10, min_days=10, draws=200, seed=3)  # noqa: E731
    assert run(edge) == run(flat)
    assert se.clustered_pair_mean(se.centered(edge["open_interest"]))["mean_diff_r"] == pytest.approx(0.0)


def test_the_families_command_pools_the_cohorts_pairs_by_economic_family(tmp_path, capsys):
    from tests.test_mvp_runtime_crypto_forward_cohort import _frame, _install_cohort, _walk
    from tests.test_mvp_runtime_crypto_forward_cohort_null import _eager, _freeze, _null_walk

    _install_cohort(tmp_path, _eager("cand_a"), _eager("cand_b", family="breakdown_short"),
                    _eager("cand_c", family="trend_pullback"))
    _freeze(tmp_path)
    frame = _frame(range(1, 12), stop_on={5, 9})
    _walk(tmp_path, frame)
    _null_walk(tmp_path, frame)
    evidence = se.family_evidence(tmp_path, draws=5)
    block = evidence["1d"]
    assert block["all"]["pairs"] == 3
    # breakout and breakdown_short are one economic family; trend_pullback is another
    assert sorted((g["family"], g["pairs"]) for g in block["families"]) == [("breakout", 2), ("trend_following", 1)]
    assert se.main(["families", "--draws", "5"], root=tmp_path) == 0
    out = capsys.readouterr().out
    assert "a dry run of stage 1, not a verdict" in out and "trend_following" in out


# --- verdict: the hierarchical judgement at a cohort's close --------------------------------------

def test_the_family_grouping_is_frozen_until_thomas_changes_it():
    # H2 (Thomas 2026-10-07): the judgement's grouping is ECONOMIC_FAMILY_MARKERS as decided. A change
    # is a Thomas decision before the next epoch boundary; record it, then move this hash.
    assert se.family_markers_sha256() == "5c73cbd93306061a72ad066e60934ae7c65accc299239d8868674beeebb2dcb8"


def test_holm_steps_down_and_stops_at_the_first_miss():
    assert se.holm([0.01, 0.04, 0.03], 0.10) == {0, 1, 2}
    # 0.06 misses its rank-2 line (0.05); 0.07 would clear its own (0.10) but the walk has stopped
    assert se.holm([0.001, 0.06, 0.07], 0.10) == {0}
    # m counts hypotheses that were not tested: 0.04 against 0.10/3, not 0.10/1
    assert se.holm([0.04, 0.01], 0.10, m=4) == {1}
    assert se.holm([], 0.10) == set()


def test_the_boundary_is_a_cohorts_close_and_nothing_is_read_before_it():
    first = {"cohort_id": "c1", "frozen_at_utc": "2026-09-23T07:03:48Z"}
    second = {"cohort_id": "c2", "frozen_at_utc": "2026-10-01T04:04:03Z"}
    assert se.cohort_close(first) == "2027-03-22T07:03:48Z"
    with pytest.raises(se.ToolError) as exc:
        se.boundary_cohort([first, second], now="2027-03-22T07:03:47Z")
    assert exc.value.reason_code == se.VERDICT_NOT_DUE and "2027-03-22T07:03:48Z" in str(exc.value)
    assert se.boundary_cohort([first, second], now="2027-03-22T07:03:48Z") == (first, "2027-03-22T07:03:48Z")
    assert se.boundary_cohort([first, second], now="2027-04-01T00:00:00Z")[0] is second
    assert se.boundary_cohort([first, second], now="2027-04-01T00:00:00Z", cohort_id="c1")[0] is first
    for named in ("c2", "nope"):
        with pytest.raises(se.ToolError) as exc:
            se.boundary_cohort([first, second], now="2027-03-25T00:00:00Z", cohort_id=named)
        assert exc.value.reason_code == se.VERDICT_NOT_DUE


def _days_pair(days, *, diff, noise, start=0):
    """One pair trading on ``days`` distinct days: member ``diff ± noise``, twin 0."""
    stamps = [f"d{start + i:03d}" for i in range(days)]
    return ([(d, diff + (noise if i % 2 else -noise)) for i, d in enumerate(stamps)], [(d, 0.0) for d in stamps])


def _lineage_member(cid, family="oi_squeeze_long", scope=("DOGEUSDT",)):
    return {"candidate_id": cid, "strategy_family": family, "symbol_scope": list(scope), "timeframe": "1h"}


def test_stage_two_folds_siblings_into_one_lineage_and_holm_counts_the_untested():
    entries = [
        (_lineage_member("cand_a"), _days_pair(12, diff=1.0, noise=0.3)),
        (_lineage_member("cand_a2"), _days_pair(12, diff=1.0, noise=0.3, start=50)),   # sibling: same key
        (_lineage_member("cand_b", scope=("SOLUSDT",)), _days_pair(3, diff=3.0, noise=0.1, start=100)),
        (_lineage_member("cand_c", family="oi_unwind_short"), _days_pair(12, diff=0.0, noise=0.3, start=200)),
    ]
    rows = {r["members"][0]: r for r in se.lineage_stage(entries, alpha=0.10, min_days=10)}
    assert rows["cand_a"]["members"] == ["cand_a", "cand_a2"] and rows["cand_a"]["pairs"] == 2
    assert rows["cand_a"]["holm_pass"] is True and rows["cand_a"]["verdict"] == se.PASS
    assert rows["cand_b"]["holm_pass"] is None and rows["cand_b"]["days"] == 3     # shown, not tested
    assert rows["cand_c"]["holm_pass"] is False
    # cand_b counts in m: with the three lineages, a p between α/3 and α/2 does not pass
    assert len(rows) == 3


def test_the_s1_condition_is_h1():
    assert se.s1_condition(None) == "WAIT"
    assert se.s1_condition({"pairs": 37, "ci_high_r": 1.0}) == "WAIT"
    assert se.s1_condition({"pairs": 38, "ci_high_r": -0.01}) == "HOLD"
    assert se.s1_condition({"pairs": 38, "ci_high_r": 0.0}) == "EXECUTE"
    assert se.s1_condition({"pairs": 40, "ci_high_r": None}) == "HOLD"


def test_the_verdict_reads_once_at_the_close_and_only_trades_settled_by_it(tmp_path, monkeypatch, capsys):
    from runtime.mvp_runtime.crypto import forward_cohort as fco
    from runtime.mvp_runtime.crypto import pool_state
    from scripts.forward_cohort import member_twin_pairs
    from tests.test_mvp_runtime_crypto_forward_cohort import _frame, _member, _walk
    from tests.test_mvp_runtime_crypto_forward_cohort_null import _eager, _freeze, _null_walk

    records = [_eager("cand_a"), _eager("cand_b", family="breakdown_short"), _eager("cand_c", family="trend_pullback")]
    pool_state.append_candidates([dict(r) for r in records], root=tmp_path)
    cohort = fco.build_cohort_record([_member(r) for r in records], now="2026-07-03T00:00:00Z")
    path = fco._cohorts_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cohort) + "\n", encoding="utf-8")
    _freeze(tmp_path)
    frame = _frame(range(1, 12), stop_on={5, 9})
    _walk(tmp_path, frame)
    _null_walk(tmp_path, frame)
    monkeypatch.setattr(fco, "COHORT_LIFETIME_DAYS", 4)                 # close 2026-07-07, walked to 07-11

    assert se.main(["verdict"], root=tmp_path, now="2026-07-06T23:59:59Z") == se.EXIT_BLOCKED
    assert se.VERDICT_NOT_DUE in capsys.readouterr().err

    assert se.main(["verdict", "--json"], root=tmp_path, now="2026-07-08T00:00:00Z") == se.EXIT_OK
    first = capsys.readouterr().out
    assert se.main(["verdict", "--json"], root=tmp_path, now="2026-12-31T00:00:00Z") == se.EXIT_OK
    assert capsys.readouterr().out == first                            # any later day: the same reading
    evidence = json.loads(first)
    assert evidence["boundary"]["close_utc"] == "2026-07-07T00:00:00Z"
    assert evidence["rule"]["holm_alpha"] == 0.10 and evidence["rule"]["min_days"] == 10
    days = lambda pairs: {d for _, (m, t) in pairs for d, _ in m + t}  # noqa: E731
    assert max(days(member_twin_pairs(tmp_path))) > "2026-07-07"        # trades after the close exist...
    assert max(days(member_twin_pairs(tmp_path, settled_by="2026-07-07T00:00:00Z"))) <= "2026-07-07"
    block = evidence["timeframes"]["1d"]
    assert block["all"]["days"] <= len(days(member_twin_pairs(tmp_path, settled_by="2026-07-07T00:00:00Z")))
    assert evidence["s1"]["condition"] == "WAIT"                       # three pairs, not 38

    assert se.main(["verdict"], root=tmp_path, now="2026-07-08T00:00:00Z") == se.EXIT_OK
    out = capsys.readouterr().out
    assert "S1 (H1): 1d all" in out and "-> WAIT" in out

    monkeypatch.setattr(fco, "COHORT_LIFETIME_DAYS", 30)                # close 08-02: the walker stopped at 07-11
    assert se.main(["verdict"], root=tmp_path, now="2026-08-03T00:00:00Z") == se.EXIT_BLOCKED
    assert se.VERDICT_WALK_BEHIND in capsys.readouterr().err


def _synthetic_verdict(monkeypatch, *, any_pass_rate=0.05):
    """``verdict`` over synthetic pairs: an OI family of two 1h lineages, eight leading pairs each (a
    lineage of two pairs swaps into a pass half the time, and the calibration rightly withholds it), and
    a flat breakout family. The boundary, the walker check and the calibration's rate are stubbed; each
    has its own test."""
    def lineage(prefix, start, diff, **kw):
        return [(_lineage_member(f"{prefix}{i}", **kw), _days_pair(3, diff=diff, noise=0.3, start=start + 3 * i))
                for i in range(8)]
    pairs = (lineage("cand_a", 0, 1.0) + lineage("cand_b", 100, 1.0, scope=("SOLUSDT",))
             + lineage("cand_x", 200, 0.0, family="breakout_long"))
    monkeypatch.setattr(se.forward_cohort, "read_cohorts", lambda root: [])
    monkeypatch.setattr(se, "boundary_cohort", lambda cohorts, **kw: ({"cohort_id": "c1"}, "2027-03-22T07:03:48Z"))
    monkeypatch.setattr(se, "walk_behind", lambda root, cohorts, **kw: [])
    monkeypatch.setattr(se, "member_twin_pairs", lambda root, **kw: pairs)
    seen_m = []
    real_holm = se.holm
    monkeypatch.setattr(se, "holm", lambda p, a, *, m=None: seen_m.append(m) or real_holm(p, a, m=m))
    monkeypatch.setattr(se, "calibration", lambda groups, **kw: {"draws": 400, "any_pass_rate": any_pass_rate,
                                                                  "mean_passes": 1.0})
    return se.verdict(Path("."), now="2027-03-23T00:00:00Z")["timeframes"]["1h"], seen_m


def test_stage_two_runs_only_inside_a_family_stage_one_passes(monkeypatch):
    block, seen_m = _synthetic_verdict(monkeypatch)
    labels = {g["family"]: g["verdict"] for g in block["families"]}
    # breakout's pairs sit 0.1R under their twins on every day: rejected, two-sided, but BEHIND
    assert labels == {"open_interest": se.PASS, "breakout": se.BEHIND} and not block["stage1_withheld"]
    assert set(block["lineages"]) == {"open_interest"}                  # stage 2 only where selection leads
    assert [r["verdict"] for r in block["lineages"]["open_interest"]] == [se.PASS, se.PASS]
    assert seen_m == [2]                                                # Holm over the family's lineages


def test_a_calibration_over_q_withholds_stage_one_and_skips_stage_two(monkeypatch):
    block, seen_m = _synthetic_verdict(monkeypatch, any_pass_rate=0.101)
    assert block["stage1_withheld"] is True and block["lineages"] == {} and seen_m == []
