"""Cross-process advisory file lock for the runtime's local stores.

The MVP's stores are plain JSONL files shared by more than one process in the shipped
deployment (the continuous operator loop plus ``docker exec`` CLI invocations against the
same volume). A read-modify-write over those files — most critically the single-use
approval spend in ``consumption.py`` — is only safe if the re-read and the appends happen
under one mutual exclusion. This module provides that exclusion as an OS-level advisory
lock on a sidecar ``*.lock`` file: ``msvcrt.locking`` on Windows, ``fcntl.flock`` on POSIX.

Fail-closed: any failure to acquire the lock raises :class:`PersistenceError` with the
caller's ``reason_code`` — the caller refuses its action rather than proceeding unlocked.
On Windows the blocking acquire retries for roughly ten seconds and then fails, so a stuck
holder turns into a refusal, never an unbounded hang.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Iterator

from .errors import PersistenceError

if os.name == "nt":
    import msvcrt

    def _acquire(fh: IO[str]) -> None:
        fh.seek(0)
        # LK_LOCK retries once per second for ~10 attempts, then raises OSError.
        msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)

    def _release(fh: IO[str]) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _acquire(fh: IO[str]) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)

    def _release(fh: IO[str]) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


@contextmanager
def locked(lock_path: Path, *, code: str, label: str) -> Iterator[None]:
    """Hold an exclusive cross-process lock on ``lock_path`` for the duration of the block.

    The lock file is a zero-byte sidecar created on first use (its parents too); it is never
    read or written, only locked. Failure to create or acquire raises
    ``PersistenceError(code, ...)`` naming ``label`` — the fail-closed direction.
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
