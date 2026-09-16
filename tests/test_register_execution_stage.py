"""The execution-stage door, end to end (PR1a): ask -> Thomas approves -> spend once -> record binds;
demote without approval; no skip; no second spend; one writer at a time; a failure after the spend
says so. Needs the local Core, like every ask."""

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
                             attestation="paper ledger; counterfactual shadow book")
    _approve(root, asked["approval_id"])
    return asked, door.run_confirm(root=root, now=NOW, approval_id=asked["approval_id"])


@requires_local_core
def test_an_ask_changes_nothing_and_says_what_it_would_record(tmp_path):
    asked = door.run_request(root=tmp_path, now=NOW, target="PAPER", registered_by="thomas", reason="initial",
                             attestation="paper ledger")
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
                             attestation="paper ledger")
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
                         attestation=None)
    assert exc.value.reason_code == es.STAGE_SKIP_REFUSED
    assert len(ApprovalStore.default(tmp_path).read_all()) == before


@requires_local_core
def test_a_record_that_moved_after_the_ask_refuses_the_spend_and_keeps_the_grant(tmp_path):
    _bootstrap_paper(tmp_path)
    asked = door.run_request(root=tmp_path, now=NOW, target="SIGNED_TESTNET", registered_by="t", reason="r",
                             attestation=None)
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
    # PR1b: the board says what the doors do with this rung, and that closing is never gated.
    assert "refuses a new live entry below LIVE_AUTONOMOUS" in out
    assert "closing is never gated" in out
    assert "valid until" not in out   # no stage expires


def _testnet(root):
    _bootstrap_paper(root)
    asked = door.run_request(root=root, now=NOW, target="SIGNED_TESTNET", registered_by="thomas", reason="climb",
                             attestation=None)
    _approve(root, asked["approval_id"])
    return asked, door.run_confirm(root=root, now=NOW, approval_id=asked["approval_id"])


@requires_local_core
def test_the_real_ledger_witnesses_a_climb_and_a_demotion_that_carries_it(tmp_path):
    asked, confirmed = _testnet(tmp_path)
    assert (confirmed["status"]["stage"], confirmed["status"]["valid"]) == ("SIGNED_TESTNET", True)
    out = door.run_demote(root=tmp_path, now=NOW, target="SHADOW", registered_by="thomas", reason="step back")
    assert (out["status"]["stage"], out["status"]["valid"]) == ("SHADOW", True)
    assert out["record"]["approval_id"] == asked["approval_id"]


@requires_local_core
def test_after_a_policy_change_a_demotion_reaches_only_read_only(tmp_path, monkeypatch):
    _testnet(tmp_path)
    real = es.policy_safety_identity()
    monkeypatch.setattr(es, "policy_safety_identity", lambda root=None: {**real, "policy_version": "1.5.1"})
    with pytest.raises(MvpRuntimeError) as exc:
        door.run_demote(root=tmp_path, now=NOW, target="PAPER", registered_by="t", reason="r")
    assert exc.value.reason_code == es.STAGE_DEMOTE_FROM_UNBOUND
    out = door.run_demote(root=tmp_path, now=NOW, target="READ_ONLY", registered_by="t", reason="stop")
    assert (out["status"]["stage"], out["status"]["valid"]) == ("READ_ONLY", True)


@requires_local_core
def test_the_spend_rechecks_the_record_under_the_stage_lock(tmp_path, monkeypatch):
    """Review of #872: a --demote landing between the re-check and the write was overwritten by the
    climb. Both doors now hold one lock, and the confirm re-reads the record inside it."""
    from contextlib import contextmanager

    _bootstrap_paper(tmp_path)
    asked = door.run_request(root=tmp_path, now=NOW, target="SIGNED_TESTNET", registered_by="t", reason="r",
                             attestation=None)
    _approve(tmp_path, asked["approval_id"])
    held = {"now": False, "reads_inside": 0}
    real_lock, real_resolve = es.stage_lock, es.resolve_execution_stage

    @contextmanager
    def watched(root=None):
        with real_lock(root):
            held["now"] = True
            try:
                yield
            finally:
                held["now"] = False

    def counting(*a, **kw):
        held["reads_inside"] += held["now"]
        return real_resolve(*a, **kw)

    monkeypatch.setattr(es, "stage_lock", watched)
    monkeypatch.setattr(es, "resolve_execution_stage", counting)
    door.run_confirm(root=tmp_path, now=NOW, approval_id=asked["approval_id"])
    assert held["reads_inside"] == 1


@requires_local_core
def test_a_record_write_that_fails_after_the_spend_says_the_grant_is_gone(tmp_path, monkeypatch):
    asked = door.run_request(root=tmp_path, now=NOW, target="PAPER", registered_by="thomas", reason="initial",
                             attestation="paper ledger")
    _approve(tmp_path, asked["approval_id"])

    def refuse(record, root=None):
        raise PermissionError("read-only state dir")

    monkeypatch.setattr(es, "write_stage_record", refuse)
    with pytest.raises(MvpRuntimeError) as exc:
        door.run_confirm(root=tmp_path, now=NOW, approval_id=asked["approval_id"])
    assert exc.value.reason_code == es.STAGE_WRITE_FAILED_AFTER_SPEND
    assert asked["approval_id"] in str(exc.value) and "ask Thomas again" in str(exc.value)
    assert ApprovalStore.default(tmp_path).get(asked["approval_id"])["status"] == "CONSUMED"
    assert es.read_registered_stage(tmp_path) is None


@requires_local_core
def test_a_ledger_failure_after_the_write_is_a_warning_not_a_block(tmp_path, monkeypatch, capsys):
    from runtime.mvp_runtime.errors import PersistenceError
    from runtime.mvp_runtime.store import LedgerStore

    _bootstrap_paper(tmp_path)

    def refuse(self, event):
        raise PersistenceError("LEDGER_WRITE_FAILED", "disk full")

    monkeypatch.setattr(LedgerStore, "append_control", refuse)
    out = door.run_demote(root=tmp_path, now=NOW, target="READ_ONLY", registered_by="t", reason="stop")
    assert out["status"]["stage"] == "READ_ONLY"
    assert out["warnings"] and "LEDGER_WRITE_FAILED" in out["warnings"][0]


def _complete_cycle(root, cycle_id="cyc_door"):
    from runtime.mvp_runtime.crypto import testnet_evidence

    def leg(name):
        return {"leg": name, "algo": name == "SL",
                "order_type": "STOP_MARKET" if name == "SL" else "LIMIT",
                "observed_status": "NEW", "withdrawn": True}

    record = testnet_evidence.build_cycle_record(
        cycle_id=cycle_id, symbol="BTCUSDT",
        entry={"reconcile_status": "RECONCILED", "mismatches": []},
        protective_legs=[leg("SL"), leg("TP")],
        exit_result={"reconcile_status": "RECONCILED", "reduce_only": True},
        position_reconciliation={"status": "RECONCILED", "venue_positions": []},
        adapter_tool_id="crypto.testnet.order_adapter", base_url_host="testnet.binancefuture.com",
        started_at=NOW, completed_at=NOW,
    )
    testnet_evidence.append_cycle(record, root)
    return record


@requires_local_core
def test_the_live_climb_is_asked_and_spent_against_the_cycle_under_this_state_root(tmp_path):
    """The whole PR1d chain through the real door: a cycle recorded under THIS state root, named on
    the ask, signed into the approval, and re-verified when it is spent.

    The state root matters and is why this test exists: the evidence lives beside the machine's
    other governed state, so a door that looked it up under the image tree would refuse a cycle
    that is right there — and blame the evidence for it (review of #878)."""
    _testnet(tmp_path)
    record = _complete_cycle(tmp_path)
    asked = door.run_request(root=tmp_path, now=NOW, target="LIVE_AUTONOMOUS", registered_by="thomas",
                             reason="the cycle is clean", attestation=None, testnet_cycle="cyc_door")
    assert asked["content"]["evidence"]["testnet_cycle_id"] == "cyc_door"
    assert asked["content"]["evidence"]["testnet_cycle_sha256"] == record["record_sha256"]
    _approve(tmp_path, asked["approval_id"])
    confirmed = door.run_confirm(root=tmp_path, now=NOW, approval_id=asked["approval_id"])
    assert (confirmed["status"]["stage"], confirmed["status"]["valid"]) == ("LIVE_AUTONOMOUS", True)
    assert confirmed["record"]["evidence"]["testnet_cycle_id"] == "cyc_door"


@requires_local_core
def test_an_unnamed_or_unearned_cycle_asks_for_nothing(tmp_path):
    _testnet(tmp_path)
    before = len(ApprovalStore.default(tmp_path).read_all())
    for cycle in (None, "cyc_missing"):
        with pytest.raises(MvpRuntimeError) as exc:
            door.run_request(root=tmp_path, now=NOW, target="LIVE_AUTONOMOUS", registered_by="t",
                             reason="r", attestation=None, testnet_cycle=cycle)
        assert exc.value.reason_code in (es.STAGE_SIGNED_TESTNET_EVIDENCE_REQUIRED,
                                         "TESTNET_EVIDENCE_INCOMPLETE")
    assert len(ApprovalStore.default(tmp_path).read_all()) == before, (
        "a refused climb still spent Thomas's attention: it stored an approval request")


@requires_local_core
def test_a_cycle_tampered_between_the_ask_and_the_spend_installs_nothing(tmp_path):
    """The spend re-plans, so the registry is read again: an approval won on a row that has since
    been edited writes no record and stays APPROVED."""
    import json

    from runtime.mvp_runtime.crypto import testnet_evidence

    _testnet(tmp_path)
    record = _complete_cycle(tmp_path)
    asked = door.run_request(root=tmp_path, now=NOW, target="LIVE_AUTONOMOUS", registered_by="thomas",
                             reason="r", attestation=None, testnet_cycle="cyc_door")
    _approve(tmp_path, asked["approval_id"])
    tampered = {**record, "symbol": "ETHUSDT"}          # hash left alone
    testnet_evidence.evidence_path(tmp_path).write_text(json.dumps(tampered) + "\n", encoding="utf-8")
    with pytest.raises(MvpRuntimeError) as exc:
        door.run_confirm(root=tmp_path, now=NOW, approval_id=asked["approval_id"])
    assert exc.value.reason_code == testnet_evidence.EVIDENCE_TAMPERED
    assert ApprovalStore.default(tmp_path).get(asked["approval_id"])["status"] == "APPROVED"
    assert es.read_registered_stage(tmp_path)["stage"] == "SIGNED_TESTNET"
