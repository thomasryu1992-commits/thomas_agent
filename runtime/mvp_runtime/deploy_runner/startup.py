"""The actor's startup check: before it listens, every record must agree, or the runner refuses.

C2 specification §7.3 (steps 1–6, 8, 9; step 7, the recovery inbox, is not built in C3a). In order:

1. **Instance lock.** One actor at a time: an exclusive ``flock`` held for the process's life.
   Another holder is ``INSTANCE_LOCK_HELD``.
2. **Consumption store.** Genesis, chain, ``store_id`` (against the install's expectation and against
   the last ``RUNNER_START`` in the audit). Any failure is ``RUNNER_STARTUP_BLOCKED``; the detail
   code goes to the audit when the audit itself is sound.
3. **Audit chain.** A broken chain latches (``AUDIT_CHAIN_BROKEN``); nothing more is written to it.
4. **Cross-check.** Every APPROVAL row has exactly the CONSUMED audit row that names its ``seq`` and
   ``row_sha256``, and every CONSUMED audit row names a row that exists. Otherwise
   ``RECONCILIATION_MISMATCH`` and the runner latches.
5. **Unfinished work.** A consumed request with no terminal audit row, or a request still ``claimed``,
   becomes ``unknown`` (``RECONCILED_UNKNOWN``) and the runner latches. No timer and no retry.
6. **Unresolved effects.** Any unknown or partial effect without a resolution row keeps the runner
   latched **whether or not the LATCH file exists** — deleting it changes nothing.
8. **Clock.** A wall clock behind the last time this runner saw, beyond tolerance, is untrusted: the
   runner may start but no new effect may (``clock_trusted`` is false).
9. **RUNNER_START** is written last, with the store identity, install SHA and trust hash.

The result says whether the runner is latched and whether its clock is trusted. Acting on it —
refusing every request when latched — belongs to the actor's request loop.
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from .. import schema_cache
from .approval_verify import SCHEMAS_DIR
from .audit_chain import TERMINAL_STATES, UNRESOLVED_STATES, AuditLog
from .clock import parse_utc_second
from .consumption import ConsumptionStore
from .latch import Latch
from .private_files import encode_row, lock_exclusive, open_private, read_all
from .reasons import RunnerRefused
from .request_state import CLAIMED, RequestStore

INSTANCE_LOCK_FILE = "instance.lock"
CLOCK_FILE = "clock.json"
CLOCK_SCHEMA_FILE = "deploy_runner_clock.v0.1.schema.json"
_BLOCKED = "RUNNER_STARTUP_BLOCKED"


@dataclass
class StartupResult:
    store_id: str
    latched: bool
    clock_trusted: bool
    reasons: list[str] = field(default_factory=list)
    latch: dict[str, Any] | None = None


def acquire_instance_lock(directory: os.PathLike[str] | str) -> int:
    """The held lock's descriptor. Keep it open for the life of the process."""
    fd = open_private(directory, INSTANCE_LOCK_FILE, os.O_RDWR, code=_BLOCKED, create=True)
    try:
        lock_exclusive(fd, code="INSTANCE_LOCK_HELD", timeout_s=0.0)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _validate_clock(record: Any) -> None:
    try:
        schema_cache.validate_against_schema(record, SCHEMAS_DIR / CLOCK_SCHEMA_FILE, "runner clock")
    except RuntimeSchemaError:
        raise RunnerRefused(_BLOCKED) from None


def read_last_seen(directory: os.PathLike[str] | str) -> datetime | None:
    try:
        fd = open_private(directory, CLOCK_FILE, os.O_RDONLY, code=_BLOCKED)
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
    _validate_clock(record)
    return parse_utc_second(record["last_seen_wall"])


def write_last_seen(directory: os.PathLike[str] | str, when: str) -> None:
    """Replace the clock record atomically: a fresh private temp file, ``fsync``, rename relative to the
    checked directory's descriptor, ``fsync`` the directory. A temp file left by a crash has a different
    name and is never reused or deleted."""
    record = {"last_seen_wall": when}
    _validate_clock(record)
    tmp = f"{CLOCK_FILE}.{secrets.token_hex(8)}.tmp"
    try:
        fd = open_private(directory, tmp, os.O_WRONLY, code=_BLOCKED, create=True, exclusive=True)
    except (FileExistsError, FileNotFoundError):
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
    try:
        dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        raise RunnerRefused(_BLOCKED) from None
    try:
        st = os.fstat(dir_fd)
        if st.st_uid != os.geteuid() or st.st_mode & 0o077:
            raise RunnerRefused(_BLOCKED)
        os.replace(tmp, CLOCK_FILE, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        os.fsync(dir_fd)
    except OSError:
        raise RunnerRefused(_BLOCKED) from None
    finally:
        os.close(dir_fd)


def _cross_check(store_rows, audit_rows) -> bool:
    approvals = {r["seq"]: r["row_sha256"] for r in store_rows if r["type"] == "APPROVAL"}
    consumed = [r for r in audit_rows if r["execution_state"] == "CONSUMED"]
    named = {}
    for r in consumed:
        seq, sha = r["consumption_seq"], r["consumption_row_sha256"]
        if seq is None or seq in named or approvals.get(seq) != sha:
            return False
        named[seq] = sha
    return named == approvals


def reconcile(directory: os.PathLike[str] | str, *, runner_install_sha: str, trust_config_sha256: str | None,
              now: datetime, backstep_tolerance_s: int, expected_store_id: str | None = None) -> StartupResult:
    """Run the startup check. Raises ``RUNNER_STARTUP_BLOCKED`` when the runner must not listen at all;
    otherwise returns whether it is latched and whether its clock may start new effects."""
    directory = Path(directory)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    audit = AuditLog(directory, runner_install_sha=runner_install_sha)
    latch = Latch(directory)
    reasons: list[str] = []

    try:
        audit_rows = audit.read()
        audit_ok = True
    except RunnerRefused:
        audit_rows, audit_ok = [], False

    starts = [r for r in audit_rows if r["execution_state"] == "RUNNER_START" and r["store_id"]]
    recorded = starts[-1]["store_id"] if starts else None
    try:
        if expected_store_id and recorded and recorded != expected_store_id:
            raise RunnerRefused("STORE_ID_MISMATCH")  # the install and the audit disagree
        store = ConsumptionStore(directory).read(expected_store_id=expected_store_id or recorded)
    except RunnerRefused as exc:
        if audit_ok:
            audit.append(execution_state="RUNNER_START", validation_result="REFUSED", now=stamp,
                         reason_code=exc.reason_code, trust_config_sha256=trust_config_sha256)
        raise RunnerRefused(_BLOCKED) from None

    if not audit_ok:
        reasons.append("AUDIT_CHAIN_BROKEN")
    else:
        if not _cross_check(store.rows, audit_rows):
            reasons.append("RECONCILIATION_MISMATCH")
        unfinished: set[str] = set()
        consumed_ids = [r["request_id"] for r in audit_rows if r["execution_state"] == "CONSUMED"]
        for request_id in consumed_ids:
            after = [r for r in audit_rows if r["request_id"] == request_id]
            if not any(r["execution_state"] in TERMINAL_STATES for r in after):
                unfinished.add(request_id)
        requests = RequestStore(directory)
        for request_id, row in requests.latest().items():
            if row["state"] == CLAIMED:
                requests.mark_unknown(request_id, now=stamp)
                unfinished.add(request_id)
        for request_id in sorted(unfinished):
            audit.append(execution_state="RECONCILED_UNKNOWN", validation_result="NOT_REQUIRED", now=stamp,
                         request_id=request_id, reason_code="RECONCILED_UNKNOWN", store_id=store.store_id)
            audit_rows.append({"request_id": request_id, "execution_state": "RECONCILED_UNKNOWN"})
        resolved = {r["request_id"] for r in audit_rows if r["execution_state"] == "LATCH_RESOLVED"}
        if any(r["execution_state"] in UNRESOLVED_STATES and r["request_id"] not in resolved for r in audit_rows):
            reasons.append("RECONCILED_UNKNOWN")

    existing = latch.read()
    if existing is not None:
        reasons.append(existing["reason_code"])
    latched = bool(reasons)
    latch_record = existing
    if latched and existing is None:
        latch_record = latch.set(reason_code=reasons[0], request_id=None, now=stamp)
        if audit_ok:
            audit.append(execution_state="LATCH_SET", validation_result="NOT_REQUIRED", now=stamp,
                         reason_code=reasons[0], latch_id=latch_record["latch_id"], store_id=store.store_id)

    last_seen = read_last_seen(directory)
    clock_trusted = last_seen is None or now >= last_seen - timedelta(seconds=backstep_tolerance_s)
    if clock_trusted and (last_seen is None or now > last_seen):
        write_last_seen(directory, stamp)

    if audit_ok:
        audit.append(execution_state="RUNNER_START", validation_result="NOT_REQUIRED", now=stamp,
                     store_id=store.store_id, trust_config_sha256=trust_config_sha256,
                     latch_id=latch_record["latch_id"] if latch_record else None,
                     reason_code=None if clock_trusted else "CLOCK_UNTRUSTED")
    return StartupResult(store_id=store.store_id, latched=latched, clock_trusted=clock_trusted,
                         reasons=reasons, latch=latch_record)
