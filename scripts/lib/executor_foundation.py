from __future__ import annotations

import hashlib
import json
from typing import Any


class ExecutorFoundationError(ValueError):
    pass


def canonical_sha256(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def compute_rollback_plan_fingerprint(payload: dict[str, Any]) -> str:
    required = {
        "execution_request_id", "execution_request_fingerprint", "action_fingerprint",
        "rollback_required", "risk_class", "reversibility", "checkpoint_refs",
        "rollback_steps", "recovery_steps", "recovery_owner",
        "recovery_time_objective_seconds", "max_data_loss_seconds",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise ExecutorFoundationError(f"rollback payload missing fields: {missing}")
    return canonical_sha256(payload)


def summarize_checks(checks: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    passed = [item["check_id"] for item in checks if item.get("result") == "PASS"]
    failed = [item["check_id"] for item in checks if item.get("result") != "PASS"]
    return passed, failed


def ensure_no_secret_keys(value: Any, path: str = "root") -> None:
    forbidden = {
        "api_key", "api_secret", "private_key", "password", "passphrase",
        "secret_value", "secret_file", "token_value", "credential_value",
    }
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in forbidden:
                raise ExecutorFoundationError(f"secret-bearing key prohibited at {path}.{key}")
            ensure_no_secret_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            ensure_no_secret_keys(child, f"{path}[{index}]")
