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

from tests._helpers import requires_local_core

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
        "consumed_at": None, "consumption_ref": None,
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
def test_an_approved_approval_authorizes_no_execution_on_its_own():
    """APPROVED records Thomas's answer; it is not itself an execution token. Spending it is a
    separate, safety-flag-gated step (R10 consumption) — the decision never executes as a side
    effect, and the APPROVED record is still unconsumed and REVIEW_ONLY."""
    ok = approval.record_decision(_request(), _permdec(), granted=True, verification=VERIFIED,
                                  reason="ok", now=LATER)
    assert ok["approval_scope"] == "REVIEW_ONLY"
    assert ok["runtime_effect"]["mode"] == "REVIEW_ONLY"
    assert ok["runtime_effect"]["executor_handoff_allowed"] is False
    # An APPROVED record is not yet consumed — consumption is a deliberate later step.
    assert ok["consumption"]["consumption_status"] == "NOT_CONSUMED"
    assert ok["consumption"]["one_time_use"] is True
    # And building/deciding an APPROVAL_REQUIRED action never executes it.
    assert "APPROVAL_REQUIRED" not in permission._EXECUTABLE_DISPOSITIONS


# --- commands ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("/approve approval_abc", ("approve", "approval_abc", None)),
        ("/reject approval_abc", ("reject", "approval_abc", None)),
        ("approve approval_abc", ("approve", "approval_abc", None)),
        ("  /APPROVE  approval_abc  ", ("approve", "approval_abc", None)),
        ("/approve", ("approve", None, None)),
        # Telegram appends the bot username to menu-picked commands.
        ("/approve@thomas_agent_bot approval_abc", ("approve", "approval_abc", None)),
        # Free text after the id is Thomas's own decision reason, verbatim.
        ("/reject approval_abc 근거 문서가 부족함", ("reject", "approval_abc", "근거 문서가 부족함")),
        ("/approve approval_abc  looks safe, low blast radius  ",
         ("approve", "approval_abc", "looks safe, low blast radius")),
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
def test_answering_a_timed_out_ask_retires_it(tmp_path):
    """The lifecycle has always claimed --(ttl)--> EXPIRED and nothing ever performed it,
    so `pending()` listed dead asks forever. Answering one is the unambiguous moment to
    retire it: an explicit operator action on that exact approval, never a write hidden
    inside a read. The refusal itself is unchanged — an expired approval is not decidable."""
    store = ApprovalStore(tmp_path)
    permdec = _permdec()
    req = approval.build_approval_request(permdec, now=NOW)
    store.append([req])
    store.append_permission_decision(permdec)
    assert [a["approval_id"] for a in store.pending()] == [req["approval_id"]]

    with pytest.raises(ApprovalBlocked) as exc:
        approval.apply_command(store, "approve", req["approval_id"],
                               verification=VERIFIED, now="2026-07-16T23:59:00Z")
    assert exc.value.reason_code == "APPROVAL_EXPIRED"

    assert store.get(req["approval_id"])["status"] == approval.STATUS_EXPIRED
    assert store.pending() == []            # the dead ask stops being listed
    # EXPIRED is a retirement, not a verdict: nobody approved or rejected it.
    assert store.get(req["approval_id"])["approver"]["approved_by"] is None


@requires_local_core
def test_a_retired_approval_cannot_then_be_decided(tmp_path):
    """Retiring must not become a second bite: EXPIRED is not PENDING."""
    store = ApprovalStore(tmp_path)
    permdec = _permdec()
    req = approval.build_approval_request(permdec, now=NOW)
    store.append([req])
    store.append_permission_decision(permdec)
    late = "2026-07-16T23:59:00Z"
    for expected in ("APPROVAL_EXPIRED", "NOT_PENDING"):
        with pytest.raises(ApprovalBlocked) as exc:
            approval.apply_command(store, "approve", req["approval_id"],
                                   verification=VERIFIED, now=late)
        assert exc.value.reason_code == expected


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
    # No explicit reason → the boilerplate default, and no reason echo in the reply.
    assert store.get(req["approval_id"])["decision"]["decision_reason"] == (
        "Approved by Thomas on the verified control channel."
    )
    assert "Reason recorded" not in outcome["reply"]


@requires_local_core
def test_an_explicit_reason_is_recorded_verbatim_and_echoed(tmp_path):
    """Thomas's own words are the material for later preference inference — they must land
    in the durable record exactly as given, and the reply must confirm they did."""
    store = ApprovalStore(tmp_path)
    permdec = _permdec()
    req = approval.build_approval_request(permdec, now=NOW)
    store.append([req])
    store.append_permission_decision(permdec)

    outcome = approval.apply_command(store, "reject", req["approval_id"],
                                     verification=VERIFIED, now=LATER,
                                     reason="근거 문서가 부족함")
    assert outcome["action"] == "REJECTED"
    decided = store.get(req["approval_id"])
    assert decided["decision"]["decision_reason"] == "근거 문서가 부족함"
    assert "Reason recorded: 근거 문서가 부족함" in outcome["reply"]


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
