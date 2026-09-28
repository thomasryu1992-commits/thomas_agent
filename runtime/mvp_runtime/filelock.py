"""Cross-process advisory file lock for the runtime's local stores.

The MVP's stores are plain JSONL files shared by more than one process in the shipped
deployment (the continuous operator loop plus ``docker exec`` CLI invocations against the
same volume). A read-modify-write over those files — most critically the single-use
approval spend in ``consumption.py`` — is only safe if the re-read and the appends happen
under one mutual exclusion. This module provides that exclusion as an OS-level advisory
lock on a sidecar ``*.lock`` file: ``msvcrt.locking`` on Windows, ``fcntl.flock`` on POSIX.

Fail-closed: any failure to acquire the lock raises :class:`PersistenceError` with the
caller's ``reason_code`` — the caller refuses its action rather than proceeding unlocked.
Waiting is bounded on every platform: the acquire polls a non-blocking lock until
``ACQUIRE_DEADLINE_SECONDS`` and then fails, so a stuck holder turns into a refusal, never an
unbounded hang. This used to hold only on Windows (``LK_LOCK``'s own ten retries); on Linux,
where the service runs, ``flock(LOCK_EX)`` waited forever, and one wedged holder could stall
every write in every container that shares the store (review A1, 2026-09-26).

The deadline bounds the WAITER, never the holder. A holder may legitimately keep a lock
longer — the forward-cohort walk replays its whole book under one — and only a contender that
arrives meanwhile is refused. Sixty seconds is a hang detector, not a contention tuner: the
hot shared stores (records, audit, schedules) measured about a second at worst.
"""

from __future__ import annotations

import errno
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Iterator

from .errors import PersistenceError

# How long a contender waits for a held lock before refusing, and how often it re-tries.
ACQUIRE_DEADLINE_SECONDS = 60.0
ACQUIRE_POLL_SECONDS = 0.05

if os.name == "nt":
    import msvcrt

    # What `_locking(LK_NBLCK)` reports for a region another handle holds. Anything else is a
    # real failure and is raised at once rather than waited out.
    _BUSY_ERRNOS = frozenset({errno.EACCES, errno.EDEADLK})

    def _try_acquire(fh: IO[str]) -> bool:
        fh.seek(0)
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in _BUSY_ERRNOS:
                return False
            raise
        return True

    def _release(fh: IO[str]) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_acquire(fh: IO[str]) -> bool:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def _release(fh: IO[str]) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _acquire(fh: IO[str]) -> None:
    """Poll the non-blocking lock until it is ours or the deadline passes. A timeout is an
    ``OSError`` (``TimeoutError``), which ``locked`` turns into the caller's refusal."""
    deadline = time.monotonic() + ACQUIRE_DEADLINE_SECONDS
    while not _try_acquire(fh):
        if time.monotonic() >= deadline:
            raise TimeoutError(f"still held by another process after {ACQUIRE_DEADLINE_SECONDS:g}s")
        time.sleep(ACQUIRE_POLL_SECONDS)


@contextmanager
def locked(lock_path: Path, *, code: str, label: str) -> Iterator[None]:
    """Hold an exclusive cross-process lock on ``lock_path`` for the duration of the block.

    The lock file is a zero-byte sidecar created on first use (its parents too); it is never
    read or written, only locked. Failure to create or acquire — including a wait past
    ``ACQUIRE_DEADLINE_SECONDS`` — raises ``PersistenceError(code, ...)`` naming ``label``:
    the fail-closed direction.
    """
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = lock_path.open("a")
    except OSError as exc:
        raise PersistenceError(code, f"could not open the lock for {label}: {exc}") from exc
    try:
        try:
            _acquire(fh)
        except OSError as exc:
            raise PersistenceError(code, f"could not lock {label}: {exc}") from exc
        try:
            yield
        finally:
            try:
                _release(fh)
            except OSError:
                # Releasing can only fail if the handle is already invalid; closing it
                # below drops the lock at the OS level regardless.
                pass
    finally:
        fh.close()
