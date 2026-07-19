"""R4 audit/recovery console verbs + the audit-chain verifier.

The runtime has built a hash chain since R2.6 and never once checked it, so most of these
tests are about what the verifier must CATCH. The tamper cases matter more than the happy
path: a verifier that passes everything is worse than none, because it manufactures trust.

Both verbs are read-only and must keep answering while PAUSED/KILLED
(`kill_switch.kill_allows: [read_only_status, audit_read]`) — that is exactly when an
operator needs them.
"""

from __future__ import annotations

import copy
import json

import pytest

from runtime.mvp_runtime import control
from runtime.mvp_runtime.audit import verify_audit_chain
from runtime.mvp_runtime.authority import audit_event_runtime_effect
from runtime.mvp_runtime.control import ControlState, ControlStore
from runtime.mvp_runtime.store import LedgerStore


def _event(*, seq, previous, summary="e", event_id=None):
    """A minimally-shaped audit event whose fingerprint payload agrees with its record and
    whose event_sha256 is genuine — i.e. one link of a real chain."""
    from runtime.read_only_kernel import integrity

    audit_id = event_id or f"audit_{seq:020d}"
    payload = {
        "schema_version": "audit_event_fingerprint_payload.v0.1",
        "audit_event_id": audit_id,
        "trace_id": "trace_t", "task_id": "task_t", "task_revision": 1,
        "core_context_binding_id": "ccb-t",
        "event_type": "OTHER",
        "actor_ref": "system:test",
        "subject_ref": "in_memory:s", "subject_fingerprint": "sha256:" + "0" * 64,
        "event_summary": summary,
        "outcome": "RECORDED", "reason_codes": ["TEST"],
        "payload_sha256": None, "evidence_refs": [], "related_record_refs": [],
        "parent_audit_event_ids": [], "previous_event_sha256": previous,
        "sequence_number": seq, "created_at": "2026-07-17T00:00:00Z",
    }
    return {
        "schema_version": "audit_event.v0.1",
        "audit_event_id": audit_id,
        "trace_id": "trace_t", "task_id": "task_t", "task_revision": 1,
        "core_context_binding_id": "ccb-t",
        "event_type": "OTHER",
        "actor": {"actor_type": "system", "actor_id": "test", "role_id": None,
                  "role_version": None, "assignment_id": None},
        "subject": {"subject_type": "TASK", "subject_id": "task_t", "subject_ref": "in_memory:s",
                    "subject_fingerprint": "sha256:" + "0" * 64},
        "event": {"event_summary": summary, "outcome": "RECORDED", "reason_codes": ["TEST"],
                  "payload_ref": "in_memory:s", "payload_sha256": None,
                  "evidence_refs": [], "related_record_refs": []},
        "lineage": {"parent_audit_event_ids": [], "previous_event_sha256": previous,
                    "sequence_number": seq},
        "integrity": {"hash_schema": "audit_event_fingerprint_payload.v0.1",
                      "event_fingerprint_payload": payload,
                      "event_sha256": integrity.sha256_value(payload),
                      "append_only": True, "overwrite_allowed": False, "delete_allowed": False},
        "sensitivity": "INTERNAL",
        "runtime_effect": audit_event_runtime_effect(),
        "created_at": "2026-07-17T00:00:00Z",
    }


@pytest.fixture
def chain():
    """Three genuinely chained events."""
    events = []
    previous = None
    for seq in range(1, 4):
        event = _event(seq=seq, previous=previous)
        events.append(event)
        previous = event["integrity"]["event_sha256"]
    return events


# --- the verifier: what it must catch ---------------------------------------------


def test_a_genuine_chain_verifies(chain):
    report = verify_audit_chain(chain)
    assert report["intact"] is True
    assert report["checked"] == 3
    assert report["breaks"] == []
    assert report["first_break_index"] is None


def test_empty_ledger_is_trivially_intact():
    assert verify_audit_chain([])["intact"] is True


def test_editing_the_visible_record_is_caught(chain):
    """The check a self-hash alone MISSES: the payload still hashes correctly because it was
    not touched, so only payload-vs-record agreement catches this."""
    chain[1]["event"]["event_summary"] = "TAMPERED"
    report = verify_audit_chain(chain)
    assert report["intact"] is False
    assert report["first_break_index"] == 1
    assert {b["check"] for b in report["breaks"]} == {"AUDIT_PAYLOAD_RECORD_MISMATCH"}


def test_editing_the_payload_breaks_its_own_hash(chain):
    chain[1]["integrity"]["event_fingerprint_payload"]["event_summary"] = "TAMPERED"
    report = verify_audit_chain(chain)
    assert report["intact"] is False
    assert "AUDIT_EVENT_HASH_MISMATCH" in {b["check"] for b in report["breaks"]}


def test_editing_both_consistently_still_breaks_the_chain(chain):
    """Making record and payload agree requires rehashing, which breaks the link to the next
    event — there is no self-consistent edit."""
    chain[1]["event"]["event_summary"] = "TAMPERED"
    chain[1]["integrity"]["event_fingerprint_payload"]["event_summary"] = "TAMPERED"
    report = verify_audit_chain(chain)
    assert report["intact"] is False


def test_deleting_an_event_is_caught(chain):
    """Audit concealment: removing a middle event orphans the next one's previous hash."""
    del chain[1]
    report = verify_audit_chain(chain)
    assert report["intact"] is False
    assert "AUDIT_PREVIOUS_HASH_MISMATCH" in {b["check"] for b in report["breaks"]}


def test_deleting_events_from_the_front_is_caught(chain):
    """Front truncation: the surviving first event's previous hash dangles into deleted
    history — a true genesis must carry a null previous hash."""
    report = verify_audit_chain(chain[1:])
    assert report["intact"] is False
    assert report["first_break_index"] == 0
    assert "AUDIT_PREVIOUS_HASH_MISMATCH" in {b["check"] for b in report["breaks"]}


def test_flipping_a_runtime_effect_flag_is_caught(chain):
    """runtime_effect is outside the fingerprint payload, so only the declaration-invariant
    check catches an edited grant flag — the exact blind spot the payload check leaves."""
    chain[1]["runtime_effect"] = dict(audit_event_runtime_effect(), grants_execution=True)
    report = verify_audit_chain(chain)
    assert report["intact"] is False
    assert "AUDIT_DECLARATION_MISMATCH" in {b["check"] for b in report["breaks"]}


def test_altering_the_record_schema_claim_is_caught(chain):
    chain[2]["schema_version"] = "audit_event.v9.9"
    report = verify_audit_chain(chain)
    assert report["intact"] is False
    assert "AUDIT_DECLARATION_MISMATCH" in {b["check"] for b in report["breaks"]}


def test_reordering_events_is_caught(chain):
    chain[1], chain[2] = chain[2], chain[1]
    report = verify_audit_chain(chain)
    assert report["intact"] is False
    assert "AUDIT_PREVIOUS_HASH_MISMATCH" in {b["check"] for b in report["breaks"]}


def test_inserting_a_forged_event_is_caught(chain):
    chain.insert(1, copy.deepcopy(chain[2]))
    report = verify_audit_chain(chain)
    assert report["intact"] is False


def test_flipping_the_append_only_boundary_is_caught(chain):
    """A record that announces it may be deleted contradicts the trail it lives in."""
    chain[1]["integrity"]["delete_allowed"] = True
    report = verify_audit_chain(chain)
    assert report["intact"] is False
    assert "AUDIT_APPEND_ONLY_BOUNDARY_MISMATCH" in {b["check"] for b in report["breaks"]}


def test_a_structurally_broken_event_is_reported_not_raised(chain):
    chain[1]["integrity"] = {}
    report = verify_audit_chain(chain)
    assert report["intact"] is False
    assert "AUDIT_STRUCTURE_INVALID" in {b["check"] for b in report["breaks"]}


def test_a_prefix_of_a_valid_chain_still_verifies(chain):
    """Documents the known limit: truncating the TAIL leaves a valid chain, so link
    verification alone cannot detect it."""
    assert verify_audit_chain(chain[:2])["intact"] is True


# --- the console verbs -------------------------------------------------------------


@pytest.fixture
def stores(tmp_path, chain):
    control_store = ControlStore(tmp_path)
    ledger = LedgerStore(tmp_path / "ledger")
    ledger.append_audit_events(chain)
    return control_store, ledger


def test_audit_and_recovery_are_registered_console_commands():
    assert control.CMD_AUDIT in control.COMMANDS
    assert control.CMD_RECOVERY in control.COMMANDS


@pytest.mark.parametrize("verb", ["audit", "recovery"])
@pytest.mark.parametrize("mode", [control.ACTIVE, control.PAUSED, control.KILLED])
def test_reads_answer_in_every_mode_and_change_nothing(verb, mode, stores):
    """kill_allows: [read_only_status, audit_read] — a killed runtime must still let the
    operator see the trail and diagnose. That is when they need it most."""
    control_store, ledger = stores
    control_store.path.parent.mkdir(parents=True, exist_ok=True)
    control_store.path.write_text(json.dumps(ControlState(
        mode=mode, updated_by="op", updated_at="2026-07-17T00:00:00Z", reason="t").as_record()),
        encoding="utf-8")
    outcome = control.apply_command(control_store, verb, actor="local_console", ledger=ledger)
    assert outcome["changed"] is False
    assert outcome["action"] == verb
    assert outcome["mode"] == mode
    # The state on disk is untouched by a read.
    assert control_store.load().mode == mode


def test_audit_reports_an_intact_chain(stores):
    control_store, ledger = stores
    reply = control.apply_command(control_store, "audit", actor="op", ledger=ledger)["reply"]
    assert "INTACT" in reply
    assert "3 event(s)" in reply


def test_audit_reports_a_broken_chain_without_raising(tmp_path, chain):
    control_store = ControlStore(tmp_path)
    ledger = LedgerStore(tmp_path / "ledger")
    chain[1]["event"]["event_summary"] = "TAMPERED"
    ledger.append_audit_events(chain)
    reply = control.apply_command(control_store, "audit", actor="op", ledger=ledger)["reply"]
    assert "BROKEN" in reply
    assert "AUDIT_PAYLOAD_RECORD_MISMATCH" in reply
    # It must tell the operator NOT to "repair" the trail.
    assert "audit concealment" in reply


def test_audit_survives_a_corrupt_ledger(tmp_path):
    """The diagnostic must not fail with the thing it is diagnosing."""
    control_store = ControlStore(tmp_path)
    ledger = LedgerStore(tmp_path / "ledger")
    ledger.root.mkdir(parents=True, exist_ok=True)
    (ledger.root / "audit_events.jsonl").write_text("{not json\n", encoding="utf-8")
    reply = control.apply_command(control_store, "audit", actor="op", ledger=ledger)["reply"]
    assert "LEDGER UNREADABLE" in reply
    assert "recovery" in reply  # points onward rather than dead-ending


def test_audit_tail_limit_is_clamped_not_refused(stores):
    """An operator typo must not deny them their audit trail."""
    control_store, ledger = stores
    for arg in ("abc", "-5", "99999", None):
        outcome = control.apply_command(control_store, "audit", actor="op", ledger=ledger, arg=arg)
        assert "INTACT" in outcome["reply"]


def test_recovery_reports_no_faults_on_a_healthy_runtime(stores):
    control_store, ledger = stores
    reply = control.apply_command(control_store, "recovery", actor="op", ledger=ledger)["reply"]
    assert "No faults found" in reply
    assert "audit_events" in reply


def test_recovery_names_a_corrupt_store_and_refuses_to_repair_it(tmp_path):
    control_store = ControlStore(tmp_path)
    ledger = LedgerStore(tmp_path / "ledger")
    ledger.root.mkdir(parents=True, exist_ok=True)
    (ledger.root / "audit_events.jsonl").write_text("{not json\n", encoding="utf-8")
    reply = control.apply_command(control_store, "recovery", actor="op", ledger=ledger)["reply"]
    assert "FAULT" in reply
    assert "CORRUPT" in reply
    assert "nothing below is modified" in reply
    assert "audit concealment" in reply


def test_recovery_names_the_exit_from_a_corrupt_control_state(tmp_path, chain):
    """The one genuinely stuck state the live runtime has — and its exit already exists."""
    control_store = ControlStore(tmp_path)
    ledger = LedgerStore(tmp_path / "ledger")
    ledger.append_audit_events(chain)
    control_store.path.parent.mkdir(parents=True, exist_ok=True)
    control_store.path.write_text("{corrupt", encoding="utf-8")
    reply = control.apply_command(control_store, "recovery", actor="op", ledger=ledger)["reply"]
    assert "KILLED" in reply
    assert "resume" in reply


def test_recovery_does_not_repair_a_corrupt_store(tmp_path):
    """The bytes on disk must be exactly as they were: this verb diagnoses only."""
    control_store = ControlStore(tmp_path)
    ledger = LedgerStore(tmp_path / "ledger")
    ledger.root.mkdir(parents=True, exist_ok=True)
    path = ledger.root / "audit_events.jsonl"
    path.write_text("{not json\n", encoding="utf-8")
    control.apply_command(control_store, "recovery", actor="op", ledger=ledger)
    assert path.read_text(encoding="utf-8") == "{not json\n"


def test_health_survives_a_corrupt_file(tmp_path):
    ledger = LedgerStore(tmp_path / "ledger")
    ledger.root.mkdir(parents=True, exist_ok=True)
    (ledger.root / "audit_events.jsonl").write_text("{bad\n", encoding="utf-8")
    report = {e["kind"]: e for e in ledger.health()}
    assert report["audit_events"]["status"] == "CORRUPT"
    assert report["records"]["status"] == "ABSENT"
