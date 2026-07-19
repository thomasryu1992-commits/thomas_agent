"""approval_cli tests — the operator-facing surface for ask / list / show / consume.

consumption.py itself is well covered; what was untested was the CLI wrapping it: argument
dispatch, the store wiring (module-level ``.default()``s, patched here onto tmp stores so
nothing touches the machine's real approval store), the PENDING lifecycle as the operator
sees it, and the consume verb's fail-closed refusal when the safety flag is off.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime import approval, consumption, permission, timeutil
from runtime.mvp_runtime.approval_cli import main
from runtime.mvp_runtime.approval_store import ApprovalStore
from runtime.mvp_runtime.binding import DEFAULT_POINTER_REL, bind_task_to_core
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK
from runtime.mvp_runtime.consumption import _CapableConsumer
from runtime.mvp_runtime.intake import build_task
from runtime.mvp_runtime.memory import CANDIDATE_SCOPE, CANDIDATE_STATUS
from runtime.mvp_runtime.safety_gate import APPROVAL_CONSUMPTION, Authorization
from runtime.mvp_runtime.store import LedgerStore
from runtime.mvp_runtime.working_memory import WorkingMemoryStore
from runtime.read_only_kernel import integrity

from tests._helpers import requires_local_core

NOW = "2026-07-19T12:00:00Z"
CONTENT = "구독 모델은 현금흐름 우선 프레이밍이 유효하다."

GRANT = Authorization(
    flags=(APPROVAL_CONSUMPTION,), provider_id="approval_consumption",
    activation_sha256="sha256:test", expires_at="2999-01-01T00:00:00Z",
    evidence_ref=".runtime_governance_state/evidence.md",
)


def _patch_defaults(monkeypatch, tmp_path):
    """Point every module-level ``.default()`` at tmp stores — the CLI has no injection
    seam, so this is how a test drives the real entry point hermetically."""
    astore = ApprovalStore(tmp_path / "approvals")
    wm = WorkingMemoryStore(tmp_path / "wm")
    ledger = LedgerStore(tmp_path / "ledger")
    monkeypatch.setattr(ApprovalStore, "default", classmethod(lambda cls: astore))
    monkeypatch.setattr(WorkingMemoryStore, "default", classmethod(lambda cls: wm))
    monkeypatch.setattr(LedgerStore, "default", classmethod(lambda cls: ledger))
    monkeypatch.delenv(consumption.ENV_VAR, raising=False)
    return astore, wm, ledger


def _seed_candidate(wm, content=CONTENT):
    task = build_task("이 사업 아이디어를 분석해줘", now=NOW, channel="manual",
                      requester_type="real_thomas", requester_id="Thomas", authenticated=True)
    _, bound = bind_task_to_core(task, now=NOW)
    ident, ctx = bound["identity"], bound["context"]
    candidate = {
        "candidate_id": integrity.short_id("memcand", {"content": content}),
        "candidate_type": "reusable_knowledge",
        "scope": CANDIDATE_SCOPE, "status": CANDIDATE_STATUS,
        "validated": False, "promotable": False,
        "content": content, "evidence_refs": ["model:analysis"],
        "created_at": NOW, "expires_at": timeutil.plus_minutes(NOW, 7 * 24 * 60),
        "origin": {
            "task_id": ident["task_id"], "task_revision": ident["task_revision"],
            "trace_id": ident["trace_id"],
            "core_context_binding_id": ctx["core_context_binding_id"],
            "data_sensitivity": ctx["data_sensitivity"],
        },
    }
    wm.append([candidate])
    return candidate


# --- verbs that need no Core -------------------------------------------------

def test_list_with_nothing_pending(monkeypatch, tmp_path, capsys):
    _patch_defaults(monkeypatch, tmp_path)
    assert main(["list"]) == EXIT_OK
    assert "No approvals are pending." in capsys.readouterr().out


def test_show_unknown_approval_is_blocked(monkeypatch, tmp_path, capsys):
    _patch_defaults(monkeypatch, tmp_path)
    assert main(["show", "approval_nope"]) == EXIT_BLOCKED
    assert "UNKNOWN_APPROVAL" in capsys.readouterr().err


def test_request_unknown_candidate_is_blocked(monkeypatch, tmp_path, capsys):
    _patch_defaults(monkeypatch, tmp_path)
    assert main(["request", "--candidate-id", "memcand_nope"]) == EXIT_BLOCKED
    assert "UNKNOWN_CANDIDATE" in capsys.readouterr().err


# --- the ask lifecycle as the operator drives it (needs a Core) ---------------

@requires_local_core
def test_request_stores_a_pending_ask_and_audits_it(monkeypatch, tmp_path, capsys):
    astore, wm, ledger = _patch_defaults(monkeypatch, tmp_path)
    candidate = _seed_candidate(wm)

    assert main(["request", "--candidate-id", candidate["candidate_id"]]) == EXIT_OK
    out = capsys.readouterr()
    pending = astore.pending()
    assert len(pending) == 1
    record = pending[0]
    assert record["status"] == "PENDING"
    assert record["approved_action_snapshot"]["target_ref"] == f"memory_candidate:{candidate['candidate_id']}"
    assert record["approval_id"] in out.out                    # the ask names its id
    assert ledger.last_audit_hash() is not None                # the ask is audited

    # list now shows exactly this ask; show renders the PENDING record.
    assert main(["list"]) == EXIT_OK
    assert record["approval_id"] in capsys.readouterr().out
    assert main(["show", record["approval_id"]]) == EXIT_OK
    shown = capsys.readouterr().out
    assert "PENDING" in shown and record["action_fingerprint"] in shown


@requires_local_core
def test_consume_refuses_when_the_safety_flag_is_off(monkeypatch, tmp_path, capsys):
    """The consume verb is gated exactly like every other capability: without the opt-in
    env + activation, the CLI must refuse loudly and promote nothing."""
    astore, wm, ledger = _patch_defaults(monkeypatch, tmp_path)
    candidate = _seed_candidate(wm)
    assert main(["request", "--candidate-id", candidate["candidate_id"]]) == EXIT_OK
    approval_id = astore.pending()[0]["approval_id"]
    _decide(astore, approval_id)

    assert main(["consume", approval_id]) == EXIT_BLOCKED
    assert "CONSUMPTION_DISABLED" in capsys.readouterr().err
    assert wm.read_validated() == []                           # nothing promoted
    assert astore.get(approval_id)["status"] == "APPROVED"     # grant not spent


@requires_local_core
def test_consume_spends_the_grant_end_to_end(monkeypatch, tmp_path, capsys):
    astore, wm, ledger = _patch_defaults(monkeypatch, tmp_path)
    candidate = _seed_candidate(wm)
    assert main(["request", "--candidate-id", candidate["candidate_id"]]) == EXIT_OK
    approval_id = astore.pending()[0]["approval_id"]
    _decide(astore, approval_id)
    # Stand in for the gate (the documented in-process seam): the CLI path itself —
    # dispatch, store wiring, ordering, output — is what this exercises.
    monkeypatch.setattr(consumption, "select_consumer", lambda **_: _CapableConsumer(GRANT))

    assert main(["consume", approval_id]) == EXIT_OK
    out = capsys.readouterr().out
    assert f"CONSUMED {approval_id}" in out and "promoted ->" in out
    assert astore.get(approval_id)["status"] == "CONSUMED"
    assert [v["source_candidate_id"] for v in wm.read_validated()] == [candidate["candidate_id"]]

    # Second spend refuses: single-use, reported as ALREADY_CONSUMED.
    assert main(["consume", approval_id]) == EXIT_BLOCKED
    assert "ALREADY_CONSUMED" in capsys.readouterr().err


def _decide(astore, approval_id):
    """Record Thomas's APPROVED decision the way the operator channel does."""
    record = astore.get(approval_id)
    permdec = astore.get_permission_decision(record["permission_decision_id"])
    verification = approval.Verification(
        approved_by="Thomas", method="telegram_private_control_channel",
        verification_ref=f"telegram:private_chat:registered-thomas:{approval_id}")
    decided = approval.record_decision(record, permdec, granted=True,
                                       verification=verification, reason="Approved.", now=NOW)
    astore.append([decided])
    return decided
