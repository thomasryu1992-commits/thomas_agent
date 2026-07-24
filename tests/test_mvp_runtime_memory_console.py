"""Memory-console tests — /memory (list) and /promote over the control channel.

The convenience door onto scripts/promote_memory_candidate.py: same building blocks,
same guards, same audit event, exposed as a control-channel command family. Fail-closed
everywhere — no store, unknown/expired candidate, missing reason, or a non-ACTIVE kill
state all refuse with a typed reason, never a guess or an unaudited mutation.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime import control, memory_console
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.errors import OperatorBlocked
from runtime.mvp_runtime.memory import (
    CANDIDATE_STATUS,
    PROMOTED_STATUS,
    VALIDATED_STATUS,
)
from runtime.mvp_runtime.operator import (
    InboundMessage,
    OperatorIdentity,
    handle_operator_message,
)
from runtime.mvp_runtime.store import AUDIT_FILE, LedgerStore
from runtime.mvp_runtime.working_memory import WorkingMemoryStore, find_candidate

NOW = "2026-07-24T09:00:00Z"
REG = OperatorIdentity(operator_id="tg-12345", chat_id="chat-777")

_ORIGIN = {
    "task_id": "task_origin1", "task_revision": 1, "trace_id": "trace_origin1",
    "core_context_binding_id": "ccb-origin1", "data_sensitivity": "INTERNAL",
}


def _candidate(**overrides):
    c = {
        "candidate_id": "memcand_x1", "candidate_type": "reusable_knowledge",
        "scope": "task_working_memory", "status": CANDIDATE_STATUS, "validated": False,
        "promotable": False, "content": "recurring-revenue model works", "created_at": NOW,
        "expires_at": "2026-08-01T00:00:00Z", "origin": dict(_ORIGIN),
    }
    c.update(overrides)
    return c


def _wm(tmp_path):
    return WorkingMemoryStore(tmp_path / "working_memory")


def _ledger(tmp_path):
    return LedgerStore(tmp_path / "ledger")


# --- parsing -----------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("/memory", ("LIST", None, None)),
    ("memory", ("LIST", None, None)),
    ("/memory list", ("LIST", None, None)),
    ("/MEMORY", ("LIST", None, None)),
    ("/memory@thomas_bot", ("LIST", None, None)),
    ("/promote memcand_x1 confirmed reuse", ("PROMOTE", "memcand_x1", "confirmed reuse")),
    ("promote memcand_x1", ("PROMOTE", "memcand_x1", None)),
    ("/promote", ("PROMOTE", None, None)),
])
def test_parse(text, expected):
    assert memory_console.parse_memory_command(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "이 사업 아이디어를 분석해줘", "/status", "/feedback good", 42, None])
def test_parse_non_memory(text):
    assert memory_console.parse_memory_command(text) is None


# --- listing (read-only) -----------------------------------------------------

def test_list_empty(tmp_path):
    out = memory_console.apply_memory_command(
        ("LIST", None, None), operator_id="Thomas",
        working_memory=_wm(tmp_path), ledger=None, control_store=None, now=NOW,
    )
    assert out["count"] == 0 and "후보가 없습니다" in out["reply"]


def test_list_shows_live_only(tmp_path):
    wm = _wm(tmp_path)
    wm.append([
        _candidate(candidate_id="memcand_live", content="live finding"),
        _candidate(candidate_id="memcand_exp", content="expired finding",
                   expires_at="2020-01-01T00:00:00Z"),
    ])
    out = memory_console.apply_memory_command(
        ("LIST", None, None), operator_id="Thomas",
        working_memory=wm, ledger=None, control_store=None, now=NOW,
    )
    assert out["count"] == 1
    assert "memcand_live" in out["reply"] and "memcand_exp" not in out["reply"]


def test_list_needs_store():
    with pytest.raises(OperatorBlocked) as exc:
        memory_console.apply_memory_command(
            ("LIST", None, None), operator_id="Thomas",
            working_memory=None, ledger=None, control_store=None, now=NOW,
        )
    assert exc.value.reason_code == "MEMORY_UNAVAILABLE"


# --- promotion (EXECUTE_AND_REPORT) ------------------------------------------

def test_promote_happy_path(tmp_path):
    wm, ledger, cs = _wm(tmp_path), _ledger(tmp_path), ControlStore(tmp_path)
    wm.append([_candidate()])
    out = memory_console.apply_memory_command(
        ("PROMOTE", "memcand_x1", "confirmed reusable"), operator_id="Thomas",
        working_memory=wm, ledger=ledger, control_store=cs, now=NOW,
    )
    assert out["action"] == "MEMORY_PROMOTED"
    # VALIDATED entry written, candidate retired, audit event chained.
    validated = wm.read_validated()
    assert len(validated) == 1 and validated[0]["status"] == VALIDATED_STATUS
    assert find_candidate(wm, "memcand_x1") is None  # PROMOTED marker retired it
    statuses = [e["status"] for e in wm.read_all() if e["candidate_id"] == "memcand_x1"]
    assert PROMOTED_STATUS in statuses
    audit = [ln for ln in (ledger.root / AUDIT_FILE).read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(audit) == 1 and "MEMORY_PROMOTED" in audit[0]


def test_promote_requires_reason(tmp_path):
    wm = _wm(tmp_path); wm.append([_candidate()])
    with pytest.raises(OperatorBlocked) as exc:
        memory_console.apply_memory_command(
            ("PROMOTE", "memcand_x1", None), operator_id="Thomas",
            working_memory=wm, ledger=_ledger(tmp_path), control_store=ControlStore(tmp_path), now=NOW,
        )
    assert exc.value.reason_code == "MISSING_REASON"


def test_promote_requires_candidate_id(tmp_path):
    with pytest.raises(OperatorBlocked) as exc:
        memory_console.apply_memory_command(
            ("PROMOTE", None, None), operator_id="Thomas",
            working_memory=_wm(tmp_path), ledger=_ledger(tmp_path), control_store=ControlStore(tmp_path), now=NOW,
        )
    assert exc.value.reason_code == "USAGE"


def test_promote_unknown_candidate(tmp_path):
    with pytest.raises(OperatorBlocked) as exc:
        memory_console.apply_memory_command(
            ("PROMOTE", "memcand_nope", "reason"), operator_id="Thomas",
            working_memory=_wm(tmp_path), ledger=_ledger(tmp_path), control_store=ControlStore(tmp_path), now=NOW,
        )
    assert exc.value.reason_code == "CANDIDATE_GONE"


def test_promote_expired_refused(tmp_path):
    wm = _wm(tmp_path)
    wm.append([_candidate(expires_at="2020-01-01T00:00:00Z")])
    with pytest.raises(OperatorBlocked) as exc:
        memory_console.apply_memory_command(
            ("PROMOTE", "memcand_x1", "reason"), operator_id="Thomas",
            working_memory=wm, ledger=_ledger(tmp_path), control_store=ControlStore(tmp_path), now=NOW,
        )
    assert exc.value.reason_code == "CANDIDATE_EXPIRED"


def test_promote_kill_switch_refused(tmp_path):
    wm = _wm(tmp_path); wm.append([_candidate()])
    cs = ControlStore(tmp_path)
    control.apply_command(cs, "kill", actor="tester", now=NOW)
    with pytest.raises(OperatorBlocked) as exc:
        memory_console.apply_memory_command(
            ("PROMOTE", "memcand_x1", "reason"), operator_id="Thomas",
            working_memory=wm, ledger=_ledger(tmp_path), control_store=cs, now=NOW,
        )
    assert exc.value.reason_code in ("RUNTIME_KILLED", "RUNTIME_PAUSED")
    assert wm.read_validated() == []  # nothing mutated


def test_promote_no_ledger_fails_closed(tmp_path):
    wm = _wm(tmp_path); wm.append([_candidate()])
    with pytest.raises(OperatorBlocked) as exc:
        memory_console.apply_memory_command(
            ("PROMOTE", "memcand_x1", "reason"), operator_id="Thomas",
            working_memory=wm, ledger=None, control_store=ControlStore(tmp_path), now=NOW,
        )
    assert exc.value.reason_code == "MEMORY_UNAVAILABLE"


# --- end-to-end through the operator dispatch --------------------------------

def _msg(text):
    return InboundMessage(text=text, sender_id="tg-12345", chat_id="chat-777",
                          chat_type="private", is_forwarded=False, channel="telegram_private")


def test_dispatch_promote_end_to_end(tmp_path):
    wm, ledger, cs = _wm(tmp_path), _ledger(tmp_path), ControlStore(tmp_path)
    wm.append([_candidate()])
    reply = handle_operator_message(
        _msg("/promote memcand_x1 확인된 재사용 지식"), registration=REG, now=NOW,
        working_memory=wm, store=ledger, control_store=cs,
    )
    assert reply.accepted and reply.status == "MEMORY" and reply.reason_code == "MEMORY_PROMOTED"
    assert wm.read_validated()[0]["status"] == VALIDATED_STATUS


def test_dispatch_list_answers_in_any_mode(tmp_path):
    wm = _wm(tmp_path); wm.append([_candidate()])
    reply = handle_operator_message(
        _msg("/memory"), registration=REG, now=NOW,
        working_memory=wm, store=_ledger(tmp_path), control_store=ControlStore(tmp_path),
    )
    assert reply.accepted and reply.status == "MEMORY" and reply.reason_code == "MEMORY_LISTED"
    assert "memcand_x1" in reply.text
