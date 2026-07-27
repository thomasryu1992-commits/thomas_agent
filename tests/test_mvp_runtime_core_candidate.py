"""Core Candidate — the memory ladder's fourth rung (policy §12.6, architecture §8.8).

The ladder is Session -> Working -> Validated -> Core Candidate -> Thomas Core. Three rungs
existed; this one did not. What these tests pin is less "the record has the right fields" than
the two properties that make the rung safe to have at all:

  1. it can only be reached from the rung below (a promoted VALIDATED entry), and
  2. it changes nothing — accepting a proposal is a decision, not an effect.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.mvp_runtime import memory, memory_cli, working_memory
from runtime.mvp_runtime.errors import MemoryBlocked
from runtime.mvp_runtime.paths import repo_root
from runtime.mvp_runtime.working_memory import WorkingMemoryStore

NOW = "2026-07-27T00:00:00Z"
LATER = "2026-07-27T01:00:00Z"


def _validated(**overrides):
    entry = {
        "validated_memory_id": "valmem-abc123",
        "source_candidate_id": "memcand-xyz",
        "candidate_type": "reusable_knowledge",
        "scope": memory.VALIDATED_SCOPE,
        "status": memory.VALIDATED_STATUS,
        "disposition": memory.PROMOTION_DISPOSITION,
        "content": "Thomas consistently prefers a slower rollout over a larger first bet.",
        "promoted_by": "thomas",
        "promotion_reason": "seen across four analyses",
        "promoted_at": NOW,
    }
    entry.update(overrides)
    return entry


def _proposal(**overrides):
    return memory.build_core_candidate(
        _validated(), candidate_type="risk_preference_change",
        rationale="four independent runs pointed at the same risk preference",
        proposed_by="thomas", now=NOW, **overrides,
    )


# --------------------------------------------------------------------------- creation


def test_a_core_candidate_comes_from_the_rung_below():
    candidate = _proposal()
    assert candidate["source_validated_memory_id"] == "valmem-abc123"
    assert candidate["scope"] == memory.CORE_CANDIDATE_SCOPE
    assert candidate["status"] == memory.CORE_CANDIDATE_STATUS
    assert candidate["evidence_refs"] == ["validated_memory:valmem-abc123"]


def test_a_working_memory_candidate_cannot_skip_the_ladder():
    """The load-bearing rule. Without it, a raw model finding could become a proposed change to
    Thomas's values without anyone ever having said yes to it once."""
    raw = {
        "candidate_id": "memcand-1",
        "scope": memory.CANDIDATE_SCOPE,
        "status": memory.CANDIDATE_STATUS,
        "content": "the model thinks Thomas is risk-tolerant",
    }
    with pytest.raises(MemoryBlocked) as exc:
        memory.build_core_candidate(raw, candidate_type="risk_preference_change",
                                    rationale="r", proposed_by="thomas", now=NOW)
    assert exc.value.reason_code == "NOT_VALIDATED_MEMORY"


def test_a_promoted_marker_is_not_a_validated_entry():
    """A PROMOTED retirement marker shares the candidate's fields but is not the promoted
    knowledge — it must not be a usable source either."""
    with pytest.raises(MemoryBlocked) as exc:
        memory.build_core_candidate(
            _validated(status=memory.PROMOTED_STATUS), candidate_type="new_disposition",
            rationale="r", proposed_by="thomas", now=NOW,
        )
    assert exc.value.reason_code == "NOT_VALIDATED_MEMORY"


@pytest.mark.parametrize("kwargs,code", [
    ({"candidate_type": "whatever_i_like"}, "UNKNOWN_CORE_CANDIDATE_TYPE"),
    ({"proposed_by": "   "}, "MISSING_OPERATOR"),
    ({"rationale": ""}, "MISSING_RATIONALE"),
])
def test_creation_fails_closed(kwargs, code):
    base = {"candidate_type": "new_disposition", "rationale": "r", "proposed_by": "thomas"}
    base.update(kwargs)
    with pytest.raises(MemoryBlocked) as exc:
        memory.build_core_candidate(_validated(), now=NOW, **base)
    assert exc.value.reason_code == code


def test_lineage_is_carried_but_never_invented():
    origin = {"task_id": "task-1", "task_revision": 1, "trace_id": "trace-1",
              "core_context_binding_id": "ccb-1", "data_sensitivity": "INTERNAL"}
    with_lineage = memory.build_core_candidate(
        _validated(source_origin=origin), candidate_type="new_disposition",
        rationale="r", proposed_by="thomas", now=NOW,
    )
    assert with_lineage["source_origin"] == origin
    assert "source_origin" not in _proposal()


def test_a_secret_bearing_proposal_is_refused():
    with pytest.raises(MemoryBlocked) as exc:
        memory.build_core_candidate(
            _validated(source_origin={"api_secret": "shhh"}), candidate_type="new_disposition",
            rationale="r", proposed_by="thomas", now=NOW,
        )
    assert exc.value.reason_code == "SECRET_IN_CORE_CANDIDATE"


# --------------------------------------------------------------------------- it grants nothing


def test_a_proposal_grants_nothing_and_says_so_in_fields():
    candidate = _proposal()
    assert candidate["applied_to_core"] is False
    assert candidate["promotable"] is False
    assert candidate["promotion_effect"] == "NONE"
    assert candidate["permission_expansion"] is False


def test_accepting_is_a_decision_not_an_effect():
    """The one a later reader is most likely to get wrong: ACCEPTED does not mean 'in the Core'."""
    accepted = memory.decide_core_candidate(
        _proposal(), decision=memory.CORE_CANDIDATE_ACCEPTED, decided_by="thomas",
        reason="carry into the next Core release", now=LATER,
    )
    assert accepted["status"] == memory.CORE_CANDIDATE_ACCEPTED
    assert accepted["applied_to_core"] is False
    assert accepted["promotion_effect"] == "NONE"
    assert accepted["permission_expansion"] is False


def test_a_decision_is_terminal(tmp_path):
    store = WorkingMemoryStore(tmp_path)
    candidate = _proposal()
    store.append_core_candidates([candidate])
    assert working_memory.find_core_candidate(store, candidate["core_candidate_id"]) is not None

    decided = memory.decide_core_candidate(
        candidate, decision=memory.CORE_CANDIDATE_REJECTED, decided_by="thomas",
        reason="not a Core matter", now=LATER,
    )
    store.append_core_candidates([decided])

    assert working_memory.find_core_candidate(store, candidate["core_candidate_id"]) is None
    assert working_memory.live_core_candidates(store) == []
    with pytest.raises(MemoryBlocked) as exc:
        memory.decide_core_candidate(decided, decision=memory.CORE_CANDIDATE_ACCEPTED,
                                     decided_by="thomas", reason="changed my mind", now=LATER)
    assert exc.value.reason_code == "CORE_CANDIDATE_ALREADY_DECIDED"


@pytest.mark.parametrize("kwargs,code", [
    ({"decision": "MAYBE"}, "UNKNOWN_CORE_CANDIDATE_DECISION"),
    ({"decided_by": ""}, "MISSING_OPERATOR"),
    ({"reason": "  "}, "MISSING_REASON"),
])
def test_decision_fails_closed(kwargs, code):
    base = {"decision": memory.CORE_CANDIDATE_ACCEPTED, "decided_by": "thomas", "reason": "r"}
    base.update(kwargs)
    with pytest.raises(MemoryBlocked) as exc:
        memory.decide_core_candidate(_proposal(), now=LATER, **base)
    assert exc.value.reason_code == code


# --------------------------------------------------------------------------- store behavior


def test_retention_never_ages_out_a_core_candidate(tmp_path):
    """Working memory expires (§12.4); this rung does not. It lives in its own file, so the
    prune compaction cannot reach it even by accident."""
    store = WorkingMemoryStore(tmp_path)
    store.append([{
        "candidate_id": "memcand-1", "scope": memory.CANDIDATE_SCOPE,
        "status": memory.CANDIDATE_STATUS, "content": "x", "created_at": NOW,
        memory.EXPIRES_AT: "2026-07-26T00:00:00Z",
    }])
    candidate = _proposal()
    store.append_core_candidates([candidate])

    removed = store.prune_expired(NOW)

    assert len(removed) == 1
    assert working_memory.live_core_candidates(store) == [candidate]


def test_live_listing_is_latest_wins_not_a_status_filter(tmp_path):
    """A status filter would list a proposal and its decision row as two entries, and would keep
    showing a decided proposal as open."""
    store = WorkingMemoryStore(tmp_path)
    first = _proposal()
    second = memory.build_core_candidate(
        _validated(validated_memory_id="valmem-second"), candidate_type="long_term_goal_change",
        rationale="r", proposed_by="thomas", now=LATER,
    )
    store.append_core_candidates([first, second])
    store.append_core_candidates([memory.decide_core_candidate(
        first, decision=memory.CORE_CANDIDATE_ACCEPTED, decided_by="thomas",
        reason="agreed", now=LATER,
    )])

    live = working_memory.live_core_candidates(store)

    assert [e["core_candidate_id"] for e in live] == [second["core_candidate_id"]]


def test_find_validated_ignores_a_non_validated_row(tmp_path):
    store = WorkingMemoryStore(tmp_path)
    store.append_validated([_validated(status="SOMETHING_ELSE")])
    assert working_memory.find_validated(store, "valmem-abc123") is None


# --------------------------------------------------------------------------- the event


def test_the_event_is_self_hashed_and_claims_no_effect():
    event = memory.build_core_candidate_event(
        _proposal(), action=memory.CORE_CANDIDATE_PROPOSE_ACTION, now=NOW, operator_id="thomas",
    )
    assert event["record_type"] == memory.CORE_CANDIDATE_EVENT_TYPE
    assert event["applied_to_core"] is False
    assert event["promotion_effect"] == "NONE"
    assert event["integrity"]["event_sha256"]


# --------------------------------------------------------------------------- the operator door


class _Ledger:
    def __init__(self):
        self.events = []

    def append_memory_event(self, entry):
        self.events.append(entry)


class _Control:
    def __init__(self, allowed=True, mode="ACTIVE"):
        self._allowed, self.mode = allowed, mode

    def load(self):
        return self

    @property
    def execution_allowed(self):
        return self._allowed

    def refusal_reason_code(self):
        return "RUNTIME_KILLED"


def test_the_cli_walks_a_proposal_from_validated_to_accepted(tmp_path, capsys):
    store = WorkingMemoryStore(tmp_path)
    store.append_validated([_validated()])
    ledger = _Ledger()

    code = memory_cli.main(
        ["core-propose", "--validated-id", "valmem-abc123", "--type", "risk_preference_change",
         "--rationale", "four runs agreed", "--by", "thomas"],
        store=store, ledger=ledger, control_store=_Control(), now=NOW,
    )
    assert code == 0
    live = working_memory.live_core_candidates(store)
    assert len(live) == 1

    code = memory_cli.main(
        ["core-accept", "--candidate-id", live[0]["core_candidate_id"], "--by", "thomas",
         "--reason", "carry into the next release"],
        store=store, ledger=ledger, control_store=_Control(), now=LATER,
    )
    assert code == 0
    assert working_memory.live_core_candidates(store) == []
    assert [e["action"] for e in ledger.events] == [
        memory.CORE_CANDIDATE_PROPOSE_ACTION, memory.CORE_CANDIDATE_DECIDE_ACTION,
    ]
    assert "no Core file changed" in capsys.readouterr().out


def test_the_cli_refuses_an_unknown_source(tmp_path):
    code = memory_cli.main(
        ["core-propose", "--validated-id", "valmem-nope", "--type", "new_disposition",
         "--rationale", "r", "--by", "thomas"],
        store=WorkingMemoryStore(tmp_path), ledger=_Ledger(), control_store=_Control(), now=NOW,
    )
    assert code == 2


def test_the_writing_verbs_are_kill_switch_bound(tmp_path, capsys):
    store = WorkingMemoryStore(tmp_path)
    store.append_validated([_validated()])
    code = memory_cli.main(
        ["core-propose", "--validated-id", "valmem-abc123", "--type", "new_disposition",
         "--rationale", "r", "--by", "thomas"],
        store=store, ledger=_Ledger(), control_store=_Control(allowed=False, mode="KILLED"), now=NOW,
    )
    assert code == 2
    assert "RUNTIME_KILLED" in capsys.readouterr().err
    assert store.read_core_candidates() == []


def test_listing_answers_while_killed(tmp_path):
    """`core-list` is a read; kill_allows admits read_only_status. A killed runtime that cannot
    report what is pending is harder to reason about, not safer."""
    code = memory_cli.main(
        ["core-list"], store=WorkingMemoryStore(tmp_path), ledger=_Ledger(),
        control_store=_Control(allowed=False, mode="KILLED"), now=NOW,
    )
    assert code == 0


# --------------------------------------------------------------------------- the tripwire


def test_no_run_path_module_can_mint_a_core_candidate():
    """`automatic_runtime_promotion_allowed` is false, and the guarantee has to be structural
    rather than a convention someone remembers.

    A Core Candidate says Thomas's identity, values, goals or risk appetite may need to change.
    Nothing that runs on a schedule or off a chat message gets to say that — a proposal must
    come from a person, which is why creation requires an operator identity *and* why no
    autonomous entry point may reach the builder at all. Wiring one is a deliberate decision,
    and this test is what makes it impossible to do by accident.
    """
    entry_points = [
        "runtime/mvp_runtime/pipeline.py",
        "runtime/mvp_runtime/worker.py",
        "runtime/mvp_runtime/scheduler.py",
        "runtime/mvp_runtime/operator.py",
        "runtime/mvp_runtime/frontdesk.py",
        "runtime/mvp_runtime/operator_feedback.py",
    ]
    surface = ("build_core_candidate", "decide_core_candidate", "append_core_candidates",
               "CORE_CANDIDATE_SCOPE", "find_core_candidate")
    offenders = []
    for rel in entry_points:
        path = Path(repo_root()) / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        hits = [needle for needle in surface if needle in text]
        if hits:
            offenders.append(f"{rel}: {hits}")
    assert offenders == [], (
        "a run-path module now reaches the Core Candidate door — proposing a change to Thomas "
        f"Core is a person's act, not a pipeline's: {offenders}"
    )


def test_no_runtime_code_path_can_emit_an_applied_record():
    """`applied_to_core: False` is only a guarantee if no code anywhere sets it true.

    The field is the one thing a later reader will trust without re-deriving it, so the absence
    of a writer for `True` is the actual invariant — not the three assertions above, which only
    show that today's two builders happen to agree."""
    offenders = []
    for path in sorted((Path(repo_root()) / "runtime").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for needle in ('"applied_to_core": True', "'applied_to_core': True",
                       "applied_to_core=True"):
            if needle in text:
                offenders.append(f"{path.relative_to(repo_root())}: {needle}")
    assert offenders == [], (
        "something now writes applied_to_core=True — applying a Core change is the release "
        f"lifecycle's act, and runtime_effect.core_activation_allowed is false: {offenders}"
    )
