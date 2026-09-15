"""The execution-stage door, end to end (PR1a): ask -> Thomas approves -> spend once -> record binds;
demote without approval; no skip; no second spend. Needs the local Core, like every ask."""

from __future__ import annotations

import pytest

from runtime.mvp_runtime import approval, permission
from runtime.mvp_runtime.approval_store import ApprovalStore
from runtime.mvp_runtime.crypto import execution_stage as es
from runtime.mvp_runtime.errors import MvpRuntimeError
from scripts import register_execution_stage as door
from tests._helpers import requires_local_core

NOW = "2026-09-14T08:00:00Z"   # in the past: --show reads the real clock


def _approve(root, approval_id):
    store = ApprovalStore.default(root)
    record = store.get(approval_id)
    decision = store.get_permission_decision(record["permission_decision_id"])
    verification = approval.Verification(
        approved_by="Thomas", method="telegram_private_control_channel",
        verification_ref=f"telegram:private_chat:registered-thomas:{approval_id}")
    store.append([approval.record_decision(record, decision, granted=True, verification=verification,
                                           reason="Approved.", now=NOW)])


def _bootstrap_paper(root):
    asked = door.run_request(root=root, now=NOW, target="PAPER", registered_by="thomas", reason="initial",
                             attestation="paper ledger; counterfactual shadow book", valid_days=None)
    _approve(root, asked["approval_id"])
    return asked, door.run_confirm(root=root, now=NOW, approval_id=asked["approval_id"])


@requires_local_core
def test_an_ask_changes_nothing_and_says_what_it_would_record(tmp_path):
    asked = door.run_request(root=tmp_path, now=NOW, target="PAPER", registered_by="thomas", reason="initial",
                             attestation="paper ledger", valid_days=None)
    assert asked["content"]["transition"] == es.T_BOOTSTRAP
    assert es.read_registered_stage(tmp_path) is None
    snapshot = ApprovalStore.default(tmp_path).get(asked["approval_id"])["approved_action_snapshot"]
    assert snapshot["permission_scope"] == permission.EXECUTION_STAGE_PERMISSION_SCOPE
    assert snapshot["target_ref"] == f"{permission.EXECUTION_STAGE_TARGET_PREFIX}binance_futures:PAPER"
    assert snapshot["normalized_parameters"] == asked["content"]


@requires_local_core
def test_the_approved_transition_is_spent_once_and_the_record_binds(tmp_path):
    asked, confirmed = _bootstrap_paper(tmp_path)
    assert confirmed["status"]["stage"] == "PAPER" and confirmed["status"]["valid"] is True
    spent = ApprovalStore.default(tmp_path).get(asked["approval_id"])
    assert spent["status"] == "CONSUMED"
    assert spent["consumption"]["consumption_ref"] == es.consumption_ref(confirmed["record"]["stage_id"])
    with pytest.raises(MvpRuntimeError) as exc:
        door.run_confirm(root=tmp_path, now=NOW, approval_id=asked["approval_id"])
    assert exc.value.reason_code == "ALREADY_CONSUMED"


@requires_local_core
def test_an_unapproved_ask_cannot_be_spent(tmp_path):
    asked = door.run_request(root=tmp_path, now=NOW, target="PAPER", registered_by="thomas", reason="initial",
                             attestation="paper ledger", valid_days=None)
    with pytest.raises(MvpRuntimeError) as exc:
        door.run_confirm(root=tmp_path, now=NOW, approval_id=asked["approval_id"])
    assert exc.value.reason_code == "NOT_APPROVED"
    assert es.read_registered_stage(tmp_path) is None


@requires_local_core
def test_a_skip_is_refused_before_anything_is_asked(tmp_path):
    _bootstrap_paper(tmp_path)
    before = len(ApprovalStore.default(tmp_path).read_all())
    with pytest.raises(MvpRuntimeError) as exc:
        door.run_request(root=tmp_path, now=NOW, target="LIVE_AUTONOMOUS", registered_by="t", reason="r",
                         attestation=None, valid_days=7)
    assert exc.value.reason_code == es.STAGE_SKIP_REFUSED
    assert len(ApprovalStore.default(tmp_path).read_all()) == before


@requires_local_core
def test_a_record_that_moved_after_the_ask_refuses_the_spend_and_keeps_the_grant(tmp_path):
    _bootstrap_paper(tmp_path)
    asked = door.run_request(root=tmp_path, now=NOW, target="SIGNED_TESTNET", registered_by="t", reason="r",
                             attestation=None, valid_days=None)
    _approve(tmp_path, asked["approval_id"])
    door.run_demote(root=tmp_path, now=NOW, target="SHADOW", registered_by="t", reason="moved")
    with pytest.raises(MvpRuntimeError) as exc:
        door.run_confirm(root=tmp_path, now=NOW, approval_id=asked["approval_id"])
    assert exc.value.reason_code == es.STAGE_CHANGED
    assert ApprovalStore.default(tmp_path).get(asked["approval_id"])["status"] == "APPROVED"


@requires_local_core
def test_a_demotion_needs_no_approval_and_leaves_a_ledger_event(tmp_path):
    from runtime.mvp_runtime.store import LEDGER_REL

    _bootstrap_paper(tmp_path)
    out = door.run_demote(root=tmp_path, now=NOW, target="READ_ONLY", registered_by="thomas", reason="stop")
    assert out["status"]["stage"] == "READ_ONLY" and out["status"]["valid"] is True
    import json

    from runtime.mvp_runtime.store import CONTROL_FILE

    lines = (tmp_path / LEDGER_REL / CONTROL_FILE).read_text(encoding="utf-8").splitlines()
    events = [e for e in map(json.loads, lines) if e.get("record_type") == es.TRANSITION_EVENT_TYPE]
    assert [e["transition"] for e in events] == [es.T_BOOTSTRAP, es.T_DEMOTE]


def test_there_is_no_escape_hatch_around_the_approval():
    with pytest.raises(SystemExit):
        door.main(["--request", "--to", "PAPER", "--registered-by", "t", "--reason", "r", "--without-approval"])


@requires_local_core
def test_show_reports_the_stage_without_changing_anything(tmp_path, capsys):
    _bootstrap_paper(tmp_path)
    assert door.main(["--show", "--root", str(tmp_path)]) == door.EXIT_OK
    out = capsys.readouterr().out
    assert "execution stage : PAPER" in out
    assert "none yet" in out   # PR1a says plainly that nothing enforces the stage
