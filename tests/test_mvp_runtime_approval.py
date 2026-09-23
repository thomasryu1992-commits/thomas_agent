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
from unittest import mock

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


# --- stage 2: decision history on a new ask ----------------------------------------


def _decided_store(tmp_path, decisions):
    """A store holding one decided approval per (verb, reason) pair, distinct candidates."""
    store = ApprovalStore(tmp_path)
    for index, (verb, reason) in enumerate(decisions):
        candidate = dict(CANDIDATE, candidate_id=f"memcand_hist{index:04d}",
                         content=f"Preference-history fixture fact {index}.")
        permdec = _permdec(candidate)
        req = approval.build_approval_request(permdec, now=NOW)
        store.append([req])
        store.append_permission_decision(permdec)
        approval.apply_command(store, verb, req["approval_id"],
                               verification=VERIFIED, now=LATER, reason=reason)
    return store


@requires_local_core
def test_decision_history_summarizes_past_same_action_decisions(tmp_path):
    store = _decided_store(tmp_path, [
        ("approve", None),
        ("reject", "근거 문서가 부족함"),
        ("reject", None),
    ])
    new_request = _request()
    store.append([new_request])

    history = approval.decision_history(store, new_request)
    assert history["approved"] == 1 and history["rejected"] == 2
    assert len(history["recent"]) == 3
    # The articulated reason survives verbatim; bare verdicts are flagged, so a
    # boilerplate line can never be mistaken for a stated preference.
    articulated = [r for r in history["recent"] if not r["boilerplate"]]
    assert [r["reason"] for r in articulated] == ["근거 문서가 부족함"]
    # The new (PENDING) request itself is not part of its own history.
    assert history["approved"] + history["rejected"] == 3


@requires_local_core
def test_decision_history_counts_consumed_as_approved_and_skips_undecided(tmp_path):
    store = _decided_store(tmp_path, [("approve", "쓸모 있는 지식")])
    # Latest-wins: the approved grant was later spent — still an approval Thomas gave.
    approved = [a for a in store.current().values() if a["status"] == "APPROVED"][0]
    store.append([dict(approved, status="CONSUMED")])
    # A PENDING ask and an EXPIRED one carry no decision — excluded.
    pending_permdec = _permdec(dict(CANDIDATE, content="undecided one"))
    store.append([approval.build_approval_request(pending_permdec, now=NOW)])

    new_request = _request()
    history = approval.decision_history(store, new_request)
    assert history["approved"] == 1 and history["rejected"] == 0
    assert history["recent"][0]["status"] == "APPROVED"


@requires_local_core
def test_decision_history_respects_the_limit(tmp_path):
    store = _decided_store(tmp_path, [("reject", f"이유 {i}") for i in range(5)])
    history = approval.decision_history(store, _request(), limit=2)
    assert history["rejected"] == 5            # counts cover everything
    assert len(history["recent"]) == 2         # detail is capped


def test_format_decision_history_renders_counts_reasons_and_advisory():
    text = approval.format_decision_history({
        "action_type": "memory.validated.promote",
        "approved": 1, "rejected": 2,
        "recent": [
            {"status": "REJECTED", "decided_at": "2026-07-21T03:13:20Z",
             "reason": "근거 문서가 부족함", "boilerplate": False},
            {"status": "APPROVED", "decided_at": "2026-07-18T10:00:00Z",
             "reason": approval.DEFAULT_APPROVE_REASON, "boilerplate": True},
        ],
    })
    assert "승인 1 / 거절 2" in text
    assert "[REJECTED 2026-07-21] 근거 문서가 부족함" in text
    assert "[APPROVED 2026-07-18] (이유 미기재)" in text
    # Advisory by design: the history informs the ask, it never answers it.
    assert "결정은 이 요청 자체로 판단해 주세요" in text


def test_format_decision_history_is_empty_without_decisions():
    assert approval.format_decision_history(
        {"action_type": "x", "approved": 0, "rejected": 0, "recent": []}) == ""


@requires_local_core
def test_request_message_appends_history_only_when_given(tmp_path):
    permdec = _permdec()
    req = approval.build_approval_request(permdec, now=NOW)
    bare = approval.request_message(req, permdec)
    assert "과거 유사 결정" not in bare

    store = _decided_store(tmp_path, [("reject", "테스트 사유")])
    history = approval.decision_history(store, req)
    with_history = approval.request_message(req, permdec, history=history)
    assert with_history.startswith(bare)
    assert "과거 유사 결정" in with_history and "테스트 사유" in with_history


# --- the store --------------------------------------------------------------------


def test_store_appends_serialize_under_the_sidecar_lock(tmp_path):
    """The approval store is written concurrently by the operator loop (/approve) and
    docker-exec CLIs (approval_cli request), and a row embeds its whole action snapshot —
    large enough to flush in several syscalls. Appends must hold the per-file sidecar
    lock like every other shared store; this store was the one left unlocked."""
    import threading

    from runtime.mvp_runtime.filelock import locked

    store = ApprovalStore(tmp_path)
    store.append([{"approval_id": "approval_lock_a", "status": "PENDING"}])
    assert (tmp_path / "approvals.jsonl.lock").is_file()
    store.append_permission_decision({"permission_decision_id": "permdec_lock_a"})
    assert (tmp_path / "permission_decisions.jsonl.lock").is_file()

    order: list[str] = []
    entered = threading.Event()
    release = threading.Event()

    def holder():
        with locked(tmp_path / "approvals.jsonl.lock", code="LOCK_TEST", label="test"):
            order.append("holder_in")
            entered.set()
            release.wait(timeout=10)
            order.append("holder_out")

    def writer():
        entered.wait(timeout=10)
        store.append([{"approval_id": "approval_lock_b", "status": "PENDING"}])
        order.append("append_done")

    threads = [threading.Thread(target=holder), threading.Thread(target=writer)]
    for t in threads:
        t.start()
    entered.wait(timeout=10)
    threading.Timer(0.3, release.set).start()
    for t in threads:
        t.join(timeout=30)
    # The append could not interleave with the held lock — it waited for the release.
    assert order == ["holder_in", "holder_out", "append_done"]
    assert store.get("approval_lock_b") is not None


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


# --- the switch door's asks -------------------------------------------------------
#
# Until 2026-08-22 `format_request` had two branches — trial, and everything else — and the
# switch door's asks fell into "everything else". They therefore told Thomas that approving a
# runtime switch could not be undone because "validated memory는 지속됩니다", quoted a cost of
# none, and closed with a paragraph about spending the grant through `approval_consumption`.
# None of that describes a switch. The door's whole point is that Thomas sees what he is
# deciding, so a renderer that describes a different decision defeats it.


def _switch_permdec(*, arms: bool):
    """A real switch-door decision, minted by the module that owns the target prefix."""
    builder = (permission.build_trading_switch_permission_decision if arms
               else permission.build_nonfinancial_resume_permission_decision)
    return builder(
        _bound(), "crypto",
        stop_ref="stop_abc123",
        stop_summary="the KILLED placed by local_console at 2026-07-18T10:44:41Z",
        now=NOW,
    )


@requires_local_core
def test_a_switch_grant_is_not_pointed_at_the_consume_cli():
    """A switch grant is spent by calling the door again with the id. It is not a promotion,
    it does not go through `approval_cli consume`, and it is not gated on the
    `approval_consumption` safety flag — so an ask that says otherwise sends Thomas to a
    command that will not work on it."""
    permdec = _switch_permdec(arms=True)
    text = approval.request_message(approval.build_approval_request(permdec, now=NOW), permdec)
    # The claim, not the token: the ask may name `approval_consumption` — it does, to say the
    # flag is irrelevant here — but it must not send Thomas to that step.
    assert "`approval_cli consume`으로 쓰지 않습니다" in text
    assert "approval_consumption 세이프티 플래그와" in text and "무관합니다" in text
    assert "승격을 1회 수행합니다" not in text
    assert "플래그가 켜진 기기에서만" not in text
    assert "스위치 문을 다시 호출할 때 1회만 소비됩니다" in text


@requires_local_core
def test_a_switch_grant_says_stopping_needs_no_approval():
    """The reversibility line was exactly backwards. Stopping is the cheap verb — it applies at
    once with no approval — and that asymmetry is the reason this door exists at all."""
    permdec = _switch_permdec(arms=True)
    text = approval.request_message(approval.build_approval_request(permdec, now=NOW), permdec)
    assert "validated memory는 지속됩니다" not in text
    assert "되돌릴 수 있는가: 예 — 끄기(stop/pause)는 승인 없이 즉시 적용됩니다" in text


@requires_local_core
def test_the_two_switch_grants_differ_on_the_one_thing_that_differs():
    """`trading_switch:` re-arms live entries and `runtime_resume:` structurally cannot. That is
    the entire difference in effect between the two, so it is the line Thomas must be able to
    read — and the cost line follows it."""
    arming = approval.request_message(
        approval.build_approval_request(_switch_permdec(arms=True), now=NOW),
        _switch_permdec(arms=True),
    )
    runtime_only = approval.request_message(
        approval.build_approval_request(_switch_permdec(arms=False), now=NOW),
        _switch_permdec(arms=False),
    )
    assert "승인하면 라이브 진입이 다시 무장됩니다." in arming
    assert "예상 비용: 실주문이 다시 나갈 수 있게 되므로" in arming

    assert "라이브 진입을 무장하지 않습니다" in runtime_only
    assert "다시 무장됩니다" not in runtime_only
    assert "예상 비용: 없음" in runtime_only


@requires_local_core
def test_a_switch_grant_warns_that_a_changed_stop_voids_it():
    """`stop_ref` is derived from the whole control state, so anything that rewrites
    `updated_at` between minting and spending refuses the grant with STOP_CHANGED. That costs a
    re-ask, and Thomas should not learn it from a refusal."""
    permdec = _switch_permdec(arms=False)
    text = approval.request_message(approval.build_approval_request(permdec, now=NOW), permdec)
    assert "STOP_CHANGED" in text


@requires_local_core
def test_a_memory_promotion_ask_is_unchanged():
    """The control. The two lines above are keyed on the target prefix, so the ask this
    renderer was written for must read exactly as it did."""
    permdec = _permdec()
    text = approval.request_message(approval.build_approval_request(permdec, now=NOW), permdec)
    assert "되돌릴 수 있는가: 아니오 — validated memory는 지속됩니다" in text
    assert "예상 비용: 없음" in text
    assert "approval_consumption" in text
    assert "스위치 문" not in text


# --- what undoes each ask ---------------------------------------------------------
#
# The switch door's fix above was one kind at a time. Until 2026-09-19 the reversibility line still
# fell through to the memory promotion's answer, so a paper-tier pool promotion, a retirement, a
# P&L correction, a probe batch and a program registration all rendered as permanent because
# "validated memory는 지속됩니다" — and a retirement, which shares its target with a paper
# promotion, rendered the promotion door's install step. The promotion, probe and registration asks
# are printed for Thomas by their scripts; no door renders the other two yet, so theirs was latent.

_MEMORY_UNDO = "되돌릴 수 있는가: 아니오 — validated memory는 지속됩니다"
_MEMORY_CONSUME_STEP = "이 승인 하나에 묶인 승격을 1회 수행합니다."
_PAPER_POOL_UNDO = ("되돌릴 수 있는가: 예 — 이후의 승격(교체)이나 은퇴(scripts/retire_strategies.py)로 "
                    "되돌리며, 어느 쪽이든 새 승인 요청을 거칩니다")
_PROMOTION = dict(candidate_ids=["cand_a"], strategy_ids=["S1"], rule_hashes=["sha256:r"],
                  artifact_sha256s=["sha256:a"], keep_active=False, content_sha256="sha256:" + "c" * 64)


def _minted_ask(builder, *args, **kwargs):
    """The ask a real builder mints, without a bound task: the decision builder is stubbed and the
    snapshot carries the fields `build_approval_request` copies from its fingerprint payload."""
    captured = {}

    def fake_build(bound, **kw):
        captured.update(kw)
        return {}

    with mock.patch.object(permission, "build_permission_decision", fake_build):
        builder({}, *args, now=NOW, **kwargs)
    action = captured["action"]
    return {
        "approval_id": "approval_x", "task_id": "task_x",
        "validity": {"expires_at": LATER}, "action_fingerprint": "sha256:f",
        "approved_action_snapshot": {
            "action_type": action.action_type,
            "permission_scope": captured["permission_scope"],
            "target_ref": action.target_ref,
            "content_sha256": action.content_sha256,
        },
    }


_ASKS = {
    "memory": lambda: _minted_ask(permission.build_memory_promotion_permission_decision, CANDIDATE),
    "trial": lambda: _minted_ask(
        permission.build_trial_permission_decision,
        {"role_id": "research.candidate", "version": "0.1.0", "definition_sha256": "sha256:d"},
        trial_request="t",
    ),
    "trading_switch": lambda: _minted_ask(
        permission.build_trading_switch_permission_decision, "crypto", stop_ref="stop_1", stop_summary="s"),
    "runtime_resume": lambda: _minted_ask(
        permission.build_nonfinancial_resume_permission_decision, "crypto", stop_ref="stop_1", stop_summary="s"),
    "workflow_step": lambda: _minted_ask(
        permission.build_workflow_step_permission_decision, workflow_id="wf_1", step_key="s1", plan_version=1,
        capability="analysis", request_sha256="sha256:q", request_preview="p",
    ),
    "execution_stage": lambda: _minted_ask(
        permission.build_execution_stage_permission_decision,
        content={"venue": "binance", "transition": "CLIMB", "from_stage": "PAPER", "to_stage": "SIGNED_TESTNET",
                 "stage_ref": "sha256:s", "policy_version": "1.5.1", "policy_safety_sha256": "sha256:p",
                 "registered_by": "Thomas", "reason": "r", "evidence": {}},
    ),
    "pool_live": lambda: _minted_ask(
        permission.build_strategy_promotion_permission_decision, live_tier="LIVE", **_PROMOTION),
    "pool_paper": lambda: _minted_ask(
        permission.build_strategy_promotion_permission_decision, live_tier="OBSERVATION", **_PROMOTION),
    "retirement": lambda: _minted_ask(
        permission.build_strategy_retirement_permission_decision, strategy_ids=["S1"], candidate_ids=["cand_a"],
        rule_hashes=["sha256:r"], reason="r", content_sha256="sha256:c",
    ),
    "correction": lambda: _minted_ask(
        permission.build_live_outcome_correction_permission_decision, corrects_outcome_id="live_out_1",
        corrects_record_sha256="sha256:o", disposition="VOID", reason="r", content_sha256="sha256:c",
    ),
    "probe": lambda: _minted_ask(
        permission.build_slippage_probe_permission_decision, batch_id="b1",
        params={"symbols": ["BTCUSDT"], "n": 3}, content_sha256="sha256:c",
    ),
    "registration": lambda: _minted_ask(
        permission.build_program_registration_permission_decision, program_id="p.x", program_version="0.1.0",
        definition_sha256="sha256:d", candidate_id="progcand_1", program_request_id="progreq_1",
    ),
    # The operator's emergency close (crypto PR6c): the one irreversible money-path ask, so its
    # answer leads with no. The real builder's ask is rendered in test_mvp_runtime_crypto_emergency_close.
    "emergency_close": lambda: {
        "approval_id": "approval_x", "task_id": "task_x",
        "validity": {"expires_at": LATER}, "action_fingerprint": "sha256:f",
        "approved_action_snapshot": {"action_type": "crypto.live.emergency_close",
                                     "permission_scope": "RUNTIME_GOVERNANCE",
                                     "target_ref": permission.EMERGENCY_CLOSE_TARGET_PREFIX + "x",
                                     "normalized_parameters": {"requested_by": "thomas", "reason": "r"}},
    },
    # A kind this renderer has never heard of: it must say so, not borrow another kind's answer.
    "unregistered": lambda: {
        "approval_id": "approval_x", "task_id": "task_x",
        "validity": {"expires_at": LATER}, "action_fingerprint": "sha256:f",
        "approved_action_snapshot": {"action_type": "crypto.something.new",
                                     "permission_scope": "RUNTIME_GOVERNANCE", "target_ref": "something:x"},
    },
}

_UNDO = {
    "memory": _MEMORY_UNDO,
    "trial": "되돌릴 수 있는가: 예 — 격리된 1회 시험 실행이며 기록만 남습니다 (역할 활성화 아님)",
    "trading_switch": "되돌릴 수 있는가: 예 — 끄기(stop/pause)는 승인 없이 즉시 적용됩니다",
    "runtime_resume": "되돌릴 수 있는가: 예 — 끄기(stop/pause)는 승인 없이 즉시 적용됩니다",
    "workflow_step": ("되돌릴 수 있는가: 실행 전까지는 예(cancel_workflow·/kill) — 실행된 단계는 "
                      "P3 작업(모델 호출·작업공간 쓰기)으로 기록이 남습니다"),
    "execution_stage": ("되돌릴 수 있는가: 예 — 단계 강등은 승인 없이 즉시 적용됩니다(--demote), "
                        "청산은 어느 단계에서도 막히지 않습니다"),
    "pool_live": ("되돌릴 수 있는가: 예 — 무장 해제(scripts/disarm_live_strategies.py)는 승인 없이 즉시 "
                  "적용되고, 이미 열린 포지션의 청산·보호는 무장과 무관하게 계속됩니다"),
    "pool_paper": _PAPER_POOL_UNDO,
    "retirement": ("되돌릴 수 있는가: 예 — 은퇴한 규칙은 재활성화 승격(scripts/promote_strategy_candidates.py "
                   "--allow-reactivation)으로만 돌아오며, 그 승격도 새 승인 요청을 거칩니다"),
    "correction": ("되돌릴 수 있는가: 아니오 — 정정은 추가 전용 기록이라 거두는 문이 없고, 같은 행에 정정을 "
                   "하나 더 붙이면 CORRECTION_AMBIGUOUS로 라이브 이력 전체가 읽히지 않습니다"),
    "probe": ("되돌릴 수 있는가: 발주 전까지는 예(scripts/run_slippage_probe.py --abandon, 승인 불필요) — "
              "이미 나간 프로브 주문은 실주문이라 그 손익은 되돌릴 수 없습니다"),
    "registration": ("되돌릴 수 있는가: 예 — 작업 트리에 후보 항목(enabled false)만 쓰고 반영은 Thomas의 "
                     "PR이므로, 그 PR을 머지하지 않거나 되돌리면 됩니다"),
    "emergency_close": ("되돌릴 수 있는가: 아니오 — 청산 주문이 나가면 시장가로 확정된 손익은 되돌릴 수 없습니다. "
                        "소비(--confirm) 전에는 아무것도 나가지 않으며(15분 뒤 만료, 그 사이 HARD 정지를 바꾸면 "
                        "무효), 다시 진입하려면 HARD 정지를 푸는 재개(/resume, Thomas)와 정상 진입 경로를 "
                        "처음부터 거쳐야 합니다"),
    "unregistered": ("되돌릴 수 있는가: 확인되지 않음 — 이 요청 종류에는 등록된 답이 없습니다"
                     "(되돌릴 수 없다고 보고 판단해 주세요)"),
}


@pytest.mark.parametrize("kind", sorted(_ASKS))
def test_each_ask_names_what_undoes_it_and_only_a_memory_ask_says_memory(kind):
    text = approval.format_request(_ASKS[kind]())
    assert [line for line in text.splitlines() if line.startswith("되돌릴 수 있는가:")] == [_UNDO[kind]]
    # The memory promotion's answer and its consume step are its own, not a default.
    assert (_MEMORY_UNDO in text) == (kind == "memory")
    assert (_MEMORY_CONSUME_STEP in text) == (kind == "memory")


# The doors that verify their approval (never spend it), each named by the ask it serves.
_DOORS = {
    "pool_paper": "scripts/promote_strategy_candidates.py --confirm",
    "retirement": "scripts/retire_strategies.py --confirm",
    "correction": "scripts/correct_live_outcome.py --confirm",
    "probe": "scripts/run_slippage_probe.py --confirm",
    "registration": "scripts/register_program_candidate.py --confirm",
}


@pytest.mark.parametrize("kind", sorted(_DOORS))
def test_each_verified_ask_sends_the_operator_to_its_own_door(kind):
    """A retirement's target is a paper promotion's (`active_strategy_pool:paper`), so the closing
    paragraph keyed on the target rendered a retirement ask with the promotion door's install step."""
    text = approval.format_request(_ASKS[kind]())
    assert [door for door in _DOORS.values() if door in text] == [_DOORS[kind]]
    assert "approval_consumption" not in text


def test_an_unregistered_ask_names_no_door():
    """The REVIEW_ONLY sentence is true of every ask; a next step for an unknown kind is not guessed."""
    text = approval.format_request(_ASKS["unregistered"]())
    assert text.endswith("이 승인은 REVIEW_ONLY입니다. 승인만으로 런타임이 자동 실행하지 않습니다.")


@requires_local_core
def test_a_paper_tier_pool_promotion_names_the_verbs_that_undo_it():
    """The ask as Thomas receives it: an OBSERVATION promotion is undone by a later promotion or a
    retirement, each asked for again — not "validated memory persists", which it said until
    2026-09-19."""
    permdec = permission.build_strategy_promotion_permission_decision(
        _bound(), live_tier="OBSERVATION", now=NOW, **_PROMOTION)
    text = approval.request_message(approval.build_approval_request(permdec, now=NOW), permdec)
    assert permdec["fingerprint_payload"]["target_ref"] == permission.STRATEGY_POOL_PAPER_TARGET_REF
    assert _PAPER_POOL_UNDO in text
    assert "validated memory" not in text
    assert _MEMORY_CONSUME_STEP not in text
    assert "예상 비용: 없음" in text


def test_the_action_types_the_renderer_keys_on_are_the_ones_their_doors_verify():
    """`permission` names these for the renderer; each owning module keeps its own copy for its
    verification and content hash (`live_correction` must not import `permission`). A drifted pair
    would render one kind's ask with another's text — or with none."""
    from runtime.mvp_runtime import registration
    from runtime.mvp_runtime.crypto import live_correction, probe, promotion, retirement

    assert permission.STRATEGY_PROMOTION_ACTION_TYPE == promotion.PROMOTION_ACTION_TYPE
    assert permission.STRATEGY_RETIREMENT_ACTION_TYPE == retirement.RETIREMENT_ACTION_TYPE
    assert permission.LIVE_OUTCOME_CORRECTION_ACTION_TYPE == live_correction.CORRECTION_ACTION_TYPE
    assert permission.SLIPPAGE_PROBE_ACTION_TYPE == probe.PROBE_ACTION_TYPE
    assert permission.REGISTRATION_ACTION_TYPE == registration.REGISTRATION_ACTION_TYPE
