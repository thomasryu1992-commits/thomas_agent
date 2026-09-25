"""Hypothesis trials walked forward beside a coin-flip twin each (`crypto/forward_trial.py`;
HYPOTHESIS_TRIAL_V0.1 option C, PR3).

Evidence only if it is isolated (the cohort, its null arm and the forward book never see a trial
row), deterministic (a re-walk mints nothing), clocked from the mint, and paced honestly (the
twin's rate is per leg, not the pooled count over one leg's bars).
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.crypto import forward_book as fb
from runtime.mvp_runtime.crypto import forward_cohort as fco
from runtime.mvp_runtime.crypto import forward_cohort_null as fcn
from runtime.mvp_runtime.crypto import forward_trial as ftr
from runtime.mvp_runtime.crypto import pool_state
from runtime.mvp_runtime.crypto.candidate_ranking import expected_replayed_bars
from runtime.mvp_runtime.errors import ToolError
from tests.test_mvp_runtime_crypto_forward_cohort import (
    NOW, _cohort_schedule, _frame, _install_cohort, _record,
)


def _trial(cid, *, closed=60, legs=1, **kw):
    """A trial row as `factory._screen_trials` stores it: derivation, provenance, no parents, the
    proposal it came from, and evidence that says how many legs it was scored on."""
    record = _record(cid, closed=closed, **kw)
    record.update(derivation_type="hypothesis_trial", provenance="mvp_hypothesis_trial",
                  parent_candidate_ids=[],
                  trial_source={"proposal_id": "propose_x", "family": "proposed_x",
                                "strategy_rule_hash": f"src-{cid}"})
    record["backtest_evidence"]["symbols_replayed"] = legs
    return record


def _eager_trial(cid, **kw):
    """A trial whose twin flips a coin that always lands: per-leg rate 1."""
    return _trial(cid, closed=expected_replayed_bars("1d"), **kw)


def _store(tmp_path, *records):
    pool_state.append_candidates([dict(r) for r in records], root=tmp_path)


def _walk(tmp_path, frame, *, now=NOW, persist=True):
    return ftr.run_trial_walk(tmp_path, now=now, frame_for=lambda s, t, b: frame, persist=persist)


FRAME = _frame(range(1, 12), stop_on={5, 9})


# --- membership and the twin ---------------------------------------------------------------------

def test_the_trials_are_the_stores_trial_rows_and_nothing_else(tmp_path):
    _store(tmp_path, _trial("cand_t"), _record("cand_f", family="other"))
    assert [ftr.candidate_id(r) for r in ftr.trial_rows(tmp_path)] == ["cand_t"]
    (members,) = ftr.trial_walk_plan(tmp_path).values()
    assert [walk["candidate_id"] for walk, _ in members] == ["cand_t"]
    assert members[0][0]["selected_at_utc"] == _trial("cand_t")["created_at_utc"]


def test_the_twins_rate_is_per_leg_not_the_pooled_count_over_one_legs_bars():
    """A pooled trial's `closed_count` sums five legs while `bars_replayed` is one leg's; the twin
    walks each leg, so dividing by one leg's bars would make it trade five times its parent."""
    bars = expected_replayed_bars("1d")
    single = _trial("cand_a", closed=60, legs=1)
    pooled = _trial("cand_b", closed=300, legs=5)
    assert ftr.per_leg_trade_rate(single) == pytest.approx(60 / bars)
    assert ftr.per_leg_trade_rate(pooled) == pytest.approx(60 / bars)
    assert ftr.trial_twin(pooled)["signal_rate"] == round(60 / bars, 6)


def test_the_twin_keeps_the_trials_exits_clock_and_direction_and_enters_on_a_coin():
    trial = _trial("cand_t")
    twin = ftr.trial_twin(trial)
    assert twin["null_id"] == "null_cand_t" and twin["parent_candidate_id"] == "cand_t"
    assert twin["selected_at_utc"] == trial["created_at_utc"]
    assert twin["seed"] == "trial|cand_t"
    assert twin["null_spec"]["exit_rules"] == trial["strategy_spec"]["exit_rules"]
    assert twin["null_spec"]["direction"] == trial["strategy_spec"]["direction"]
    assert twin == ftr.trial_twin(trial), "a twin is a pure function of the sealed row"


def test_a_trial_that_cannot_give_a_rate_walks_without_a_twin(tmp_path):
    trial = _trial("cand_t")
    trial["backtest_evidence"].pop("bars_replayed")
    _store(tmp_path, trial)
    assert ftr.trial_twin(trial) is None
    assert ftr.trial_null_walk_plan(tmp_path) == {}
    assert ftr.trial_walk_plan(tmp_path)


# --- the walk ------------------------------------------------------------------------------------

def test_a_trial_and_its_twin_trade_into_their_own_stores_only(tmp_path):
    _store(tmp_path, _eager_trial("cand_t"))
    summary = _walk(tmp_path, FRAME)
    assert summary["trials"]["members"] == 1 and summary["trials"]["settled"] >= 1
    assert summary["nulls"]["members"] == 1 and summary["nulls"]["settled"] >= 1
    trial_rows = ftr.read_trial_outcomes(tmp_path)
    null_rows = ftr.read_trial_null_outcomes(tmp_path)
    assert {r["candidate_id"] for r in trial_rows} == {"cand_t"}
    assert {r["provenance"] for r in trial_rows} == {ftr.TRIAL_PROVENANCE}
    assert {r["candidate_id"] for r in null_rows} == {"null_cand_t"}
    assert {r["provenance"] for r in null_rows} == {ftr.TRIAL_NULL_PROVENANCE}
    for path in (fco._outcomes_path(tmp_path), fco._positions_path(tmp_path),
                 fcn._null_outcomes_path(tmp_path), fcn._null_positions_path(tmp_path),
                 fb._outcomes_path(tmp_path)):
        assert not path.exists(), path


def test_re_walking_the_same_bars_mints_nothing_new(tmp_path):
    _store(tmp_path, _eager_trial("cand_t"))
    first = _walk(tmp_path, FRAME)
    again = _walk(tmp_path, FRAME)
    assert first["trials"]["settled"] >= 1
    assert again["trials"]["settled"] == again["trials"]["opened"] == 0
    assert again["nulls"]["settled"] == again["nulls"]["opened"] == 0


def test_the_cohort_walk_is_the_same_with_a_trial_in_the_store(tmp_path):
    """A trial is never a member, and its walk writes nothing the cohort reads."""
    alone, beside = tmp_path / "alone", tmp_path / "beside"
    for root in (alone, beside):
        _install_cohort(root, _record("cand_a"))
    _store(beside, _eager_trial("cand_t"))
    fco.run_cohort_walk(alone, now=NOW, frame_for=lambda s, t, b: FRAME)
    fco.run_cohort_walk(beside, now=NOW, frame_for=lambda s, t, b: FRAME)
    _walk(beside, FRAME)
    assert fco.read_cohort_outcomes(alone) == fco.read_cohort_outcomes(beside)
    assert "cand_t" not in fco.member_candidate_ids(beside)


def test_a_book_entry_for_something_that_is_not_a_trial_is_refused(tmp_path):
    _store(tmp_path, _eager_trial("cand_t"))
    _walk(tmp_path, FRAME)
    path = ftr._positions_path(tmp_path)
    book = json.loads(path.read_text(encoding="utf-8"))
    key, entry = next(iter(book["entries"].items()))
    book["entries"][key.replace("cand_t", "cand_x")] = {**entry, "lineage": "cand:cand_x"}
    path.write_text(json.dumps(book), encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        ftr.load_trial_positions(tmp_path)
    assert exc.value.reason_code == fco.FORWARD_COHORT_POSITIONS_INVALID


# --- the report ----------------------------------------------------------------------------------

def test_the_report_pairs_each_trial_with_its_twin(tmp_path):
    _store(tmp_path, _eager_trial("cand_t"))
    _walk(tmp_path, FRAME)
    (line,) = ftr.trial_report(tmp_path)
    assert line["candidate_id"] == "cand_t"
    assert line["trial_source"]["family"] == "proposed_x"
    assert line["minted_at_utc"] == _trial("cand_t")["created_at_utc"]
    assert "status" in line and "maturity" in line
    assert line["twin"]["null_id"] == "null_cand_t" and "status" in line["twin"]
    assert line["priceable_count"] == len(ftr.read_trial_outcomes(tmp_path))


def test_the_status_line_names_both_walks():
    line = ftr.status_line({
        "trials": {"members": 2, "walked": 5, "opened": 1, "settled": 0, "failed": [],
                   "first_confirmed": 1},
        "nulls": {"members": 2, "opened": 3, "settled": 2, "failed": ["BTCUSDT 1d: X"]},
    })
    assert line == ("trials members=2 walked=5 opened=1 settled=0 first_confirmed=1 "
                    "nulls opened=3 settled=2 failed=1")


# --- the scheduled fire --------------------------------------------------------------------------

def _fire(tmp_path, monkeypatch, frame_for):
    from runtime.mvp_runtime import scheduler
    monkeypatch.setattr(fco, "collector_frames", lambda root, *, now: frame_for)
    return scheduler._execute(
        _cohort_schedule(), now=NOW, ledger=None, working_memory=None,
        programization=None, repo_root=tmp_path, executor=lambda **_: {},
    )


def test_the_fire_walks_the_trials_after_the_cohort_and_says_so(tmp_path, monkeypatch):
    _install_cohort(tmp_path, _record("cand_a"))
    _store(tmp_path, _eager_trial("cand_t"))
    status = _fire(tmp_path, monkeypatch, lambda s, t, b: FRAME)
    assert status.startswith("forward_cohort members=1 ")
    assert " | trials members=1 " in status
    assert ftr.read_trial_outcomes(tmp_path)


def test_a_fire_with_no_trial_says_nothing_about_trials(tmp_path, monkeypatch):
    _install_cohort(tmp_path, _record("cand_a"))
    status = _fire(tmp_path, monkeypatch, lambda s, t, b: FRAME)
    assert "trials" not in status


def test_a_broken_trial_book_is_named_and_the_cohort_walk_still_lands(tmp_path, monkeypatch):
    _install_cohort(tmp_path, _record("cand_a"))
    _store(tmp_path, _eager_trial("cand_t"))
    ftr._positions_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    ftr._positions_path(tmp_path).write_text("{not json", encoding="utf-8")
    status = _fire(tmp_path, monkeypatch, lambda s, t, b: FRAME)
    assert status.startswith("forward_cohort members=1 ")
    assert "trials failed=FORWARD_COHORT_UNREADABLE" in status
    assert fco.read_cohort_outcomes(tmp_path)


def test_a_broken_twin_book_is_named_as_the_twins_and_the_trials_still_walk(tmp_path, monkeypatch):
    _install_cohort(tmp_path, _record("cand_a"))
    _store(tmp_path, _eager_trial("cand_t"))
    ftr._null_positions_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    ftr._null_positions_path(tmp_path).write_text("{not json", encoding="utf-8")
    status = _fire(tmp_path, monkeypatch, lambda s, t, b: FRAME)
    assert "trial_nulls failed=FORWARD_COHORT_UNREADABLE" in status
    assert "trials failed" not in status and " | trials members=1 " in status
    assert ftr.read_trial_outcomes(tmp_path)


# --- closing a trial (PR4) -----------------------------------------------------------------------

LATER = "2026-07-25T00:00:00Z"


def _confirmed_trial(cid):
    record = _eager_trial(cid)
    # A holdout the judge reads as CONFIRMED: deep and clearly positive (`robustness.holdout_status`).
    record["backtest_evidence"]["holdout"] = {
        "closed_count": 200, "expectancy": 0.5, "stdev_r": 1.0,
        "period_r": [9.0, 11.0, 10.0, 10.5, 9.5, 10.0, 11.0, 9.0, 10.0, 10.0],
        "period_trades": [20] * 10}
    return record


def test_a_retire_is_sealed_and_stops_both_walks(tmp_path):
    _store(tmp_path, _eager_trial("cand_t"))
    _walk(tmp_path, _frame(range(1, 8)))
    marks = {k: e["last_seen_candle"] for k, e in ftr.load_trial_positions(tmp_path)["entries"].items()}
    record = ftr.close_trial(tmp_path, "cand_t", decision="retire", reason="holdout contradicted",
                             now=NOW, apply=True)
    assert record["forward_at_close"]["holdout_status"] == "INSUFFICIENT"
    assert ftr.closed_trial_ids(tmp_path) == {"cand_t"}
    assert ftr.trial_walk_plan(tmp_path) == {} and ftr.trial_null_walk_plan(tmp_path) == {}
    after = _walk(tmp_path, FRAME, now=LATER)
    assert after["trials"]["members"] == 0 and after["nulls"]["members"] == 0
    # The book still loads (the closed trial's entries stay) and its marks did not move.
    book = ftr.load_trial_positions(tmp_path)["entries"]
    assert {k: e["last_seen_candle"] for k, e in book.items()} == marks
    (line,) = ftr.trial_report(tmp_path)
    assert line["close"]["decision"] == "retire"


def test_a_dry_close_writes_nothing(tmp_path):
    _store(tmp_path, _eager_trial("cand_t"))
    ftr.close_trial(tmp_path, "cand_t", decision="retire", reason="r", now=NOW)
    assert not ftr._closes_path(tmp_path).exists()
    assert ftr.closed_trial_ids(tmp_path) == frozenset()


def test_graduation_needs_the_trials_own_holdout_confirmed(tmp_path):
    """Q5: no new threshold — the install gate is the existing holdout one, and a close must not
    promise an install that gate would refuse."""
    _store(tmp_path, _eager_trial("cand_t"), _confirmed_trial("cand_c"))
    with pytest.raises(ToolError) as exc:
        ftr.close_trial(tmp_path, "cand_t", decision="graduate", reason="looks good", now=NOW, apply=True)
    assert exc.value.reason_code == ftr.HYPOTHESIS_TRIAL_NOT_GRADUABLE
    record = ftr.close_trial(tmp_path, "cand_c", decision="graduate", reason="install", now=NOW, apply=True)
    assert record["decision"] == "graduate"
    assert ftr.closed_trial_ids(tmp_path) == {"cand_c"}


@pytest.mark.parametrize("case,code", [
    ("unknown", "HYPOTHESIS_TRIAL_UNKNOWN"), ("twice", "HYPOTHESIS_TRIAL_ALREADY_CLOSED"),
    ("no_reason", "HYPOTHESIS_TRIAL_CLOSE_INVALID"), ("bad_decision", "HYPOTHESIS_TRIAL_CLOSE_INVALID"),
])
def test_a_close_refuses_what_it_cannot_honestly_record(tmp_path, case, code):
    _store(tmp_path, _eager_trial("cand_t"), _record("cand_f", family="other"))
    kwargs = {"decision": "retire", "reason": "r", "now": NOW, "apply": True}
    target = "cand_t"
    if case == "unknown":
        target = "cand_f"
    elif case == "twice":
        ftr.close_trial(tmp_path, "cand_t", **kwargs)
    elif case == "no_reason":
        kwargs["reason"] = "  "
    else:
        kwargs["decision"] = "promote"
    with pytest.raises(ToolError) as exc:
        ftr.close_trial(tmp_path, target, **kwargs)
    assert exc.value.reason_code == code


def test_an_edited_close_is_refused(tmp_path):
    _store(tmp_path, _eager_trial("cand_t"))
    ftr.close_trial(tmp_path, "cand_t", decision="retire", reason="r", now=NOW, apply=True)
    path = ftr._closes_path(tmp_path)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["decision"] = "graduate"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        ftr.read_trial_closes(tmp_path)
    assert exc.value.reason_code == ftr.HYPOTHESIS_TRIAL_CLOSES_TAMPERED


def test_a_close_frees_the_slot_but_never_re_queues_the_proposal(tmp_path):
    from runtime.mvp_runtime.crypto import factory
    trials = [_eager_trial(f"cand_{i}") for i in range(factory.MAX_OPEN_TRIALS)]
    _store(tmp_path, *trials)
    rows = pool_state.read_candidates(tmp_path)
    assert factory.open_trial_count(rows) == factory.MAX_OPEN_TRIALS
    ftr.close_trial(tmp_path, "cand_0", decision="retire", reason="r", now=NOW, apply=True)
    assert factory.open_trial_count(rows, ftr.closed_trial_ids(tmp_path)) == factory.MAX_OPEN_TRIALS - 1
    assert "src-cand_0" in factory.trial_source_hashes(rows)


# --- the board and the funnel (PR4) ---------------------------------------------------------------

def test_the_board_summary_counts_open_closed_and_both_arms_per_timeframe(tmp_path):
    _store(tmp_path, _eager_trial("cand_t"), _eager_trial("cand_u", family="other"))
    _walk(tmp_path, FRAME)
    ftr.close_trial(tmp_path, "cand_u", decision="retire", reason="r", now=NOW, apply=True)
    board = ftr.board_summary(tmp_path)
    assert (board["trials"], board["open"], board["closed"], board["cap"]) == (2, 1, 1, 4)
    assert set(board["real"]) == {"1d"} and set(board["null"]) == {"1d"}, "twins filed under their timeframe"
    assert board["real"]["1d"]["members"] == 2 and board["null"]["1d"]["members"] == 2
    assert ftr.board_summary(tmp_path / "empty") is None


def test_the_board_prints_the_trial_line(tmp_path):
    from runtime.mvp_runtime.crypto.dashboard import build_status, render_status_text
    _store(tmp_path, _eager_trial("cand_t"))
    _walk(tmp_path, FRAME)
    status = build_status(tmp_path, now=NOW)
    assert status["hypothesis_trials"]["open"] == 1
    text = render_status_text(status)
    assert "트라이얼 열림 1/4 · 종료 0 · 기록 1 · 확정·반박/계보 1d " in text
    assert "hypothesis_trials" in build_status(tmp_path / "empty", now=NOW)
    assert build_status(tmp_path / "empty", now=NOW)["hypothesis_trials"] is None


def test_the_funnel_has_a_trials_section(tmp_path, capsys):
    from scripts import strategy_funnel as script
    _store(tmp_path, _eager_trial("cand_t"))
    _walk(tmp_path, FRAME)
    assert script.main([], root=tmp_path) == 0
    out = capsys.readouterr().out
    assert "HYPOTHESIS TRIALS" in out
    assert "cand_t 1d breakout [open] holdout INSUFFICIENT" in out
    assert script.main(["--json"], root=tmp_path) == 0
    funnel = json.loads(capsys.readouterr().out)
    assert funnel["trials"]["lines"][0]["candidate_id"] == "cand_t"
    assert funnel["trials"]["twins"]["counts"]["members"] == 1


# --- the operator script (PR4) --------------------------------------------------------------------

def test_the_script_lists_and_closes_only_with_apply(tmp_path, capsys):
    from scripts import hypothesis_trial as script
    _store(tmp_path, _eager_trial("cand_t"))
    assert script.main(["list"], root=tmp_path) == 0
    assert "1 open of 4 slots" in capsys.readouterr().out
    assert script.main(["close", "cand_t", "--retire", "--reason", "r"], root=tmp_path) == 0
    assert "DRY RUN" in capsys.readouterr().out and not ftr._closes_path(tmp_path).exists()
    assert script.main(["close", "cand_t", "--graduate", "--reason", "r", "--apply"], root=tmp_path) == 2
    assert "HYPOTHESIS_TRIAL_NOT_GRADUABLE" in capsys.readouterr().err
    assert ftr.closed_trial_ids(tmp_path) == frozenset()
    assert script.main(["close", "cand_t", "--retire", "--reason", "r", "--apply"], root=tmp_path) == 0
    assert ftr.closed_trial_ids(tmp_path) == {"cand_t"}
