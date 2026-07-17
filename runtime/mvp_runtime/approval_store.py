"""Durable append-only store for Approval records (R9).

An approval is evidence of what Thomas was asked and what he answered, so the store is
**append-only**: a decision is recorded by appending the decided record, never by editing
the PENDING one. The history of an approval is therefore replayable, and a decision cannot
be quietly rewritten. ``current`` folds that history down to the latest state of each
approval id.

Local and per-machine (gitignored, under ``.runtime_governance_state/``) like every other
runtime store: an approval is a fact about this machine's operator conversation, not
shared source.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from . import jsonl
from .paths import repo_root as _repo_root

STORE_REL = ".runtime_governance_state/approvals"
APPROVALS_FILE = "approvals.jsonl"
# The decisions the approvals are bound to, kept beside them so a decision arriving later
# (Thomas answers minutes after the ask) can still be validated against the exact action it
# authorizes. One record type per file; both append-only.
PERMISSION_DECISIONS_FILE = "permission_decisions.jsonl"


class ApprovalStore:
    """Append-only JSONL store of approval records, latest-wins per approval_id."""

    def __init__(self, root: Path):
        self._root = Path(root)

    @classmethod
    def default(cls) -> "ApprovalStore":
        return cls(_repo_root() / STORE_REL)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def path(self) -> Path:
        return self._root / APPROVALS_FILE

    def append(self, approvals: Iterable[Mapping[str, Any]]) -> None:
        """Append approval records. A decision appends the decided record — it never edits
        the request, so the trail keeps both."""
        records = [dict(a) for a in approvals]
        if not records:
            return
        jsonl.append_lines(
            self.path, records, write_code="APPROVAL_WRITE_FAILED", label="approval store"
        )

    def read_all(self) -> list[dict[str, Any]]:
        """Every appended record in order. Fail-closed on a corrupt store."""
        return jsonl.read_objects(
            self.path, read_code="APPROVAL_READ_FAILED", label="approval store"
        )

    def current(self) -> dict[str, dict[str, Any]]:
        """The latest record per approval_id — the approval's current state.

        Later appends win, which is what makes an append-only store readable as state.
        """
        latest: dict[str, dict[str, Any]] = {}
        for record in self.read_all():
            approval_id = record.get("approval_id")
            if isinstance(approval_id, str) and approval_id:
                latest[approval_id] = record
        return latest

    def get(self, approval_id: str) -> dict[str, Any] | None:
        return self.current().get(approval_id)

    def pending(self) -> list[dict[str, Any]]:
        """Approvals still awaiting an answer, oldest first."""
        items = [a for a in self.current().values() if a.get("status") == "PENDING"]
        items.sort(key=lambda a: str(a.get("validity", {}).get("issued_at", "")))
        return items

    # --- the decisions approvals are bound to -------------------------------------

    @property
    def permission_path(self) -> Path:
        return self._root / PERMISSION_DECISIONS_FILE

    def append_permission_decision(self, permission_decision: Mapping[str, Any]) -> None:
        jsonl.append_lines(
            self.permission_path, [dict(permission_decision)],
            write_code="APPROVAL_WRITE_FAILED", label="approval permission-decision store",
        )

    def get_permission_decision(self, permission_decision_id: str) -> dict[str, Any] | None:
        """The decision an approval binds to, so a later answer can be re-validated against
        the exact action rather than trusted on its own."""
        records = jsonl.read_objects(
            self.permission_path,
            read_code="APPROVAL_READ_FAILED", label="approval permission-decision store",
        )
        for record in reversed(records):
            if record.get("permission_decision_id") == permission_decision_id:
                return record
        return None
