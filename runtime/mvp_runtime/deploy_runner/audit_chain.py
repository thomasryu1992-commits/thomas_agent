"""The actor's effect audit: one hash chain, the authoritative record of what the runner did.

C2 specification §7.1a–§7.4. Each row has every field of ``deploy_audit.v0.1`` (unset ones are
``null``), a ``seq`` counting up from 1, the previous row's ``event_sha256``, and its own
``event_sha256`` — the kernel hash of the row without that field. Rows are validated against the
schema before they are written; a row that does not validate is never written
(``AUDIT_WRITE_FAILED``), and neither is one whose write is short or cannot be ``fsync``-ed.

Reading verifies the whole chain. A torn line, an edit, a gap or a reordering is
``AUDIT_CHAIN_BROKEN``: the startup check latches on it. The chain detects partial changes by an
actor that cannot recompute it; a full rewrite by root or a compromised actor is not detectable
locally (§7.5) — only an external anchor can show it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from runtime.read_only_kernel import integrity
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from .. import schema_cache
from .approval_verify import SCHEMAS_DIR
from .private_files import append_row, lock_exclusive, open_private, parse_rows, read_all
from .reasons import RunnerRefused

AUDIT_FILE = "audit.jsonl"
SCHEMA_FILE = "deploy_audit.v0.1.schema.json"

FIELDS = ("request_id", "action", "caller_uid", "client_id", "gate_intake_seq", "approval_ref",
          "trust_config_sha256", "signer_key_fingerprint", "target", "reason_code", "latch_id",
          "store_id", "consumption_seq", "consumption_row_sha256")

# The states that end a consumed request, and the ones that leave it unresolved.
TERMINAL_STATES = frozenset({"COMPLETED", "FAILED_NOT_STARTED", "FAILED_EFFECT_PARTIAL",
                             "EFFECT_STATE_UNKNOWN", "RECONCILED_UNKNOWN"})
UNRESOLVED_STATES = frozenset({"FAILED_EFFECT_PARTIAL", "EFFECT_STATE_UNKNOWN", "RECONCILED_UNKNOWN"})


def event_hash(row: dict[str, Any]) -> str:
    return integrity.sha256_value({k: v for k, v in row.items() if k != "event_sha256"})


def _validate(row: dict[str, Any], code: str) -> None:
    try:
        schema_cache.validate_against_schema(row, SCHEMAS_DIR / SCHEMA_FILE, "deploy audit row")
    except RuntimeSchemaError:
        raise RunnerRefused(code) from None


def verify_chain(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    previous: str | None = None
    for index, row in enumerate(rows, start=1):
        _validate(row, "AUDIT_CHAIN_BROKEN")
        if row["seq"] != index or row["previous_event_sha256"] != previous or row["event_sha256"] != event_hash(row):
            raise RunnerRefused("AUDIT_CHAIN_BROKEN")
        previous = row["event_sha256"]
    return rows


class AuditLog:
    """The audit in the actor's private directory. The actor creates it on its first write."""

    def __init__(self, directory: os.PathLike[str] | str, *, runner_install_sha: str, generation: int = 1) -> None:
        self._dir = Path(directory)
        self._install_sha = runner_install_sha
        self._generation = generation

    def read(self) -> list[dict[str, Any]]:
        """The verified chain; an absent audit is an empty one (a fresh install)."""
        try:
            fd = open_private(self._dir, AUDIT_FILE, os.O_RDONLY, code="AUDIT_CHAIN_BROKEN")
        except FileNotFoundError:
            return []
        try:
            return verify_chain(parse_rows(read_all(fd, code="AUDIT_CHAIN_BROKEN"), code="AUDIT_CHAIN_BROKEN"))
        finally:
            os.close(fd)

    def append(self, *, execution_state: str, validation_result: str, now: str, **fields: Any) -> dict[str, Any]:
        """Write one row after the verified tail. Unknown field names are refused, not dropped."""
        if set(fields) - set(FIELDS):
            raise RunnerRefused("AUDIT_WRITE_FAILED")
        fd = open_private(self._dir, AUDIT_FILE, os.O_RDWR | os.O_APPEND, code="AUDIT_WRITE_FAILED", create=True)
        try:
            lock_exclusive(fd, code="AUDIT_WRITE_FAILED")
            try:
                rows = verify_chain(parse_rows(read_all(fd, code="AUDIT_WRITE_FAILED"), code="AUDIT_WRITE_FAILED"))
            except RunnerRefused:
                raise RunnerRefused("AUDIT_WRITE_FAILED") from None
            previous = rows[-1]["event_sha256"] if rows else None
            seq = len(rows) + 1
            row: dict[str, Any] = {name: fields.get(name) for name in FIELDS}
            row.update(seq=seq, recorded_at=now, generation=self._generation, execution_state=execution_state,
                       validation_result=validation_result, runner_install_sha=self._install_sha,
                       previous_event_sha256=previous)
            row["event_id"] = integrity.short_id("dae", {"seq": seq, "previous": previous, "at": now})
            row["event_sha256"] = event_hash(row)
            _validate(row, "AUDIT_WRITE_FAILED")
            append_row(fd, row, code="AUDIT_WRITE_FAILED")
            return row
        finally:
            os.close(fd)
