"""`scripts/selection_evidence.py`: the store's selection against coin flips, read-only (2026-10-07)."""

from __future__ import annotations

import json
import math

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
