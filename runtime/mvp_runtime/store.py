"""Append-only runtime ledger — durable, tamper-evident evidence.

The MVP produces governance records (task, permission decision, agent output, validation
result) and a hash-chained audit trail for every run. Before this store they lived only
in memory and were discarded when the process exited, so "append-only, hash-chained,
auditable" described nothing durable. :class:`LedgerStore` writes them to append-only
JSONL files under a local, per-machine, gitignored directory (``.runtime_governance_state/
runtime_ledger/`` by default, mirroring the Core pointer and safety-flag activation).

Three files, each append-only (one JSON object per line):

- ``audit_events.jsonl`` — the hash-chained ``audit_event.v0.1`` records. ``last_audit_hash``
  returns the tip so a new run chains onto the previous run's last event, making the
  ledger tamper-evident *across* runs, not just within one.
- ``records.jsonl`` — the non-audit records produced by a run, each tagged with its kind
  and the run's trace id.
- ``blocks.jsonl`` — lightweight block entries for runs that fail *before* a Core binding
  exists. Such a failure cannot be expressed as an ``audit_event.v0.1`` (that schema
  requires a bound task with a ``core_context_binding_id``), so a minimal, still-durable
  block entry is recorded instead.
- ``control_events.jsonl`` — operator emergency-console events (pause/kill/resume/stop).
  These are runtime control actions, not task outcomes, so like blocks they are durable
  standalone entries rather than task-bound ``audit_event.v0.1`` records.
- ``memory_events.jsonl`` — memory maintenance events (working-memory retention/deletion),
  likewise standalone rather than task-bound.
- ``scheduler_events.jsonl`` — scheduler events (a schedule fired, or was skipped by the kill
  switch), likewise standalone.

Fail-closed: any write or read failure raises :class:`PersistenceError`. Secrets are never
written — the records are already metadata-only and secret-scanned upstream.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from . import jsonl
from .errors import PersistenceError
from .paths import repo_root as _repo_root

LEDGER_REL = ".runtime_governance_state/runtime_ledger"
AUDIT_FILE = "audit_events.jsonl"
RECORDS_FILE = "records.jsonl"
BLOCKS_FILE = "blocks.jsonl"
CONTROL_FILE = "control_events.jsonl"
MEMORY_FILE = "memory_events.jsonl"
SCHEDULER_FILE = "scheduler_events.jsonl"

# Non-audit records persisted per run, in pipeline order.
_RECORD_KINDS = (
    "received_task", "task", "binding", "permission_decision",
    "search_permission_decision", "role_assignment", "tool_use",
    "agent_output", "invocation", "validation_result",
)


class LedgerStore:
    """Append-only JSONL ledger rooted at a directory (created on first write)."""

    def __init__(self, root: Path):
        self._root = Path(root)

    @classmethod
    def default(cls) -> "LedgerStore":
        """The repo-local ledger under ``.runtime_governance_state/`` (gitignored)."""
        return cls(_repo_root() / LEDGER_REL)

    @property
    def root(self) -> Path:
        return self._root

    def append_audit_events(self, events: list[Mapping[str, Any]]) -> None:
        jsonl.append_lines(self._root / AUDIT_FILE, events, write_code="LEDGER_WRITE_FAILED", label="the audit ledger")

    def append_records(self, trace_id: str | None, records: Mapping[str, Any]) -> None:
        rows = [
            {"kind": kind, "trace_id": trace_id, "record": records[kind]}
            for kind in _RECORD_KINDS
            if kind in records
        ]
        jsonl.append_lines(self._root / RECORDS_FILE, rows, write_code="LEDGER_WRITE_FAILED", label="the record ledger")

    def append_block(self, entry: Mapping[str, Any]) -> None:
        jsonl.append_lines(self._root / BLOCKS_FILE, [dict(entry)], write_code="LEDGER_WRITE_FAILED", label="the block ledger")

    def append_control(self, entry: Mapping[str, Any]) -> None:
        """Durably record one operator emergency-console event (pause/kill/resume/stop)."""
        jsonl.append_lines(self._root / CONTROL_FILE, [dict(entry)], write_code="LEDGER_WRITE_FAILED", label="the control ledger")

    def append_memory_event(self, entry: Mapping[str, Any]) -> None:
        """Durably record one memory maintenance event (e.g. working-memory retention/deletion)."""
        jsonl.append_lines(self._root / MEMORY_FILE, [dict(entry)], write_code="LEDGER_WRITE_FAILED", label="the memory ledger")

    def append_scheduler_event(self, entry: Mapping[str, Any]) -> None:
        """Durably record one scheduler event (a schedule fired, or was skipped by the kill switch)."""
        jsonl.append_lines(self._root / SCHEDULER_FILE, [dict(entry)], write_code="LEDGER_WRITE_FAILED", label="the scheduler ledger")

    def last_audit_hash(self) -> str | None:
        """Return the last persisted event's ``event_sha256`` (the chain tip), or None.

        Reading a corrupt or unparseable ledger fails closed rather than silently
        starting a fresh chain over a damaged one."""
        events = jsonl.read_objects(self._root / AUDIT_FILE, read_code="LEDGER_UNREADABLE", label="the audit ledger tip")
        if not events:
            return None
        try:
            return events[-1]["integrity"]["event_sha256"]
        except (KeyError, TypeError) as exc:
            raise PersistenceError("LEDGER_UNREADABLE", f"could not read the audit ledger tip: {exc}") from exc
