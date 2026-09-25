"""Hypothesis trials, minted inside a cohort factory fire (HYPOTHESIS_TRIAL_V0.1, option C, PR2).

A proposer acceptance was judged on 120 bars of one symbol. A trial re-scores ONE accepted
proposal the way the factory scores its own mints — its own timeframe, factory depth, every leg
of the cohort — and stores it once as a `hypothesis_trial` row. PR1 (#977) made that row
unpromotable and unbreedable before any existed; these tests are about minting it without
moving anything the factory already does.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime import scheduler as scheduler_mod
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.crypto import factory, pool, proposer
from runtime.mvp_runtime.crypto.factory import run_factory
from runtime.mvp_runtime.crypto.strategy import StrategySpec
from runtime.mvp_runtime.scheduler import KIND_FACTORY, ScheduleStore, build_schedule, run_due
from runtime.mvp_runtime.store import LEDGER_REL, RECORDS_FILE, LedgerStore
from tests.test_mvp_runtime_crypto_factory import NOW, _trending_snapshot, _weak_trend_snapshot


def _proposal_spec(*, family="proposed_trend_long", timeframe="1d", conditions=None):
    """A proposal's spec as the proposer stores it: one symbol, its own rule hash."""
    raw = {
        "schema_version": "strategy_spec.v1",
        "strategy_id": "PROP-001", "strategy_version": "1.0", "strategy_family": family,
        "symbol_scope": ["BTCUSDT"], "timeframe": timeframe, "direction": "long",
        "entry_rules": {"operator": "AND", "conditions": conditions or [
            {"feature": "close", "comparison": ">", "value_from": "ma20"}]},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 2.0,
                       "max_holding_bars": 10},
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    }
    return StrategySpec.from_dict(raw).to_dict()


def _queued(spec, *, proposal_id="propose_a", proposed_at="2026-07-20T06:18:00Z"):
    return {"proposal_id": proposal_id, "family": spec["strategy_family"],
            "strategy_rule_hash": spec["strategy_rule_hash"], "proposed_at": proposed_at,
            "spec": spec}


def _cohort():
    return _trending_snapshot(), [_weak_trend_snapshot(symbol="ETHUSDT")]


def _run(snapshot, cohort, *, trials=None, existing=()):
    return run_factory(snapshot, active_pool={"active_strategies": []},
                       existing_candidates=list(existing), now=NOW,
                       cohort_snapshots=cohort, trial_proposals=trials)


# --- the row ---------------------------------------------------------------------------------

def test_a_cohort_fire_mints_one_trial_row_scored_across_every_leg():
    snapshot, cohort = _cohort()
    result = _run(snapshot, cohort, trials=[_queued(_proposal_spec())])
    trials = [c for c in result["candidates"] if factory.is_trial(c)]
    assert len(trials) == 1
    row = trials[0]
    assert row["derivation_type"] == "hypothesis_trial"
    assert row["provenance"] == "mvp_hypothesis_trial"
    assert row["parent_candidate_ids"] == []
    assert row["strategy_spec"]["symbol_scope"] == ["BTCUSDT", "ETHUSDT"]
    assert row["strategy_spec"]["created_by"] == "mvp_hypothesis_trial"
    assert row["strategy_id"].startswith("T")
    assert row["generation_id"] == result["generation_id"]
    assert row["trial_source"]["strategy_rule_hash"] == _proposal_spec()["strategy_rule_hash"]
    # Scored on the cohort, not on the primary alone.
    assert row["backtest_evidence"]["closed_count"] > 0
    assert result["trial"]["status"] == "minted"
    assert "rows" not in result["trial"]


def test_a_trial_row_carries_no_mint_params_so_it_never_centres_a_search():
    """`_best_mint_params` reads `mint_params` as a search centre for the row's family; a
    proposal's point is not a draw from any template's space."""
    snapshot, cohort = _cohort()
    result = _run(snapshot, cohort, trials=[_queued(_proposal_spec())])
    row = next(c for c in result["candidates"] if factory.is_trial(c))
    assert "mint_params" not in row
    assert factory._best_mint_params([row], symbol="BTCUSDT", timeframe="1d",
                                     scope=["BTCUSDT", "ETHUSDT"]) == {}


def test_the_stored_proposal_hash_is_dropped_before_parsing():
    """The proposal's hash is its single-symbol spec's; `from_dict` refuses a hash that does not
    match the rules ("tampered or stale"), and widening the scope guarantees that."""
    spec = _proposal_spec()
    trial = factory.trial_spec_dict(spec, scope=["ETHUSDT", "BTCUSDT"], generation_id="GEN-009",
                                    strategy_id="T001", venue="binance_futures")
    parsed = StrategySpec.from_dict(trial)
    assert parsed.strategy_rule_hash != spec["strategy_rule_hash"]
    assert list(parsed.to_dict()["symbol_scope"]) == ["BTCUSDT", "ETHUSDT"]


def test_the_rotation_is_what_it_was_without_the_trial():
    """The trial is screened after every rotation draw and is not counted in `generated=N/M`, so
    the fire's own rows are byte-for-byte the rows it mints when not asked."""
    snapshot, cohort = _cohort()
    plain = _run(snapshot, cohort)
    with_trial = _run(snapshot, cohort, trials=[_queued(_proposal_spec())])
    rotation = [c for c in with_trial["candidates"] if not factory.is_trial(c)]
    assert rotation == plain["candidates"]
    assert with_trial["accepted_count"] == plain["accepted_count"]
    assert with_trial["requested_count"] == plain["requested_count"]
    assert "trial" not in plain


def test_a_1h_cohort_fire_pools_the_trial_but_not_its_own_mints():
    """`POOLED_TIMEFRAMES` keeps the factory's 1h rows single-symbol (the live book's direction
    capacity). A trial never reaches the live book, so it is scored across the cohort anyway."""
    snapshot = {**_trending_snapshot(), "timeframe": "1h"}
    cohort = [{**_weak_trend_snapshot(symbol="ETHUSDT"), "timeframe": "1h"}]
    result = _run(snapshot, cohort, trials=[_queued(_proposal_spec(timeframe="1h"))])
    # The fire itself stays single-symbol (this fixture starves the 1h templates, so the flag
    # is what says it rather than the rows).
    assert result["pooled"] is False and result["pooled_symbols"] == []
    seeded = [c for c in result["candidates"] if not factory.is_trial(c)]
    assert all(c["strategy_spec"]["symbol_scope"] == ["BTCUSDT"] for c in seeded)
    trial = next(c for c in result["candidates"] if factory.is_trial(c))
    assert trial["strategy_spec"]["symbol_scope"] == ["BTCUSDT", "ETHUSDT"]


# --- what refuses, and what only skips -------------------------------------------------------

def test_a_refused_proposal_does_not_hold_the_queue_shut():
    """A zero-trade proposal at the head is refused with its reason, and the next one mints."""
    never = _proposal_spec(family="proposed_never_long", conditions=[
        {"feature": "close", "comparison": "<", "value": 0}])
    snapshot, cohort = _cohort()
    result = _run(snapshot, cohort, trials=[_queued(never, proposal_id="p1"),
                                            _queued(_proposal_spec(), proposal_id="p2")])
    assert [r["reason"] for r in result["trial"]["refused"]] == ["no_trades"]
    assert result["trial"]["refused"][0]["strategy_rule_hash"] == never["strategy_rule_hash"]
    assert [m["proposal_id"] for m in result["trial"]["minted"]] == ["p2"]
    assert factory.trial_status(result["trial"]) == "minted:proposed_trend_long,refused:1"


def test_a_proposal_that_breaks_the_scorer_is_refused_and_the_fire_survives(monkeypatch):
    """A proposal is model-written and is scored inside the fire that mints the rotation."""
    snapshot, cohort = _cohort()
    plain = _run(snapshot, cohort)

    def _boom(spec, snapshots, **kwargs):
        if spec.strategy_family == "proposed_trend_long":
            raise ZeroDivisionError("bad spec")
        return real(spec, snapshots, **kwargs)

    real = factory.backtest_spec_pooled
    monkeypatch.setattr(factory, "backtest_spec_pooled", _boom)
    result = _run(snapshot, cohort, trials=[_queued(_proposal_spec())])
    assert [r["reason"] for r in result["trial"]["refused"]] == ["scoring_error"]
    assert "ZeroDivisionError" in result["trial"]["refused"][0]["detail"]
    assert result["candidates"] == plain["candidates"]


def test_a_family_the_rotation_mints_or_retired_is_not_a_new_hypothesis():
    installed = factory.TEMPLATES[0].family
    retired = sorted(factory.RETIRED_FAMILIES)[0]
    snapshot, cohort = _cohort()
    result = _run(snapshot, cohort, trials=[
        _queued(_proposal_spec(family=installed), proposal_id="p1"),
        _queued(_proposal_spec(family=retired), proposal_id="p2"),
    ])
    assert [r["reason"] for r in result["trial"]["refused"]] == ["known_family", "known_family"]
    assert result["trial"]["status"] == "refused"
    assert not any(factory.is_trial(c) for c in result["candidates"])


def test_one_fire_screens_a_bounded_number_of_proposals():
    never = [_proposal_spec(family=f"proposed_never_{i}_long", conditions=[
        {"feature": "close", "comparison": "<", "value": -i}]) for i in range(5)]
    snapshot, cohort = _cohort()
    result = _run(snapshot, cohort, trials=[_queued(s, proposal_id=f"p{i}")
                                            for i, s in enumerate(never)])
    assert len(result["trial"]["refused"]) == factory.MAX_TRIAL_SCREENS_PER_FIRE


@pytest.mark.parametrize("case", ["empty", "not_cohort", "cap"])
def test_the_fire_level_skips_refuse_no_proposal(case):
    """A skip is about the FIRE; recording it as a refusal would drop a proposal from the queue
    (and the backlog) that nobody scored."""
    snapshot, cohort = _cohort()
    existing = []
    trials = [_queued(_proposal_spec())]
    if case == "empty":
        trials = []
    elif case == "not_cohort":
        cohort = None
    else:
        existing = [{"derivation_type": "hypothesis_trial",
                     "trial_source": {"strategy_rule_hash": f"h{i}"}}
                    for i in range(factory.MAX_OPEN_TRIALS)]
    result = _run(snapshot, cohort, trials=trials, existing=existing)
    assert result["trial"]["status"] == {"empty": "empty", "not_cohort": "skipped:not_cohort",
                                         "cap": "skipped:cap"}[case]
    assert result["trial"]["refused"] == [] and result["trial"]["minted"] == []
    assert not any(factory.is_trial(c) for c in result["candidates"])


# --- what the store sees ---------------------------------------------------------------------

def test_the_store_admits_the_minted_row(tmp_path):
    snapshot, cohort = _cohort()
    result = _run(snapshot, cohort, trials=[_queued(_proposal_spec())])
    pool.append_candidates(result["candidates"], root=tmp_path)
    stored = [r for r in pool.read_candidates(tmp_path) if factory.is_trial(r)]
    assert len(stored) == 1
    assert factory.trial_source_hashes(stored) == {_proposal_spec()["strategy_rule_hash"]}


def test_a_trial_is_not_a_step_of_any_rotation():
    """It carries the fire's generation id and the whole cohort's scope, so at 1h every
    single-symbol context matches it by membership and would count the BTC fire as its own."""
    spec = factory.trial_spec_dict(_proposal_spec(timeframe="1h"),
                                   scope=["BTCUSDT", "ETHUSDT"], generation_id="GEN-042",
                                   strategy_id="T001", venue="binance_futures")
    row = {"derivation_type": "hypothesis_trial", "generation_id": "GEN-042",
           "strategy_spec": spec}
    assert factory.context_rotation_index([row], symbol="ETHUSDT", timeframe="1h") == 0
    seeded = {**row, "derivation_type": "seeded_template"}
    assert factory.context_rotation_index([seeded], symbol="ETHUSDT", timeframe="1h") == 1


# --- the queue and the backlog ---------------------------------------------------------------

def _proposal_row(created_at, proposals, *, timeframe="1h", proposal_id="propose_x"):
    return {"kind": "crypto_strategy_proposal",
            "record": {"created_at": created_at, "timeframe": timeframe,
                       "proposal_id": proposal_id, "proposals": proposals}}


def _accepted(spec):
    return {"family": spec["strategy_family"], "accepted": True, "spec": spec,
            "strategy_rule_hash": spec["strategy_rule_hash"]}


def _factory_row(*, minted=(), refused=()):
    return {"kind": "crypto_factory",
            "record": {"trial": {"minted": [{"strategy_rule_hash": h} for h in minted],
                                 "refused": [{"strategy_rule_hash": h} for h in refused]}}}


QNOW = "2026-09-25T08:50:00Z"


def test_the_queue_is_this_timeframe_unscreened_oldest_first():
    a = _proposal_spec(family="proposed_a_long", timeframe="1h")
    b = _proposal_spec(family="proposed_b_long", timeframe="1h",
                       conditions=[{"feature": "close", "comparison": ">", "value_from": "ema20"}])
    c = _proposal_spec(family="proposed_c_long", timeframe="1h",
                       conditions=[{"feature": "rsi", "comparison": ">", "value": 55}])
    four = _proposal_spec(family="proposed_4h_long", timeframe="4h")
    rows = [
        _proposal_row("2026-09-10T06:18:00Z", [_accepted(b)], proposal_id="p2"),
        _proposal_row("2026-09-10T06:18:00Z", [_accepted(four)], timeframe="4h", proposal_id="p4"),
        _proposal_row("2026-09-08T06:18:00Z", [_accepted(a)], proposal_id="p1"),
        _proposal_row("2026-09-11T06:18:00Z", [_accepted(c), _accepted(a)], proposal_id="p3"),
        _factory_row(refused=[b["strategy_rule_hash"]]),
    ]
    queue = proposer.pending_trial_proposals(rows, timeframe="1h", now=QNOW)
    assert [q["family"] for q in queue] == ["proposed_a_long", "proposed_c_long"]
    assert queue[0]["proposal_id"] == "p1"
    assert proposer.pending_trial_proposals(
        rows, timeframe="1h", now=QNOW, exclude_rule_hashes=[a["strategy_rule_hash"]],
    )[0]["family"] == "proposed_c_long"
    assert [q["family"] for q in proposer.pending_trial_proposals(rows, timeframe="4h", now=QNOW)
            ] == ["proposed_4h_long"]


def test_an_acceptance_scored_on_another_timeframe_is_not_queued():
    """D-0's rule: the ledger keeps the old verdict, and today's reading of it is a rejection."""
    four = _proposal_spec(family="proposed_4h_long", timeframe="4h")
    rows = [_proposal_row("2026-09-10T06:18:00Z", [_accepted(four)], timeframe="1h")]
    assert proposer.pending_trial_proposals(rows, timeframe="4h", now=QNOW) == []


def test_a_screened_proposal_leaves_the_backlog():
    """Minted or refused, a screen is a review — so the tap reopens as trial fires work the
    queue, not only as the window ages it out (HYPOTHESIS_TRIAL_V0.1 Q6)."""
    a = _proposal_spec(family="proposed_a_long", timeframe="1h")
    b = _proposal_spec(family="proposed_b_long", timeframe="1h",
                       conditions=[{"feature": "close", "comparison": ">", "value_from": "ema20"}])
    c = _proposal_spec(family="proposed_c_long", timeframe="1h",
                       conditions=[{"feature": "rsi", "comparison": ">", "value": 55}])
    proposals = [_proposal_row("2026-09-10T06:18:00Z", [_accepted(a), _accepted(b), _accepted(c)])]
    assert proposer.count_unreviewed_backlog(proposals, [], now=QNOW) == 3
    screened = [*proposals, _factory_row(minted=[a["strategy_rule_hash"]],
                                         refused=[b["strategy_rule_hash"]])]
    assert proposer.count_unreviewed_backlog(screened, [], now=QNOW) == 1


# --- the scheduler ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_leftover_factory_child():
    yield
    scheduler_mod._reset_factory_child()


def _fire(tmp_path, request, *, proposals=()):
    store = ScheduleStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.add(build_schedule(kind=KIND_FACTORY, request=request, interval_seconds=86400,
                             created_by="op", now="2026-07-22T10:00:00Z"))
    ledger = LedgerStore(tmp_path / LEDGER_REL)
    for row in proposals:
        ledger.append_records("proposal", {"crypto_strategy_proposal": row["record"]})
    spawn = run_due(store, now="2026-07-23T11:00:00Z", control_store=ControlStore(tmp_path),
                    ledger=ledger, repo_root=tmp_path)
    assert spawn["spawned"] == 1, spawn["results"]
    assert scheduler_mod.factory_child_join(300), "the factory child never finished"
    summary = run_due(store, now="2026-07-23T11:01:00Z", control_store=ControlStore(tmp_path),
                      ledger=ledger, repo_root=tmp_path)
    records = [json.loads(line) for line in
               (tmp_path / LEDGER_REL / RECORDS_FILE).read_text(encoding="utf-8").splitlines()]
    factory_records = [r["record"] for r in records if r["kind"] == "crypto_factory"]
    return summary["results"][0]["status"], factory_records[-1]


# Both fires run at 1d. At 1h the factory replays ~24,000 bars per leg, which made these two the
# slowest tests in the suite (40 s and 19 s) for wiring that does not depend on the timeframe; the
# 1h pooling rule itself is pinned above, on `run_factory`, by
# `test_a_1h_cohort_fire_pools_the_trial_but_not_its_own_mints`.

def test_a_single_symbol_fire_is_not_asked_and_its_line_is_unchanged(tmp_path):
    status, record = _fire(tmp_path, "BTCUSDT 1d")
    assert "trial=" not in status
    assert "trial" not in record


def test_a_cohort_fire_reads_the_queue_from_the_ledger_and_says_what_it_did(tmp_path):
    """The fixture frame is deterministic, so the outcome is pinned: an assertion that accepts
    either `minted` or `refused` passes whether or not the queue reached the factory at all."""
    spec = _proposal_spec(timeframe="1d")
    proposals = [_proposal_row("2026-07-22T06:18:00Z", [_accepted(spec)], timeframe="1d")]
    status, record = _fire(tmp_path, "BTCUSDT,ETHUSDT 1d", proposals=proposals)
    assert " trial=minted:proposed_trend_long" in status
    assert record["trial"]["status"] == "minted" and record["trial"]["refused"] == []
    minted = record["trial"]["minted"]
    assert [m["strategy_rule_hash"] for m in minted] == [spec["strategy_rule_hash"]]
    stored = [r for r in pool.read_candidates(tmp_path) if factory.is_trial(r)]
    assert [r["candidate_id"] for r in stored] == [minted[0]["candidate_id"]]


# --- the readers that render every fire ------------------------------------------------------

def test_the_board_and_the_funnel_render_over_a_store_holding_a_trial(tmp_path, capsys):
    """The board renders after every fire; a reader that keyed on a template family or on the
    factory's provenance would break at the first trial fire rather than in a test."""
    from runtime.mvp_runtime.crypto.dashboard import build_status, render_status_text
    from scripts import strategy_funnel as funnel_script

    snapshot, cohort = _cohort()
    result = _run(snapshot, cohort, trials=[_queued(_proposal_spec())])
    pool.append_candidates(result["candidates"], root=tmp_path)
    assert any(factory.is_trial(r) for r in pool.read_candidates(tmp_path))
    assert render_status_text(build_status(tmp_path, now="2026-09-25T09:00:00Z"))
    assert funnel_script.main([], root=tmp_path) == 0
    assert capsys.readouterr().out
