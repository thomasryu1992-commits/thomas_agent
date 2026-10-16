"""The latch: a file that says the runner must refuse everything until an operator resolves it.

C2 specification §7.3a and §7.6 (CR-18 accepted; CR-17 recovery still deferred):

- the latch is a file, so a restart does not forget it; and the startup check re-derives it from the
  records, so deleting the file does not clear it either;
- it is created with ``O_CREAT | O_EXCL`` and never rewritten — a second reason leaves the first latch
  as it is;
- **a latch whose state cannot be read with certainty counts as latched**: anything but "the file
  does not exist" or "the file is a valid private latch record" raises ``RUNNER_STARTUP_BLOCKED``;
- nothing in this module removes a latch. Resolution is a signed recovery document (not built in
  C3a), never a file deletion.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
from pathlib import Path
from typing import Any

from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from .. import schema_cache
from .approval_verify import SCHEMAS_DIR
from .private_files import encode_row, fsync_directory, open_private, read_all
from .reasons import RunnerRefused

LATCH_FILE = "LATCH"
SCHEMA_FILE = "deploy_latch.v0.1.schema.json"
_BLOCKED = "RUNNER_STARTUP_BLOCKED"


def new_latch_id() -> str:
    return "latch_" + base64.b32encode(secrets.token_bytes(16)).decode("ascii").rstrip("=").lower()[:26]


def _validate(record: Any) -> None:
    try:
        schema_cache.validate_against_schema(record, SCHEMAS_DIR / SCHEMA_FILE, "latch")
    except RuntimeSchemaError:
        raise RunnerRefused(_BLOCKED) from None


class Latch:
    def __init__(self, directory: os.PathLike[str] | str) -> None:
        self._dir = Path(directory)

    def read(self) -> dict[str, Any] | None:
        try:
            fd = open_private(self._dir, LATCH_FILE, os.O_RDONLY, code=_BLOCKED)
        except FileNotFoundError:
            return None
        try:
            raw = read_all(fd, code=_BLOCKED)
        finally:
            os.close(fd)
        try:
            record = json.loads(raw.decode("ascii"))
        except (UnicodeDecodeError, ValueError):
            raise RunnerRefused(_BLOCKED) from None
        _validate(record)
        return record

    def set(self, *, reason_code: str, request_id: str | None, now: str) -> dict[str, Any]:
        """Latch, or keep the latch that is already there. Returns the latch in force."""
        record = {"latch_id": new_latch_id(), "reason_code": reason_code, "request_id": request_id,
                  "created_at": now}
        _validate(record)
        try:
            fd = open_private(self._dir, LATCH_FILE, os.O_WRONLY, code=_BLOCKED, create=True, exclusive=True)
        except FileExistsError:
            existing = self.read()
            if existing is None:
                raise RunnerRefused(_BLOCKED) from None
            return existing
        except FileNotFoundError:
            raise RunnerRefused(_BLOCKED) from None
        try:
            data = encode_row(record)
            if os.write(fd, data) != len(data):
                raise RunnerRefused(_BLOCKED)
            os.fsync(fd)
        except OSError:
            raise RunnerRefused(_BLOCKED) from None
        finally:
            os.close(fd)
        fsync_directory(self._dir, code=_BLOCKED)
        return record
