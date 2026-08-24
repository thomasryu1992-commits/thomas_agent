"""The candidate store's duplicate check — the thing an append-only store never had.

`candidate_id` is derived from the originating task, so the same finding proposed by twenty
runs stored twenty rows under twenty ids and nothing could see they were one fact. Measured on
the live host 2026-08-24: 316 rows, 129 distinct texts, five of them repeated 38 times each for
170 of the rows. What is pinned here is that a live duplicate is skipped, that an expired one is
NOT (a fact observed again earns a fresh TTL), and — the two traps — that the promotion marker
and the front desk's session turns still get through, because both look like duplicates and
neither is one.
"""

from __future__ import annotations

from runtime.mvp_runtime import memory
from runtime.mvp_runtime.working_memory import WorkingMemoryStore, mark_promoted

NOW = "2026-07-16T09:00:00Z"
LATER = "2026-07-16T09:30:00Z"
AFTER_TTL = "2026-07-30T09:00:00Z"          # past the 7-day working-memory TTL


def _candidate(content, *, cid="memcand_a", now=NOW, kind="reusable_knowledge", ttl_minutes=None):
    """A candidate shaped like `build_memory_candidates` makes them."""
    from runtime.mvp_runtime import timeutil
    return {
        "candidate_id": cid,
        "candidate_type": kind,
        "scope": memory.CANDIDATE_SCOPE,
        "status": memory.CANDIDATE_STATUS,
        "validated": False,
        "promotable": False,
        "content": content,
        "created_at": now,
        memory.EXPIRES_AT: timeutil.plus_minutes(
            now, ttl_minutes if ttl_minutes is not None else memory.WORKING_MEMORY_TTL_MINUTES),
    }


# --- the leak this closes ----------------------------------------------------

def test_the_same_finding_from_a_second_run_is_not_stored_twice(tmp_path):
    """Two runs, two task-derived ids, one fact. The row count is what the digest counts."""
    store = WorkingMemoryStore(tmp_path / "wm")
    store.append([_candidate("recurring-revenue model with plausible early cash flow")])
    written = store.append([
        _candidate("recurring-revenue model with plausible early cash flow",
                   cid="memcand_b", now=LATER)])
    assert written == []
    assert len(store.read_all()) == 1


def test_a_batch_carrying_the_same_finding_twice_stores_it_once(tmp_path):
    """The duplicate does not have to come from an earlier run to be a duplicate."""
    store = WorkingMemoryStore(tmp_path / "wm")
    written = store.append([
        _candidate("scalability: constrained by fulfilment/logistics", cid="memcand_a"),
        _candidate("scalability: constrained by fulfilment/logistics", cid="memcand_b"),
    ])
    assert len(written) == 1
    assert len(store.read_all()) == 1


def test_wrapping_a_finding_differently_is_still_the_same_finding(tmp_path):
    """Whitespace collapses; the same sentence arrives line-wrapped from different runs."""
    store = WorkingMemoryStore(tmp_path / "wm")
    store.append([_candidate("ordering and reordering\n  are automatable")])
    assert store.append([_candidate("ordering and reordering are automatable",
                                    cid="memcand_b")]) == []


def test_a_finding_that_differs_by_a_word_is_a_different_finding(tmp_path):
    """Nothing beyond whitespace is normalised — this check must not merge two facts."""
    store = WorkingMemoryStore(tmp_path / "wm")
    store.append([_candidate("scalability is limited by logistics")])
    store.append([_candidate("scalability is limited by staffing", cid="memcand_b")])
    assert len(store.read_all()) == 2


def test_the_same_text_under_a_different_candidate_type_is_kept(tmp_path):
    store = WorkingMemoryStore(tmp_path / "wm")
    store.append([_candidate("logistics is the constraint")])
    store.append([_candidate("logistics is the constraint", cid="memcand_b",
                             kind="approved_brand_observation_candidate")])
    assert len(store.read_all()) == 2


# --- live, not ever ----------------------------------------------------------

def test_a_finding_re_derived_after_its_ttl_gets_a_fresh_row(tmp_path):
    """A fact observed again after expiry is evidence, not noise. Deduplicating against
    expired rows would let an unpruned store suppress new observations forever."""
    store = WorkingMemoryStore(tmp_path / "wm")
    store.append([_candidate("the market is saturated")])
    written = store.append([_candidate("the market is saturated", cid="memcand_b",
                                       now=AFTER_TTL)])
    assert len(written) == 1
    assert len(store.read_all()) == 2


def test_the_reference_instant_can_be_given_explicitly(tmp_path):
    """`now` decides what counts as live; without it the batch's own stamps do."""
    store = WorkingMemoryStore(tmp_path / "wm")
    store.append([_candidate("the market is saturated")])
    assert store.append([_candidate("the market is saturated", cid="memcand_b")],
                        now=AFTER_TTL) != []


# --- the two neighbours that look identical and are not ----------------------

def test_the_promotion_marker_is_not_refused_as_a_duplicate(tmp_path):
    """`mark_promoted` writes a COPY of the candidate that differs only in status.
    A duplicate check keyed on content alone would reject it, and the candidate would read as
    never promoted — CANDIDATE_GONE's half-true message all over again."""
    store = WorkingMemoryStore(tmp_path / "wm")
    candidate = _candidate("retention compounds the brand")
    store.append([candidate])
    marker = mark_promoted(store, candidate, validated_memory_id="vm_1", now=LATER)
    rows = store.read_all()
    assert marker["status"] == memory.PROMOTED_STATUS
    assert len(rows) == 2
    assert {r["status"] for r in rows} == {memory.CANDIDATE_STATUS, memory.PROMOTED_STATUS}


def test_a_repeated_front_desk_turn_is_still_recorded(tmp_path):
    """Session turns are a different scope. Two identical lines in a conversation are
    conversation, not one fact stored twice, and the SUBMIT_TASK window reads them in order."""
    store = WorkingMemoryStore(tmp_path / "wm")
    turn = {
        "candidate_id": "fdsess_a", "candidate_type": "frontdesk_session_context",
        "scope": "frontdesk_session", "status": memory.CANDIDATE_STATUS,
        "content": "Thomas: 지금\nFrontdesk[QUERY_STATUS]: 대기 중인 작업이 없습니다.",
        "created_at": NOW, memory.EXPIRES_AT: "2026-07-16T21:00:00Z",
    }
    store.append([turn])
    store.append([{**turn, "candidate_id": "fdsess_b", "created_at": LATER}])
    assert len(store.read_all()) == 2


def test_a_row_with_no_content_is_never_treated_as_a_duplicate(tmp_path):
    """Empty content would collide with every other empty one; those rows skip the check."""
    store = WorkingMemoryStore(tmp_path / "wm")
    bare = {"candidate_id": "memcand_a", "scope": memory.CANDIDATE_SCOPE,
            "status": memory.CANDIDATE_STATUS, "created_at": NOW}
    store.append([bare])
    store.append([{**bare, "candidate_id": "memcand_b"}])
    assert len(store.read_all()) == 2


# --- unchanged behaviour -----------------------------------------------------

def test_an_empty_batch_writes_nothing_and_returns_nothing(tmp_path):
    store = WorkingMemoryStore(tmp_path / "wm")
    assert store.append([]) == []
    assert store.read_all() == []


def test_the_prune_still_removes_the_expired_row_the_check_left(tmp_path):
    """The two mechanisms compose: dedup keeps the store from filling, retention empties it."""
    store = WorkingMemoryStore(tmp_path / "wm")
    store.append([_candidate("the market is saturated")])
    store.append([_candidate("the market is saturated", cid="memcand_b", now=AFTER_TTL)])
    removed = store.prune_expired(AFTER_TTL)
    assert [r["candidate_id"] for r in removed] == ["memcand_a"]
    assert [r["candidate_id"] for r in store.read_all()] == ["memcand_b"]
