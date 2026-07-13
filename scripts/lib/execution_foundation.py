from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any

EXECUTION_REQUEST_FINGERPRINT_SCHEMA = "execution_request_fingerprint_payload.v0.1"
AUDIT_EVENT_FINGERPRINT_SCHEMA = "audit_event_fingerprint_payload.v0.1"
AUTHORITY_ORDER = {f"P{index}": index for index in range(7)}

_FORBIDDEN_EXACT_KEYS = {
    "api_key", "api_secret", "private_key", "password", "passphrase",
    "access_token", "refresh_token", "secret", "token", "credential",
    "authorization", "cookie", "session_key",
}
_FORBIDDEN_SUFFIXES = (
    "_api_key", "_api_secret", "_private_key", "_password", "_passphrase",
    "_access_token", "_refresh_token", "_secret", "_token", "_credential",
    "_authorization", "_cookie", "_session_key",
)


class ExecutionFoundationError(ValueError):
    pass


def _check_key(key: str, path: str) -> None:
    normalized = key.strip().lower()
    if normalized in _FORBIDDEN_EXACT_KEYS or normalized.endswith(_FORBIDDEN_SUFFIXES):
        raise ExecutionFoundationError(f"{path}: secret-bearing key is forbidden: {key}")


def _normalize(value: Any, path: str) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        raise ExecutionFoundationError(
            f"{path}: float values are forbidden; use normalized decimal strings"
        )
    if isinstance(value, list):
        return [_normalize(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key in sorted(value):
            if not isinstance(key, str):
                raise ExecutionFoundationError(f"{path}: object keys must be strings")
            _check_key(key, path)
            result[key] = _normalize(value[key], f"{path}.{key}")
        return result
    raise ExecutionFoundationError(f"{path}: unsupported value type {type(value).__name__}")


def _canonical_bytes(payload: dict[str, Any], expected_schema: str, path: str) -> bytes:
    if not isinstance(payload, dict):
        raise ExecutionFoundationError(f"{path} must be an object")
    normalized = _normalize(deepcopy(payload), path)
    if normalized.get("schema_version") != expected_schema:
        raise ExecutionFoundationError(f"schema_version must be {expected_schema}")

    for key in ["data_scope", "reason_codes", "evidence_refs", "related_record_refs", "parent_audit_event_ids"]:
        if key in normalized:
            value = normalized[key]
            if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
                raise ExecutionFoundationError(f"{path}.{key} must be a list of non-empty strings")
            if len(value) != len(set(value)):
                raise ExecutionFoundationError(f"{path}.{key} must not contain duplicates")
            normalized[key] = sorted(value)

    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def compute_execution_request_fingerprint(payload: dict[str, Any]) -> str:
    data = _canonical_bytes(
        payload,
        EXECUTION_REQUEST_FINGERPRINT_SCHEMA,
        "execution_request_fingerprint_payload",
    )
    return "sha256:" + hashlib.sha256(data).hexdigest()


def compute_audit_event_sha256(payload: dict[str, Any]) -> str:
    data = _canonical_bytes(
        payload,
        AUDIT_EVENT_FINGERPRINT_SCHEMA,
        "audit_event_fingerprint_payload",
    )
    return "sha256:" + hashlib.sha256(data).hexdigest()


def authority_sufficient(authority: dict[str, Any]) -> bool:
    try:
        required = AUTHORITY_ORDER[authority["required_permission_level"]]
        effective = AUTHORITY_ORDER[authority["effective_permission_level"]]
        granted = AUTHORITY_ORDER[authority["assignment_granted_permission_level"]]
        ceiling = AUTHORITY_ORDER[authority["role_permission_ceiling"]]
    except KeyError as exc:
        raise ExecutionFoundationError(f"invalid Authority record: missing or unknown {exc}") from exc
    return required <= effective <= granted <= ceiling


def _decimal_within(requested: str | None, remaining: str | None) -> bool:
    if requested is None and remaining is None:
        return True
    if requested is None or remaining is None:
        return False
    try:
        return Decimal(requested) <= Decimal(remaining)
    except InvalidOperation:
        return False


def budget_within(budget: dict[str, Any]) -> bool:
    return (
        budget["requested_runtime_seconds"] <= budget["remaining_runtime_seconds"]
        and _decimal_within(
            budget.get("requested_cost_decimal"),
            budget.get("remaining_cost_decimal"),
        )
    )


def requester_ref(requested_by: dict[str, Any]) -> str:
    return f"{requested_by['actor_type']}:{requested_by['actor_id']}"


def actor_ref(actor: dict[str, Any]) -> str:
    base = f"{actor['actor_type']}:{actor['actor_id']}"
    if actor.get("role_id"):
        base += f"@{actor['role_id']}"
    if actor.get("assignment_id"):
        base += f"#{actor['assignment_id']}"
    return base
