"""R10 approval-consumption tests.

Consuming an approval is the runtime's first action that spends a governed grant to *do*
something, so — like the R8 write tests — these concentrate on what must fail closed: a grant
that is not APPROVED, has expired, was already spent, or whose bound content drifted since
Thomas saw it; and the safety flag being off. The happy path proves the narrow thing the
increment adds — one APPROVED grant, spent once, promotes exactly the candidate it named — and
nothing wider (the CONSUMED record stays REVIEW_ONLY).

The paths that need a bound task (local Core activation) skip on a core-neutral CI checkout,
like every other binding-dependent suite here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.mvp_runtime import approval, consumption, permission, timeutil
from runtime.mvp_runtime.approval_store import ApprovalStore
from runtime.mvp_runtime.binding import DEFAULT_POINTER_REL, bind_task_to_core
from runtime.mvp_runtime.consumption import _CapableConsumer, _DryRunConsumer, consume_approval
from runtime.mvp_runtime.control import ControlState, ControlStore
from runtime.mvp_runtime.errors import ApprovalBlocked, PersistenceError, SafetyGateBlocked
from runtime.mvp_runtime.intake import build_task
from runtime.mvp_runtime.memory import CANDIDATE_SCOPE, CANDIDATE_STATUS
from runtime.mvp_runtime.safety_gate import APPROVAL_CONSUMPTION, Authorization
from runtime.mvp_runtime.store import LedgerStore
from runtime.mvp_runtime.working_memory import WorkingMemoryStore
from runtime.read_only_kernel import integrity

REPO = Path(__file__).resolve().parents[1]
from tests._helpers import requires_local_core

NOW = "2026-07-16T12:00:00Z"
LATER = "2026-07-16T12:20:00Z"
AFTER_EXPIRY = "2026-07-16T13:30:00Z"
CONTENT = "Thomas prefers cash-flow first framing in business analyses."

# A granted authorization, as select_consumer would produce once the gate passed.
GRANT = Authorization(
    flags=(APPROVAL_CONSUMPTION,), provider_id="approval_consumption",
    activation_sha256="sha256:test", expires_at="2999-01-01T00:00:00Z",
    evidence_ref=".runtime_governance_state/evidence.md",
)


def _bound():
    task = build_task("이 사업 아이디어를 분석해줘", now=NOW, channel="manual",
                      requester_type="real_thomas", requester_id="Thomas", authenticated=True)
    _, bound = bind_task_to_core(task, now=NOW)
    return bound


def _candidate(bound, content=CONTENT):
    """A full working-memory candidate with provenance from the bound task."""
    ident, ctx = bound["identity"], bound["context"]
    return {
        "candidate_id": integrity.short_id("memcand", {"content": content}),
        "candidate_type": "reusable_knowledge",
        "scope": CANDIDATE_SCOPE,
        "status": CANDIDATE_STATUS,
        "validated": False,
        "promotable": False,
        "content": content,
        "evidence_refs": ["model:analysis"],
        "created_at": NOW,
        "expires_at": timeutil.plus_minutes(NOW, 7 * 24 * 60),
        "origin": {
            "task_id": ident["task_id"], "task_revision": ident["task_revision"],
            "trace_id": ident["trace_id"],
            "core_context_binding_id": ctx["core_context_binding_id"],
            "data_sensitivity": ctx["data_sensitivity"],
        },
    }


def _stores(tmp_path):
    return (
        ApprovalStore(tmp_path / "approvals"),
        WorkingMemoryStore(tmp_path / "working_memory"),
        LedgerStore(tmp_path / "ledger"),
    )


def _approved(tmp_path, *, content=CONTENT, store_candidate=True):
    """Set up an APPROVED approval in a fresh store bound to a stored candidate.

    Returns (approval_store, working_memory_store, ledger, approval_id, candidate)."""
    bound = _bound()
    candidate = _candidate(bound, content)
    astore, wm, ledger = _stores(tmp_path)
    if store_candidate:
        wm.append([candidate])
    permdec = permission.build_memory_promotion_permission_decision(bound, candidate, now=NOW)
    request = approval.build_approval_request(permdec, now=NOW)
    astore.append_permission_decision(permdec)
    astore.append([request])
    verification = approval.Verification(
        approved_by="Thomas", method="telegram_private_control_channel",
        verification_ref="telegram:private_chat:registered-thomas:msg-1")
    decided = approval.record_decision(request, permdec, granted=True,
                                       verification=verification, reason="Approved.", now=NOW)
    astore.append([decided])
    return astore, wm, ledger, request["approval_id"], candidate


# --- happy path -------------------------------------------------------------------


@requires_local_core
def test_consume_spends_the_grant_and_promotes_exactly_the_bound_candidate(tmp_path):
    astore, wm, ledger, approval_id, candidate = _approved(tmp_path)
    result = consume_approval(approval_id, approval_store=astore, working_memory_store=wm,
                              ledger=ledger, now=LATER, consumer=_CapableConsumer(GRANT))

    consumed = result["approval"]
    assert consumed["status"] == "CONSUMED"
    assert consumed["consumption"]["consumption_status"] == "CONSUMED"
    assert consumed["consumption"]["consumed_at"] == LATER
    assert consumed["consumption"]["consumption_ref"] == f"validated_memory:{result['validated']['validated_memory_id']}"
    # The promotion is of exactly the approved candidate, and it landed in the validated store.
    assert result["validated"]["source_candidate_id"] == candidate["candidate_id"]
    assert result["validated"]["content"] == CONTENT.strip()
    assert any(v["source_candidate_id"] == candidate["candidate_id"] for v in wm.read_validated())
    # The stored latest state is CONSUMED.
    assert astore.get(approval_id)["status"] == "CONSUMED"


@requires_local_core
def test_consumed_record_stays_review_only(tmp_path):
    astore, wm, ledger, approval_id, _ = _approved(tmp_path)
    consumed = consume_approval(approval_id, approval_store=astore, working_memory_store=wm,
                                ledger=ledger, now=LATER, consumer=_CapableConsumer(GRANT))["approval"]
    assert consumed["approval_scope"] == "REVIEW_ONLY"
    eff = consumed["runtime_effect"]
    assert eff["mode"] == "REVIEW_ONLY"
    assert all(v is False for k, v in eff.items() if k != "mode")


@requires_local_core
def test_consumption_is_audited_onto_the_ledger_tip(tmp_path):
    astore, wm, ledger, approval_id, _ = _approved(tmp_path)
    result = consume_approval(approval_id, approval_store=astore, working_memory_store=wm,
                              ledger=ledger, now=LATER, consumer=_CapableConsumer(GRANT))
    event = result["audit"][0]
    assert event["event_type"] == "OTHER"
    assert "APPROVAL_CONSUMED" in event["event"]["reason_codes"]
    assert "CONSUMED" in event["event"]["reason_codes"]
    # It was actually appended to the durable ledger.
    assert ledger.last_audit_hash() == event["integrity"]["event_sha256"]


# --- single use -------------------------------------------------------------------


@requires_local_core
def test_a_grant_can_be_consumed_only_once(tmp_path):
    astore, wm, ledger, approval_id, _ = _approved(tmp_path)
    consume_approval(approval_id, approval_store=astore, working_memory_store=wm,
                     ledger=ledger, now=LATER, consumer=_CapableConsumer(GRANT))
    with pytest.raises(ApprovalBlocked) as exc:
        consume_approval(approval_id, approval_store=astore, working_memory_store=wm,
                         ledger=ledger, now=LATER, consumer=_CapableConsumer(GRANT))
    assert exc.value.reason_code == "ALREADY_CONSUMED"


# --- fail-closed refusals ---------------------------------------------------------


@requires_local_core
def test_only_an_approved_grant_can_be_consumed(tmp_path):
    """A PENDING approval (asked but not answered) cannot be consumed."""
    bound = _bound()
    candidate = _candidate(bound)
    astore, wm, ledger = _stores(tmp_path)
    wm.append([candidate])
    permdec = permission.build_memory_promotion_permission_decision(bound, candidate, now=NOW)
    request = approval.build_approval_request(permdec, now=NOW)
    astore.append_permission_decision(permdec)
    astore.append([request])  # PENDING, never decided
    with pytest.raises(ApprovalBlocked) as exc:
        consume_approval(request["approval_id"], approval_store=astore, working_memory_store=wm,
                         ledger=ledger, now=LATER, consumer=_CapableConsumer(GRANT))
    assert exc.value.reason_code == "NOT_APPROVED"


@requires_local_core
def test_an_expired_grant_cannot_be_consumed(tmp_path):
    astore, wm, ledger, approval_id, _ = _approved(tmp_path)
    with pytest.raises(ApprovalBlocked) as exc:
        consume_approval(approval_id, approval_store=astore, working_memory_store=wm,
                         ledger=ledger, now=AFTER_EXPIRY, consumer=_CapableConsumer(GRANT))
    assert exc.value.reason_code == "APPROVAL_EXPIRED"


@requires_local_core
def test_content_changed_since_approval_is_refused(tmp_path):
    """The approval bound a content hash; if the candidate's content changed, consuming it
    would promote something Thomas never saw. Refuse."""
    astore, wm, ledger, approval_id, candidate = _approved(tmp_path)
    # Replace the candidate with same id but different content.
    tampered = dict(candidate, content="A completely different, unapproved finding.")
    wm.append([tampered])
    with pytest.raises(ApprovalBlocked) as exc:
        consume_approval(approval_id, approval_store=astore, working_memory_store=wm,
                         ledger=ledger, now=LATER, consumer=_CapableConsumer(GRANT))
    assert exc.value.reason_code == "CONTENT_CHANGED"


@requires_local_core
def test_an_expired_candidate_cannot_be_promoted(tmp_path):
    """Retention (§12.4) holds on the write path, not only on reads: a candidate whose
    ``expires_at`` has passed is refused even though the prune has not run yet."""
    bound = _bound()
    candidate = _candidate(bound)
    candidate["expires_at"] = "2026-07-16T12:10:00Z"  # before LATER, after approval issue
    astore, wm, ledger = _stores(tmp_path)
    wm.append([candidate])
    permdec = permission.build_memory_promotion_permission_decision(bound, candidate, now=NOW)
    request = approval.build_approval_request(permdec, now=NOW)
    astore.append_permission_decision(permdec)
    astore.append([request])
    verification = approval.Verification(
        approved_by="Thomas", method="telegram_private_control_channel",
        verification_ref="telegram:private_chat:registered-thomas:msg-1")
    decided = approval.record_decision(request, permdec, granted=True,
                                       verification=verification, reason="Approved.", now=NOW)
    astore.append([decided])
    with pytest.raises(ApprovalBlocked) as exc:
        consume_approval(request["approval_id"], approval_store=astore, working_memory_store=wm,
                         ledger=ledger, now=LATER, consumer=_CapableConsumer(GRANT))
    assert exc.value.reason_code == "CANDIDATE_EXPIRED"
    assert wm.read_validated() == []


@requires_local_core
def test_concurrent_consumes_spend_the_grant_exactly_once(tmp_path):
    """Two consumes racing on the same grant (operator loop + docker-exec CLI in the shipped
    deployment): the cross-process lock serializes the compare-and-set, so exactly one wins
    and the loser is refused with ALREADY_CONSUMED — never two promotions from one grant."""
    import threading

    astore, wm, ledger, approval_id, _ = _approved(tmp_path)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def attempt():
        barrier.wait()
        try:
            consume_approval(approval_id, approval_store=astore, working_memory_store=wm,
                             ledger=ledger, now=LATER, consumer=_CapableConsumer(GRANT))
            result = "CONSUMED"
        except ApprovalBlocked as exc:
            result = exc.reason_code
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(outcomes) == ["ALREADY_CONSUMED", "CONSUMED"]
    assert len(wm.read_validated()) == 1  # one promotion, not two
    assert astore.get(approval_id)["status"] == "CONSUMED"


@requires_local_core
def test_a_vanished_candidate_is_refused(tmp_path):
    """The approval is set up but the candidate was never stored (promoted/pruned already)."""
    astore, wm, ledger, approval_id, _ = _approved(tmp_path, store_candidate=False)
    with pytest.raises(ApprovalBlocked) as exc:
        consume_approval(approval_id, approval_store=astore, working_memory_store=wm,
                         ledger=ledger, now=LATER, consumer=_CapableConsumer(GRANT))
    assert exc.value.reason_code == "CANDIDATE_GONE"


def test_unknown_approval_is_refused(tmp_path):
    astore, wm, ledger = _stores(tmp_path)
    with pytest.raises(ApprovalBlocked) as exc:
        consume_approval("approval_does_not_exist", approval_store=astore,
                         working_memory_store=wm, ledger=ledger, now=NOW,
                         consumer=_CapableConsumer(GRANT))
    assert exc.value.reason_code == "UNKNOWN_APPROVAL"


# --- the safety gate --------------------------------------------------------------


@requires_local_core
def test_consume_fails_closed_when_the_flag_is_off(tmp_path, monkeypatch):
    """No opt-in => the inert consumer, which refuses. Nothing is promoted."""
    monkeypatch.delenv("MVP_APPROVAL_CONSUMPTION", raising=False)
    astore, wm, ledger, approval_id, _ = _approved(tmp_path)
    with pytest.raises(ApprovalBlocked) as exc:
        consume_approval(approval_id, approval_store=astore, working_memory_store=wm,
                         ledger=ledger, now=LATER)  # no injected consumer -> real gate
    assert exc.value.reason_code == "CONSUMPTION_DISABLED"
    assert astore.get(approval_id)["status"] == "APPROVED"  # unchanged
    assert wm.read_validated() == []


@requires_local_core
def test_consume_opted_in_without_activation_fails_closed(tmp_path, monkeypatch):
    """Opted in but no activation record => SafetyGateBlocked before any consumer is built."""
    monkeypatch.setenv("MVP_APPROVAL_CONSUMPTION", "on")
    astore, wm, ledger, approval_id, _ = _approved(tmp_path)
    with pytest.raises(SafetyGateBlocked):
        # repo_root points at an empty tmp with no activation record.
        consume_approval(approval_id, approval_store=astore, working_memory_store=wm,
                         ledger=ledger, now=LATER, repo_root=tmp_path)


# --- the kill switch -------------------------------------------------------------


@requires_local_core
@pytest.mark.parametrize("mode", ["PAUSED", "KILLED"])
def test_consume_is_refused_while_the_runtime_is_not_active(tmp_path, mode):
    """Spending a grant mutates VALIDATED memory, so the emergency stop must block it
    (`kill_blocks: new_execution` / `pending_execution`) — as it does for the R8 write and
    the R6 scheduler."""
    astore, wm, ledger, approval_id, _ = _approved(tmp_path)
    control = ControlStore(tmp_path / "control")
    control.save(ControlState(mode=mode, updated_by="Thomas", updated_at=NOW, reason="test"))
    with pytest.raises(ApprovalBlocked) as exc:
        consume_approval(approval_id, approval_store=astore, working_memory_store=wm,
                         ledger=ledger, now=LATER, consumer=_CapableConsumer(GRANT),
                         control_store=control)
    assert exc.value.reason_code == "KILL_SWITCH_ACTIVE"
    # Nothing was spent or promoted.
    assert astore.get(approval_id)["status"] == "APPROVED"
    assert wm.read_validated() == []


@requires_local_core
def test_the_grant_is_spent_before_the_promotion_is_written(tmp_path):
    """One-time-use ordering: if the promotion write fails, the grant must already be spent —
    otherwise a retry would pass every guard and promote a second time."""
    astore, wm, ledger, approval_id, _ = _approved(tmp_path)

    class _FailingValidatedStore:
        """Wraps the real store but fails the validated write, as a disk error would."""
        def __init__(self, inner): self._inner = inner
        def read_all(self): return self._inner.read_all()
        def read_validated(self): return self._inner.read_validated()
        def append_validated(self, entries): raise PersistenceError("WRITE_FAILED", "disk full")

    with pytest.raises(ApprovalBlocked) as exc:
        consume_approval(approval_id, approval_store=astore,
                         working_memory_store=_FailingValidatedStore(wm),
                         ledger=ledger, now=LATER, consumer=_CapableConsumer(GRANT))
    # The failure names the TRUE state — a durably spent grant — not a generic block that
    # reads like nothing happened.
    assert exc.value.reason_code == "CONSUMED_NOT_PROMOTED"
    # The grant is spent (safe direction) and nothing was promoted, so it cannot be spent twice.
    assert astore.get(approval_id)["status"] == "CONSUMED"
    assert wm.read_validated() == []
    with pytest.raises(ApprovalBlocked) as exc:
        consume_approval(approval_id, approval_store=astore, working_memory_store=wm,
                         ledger=ledger, now=LATER, consumer=_CapableConsumer(GRANT))
    assert exc.value.reason_code == "ALREADY_CONSUMED"


def test_dry_run_consumer_refuses():
    with pytest.raises(ApprovalBlocked) as exc:
        _DryRunConsumer().consume({"content": "x"})
    assert exc.value.reason_code == "CONSUMPTION_DISABLED"


def test_capable_consumer_reverifies_authorization():
    """The capable consumer re-checks its authorization at the moment of acting (defense in
    depth): an expired grant is refused even though the consumer exists."""
    stale = Authorization(
        flags=(APPROVAL_CONSUMPTION,), provider_id="approval_consumption",
        activation_sha256="sha256:test", expires_at="2000-01-01T00:00:00Z",
        evidence_ref=".runtime_governance_state/evidence.md")
    with pytest.raises(SafetyGateBlocked):
        _CapableConsumer(stale).consume({"content": "x"}, promoted_by="Thomas",
                                        reason="r", now=NOW)


# --- the record builder -----------------------------------------------------------


@requires_local_core
def test_build_consumed_record_refuses_a_non_approved_input(tmp_path):
    bound = _bound()
    candidate = _candidate(bound)
    permdec = permission.build_memory_promotion_permission_decision(bound, candidate, now=NOW)
    request = approval.build_approval_request(permdec, now=NOW)  # PENDING
    with pytest.raises(ApprovalBlocked) as exc:
        approval.build_consumed_record(request, permdec, consumed_at=LATER,
                                       consumption_ref="validated_memory:x")
    assert exc.value.reason_code == "NOT_APPROVED"
