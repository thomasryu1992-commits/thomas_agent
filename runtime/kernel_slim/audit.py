from __future__ import annotations

from hashlib import sha256
from json import dumps
from typing import Any, Mapping


def _hash(value: Mapping[str, Any]) -> str:
    return sha256(
        dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def build_audit_event(
    *,
    event_type: str,
    actor_id: str,
    task_id: str,
    payload: Mapping[str, Any],
    created_at: str,
) -> dict[str, Any]:
    event = {
        "event_type": event_type,
        "actor_id": actor_id,
        "task_id": task_id,
        "payload": dict(payload),
        "created_at": created_at,
    }
    return {**event, "event_sha256": _hash(event)}
