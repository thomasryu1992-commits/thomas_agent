"""The forward cohort's null arm (`crypto/forward_cohort_null.py`): one coin-flip twin per member, frozen
as a companion record and walked by the cohort's own walker into stores of its own.

It is evidence only if it is isolated (nothing that reads the cohort reads it), deterministic (a
re-walk mints nothing new) and honest about what it skipped. And it must not change the real walk.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.crypto import forward_book as fb
from runtime.mvp_runtime.crypto import forward_cohort as fco
from runtime.mvp_runtime.crypto import forward_cohort_null as fcn
from runtime.mvp_runtime.crypto.candidate_ranking import expected_replayed_bars
from runtime.mvp_runtime.crypto.null_control import NULL_FAMILY, NULL_FEATURE
from runtime.mvp_runtime.errors import ToolError
from tests.test_mvp_runtime_crypto_forward_cohort import NOW, _day, _frame, _install_cohort, _record, _walk


def _eager(cid, **kw):
    """A member whose twin flips a coin that always lands: trade rate 1 (closed == bars replayed)."""
    return _record(cid, closed=expected_replayed_bars("1d"), **kw)


def _freeze(tmp_path):
    return fcn.freeze_nulls(tmp_path, now=NOW, apply=True)


def _null_walk(tmp_path, frame, *, now=NOW, persist=True):
    return fcn.run_null_walk(tmp_path, now=now, frame_for=lambda s, t, b: frame, persist=persist)


# --- the record ---------------------------------------------------------------------------------

def test_every_member_gets_one_twin_with_its_parents_clock_and_exits(tmp_path):
    a = _record("cand_a")
    _install_cohort(tmp_path, a)
    (record,) = _freeze(tmp_path)
    (twin,) = record["members"]
    assert twin["null_id"] == "null_v2_cand_a" and twin["parent_candidate_id"] == "cand_a"
    assert twin["selected_at_utc"] == a["created_at_utc"]
    assert twin["signal_rate"] == round(60 / expected_replayed_bars("1d"), 6)
    spec = twin["null_spec"]
    assert spec["strategy_family"] == NULL_FAMILY
    assert spec["entry_rules"]["conditions"][0]["feature"] == NULL_FEATURE
    assert spec["exit_rules"] == a["strategy_spec"]["exit_rules"]
    assert spec["direction"] == a["strategy_spec"]["direction"]
    assert record["null_size"] == 1 and record["skipped"] == []


def test_a_member_that_cannot_be_twinned_is_named_not_dropped(tmp_path):
    a, b = _record("cand_a"), _record("cand_b", family="other")
    b["backtest_evidence"].pop("bars_replayed")
    _install_cohort(tmp_path, a, b)
    (record,) = fcn.freeze_nulls(tmp_path, now=NOW)
    assert [t["null_id"] for t in record["members"]] == ["null_v2_cand_a"]
    assert record["skipped"] == [{"parent_candidate_id": "cand_b", "reason": "no_trade_rate"}]


def test_a_cohorts_null_arm_is_frozen_once_and_a_dry_run_writes_nothing(tmp_path):
    _install_cohort(tmp_path, _record("cand_a"))
    assert fcn.freeze_nulls(tmp_path, now=NOW) and not fcn._nulls_path(tmp_path).exists()
    _freeze(tmp_path)
    assert fcn.freeze_nulls(tmp_path, now=NOW, apply=True) == []
    assert len(fcn.read_null_records(tmp_path)) == 1


def test_an_edited_null_record_is_refused(tmp_path):
    _install_cohort(tmp_path, _record("cand_a"))
    _freeze(tmp_path)
    path = fcn._nulls_path(tmp_path)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["members"][0]["signal_rate"] = 1.0
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        fcn.read_null_records(tmp_path)
    assert exc.value.reason_code == fcn.FORWARD_COHORT_NULLS_TAMPERED


# --- the coin -----------------------------------------------------------------------------------

def test_the_coin_is_a_function_of_the_seed_and_the_bar_and_is_uniform():
    assert fcn.coin("c1|cand_a", _day(3)) == fcn.coin("c1|cand_a", _day(3))
    assert fcn.coin("c1|cand_a", _day(3)) != fcn.coin("c1|cand_b", _day(3))
    draws = [fcn.coin("c1|cand_a", f"bar-{n}") for n in range(4000)]
    assert all(0.0 <= d < 1.0 for d in draws)
    assert 0.47 < sum(draws) / len(draws) < 0.53


# --- the walk -----------------------------------------------------------------------------------

def test_a_twin_trades_into_its_own_store_and_never_the_cohorts(tmp_path):
    _install_cohort(tmp_path, _eager("cand_a"))
    _freeze(tmp_path)
    summary = _null_walk(tmp_path, _frame(range(1, 12), stop_on={5, 9}))
    assert summary["members"] == 1 and summary["settled"] >= 1
    rows = fcn.read_null_outcomes(tmp_path)
    assert rows and {r["candidate_id"] for r in rows} == {"null_v2_cand_a"}
    assert {r["provenance"] for r in rows} == {fcn.NULL_PROVENANCE}
    assert not fco._outcomes_path(tmp_path).exists() and not fco._positions_path(tmp_path).exists()
    assert not fb._outcomes_path(tmp_path).exists()


def test_re_walking_the_same_bars_mints_nothing_new(tmp_path):
    _install_cohort(tmp_path, _eager("cand_a"))
    _freeze(tmp_path)
    frame = _frame(range(1, 12), stop_on={5, 9})
    first = _null_walk(tmp_path, frame)
    again = _null_walk(tmp_path, frame)
    assert first["settled"] >= 1 and again["settled"] == 0 and again["opened"] == 0


def test_the_real_walk_is_the_same_with_the_null_arm_beside_it(tmp_path):
    """Twins walk copies of the rows: the real members' outcomes are what they are without them."""
    frame = _frame(range(1, 12), stop_on={5, 9})
    alone = tmp_path / "alone"
    _install_cohort(alone, _eager("cand_a"))
    _walk(alone, frame)
    beside = tmp_path / "beside"
    _install_cohort(beside, _eager("cand_a"))
    _freeze(beside)
    _null_walk(beside, frame)
    _walk(beside, frame)
    assert fco.read_cohort_outcomes(beside) == fco.read_cohort_outcomes(alone)
    assert all(NULL_FEATURE not in row for row in frame[0])


def test_nothing_that_reads_the_cohort_sees_a_twin(tmp_path):
    _install_cohort(tmp_path, _eager("cand_a"))
    _freeze(tmp_path)
    _null_walk(tmp_path, _frame(range(1, 12), stop_on={5, 9}))
    assert fco.member_candidate_ids(tmp_path) == frozenset({"cand_a"})
    assert [m["candidate_id"] for c in fco.cohort_report(tmp_path) for m in c["members"]] == ["cand_a"]
    assert fcn.null_ids(tmp_path) == frozenset({"null_v2_cand_a"})


def test_the_null_positions_book_refuses_a_lineage_no_null_arm_holds(tmp_path):
    _install_cohort(tmp_path, _eager("cand_a"))
    _freeze(tmp_path)
    _null_walk(tmp_path, _frame(range(1, 5)), now=_day(4))
    path = fcn._null_positions_path(tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    (key, entry), = raw["entries"].items()
    entry["lineage"] = "cand:null_stranger"
    raw["entries"] = {fb.book_key("cand:null_stranger", entry["symbol"], entry["timeframe"]): entry}
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        fcn.load_null_positions(tmp_path)
    assert exc.value.reason_code == fco.FORWARD_COHORT_POSITIONS_INVALID


def test_no_null_arm_walks_nothing(tmp_path):
    _install_cohort(tmp_path, _record("cand_a"))
    summary = _null_walk(tmp_path, _frame(range(1, 5)))
    assert summary["members"] == 0 and summary["settled"] == 0
    assert not fcn._null_positions_path(tmp_path).exists() or fcn.load_null_positions(tmp_path)["entries"] == {}


# --- the scheduled fire and the operator script -------------------------------------------------

def _fire(tmp_path, monkeypatch, frame_for):
    from tests.test_mvp_runtime_crypto_forward_cohort import _fire as cohort_fire
    return cohort_fire(tmp_path, monkeypatch, frame_for)


def test_a_fire_walks_the_twins_after_the_members_on_the_same_frames(tmp_path, monkeypatch):
    _install_cohort(tmp_path, _eager("cand_a"))
    _freeze(tmp_path)
    fetched = []

    def frame_for(symbol, timeframe, bars):
        fetched.append((symbol, timeframe))
        return _frame(range(1, 12), stop_on={5, 9})

    status = _fire(tmp_path, monkeypatch, frame_for)
    assert status.startswith("forward_cohort members=1") and " | nulls members=1 walked=1" in status
    assert fetched == [("BTCUSDT", "1d")], "the twin reuses its parent's frame"
    assert fcn.read_null_outcomes(tmp_path) and fco.read_cohort_outcomes(tmp_path)


def test_a_fire_without_a_null_arm_prints_the_members_line_alone(tmp_path, monkeypatch):
    _install_cohort(tmp_path, _record("cand_a"))
    status = _fire(tmp_path, monkeypatch, lambda s, t, b: _frame(range(1, 8), stop_on={5}))
    assert status.startswith("forward_cohort members=1") and "nulls" not in status


def test_a_null_arm_that_fails_is_named_and_never_fails_the_fire(tmp_path, monkeypatch):
    _install_cohort(tmp_path, _eager("cand_a"))
    _freeze(tmp_path)
    fcn._null_positions_path(tmp_path).write_text("{not json", encoding="utf-8")
    status = _fire(tmp_path, monkeypatch, lambda s, t, b: _frame(range(1, 8), stop_on={5}))
    assert status.startswith("forward_cohort members=1")
    assert status.endswith(f"| nulls failed={fco.FORWARD_COHORT_UNREADABLE}")
    assert fco.read_cohort_outcomes(tmp_path), "the members were walked"


def test_the_script_freezes_the_null_arm_dry_then_for_real(tmp_path, monkeypatch, capsys):
    from scripts import forward_cohort as script
    monkeypatch.setattr(script, "ROOT", tmp_path)
    _install_cohort(tmp_path, _record("cand_a"))
    assert script.main(["freeze-nulls"]) == 0
    assert "c" in capsys.readouterr().out and not fcn._nulls_path(tmp_path).exists()
    assert script.main(["freeze-nulls", "--apply"]) == 0
    assert "FROZEN" in capsys.readouterr().out and len(fcn.read_null_records(tmp_path)) == 1
    assert script.main(["freeze-nulls", "--apply"]) == 0
    assert "already has its null arm" in capsys.readouterr().out


# --- the report: the judge's rate over the twins (PR 2) -------------------------------------------

def _seal_null_rows(tmp_path, rows):
    """Rows the walker would have settled for a twin: stamped with the null arm's provenance."""
    sealed = [fco._finalize_row(row, fcn.NULL_PROVENANCE) for row in rows]
    fb.append_sealed_rows(sealed, path=fcn._null_outcomes_path(tmp_path),
                          read=lambda: fcn.read_null_outcomes(tmp_path))


def test_a_twin_can_be_confirmed_by_the_judge_and_is_counted_as_a_null_confirmation(tmp_path):
    """The proof the arm can measure anything: a rate that can never be anything but zero measures
    nothing. A twin whose rows are a consistent, spread edge is FORWARD_CONFIRMED exactly as a member
    would be, and the comparison counts it under null."""
    from tests.test_mvp_runtime_crypto_forward_confirmation import _spread_outcomes
    _install_cohort(tmp_path, _record("cand_a", created="2025-12-01T00:00:00Z"))
    _freeze(tmp_path)
    rows = _spread_outcomes(cid="null_v2_cand_a")
    for index, row in enumerate(rows):
        row["settlement_id"] = f"settle_null_{index}"
    _seal_null_rows(tmp_path, rows)
    (line,) = fcn.null_report(tmp_path)
    assert line["null_id"] == "null_v2_cand_a" and line["status"] == "FORWARD_CONFIRMED"
    assert line["maturity"] == fco.MATURITY_CONFIRMED
    comparison = fcn.arm_comparison(tmp_path)
    assert comparison["null"]["1d"]["confirmed"] == 1 and comparison["null"]["1d"]["members"] == 1
    assert comparison["real"]["1d"]["confirmed"] == 0


def test_the_comparison_is_per_timeframe_and_absent_before_a_null_arm(tmp_path):
    _install_cohort(tmp_path, _record("cand_a"), _record("cand_h", family="h", timeframe="1h"))
    assert fcn.arm_comparison(tmp_path) is None
    _freeze(tmp_path)
    comparison = fcn.arm_comparison(tmp_path)
    assert set(comparison["null"]) == {"1d", "1h"} == set(comparison["real"])
    assert comparison["null"]["1h"] == {"members": 1, "with_rows": 0, "at_floor": 0,
                                        "confirmed": 0, "contradicted": 0, "underpowered": 0,
                                        "ever_confirmed": 0, "ever_contradicted": 0}


def test_the_board_prints_the_null_line_beside_the_members(tmp_path):
    from runtime.mvp_runtime.crypto.dashboard import build_status, render_status_text
    _install_cohort(tmp_path, _record("cand_a"))
    assert "null 대조" not in render_status_text(build_status(tmp_path, now=NOW))
    _freeze(tmp_path)
    status = build_status(tmp_path, now=NOW)
    assert status["forward_cohort_null"]["null"]["1d"]["members"] == 1
    assert ("       null 대조(확정·반박/계보, 실제 vs null) 1d 0·0/1 vs 0·0/1 (null 기록 0계보)"
            " · 누적 확정·반박 실제 0·0 vs null 0·0") in render_status_text(status)


def test_an_unreadable_null_arm_is_a_board_warning_not_a_broken_board(tmp_path):
    from runtime.mvp_runtime.crypto.dashboard import build_status
    _install_cohort(tmp_path, _record("cand_a"))
    fcn._nulls_path(tmp_path).write_text("{not json\n", encoding="utf-8")
    status = build_status(tmp_path, now=NOW)
    assert status["forward_cohort_null"] is None
    assert any("null arm unreadable (FORWARD_COHORT_NULLS_UNREADABLE)" in w for w in status["warnings"])


def test_the_funnel_counts_the_twins_as_it_counts_the_members(tmp_path):
    from runtime.mvp_runtime.crypto import strategy_funnel
    _install_cohort(tmp_path, _eager("cand_a"))
    _freeze(tmp_path)
    _null_walk(tmp_path, _frame(range(1, 12), stop_on={5, 9}))
    funnel = strategy_funnel.strategy_funnel(tmp_path)
    counts = funnel["null"]["counts"]
    assert counts["members"] == 1 and counts["with_outcomes"] == 1
    assert counts["maturity:EXPLORATORY"] == 1
    assert "NULL ARM - a coin-flip twin per member, judged the same way (a null CONFIRMED is the judge " \
           "passing noise)" in strategy_funnel.render_text(funnel)


# --- first-verdict stamps on the twins (2026-09-24, SEQUENTIAL_FORWARD_TEST_V0.1 decision Q2) -------

def test_a_twin_is_stamped_the_first_walk_the_judge_confirms_it_and_the_board_counts_it_ever(
        tmp_path, monkeypatch):
    from runtime.mvp_runtime.crypto.dashboard import build_status, render_status_text
    _install_cohort(tmp_path, _record("cand_a"))
    _freeze(tmp_path)
    (twin_id,) = fcn.null_ids(tmp_path)
    real_report = fcn.null_report
    monkeypatch.setattr(fcn, "null_report", lambda root=None: [
        {**line, "status": "FORWARD_CONFIRMED"} for line in real_report(root)])
    summary = _null_walk(tmp_path, _frame(range(1, 5)), now=_day(4))
    assert summary["first_confirmed"] == 1 and "first_confirmed=1" in fcn.status_line(summary)
    monkeypatch.setattr(fcn, "null_report", real_report)  # today's look no longer confirms it
    assert fcn.load_null_positions(tmp_path)["verdicts"] == {twin_id: {"first_confirmed_at_utc": _day(4)}}
    comparison = fcn.arm_comparison(tmp_path)
    assert comparison["null"]["1d"]["confirmed"] == 0 and comparison["null"]["1d"]["ever_confirmed"] == 1
    assert "누적 확정·반박 실제 0·0 vs null 1·0" in render_status_text(build_status(tmp_path, now=_day(4)))


def test_arm_counts_without_a_history_carry_no_ever_counts():
    (cell,) = fcn.arm_counts([{"timeframe": "4h", "candidate_id": "x", "status": "FORWARD_CONFIRMED",
                               "priceable_count": 30}]).values()
    assert "ever_confirmed" not in cell and cell["confirmed"] == 1


# --- v2: the per-leg rate, and superseding a sealed v1 arm (2026-09-25) ---------------------------

def _v1_record(tmp_path, *records):
    """A sealed v1 arm exactly as the live host holds one: v1 ids, the v1 rate rule (pooled count
    over one leg's bars), no ``supersedes``."""
    import runtime.read_only_kernel.integrity as integrity
    (cohort,) = fco.read_cohorts(tmp_path)
    twins = []
    for r in records:
        ev = r["backtest_evidence"]
        rate = min(1.0, ev["closed_count"] / ev["bars_replayed"])
        from runtime.mvp_runtime.crypto.null_control import _null_spec
        from runtime.mvp_runtime.crypto.strategy import StrategySpec
        from runtime.mvp_runtime.crypto.strategy_artifact import admission_evidence
        twins.append({
            "null_id": fcn.null_id(r["candidate_id"]), "parent_candidate_id": r["candidate_id"],
            "parent_rule_hash": r["strategy_rule_hash"], "selected_at_utc": r["created_at_utc"],
            "symbol_scope": list(r["strategy_spec"]["symbol_scope"]),
            "timeframe": r["strategy_spec"]["timeframe"],
            "strategy_family": r["strategy_spec"]["strategy_family"], "signal_rate": round(rate, 6),
            "seed": f"{cohort['cohort_id']}|{r['candidate_id']}",
            "null_spec": _null_spec(StrategySpec.from_dict(r["strategy_spec"]), rate).to_dict(),
            "admission_evidence": admission_evidence(r)})
    body = {"forward_cohort_nulls_version": fcn.NULLS_VERSION_V1, "cohort_id": cohort["cohort_id"],
            "frozen_at_utc": NOW, "rate_rule": fcn.RATE_RULE_V1, "null_size": len(twins),
            "members": twins, "skipped": []}
    record = {**body, "record_sha256": integrity.sha256_record(body)}
    path = fcn._nulls_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    return record


def test_a_pooled_parents_twin_is_paced_per_leg():
    """`closed_count` sums the legs and `bars_replayed` is one leg's; v1 divided the first by the
    second and paced a five-leg parent's twin five times too fast on every leg."""
    single = _record("cand_a", closed=60)
    pooled = _record("cand_b", closed=300)
    pooled["backtest_evidence"]["symbols_replayed"] = 5
    assert fcn.trade_rate(pooled) == fcn.trade_rate(single) == 60 / expected_replayed_bars("1d")


def test_a_sealed_v1_arm_still_reads_and_a_v2_supersedes_it_once(tmp_path):
    a = _record("cand_a", closed=300)
    a["backtest_evidence"]["symbols_replayed"] = 5
    b = _record("cand_b", family="other")
    _install_cohort(tmp_path, a, b)
    v1 = _v1_record(tmp_path, a, b)
    assert [r["forward_cohort_nulls_version"] for r in fcn.read_null_records(tmp_path)] == [fcn.NULLS_VERSION_V1]
    (v2,) = _freeze(tmp_path)
    assert v2["forward_cohort_nulls_version"] == fcn.NULLS_VERSION and v2["rate_rule"] == fcn.RATE_RULE
    assert v2["supersedes"] == {"version": fcn.NULLS_VERSION_V1, "record_sha256": v1["record_sha256"],
                                "rate_rule": fcn.RATE_RULE_V1}
    rates = {t["parent_candidate_id"]: t["signal_rate"] for t in v2["members"]}
    old = {t["parent_candidate_id"]: t["signal_rate"] for t in v1["members"]}
    assert rates["cand_a"] == round(old["cand_a"] / 5, 6), "the pooled parent's twin slows by its legs"
    assert rates["cand_b"] == old["cand_b"], "a one-leg parent's twin is unchanged"
    assert [t["seed"] for t in v2["members"]] == [t["seed"] for t in v1["members"]]
    assert fcn.freeze_nulls(tmp_path, now=NOW, apply=True) == []
    assert [r["forward_cohort_nulls_version"] for r in fcn.active_null_records(tmp_path)] == [fcn.NULLS_VERSION]


def test_after_re_freezing_only_v2_walks_and_v1_rows_are_never_priced_into_it(tmp_path):
    a = _eager("cand_a")
    _install_cohort(tmp_path, a)
    _v1_record(tmp_path, a)
    frame = _frame(range(1, 12), stop_on={5, 9})
    first = _null_walk(tmp_path, frame)
    assert first["members"] == 1 and {r["candidate_id"] for r in fcn.read_null_outcomes(tmp_path)} == {"null_cand_a"}
    _freeze(tmp_path)
    assert [w["candidate_id"] for ms in fcn.null_walk_plan(tmp_path).values() for w, _ in ms] == ["null_v2_cand_a"]
    (line,) = fcn.null_report(tmp_path)
    assert line["null_id"] == "null_v2_cand_a" and line["priceable_count"] == 0
    # The book still loads with the v1 entries in it, and the v2 twin re-walks from selection once.
    again = _null_walk(tmp_path, frame)
    assert again["members"] == 1 and again["settled"] >= 1
    assert fcn.load_null_positions(tmp_path)["entries"]
    assert _null_walk(tmp_path, frame)["settled"] == 0
    ids = {r["candidate_id"] for r in fcn.read_null_outcomes(tmp_path)}
    assert ids == {"null_cand_a", "null_v2_cand_a"}
