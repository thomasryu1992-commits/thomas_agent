#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

FINGERPRINT_SCHEMA_VERSION = "action_fingerprint_payload.v0.1"

_FORBIDDEN_EXACT_KEYS = {
    "api_key",
    "api_secret",
    "private_key",
    "password",
    "passphrase",
    "access_token",
    "refresh_token",
    "secret",
    "token",
}
_FORBIDDEN_SUFFIXES = (
    "_api_key",
    "_api_secret",
    "_private_key",
    "_password",
    "_passphrase",
    "_access_token",
    "_refresh_token",
    "_secret",
)


class FingerprintPayloadError(ValueError):
    """Raised when a payload cannot be safely canonicalized."""


def _check_key(key: str, path: str) -> None:
    normalized = key.strip().lower()
    if normalized in _FORBIDDEN_EXACT_KEYS or normalized.endswith(_FORBIDDEN_SUFFIXES):
        raise FingerprintPayloadError(
            f"{path}: secret-bearing key is forbidden in fingerprint payload: {key}"
        )


def _normalize_json_value(value: Any, path: str) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        raise FingerprintPayloadError(
            f"{path}: float values are forbidden; use a normalized decimal string"
        )
    if isinstance(value, list):
        return [
            _normalize_json_value(item, f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key in sorted(value):
            if not isinstance(key, str):
                raise FingerprintPayloadError(f"{path}: object keys must be strings")
            _check_key(key, path)
            normalized[key] = _normalize_json_value(value[key], f"{path}.{key}")
        return normalized
    raise FingerprintPayloadError(
        f"{path}: unsupported value type {type(value).__name__}"
    )


def normalize_fingerprint_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise FingerprintPayloadError("fingerprint payload must be an object")

    normalized = _normalize_json_value(deepcopy(payload), "fingerprint_payload")
    if normalized.get("schema_version") != FINGERPRINT_SCHEMA_VERSION:
        raise FingerprintPayloadError(
            f"schema_version must be {FINGERPRINT_SCHEMA_VERSION}"
        )

    permission_scope = normalized.get("permission_scope")
    if not isinstance(permission_scope, str) or not permission_scope:
        raise FingerprintPayloadError(
            "permission_scope must be a non-empty string"
        )

    data_scope = normalized.get("data_scope")
    if not isinstance(data_scope, list) or not all(
        isinstance(item, str) and item for item in data_scope
    ):
        raise FingerprintPayloadError("data_scope must be a list of non-empty strings")
    if len(data_scope) != len(set(data_scope)):
        raise FingerprintPayloadError("data_scope must not contain duplicates")
    normalized["data_scope"] = sorted(data_scope)

    amount = normalized.get("amount_decimal")
    currency = normalized.get("currency")
    if (amount is None) != (currency is None):
        raise FingerprintPayloadError(
            "amount_decimal and currency must either both be present or both be null"
        )

    return normalized


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    normalized = normalize_fingerprint_payload(payload)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def compute_action_fingerprint(payload: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
