"""The consumption store: where an approval is spent, exactly once, before any effect.

Owned by the actor alone (N1-4). The rules (C2 specification §3.1b, §3.2, §3.4):

- **The actor never creates the store.** The install procedure writes it with a ``GENESIS`` row
  carrying a random ``store_id``. A missing file, or a first row that is not that genesis, is
  ``STORE_GENESIS_MISSING`` and the actor does not start. Nothing here can write a genesis.
- **Every row is chained**: ``seq`` counts up from 0, ``prev_sha256`` is the previous row's
  ``row_sha256``, and ``row_sha256`` is the kernel hash of the row without itself. A gap, an edited
  row, a torn last line or a duplicate id is ``STORE_CHAIN_BROKEN``.
- **Spending is check-then-append under one exclusive lock** on the file's own descriptor: read the
  whole store, verify it, refuse an ``approval_id`` or ``nonce`` already present, append, ``fsync``.
  Only then may the caller write its CONSUMED audit row and start the effect.
- **A failed or uncertain write is never retried here.** It raises ``CONSUMPTION_WRITE_FAILED``; the
  caller treats the request as failed-not-started and starts nothing.

What this does not do: the chain detects a partial edit by someone who cannot recompute it. An actor
or root rewriting the store and the audit together is not detected locally (§3.4, stated limit).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from runtime.read_only_kernel import integrity
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from .. import schema_cache
from .approval_verify import SCHEMAS_DIR, VerifiedApproval
from .private_files import append_row, lock_exclusive, open_private, parse_rows, read_all
from .reasons import RunnerRefused

STORE_FILE = "consumed.jsonl"
SCHEMA_FILE = "deploy_consumption_row.v0.1.schema.json"

GENESIS = "GENESIS"
APPROVAL = "APPROVAL"
# Row types the C2 contract defines for recovery (TYPE A/B). C3a implements none of them, so a store
# that holds one is not a store this code knows how to judge: it refuses rather than guesses.
NOT_IMPLEMENTED_TYPES = frozenset({"RESOLUTION", "REINIT_GENESIS", "REINIT", "IMPORTED"})


def row_hash(row: dict[str, Any]) -> str:
    return integrity.sha256_value({k: v for k, v in row.items() if k != "row_sha256"})


def genesis_row(*, store_id: str, install_sha: str, created_at: str) -> dict[str, Any]:
    """The genesis the install procedure writes. Rendered here so the install and the check agree;
    the actor itself never writes it."""
    row = {"type": GENESIS, "seq": 0, "prev_sha256": None, "store_id": store_id,
           "install_sha": install_sha, "created_at": created_at}
    row["row_sha256"] = row_hash(row)
    return row


def _validate(row: dict[str, Any], code: str) -> None:
    try:
        schema_cache.validate_against_schema(row, SCHEMAS_DIR / SCHEMA_FILE, "consumption row")
    except RuntimeSchemaError:
        raise RunnerRefused(code) from None


@dataclass(frozen=True)
class StoreState:
    store_id: str
    rows: tuple[dict[str, Any], ...]
    approval_ids: frozenset[str]
    nonces: frozenset[str]

    @property
    def last(self) -> dict[str, Any]:
        return self.rows[-1]

    def approval_rows(self) -> Iterable[dict[str, Any]]:
        return (r for r in self.rows if r["type"] == APPROVAL)


def verify_rows(rows: list[dict[str, Any]], *, expected_store_id: str | None = None) -> StoreState:
    """Judge a whole store. Raises the audit-only codes; the caller maps them to its own answer."""
    if not rows or rows[0].get("type") != GENESIS:
        raise RunnerRefused("STORE_GENESIS_MISSING")
    approval_ids: set[str] = set()
    nonces: set[str] = set()
    previous: dict[str, Any] | None = None
    for index, row in enumerate(rows):
        _validate(row, "STORE_CHAIN_BROKEN")
        kind = row["type"]
        if (index == 0) != (kind == GENESIS) or kind in NOT_IMPLEMENTED_TYPES:
            raise RunnerRefused("STORE_CHAIN_BROKEN")
        if row["seq"] != index or row["row_sha256"] != row_hash(row):
            raise RunnerRefused("STORE_CHAIN_BROKEN")
        if row["prev_sha256"] != (None if previous is None else previous["row_sha256"]):
            raise RunnerRefused("STORE_CHAIN_BROKEN")
        if kind == APPROVAL:
            if row["approval_id"] in approval_ids or row["nonce"] in nonces:
                raise RunnerRefused("STORE_CHAIN_BROKEN")
            approval_ids.add(row["approval_id"])
            nonces.add(row["nonce"])
        previous = row
    store_id = rows[0]["store_id"]
    if expected_store_id is not None and store_id != expected_store_id:
        raise RunnerRefused("STORE_ID_MISMATCH")
    return StoreState(store_id=store_id, rows=tuple(rows), approval_ids=frozenset(approval_ids),
                      nonces=frozenset(nonces))


class ConsumptionStore:
    """The store in the actor's private directory."""

    def __init__(self, directory: os.PathLike[str] | str) -> None:
        self._dir = Path(directory)

    def read(self, *, expected_store_id: str | None = None) -> StoreState:
        """The verified store for the startup check. An absent file is ``STORE_GENESIS_MISSING``; a
        file that is not the actor's private file is ``CONSUMPTION_STORE_UNAVAILABLE``."""
        try:
            fd = open_private(self._dir, STORE_FILE, os.O_RDONLY, code="CONSUMPTION_STORE_UNAVAILABLE")
        except FileNotFoundError:
            raise RunnerRefused("STORE_GENESIS_MISSING") from None
        try:
            rows = parse_rows(read_all(fd, code="CONSUMPTION_STORE_UNAVAILABLE"), code="STORE_CHAIN_BROKEN")
        finally:
            os.close(fd)
        return verify_rows(rows, expected_store_id=expected_store_id)

    def consume(self, approval: VerifiedApproval, *, request_id: str, now: str,
                expected_store_id: str) -> dict[str, Any]:
        """Spend ``approval`` once. Returns the appended row (its ``seq`` and ``row_sha256`` go into the
        CONSUMED audit row). Any doubt about the store is ``CONSUMPTION_STORE_UNAVAILABLE``."""
        try:
            fd = open_private(self._dir, STORE_FILE, os.O_RDWR | os.O_APPEND,
                              code="CONSUMPTION_STORE_UNAVAILABLE")
        except FileNotFoundError:
            raise RunnerRefused("CONSUMPTION_STORE_UNAVAILABLE") from None
        try:
            lock_exclusive(fd, code="CONSUMPTION_STORE_UNAVAILABLE")
            try:
                rows = parse_rows(read_all(fd, code="CONSUMPTION_STORE_UNAVAILABLE"),
                                  code="CONSUMPTION_STORE_UNAVAILABLE")
                state = verify_rows(rows, expected_store_id=expected_store_id)
            except RunnerRefused:
                raise RunnerRefused("CONSUMPTION_STORE_UNAVAILABLE") from None
            if approval.approval_id in state.approval_ids or approval.nonce in state.nonces:
                raise RunnerRefused("APPROVAL_ALREADY_CONSUMED")
            last = state.last
            row = {"type": APPROVAL, "seq": last["seq"] + 1, "prev_sha256": last["row_sha256"],
                   "approval_id": approval.approval_id, "nonce": approval.nonce,
                   "approval_sha256": approval.approval_sha256, "action": approval.action,
                   "request_id": request_id, "consumed_at": now}
            row["row_sha256"] = row_hash(row)
            _validate(row, "CONSUMPTION_WRITE_FAILED")
            append_row(fd, row, code="CONSUMPTION_WRITE_FAILED")
            return row
        finally:
            os.close(fd)
