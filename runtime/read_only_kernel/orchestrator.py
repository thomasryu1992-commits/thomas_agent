from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .assembler import assemble_completed_run
from .audit import build_transition_audit, build_validation_audit
from .constants import KERNEL_VERSION, REPLAY_TRANSITIONS
from .integrity import sha256_record, sha256_value, short_id
from .policy import adapt_policy
from .preflight import run_preflight
from .router import select_route
from .types import ReadCounter
from .validation import build_validation_result
from .worker import WORKER_VERSION
from .worker_port import invoke_worker


def run_loaded_replay(
    *,
    repo_root: Path,
    now: str,
    read_counter: ReadCounter,
    bundle: dict[str, Any],
    records: dict[str, dict[str, Any]],
    actual_hashes: dict[str, str],
    bundle_path: Path,
) -> dict[str, Any]:
    preflight = run_preflight(
        repo_root=repo_root,
        bundle=bundle,
        records=records,
        read_counter=read_counter,
    )
    policy = adapt_policy(preflight)
    route = select_route(preflight)

    output = invoke_worker(
        route=route,
        task=preflight.task,
        assignment=preflight.assignment,
        created_at=now,
    )
    output["status"] = "final"
    output_fingerprint = sha256_record(output)

    validation_result = build_validation_result(
        output=output,
        output_fingerprint=output_fingerprint,
        task=preflight.task,
        assignment=preflight.assignment,
        now=now,
    )
    validation_fingerprint = sha256_value(validation_result)

    transitions: list[dict[str, Any]] = []
    audit_events: list[dict[str, Any]] = []
    previous_hash: str | None = None
    previous_audit_id: str | None = None
    for sequence, (from_state, to_state) in enumerate(REPLAY_TRANSITIONS, start=1):
        event = build_transition_audit(
            task=preflight.task,
            assignment=preflight.assignment,
            from_state=from_state,
            to_state=to_state,
            sequence=sequence,
            previous_hash=previous_hash,
            previous_audit_id=previous_audit_id,
            now=now,
        )
        previous_hash = event["integrity"]["event_sha256"]
        previous_audit_id = event["audit_event_id"]
        audit_events.append(event)
        transitions.append(
            {
                "from": from_state,
                "to": to_state,
                "audit_event_id": event["audit_event_id"],
                "audit_event_sha256": previous_hash,
            }
        )

    validation_audit = build_validation_audit(
        task=preflight.task,
        assignment=preflight.assignment,
        validation_result=validation_result,
        validation_fingerprint=validation_fingerprint,
        sequence=len(audit_events) + 1,
        previous_hash=previous_hash,
        previous_audit_id=previous_audit_id,
        now=now,
    )
    audit_events.append(validation_audit)

    final_task = deepcopy(preflight.task)
    run_seed = {
        "bundle_id": bundle["bundle_id"],
        "task_id": preflight.task_id,
        "task_revision": preflight.task_revision,
        "kernel_version": KERNEL_VERSION,
        "worker_version": WORKER_VERSION,
    }
    run_id = short_id("rorun", run_seed)
    run_payload = {
        "schema_version": "read_only_runtime_run_fingerprint_payload.v0.1",
        "run_id": run_id,
        "bundle_id": bundle["bundle_id"],
        "bundle_sha256": sha256_value(bundle),
        "task_id": preflight.task_id,
        "task_revision": preflight.task_revision,
        "core_context_binding_id": preflight.core_context_binding_id,
        "assignment_id": preflight.assignment["assignment_id"],
        "agent_output_sha256": output_fingerprint,
        "validation_result_sha256": validation_fingerprint,
        "final_task_sha256": sha256_record(final_task),
        "audit_event_sha256s": [item["integrity"]["event_sha256"] for item in audit_events],
        "created_at": now,
    }

    return assemble_completed_run(
        repo_root=repo_root,
        now=now,
        filesystem_read_count=read_counter.value,
        bundle=bundle,
        actual_hashes=actual_hashes,
        bundle_path=bundle_path,
        preflight_checks=preflight.checks,
        task=preflight.task,
        binding=preflight.binding,
        assignment=preflight.assignment,
        authority=policy.authority,
        permission=policy.permission,
        route=route,
        output=output,
        output_fingerprint=output_fingerprint,
        validation_result=validation_result,
        validation_fingerprint=validation_fingerprint,
        final_task=final_task,
        audit_events=audit_events,
        transitions=transitions,
        run_id=run_id,
        run_payload=run_payload,
        task_id=preflight.task_id,
        task_revision=preflight.task_revision,
        trace_id=preflight.trace_id,
        core_context_binding_id=preflight.core_context_binding_id,
    )
