from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

FORBIDDEN_EXACT_KEYS = {
    "api_key",
    "api_secret",
    "private_key",
    "password",
    "passphrase",
    "access_token",
    "refresh_token",
    "secret",
    "token",
    "credential",
    "authorization",
    "cookie",
    "session_key",
    "bot_token",
    "webhook_secret",
}
FORBIDDEN_SUFFIXES = tuple(f"_{item}" for item in FORBIDDEN_EXACT_KEYS)


class IntegrityError(ValueError):
    pass


def scan_for_secret_bearing_keys(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise IntegrityError(f"{path}: object keys must be strings")
            normalized = key.strip().lower()
            if normalized in FORBIDDEN_EXACT_KEYS or normalized.endswith(FORBIDDEN_SUFFIXES):
                raise IntegrityError(f"{path}: secret-bearing key is forbidden: {key}")
            scan_for_secret_bearing_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            scan_for_secret_bearing_keys(child, f"{path}[{index}]")


def _normalize(value: Any, path: str = "$") -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        raise IntegrityError(f"{path}: float values are forbidden in fingerprint payloads")
    if isinstance(value, list):
        return [_normalize(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key in sorted(value):
            if not isinstance(key, str):
                raise IntegrityError(f"{path}: object keys must be strings")
            result[key] = _normalize(value[key], f"{path}.{key}")
        return result
    raise IntegrityError(f"{path}: unsupported type {type(value).__name__}")


def canonical_bytes(value: Any) -> bytes:
    scan_for_secret_bearing_keys(value)
    normalized = _normalize(deepcopy(value))
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_value(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def canonical_text_file_bytes(path: Path) -> bytes:
    """Return stable UTF-8 text bytes independent of checkout line endings."""
    raw = path.read_bytes()
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IntegrityError(f"{path}: record must be valid UTF-8") from exc
    return raw.replace(b"\r\n", b"\n")


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(canonical_text_file_bytes(path)).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def sha256_record(value: Any) -> str:
    """Hash a general record deterministically while allowing finite JSON numbers.

    Action/run fingerprint payloads still use ``sha256_value`` and reject floats.
    This helper is only for immutable record evidence such as Task and Agent Output snapshots.
    """
    scan_for_secret_bearing_keys(value)
    data = json.dumps(
        deepcopy(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def short_id(prefix: str, value: Any, length: int = 20) -> str:
    digest = hashlib.sha256(canonical_bytes(value)).hexdigest()[:length]
    return f"{prefix}_{digest}"
