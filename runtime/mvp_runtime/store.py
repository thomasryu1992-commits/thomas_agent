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

Fail-closed: any write or read failure raises :class:`PersistenceError`. Secrets are never
written — the records are already metadata-only and secret-scanned upstream.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .errors import PersistenceError

LEDGER_REL = ".runtime_governance_state/runtime_ledger"
AUDIT_FILE = "audit_events.jsonl"
RECORDS_FILE = "records.jsonl"
BLOCKS_FILE = "blocks.jsonl"

# Non-audit records persisted per run, in pipeline order.
_RECORD_KINDS = (
    "received_task", "task", "binding", "permission_decision",
    "role_assignment", "agent_output", "invocation", "validation_result",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


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

    def _append_line(self, filename: str, obj: Any) -> None:
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            line = json.dumps(obj, ensure_ascii=False, sort_keys=True)
            with (self._root / filename).open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except (OSError, TypeError, ValueError) as exc:
            raise PersistenceError("LEDGER_WRITE_FAILED", f"could not append to {filename}: {exc}") from exc

    def append_audit_events(self, events: list[Mapping[str, Any]]) -> None:
        for event in events:
            self._append_line(AUDIT_FILE, event)

    def append_records(self, trace_id: str | None, records: Mapping[str, Any]) -> None:
        for kind in _RECORD_KINDS:
            if kind in records:
                self._append_line(RECORDS_FILE, {"kind": kind, "trace_id": trace_id, "record": records[kind]})

    def append_block(self, entry: Mapping[str, Any]) -> None:
        self._append_line(BLOCKS_FILE, dict(entry))

    def last_audit_hash(self) -> str | None:
        """Return the last persisted event's ``event_sha256`` (the chain tip), or None.

        Reading a corrupt or unparseable ledger fails closed rather than silently
        starting a fresh chain over a damaged one."""
        path = self._root / AUDIT_FILE
        if not path.is_file():
            return None
        try:
            lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if not lines:
                return None
            last = json.loads(lines[-1])
            return last["integrity"]["event_sha256"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise PersistenceError("LEDGER_UNREADABLE", f"could not read the audit ledger tip: {exc}") from exc
