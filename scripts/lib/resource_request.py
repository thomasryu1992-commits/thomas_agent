
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

SCHEMA_VERSION = "resource_request_fingerprint_payload.v0.1"
AUTHORITY_ORDER = {f"P{index}": index for index in range(7)}

_FORBIDDEN_EXACT_KEYS = {
    "api_key", "api_secret", "private_key", "password", "passphrase",
    "access_token", "refresh_token", "secret", "token", "credential",
}
_FORBIDDEN_SUFFIXES = (
    "_api_key", "_api_secret", "_private_key", "_password", "_passphrase",
    "_access_token", "_refresh_token", "_secret", "_token", "_credential",
)


class ResourceRequestError(ValueError):
    pass


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ResourceRequestError(f"{path}: expected YAML mapping")
    return data


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ResourceRequestError(f"{path}: expected JSON object")
    return data


def _check_key(key: str, path: str) -> None:
    normalized = key.strip().lower()
    if normalized in _FORBIDDEN_EXACT_KEYS or normalized.endswith(_FORBIDDEN_SUFFIXES):
        raise ResourceRequestError(f"{path}: secret-bearing key is forbidden: {key}")


def _normalize(value: Any, path: str) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        raise ResourceRequestError(f"{path}: float values are forbidden; use normalized decimal strings")
    if isinstance(value, list):
        return [_normalize(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key in sorted(value):
            if not isinstance(key, str):
                raise ResourceRequestError(f"{path}: object keys must be strings")
            _check_key(key, path)
            result[key] = _normalize(value[key], f"{path}.{key}")
        return result
    raise ResourceRequestError(f"{path}: unsupported value type {type(value).__name__}")


def normalize_request_fingerprint_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ResourceRequestError("request fingerprint payload must be an object")
    normalized = _normalize(deepcopy(payload), "request_fingerprint_payload")
    if normalized.get("schema_version") != SCHEMA_VERSION:
        raise ResourceRequestError(f"schema_version must be {SCHEMA_VERSION}")
    if normalized.get("resource_type") not in {"TOOL", "PROGRAM"}:
        raise ResourceRequestError("resource_type must be TOOL or PROGRAM")
    for key in ["data_scope", "input_refs", "input_sha256"]:
        value = normalized.get(key)
        if not isinstance(value, list):
            raise ResourceRequestError(f"{key} must be a list")
        if len(value) != len(set(value)):
            raise ResourceRequestError(f"{key} must not contain duplicates")
    normalized["data_scope"] = sorted(normalized["data_scope"])
    return normalized


def compute_request_fingerprint(payload: dict[str, Any]) -> str:
    normalized = normalize_request_fingerprint_payload(payload)
    data = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def authority_sufficient(authority: dict[str, Any]) -> bool:
    required = max(
        AUTHORITY_ORDER[authority["request_required_permission_level"]],
        AUTHORITY_ORDER[authority["resource_required_permission_level"]],
    )
    effective = AUTHORITY_ORDER[authority["effective_permission_level"]]
    granted = AUTHORITY_ORDER[authority["assignment_granted_permission_level"]]
    ceiling = AUTHORITY_ORDER[authority["role_permission_ceiling"]]
    return required <= effective <= granted <= ceiling


def decimal_within(requested: str | None, remaining: str | None) -> bool:
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
        budget["requested_call_count"] <= budget["remaining_call_count"]
        and budget["requested_runtime_seconds"] <= budget["remaining_runtime_seconds"]
        and decimal_within(budget.get("requested_cost_decimal"), budget.get("remaining_cost_decimal"))
    )


def registry_index(registry: dict[str, Any], collection: str, id_key: str) -> dict[str, dict[str, Any]]:
    return {
        item[id_key]: item
        for item in registry.get(collection, [])
        if isinstance(item, dict) and isinstance(item.get(id_key), str)
    }


def resource_runtime_eligible(entry: dict[str, Any] | None) -> bool:
    return bool(
        entry
        and entry.get("status") == "active"
        and entry.get("enabled") is True
        and entry.get("runtime_implementation_available") is True
    )


def parse_role_front_matter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ResourceRequestError(f"{path}: role definition front matter is missing")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ResourceRequestError(f"{path}: role definition front matter is unterminated")
    data = yaml.safe_load(text[4:end])
    if not isinstance(data, dict):
        raise ResourceRequestError(f"{path}: role definition front matter must be a mapping")
    return data


def request_operation(record: dict[str, Any]) -> dict[str, Any]:
    if record.get("schema_version") == "tool_request.v0.1":
        return record["operation"]
    if record.get("schema_version") == "program_request.v0.1":
        return record["invocation"]
    raise ResourceRequestError("unsupported resource request schema")


def resource_identity(record: dict[str, Any]) -> tuple[str, str, str]:
    if record.get("schema_version") == "tool_request.v0.1":
        resource = record["resource"]
        return "TOOL", resource["tool_id"], resource["tool_version"]
    if record.get("schema_version") == "program_request.v0.1":
        resource = record["resource"]
        return "PROGRAM", resource["program_id"], resource["program_version"]
    raise ResourceRequestError("unsupported resource request schema")
