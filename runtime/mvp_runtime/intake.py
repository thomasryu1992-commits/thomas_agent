"""R2.1 Task Intake.

Turns a single user request into a schema-valid ``task.v0.3`` record in the
``RECEIVED`` lifecycle state. This is the first live step of the MVP runtime.
Side effects are limited to **read-only** schema-file reads during validation:
it constructs, secret-scans, and schema-validates a record, then returns it. It
performs no external writes, no network I/O, no model invocation, and does not
classify, bind a Core Release, or route (those are R2.2/R2.3).

Responsibility boundary (kept deliberately thin):
  - Intake fills identity / source / request / scope / minimal context / audit.
  - It leaves classification UNCLASSIFIED, permission NOT_EVALUATED, routing
    UNASSIGNED, authority null, and ``core_context_binding_id`` null. Those are
    filled by the Prime planner (R2.2) and routing (R2.3).

Kernel components are reused as libraries (never modified):
  - ``integrity.short_id`` for deterministic ids (same input+time -> same ids,
    which keeps recorded-replay stable),
  - ``integrity.scan_for_secret_bearing_keys`` to fail closed on secret keys,
  - ``schema_validation.validate_against_schema`` for the closed task.v0.3 schema.

Note: importing these leaf modules currently also loads the kernel package
``__init__`` (which eagerly imports the replay engine). The imported modules are
pure function/class definitions with no import-time side effects, so this is
safe; making the kernel package import lazy is a tracked follow-up.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from runtime.read_only_kernel import integrity, schema_validation
from runtime.read_only_kernel.integrity import IntegrityError
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from . import timeutil
from .budgets import default_execution_budget
from .errors import TaskIntakeBlocked
from .paths import repo_root as _repo_root

TASK_SCHEMA_VERSION = "task.v0.3"

# Bounds. The request feeds an LLM prompt and audit records later, so cap it to
# keep token/cost budgets and log volume sane, and reject non-printable control
# characters (NUL, ANSI escapes, BEL) that could corrupt logs or downstream sinks.
MAX_REQUEST_CHARS = 20000
MAX_FIELD_CHARS = 2000
MAX_LIST_ITEMS = 64
_ALLOWED_CONTROL_CHARS = {"\t", "\n", "\r"}

_ALLOWED_CHANNELS = {"telegram", "scheduler", "agent", "system", "api", "manual"}
_ALLOWED_REQUESTER_TYPES = {"real_thomas", "thomas_prime", "scheduler", "agent", "system"}
_ALLOWED_SENSITIVITY = {"PUBLIC", "INTERNAL", "PRIVATE", "SENSITIVE", "RESTRICTED"}

# Minimal set of active Core rules loaded at intake for the MVP business-analysis
# use case. Membership is only *finally* validated once a Core Context Binding is
# created (R2.2/R2.3); at RECEIVED this is the loaded-for-this-task set.
DEFAULT_ACTIVE_CORE_RULE_IDS = ("MVP_RULE_005", "MVP_RULE_007", "MVP_RULE_008")

# Read-only by construction: the MVP forbids external action, so intake stamps it
# as a task constraint rather than trusting downstream stages to remember.
DEFAULT_CONSTRAINTS = ("no_external_action",)
DEFAULT_SUCCESS_CONDITIONS = ("primary_objective_addressed",)
DEFAULT_EXPECTED_OUTPUTS = ("structured_response",)
DEFAULT_ACCEPTANCE_CRITERIA = ("objective_met", "output_contract_valid")
DEFAULT_REJECTION_CRITERIA = ("authority_or_permission_violation",)


def _reject_control_chars(value: str, field: str) -> None:
    for ch in value:
        code = ord(ch)
        if (code < 0x20 and ch not in _ALLOWED_CONTROL_CHARS) or code == 0x7F:
            raise TaskIntakeBlocked(
                "CONTROL_CHARS", f"{field} contains a disallowed control character (U+{code:04X})"
            )


def _require_text(value: Any, reason_code: str, field: str, *, max_len: int = MAX_FIELD_CHARS) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TaskIntakeBlocked(reason_code, f"{field} must be a non-empty string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise TaskIntakeBlocked("INVALID_ENCODING", f"{field} must be valid UTF-8 text") from exc
    if len(value) > max_len:
        raise TaskIntakeBlocked("TOO_LONG", f"{field} exceeds {max_len} characters")
    _reject_control_chars(value, field)
    return value


def _validate_timestamp(value: str) -> str:
    """Reject anything that is not an RFC3339 date-time. The schema's date-time
    format check is a no-op without an optional dependency, so validate here."""
    if not isinstance(value, str):
        raise TaskIntakeBlocked("INVALID_TIMESTAMP", "now must be an RFC3339 date-time string")
    try:
        timeutil.parse_iso(value)
    except ValueError as exc:
        raise TaskIntakeBlocked("INVALID_TIMESTAMP", f"now is not a valid RFC3339 date-time: {value!r}") from exc
    return value


def _clean_str_list(value: Sequence[str] | None, default: Sequence[str], field: str) -> list[str]:
    if value is not None and isinstance(value, (str, bytes)):
        raise TaskIntakeBlocked("INVALID_LIST", f"{field} must be a list of strings, not a single string")
    items = list(default) if value is None else list(value)
    if len(items) > MAX_LIST_ITEMS:
        raise TaskIntakeBlocked("TOO_MANY_ITEMS", f"{field} exceeds {MAX_LIST_ITEMS} entries")
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise TaskIntakeBlocked("INVALID_LIST_ITEM", f"{field} entries must be non-empty strings")
        try:
            item.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise TaskIntakeBlocked("INVALID_ENCODING", f"{field} entries must be valid UTF-8 text") from exc
        if len(item) > MAX_FIELD_CHARS:
            raise TaskIntakeBlocked("TOO_LONG", f"{field} entry exceeds {MAX_FIELD_CHARS} characters")
        _reject_control_chars(item, field)
    return items


def build_task(
    raw_request: str,
    *,
    requester_id: str = "thomas",
    requester_type: str = "real_thomas",
    channel: str = "api",
    source_ref: str | None = None,
    authenticated: bool = True,
    normalized_goal: str | None = None,
    primary_objective: str | None = None,
    success_conditions: Sequence[str] | None = None,
    constraints: Sequence[str] | None = None,
    exclusions: Sequence[str] | None = None,
    expected_outputs: Sequence[str] | None = None,
    active_core_rule_ids: Sequence[str] | None = None,
    data_sensitivity: str = "INTERNAL",
    now: str | None = None,
    created_by: str | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Build and validate a RECEIVED ``task.v0.3`` record. Fail-closed.

    Raises ``TaskIntakeBlocked`` on any missing/invalid input, a secret-bearing
    key, or a schema violation. Never returns an unvalidated record.
    """
    raw_request = _require_text(raw_request, "EMPTY_REQUEST", "raw_request", max_len=MAX_REQUEST_CHARS)
    requester_id = _require_text(requester_id, "MISSING_REQUESTER", "requester_id", max_len=200)

    if channel not in _ALLOWED_CHANNELS:
        raise TaskIntakeBlocked("INVALID_CHANNEL", f"channel must be one of {sorted(_ALLOWED_CHANNELS)}")
    if requester_type not in _ALLOWED_REQUESTER_TYPES:
        raise TaskIntakeBlocked("INVALID_REQUESTER_TYPE", f"requester_type must be one of {sorted(_ALLOWED_REQUESTER_TYPES)}")
    if data_sensitivity not in _ALLOWED_SENSITIVITY:
        raise TaskIntakeBlocked("INVALID_SENSITIVITY", f"data_sensitivity must be one of {sorted(_ALLOWED_SENSITIVITY)}")
    if not isinstance(authenticated, bool):
        raise TaskIntakeBlocked("INVALID_AUTHENTICATED", "authenticated must be a bool (no coercion)")

    now_str = _validate_timestamp(now) if now is not None else timeutil.utc_now_iso()
    goal = normalized_goal.strip() if isinstance(normalized_goal, str) and normalized_goal.strip() else raw_request.strip()
    objective = primary_objective.strip() if isinstance(primary_objective, str) and primary_objective.strip() else goal

    # Route rule ids through the same per-item validation as every other list so an
    # unhashable/invalid element fails closed with a precise code instead of a raw
    # TypeError at set() or a late generic SCHEMA_INVALID.
    rule_ids = _clean_str_list(active_core_rule_ids, DEFAULT_ACTIVE_CORE_RULE_IDS, "active_core_rule_ids")
    if not rule_ids:
        raise TaskIntakeBlocked("MISSING_CORE_RULES", "at least one active_core_rule_id is required")
    if len(set(rule_ids)) != len(rule_ids):
        raise TaskIntakeBlocked("DUPLICATE_CORE_RULES", "active_core_rule_ids must be unique")

    success = _clean_str_list(success_conditions, DEFAULT_SUCCESS_CONDITIONS, "success_conditions")
    outputs = _clean_str_list(expected_outputs, DEFAULT_EXPECTED_OUTPUTS, "expected_outputs")
    constraint_list = _clean_str_list(constraints, DEFAULT_CONSTRAINTS, "constraints")
    exclusion_list = _clean_str_list(exclusions, (), "exclusions")

    seed = {
        "raw_request": raw_request,
        "received_at": now_str,
        "requester_id": requester_id,
        "channel": channel,
        "task_revision": 1,
    }
    task_id = integrity.short_id("task", seed)
    trace_id = integrity.short_id("trace", seed)
    audit_id = integrity.short_id("audit", {**seed, "kind": "received"})

    task: dict[str, Any] = {
        "schema_version": TASK_SCHEMA_VERSION,
        "identity": {
            "task_id": task_id,
            "trace_id": trace_id,
            "root_task_id": task_id,
            "parent_task_id": None,
            "task_revision": 1,
        },
        "source": {
            "channel": channel,
            "source_ref": source_ref if (isinstance(source_ref, str) and source_ref.strip()) else f"{channel}:intake",
            "requester": {
                "requester_type": requester_type,
                "requester_id": requester_id,
                "authenticated": authenticated,
            },
        },
        "request": {
            "raw_request": raw_request,
            "normalized_goal": goal,
            "received_at": now_str,
        },
        "scope": {
            "primary_objective": objective,
            "success_conditions": success,
            "constraints": constraint_list,
            "exclusions": exclusion_list,
            "expected_outputs": outputs,
        },
        "classification": {
            "classification_status": "UNCLASSIFIED",
            "execution_mode": None,
            "complexity": None,
            "priority": "NORMAL",
            "risk_level": None,
            "classification_reasons": [],
        },
        "authority": {
            "required_permission_level": None,
            "authority_reason": None,
        },
        "permission": {
            "evaluation_status": "NOT_EVALUATED",
            "permission_decision": None,
            "permission_decision_ref": None,
            "approval_state": "NOT_REQUIRED",
            "approval_id": None,
            "action_fingerprint": None,
        },
        "routing": {
            "required_capabilities": [],
            "selected_route": "UNASSIGNED",
            "assigned_role_ids": [],
            "assigned_actor_ids": [],
            "role_assignment_ids": [],
            "program_request_ids": [],
            "tool_request_ids": [],
        },
        "context": {
            "core_context_binding_id": None,
            "input_refs": ["task.request.raw_request"],
            "context_refs": [],
            "active_core_rule_ids": rule_ids,
            "memory_refs": [],
            "data_sensitivity": data_sensitivity,
        },
        "validation": {
            "mode": "AUTOMATIC",
            "status": "NOT_STARTED",
            "acceptance_criteria": list(DEFAULT_ACCEPTANCE_CRITERIA),
            "rejection_criteria": list(DEFAULT_REJECTION_CRITERIA),
            "validation_output_refs": [],
        },
        "execution_budget": default_execution_budget(),
        "results": {
            "agent_output_refs": [],
            "program_result_refs": [],
            "validation_output_refs": [],
            "final_output_ref": None,
            "partial_completion": {
                "is_partial": False,
                "completed_scope": [],
                "missing_scope": [],
                "impact": [],
                "next_action": None,
            },
        },
        "lifecycle": {
            "status": "RECEIVED",
            "previous_status": None,
            "status_reason": "Task received; Core Binding not created yet.",
            "blocked_reason": None,
            "pause_resume_target": None,
            "transition_event_ref": audit_id,
            "status_entered_at": now_str,
        },
        "audit": {
            "created_by": created_by.strip() if (isinstance(created_by, str) and created_by.strip()) else f"mvp_intake:{channel}",
            "created_at": now_str,
            "updated_at": now_str,
            "audit_refs": [audit_id],
        },
    }

    # Fail closed on secret-bearing keys before the record can travel anywhere.
    try:
        integrity.scan_for_secret_bearing_keys(task)
    except IntegrityError as exc:
        raise TaskIntakeBlocked("SECRET_BEARING_KEY", str(exc)) from exc

    # Closed-schema validation is authoritative: reject anything the contract rejects.
    root = repo_root if repo_root is not None else _repo_root()
    schema_path = root / "schemas" / f"{TASK_SCHEMA_VERSION}.schema.json"
    if not schema_path.is_file():
        # Misconfiguration (bad repo_root / missing schema), not an invalid record.
        raise TaskIntakeBlocked("SCHEMA_UNAVAILABLE", f"task schema not found at {schema_path.as_posix()}")
    try:
        schema_validation.validate_against_schema(task, schema_path, "task_intake")
    except RuntimeSchemaError as exc:
        raise TaskIntakeBlocked("SCHEMA_INVALID", str(exc)) from exc

    return task
