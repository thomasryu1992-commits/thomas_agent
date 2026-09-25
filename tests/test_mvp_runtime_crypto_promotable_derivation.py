"""The promotion door asks what a row MEASURED and never asked how it was MADE.

`docs/REMAINING_WORK.md` §I2 designs a trial rotation slot: a family an LLM proposed, minted
into the store so it can accrue the family-level evidence its own install decision needs, and
quarantined from the live path throughout. Verified 2026-08-05 while that design was written:
`scripts/promote_strategy_candidates.py` names `provenance` in a report line and filters on
neither it nor `derivation_type`, so such a row would reach the live pool through the ordinary
door — the one piece of that design that is not optional.

The door is built here, BEFORE anything mints a row it must stop. That ordering is the point.
A quarantine added in the same increment that starts minting the rows it quarantines is the
shape of change this repo has historically merged with one half missing (the evidence-depth
axis reached `promotable_backlog` seven minutes after it reached the door), and a quarantine
is worth nothing on the day it is late.

That design became `docs/proposals/HYPOTHESIS_TRIAL_V0.1.md` (option C), and its derivation is
``hypothesis_trial``: admitted by the store, refused by the door, and barred from parenting a
child the door would take (`factory.BREEDING_DERIVATION_TYPES`). The tests below assert on
fixtures, because the quarantine has to stand before the first trial row is minted.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import factory, pool
from runtime.mvp_runtime.crypto.pool import (
    DERIVATION_TYPES,
    PROMOTABLE_DERIVATION_TYPES,
    assert_promotable_derivation,
)
from runtime.mvp_runtime.errors import ToolError


def _record(cid, derivation=None):
    """One candidate row, carrying only what this gate reads."""
    row = {"candidate_id": cid}
    if derivation is not None:
        row["derivation_type"] = derivation
    return row


# --- what the gate takes and what it stops -------------------------------------

@pytest.mark.parametrize("derivation", sorted(PROMOTABLE_DERIVATION_TYPES))
def test_every_derivation_the_factory_mints_today_is_promotable(derivation):
    """The gate must be a no-op on the current store. A door that starts refusing real rows
    the day it lands is not a quarantine, it is an outage."""
    assert_promotable_derivation([_record("cand_ok", derivation)])


def test_a_row_that_names_no_derivation_passes_as_legacy():
    """The `candidate_id` legacy rule, again: 402 rows in the real store predate the field.
    Refusing them would be a schema vintage acting as a quality judgement."""
    assert_promotable_derivation([_record("cand_legacy")])


@pytest.mark.parametrize("derivation", ["hypothesis_trial", "trial_family"])
def test_a_derivation_the_pool_does_not_take_is_refused(derivation):
    """The case the door exists for: the trial derivation the store admits, and a name the store
    does not know at all (a row that reached the door without passing the append door)."""
    with pytest.raises(ToolError) as exc:
        assert_promotable_derivation([_record("cand_trial", derivation)])
    assert exc.value.reason_code == "CANDIDATE_DERIVATION_NOT_PROMOTABLE"


def test_the_refusal_names_the_rows_and_the_escape():
    """A gate that will not say which rows it stopped sends the operator back to the listing."""
    with pytest.raises(ToolError) as exc:
        assert_promotable_derivation([
            _record("cand_fine", "seeded_template"),
            _record("cand_trial", "hypothesis_trial"),
        ])
    assert "cand_trial" in exc.value.reason
    assert "cand_fine" not in exc.value.reason
    assert "hypothesis_trial" in exc.value.reason
    assert "--allow-quarantined-derivation" in exc.value.reason


def test_an_explicit_null_derivation_is_refused_rather_than_read_as_legacy():
    """Absence is legacy; a row that WRITES the field as null is a writer that thinks it is
    saying something. Fail closed on the difference rather than collapsing the two."""
    with pytest.raises(ToolError) as exc:
        assert_promotable_derivation([{"candidate_id": "cand_null", "derivation_type": None}])
    assert exc.value.reason_code == "CANDIDATE_DERIVATION_NOT_PROMOTABLE"


# --- the constant that makes this load-bearing ---------------------------------

def test_widening_the_store_does_not_widen_the_door():
    """**This test is the whole mechanism.** `PROMOTABLE_DERIVATION_TYPES` is written as its
    own literal rather than as an alias of `DERIVATION_TYPES`, so that adding a trial derivation
    to the closed set the append door validates does not silently make it promotable.

    The third assertion is the one that fires: it pins the STORE's set, so the commit that
    widens it lands here and has to state, in its own diff, whether the new derivation may
    trade. That is a deliberate ~30 seconds of friction on exactly the change that must not
    happen by accident — and it fired once, on the commit that added ``hypothesis_trial``,
    which answers here: it may not."""
    assert PROMOTABLE_DERIVATION_TYPES <= DERIVATION_TYPES
    assert PROMOTABLE_DERIVATION_TYPES == {"seeded_template", "crossover", "mutation"}
    assert DERIVATION_TYPES == {"seeded_template", "crossover", "mutation", "hypothesis_trial"}
    assert "hypothesis_trial" not in PROMOTABLE_DERIVATION_TYPES


def test_what_may_breed_is_what_may_be_promoted():
    """A fused child is written as ``crossover``, which the door takes, so a row the door refuses
    must not parent one — or the trial would reach the live pool a generation later. The factory
    keeps its own literal (it sits below the door's layer); this is where the two meet."""
    assert factory.BREEDING_DERIVATION_TYPES == PROMOTABLE_DERIVATION_TYPES
    assert factory.BREEDING_DERIVATION_TYPES is not PROMOTABLE_DERIVATION_TYPES


def test_a_trial_row_never_parents_however_well_it_scored():
    """Everything else about the row would qualify it: a score, a parseable spec, a judgeable
    holdout that did not lose. The derivation alone keeps it out."""
    spec = {"strategy_family": "proposed_x", "symbol_scope": ["BTCUSDT"], "timeframe": "1h"}
    evidence = {"holdout": {"closed_count": 60, "expectancy": 0.3}}
    seeded = {"candidate_id": "cand_seeded", "strategy_rule_hash": "h1", "champion_score": 0.5,
              "strategy_spec": spec, "backtest_evidence": evidence,
              "derivation_type": "seeded_template"}
    trial = {**seeded, "candidate_id": "cand_trial", "strategy_rule_hash": "h2",
             "champion_score": 0.99, "derivation_type": "hypothesis_trial"}
    legacy = {k: v for k, v in seeded.items() if k != "derivation_type"}
    legacy.update(candidate_id="cand_legacy", strategy_rule_hash="h3")
    null = {**seeded, "candidate_id": "cand_null", "strategy_rule_hash": "h4", "derivation_type": None}
    ranked = [r["candidate_id"] for r in factory.rank_fusion_parents([seeded, trial, legacy, null])]
    assert sorted(ranked) == ["cand_legacy", "cand_seeded"]


def test_the_store_admits_a_trial_row_with_no_parents_and_only_so():
    """Fresh like a seeded row: a trial is a hypothesis, not a child of stored evidence."""
    from runtime.mvp_runtime.crypto.pool_state import validate_candidate_lineage

    validate_candidate_lineage({"derivation_type": "hypothesis_trial", "parent_candidate_ids": []},
                               frozenset())
    with pytest.raises(ToolError) as exc:
        validate_candidate_lineage(
            {"derivation_type": "hypothesis_trial", "parent_candidate_ids": ["cand_p"]},
            frozenset({"cand_p"}))
    assert exc.value.reason_code == "CANDIDATE_LINEAGE_INVALID"


def test_the_two_sets_are_not_the_same_object():
    """An alias would pass every assertion above today and fail silently on the day it
    matters, which is the failure mode the literal is written against."""
    assert PROMOTABLE_DERIVATION_TYPES is not DERIVATION_TYPES


# --- the listing has to agree with the door ------------------------------------

def test_the_listing_names_the_refused_rows(monkeypatch, capsys):
    """An operator picks from `--list`. A row the door will stop has to be visible as such
    there, not discovered at the ask — the rule the depth axis already follows."""
    from scripts import promote_strategy_candidates as prom

    monkeypatch.setattr(prom.pool_store, "read_candidates", lambda root: [
        {**_record("cand_trial", "trial_family"),
         "strategy_id": "S001", "generation_id": "GEN-001",
         "strategy_rule_hash": "hash-trial",
         "strategy_spec": {"strategy_family": "proposed_x", "symbol_scope": ["BTCUSDT"],
                           "timeframe": "1h"},
         "champion_score": 0.9,
         "backtest_evidence": {"closed_count": 100, "expectancy": 0.05}},
    ])
    assert prom.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "CANDIDATE_DERIVATION_NOT_PROMOTABLE" in out
    assert "--allow-quarantined-derivation" in out


def test_the_listing_stays_quiet_when_nothing_is_quarantined(monkeypatch, capsys):
    """The count is zero on the real store, so the line must not print there — a warning that
    fires every morning is a warning nobody reads by the second week."""
    from scripts import promote_strategy_candidates as prom

    monkeypatch.setattr(prom.pool_store, "read_candidates", lambda root: [
        {**_record("cand_seeded", "seeded_template"),
         "strategy_id": "S001", "generation_id": "GEN-001",
         "strategy_rule_hash": "hash-seeded",
         "strategy_spec": {"strategy_family": "breakout", "symbol_scope": ["BTCUSDT"],
                           "timeframe": "1h"},
         "champion_score": 0.9,
         "backtest_evidence": {"closed_count": 100, "expectancy": 0.05}},
    ])
    assert prom.main(["--list"]) == 0
    assert "CANDIDATE_DERIVATION_NOT_PROMOTABLE" not in capsys.readouterr().out


def test_the_axis_is_counted_in_the_backlog_breakdown():
    """Pinned here as well as in the backlog suite, because the pairing is the actual rule:
    a door axis that the counter does not know about advertises work that BLOCKS."""
    assert "derivation" in pool.BACKLOG_REFUSAL_AXES
