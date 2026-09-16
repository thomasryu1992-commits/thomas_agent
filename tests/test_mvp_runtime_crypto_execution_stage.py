"""The execution stage record (PR1a; Thomas decisions 1, 4, 8, 9, 2026-09-15).

What is pinned here: the ladder without a canary rung and without expiry (Thomas 2026-07-29 and
2026-07-28/08-10); BOOTSTRAP at SHADOW/PAPER only, one rung up from a binding record, REBIND only
after a policy change, any rung down without approval; every way a record fails to bind and reads
READ_ONLY; the witness — a Thomas-verified CONSUMED approval whose content the record carries, and
the approval a demotion descends from; and the refusal a climb to LIVE carries before the signed
testnet evidence path exists (PR1d)."""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime import approval as _approval  # noqa: F401  (puts scripts/lib on the path)
from runtime.mvp_runtime.crypto import execution_stage as es
from runtime.mvp_runtime.errors import ToolError
from runtime.read_only_kernel import integrity

from lib.action_fingerprint import FINGERPRINT_SCHEMA_VERSION, compute_action_fingerprint  # noqa: E402

NOW = "2026-09-15T08:00:00Z"
LATER = "2026-09-16T08:00:00Z"


class _Approvals:
    """An approval ledger holding the stage grants the tests make, shaped like the real records the
    witness reads: Thomas-verified, a snapshot that fingerprints, the content in normalized_parameters."""

    def __init__(self):
        self.records: dict[str, dict] = {}

    def grant(self, approval_id, content):
        snapshot = {
            "schema_version": FINGERPRINT_SCHEMA_VERSION, "permission_scope": "RUNTIME_GOVERNANCE",
            "data_scope": ["crypto.execution_stage"],
            "target_ref": f"execution_stage:{content['venue']}:{content['to_stage']}",
            "normalized_parameters": dict(content),
        }
        self.records[approval_id] = {
            "approval_id": approval_id, "status": "APPROVED",
            "action_fingerprint": compute_action_fingerprint(snapshot), "approved_action_snapshot": snapshot,
            "approver": {"approved_by": "Thomas", "verification_status": "VERIFIED",
                         "identity_verification_method": "telegram_private_control_channel"},
            "consumption": {"consumption_ref": None, "consumed_at": None},
        }
        return self.records[approval_id]

    def consume(self, approval_id, record):
        self.records[approval_id].update(status="CONSUMED", consumption={
            "consumption_ref": es.consumption_ref(record["stage_id"]), "consumed_at": record["effective_from"]})

    def get(self, approval_id):
        return self.records.get(approval_id)


def _resolve(tmp_path, approvals, now=NOW):
    return es.resolve_execution_stage(tmp_path, now=now, approval_store=approvals)


def _register(tmp_path, approvals, status, target, *, approval_id, now=NOW, evidence_root=None, **kw):
    content = es.plan_transition(status, target=target, registered_by="thomas", reason="test",
                                 evidence_root=evidence_root, **kw)
    granted = approvals.grant(approval_id, content)
    record = es.record_from_approved(content, status_now=status, approval_id=approval_id,
                                     action_fingerprint=granted["action_fingerprint"], now=now,
                                     evidence_root=evidence_root)
    approvals.consume(approval_id, record)
    es.write_stage_record(record, tmp_path)
    return _resolve(tmp_path, approvals, now)


def _paper(tmp_path, approvals):
    return _register(tmp_path, approvals, _resolve(tmp_path, approvals), "PAPER", approval_id="approval_boot",
                     attestation="paper ledger")


def _testnet(tmp_path, approvals):
    return _register(tmp_path, approvals, _paper(tmp_path, approvals), "SIGNED_TESTNET", approval_id="approval_tn")


def _rewrite(tmp_path, **fields):
    """Edit the record on disk and recompute its self-hash — what anyone who can write the state
    directory can do (audit FO-12)."""
    path = es.stage_path(tmp_path)
    body = {k: v for k, v in json.loads(path.read_text(encoding="utf-8")).items() if k != "record_sha256"}
    body.update(fields)
    body["record_sha256"] = integrity.sha256_record(body)
    path.write_text(json.dumps(body), encoding="utf-8")
    return body


def _policy_moved(monkeypatch, **change):
    real = es.policy_safety_identity()
    monkeypatch.setattr(es, "policy_safety_identity", lambda root=None: {**real, **change})


# --- the ladder ---------------------------------------------------------------------------------

def test_the_ladder_has_no_canary_rung():
    assert es.LADDER == ("READ_ONLY", "SHADOW", "PAPER", "SIGNED_TESTNET", "LIVE_AUTONOMOUS", "LIVE_SCALED")
    assert {es.required_stage(p) for p in (es.PURPOSE_PROBE, es.PURPOSE_AUTONOMOUS, es.PURPOSE_LIVE_ARM)} == {
        "LIVE_AUTONOMOUS"}


def test_no_record_reads_read_only(tmp_path):
    status = _resolve(tmp_path, _Approvals())
    assert (status.stage, status.valid, status.reason_code, status.record_present) == (
        "READ_ONLY", False, es.STAGE_RECORD_MISSING, False)
    assert not status.allows(es.PURPOSE_PROBE)


def test_bootstrap_at_paper_binds_with_its_approval(tmp_path):
    status = _paper(tmp_path, _Approvals())
    assert (status.stage, status.valid, status.approval_id) == ("PAPER", True, "approval_boot")
    assert not status.allows(es.PURPOSE_AUTONOMOUS)


@pytest.mark.parametrize("target", ["SIGNED_TESTNET", "LIVE_AUTONOMOUS", "READ_ONLY"])
def test_the_first_record_is_only_shadow_or_paper(tmp_path, target):
    with pytest.raises(ToolError) as exc:
        es.plan_transition(_resolve(tmp_path, _Approvals()), target=target, registered_by="t", reason="r",
                           attestation="a")
    assert exc.value.reason_code == es.STAGE_BOOTSTRAP_ONLY_SHADOW_OR_PAPER


def test_the_first_record_names_its_evidence(tmp_path):
    with pytest.raises(ToolError) as exc:
        es.plan_transition(_resolve(tmp_path, _Approvals()), target="PAPER", registered_by="t", reason="r")
    assert exc.value.reason_code == es.STAGE_ATTESTATION_REQUIRED


def test_climbing_is_one_rung_at_a_time(tmp_path):
    approvals = _Approvals()
    paper = _paper(tmp_path, approvals)
    for target in ("LIVE_AUTONOMOUS", "LIVE_SCALED"):
        with pytest.raises(ToolError) as exc:
            es.plan_transition(paper, target=target, registered_by="t", reason="r")
        assert exc.value.reason_code == es.STAGE_SKIP_REFUSED
    with pytest.raises(ToolError) as exc:
        es.plan_transition(paper, target="PAPER", registered_by="t", reason="r")
    assert exc.value.reason_code == es.STAGE_ALREADY_BINDS
    testnet = _register(tmp_path, approvals, paper, "SIGNED_TESTNET", approval_id="approval_tn")
    assert (testnet.stage, testnet.valid) == ("SIGNED_TESTNET", True)


def test_live_waits_for_signed_testnet_evidence(tmp_path):
    """Decision 2: a reconciled signed testnet order. The path does not exist yet (PR1d), so the
    climb refuses by name rather than proceeding on anything else — and no canary count stands in."""
    approvals = _Approvals()
    testnet = _testnet(tmp_path, approvals)
    with pytest.raises(ToolError) as exc:
        es.plan_transition(testnet, target="LIVE_AUTONOMOUS", registered_by="t", reason="r", attestation="4 canaries")
    assert exc.value.reason_code == es.STAGE_SIGNED_TESTNET_EVIDENCE_REQUIRED


def test_no_stage_expires(tmp_path):
    approvals = _Approvals()
    _testnet(tmp_path, approvals)
    years_later = _resolve(tmp_path, approvals, now="2031-01-01T00:00:00Z")
    assert (years_later.stage, years_later.valid) == ("SIGNED_TESTNET", True)
    assert "valid_until" not in json.loads(es.stage_path(tmp_path).read_text(encoding="utf-8"))


def test_a_lower_target_is_a_demotion_not_an_ask(tmp_path):
    approvals = _Approvals()
    with pytest.raises(ToolError) as exc:
        es.plan_transition(_testnet(tmp_path, approvals), target="PAPER", registered_by="t", reason="r")
    assert exc.value.reason_code == es.STAGE_USE_DEMOTE


# --- the witness ---------------------------------------------------------------------------------

def test_a_record_the_door_did_not_spend_an_approval_for_reads_read_only(tmp_path):
    _paper(tmp_path, _Approvals())
    status = _resolve(tmp_path, _Approvals())   # empty ledger
    assert (status.stage, status.reason_code, status.recorded_stage) == (
        "READ_ONLY", es.STAGE_APPROVAL_NOT_CONSUMED, "PAPER")


def test_an_unreadable_approval_ledger_is_not_a_witness(tmp_path):
    _paper(tmp_path, _Approvals())

    class _Broken:
        def get(self, approval_id):
            raise OSError("disk")

    assert _resolve(tmp_path, _Broken()).reason_code == es.STAGE_APPROVAL_UNREADABLE


@pytest.mark.parametrize("approver", [
    {"approved_by": "assistant"},
    {"verification_status": "NOT_VERIFIED"},
    {"identity_verification_method": "console"},
])
def test_an_approval_not_decided_by_verified_thomas_is_not_a_witness(tmp_path, approver):
    approvals = _Approvals()
    _paper(tmp_path, approvals)
    approvals.records["approval_boot"]["approver"].update(approver)
    assert _resolve(tmp_path, approvals).reason_code == es.STAGE_APPROVAL_NOT_CONSUMED


def test_a_spent_approval_witnesses_only_the_content_it_was_granted_for(tmp_path):
    """Review of #872: one CONSUMED PAPER grant used to witness any record that copied its id,
    fingerprint and stage_id. The record must now carry exactly the approved content."""
    approvals = _Approvals()
    _paper(tmp_path, approvals)
    _rewrite(tmp_path, stage="SIGNED_TESTNET", previous_stage="PAPER", transition="CLIMB",
             evidence={"attestation": "paper ledger"})
    assert _resolve(tmp_path, approvals).reason_code in (es.STAGE_WITNESS_MISMATCH, es.STAGE_TRANSITION_INVALID)
    _paper(tmp_path / "b", approvals := _Approvals())
    _rewrite(tmp_path / "b", reason="edited after the grant")
    assert _resolve(tmp_path / "b", approvals).reason_code == es.STAGE_WITNESS_MISMATCH


def test_an_approval_whose_snapshot_was_edited_is_not_a_witness(tmp_path):
    approvals = _Approvals()
    _paper(tmp_path, approvals)
    approvals.records["approval_boot"]["approved_action_snapshot"]["normalized_parameters"]["to_stage"] = "LIVE_SCALED"
    assert _resolve(tmp_path, approvals).reason_code == es.STAGE_WITNESS_MISMATCH


def test_a_forged_demotion_cannot_mint_a_live_stage(tmp_path):
    """Review of #872 (all three lenses): a hand-written DEMOTE needed no witness and read valid at
    LIVE_AUTONOMOUS with an empty ledger."""
    approvals = _Approvals()
    paper = _paper(tmp_path, approvals)
    # No witness at all: a DEMOTE above READ_ONLY must carry one.
    _rewrite(tmp_path, stage="LIVE_AUTONOMOUS", previous_stage="LIVE_SCALED", transition="DEMOTE",
             approval_id=None, action_fingerprint=None, witness_stage_id=None)
    assert _resolve(tmp_path, approvals).reason_code == es.STAGE_TRANSITION_INVALID
    # Citing the real PAPER grant: a demotion sits strictly below the stage its witness approved.
    _rewrite(tmp_path, stage="LIVE_AUTONOMOUS", previous_stage="LIVE_SCALED", transition="DEMOTE",
             approval_id="approval_boot", action_fingerprint=approvals.records["approval_boot"]["action_fingerprint"],
             witness_stage_id=paper.stage_id, stage_id="stage_" + "a" * 20)
    assert _resolve(tmp_path, approvals).reason_code == es.STAGE_WITNESS_MISMATCH
    # Citing a grant that does not exist.
    _rewrite(tmp_path, approval_id="approval_fake")
    assert _resolve(tmp_path, approvals).reason_code == es.STAGE_APPROVAL_NOT_CONSUMED


def test_a_hand_written_transition_that_breaks_the_ladder_reads_read_only(tmp_path):
    approvals = _Approvals()
    _paper(tmp_path, approvals)
    _rewrite(tmp_path, stage="LIVE_AUTONOMOUS")   # BOOTSTRAP straight to LIVE
    assert _resolve(tmp_path, approvals).reason_code == es.STAGE_TRANSITION_INVALID


# --- reading a record ------------------------------------------------------------------------------

def test_a_tampered_record_reads_read_only(tmp_path):
    approvals = _Approvals()
    _paper(tmp_path, approvals)
    path = es.stage_path(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["stage"] = "LIVE_AUTONOMOUS"
    path.write_text(json.dumps(data), encoding="utf-8")
    status = _resolve(tmp_path, approvals)
    assert (status.reason_code, status.record_present) == (es.STAGE_RECORD_TAMPERED, True)


@pytest.mark.parametrize("payload", [
    "{not json",
    '{"stage": NaN}',
    '{"api_secret": "x", "record_sha256": "sha256:0"}',
    "[" * 5000 + "]" * 5000,
])
def test_a_malformed_record_reads_read_only_and_never_raises(tmp_path, payload):
    """Review of #872: a NaN, a secret-shaped key or deep nesting raised out of the read, and the live
    leg — which reads the stage before it settles and protects — halted as an INCIDENT."""
    es.stage_path(tmp_path).parent.mkdir(parents=True)
    es.stage_path(tmp_path).write_text(payload, encoding="utf-8")
    status = _resolve(tmp_path, _Approvals())
    assert (status.stage, status.valid) == ("READ_ONLY", False)
    assert status.reason_code in (es.STAGE_RECORD_UNREADABLE, es.STAGE_RECORD_TAMPERED)


def test_resolve_never_raises_whatever_goes_wrong_inside(tmp_path, monkeypatch):
    _paper(tmp_path, _Approvals())
    monkeypatch.setattr(es, "_transition_consistent", lambda record: 1 / 0)
    assert _resolve(tmp_path, _Approvals()).reason_code == es.STAGE_RECORD_UNREADABLE


def test_a_policy_change_unbinds_the_record(tmp_path, monkeypatch):
    """Decision 4: bound to the policy version AND the safety semantic fingerprint."""
    approvals = _Approvals()
    _paper(tmp_path, approvals)
    _policy_moved(monkeypatch, policy_safety_sha256="sha256:" + "0" * 64)
    assert _resolve(tmp_path, approvals).reason_code == es.STAGE_POLICY_SAFETY_CHANGED
    _policy_moved(monkeypatch, policy_version="9.9.9")
    assert _resolve(tmp_path, approvals).reason_code == es.STAGE_POLICY_VERSION_CHANGED
    monkeypatch.setattr(es, "policy_safety_identity", lambda root=None: None)
    assert _resolve(tmp_path, approvals).reason_code == es.STAGE_POLICY_UNREADABLE


# --- REBIND and BOOTSTRAP over a record that does not bind ----------------------------------------

def test_rebind_is_offered_only_after_a_policy_change(tmp_path, monkeypatch):
    approvals = _Approvals()
    _testnet(tmp_path, approvals)
    _policy_moved(monkeypatch, policy_version="1.5.1")
    moved = _resolve(tmp_path, approvals)
    content = es.plan_transition(moved, target="SIGNED_TESTNET", registered_by="t", reason="policy 1.5.1")
    assert content["transition"] == es.T_REBIND
    assert content["evidence"] == {"replaced_reason_code": es.STAGE_POLICY_VERSION_CHANGED}
    with pytest.raises(ToolError) as exc:
        es.plan_transition(moved, target="LIVE_AUTONOMOUS", registered_by="t", reason="r")
    assert exc.value.reason_code == es.STAGE_REBIND_FIRST
    rebound = _register(tmp_path, approvals, moved, "SIGNED_TESTNET", approval_id="approval_rb")
    assert (rebound.stage, rebound.valid) == ("SIGNED_TESTNET", True)


def test_a_record_that_was_never_approved_cannot_be_rebound_into_a_stage(tmp_path):
    """Review of #872: REBIND used to be offered over a forged record, bypassing the ladder."""
    _testnet(tmp_path, _Approvals())
    forged = _resolve(tmp_path, _Approvals())   # APPROVAL_NOT_CONSUMED
    for target in ("SIGNED_TESTNET", "LIVE_AUTONOMOUS"):
        with pytest.raises(ToolError) as exc:
            es.plan_transition(forged, target=target, registered_by="t", reason="r")
        assert exc.value.reason_code == es.STAGE_BOOTSTRAP_ONLY_SHADOW_OR_PAPER


def test_a_bootstrap_over_a_record_that_does_not_bind_names_it(tmp_path):
    _testnet(tmp_path, _Approvals())
    forged = _resolve(tmp_path, _Approvals())
    content = es.plan_transition(forged, target="PAPER", registered_by="t", reason="r", attestation="paper ledger")
    assert content["transition"] == es.T_BOOTSTRAP and content["from_stage"] == "NONE"
    assert content["evidence"]["replaced_stage"] == "SIGNED_TESTNET"
    assert content["evidence"]["replaced_reason_code"] == es.STAGE_APPROVAL_NOT_CONSUMED


# --- DEMOTE --------------------------------------------------------------------------------------

def test_demotion_to_read_only_needs_no_approval_and_binds_at_once(tmp_path):
    approvals = _Approvals()
    testnet = _testnet(tmp_path, approvals)
    record = es.demote_record(testnet, target="READ_ONLY", registered_by="thomas", reason="stop", now=NOW)
    es.write_stage_record(record, tmp_path)
    status = _resolve(tmp_path, _Approvals())
    assert (status.stage, status.valid, status.recorded_stage) == ("READ_ONLY", True, "READ_ONLY")
    assert record["approval_id"] is None and record["previous_stage"] == "SIGNED_TESTNET"


def test_demotion_to_read_only_works_from_a_record_that_cannot_be_read(tmp_path):
    es.stage_path(tmp_path).parent.mkdir(parents=True)
    es.stage_path(tmp_path).write_text("{not json", encoding="utf-8")
    broken = _resolve(tmp_path, _Approvals())
    record = es.demote_record(broken, target="READ_ONLY", registered_by="t", reason="clear", now=NOW)
    es.write_stage_record(record, tmp_path)
    assert (record["previous_stage"], record["evidence"]) == (None, {"replaced_reason_code": es.STAGE_RECORD_UNREADABLE})
    assert _resolve(tmp_path, _Approvals()).valid is True


def test_a_demotion_to_a_live_or_paper_rung_carries_its_witness_forward(tmp_path, monkeypatch):
    approvals = _Approvals()
    testnet = _testnet(tmp_path, approvals)
    record = es.demote_record(testnet, target="SHADOW", registered_by="t", reason="step back", now=LATER)
    es.write_stage_record(record, tmp_path)
    assert (record["approval_id"], record["witness_stage_id"]) == ("approval_tn", testnet.stage_id)
    shadow = _resolve(tmp_path, approvals, now=LATER)
    assert (shadow.stage, shadow.valid) == ("SHADOW", True)
    assert _resolve(tmp_path, _Approvals(), now=LATER).reason_code == es.STAGE_APPROVAL_NOT_CONSUMED
    # A demotion keeps the policy its witness bound: a policy change still unbinds it.
    _policy_moved(monkeypatch, policy_version="1.5.1")
    assert _resolve(tmp_path, approvals, now=LATER).reason_code == es.STAGE_POLICY_VERSION_CHANGED


def test_a_demotion_cannot_launder_a_record_that_does_not_bind(tmp_path, monkeypatch):
    """Review of #872: --demote used to turn a forged or policy-unbound record into a valid lower one."""
    approvals = _Approvals()
    _testnet(tmp_path, approvals)
    forged = _resolve(tmp_path, _Approvals())
    _policy_moved(monkeypatch, policy_version="1.5.1")
    unbound = _resolve(tmp_path, approvals)
    for status in (forged, unbound):
        with pytest.raises(ToolError) as exc:
            es.demote_record(status, target="PAPER", registered_by="t", reason="r", now=NOW)
        assert exc.value.reason_code == es.STAGE_DEMOTE_FROM_UNBOUND
        assert es.demote_record(status, target="READ_ONLY", registered_by="t", reason="r", now=NOW)["stage"] == "READ_ONLY"


def test_a_demotion_must_be_lower_and_needs_something_to_demote(tmp_path):
    approvals = _Approvals()
    paper = _paper(tmp_path, approvals)
    with pytest.raises(ToolError) as exc:
        es.demote_record(paper, target="SIGNED_TESTNET", registered_by="t", reason="r", now=NOW)
    assert exc.value.reason_code == es.STAGE_NOT_LOWER
    nothing = _resolve(tmp_path / "empty", approvals)
    with pytest.raises(ToolError) as exc:
        es.demote_record(nothing, target="READ_ONLY", registered_by="t", reason="r", now=NOW)
    assert exc.value.reason_code == es.STAGE_NOTHING_TO_DEMOTE


def test_after_a_demotion_to_read_only_the_way_back_is_one_bootstrap(tmp_path):
    approvals = _Approvals()
    es.write_stage_record(es.demote_record(_paper(tmp_path, approvals), target="READ_ONLY", registered_by="t",
                                           reason="r", now=NOW), tmp_path)
    read_only = _resolve(tmp_path, approvals)
    for target in ("SIGNED_TESTNET", "LIVE_AUTONOMOUS"):
        with pytest.raises(ToolError) as exc:
            es.plan_transition(read_only, target=target, registered_by="t", reason="r", attestation="a")
        assert exc.value.reason_code == es.STAGE_BOOTSTRAP_ONLY_SHADOW_OR_PAPER
    back = _register(tmp_path, approvals, read_only, "PAPER", approval_id="approval_back", attestation="paper ledger")
    assert (back.stage, back.valid) == ("PAPER", True)


# --- the spend re-checks ---------------------------------------------------------------------------

def test_an_approval_asked_against_one_record_does_not_apply_over_another(tmp_path):
    approvals = _Approvals()
    paper = _paper(tmp_path, approvals)
    content = es.plan_transition(paper, target="SIGNED_TESTNET", registered_by="t", reason="r")
    es.write_stage_record(es.demote_record(paper, target="SHADOW", registered_by="t", reason="r", now=NOW), tmp_path)
    with pytest.raises(ToolError) as exc:
        es.record_from_approved(content, status_now=_resolve(tmp_path, approvals), approval_id="approval_late",
                                action_fingerprint="sha256:late", now=NOW)
    assert exc.value.reason_code == es.STAGE_CHANGED


def test_an_approval_asked_under_one_policy_does_not_apply_under_another(tmp_path, monkeypatch):
    approvals = _Approvals()
    paper = _paper(tmp_path, approvals)
    content = es.plan_transition(paper, target="SIGNED_TESTNET", registered_by="t", reason="r")
    _policy_moved(monkeypatch, policy_safety_sha256="sha256:" + "1" * 64)
    with pytest.raises(ToolError) as exc:
        es.record_from_approved(content, status_now=paper, approval_id="approval_p", action_fingerprint="sha256:p", now=NOW)
    assert exc.value.reason_code == es.STAGE_POLICY_CHANGED_SINCE_ASK


def test_the_spend_replans_so_content_the_ladder_refuses_is_never_written(tmp_path):
    approvals = _Approvals()
    testnet = _testnet(tmp_path, approvals)
    shaped = {**es.plan_transition(_resolve(tmp_path / "x", approvals), target="PAPER", registered_by="t",
                                   reason="r", attestation="a"),
              "stage_ref": es.stage_ref(testnet), "transition": es.T_CLIMB, "from_stage": "SIGNED_TESTNET",
              "to_stage": "LIVE_AUTONOMOUS"}
    with pytest.raises(ToolError) as exc:
        es.record_from_approved(shaped, status_now=testnet, approval_id="approval_s", action_fingerprint="sha256:s", now=NOW)
    assert exc.value.reason_code == es.STAGE_SIGNED_TESTNET_EVIDENCE_REQUIRED


# --- the policy fingerprint ------------------------------------------------------------------------

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


def test_the_cached_fingerprint_follows_the_content_not_the_clock(tmp_path):
    """The parse is cached for the live leg; a same-size rewrite inside one filesystem clock tick must
    still read as the new policy."""
    from pathlib import Path

    from runtime.mvp_runtime import policy_fingerprint as pf

    text = (Path(__file__).resolve().parents[1] / pf.POLICY_REL).read_text(encoding="utf-8")
    (tmp_path / "governance").mkdir()
    (tmp_path / pf.POLICY_REL).write_text(text, encoding="utf-8")
    first = pf.policy_safety_identity(tmp_path)
    marker = "policy_version: "
    at = text.index(marker) + len(marker)
    bumped = text[:at] + ("9" if text[at] != "9" else "8") + text[at + 1:]
    (tmp_path / pf.POLICY_REL).write_text(bumped, encoding="utf-8")
    assert pf.policy_safety_identity(tmp_path)["policy_version"] != first["policy_version"]
    first["policy_version"] = "mutated"
    assert pf.policy_safety_identity(tmp_path)["policy_version"] != "mutated"   # callers get copies


# --- the ask, the stamp, and what PR1a does NOT do -----------------------------------------------

def _content(target="PAPER", **kw):
    status = es.StageStatus(stage="READ_ONLY", valid=False, reason_code=es.STAGE_RECORD_MISSING)
    return es.plan_transition(status, target=target, registered_by="thomas", reason="r",
                              attestation="paper ledger", **kw)


def test_the_ask_is_red_only_for_a_live_target_and_says_what_it_is():
    import unittest.mock as mock

    from runtime.mvp_runtime import permission

    captured = {}

    def fake_build(bound, **kw):
        captured.update(kw)
        return {"ok": True}

    with mock.patch.object(permission, "build_permission_decision", fake_build):
        permission.build_execution_stage_permission_decision({}, content=_content(), now=NOW)
        assert captured["action"].risk_level == "ORANGE"
        assert captured["permission_scope"] == permission.EXECUTION_STAGE_PERMISSION_SCOPE
        assert "The entry doors read this" in captured["action"].risk_reason
        assert "Closing a position is never gated" in captured["action"].risk_reason
        assert "no stage expires" in captured["action"].risk_reason
        live = {**_content(), "to_stage": "LIVE_AUTONOMOUS", "transition": es.T_CLIMB, "from_stage": "SIGNED_TESTNET"}
        permission.build_execution_stage_permission_decision({}, content=live, now=NOW)
        assert captured["action"].risk_level == "RED"
        assert captured["action"].target_ref == "execution_stage:binance_futures:LIVE_AUTONOMOUS"
        assert "canary" not in captured["action"].risk_reason
        replacing = {**_content(), "evidence": {"attestation": "a", "replaced_stage": "SIGNED_TESTNET",
                                                "replaced_reason_code": es.STAGE_APPROVAL_NOT_CONSUMED}}
        permission.build_execution_stage_permission_decision({}, content=replacing, now=NOW)
        assert "replaces a SIGNED_TESTNET record that does not bind" in captured["action"].risk_reason


def test_an_ask_with_missing_content_is_refused():
    from runtime.mvp_runtime import permission
    from runtime.mvp_runtime.errors import PlannerBlocked

    content = _content()
    content.pop("stage_ref")
    with pytest.raises(PlannerBlocked):
        permission.build_execution_stage_permission_decision({}, content=content, now=NOW)


def test_every_entry_door_is_judged_against_the_stage():
    """PR1b flipped `STAGE_ENFORCED`, so this is the positive sweep that replaced PR1a's negative
    pin ("no entry-decision module imports the stage"). What it holds: the one chokepoint asks for
    the stage and cannot be called without it, every caller of that chokepoint passes it, and the
    close path is not one of them."""
    import ast
    import inspect
    from pathlib import Path

    from runtime.mvp_runtime.crypto import live_order

    assert es.STAGE_ENFORCED is True
    entry = inspect.signature(live_order.evaluate_live_order_guard).parameters["execution_stage"]
    assert entry.default is inspect.Parameter.empty, "a forgotten stage must not authorize an entry"
    assert entry.kind is inspect.Parameter.KEYWORD_ONLY
    assert "execution_stage" not in inspect.signature(live_order.evaluate_live_close_guard).parameters, (
        "the close guard must never read the stage: a demotion cannot be allowed to trap a position"
    )

    crypto = Path(es.__file__).resolve().parent
    repo = crypto.parents[2]
    # Each door, and the name of the local it must pass: the board's dry-run and the leg have to
    # judge against the very stage they report, or the board says one thing and the door does
    # another. The probe resolves its own, named `stage` there too.
    callers = {
        crypto / "live_entry.py": "execution_stage",
        crypto / "live_readiness.py": "stage",
        repo / "scripts" / "run_slippage_probe.py": "stage",
    }
    seen = 0
    for path, local in callers.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "evaluate_live_order_guard"):
                seen += 1
                passed = [kw for kw in node.keywords if kw.arg == "execution_stage"]
                assert passed, path.name
                assert getattr(passed[0].value, "id", None) == local, (
                    f"{path.name} must pass the stage it read ({local}), not a second or a made-up one"
                )
    assert seen == len(callers), f"expected one guard call per entry door, found {seen}"

    # And the leg that feeds `plan_live_entry` passes the stage it stamped, not a second read.
    route = ast.parse((crypto / "live_route.py").read_text(encoding="utf-8"))
    plan_calls = [n for n in ast.walk(route)
                  if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "plan_live_entry"]
    assert plan_calls and all(
        any(kw.arg == "execution_stage" and getattr(kw.value, "id", None) == "stage" for kw in call.keywords)
        for call in plan_calls
    )


def test_a_stage_below_the_rung_refuses_a_live_entry_and_never_a_close():
    """The property the ladder exists for, at the guard itself."""
    from runtime.mvp_runtime.crypto.live_order import (
        LIVE_CONFIRMATION_PHRASE, LiveOrderLimits, evaluate_live_close_guard, evaluate_live_order_guard,
    )

    limits = LiveOrderLimits(
        max_order_notional_usdt=60.0, absolute_max_notional_usdt=200.0, max_daily_order_count=2,
        max_open_notional_usdt=120.0, daily_loss_limit_usdt=20.0, confirmation=LIVE_CONFIRMATION_PHRASE,
    )
    intent = {"status": "ORDER_INTENT_CREATED", "symbol": "BTCUSDT", "direction": "LONG",
              "quantity": 0.001, "order_notional_usdt": 55.0, "reduce_only": False,
              "connectivity_test": False}
    facts = dict(gate_open=True, runtime_active=True, daily_loss_breached=False, submitted_today=0,
                 current_open_notional_usdt=0.0, budget_registered=True, allowed_symbols=["BTCUSDT"],
                 limits=limits)
    for stage in ("READ_ONLY", "SHADOW", "PAPER", "SIGNED_TESTNET"):
        status = es.StageStatus(stage=stage, valid=True, reason_code=None, recorded_stage=stage)
        verdict = evaluate_live_order_guard(intent, execution_stage=status, **facts)
        assert verdict["approved"] is False
        assert any("execution stage" in block for block in verdict["blocks"]), stage
        assert verdict["execution_stage"] == stage
        # The same stage, and the same limits, still close a position.
        close = evaluate_live_close_guard({**intent, "reduce_only": True}, gate_open=True, limits=limits)
        assert close["approved"] is True
    for stage in ("LIVE_AUTONOMOUS", "LIVE_SCALED"):
        status = es.StageStatus(stage=stage, valid=True, reason_code=None, recorded_stage=stage)
        verdict = evaluate_live_order_guard(intent, execution_stage=status, **facts)
        assert verdict["approved"] is True, verdict["blocks"]
    # A record that does not bind reads READ_ONLY and admits nothing, whatever rung it claims.
    unbound = es.StageStatus(stage="READ_ONLY", valid=False, reason_code=es.STAGE_APPROVAL_NOT_CONSUMED,
                             recorded_stage="LIVE_AUTONOMOUS")
    refused = evaluate_live_order_guard(intent, execution_stage=unbound, **facts)
    assert refused["approved"] is False
    assert any(es.STAGE_APPROVAL_NOT_CONSUMED in block for block in refused["blocks"])


def _gated_leg(tmp_path, monkeypatch):
    from runtime.mvp_runtime.crypto import live_route
    from runtime.mvp_runtime.crypto.account import AccountSnapshot

    monkeypatch.setenv("MVP_LIVE_TRADING", "real")
    snapshot = AccountSnapshot(asset="USDT", wallet_balance=1.0, margin_balance=1.0, available_balance=1.0,
                               unrealized_pnl=0.0, positions=[], realized_windows={}, source="fake", collected_at=NOW)
    monkeypatch.setattr(live_route, "read_account", lambda **kw: (snapshot, {}))
    monkeypatch.setattr(live_route, "plan_live_entry",
                        lambda plan, **kw: {"status": "REFUSED", "ready": False, "reasons": ["stub"]})
    return live_route.run_live_leg(live_routable_strategy_ids=set(), route=None, feature_row={"timestamp": NOW},
                                   verdict={}, symbol="BTCUSDT", collector=object(), now=NOW, root=tmp_path)


def test_the_gated_leg_stamps_the_stage_it_read_and_a_closed_gate_stamps_nothing(tmp_path, monkeypatch):
    from runtime.mvp_runtime.crypto import live_route

    closed = live_route.run_live_leg(live_routable_strategy_ids=set(), route=None, feature_row={},
                                     verdict={}, symbol="BTCUSDT", collector=object(), now=NOW, root=tmp_path)
    assert closed["execution_stage"] is None
    opened = _gated_leg(tmp_path, monkeypatch)
    assert opened["execution_stage"]["stage"] == "READ_ONLY"
    assert opened["execution_stage"]["reason_code"] == es.STAGE_RECORD_MISSING


@pytest.mark.parametrize("payload", ['{"stage": NaN}', '{"api_secret": "x"}'])
def test_a_malformed_stage_file_changes_nothing_the_leg_does(tmp_path, monkeypatch, payload):
    """Observation only: the leg with a malformed record behaves exactly as with no record — same
    status, no halt — and only the stamp differs."""
    baseline = _gated_leg(tmp_path / "none", monkeypatch)
    es.stage_path(tmp_path / "bad").parent.mkdir(parents=True)
    es.stage_path(tmp_path / "bad").write_text(payload, encoding="utf-8")
    broken = _gated_leg(tmp_path / "bad", monkeypatch)
    assert broken["execution_stage"]["reason_code"] in (es.STAGE_RECORD_UNREADABLE, es.STAGE_RECORD_TAMPERED)
    for key in ("live_route_status", "halt", "live_opened"):
        assert broken.get(key) == baseline.get(key), key


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
    assert "만료가 없습니다" in text
    assert "그 이후의 손익이 곧 비용" in text        # a LIVE target is priced as one
    paper = {**ask, "approved_action_snapshot": {**ask["approved_action_snapshot"],
                                                 "target_ref": "execution_stage:binance_futures:PAPER"}}
    assert "예상 비용: 없음" in approval.format_request(paper)


# --- the climb out of SIGNED_TESTNET (PR1d-2, Thomas decisions 2 and 11) --------------------

def _complete_cycle(root, cycle_id="cyc_ok"):
    from runtime.mvp_runtime.crypto import testnet_evidence

    def leg(name):
        return {"leg": name, "algo": name == "SL", "order_type": "STOP_MARKET" if name == "SL" else "LIMIT",
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


def test_the_live_climb_needs_a_completed_signed_testnet_cycle(tmp_path):
    """Thomas decision 2: the rung that can open real positions is entered on evidence the machine
    earned at the venue, not on an attestation."""
    approvals = _Approvals()
    testnet = _testnet(tmp_path, approvals)
    with pytest.raises(ToolError) as unnamed:
        es.plan_transition(testnet, target="LIVE_AUTONOMOUS", registered_by="t", reason="r",
                           evidence_root=tmp_path)
    assert unnamed.value.reason_code == es.STAGE_SIGNED_TESTNET_EVIDENCE_REQUIRED
    with pytest.raises(ToolError) as missing:
        es.plan_transition(testnet, target="LIVE_AUTONOMOUS", registered_by="t", reason="r",
                           testnet_cycle_id="cyc_nope", evidence_root=tmp_path)
    assert missing.value.reason_code == "TESTNET_EVIDENCE_INCOMPLETE"

    record = _complete_cycle(tmp_path)
    content = es.plan_transition(testnet, target="LIVE_AUTONOMOUS", registered_by="t",
                                 reason="the cycle is clean", testnet_cycle_id="cyc_ok",
                                 evidence_root=tmp_path)
    assert content["transition"] == es.T_CLIMB
    # The cycle rides in the content, so the approval signs THIS cycle.
    assert content["evidence"]["testnet_cycle_id"] == "cyc_ok"
    assert content["evidence"]["testnet_cycle_sha256"] == record["record_sha256"]


def test_an_incomplete_cycle_cannot_carry_the_climb(tmp_path):
    from runtime.mvp_runtime.crypto import testnet_evidence

    approvals = _Approvals()
    testnet = _testnet(tmp_path, approvals)
    record = _complete_cycle(tmp_path, cycle_id="cyc_half")
    body = {k: v for k, v in record.items() if k != "record_sha256"}
    body["protective_legs"] = [{**body["protective_legs"][0], "withdrawn": False},
                               body["protective_legs"][1]]
    body["record_sha256"] = integrity.sha256_record(body)
    testnet_evidence.evidence_path(tmp_path).write_text(json.dumps(body) + "\n", encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        es.plan_transition(testnet, target="LIVE_AUTONOMOUS", registered_by="t", reason="r",
                           testnet_cycle_id="cyc_half", evidence_root=tmp_path)
    assert exc.value.reason_code == "TESTNET_EVIDENCE_INCOMPLETE"
    assert "withdrawn" in exc.value.reason


def test_the_rung_is_what_carries_the_condition_not_the_transition_kind(tmp_path, monkeypatch):
    """The hole this PR closed: the refusal used to live inside the CLIMB branch, so the REBIND a
    policy bump forces would have walked around it."""
    approvals = _Approvals()
    _complete_cycle(tmp_path)
    testnet = _testnet(tmp_path, approvals)
    live = _register(tmp_path, approvals, testnet, "LIVE_AUTONOMOUS", approval_id="approval_live",
                     testnet_cycle_id="cyc_ok", evidence_root=tmp_path)
    assert (live.stage, live.valid) == ("LIVE_AUTONOMOUS", True)
    _policy_moved(monkeypatch, policy_version="1.5.1")
    moved = _resolve(tmp_path, approvals)
    assert moved.reason_code == es.STAGE_POLICY_VERSION_CHANGED
    with pytest.raises(ToolError) as exc:
        es.plan_transition(moved, target="LIVE_AUTONOMOUS", registered_by="t", reason="policy 1.5.1",
                           evidence_root=tmp_path)
    assert exc.value.reason_code == es.STAGE_SIGNED_TESTNET_EVIDENCE_REQUIRED
    # Named again, the rebind is allowed — the evidence did not expire, it has to be pointed at.
    rebind = es.plan_transition(moved, target="LIVE_AUTONOMOUS", registered_by="t",
                                reason="policy 1.5.1", testnet_cycle_id="cyc_ok",
                                evidence_root=tmp_path)
    assert rebind["transition"] == es.T_REBIND


def test_a_cycle_is_not_evidence_for_any_other_rung(tmp_path):
    approvals = _Approvals()
    _complete_cycle(tmp_path)
    paper = _paper(tmp_path, approvals)
    with pytest.raises(ToolError) as exc:
        es.plan_transition(paper, target="SIGNED_TESTNET", registered_by="t", reason="r",
                           testnet_cycle_id="cyc_ok", evidence_root=tmp_path)
    assert exc.value.reason_code == es.STAGE_EVIDENCE_NOT_APPLICABLE


def test_a_live_record_that_names_no_cycle_reads_read_only(tmp_path):
    """Structural, so the live leg's read path pays no I/O for it: a record that ARRIVED at the
    rung without naming a cycle cannot be one the door wrote."""
    approvals = _Approvals()
    _complete_cycle(tmp_path)
    testnet = _testnet(tmp_path, approvals)
    _register(tmp_path, approvals, testnet, "LIVE_AUTONOMOUS", approval_id="approval_live",
              testnet_cycle_id="cyc_ok", evidence_root=tmp_path)
    _rewrite(tmp_path, evidence={})
    assert _resolve(tmp_path, approvals).reason_code == es.STAGE_TRANSITION_INVALID


def test_the_read_path_never_touches_the_registry(tmp_path, monkeypatch):
    """`resolve_execution_stage` is what the live leg calls before it settles and protects, and its
    contract is that it never raises. A registry read there would turn a damaged evidence file into
    a machine that silently reads READ_ONLY every cycle."""
    from runtime.mvp_runtime.crypto import testnet_evidence

    approvals = _Approvals()
    _complete_cycle(tmp_path)
    testnet = _testnet(tmp_path, approvals)
    _register(tmp_path, approvals, testnet, "LIVE_AUTONOMOUS", approval_id="approval_live",
              testnet_cycle_id="cyc_ok", evidence_root=tmp_path)
    reads: list[int] = []
    real = testnet_evidence.read_cycles
    monkeypatch.setattr(testnet_evidence, "read_cycles",
                        lambda root=None: (reads.append(1), real(root))[1])
    status = _resolve(tmp_path, approvals)
    assert (status.stage, status.valid) == ("LIVE_AUTONOMOUS", True)
    assert reads == [], "the read path consulted the evidence registry"
    # And a damaged registry does not move the stage.
    testnet_evidence.evidence_path(tmp_path).write_text("{not json\n", encoding="utf-8")
    assert _resolve(tmp_path, approvals).stage == "LIVE_AUTONOMOUS"
