"""R2.6 Audit.

``build_pipeline_audit`` produces the append-only, hash-chained sequence of
``audit_event.v0.1`` records for one MVP task run: TASK_CREATED -> PERMISSION_DECIDED
-> VALIDATION_COMPLETED -> TASK_STATE_CHANGED. Each event is fingerprinted
(``event_sha256``) and the next event carries the previous event's hash and id, so the
chain is tamper-evident. Audit is evidence only — ``runtime_effect.mode`` is
EVIDENCE_ONLY with every grant/mutate flag false; it is not Authority and does not
enable anything. Secret values are never recorded (payloads carry refs + fingerprints,
and ``sha256_record`` secret-scans the fingerprinted records).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from runtime.read_only_kernel import integrity, schema_validation
from runtime.read_only_kernel.integrity import IntegrityError
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from .authority import audit_event_runtime_effect
from .errors import AuditError

AUDIT_EVENT_SCHEMA_VERSION = "audit_event.v0.1"
FINGERPRINT_SCHEMA = "audit_event_fingerprint_payload.v0.1"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _fingerprint(record: Mapping[str, Any], label: str) -> str:
    try:
        return integrity.sha256_record(dict(record))
    except IntegrityError as exc:
        raise AuditError("SUBJECT_FINGERPRINT_FAILED", f"{label}: {exc}") from exc


def _make_event(
    *,
    root: Path,
    task: Mapping[str, Any],
    event_type: str,
    actor: dict[str, Any],
    subject_type: str,
    subject_id: str,
    subject_ref: str,
    subject_fingerprint: str,
    summary: str,
    outcome: str,
    reason_codes: list[str],
    related_record_refs: list[str],
    evidence_refs: list[str],
    payload_sha256: str | None,
    sequence: int,
    previous_hash: str | None,
    previous_audit_id: str | None,
    now: str,
) -> tuple[dict[str, Any], str]:
    identity = task["identity"]
    ccb = task["context"]["core_context_binding_id"]
    audit_id = integrity.short_id(
        "audit", {"task_id": identity["task_id"], "task_revision": identity["task_revision"],
                  "event_type": event_type, "sequence": sequence}
    )
    payload = {
        "schema_version": FINGERPRINT_SCHEMA,
        "audit_event_id": audit_id,
        "trace_id": identity["trace_id"],
        "task_id": identity["task_id"],
        "task_revision": identity["task_revision"],
        "core_context_binding_id": ccb,
        "event_type": event_type,
        "actor_ref": f"{actor['actor_type']}:{actor['actor_id']}",
        "subject_ref": subject_ref,
        "subject_fingerprint": subject_fingerprint,
        "event_summary": summary,
        "outcome": outcome,
        "reason_codes": reason_codes,
        "payload_sha256": payload_sha256,
        "evidence_refs": evidence_refs,
        "related_record_refs": related_record_refs,
        "parent_audit_event_ids": [previous_audit_id] if previous_audit_id else [],
        "previous_event_sha256": previous_hash,
        "sequence_number": sequence,
        "created_at": now,
    }
    try:
        event_sha256 = integrity.sha256_value(payload)
    except IntegrityError as exc:
        raise AuditError("EVENT_FINGERPRINT_FAILED", str(exc)) from exc

    record = {
        "schema_version": AUDIT_EVENT_SCHEMA_VERSION,
        "audit_event_id": audit_id,
        "trace_id": identity["trace_id"],
        "task_id": identity["task_id"],
        "task_revision": identity["task_revision"],
        "core_context_binding_id": ccb,
        "event_type": event_type,
        "actor": actor,
        "subject": {
            "subject_type": subject_type,
            "subject_id": subject_id,
            "subject_ref": subject_ref,
            "subject_fingerprint": subject_fingerprint,
        },
        "event": {
            "event_summary": summary,
            "outcome": outcome,
            "reason_codes": reason_codes,
            "payload_ref": subject_ref,
            "payload_sha256": payload_sha256,
            "evidence_refs": evidence_refs,
            "related_record_refs": related_record_refs,
        },
        "lineage": {
            "parent_audit_event_ids": payload["parent_audit_event_ids"],
            "previous_event_sha256": previous_hash,
            "sequence_number": sequence,
        },
        "integrity": {
            "hash_schema": FINGERPRINT_SCHEMA,
            "event_fingerprint_payload": payload,
            "event_sha256": event_sha256,
            "append_only": True,
            "overwrite_allowed": False,
            "delete_allowed": False,
        },
        "sensitivity": task["context"]["data_sensitivity"],
        "runtime_effect": audit_event_runtime_effect(),
        "created_at": now,
    }
    schema_path = root / "schemas" / f"{AUDIT_EVENT_SCHEMA_VERSION}.schema.json"
    try:
        schema_validation.validate_against_schema(record, schema_path, "audit_event")
    except RuntimeSchemaError as exc:
        raise AuditError("AUDIT_EVENT_INVALID", str(exc)) from exc
    return record, event_sha256


def _actor(actor_type: str, actor_id: str, *, role_id: str | None = None,
           role_version: str | None = None, assignment_id: str | None = None) -> dict[str, Any]:
    return {"actor_type": actor_type, "actor_id": actor_id, "role_id": role_id,
            "role_version": role_version, "assignment_id": assignment_id}


def _chain_events(
    root: Path, task: Mapping[str, Any], steps: list[dict[str, Any]], now: str,
    genesis_previous_hash: str | None,
) -> list[dict[str, Any]]:
    """Fingerprint and hash-chain a sequence of event steps. ``genesis_previous_hash``
    links the first event onto a prior run's last event so the ledger is tamper-evident
    across runs, not just within one."""
    events: list[dict[str, Any]] = []
    previous_hash = genesis_previous_hash
    previous_audit_id: str | None = None
    for sequence, step in enumerate(steps, start=1):
        record, event_sha256 = _make_event(
            root=root, task=task, now=now, sequence=sequence,
            previous_hash=previous_hash, previous_audit_id=previous_audit_id, **step,
        )
        events.append(record)
        previous_hash = event_sha256
        previous_audit_id = record["audit_event_id"]
    return events


def _task_created_step(tid: str, task_fp: str) -> dict[str, Any]:
    return dict(
        event_type="TASK_CREATED",
        actor=_actor("system", "mvp.intake"),
        subject_type="TASK", subject_id=tid,
        subject_ref=f"in_memory:task:{tid}", subject_fingerprint=task_fp,
        summary="Task received and recorded (read-only intake).",
        outcome="RECORDED", reason_codes=["TASK_INTAKE"],
        related_record_refs=[], evidence_refs=[f"in_memory:task:{tid}"], payload_sha256=None,
    )


def build_pipeline_audit(
    task: Mapping[str, Any],
    permission_decision: Mapping[str, Any],
    validation_result: Mapping[str, Any],
    agent_output: Mapping[str, Any],
    invocation: Mapping[str, Any],
    *,
    now: str,
    genesis_previous_hash: str | None = None,
    repo_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Return the hash-chained audit trail for one completed task run. Append-only;
    evidence only.

    Events: TASK_CREATED -> PERMISSION_DECIDED -> MODEL_INVOKED -> VALIDATION_COMPLETED ->
    TASK_STATE_CHANGED. MODEL_INVOKED records the gated action itself (which model/version
    answered, token usage, finish reason, and whether it crossed the network boundary) as
    an OTHER-typed event — ``audit_event.v0.1`` has no model-specific type, so the fact is
    carried in the reason codes + fingerprinted invocation payload rather than by adding a
    new schema. ``validation_result`` carries the Agent Output reference and fingerprint.
    """
    root = repo_root if repo_root is not None else _repo_root()
    tid = task["identity"]["task_id"]
    result = validation_result["validation"]["result"]
    # Map the validation result (PASS/REVISE/BLOCK) onto the audit outcome enum
    # (which uses BLOCKED, not BLOCK).
    validation_outcome = {"PASS": "PASS", "REVISE": "REVISE", "BLOCK": "BLOCKED"}[result]
    final_state = "COMPLETED" if result == "PASS" else "BLOCKED"

    task_fp = _fingerprint(task, "task")
    perm_fp = _fingerprint(permission_decision, "permission_decision")
    val_fp = _fingerprint(validation_result, "validation_result")
    inv_fp = _fingerprint(dict(invocation), "invocation")
    output_ref = validation_result["subject"]["subject_ref"]
    output_fp = validation_result["subject"]["subject_fingerprint"]
    egress = bool(invocation.get("network_egress"))

    steps = [
        _task_created_step(tid, task_fp),
        dict(
            event_type="PERMISSION_DECIDED",
            actor=_actor("thomas_prime", "thomas.prime"),
            subject_type="PERMISSION_DECISION", subject_id=permission_decision["permission_decision_id"],
            subject_ref=f"in_memory:{permission_decision['permission_decision_id']}", subject_fingerprint=perm_fp,
            summary=f"Governance decided {permission_decision['decision']['permission_decision']} for the planned action.",
            outcome="RECORDED", reason_codes=[permission_decision["decision"]["permission_decision"]],
            related_record_refs=[f"in_memory:task:{tid}"],
            evidence_refs=[f"in_memory:{permission_decision['permission_decision_id']}"], payload_sha256=perm_fp,
        ),
        dict(
            event_type="OTHER",
            actor=_actor("role", agent_output["role_id"], role_id=agent_output["role_id"],
                         role_version=agent_output["role_version"], assignment_id=agent_output["assignment_id"]),
            subject_type="AGENT_OUTPUT", subject_id=agent_output["agent_output_id"],
            subject_ref=output_ref, subject_fingerprint=output_fp,
            summary=(f"Model invoked: {invocation.get('model_id')} {invocation.get('model_version')} — "
                     f"{invocation.get('tokens_used')} tokens, finish={invocation.get('finish_reason')}, "
                     f"network_egress={egress}."),
            outcome="RECORDED",
            reason_codes=["MODEL_INVOKED", "NETWORK_EGRESS" if egress else "NO_NETWORK_EGRESS"],
            related_record_refs=[f"in_memory:{permission_decision['permission_decision_id']}"],
            evidence_refs=[output_ref], payload_sha256=inv_fp,
        ),
        dict(
            event_type="VALIDATION_COMPLETED",
            actor=_actor("system", "mvp.output_validator.automatic"),
            subject_type="VALIDATION_RESULT", subject_id=validation_result["validation_result_id"],
            subject_ref=f"in_memory:{validation_result['validation_result_id']}", subject_fingerprint=val_fp,
            summary=f"Automatic output validation result: {result}.",
            outcome=validation_outcome, reason_codes=[f"VALIDATION_{result}"],
            related_record_refs=[output_ref], evidence_refs=[output_ref], payload_sha256=output_fp,
        ),
        dict(
            event_type="TASK_STATE_CHANGED",
            actor=_actor("thomas_prime", "thomas.prime"),
            subject_type="TASK", subject_id=tid,
            subject_ref=f"in_memory:task:{tid}", subject_fingerprint=task_fp,
            summary=f"Task run concluded: {final_state}.",
            outcome="RECORDED" if result == "PASS" else "BLOCKED", reason_codes=[f"FINAL_{final_state}"],
            related_record_refs=[f"in_memory:{validation_result['validation_result_id']}"],
            evidence_refs=[f"in_memory:task:{tid}"], payload_sha256=None,
        ),
    ]
    return _chain_events(root, task, steps, now, genesis_previous_hash)


def build_blocked_audit(
    task: Mapping[str, Any],
    *,
    stage: str,
    reason_code: str,
    now: str,
    genesis_previous_hash: str | None = None,
    repo_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Return a minimal hash-chained trail for a run that failed *after* binding but
    before completion: TASK_CREATED -> TASK_STATE_CHANGED(BLOCKED). A blocked run is
    audited too — a governance-first agent that only records its successes is not
    auditable. Requires a bound ``task`` (an unbound early failure is recorded by the
    store's block ledger instead, since ``audit_event.v0.1`` needs a binding)."""
    root = repo_root if repo_root is not None else _repo_root()
    tid = task["identity"]["task_id"]
    task_fp = _fingerprint(task, "task")
    steps = [
        _task_created_step(tid, task_fp),
        dict(
            event_type="TASK_STATE_CHANGED",
            actor=_actor("system", "mvp.pipeline"),
            subject_type="TASK", subject_id=tid,
            subject_ref=f"in_memory:task:{tid}", subject_fingerprint=task_fp,
            summary=f"Task run blocked at {stage}: {reason_code}.",
            outcome="BLOCKED", reason_codes=[reason_code, "FINAL_BLOCKED"],
            related_record_refs=[], evidence_refs=[f"in_memory:task:{tid}"], payload_sha256=None,
        ),
    ]
    return _chain_events(root, task, steps, now, genesis_previous_hash)
