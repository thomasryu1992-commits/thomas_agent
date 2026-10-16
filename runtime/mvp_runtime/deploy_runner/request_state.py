"""Request states: the record that makes a resent request a lookup, never a second execution.

C2 specification §3.1, §3.1a and §6.7 (CR-6, model A), with the one difference from
``bridge_idempotency`` that matters: **a claim without an outcome never becomes claimable again.**
That module lets an id be re-claimed after its TTL, which it states may run an effect twice; here a
claim stays ``claimed`` until the request finishes, and the startup check turns a leftover claim into
``unknown`` and latches the runner. No timer turns doubt into permission.

States, latest row per ``request_id`` wins:

- ``claimed`` — the request is being carried out; a resend is ``REQUEST_IN_FLIGHT``;
- ``completed`` / ``refused_before_consume`` — finished; a resend replays the recorded reply;
- ``unknown`` / ``partial`` — the effect is uncertain; a resend is ``EFFECT_STATE_UNKNOWN`` /
  ``FAILED_EFFECT_PARTIAL`` and nothing runs.

A resend with the same id but a different request fingerprint is ``REQUEST_ID_REUSED``. A record file
the actor cannot read with certainty answers ``RUNNER_ERROR``: it must not read as "no record".
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from .. import schema_cache
from .approval_verify import SCHEMAS_DIR
from .private_files import append_row, lock_exclusive, open_private, parse_rows, read_all
from .reasons import RunnerRefused

REQUESTS_FILE = "requests.jsonl"
SCHEMA_FILE = "deploy_request_state.v0.1.schema.json"

CLAIMED = "claimed"
COMPLETED = "completed"
REFUSED_BEFORE_CONSUME = "refused_before_consume"
UNKNOWN = "unknown"
PARTIAL = "partial"
TERMINAL = frozenset({COMPLETED, REFUSED_BEFORE_CONSUME, UNKNOWN, PARTIAL})
_REPLAYABLE = frozenset({COMPLETED, REFUSED_BEFORE_CONSUME})
_FAIL = "RUNNER_ERROR"


@dataclass(frozen=True)
class Begun:
    """``replay`` is None for a new request (the caller now owns it), or the recorded reply."""

    replay: dict[str, Any] | None


def _validate(row: dict[str, Any]) -> None:
    try:
        schema_cache.validate_against_schema(row, SCHEMAS_DIR / SCHEMA_FILE, "request state")
    except RuntimeSchemaError:
        raise RunnerRefused(_FAIL) from None


class RequestStore:
    def __init__(self, directory: os.PathLike[str] | str) -> None:
        self._dir = Path(directory)

    def _open(self) -> int:
        return open_private(self._dir, REQUESTS_FILE, os.O_RDWR | os.O_APPEND, code=_FAIL, create=True)

    @staticmethod
    def _latest(fd: int) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for row in parse_rows(read_all(fd, code=_FAIL), code=_FAIL):
            _validate(row)
            latest[row["request_id"]] = row
        return latest

    def latest(self) -> dict[str, dict[str, Any]]:
        """Every request's current row. Read by the startup check."""
        fd = self._open()
        try:
            lock_exclusive(fd, code=_FAIL)
            return self._latest(fd)
        finally:
            os.close(fd)

    def begin(self, request_id: str, request_sha256: str, *, now: str) -> Begun:
        fd = self._open()
        try:
            lock_exclusive(fd, code=_FAIL)
            prior = self._latest(fd).get(request_id)
            if prior is None:
                row = {"request_id": request_id, "request_sha256": request_sha256, "state": CLAIMED,
                       "recorded_at": now, "result": None}
                _validate(row)
                append_row(fd, row, code=_FAIL)
                return Begun(replay=None)
            if prior["request_sha256"] != request_sha256:
                raise RunnerRefused("REQUEST_ID_REUSED")
            state = prior["state"]
            if state == CLAIMED:
                raise RunnerRefused("REQUEST_IN_FLIGHT")
            if state in _REPLAYABLE:
                return Begun(replay=prior["result"])
            raise RunnerRefused("EFFECT_STATE_UNKNOWN" if state == UNKNOWN else "FAILED_EFFECT_PARTIAL")
        finally:
            os.close(fd)

    def finish(self, request_id: str, request_sha256: str, state: str, *, now: str,
               result: dict[str, Any] | None) -> None:
        """Close a claim the caller owns. Only a ``claimed`` request with the same fingerprint can finish."""
        if state not in TERMINAL:
            raise RunnerRefused(_FAIL)
        fd = self._open()
        try:
            lock_exclusive(fd, code=_FAIL)
            prior = self._latest(fd).get(request_id)
            if prior is None or prior["state"] != CLAIMED or prior["request_sha256"] != request_sha256:
                raise RunnerRefused(_FAIL)
            row = {"request_id": request_id, "request_sha256": request_sha256, "state": state,
                   "recorded_at": now, "result": result}
            _validate(row)
            append_row(fd, row, code=_FAIL)
        finally:
            os.close(fd)

    def mark_unknown(self, request_id: str, *, now: str) -> None:
        """The startup check's verdict on a claim that never finished. Never used to clear anything."""
        fd = self._open()
        try:
            lock_exclusive(fd, code=_FAIL)
            prior = self._latest(fd).get(request_id)
            if prior is None or prior["state"] != CLAIMED:
                raise RunnerRefused(_FAIL)
            row = {"request_id": request_id, "request_sha256": prior["request_sha256"], "state": UNKNOWN,
                   "recorded_at": now, "result": None}
            _validate(row)
            append_row(fd, row, code=_FAIL)
        finally:
            os.close(fd)
