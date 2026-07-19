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
from typing import Any, Mapping, MutableMapping, Sequence

from runtime.read_only_kernel import integrity, schema_validation
from runtime.read_only_kernel.integrity import IntegrityError
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from .authority import audit_event_runtime_effect
from .errors import AuditError
from .memory import missing_origin_fields
from .paths import repo_root as _repo_root
from .validation import AUDIT_OUTCOME, stricter_result

AUDIT_EVENT_SCHEMA_VERSION = "audit_event.v0.1"
FINGERPRINT_SCHEMA = "audit_event_fingerprint_payload.v0.1"


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
    id_seed_extra: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    identity = task["identity"]
    ccb = task["context"]["core_context_binding_id"]
    audit_id = integrity.short_id(
        "audit", {"task_id": identity["task_id"], "task_revision": identity["task_revision"],
                  "event_type": event_type, "sequence": sequence,
                  **(dict(id_seed_extra) if id_seed_extra else {})}
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
    tool_use: Mapping[str, Any] | None = None,
    search_permission_decision: Mapping[str, Any] | None = None,
    independent_validation_result: Mapping[str, Any] | None = None,
    validator_invocation: Mapping[str, Any] | None = None,
    validator_permission_decision: Mapping[str, Any] | None = None,
    write_use: Mapping[str, Any] | None = None,
    write_permission_decision: Mapping[str, Any] | None = None,
    genesis_previous_hash: str | None = None,
    repo_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Return the hash-chained audit trail for one completed task run. Append-only;
    evidence only.

    Events: TASK_CREATED -> PERMISSION_DECIDED -> [TOOL_USED] -> MODEL_INVOKED ->
    [MEMORY_CANDIDATE_CREATED] -> VALIDATION_COMPLETED -> [VALIDATOR MODEL_INVOKED ->
    INDEPENDENT VALIDATION_COMPLETED] -> TASK_STATE_CHANGED. When the specialist proposed
    working-memory candidates, a MEMORY_CANDIDATE_CREATED event records their creation
    (proposals only — none promoted). When a read-only search ran, a TOOL_USED event
    (OTHER-typed, like MODEL_INVOKED — ``audit_event.v0.1`` has no tool-specific type)
    records the gated tool use itself, referencing its INTERNAL_READ PermissionDecision.
    MODEL_INVOKED records the gated model call; ``validation_result`` carries the Agent
    Output reference and fingerprint.

    R7: when an independent validator ran (``independent_validation_result`` +
    ``validator_invocation``), its model call and its INDEPENDENT validation verdict are
    each recorded, and the final task state derives from the **stricter** of the automatic
    and independent results (stricter_rule_wins).

    R8: when a controlled write ran (``write_use``), a WORKSPACE_WRITE event records it —
    the durable half of the EXECUTE_AND_REPORT obligation, referencing its
    WORKSPACE_REVERSIBLE_WRITE PermissionDecision.
    """
    root = repo_root if repo_root is not None else _repo_root()
    tid = task["identity"]["task_id"]
    result = validation_result["validation"]["result"]
    if independent_validation_result is not None:
        # The SAME merge that decides delivery (pipeline.py) decides the audited final
        # state — a second encoding here could desynchronize the ledger from reality.
        result = stricter_result(result, independent_validation_result["validation"]["result"])
    validation_outcome = AUDIT_OUTCOME[validation_result["validation"]["result"]]
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
    ]

    if tool_use is not None:
        tool_fp = _fingerprint(dict(tool_use), "tool_use")
        tool_egress = bool(tool_use.get("network_egress"))
        search_permdec_ref = (
            f"in_memory:{search_permission_decision['permission_decision_id']}"
            if search_permission_decision else f"in_memory:task:{tid}"
        )
        steps.append(dict(
            event_type="OTHER",
            actor=_actor("role", agent_output["role_id"], role_id=agent_output["role_id"],
                         role_version=agent_output["role_version"], assignment_id=agent_output["assignment_id"]),
            subject_type="TOOL_USE", subject_id=str(tool_use.get("tool_id")),
            subject_ref=f"in_memory:tool_use:{tid}", subject_fingerprint=tool_fp,
            summary=(f"Read-only tool used: {tool_use.get('tool_id')} ({tool_use.get('tool_class')}) — "
                     f"{tool_use.get('result_count')} results from {tool_use.get('sources')}, "
                     f"network_egress={tool_egress}."),
            outcome="RECORDED",
            reason_codes=["TOOL_USED", "NETWORK_EGRESS" if tool_egress else "NO_NETWORK_EGRESS"],
            related_record_refs=[search_permdec_ref],
            evidence_refs=[f"in_memory:tool_use:{tid}"], payload_sha256=tool_fp,
        ))

    steps.append(dict(
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
    ))

    # R5: if the specialist proposed working-memory candidates, record their creation as its
    # own EVIDENCE_ONLY event. Candidates are proposals (ALLOW/CANDIDATE_CREATION) — the event
    # asserts nothing was promoted; the candidates are fingerprinted via the output.
    candidates = agent_output.get("memory_candidates") or []
    if candidates:
        types = sorted({c.get("candidate_type", "memory") for c in candidates if isinstance(c, dict)})
        steps.append(dict(
            event_type="MEMORY_CANDIDATE_CREATED",
            actor=_actor("role", agent_output["role_id"], role_id=agent_output["role_id"],
                         role_version=agent_output["role_version"], assignment_id=agent_output["assignment_id"]),
            subject_type="MEMORY_CANDIDATE", subject_id=agent_output["agent_output_id"],
            subject_ref=output_ref, subject_fingerprint=output_fp,
            summary=f"{len(candidates)} working-memory candidate(s) proposed (types: {types}); none promoted.",
            outcome="RECORDED",
            reason_codes=["MEMORY_CANDIDATE_CREATED", f"COUNT_{len(candidates)}", "NO_PROMOTION"],
            related_record_refs=[output_ref], evidence_refs=[output_ref], payload_sha256=output_fp,
        ))

    automatic_result = validation_result["validation"]["result"]
    steps.append(dict(
        event_type="VALIDATION_COMPLETED",
        actor=_actor("system", "mvp.output_validator.automatic"),
        subject_type="VALIDATION_RESULT", subject_id=validation_result["validation_result_id"],
        subject_ref=f"in_memory:{validation_result['validation_result_id']}", subject_fingerprint=val_fp,
        summary=f"Automatic output validation result: {automatic_result}.",
        outcome=validation_outcome, reason_codes=[f"VALIDATION_{automatic_result}"],
        related_record_refs=[output_ref], evidence_refs=[output_ref], payload_sha256=output_fp,
    ))

    # R7: the independent validator's own model call + INDEPENDENT verdict, each audited.
    if independent_validation_result is not None:
        ival = independent_validation_result
        ival_fp = _fingerprint(ival, "independent_validation_result")
        ival_result = ival["validation"]["result"]
        ival_outcome = AUDIT_OUTCOME[ival_result]
        validator_role = ival["validator"].get("validator_role_id") or "validation.independent"
        validator_permdec_ref = (
            f"in_memory:{validator_permission_decision['permission_decision_id']}"
            if validator_permission_decision else output_ref
        )
        if validator_invocation is not None:
            vinv_fp = _fingerprint(dict(validator_invocation), "validator_invocation")
            vegress = bool(validator_invocation.get("network_egress"))
            steps.append(dict(
                event_type="OTHER",
                actor=_actor("role", validator_role, role_id=validator_role,
                             role_version=ival["validator"].get("validator_role_version")),
                subject_type="VALIDATION_RESULT", subject_id=ival["validation_result_id"],
                subject_ref=f"in_memory:{ival['validation_result_id']}", subject_fingerprint=ival_fp,
                summary=(f"Validator model invoked: {validator_invocation.get('model_id')} "
                         f"{validator_invocation.get('model_version')} — "
                         f"{validator_invocation.get('tokens_used')} tokens, network_egress={vegress}."),
                outcome="RECORDED",
                reason_codes=["MODEL_INVOKED", "NETWORK_EGRESS" if vegress else "NO_NETWORK_EGRESS"],
                related_record_refs=[validator_permdec_ref],
                evidence_refs=[output_ref], payload_sha256=vinv_fp,
            ))
        steps.append(dict(
            event_type="VALIDATION_COMPLETED",
            actor=_actor("role", validator_role, role_id=validator_role,
                         role_version=ival["validator"].get("validator_role_version")),
            subject_type="VALIDATION_RESULT", subject_id=ival["validation_result_id"],
            subject_ref=f"in_memory:{ival['validation_result_id']}", subject_fingerprint=ival_fp,
            summary=f"Independent validation result: {ival_result} (stricter result decides the final state).",
            outcome=ival_outcome, reason_codes=[f"VALIDATION_{ival_result}", "INDEPENDENT"],
            related_record_refs=[output_ref], evidence_refs=[output_ref], payload_sha256=output_fp,
        ))

    # R8: the controlled write is the runtime's first EXECUTE_AND_REPORT action — this
    # event is the "report" half's durable record. OTHER-typed with the subtype in
    # reason_codes (audit_event.v0.1 has no write type), following MODEL_INVOKED/TOOL_USED.
    # It records the target, size, and content hash — never the content. It is emitted
    # last because the write persists an already-validated result; a run whose validation
    # did not PASS never reaches it (the pipeline does not write).
    if write_use is not None:
        write_fp = _fingerprint(dict(write_use), "write_use")
        touched_disk = bool(write_use.get("filesystem_write"))
        write_permdec_ref = (
            f"in_memory:{write_permission_decision['permission_decision_id']}"
            if write_permission_decision else f"in_memory:task:{tid}"
        )
        steps.append(dict(
            event_type="OTHER",
            actor=_actor("role", agent_output["role_id"], role_id=agent_output["role_id"],
                         role_version=agent_output["role_version"], assignment_id=agent_output["assignment_id"]),
            subject_type="TOOL_USE", subject_id=str(write_use.get("tool_id")),
            subject_ref=f"in_memory:write_use:{tid}", subject_fingerprint=write_fp,
            summary=(f"Controlled write: {write_use.get('target_ref')} — "
                     f"{write_use.get('bytes_written')} bytes, create-only, "
                     f"filesystem_write={touched_disk}."),
            outcome="RECORDED",
            reason_codes=[
                "WORKSPACE_WRITE",
                "EXECUTE_AND_REPORT",
                "CREATE_ONLY",
                "FILESYSTEM_WRITE" if touched_disk else "NO_FILESYSTEM_WRITE",
            ],
            related_record_refs=[write_permdec_ref],
            evidence_refs=[f"in_memory:write_use:{tid}"], payload_sha256=write_fp,
        ))

    steps.append(dict(
        event_type="TASK_STATE_CHANGED",
        actor=_actor("thomas_prime", "thomas.prime"),
        subject_type="TASK", subject_id=tid,
        subject_ref=f"in_memory:task:{tid}", subject_fingerprint=task_fp,
        summary=f"Task run concluded: {final_state}.",
        outcome="RECORDED" if result == "PASS" else "BLOCKED", reason_codes=[f"FINAL_{final_state}"],
        related_record_refs=[f"in_memory:{validation_result['validation_result_id']}"],
        evidence_refs=[f"in_memory:task:{tid}"], payload_sha256=None,
    ))
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


def build_promotion_audit(
    candidate: Mapping[str, Any],
    validated_entry: Mapping[str, Any],
    *,
    promoted_by: str,
    reason: str,
    now: str,
    previous_hash: str | None = None,
    previous_audit_id: str | None = None,
    repo_root: Path | None = None,
) -> tuple[dict[str, Any], str]:
    """Build the single audit event that *reports* an operator promotion (R5.4).

    Promoting a working-memory candidate to VALIDATED memory is EXECUTE_AND_REPORT and happens
    off the run path, so it is not part of any pipeline chain — it is one standalone event that
    chains onto the durable ledger's current tip (``previous_hash``), keeping the audit trail
    tamper-evident across runs *and* operator actions. There is no dedicated ``event_type`` for
    promotion, so (reuse-first) it is ``OTHER`` with the specifics carried in ``reason_codes``.

    The event is anchored to the *originating task* via the candidate's ``origin`` provenance;
    a candidate without complete provenance fails closed (``AuditError``) rather than fabricate a
    task identity. Returns ``(record, event_sha256)``; the caller appends it to the ledger."""
    root = repo_root if repo_root is not None else _repo_root()
    origin = candidate.get("origin") if isinstance(candidate, Mapping) else None
    if not isinstance(origin, Mapping):
        raise AuditError("PROMOTION_ORIGIN_MISSING",
                         "candidate has no origin provenance; promotion cannot be audited")
    # THE completeness rule lives in memory.missing_origin_fields — creation and this
    # audit must agree on what "complete provenance" means (the boundary re-check stays;
    # only the field list is shared).
    missing = missing_origin_fields(origin)
    if missing:
        raise AuditError("PROMOTION_ORIGIN_INVALID",
                         f"candidate origin provenance is incomplete: {missing}")
    if not (isinstance(promoted_by, str) and promoted_by.strip()):
        raise AuditError("PROMOTION_ACTOR_MISSING", "promotion audit requires an operator identity")
    if not (isinstance(reason, str) and reason.strip()):
        raise AuditError("PROMOTION_REASON_MISSING", "promotion audit requires an operator reason")

    synthetic_task = {
        "identity": {"task_id": origin["task_id"], "task_revision": origin["task_revision"],
                     "trace_id": origin["trace_id"]},
        "context": {"core_context_binding_id": origin["core_context_binding_id"],
                    "data_sensitivity": origin["data_sensitivity"]},
    }
    candidate_id = candidate.get("candidate_id")
    validated_id = validated_entry.get("validated_memory_id")
    if not (isinstance(candidate_id, str) and candidate_id
            and isinstance(validated_id, str) and validated_id):
        raise AuditError("PROMOTION_SUBJECT_INVALID",
                         "promotion audit requires a candidate_id and a validated_memory_id")
    validated_fp = _fingerprint(validated_entry, "validated_memory")

    return _make_event(
        root=root, task=synthetic_task, now=now,
        event_type="OTHER",
        actor=_actor("thomas", promoted_by.strip()),
        subject_type="validated_memory", subject_id=validated_id,
        subject_ref=f"validated_memory:{validated_id}", subject_fingerprint=validated_fp,
        summary=(f"Operator {promoted_by.strip()} promoted working-memory candidate "
                 f"{candidate_id} to VALIDATED memory (EXECUTE_AND_REPORT): {reason.strip()}"),
        outcome="RECORDED",
        reason_codes=["MEMORY_PROMOTED", "EXECUTE_AND_REPORT", f"SOURCE_CANDIDATE_{candidate_id}"],
        related_record_refs=[f"working_memory:{candidate_id}"],
        evidence_refs=[f"validated_memory:{validated_id}", f"working_memory:{candidate_id}"],
        payload_sha256=validated_fp,
        sequence=1,
        previous_hash=previous_hash, previous_audit_id=previous_audit_id,
        id_seed_extra={"candidate_id": candidate_id, "validated_memory_id": validated_id,
                       "promoted_by": promoted_by.strip()},
    )


def _approval_synthetic_task(approval: Mapping[str, Any]) -> dict[str, Any]:
    """The task identity an approval event anchors to.

    An approval carries the full lineage of the action it decides (task, revision, trace,
    Core binding), so the event anchors to the originating task without fabricating anything.
    An action that needs Thomas is SENSITIVE by construction — it is the class of action the
    governance reserves to him — so the event is classified that way rather than as routine
    internal traffic.
    """
    return {
        "identity": {
            "task_id": approval["task_id"],
            "task_revision": approval["task_revision"],
            "trace_id": approval["trace_id"],
        },
        "context": {
            "core_context_binding_id": approval["core_context_binding_id"],
            "data_sensitivity": "SENSITIVE",
        },
    }


def build_approval_request_audit(
    approval: Mapping[str, Any],
    *,
    now: str,
    genesis_previous_hash: str | None = None,
    repo_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Audit that Thomas was ASKED (R9) — one standalone event chained onto the ledger tip.

    Asking is not acting: the outcome is RECORDED and the event asserts that nothing was
    authorized. ``audit_event.v0.1`` has no approval event type, so (reuse-first, per the
    I0.5.4 precedent) this is ``OTHER`` with the subtype in ``reason_codes``.
    """
    root = repo_root if repo_root is not None else _repo_root()
    approval_id = approval["approval_id"]
    snapshot = approval["approved_action_snapshot"]
    fp = _fingerprint(dict(approval), "approval")
    event, _ = _make_event(
        root=root, task=_approval_synthetic_task(approval), now=now,
        event_type="OTHER",
        actor=_actor("thomas_prime", "thomas.prime"),
        subject_type="APPROVAL", subject_id=approval_id,
        subject_ref=f"approval:{approval_id}", subject_fingerprint=fp,
        summary=(f"Approval requested from Thomas: {snapshot['action_type']} on "
                 f"{snapshot['target_ref']} ({snapshot['permission_scope']}); "
                 f"expires {approval['validity']['expires_at']}."),
        outcome="RECORDED",
        reason_codes=["APPROVAL_REQUESTED", "APPROVAL_REQUIRED", "PENDING", "NO_EXECUTION_AUTHORIZED"],
        related_record_refs=[f"in_memory:{approval['permission_decision_id']}"],
        evidence_refs=[f"approval:{approval_id}"],
        payload_sha256=fp,
        sequence=1,
        previous_hash=genesis_previous_hash, previous_audit_id=None,
        id_seed_extra={"approval_id": approval_id, "kind": "approval_request"},
    )
    return [event]


def build_approval_decision_audit(
    approval: Mapping[str, Any],
    *,
    now: str,
    actor_id: str,
    genesis_previous_hash: str | None = None,
    repo_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Audit Thomas's ANSWER (R9) — the durable record of who decided what, and how we know.

    The event names the verification method and ref, so the trail shows not just that an
    approval was granted but that it was granted by the registered Thomas on the verified
    private channel. It records no execution: an APPROVED approval authorizes nothing here.
    """
    root = repo_root if repo_root is not None else _repo_root()
    approval_id = approval["approval_id"]
    snapshot = approval["approved_action_snapshot"]
    status = approval["status"]
    approver = approval["approver"]
    if approver.get("verification_status") != "VERIFIED":
        raise AuditError("APPROVAL_UNVERIFIED",
                         "a decided approval must carry a verified approver to be audited")
    fp = _fingerprint(dict(approval), "approval")
    event, _ = _make_event(
        root=root, task=_approval_synthetic_task(approval), now=now,
        event_type="OTHER",
        actor=_actor("thomas", actor_id),
        subject_type="APPROVAL", subject_id=approval_id,
        subject_ref=f"approval:{approval_id}", subject_fingerprint=fp,
        summary=(f"Thomas {status} approval {approval_id} for {snapshot['action_type']} on "
                 f"{snapshot['target_ref']} via {approver['identity_verification_method']}; "
                 f"the decision authorizes nothing on its own — consumption is a separate step."),
        outcome="RECORDED",
        reason_codes=[f"APPROVAL_{status}", "VERIFIED_CONTROL_CHANNEL", "ONE_TIME_USE",
                      "NOT_CONSUMED", "NO_EXECUTION_AUTHORIZED"],
        related_record_refs=[f"in_memory:{approval['permission_decision_id']}"],
        evidence_refs=[f"approval:{approval_id}", approver["verification_ref"]],
        payload_sha256=fp,
        sequence=1,
        previous_hash=genesis_previous_hash, previous_audit_id=None,
        id_seed_extra={"approval_id": approval_id, "kind": "approval_decision", "status": status},
    )
    return [event]


def build_approval_consumption_audit(
    approval: Mapping[str, Any],
    validated_entry: Mapping[str, Any],
    *,
    now: str,
    genesis_previous_hash: str | None = None,
    repo_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Audit that an APPROVED approval was CONSUMED (R10) — the one-time grant was spent to
    perform its bound promotion.

    This is the "report" half of the consumption: one standalone event chained onto the ledger
    tip, anchored to the originating task via the approval's lineage. It records the durable
    outcome (the validated-memory id it produced) but never the content. The approval must be
    CONSUMED and carry a verified Thomas approver, or the event is refused — an unspent or
    unverified approval has no consumption to report. ``OTHER``-typed with the subtype in
    ``reason_codes`` (reuse-first, per the I0.5.4 precedent), like the R5 promotion event.
    """
    root = repo_root if repo_root is not None else _repo_root()
    approval_id = approval["approval_id"]
    snapshot = approval["approved_action_snapshot"]
    approver = approval.get("approver", {})
    if approval.get("status") != "CONSUMED":
        raise AuditError("APPROVAL_NOT_CONSUMED",
                         "only a CONSUMED approval can be reported as consumed")
    if approver.get("verification_status") != "VERIFIED":
        raise AuditError("APPROVAL_UNVERIFIED",
                         "a consumed approval must carry a verified approver to be audited")
    validated_id = validated_entry.get("validated_memory_id")
    if not (isinstance(validated_id, str) and validated_id):
        raise AuditError("CONSUMPTION_SUBJECT_INVALID",
                         "consumption audit requires the validated_memory_id it produced")
    validated_fp = _fingerprint(validated_entry, "validated_memory")
    event, _ = _make_event(
        root=root, task=_approval_synthetic_task(approval), now=now,
        event_type="OTHER",
        actor=_actor("thomas", approver.get("approved_by") or "Thomas"),
        subject_type="validated_memory", subject_id=validated_id,
        subject_ref=f"validated_memory:{validated_id}", subject_fingerprint=validated_fp,
        summary=(f"Approval {approval_id} CONSUMED: {snapshot['action_type']} on "
                 f"{snapshot['target_ref']} performed once, producing validated memory "
                 f"{validated_id} (EXECUTE_AND_REPORT under REVIEW_ONLY)."),
        outcome="RECORDED",
        reason_codes=["APPROVAL_CONSUMED", "MEMORY_PROMOTED", "EXECUTE_AND_REPORT",
                      "ONE_TIME_USE", "CONSUMED"],
        related_record_refs=[f"approval:{approval_id}",
                             f"in_memory:{approval['permission_decision_id']}"],
        evidence_refs=[f"validated_memory:{validated_id}", f"approval:{approval_id}"],
        payload_sha256=validated_fp,
        sequence=1,
        previous_hash=genesis_previous_hash, previous_audit_id=None,
        id_seed_extra={"approval_id": approval_id, "kind": "approval_consumption",
                       "validated_memory_id": validated_id},
    )
    return [event]


def rechain_events(events: Sequence[MutableMapping[str, Any]], previous_hash: str | None) -> None:
    """Re-anchor a pre-built chain segment onto the CURRENT ledger tip, in place.

    Builders chain events from the tip the caller read when the run STARTED. In the shipped
    multi-process deployment (operator loop + docker-exec CLIs + scheduler on one volume),
    another process may have appended meanwhile — both segments would then point at the same
    stale tip and the ledger would FORK: an honest ledger that verify_audit_chain reports as
    tampered, indistinguishable from real tampering. The durable store therefore re-anchors
    every segment at persist time, under the ledger lock: rewrite each event's
    ``previous_event_sha256`` (record + fingerprint payload, which covers it) and recompute
    its ``event_sha256``, cascading down the segment. Recomputing against an unchanged tip
    reproduces the identical hashes, so this is a no-op for the single-process case.

    Mutates the given events so every holder of these dicts (run results, replies) sees
    exactly what the ledger holds. Fails closed on a structurally invalid event.
    """
    previous = previous_hash
    for event in events:
        integrity_block = event.get("integrity")
        lineage = event.get("lineage")
        payload = integrity_block.get("event_fingerprint_payload") if isinstance(integrity_block, MutableMapping) else None
        if not (isinstance(integrity_block, MutableMapping) and isinstance(payload, MutableMapping)
                and isinstance(lineage, MutableMapping)):
            raise AuditError("EVENT_STRUCTURE_INVALID", "cannot rechain a malformed audit event")
        payload["previous_event_sha256"] = previous
        lineage["previous_event_sha256"] = previous
        try:
            event_sha256 = integrity.sha256_value(dict(payload))
        except IntegrityError as exc:
            raise AuditError("EVENT_FINGERPRINT_FAILED", str(exc)) from exc
        integrity_block["event_sha256"] = event_sha256
        previous = event_sha256


# --- verification ------------------------------------------------------------------

# How each fingerprint-payload field maps back onto the record it fingerprints. The payload
# duplicates the record's fields, so agreement between them is not redundant — it is the
# check that catches the obvious attack: edit the visible record and leave the payload
# alone, and a self-hash-only check still passes.
def _record_view(record: Mapping[str, Any]) -> dict[str, Any]:
    actor = record.get("actor", {})
    subject = record.get("subject", {})
    event = record.get("event", {})
    lineage = record.get("lineage", {})
    return {
        "audit_event_id": record.get("audit_event_id"),
        "trace_id": record.get("trace_id"),
        "task_id": record.get("task_id"),
        "task_revision": record.get("task_revision"),
        "core_context_binding_id": record.get("core_context_binding_id"),
        "event_type": record.get("event_type"),
        "actor_ref": f"{actor.get('actor_type')}:{actor.get('actor_id')}",
        "subject_ref": subject.get("subject_ref"),
        "subject_fingerprint": subject.get("subject_fingerprint"),
        "event_summary": event.get("event_summary"),
        "outcome": event.get("outcome"),
        "reason_codes": event.get("reason_codes"),
        "payload_sha256": event.get("payload_sha256"),
        "evidence_refs": event.get("evidence_refs"),
        "related_record_refs": event.get("related_record_refs"),
        "parent_audit_event_ids": lineage.get("parent_audit_event_ids"),
        "previous_event_sha256": lineage.get("previous_event_sha256"),
        "sequence_number": lineage.get("sequence_number"),
        "created_at": record.get("created_at"),
    }


def verify_audit_chain(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Verify the append-only audit chain and report what (if anything) is wrong.

    The runtime has always *built* a hash chain; nothing ever *checked* it, which made the
    tamper-evidence a claim rather than a property. This is the check.

    These independent things must hold for every event, because each catches a different
    tampering (reason codes follow the vocabulary already used by
    ``runtime/protected_governance_state/recovery.py``, plus ``AUDIT_DECLARATION_MISMATCH``
    for the constant-by-construction fields the fingerprint payload does not cover):

    1. ``AUDIT_EVENT_HASH_MISMATCH`` — ``sha256(event_fingerprint_payload) == event_sha256``.
       Catches an edited payload.
    2. ``AUDIT_PAYLOAD_RECORD_MISMATCH`` — the payload's fields match the record they claim
       to fingerprint. This catches the easy attack that (1) alone **misses entirely**:
       change the visible record and leave the payload untouched, and a self-hash check
       still passes happily. (The existing verifier named above has this blind spot; it is
       deferred code, so this is a note, not a fix.)
    3. ``AUDIT_APPEND_ONLY_BOUNDARY_MISMATCH`` — the event still declares itself append-only.
    4. ``AUDIT_PREVIOUS_HASH_MISMATCH`` — each event's ``previous_event_sha256`` is the prior
       event's ``event_sha256``. Catches insertion, deletion, and reordering.

    Sequence numbers restart per run, so they are *not* a global ordering check; the hash
    chain is what spans runs. Front truncation IS detected (the first event must be a true
    genesis with a null previous hash). A **prefix** of a valid chain is itself valid, so a
    truncated TAIL still needs an external signal — that limit stands.

    Returns a report ``{intact, checked, breaks: [...], first_break_index}``. It never
    raises for a broken chain — a broken chain is a finding to report, not a crash — but the
    caller must treat ``intact: False`` as serious: the trail can no longer be trusted.
    """
    breaks: list[dict[str, Any]] = []
    previous_hash: str | None = None
    previous_id: str | None = None

    for index, record in enumerate(events):
        integrity_block = record.get("integrity", {}) if isinstance(record, Mapping) else {}
        payload = integrity_block.get("event_fingerprint_payload")
        claimed = integrity_block.get("event_sha256")
        event_id = record.get("audit_event_id") if isinstance(record, Mapping) else None

        def _break(check: str, detail: str) -> None:
            breaks.append({"index": index, "audit_event_id": event_id, "check": check, "detail": detail})

        if not isinstance(payload, Mapping) or not isinstance(claimed, str):
            _break("AUDIT_STRUCTURE_INVALID", "event has no integrity fingerprint payload or event_sha256")
            previous_hash, previous_id = claimed if isinstance(claimed, str) else None, event_id
            continue

        # 1. self-hash
        try:
            recomputed = integrity.sha256_value(dict(payload))
        except IntegrityError as exc:
            _break("AUDIT_EVENT_HASH_MISMATCH", f"payload is not fingerprintable: {exc}")
            recomputed = None
        if recomputed is not None and recomputed != claimed:
            _break("AUDIT_EVENT_HASH_MISMATCH",
                   "event_sha256 does not match its own fingerprint payload (payload edited)")

        # 2. the payload must describe the record it is embedded in
        view = _record_view(record)
        mismatched = sorted(k for k, v in view.items() if payload.get(k) != v)
        if mismatched:
            _break("AUDIT_PAYLOAD_RECORD_MISMATCH",
                   f"record fields disagree with the fingerprinted payload: {mismatched}")

        # 3. the event must still declare itself append-only. Flipping these flags is how a
        # record would announce it may be overwritten or deleted; the trail says otherwise.
        if (integrity_block.get("append_only") is not True
                or integrity_block.get("overwrite_allowed") is not False
                or integrity_block.get("delete_allowed") is not False):
            _break("AUDIT_APPEND_ONLY_BOUNDARY_MISMATCH",
                   "event no longer declares the append-only boundary (append_only/overwrite/delete)")

        # 3b. declaration invariants: these fields are constants by construction for every
        # event this runtime has ever built, so verification can require them even though
        # the fingerprint payload does not cover them. This closes the safety-relevant part
        # of the payload's blind spot — flipping runtime_effect flags or the record's schema
        # claim is now caught. (Actor role/assignment detail and sensitivity remain
        # uncovered until an audit_event.v0.2 extends the closed fingerprint payload — a
        # schema decision, not a code fix.)
        if record.get("runtime_effect") != audit_event_runtime_effect():
            _break("AUDIT_DECLARATION_MISMATCH",
                   "runtime_effect is not the canonical EVIDENCE_ONLY block (a grant flag was edited)")
        if record.get("schema_version") != AUDIT_EVENT_SCHEMA_VERSION:
            _break("AUDIT_DECLARATION_MISMATCH", "record schema_version was altered")
        if integrity_block.get("hash_schema") != FINGERPRINT_SCHEMA:
            _break("AUDIT_DECLARATION_MISMATCH", "integrity.hash_schema was altered")
        if record.get("event", {}).get("payload_ref") != record.get("subject", {}).get("subject_ref"):
            _break("AUDIT_DECLARATION_MISMATCH", "event.payload_ref no longer matches subject.subject_ref")

        # 4. linkage. The first event of the ledger must be a true genesis: a non-null
        # previous hash there dangles into deleted history, i.e. events were removed from
        # the FRONT — detectable for free, unlike tail truncation (see docstring).
        actual_previous = record.get("lineage", {}).get("previous_event_sha256")
        if index == 0 and actual_previous is not None:
            _break("AUDIT_PREVIOUS_HASH_MISMATCH",
                   "the first event carries a previous_event_sha256; events before the start "
                   "of the ledger were removed (front truncation)")
        if index > 0 and actual_previous != previous_hash:
            _break("AUDIT_PREVIOUS_HASH_MISMATCH",
                   f"previous_event_sha256 does not match the preceding event ({previous_id}); "
                   "an event was inserted, removed, or reordered")

        previous_hash = claimed
        previous_id = event_id

    return {
        "intact": not breaks,
        "checked": len(events),
        "breaks": breaks,
        "first_break_index": breaks[0]["index"] if breaks else None,
    }
