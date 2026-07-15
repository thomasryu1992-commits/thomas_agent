"""R2.5 Output Validation.

``validate_agent_output`` inspects an ``agent_output.v0.2`` and produces a
``validation_result.v0.1`` with result PASS / REVISE / BLOCK. Validation can only
report — it never grants permission, approval, authority, execution, or activation
(``permission_boundary`` and ``runtime_effect`` are all-false, REVIEW_ONLY). The
original output and the validation record are separate; the output is linked by a
fingerprint, not mutated.

Deterministic checks:
  - lineage: output task/trace/binding/assignment/role/actor match the Task+Assignment
    (mismatch -> BLOCK),
  - permission boundary: no permission-expansion request, no secret-bearing keys
    (violation -> BLOCK),
  - required sections: goal, summary, facts, and role key findings present (missing -> REVISE),
  - grounding: evidence present for facts (missing -> REVISE),
  - calibration: uncertainty or assumptions disclosed, i.e. not over-confident (missing -> REVISE).

The overall result is the most severe check outcome. A BLOCK/REVISE carries reasons for
the user; PASS means the output may be delivered.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from runtime.read_only_kernel import integrity, schema_validation
from runtime.read_only_kernel.integrity import IntegrityError
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from .errors import MvpRuntimeError

VALIDATION_RESULT_SCHEMA_VERSION = "validation_result.v0.1"
VALIDATOR_ACTOR_ID = "mvp.output_validator.automatic"

_SEVERITY = {"PASS": 0, "REVISE": 1, "BLOCK": 2}
_NEXT_STATE = {"PASS": "DELIVER_FINAL_RESPONSE", "REVISE": "REVISION_REQUIRED", "BLOCK": "BLOCKED_WITH_REASON"}


class ValidationError(MvpRuntimeError):
    """The validator could not produce a valid validation_result (internal fault)."""


def _check(check_id: str, result: str, evidence_refs: list[str], notes: str) -> dict[str, Any]:
    return {"check_id": check_id, "result": result, "evidence_refs": evidence_refs, "notes": notes}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def validate_agent_output(
    agent_output: Mapping[str, Any],
    task: Mapping[str, Any],
    assignment: Mapping[str, Any],
    *,
    now: str,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Validate an Agent Output. Returns a schema-valid ``validation_result.v0.1``.

    Never raises on a BLOCK/REVISE outcome (those are results, not errors); raises
    ``ValidationError`` only if it cannot build a valid validation record.
    """
    root = repo_root if repo_root is not None else _repo_root()
    ref = f"in_memory:{agent_output.get('agent_output_id')}"
    identity = task.get("identity", {})
    checks: list[dict[str, Any]] = []

    # Detect secret-bearing keys up front: sha256_record itself secret-scans, so a
    # secret-bearing output cannot be fingerprinted normally — withhold its content.
    secret_bearing = False
    try:
        integrity.scan_for_secret_bearing_keys(dict(agent_output))
    except IntegrityError:
        secret_bearing = True
    if secret_bearing:
        output_fingerprint = integrity.sha256_record(
            {"agent_output_id": agent_output.get("agent_output_id"), "content_withheld": "secret_bearing"}
        )
    else:
        output_fingerprint = integrity.sha256_record(dict(agent_output))

    # 1) Lineage — the output must belong to exactly this Task/Assignment lineage.
    lineage_ok = (
        agent_output.get("task_id") == identity.get("task_id")
        and agent_output.get("trace_id") == identity.get("trace_id")
        and agent_output.get("core_context_binding_id") == task.get("context", {}).get("core_context_binding_id")
        and agent_output.get("assignment_id") == assignment.get("assignment_id")
        and agent_output.get("role_id") == assignment.get("role_id")
        and agent_output.get("actor_instance_id") == assignment.get("actor_instance_id")
    )
    checks.append(_check(
        "lineage_consistency", "PASS" if lineage_ok else "BLOCK", [ref],
        "Output lineage matches Task, Binding, Assignment, Role, and Actor." if lineage_ok
        else "Output lineage does not match the Task/Assignment.",
    ))

    # 2) Permission boundary — no permission expansion, no secret-bearing keys.
    boundary_result, boundary_note = "PASS", "No permission expansion or secret-bearing keys."
    if agent_output.get("permission_request_refs"):
        boundary_result, boundary_note = "BLOCK", "Output attempts to request/expand permission."
    elif secret_bearing:
        boundary_result, boundary_note = "BLOCK", "Secret-bearing key present in output."
    checks.append(_check("permission_boundary", boundary_result, [ref], boundary_note))

    # 3) Required sections present.
    rso = agent_output.get("role_specific_output", {})
    sections_ok = bool(
        (agent_output.get("goal") or "").strip()
        and (agent_output.get("summary") or "").strip()
        and agent_output.get("facts")
        and rso.get("key_findings")
    )
    checks.append(_check(
        "required_sections", "PASS" if sections_ok else "REVISE", [ref],
        "Goal, summary, facts, and key findings are present." if sections_ok
        else "Missing required sections (goal / summary / facts / key_findings).",
    ))

    # 4) Grounding — facts carry evidence and there is at least one evidence entry.
    facts = agent_output.get("facts") or []
    grounded = bool(agent_output.get("evidence")) and all(f.get("evidence_refs") for f in facts) and bool(facts)
    checks.append(_check(
        "evidence_grounding", "PASS" if grounded else "REVISE", [ref],
        "Facts are grounded in evidence." if grounded else "Insufficient evidence/grounding for the findings.",
    ))

    # 5) Calibration — uncertainty or assumptions disclosed (not over-confident).
    calibrated = bool(agent_output.get("uncertainty")) or bool(agent_output.get("assumptions"))
    checks.append(_check(
        "calibration", "PASS" if calibrated else "REVISE", [ref],
        "Uncertainty/assumptions disclosed." if calibrated else "No uncertainty or assumptions disclosed (over-confident).",
    ))

    overall = max((c["result"] for c in checks), key=lambda r: _SEVERITY[r])
    result_reasons = [c["notes"] for c in checks if c["result"] != "PASS"] or [
        "All automatic output checks passed."
    ]

    risk_level = task.get("classification", {}).get("risk_level")
    independent_required = risk_level in {"ORANGE", "RED"}

    seed = {
        "agent_output_id": agent_output.get("agent_output_id"),
        "task_id": identity.get("task_id"),
        "task_revision": identity.get("task_revision"),
    }
    record: dict[str, Any] = {
        "schema_version": VALIDATION_RESULT_SCHEMA_VERSION,
        "validation_result_id": integrity.short_id("valres", seed),
        "trace_id": identity.get("trace_id"),
        "task_id": identity.get("task_id"),
        "task_revision": identity.get("task_revision"),
        "core_context_binding_id": task.get("context", {}).get("core_context_binding_id"),
        "subject": {
            "subject_type": "AGENT_OUTPUT",
            "subject_id": agent_output.get("agent_output_id"),
            "subject_ref": ref,
            "subject_fingerprint": output_fingerprint,
            "subject_created_by_actor_id": assignment.get("actor_instance_id"),
        },
        "validator": {
            "validator_type": "AUTOMATIC",
            "validator_actor_id": VALIDATOR_ACTOR_ID,
            "validator_role_id": None,
            "validator_role_version": None,
            "validator_execution_context_id": integrity.short_id("valctx", seed),
            "independent_required": independent_required,
            "independence_verified": False,
        },
        "validation": {
            "validation_mode": "AUTOMATIC",
            "result": overall,
            "acceptance_criteria": [
                "output_lineage_consistent",
                "read_only_permission_boundary_preserved",
                "required_sections_present",
                "findings_grounded_in_evidence",
                "uncertainty_disclosed",
            ],
            "rejection_criteria": [
                "lineage_mismatch",
                "permission_expansion_or_secret_exposure",
                "ungrounded_or_overconfident_output",
            ],
            "checks": checks,
            "result_reasons": result_reasons,
            "recommended_next_state": _NEXT_STATE[overall],
        },
        "findings": {
            "facts": ["The Agent Output was assessed against automatic output-quality checks."],
            "risks": ["Automatic validation is not independent domain validation."],
            "omissions": ["Live external facts and citations were not verified."],
            "assumptions": ["The Agent Output is the exact artifact to validate."],
            "limitations": ["Content-level sensitivity and hallucination are not fully detectable automatically."],
        },
        "evidence_refs": [ref],
        "permission_boundary": {
            "grants_permission": False,
            "grants_approval": False,
            "grants_authority": False,
            "grants_execution": False,
            "grants_activation": False,
            "mutates_subject": False,
        },
        "runtime_effect": {
            "mode": "REVIEW_ONLY",
            "grants_permission": False,
            "grants_approval": False,
            "grants_authority": False,
            "grants_execution": False,
            "grants_activation": False,
            "executor_handoff_allowed": False,
            "side_effects_allowed": False,
            "runtime_mutation_allowed": False,
        },
        "lifecycle": {"created_at": now, "supersedes": []},
        "audit_refs": [],
    }

    schema_path = root / "schemas" / f"{VALIDATION_RESULT_SCHEMA_VERSION}.schema.json"
    try:
        schema_validation.validate_against_schema(record, schema_path, "validation_result")
    except RuntimeSchemaError as exc:
        raise ValidationError("VALIDATION_RESULT_INVALID", str(exc)) from exc
    return record
