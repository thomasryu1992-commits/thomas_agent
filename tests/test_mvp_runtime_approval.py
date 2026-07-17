"""R9 approval-flow tests.

An approval is only worth the certainty that Thomas gave it, so most of these assert what
must be *refused*: a decision from anyone else, from any channel that cannot prove identity,
after expiry, or a second time. The tests that matter most are the ones proving an APPROVED
approval still authorizes nothing — that boundary is the point of the whole increment.

The happy paths need a bound task (local Core activation), so they skip on a core-neutral
CI checkout, like every other binding-dependent suite here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.mvp_runtime import approval, permission
from runtime.mvp_runtime.approval_store import ApprovalStore
from runtime.mvp_runtime.binding import DEFAULT_POINTER_REL, bind_task_to_core
from runtime.mvp_runtime.errors import ApprovalBlocked, PlannerBlocked
from runtime.mvp_runtime.intake import build_task

LOCAL_POINTER = Path(__file__).resolve().parents[1] / DEFAULT_POINTER_REL
requires_local_core = pytest.mark.skipif(not LOCAL_POINTER.is_file(), reason="no local Core activation")

NOW = "2026-07-16T12:00:00Z"
LATER = "2026-07-16T12:05:00Z"
CANDIDATE = {
    "candidate_id": "memcand_test0001",
    "candidate_type": "operating_preference",
    "content": "Thomas prefers cash-flow first framing in business analyses.",
}
VERIFIED = approval.Verification(
    approved_by="Thomas",
    method="telegram_private_control_channel",
    verification_ref="telegram:private_chat:registered-thomas:msg-1",
)


def _bound():
    task = build_task("이 사업 아이디어를 분석해줘", now=NOW)
    _, bound = bind_task_to_core(task, now=NOW)
    return bound


def _permdec(candidate=CANDIDATE):
    return permission.build_memory_promotion_permission_decision(_bound(), candidate, now=NOW)


def _request(**kwargs):
    return approval.build_approval_request(_permdec(), now=NOW, **kwargs)


# --- the request ------------------------------------------------------------------


@requires_local_core
def test_request_is_pending_review_only_and_unconsumed():
    req = _request()
    assert req["status"] == "PENDING"
    assert req["approval_scope"] == "REVIEW_ONLY"
    assert req["consumption"] == {
        "one_time_use": True, "consumption_status": "NOT_CONSUMED",
        "previewed_at": None, "preview_ref": None,
    }
    assert req["approver"]["verification_status"] == "NOT_VERIFIED"
    assert req["approver"]["required_approver"] == "Thomas"
    eff = req["runtime_effect"]
    assert eff["mode"] == "REVIEW_ONLY"
    assert all(v is False for k, v in eff.items() if k != "mode")


@requires_local_core
def test_request_binds_to_the_exact_action():
    """What Thomas sees must be what he decides: the approval snapshots the action and its
    fingerprint, so nothing can be substituted afterwards."""
    permdec = _permdec()
    req = approval.build_approval_request(permdec, now=NOW)
    assert req["action_fingerprint"] == permdec["action_fingerprint"]
    assert req["approved_action_snapshot"] == permdec["fingerprint_payload"]
    assert req["permission_decision_id"] == permdec["permission_decision_id"]
    assert req["task_id"] == permdec["task_id"]
    assert req["core_context_binding_id"] == permdec["core_context_binding_id"]


@requires_local_core
def test_request_for_a_different_candidate_is_a_different_action():
    """The approval_id is derived from the action fingerprint, so a materially different
    action can never collide onto the same approval — `/approve <id>` always names exactly
    one action (`action_identity.invalidated_by_any_material_field_change`)."""
    a = approval.build_approval_request(_permdec(), now=NOW)
    other = dict(CANDIDATE, content="Something else entirely.")
    b = approval.build_approval_request(_permdec(other), now=NOW)
    assert a["action_fingerprint"] != b["action_fingerprint"]
    assert a["approval_id"] != b["approval_id"]


@requires_local_core
def test_the_same_action_is_the_same_approval():
    """Determinism: re-asking about the identical action does not mint a second id."""
    a = approval.build_approval_request(_permdec(), now=NOW)
    b = approval.build_approval_request(_permdec(), now=NOW)
    assert a["approval_id"] == b["approval_id"]
    assert a["action_fingerprint"] == b["action_fingerprint"]


@requires_local_core
def test_an_allow_action_cannot_be_turned_into_an_approval_request():
    allow = permission.build_search_permission_decision(_bound(), role_permission_ceiling="P3", now=NOW)
    with pytest.raises(ApprovalBlocked) as exc:
        approval.build_approval_request(allow, now=NOW)
    assert exc.value.reason_code == "NOT_APPROVAL_REQUIRED"


@requires_local_core
def test_ttl_cannot_exceed_the_policy_maximum_for_the_scope():
    with pytest.raises(ApprovalBlocked) as exc:
        _request(ttl_minutes=999)
    assert exc.value.reason_code == "TTL_EXCEEDS_POLICY"


@requires_local_core
def test_approval_never_outlives_the_decision_it_binds_to():
    permdec = _permdec()
    req = approval.build_approval_request(permdec, now=NOW)
    assert req["validity"]["expires_at"] <= permdec["lifecycle"]["expires_at"]


# --- the decision -----------------------------------------------------------------


@requires_local_core
def test_verified_thomas_can_approve():
    req = _request()
    ok = approval.record_decision(req, _permdec(), granted=True, verification=VERIFIED,
                                  reason="Reusable preference.", now=LATER)
    assert ok["status"] == "APPROVED"
    assert ok["approver"]["approved_by"] == "Thomas"
    assert ok["approver"]["verification_status"] == "VERIFIED"
    assert ok["decision"]["decided_at"] == LATER


@requires_local_core
def test_verified_thomas_can_reject():
    req = _request()
    no = approval.record_decision(req, _permdec(), granted=False, verification=VERIFIED,
                                  reason="Not durable enough.", now=LATER)
    assert no["status"] == "REJECTED"


@requires_local_core
def test_deciding_does_not_mutate_the_request():
    """Approvals are append-only evidence: a decision produces a new record."""
    req = _request()
    approval.record_decision(req, _permdec(), granted=True, verification=VERIFIED,
                             reason="ok", now=LATER)
    assert req["status"] == "PENDING"
    assert req["approver"]["verification_status"] == "NOT_VERIFIED"


@requires_local_core
@pytest.mark.parametrize(
    "verification,reason_code",
    [
        (approval.Verification("Mallory", "telegram_private_control_channel", "telegram:x"), "WRONG_APPROVER"),
        (approval.Verification("Thomas", "telegram_group", "telegram:group:1"), "UNVERIFIED_SOURCE"),
        (approval.Verification("Thomas", "email", "mailto:thomas"), "UNVERIFIED_SOURCE"),
        (approval.Verification("Thomas", "telegram_private_control_channel", "  "), "NO_VERIFICATION_REF"),
    ],
)
def test_an_unverifiable_decision_is_refused(verification, reason_code):
    with pytest.raises(ApprovalBlocked) as exc:
        approval.record_decision(_request(), _permdec(), granted=True, verification=verification,
                                 reason="ok", now=LATER)
    assert exc.value.reason_code == reason_code


@requires_local_core
def test_a_decision_needs_a_reason():
    with pytest.raises(ApprovalBlocked) as exc:
        approval.record_decision(_request(), _permdec(), granted=True, verification=VERIFIED,
                                 reason="   ", now=LATER)
    assert exc.value.reason_code == "NO_DECISION_REASON"


@requires_local_core
def test_an_expired_approval_cannot_be_decided():
    with pytest.raises(ApprovalBlocked) as exc:
        approval.record_decision(_request(), _permdec(), granted=True, verification=VERIFIED,
                                 reason="late", now="2026-07-16T23:59:00Z")
    assert exc.value.reason_code == "APPROVAL_EXPIRED"


@requires_local_core
def test_an_approval_is_single_use():
    """Deciding twice is the reuse the Governance Policy blocks (approval_reuse_allowed:
    false; BLOCK: APPROVAL_REUSE)."""
    req = _request()
    decided = approval.record_decision(req, _permdec(), granted=True, verification=VERIFIED,
                                       reason="ok", now=LATER)
    with pytest.raises(ApprovalBlocked) as exc:
        approval.record_decision(decided, _permdec(), granted=True, verification=VERIFIED,
                                 reason="again", now=LATER)
    assert exc.value.reason_code == "NOT_PENDING"


@requires_local_core
def test_a_rejected_approval_cannot_be_flipped_to_approved():
    rejected = approval.record_decision(_request(), _permdec(), granted=False, verification=VERIFIED,
                                        reason="no", now=LATER)
    with pytest.raises(ApprovalBlocked) as exc:
        approval.record_decision(rejected, _permdec(), granted=True, verification=VERIFIED,
                                 reason="changed my mind", now=LATER)
    assert exc.value.reason_code == "NOT_PENDING"


# --- the boundary: approval authorizes nothing -------------------------------------


@requires_local_core
def test_an_approved_approval_authorizes_no_execution():
    """The point of the whole increment. APPROVED records Thomas's answer; it is not an
    execution token, and the runtime has no path to spend it."""
    ok = approval.record_decision(_request(), _permdec(), granted=True, verification=VERIFIED,
                                  reason="ok", now=LATER)
    assert ok["approval_scope"] == "REVIEW_ONLY"
    assert ok["runtime_effect"]["mode"] == "REVIEW_ONLY"
    assert ok["runtime_effect"]["executor_handoff_allowed"] is False
    # There is no CONSUMED state to reach: consumption is gate-pinned unimplemented.
    assert ok["consumption"]["consumption_status"] == "NOT_CONSUMED"
    assert ok["consumption"]["one_time_use"] is True
    # And the promotion action itself remains something the runtime cannot perform.
    assert "APPROVAL_REQUIRED" not in permission._EXECUTABLE_DISPOSITIONS


# --- commands ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("/approve approval_abc", ("approve", "approval_abc")),
        ("/reject approval_abc", ("reject", "approval_abc")),
        ("approve approval_abc", ("approve", "approval_abc")),
        ("  /APPROVE  approval_abc  ", ("approve", "approval_abc")),
        ("/approve", ("approve", None)),
    ],
)
def test_parse_approval_command(text, expected):
    assert approval.parse_approval_command(text) == expected


@pytest.mark.parametrize("text", ["/status", "/pause", "hello", "", None, "/approved x"])
def test_parse_ignores_non_approval_text(text):
    assert approval.parse_approval_command(text) is None


@requires_local_core
def test_apply_command_requires_the_approval_id(tmp_path):
    """A bare /approve is an ambiguous expression — the policy requires the answer to name
    the approval."""
    store = ApprovalStore(tmp_path)
    with pytest.raises(ApprovalBlocked) as exc:
        approval.apply_command(store, "approve", None, verification=VERIFIED, now=LATER)
    assert exc.value.reason_code == "NO_APPROVAL_ID"


@requires_local_core
def test_apply_command_refuses_an_unknown_approval(tmp_path):
    store = ApprovalStore(tmp_path)
    with pytest.raises(ApprovalBlocked) as exc:
        approval.apply_command(store, "approve", "approval_nope", verification=VERIFIED, now=LATER)
    assert exc.value.reason_code == "UNKNOWN_APPROVAL"


@requires_local_core
def test_apply_command_refuses_when_the_bound_decision_is_missing(tmp_path):
    """An answer that cannot be tied back to the exact action it authorizes is not
    evidence of anything."""
    store = ApprovalStore(tmp_path)
    req = _request()
    store.append([req])  # deliberately no permission decision stored
    with pytest.raises(ApprovalBlocked) as exc:
        approval.apply_command(store, "approve", req["approval_id"], verification=VERIFIED, now=LATER)
    assert exc.value.reason_code == "PERMISSION_DECISION_MISSING"


@requires_local_core
def test_apply_command_records_and_stores_the_decision(tmp_path):
    store = ApprovalStore(tmp_path)
    permdec = _permdec()
    req = approval.build_approval_request(permdec, now=NOW)
    store.append([req])
    store.append_permission_decision(permdec)

    outcome = approval.apply_command(store, "approve", req["approval_id"],
                                     verification=VERIFIED, now=LATER)
    assert outcome["action"] == "APPROVED"
    assert store.get(req["approval_id"])["status"] == "APPROVED"
    # Append-only: the PENDING request survives alongside the decision.
    assert [r["status"] for r in store.read_all()] == ["PENDING", "APPROVED"]


# --- the store --------------------------------------------------------------------


@requires_local_core
def test_store_current_is_latest_wins_and_pending_excludes_decided(tmp_path):
    store = ApprovalStore(tmp_path)
    permdec = _permdec()
    req = approval.build_approval_request(permdec, now=NOW)
    store.append([req])
    assert [a["approval_id"] for a in store.pending()] == [req["approval_id"]]

    decided = approval.record_decision(req, permdec, granted=True, verification=VERIFIED,
                                       reason="ok", now=LATER)
    store.append([decided])
    assert store.get(req["approval_id"])["status"] == "APPROVED"
    assert store.pending() == []


def test_store_is_empty_before_anything_is_asked(tmp_path):
    store = ApprovalStore(tmp_path)
    assert store.read_all() == []
    assert store.pending() == []
    assert store.get("approval_nope") is None
