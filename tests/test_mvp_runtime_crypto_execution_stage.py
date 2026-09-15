"""The execution stage record (PR1a; Thomas decisions 1, 4, 8, 9, 2026-09-15).

What is pinned here: the ladder and its transitions (bootstrap at SHADOW/PAPER only, one rung up,
same-rung rebind, any rung down without approval, no skip), every way a record fails to bind and
reads READ_ONLY, the second witness (the CONSUMED approval), and the refusals a transition carries
before any evidence path exists (LIVE_CANARY needs a signed testnet order — PR1d)."""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.crypto import execution_stage as es
from runtime.mvp_runtime.errors import ToolError

NOW = "2026-09-15T08:00:00Z"
LATER = "2026-09-16T08:00:00Z"


class _Approvals:
    """An approval ledger that holds exactly the CONSUMED records the tests put in it."""

    def __init__(self):
        self.records: dict[str, dict] = {}

    def consume(self, approval_id, record):
        self.records[approval_id] = {
            "status": "CONSUMED", "action_fingerprint": record["action_fingerprint"],
            "consumption": {"consumption_ref": es.consumption_ref(record["stage_id"])},
        }

    def get(self, approval_id):
        return self.records.get(approval_id)


def _register(tmp_path, approvals, status, target, *, approval_id, now=NOW, **kw):
    content = es.plan_transition(status, target=target, now=now, registered_by="thomas", reason="test", **kw)
    record = es.record_from_approved(content, status_now=status, approval_id=approval_id,
                                     action_fingerprint=f"sha256:{approval_id}", now=now)
    approvals.consume(approval_id, record)
    es.write_stage_record(record, tmp_path)
    return es.resolve_execution_stage(tmp_path, now=now, approval_store=approvals)


def _paper(tmp_path, approvals):
    start = es.resolve_execution_stage(tmp_path, now=NOW, approval_store=approvals)
    return _register(tmp_path, approvals, start, "PAPER", approval_id="approval_boot", attestation="paper ledger")


def test_the_ladder_is_the_owners_order():
    assert es.LADDER == ("READ_ONLY", "SHADOW", "PAPER", "SIGNED_TESTNET", "LIVE_CANARY",
                         "LIVE_AUTONOMOUS", "LIVE_SCALED")


def test_no_record_reads_read_only(tmp_path):
    status = es.resolve_execution_stage(tmp_path, now=NOW, approval_store=_Approvals())
    assert (status.stage, status.valid, status.reason_code) == ("READ_ONLY", False, es.STAGE_RECORD_MISSING)
    assert not status.allows(es.PURPOSE_CANARY)


def test_bootstrap_at_paper_binds_with_its_approval(tmp_path):
    status = _paper(tmp_path, _Approvals())
    assert (status.stage, status.valid) == ("PAPER", True)
    assert not status.allows(es.PURPOSE_CANARY) and not status.allows(es.PURPOSE_AUTONOMOUS)


@pytest.mark.parametrize("target", ["SIGNED_TESTNET", "LIVE_CANARY", "LIVE_AUTONOMOUS", "READ_ONLY"])
def test_the_first_record_is_only_shadow_or_paper(tmp_path, target):
    start = es.resolve_execution_stage(tmp_path, now=NOW, approval_store=_Approvals())
    with pytest.raises(ToolError) as exc:
        es.plan_transition(start, target=target, now=NOW, registered_by="t", reason="r", attestation="a")
    assert exc.value.reason_code == es.STAGE_BOOTSTRAP_ONLY_SHADOW_OR_PAPER


def test_the_first_record_names_its_evidence(tmp_path):
    start = es.resolve_execution_stage(tmp_path, now=NOW, approval_store=_Approvals())
    with pytest.raises(ToolError) as exc:
        es.plan_transition(start, target="PAPER", now=NOW, registered_by="t", reason="r")
    assert exc.value.reason_code == es.STAGE_ATTESTATION_REQUIRED


def test_a_record_the_door_did_not_spend_an_approval_for_reads_read_only(tmp_path):
    """The self-hash can be recomputed by anyone who can write the state directory (audit FO-12);
    the CONSUMED approval is the second witness."""
    approvals = _Approvals()
    _paper(tmp_path, approvals)
    status = es.resolve_execution_stage(tmp_path, now=NOW, approval_store=_Approvals())   # empty ledger
    assert (status.stage, status.reason_code, status.recorded_stage) == (
        "READ_ONLY", es.STAGE_APPROVAL_NOT_CONSUMED, "PAPER")


def test_an_unreadable_approval_ledger_is_not_a_witness(tmp_path):
    approvals = _Approvals()
    _paper(tmp_path, approvals)

    class _Broken:
        def get(self, approval_id):
            raise OSError("disk")

    assert es.resolve_execution_stage(tmp_path, now=NOW, approval_store=_Broken()).reason_code == (
        es.STAGE_APPROVAL_UNREADABLE)


def test_climbing_is_one_rung_at_a_time(tmp_path):
    approvals = _Approvals()
    paper = _paper(tmp_path, approvals)
    for target in ("LIVE_CANARY", "LIVE_AUTONOMOUS", "LIVE_SCALED"):
        with pytest.raises(ToolError) as exc:
            es.plan_transition(paper, target=target, now=NOW, registered_by="t", reason="r", valid_days=7)
        assert exc.value.reason_code == es.STAGE_SKIP_REFUSED
    testnet = _register(tmp_path, approvals, paper, "SIGNED_TESTNET", approval_id="approval_tn")
    assert (testnet.stage, testnet.valid) == ("SIGNED_TESTNET", True)


def test_live_canary_waits_for_signed_testnet_evidence(tmp_path):
    """Decision 2: a reconciled signed testnet order. The path does not exist yet (PR1d), so the
    climb refuses by name rather than proceeding on anything else."""
    approvals = _Approvals()
    testnet = _register(tmp_path, approvals, _paper(tmp_path, approvals), "SIGNED_TESTNET", approval_id="approval_tn")
    with pytest.raises(ToolError) as exc:
        es.plan_transition(testnet, target="LIVE_CANARY", now=NOW, registered_by="t", reason="r", valid_days=7)
    assert exc.value.reason_code == es.STAGE_SIGNED_TESTNET_EVIDENCE_REQUIRED


def test_live_autonomous_needs_three_clean_canaries_in_code():
    status = es.StageStatus(stage="LIVE_CANARY", valid=True, reason_code=None, recorded_stage="LIVE_CANARY",
                            stage_id="stage_x", record_sha256="sha256:x", valid_until=LATER, policy_version="1.5.0")
    for count in (None, 0, 2):
        with pytest.raises(ToolError) as exc:
            es.plan_transition(status, target="LIVE_AUTONOMOUS", now=NOW, registered_by="t", reason="r",
                               valid_days=7, clean_canary_orders=count)
        assert exc.value.reason_code == es.STAGE_CANARY_EVIDENCE_REQUIRED
    content = es.plan_transition(status, target="LIVE_AUTONOMOUS", now=NOW, registered_by="t", reason="r",
                                 valid_days=7, clean_canary_orders=4)
    assert content["transition"] == es.T_CLIMB and content["evidence"]["clean_canary_orders"] == 4


@pytest.mark.parametrize("days", [None, 0, 31])
def test_a_live_stage_ends_within_thirty_days(days):
    status = es.StageStatus(stage="LIVE_CANARY", valid=True, reason_code=None, recorded_stage="LIVE_CANARY",
                            stage_id="stage_x", record_sha256="sha256:x", valid_until=LATER, policy_version="1.5.0")
    with pytest.raises(ToolError) as exc:
        es.plan_transition(status, target="LIVE_CANARY", now=NOW, registered_by="t", reason="r", valid_days=days)
    assert exc.value.reason_code == es.STAGE_VALIDITY_INVALID


def test_a_climb_from_a_record_that_does_not_bind_is_refused(tmp_path):
    approvals = _Approvals()
    _paper(tmp_path, approvals)
    unbound = es.resolve_execution_stage(tmp_path, now=NOW, approval_store=_Approvals())
    with pytest.raises(ToolError) as exc:
        es.plan_transition(unbound, target="SIGNED_TESTNET", now=NOW, registered_by="t", reason="r")
    assert exc.value.reason_code == es.STAGE_CLIMB_FROM_INVALID
    assert es.plan_transition(unbound, target="PAPER", now=NOW, registered_by="t", reason="r")["transition"] == es.T_REBIND


def test_a_lower_target_is_a_demotion_not_an_ask(tmp_path):
    approvals = _Approvals()
    testnet = _register(tmp_path, approvals, _paper(tmp_path, approvals), "SIGNED_TESTNET", approval_id="approval_tn")
    with pytest.raises(ToolError) as exc:
        es.plan_transition(testnet, target="PAPER", now=NOW, registered_by="t", reason="r")
    assert exc.value.reason_code == es.STAGE_USE_DEMOTE


def test_demotion_needs_no_approval_and_binds_at_once(tmp_path):
    approvals = _Approvals()
    testnet = _register(tmp_path, approvals, _paper(tmp_path, approvals), "SIGNED_TESTNET", approval_id="approval_tn")
    record = es.demote_record(testnet, target="READ_ONLY", registered_by="thomas", reason="stop", now=NOW)
    es.write_stage_record(record, tmp_path)
    status = es.resolve_execution_stage(tmp_path, now=NOW, approval_store=_Approvals())
    assert (status.stage, status.valid, status.recorded_stage) == ("READ_ONLY", True, "READ_ONLY")
    assert record["approval_id"] is None and record["previous_stage"] == "SIGNED_TESTNET"


def test_a_demotion_must_be_lower_and_needs_something_to_demote(tmp_path):
    approvals = _Approvals()
    paper = _paper(tmp_path, approvals)
    with pytest.raises(ToolError) as exc:
        es.demote_record(paper, target="SIGNED_TESTNET", registered_by="t", reason="r", now=NOW)
    assert exc.value.reason_code == es.STAGE_NOT_LOWER
    nothing = es.resolve_execution_stage(tmp_path / "empty", now=NOW, approval_store=approvals)
    with pytest.raises(ToolError) as exc:
        es.demote_record(nothing, target="READ_ONLY", registered_by="t", reason="r", now=NOW)
    assert exc.value.reason_code == es.STAGE_NOTHING_TO_DEMOTE


def test_after_a_demotion_the_way_back_is_the_ladder_again(tmp_path):
    approvals = _Approvals()
    paper = _paper(tmp_path, approvals)
    es.write_stage_record(es.demote_record(paper, target="READ_ONLY", registered_by="t", reason="r", now=NOW), tmp_path)
    read_only = es.resolve_execution_stage(tmp_path, now=NOW, approval_store=approvals)
    with pytest.raises(ToolError) as exc:
        es.plan_transition(read_only, target="PAPER", now=NOW, registered_by="t", reason="r", attestation="a")
    assert exc.value.reason_code == es.STAGE_SKIP_REFUSED


def test_a_tampered_record_reads_read_only(tmp_path):
    approvals = _Approvals()
    _paper(tmp_path, approvals)
    path = es.stage_path(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["stage"] = "LIVE_AUTONOMOUS"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert es.resolve_execution_stage(tmp_path, now=NOW, approval_store=approvals).reason_code == es.STAGE_RECORD_TAMPERED


def test_an_unreadable_record_reads_read_only(tmp_path):
    es.stage_path(tmp_path).parent.mkdir(parents=True)
    es.stage_path(tmp_path).write_text("{not json", encoding="utf-8")
    assert es.resolve_execution_stage(tmp_path, now=NOW, approval_store=_Approvals()).reason_code == es.STAGE_RECORD_UNREADABLE


def test_an_expired_live_record_reads_read_only(tmp_path):
    approvals = _Approvals()
    status = es.StageStatus(stage="SIGNED_TESTNET", valid=True, reason_code=None, recorded_stage="SIGNED_TESTNET",
                            stage_id="stage_x", record_sha256="sha256:x", policy_version="1.5.0")
    content = es.plan_transition(status, target="SIGNED_TESTNET", now=NOW, registered_by="t", reason="r")
    content = {**content, "to_stage": "LIVE_CANARY", "transition": es.T_CLIMB,
               "valid_until": "2026-09-15T09:00:00Z"}   # shaped like a canary climb approved by PR1d's path
    record = es.record_from_approved(content, status_now=status, approval_id="approval_c",
                                     action_fingerprint="sha256:c", now=NOW)
    approvals.consume("approval_c", record)
    es.write_stage_record(record, tmp_path)
    assert es.resolve_execution_stage(tmp_path, now=NOW, approval_store=approvals).stage == "LIVE_CANARY"
    assert es.resolve_execution_stage(tmp_path, now="2026-09-15T10:00:00Z", approval_store=approvals).reason_code == (
        es.STAGE_RECORD_EXPIRED)


def test_a_policy_change_unbinds_the_record_until_it_is_rebound(tmp_path, monkeypatch):
    """Decision 4: bound to the policy version AND the safety semantic fingerprint."""
    approvals = _Approvals()
    _paper(tmp_path, approvals)
    real = es.policy_safety_identity()
    monkeypatch.setattr(es, "policy_safety_identity",
                        lambda root=None: {**real, "policy_safety_sha256": "sha256:" + "0" * 64})
    assert es.resolve_execution_stage(tmp_path, now=NOW, approval_store=approvals).reason_code == (
        es.STAGE_POLICY_SAFETY_CHANGED)
    monkeypatch.setattr(es, "policy_safety_identity", lambda root=None: {**real, "policy_version": "9.9.9"})
    assert es.resolve_execution_stage(tmp_path, now=NOW, approval_store=approvals).reason_code == (
        es.STAGE_POLICY_VERSION_CHANGED)
    monkeypatch.setattr(es, "policy_safety_identity", lambda root=None: None)
    assert es.resolve_execution_stage(tmp_path, now=NOW, approval_store=approvals).reason_code == (
        es.STAGE_POLICY_UNREADABLE)


def test_an_approval_asked_against_one_record_does_not_apply_over_another(tmp_path):
    approvals = _Approvals()
    paper = _paper(tmp_path, approvals)
    content = es.plan_transition(paper, target="SIGNED_TESTNET", now=NOW, registered_by="t", reason="r")
    es.write_stage_record(es.demote_record(paper, target="SHADOW", registered_by="t", reason="r", now=NOW), tmp_path)
    moved = es.resolve_execution_stage(tmp_path, now=NOW, approval_store=approvals)
    with pytest.raises(ToolError) as exc:
        es.record_from_approved(content, status_now=moved, approval_id="approval_late",
                                action_fingerprint="sha256:late", now=NOW)
    assert exc.value.reason_code == es.STAGE_CHANGED


def test_an_approval_asked_under_one_policy_does_not_apply_under_another(tmp_path, monkeypatch):
    approvals = _Approvals()
    paper = _paper(tmp_path, approvals)
    content = es.plan_transition(paper, target="SIGNED_TESTNET", now=NOW, registered_by="t", reason="r")
    real = es.policy_safety_identity()
    monkeypatch.setattr(es, "policy_safety_identity",
                        lambda root=None: {**real, "policy_safety_sha256": "sha256:" + "1" * 64})
    with pytest.raises(ToolError) as exc:
        es.record_from_approved(content, status_now=paper, approval_id="approval_p",
                                action_fingerprint="sha256:p", now=NOW)
    assert exc.value.reason_code == es.STAGE_POLICY_CHANGED_SINCE_ASK


def test_a_hand_written_transition_that_breaks_the_ladder_reads_read_only(tmp_path):
    """A record whose transition does not match its stages (a CLIMB of two rungs, a BOOTSTRAP at
    LIVE) reads READ_ONLY even with a valid self-hash and a witness."""
    from runtime.read_only_kernel import integrity

    approvals = _Approvals()
    record = es.record_from_approved(
        es.plan_transition(es.resolve_execution_stage(tmp_path, now=NOW, approval_store=approvals),
                           target="PAPER", now=NOW, registered_by="t", reason="r", attestation="a"),
        status_now=es.resolve_execution_stage(tmp_path, now=NOW, approval_store=approvals),
        approval_id="approval_h", action_fingerprint="sha256:h", now=NOW)
    body = {k: v for k, v in record.items() if k != "record_sha256"}
    body.update({"stage": "LIVE_AUTONOMOUS", "valid_until": LATER})   # BOOTSTRAP straight to LIVE
    body["record_sha256"] = integrity.sha256_record(body)
    approvals.consume("approval_h", body)
    es.write_stage_record(body, tmp_path)
    assert es.resolve_execution_stage(tmp_path, now=NOW, approval_store=approvals).reason_code == es.STAGE_TRANSITION_INVALID


def test_the_safety_fingerprint_ignores_comments_and_moves_on_meaning(tmp_path):
    from pathlib import Path

    from runtime.mvp_runtime import policy_fingerprint as pf

    repo = Path(__file__).resolve().parents[1]
    text = (repo / pf.POLICY_REL).read_text(encoding="utf-8")
    (tmp_path / "governance").mkdir()
    (tmp_path / pf.POLICY_REL).write_text(text + "\n# a comment changes nothing\n", encoding="utf-8")
    assert pf.policy_safety_identity(tmp_path) == pf.policy_safety_identity(repo)
    (tmp_path / pf.POLICY_REL).write_text(text.replace("      - recovery\n", "      - recovery\n      - halt_trading\n", 1),
                                          encoding="utf-8")
    assert pf.policy_safety_identity(tmp_path)["policy_safety_sha256"] != pf.policy_safety_identity(repo)["policy_safety_sha256"]


# --- the ask, the stamp, and what PR1a does NOT do -----------------------------------------------

def _content(target="PAPER", **kw):
    status = es.StageStatus(stage="READ_ONLY", valid=False, reason_code=es.STAGE_RECORD_MISSING)
    return es.plan_transition(status, target=target, now=NOW, registered_by="thomas", reason="r",
                              attestation="paper ledger", **kw)


def test_the_ask_is_red_only_for_a_live_target_and_says_it_is_not_enforced_yet():
    from runtime.mvp_runtime import permission

    captured = {}

    def fake_build(bound, **kw):
        captured.update(kw)
        return {"ok": True}

    import unittest.mock as mock

    with mock.patch.object(permission, "build_permission_decision", fake_build):
        permission.build_execution_stage_permission_decision({}, content=_content(), now=NOW)
        assert captured["action"].risk_level == "ORANGE"
        assert captured["permission_scope"] == permission.EXECUTION_STAGE_PERMISSION_SCOPE
        assert "NOT ENFORCED YET" in captured["action"].risk_reason
        assert "Closing a position is never gated" in captured["action"].risk_reason
        live = {**_content(), "to_stage": "LIVE_CANARY", "transition": es.T_CLIMB, "from_stage": "SIGNED_TESTNET"}
        permission.build_execution_stage_permission_decision({}, content=live, now=NOW)
        assert captured["action"].risk_level == "RED"
        assert captured["action"].target_ref == "execution_stage:binance_futures:LIVE_CANARY"


def test_an_ask_with_missing_content_is_refused():
    from runtime.mvp_runtime import permission
    from runtime.mvp_runtime.errors import PlannerBlocked

    content = _content()
    content.pop("stage_ref")
    with pytest.raises(PlannerBlocked):
        permission.build_execution_stage_permission_decision({}, content=content, now=NOW)


def test_nothing_on_the_entry_path_reads_the_stage_while_it_is_not_enforced():
    """PR1a records and reports. The flag the texts read (`STAGE_ENFORCED`) and the code must agree:
    while it is False no entry-decision module may import the stage, and PR1b flips both together."""
    import ast
    from pathlib import Path

    assert es.STAGE_ENFORCED is False
    crypto = Path(es.__file__).resolve().parent
    for name in ("live_entry.py", "live_order.py", "live_leg.py", "live_execution.py"):
        tree = ast.parse((crypto / name).read_text(encoding="utf-8"))
        imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert not any(m and m.endswith("execution_stage") for m in imported), name


def test_the_gated_leg_stamps_the_stage_it_read_and_a_closed_gate_stamps_nothing(tmp_path, monkeypatch):
    from runtime.mvp_runtime.crypto import live_route
    from runtime.mvp_runtime.crypto.account import AccountSnapshot

    closed = live_route.run_live_leg(live_routable_strategy_ids=set(), route=None, feature_row={},
                                     verdict={}, symbol="BTCUSDT", collector=object(), now=NOW, root=tmp_path)
    assert closed["execution_stage"] is None

    monkeypatch.setenv("MVP_LIVE_TRADING", "real")
    snapshot = AccountSnapshot(asset="USDT", wallet_balance=1.0, margin_balance=1.0, available_balance=1.0,
                               unrealized_pnl=0.0, positions=[], realized_windows={}, source="fake", collected_at=NOW)
    monkeypatch.setattr(live_route, "read_account", lambda **kw: (snapshot, {}))
    monkeypatch.setattr(live_route, "plan_live_entry",
                        lambda plan, **kw: {"status": "REFUSED", "ready": False, "reasons": ["stub"]})
    opened = live_route.run_live_leg(live_routable_strategy_ids=set(), route=None, feature_row={"timestamp": NOW},
                                     verdict={}, symbol="BTCUSDT", collector=object(), now=NOW, root=tmp_path)
    assert opened["execution_stage"]["stage"] == "READ_ONLY"
    assert opened["execution_stage"]["reason_code"] == es.STAGE_RECORD_MISSING


def test_the_request_text_for_a_stage_ask_says_how_it_is_spent_and_reversed():
    from runtime.mvp_runtime import approval

    ask = {
        "approval_id": "approval_x", "task_id": "task_x", "validity": {"expires_at": LATER},
        "action_fingerprint": "sha256:x",
        "approved_action_snapshot": {"action_type": "crypto.execution_stage.transition",
                                     "permission_scope": "RUNTIME_GOVERNANCE",
                                     "target_ref": "execution_stage:binance_futures:LIVE_AUTONOMOUS"},
    }
    text = approval.format_request(ask)
    assert "--confirm --approval-id" in text
    assert "승인 없이 즉시" in text and "청산은 어느 단계에서도 막히지 않습니다" in text
    assert "그 이후의 손익이 곧 비용" in text        # a LIVE target is priced as one
    paper = {**ask, "approved_action_snapshot": {**ask["approved_action_snapshot"],
                                                 "target_ref": "execution_stage:binance_futures:PAPER"}}
    assert "예상 비용: 없음" in approval.format_request(paper)
