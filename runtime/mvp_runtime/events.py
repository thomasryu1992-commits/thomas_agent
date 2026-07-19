"""Self-hashed standalone ledger events (control, scheduler, memory retention, run block).

Four builders each hand-rolled the same build-dict-then-stamp pattern — and one
(``pipeline._block_record``) had already forgotten the integrity stamp. One construction
site means the next standalone event type cannot forget it either.
"""

from __future__ import annotations

from typing import Any

from runtime.read_only_kernel import integrity


def stamped_event(record_type: str, **fields: Any) -> dict[str, Any]:
    """A standalone ledger event: ``record_type`` + fields + a tamper-evident self-hash.

    The hash covers everything but the ``integrity`` block itself; on-disk key order is
    canonicalized by the JSONL writer, so field order here is presentation only.
    """
    event: dict[str, Any] = {"record_type": record_type, **fields}
    event["integrity"] = {"event_sha256": integrity.sha256_record(dict(event))}
    return event
