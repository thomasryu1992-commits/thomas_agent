"""Audit gaps must be durable, not a warning nobody keeps.

Two paths deliberately keep going when their audit append fails — the R9 ask and Thomas's
answer, because losing his decision to protect a log is the wrong trade. That left the gap
recorded only in a stderr line and a chat suffix, both gone the moment the terminal
scrolls, so "does the trail have a hole here?" had no answer after the fact. These tests
pin the durable half and that `recovery` surfaces it.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime import control, timeutil
from runtime.mvp_runtime.audit import AUDIT_GAP_TYPE, build_audit_gap_record
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.errors import PersistenceError
from runtime.mvp_runtime.operator import (
    MockOperatorChannel,
    OperatorIdentity,
    InboundMessage,
    run_operator_once,
)
from runtime.mvp_runtime.binding import DEFAULT_POINTER_REL
from runtime.mvp_runtime.store import LedgerStore
from runtime.mvp_runtime.worker import MockProvider

from tests._helpers import requires_local_core

NOW = "2026-07-20T09:00:00Z"
REG = OperatorIdentity(operator_id="tg-12345", chat_id="chat-777")


def _msg(**overrides):
    params = dict(text="이 사업 아이디어를 분석해줘", sender_id="tg-12345", chat_id="chat-777",
                  chat_type="private", is_forwarded=False, channel="telegram_private")
    params.update(overrides)
    return InboundMessage(**params)


# --- the record -------------------------------------------------------------

def test_gap_record_is_self_hashed_and_names_what_is_unaudited():
    record = build_audit_gap_record(
        "approval_decision", reason_code="LEDGER_WRITE_FAILED",
        subject_ref="approval_abc", now=NOW, detail="disk full",
    )
    assert record["record_type"] == AUDIT_GAP_TYPE
    assert record["gap_kind"] == "approval_decision"
    assert record["reason_code"] == "LEDGER_WRITE_FAILED"
    assert record["subject_ref"] == "approval_abc"
    assert record["integrity"]["event_sha256"].startswith("sha256:")


def test_gap_records_are_readable_back_from_the_block_ledger(tmp_path):
    ledger = LedgerStore(tmp_path / "ledger")
    ledger.append_block(build_audit_gap_record(
        "approval_request", reason_code="LEDGER_WRITE_FAILED",
        subject_ref="approval_1", now=NOW))
    gaps = [e for e in ledger.read_blocks() if e["record_type"] == AUDIT_GAP_TYPE]
    assert len(gaps) == 1 and gaps[0]["subject_ref"] == "approval_1"


# --- recovery surfaces them -------------------------------------------------

def test_recovery_reports_known_audit_gaps(tmp_path):
    ledger = LedgerStore(tmp_path / "ledger")
    ledger.append_block(build_audit_gap_record(
        "approval_decision", reason_code="LEDGER_WRITE_FAILED",
        subject_ref="approval_abc", now=NOW))
    text = control.recovery_lines(ControlStore(tmp_path).load(), ledger)
    assert "KNOWN AUDIT GAPS: 1 recorded" in text
    assert "approval_decision" in text and "approval_abc" in text


def test_recovery_is_quiet_when_there_are_no_gaps(tmp_path):
    ledger = LedgerStore(tmp_path / "ledger")
    assert "KNOWN AUDIT GAPS" not in control.recovery_lines(ControlStore(tmp_path).load(), ledger)


def test_recovery_survives_an_unreadable_block_ledger(tmp_path):
    """recovery is what an operator runs when things are already broken; enriching it must
    never be what breaks it."""
    ledger = LedgerStore(tmp_path / "ledger")
    ledger.root.mkdir(parents=True, exist_ok=True)
    (ledger.root / "blocks.jsonl").write_text("{not json\n", encoding="utf-8")
    text = control.recovery_lines(ControlStore(tmp_path).load(), ledger)
    assert "CORRUPT" in text                      # reported in the stores section...
    assert "KNOWN AUDIT GAPS" not in text         # ...without the enrichment raising


# --- the operator decision path ---------------------------------------------

class _AuditFailsLedger(LedgerStore):
    """Appends work except the audit chain — the shape of a locked or full audit file."""

    def append_audit_events(self, events):
        raise PersistenceError("LEDGER_WRITE_FAILED", "audit file unavailable")


def _pending_approval(tmp_path):
    """A real PENDING approval bound to a stored candidate, as approval_cli would ask."""
    from runtime.mvp_runtime import approval, permission
    from runtime.mvp_runtime.approval_store import ApprovalStore
    from runtime.mvp_runtime.binding import bind_task_to_core
    from runtime.mvp_runtime.intake import build_task
    from runtime.mvp_runtime.memory import CANDIDATE_SCOPE, CANDIDATE_STATUS
    from runtime.read_only_kernel import integrity

    task = build_task("이 사업 아이디어를 분석해줘", now=NOW, channel="manual",
                      requester_type="real_thomas", requester_id="Thomas", authenticated=True)
    _, bound = bind_task_to_core(task, now=NOW)
    ident, ctx = bound["identity"], bound["context"]
    content = "구독 모델은 현금흐름 우선 프레이밍이 유효하다."
    candidate = {
        "candidate_id": integrity.short_id("memcand", {"content": content}),
        "candidate_type": "reusable_knowledge", "scope": CANDIDATE_SCOPE,
        "status": CANDIDATE_STATUS, "validated": False, "promotable": False,
        "content": content, "evidence_refs": ["model:analysis"], "created_at": NOW,
        "expires_at": timeutil.plus_minutes(NOW, 7 * 24 * 60),
        "origin": {"task_id": ident["task_id"], "task_revision": ident["task_revision"],
                   "trace_id": ident["trace_id"],
                   "core_context_binding_id": ctx["core_context_binding_id"],
                   "data_sensitivity": ctx["data_sensitivity"]},
    }
    store = ApprovalStore(tmp_path / "approvals")
    permdec = permission.build_memory_promotion_permission_decision(bound, candidate, now=NOW)
    request = approval.build_approval_request(permdec, now=NOW)
    store.append_permission_decision(permdec)
    store.append([request])
    return store, request["approval_id"]


@requires_local_core
def test_a_failed_decision_audit_leaves_a_durable_gap(tmp_path):
    """Thomas's answer must survive a failed audit append (losing his decision to protect
    a log is the wrong trade) — and the hole it leaves must be findable afterwards
    instead of living in a chat suffix nobody keeps."""
    approvals, approval_id = _pending_approval(tmp_path)
    ledger = _AuditFailsLedger(tmp_path / "ledger")
    ch = MockOperatorChannel(inbound=[_msg(text=f"/approve {approval_id}")])

    run_operator_once(ch, REG, provider=MockProvider(), now=NOW, store=ledger,
                      approval_store=approvals)

    # The decision stands...
    assert approvals.get(approval_id)["status"] == "APPROVED"
    assert "decision audit failed" in ch.sent[0][1]
    # ...and the gap is durable, naming exactly what is unaudited.
    gaps = [e for e in ledger.read_blocks() if e["record_type"] == AUDIT_GAP_TYPE]
    assert len(gaps) == 1
    assert gaps[0]["gap_kind"] == "approval_decision"
    assert gaps[0]["subject_ref"] == approval_id
    assert gaps[0]["reason_code"] == "LEDGER_WRITE_FAILED"

    # And `recovery` surfaces it — the whole point of making it durable.
    assert "KNOWN AUDIT GAPS" in control.recovery_lines(ControlStore(tmp_path).load(), ledger)


# --- unverified probes ------------------------------------------------------

def test_dropped_probes_leave_one_durable_note_per_batch(tmp_path):
    """Silent non-engagement is right — no reply, no info leak — but "somebody probed this
    bot" lived only in an in-memory counter. One entry per batch with the count, never one
    per message: a per-message record would make a spammer a disk-fill vector."""
    ledger = LedgerStore(tmp_path / "ledger")
    ch = MockOperatorChannel(inbound=[
        _msg(sender_id="tg-99999"), _msg(chat_type="group"), _msg(sender_id="tg-1"),
    ])
    summary = run_operator_once(ch, REG, provider=MockProvider(), now=NOW, store=ledger)
    assert summary["dropped"] == 3 and ch.sent == []          # still no engagement
    probes = [e for e in ledger.read_blocks() if e["record_type"] == "operator_probe.v0"]
    assert len(probes) == 1 and probes[0]["dropped"] == 3


def test_no_probe_note_when_every_message_is_verified(tmp_path):
    ledger = LedgerStore(tmp_path / "ledger")
    run_operator_once(MockOperatorChannel(), REG, provider=MockProvider(), now=NOW, store=ledger)
    assert [e for e in ledger.read_blocks() if e["record_type"] == "operator_probe.v0"] == []
