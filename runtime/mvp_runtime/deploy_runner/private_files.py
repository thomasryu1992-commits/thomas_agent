"""The actor's own records: opened inside a private directory, checked on the descriptor, one line at a time.

Every record the actor keeps (consumption store, request states, audit, latch, clock state) lives in
one directory the actor owns. The same rules apply to each:

- the directory is opened with ``O_DIRECTORY | O_NOFOLLOW`` and must be a directory owned by this
  process's effective user with no group or other bits;
- a file is opened relative to that directory's descriptor with ``O_NOFOLLOW`` and must be a
  regular file with one link, owned by the same user, with no group or other bits;
- anything else is refused and left exactly as it was — nothing here chmods, chowns or deletes;
- a row is one ASCII JSON object and a newline, written in one ``write`` under an exclusive
  ``flock`` on the file's own descriptor, cut back to where it began if the write is short, and
  ``fsync``-ed before the call returns.

The caller names the code to refuse with, because the same failure means different things to
different records (an unreadable consumption store is ``CONSUMPTION_STORE_UNAVAILABLE``; an
unreadable audit is ``AUDIT_WRITE_FAILED`` when writing and a broken chain when reading).
"""

from __future__ import annotations

import json
import os
import stat
import time
from typing import Any

try:  # POSIX only; the platform check below refuses everything without it.
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

from .reasons import RunnerRefused

LOCK_TIMEOUT_S = 5.0
LOCK_POLL_S = 0.02
MAX_RECORD_FILE_BYTES = 64 * 1024 * 1024


def private_files_supported() -> bool:
    return (fcntl is not None and hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY")
            and hasattr(os, "geteuid") and os.open in getattr(os, "supports_dir_fd", set()))


def _check(st: os.stat_result, *, want_dir: bool) -> bool:
    kind_ok = stat.S_ISDIR(st.st_mode) if want_dir else (stat.S_ISREG(st.st_mode) and st.st_nlink == 1)
    return kind_ok and st.st_uid == os.geteuid() and not st.st_mode & 0o077


def open_private(directory: os.PathLike[str] | str, name: str, flags: int, *, code: str,
                 create: bool = False, exclusive: bool = False) -> int:
    """A descriptor for ``directory/name`` that is provably the actor's private file, or ``code``.

    ``create`` adds ``O_CREAT`` (mode 0600); ``exclusive`` adds ``O_EXCL``. Without ``create`` a
    missing file raises ``FileNotFoundError`` so the caller can tell "absent" from "unsafe"."""
    if not private_files_supported() or not name or "/" in name or name in (".", ".."):
        raise RunnerRefused(code)
    try:
        dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        raise RunnerRefused(code) from None
    try:
        if not _check(os.fstat(dir_fd), want_dir=True):
            raise RunnerRefused(code)
        open_flags = flags | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
        if create:
            open_flags |= os.O_CREAT
        if exclusive:
            open_flags |= os.O_EXCL
        try:
            fd = os.open(name, open_flags, 0o600, dir_fd=dir_fd)
        except (FileNotFoundError, FileExistsError):
            raise
        except OSError:
            raise RunnerRefused(code) from None
        try:
            if not _check(os.fstat(fd), want_dir=False):
                raise RunnerRefused(code)
        except BaseException:
            os.close(fd)
            raise
        return fd
    finally:
        os.close(dir_fd)


def fsync_directory(directory: os.PathLike[str] | str, *, code: str) -> None:
    try:
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        raise RunnerRefused(code) from None
    try:
        os.fsync(fd)
    except OSError:
        raise RunnerRefused(code) from None
    finally:
        os.close(fd)


def lock_exclusive(fd: int, *, code: str, timeout_s: float = LOCK_TIMEOUT_S) -> None:
    """Hold ``flock(LOCK_EX)`` on ``fd`` or raise ``code`` after ``timeout_s``. Released on close."""
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise RunnerRefused(code) from None
            time.sleep(LOCK_POLL_S)
        except OSError:
            raise RunnerRefused(code) from None


def read_all(fd: int, *, code: str) -> bytes:
    """The whole file from offset 0, however the descriptor was opened."""
    chunks: list[bytes] = []
    offset = 0
    try:
        while True:
            chunk = os.pread(fd, 1 << 20, offset)
            if not chunk:
                return b"".join(chunks)
            offset += len(chunk)
            if offset > MAX_RECORD_FILE_BYTES:
                raise RunnerRefused(code)
            chunks.append(chunk)
    except OSError:
        raise RunnerRefused(code) from None


def parse_rows(raw: bytes, *, code: str) -> list[dict[str, Any]]:
    """One ASCII JSON object per newline-terminated line. A torn last line, a non-object, a blank
    line or a non-ASCII byte is ``code``: a record file is read whole or not at all."""
    if not raw:
        return []
    if not raw.isascii() or not raw.endswith(b"\n"):
        raise RunnerRefused(code)
    rows: list[dict[str, Any]] = []
    for line in raw[:-1].split(b"\n"):
        try:
            value = json.loads(line.decode("ascii"), parse_constant=_reject_constant)
        except ValueError:
            raise RunnerRefused(code) from None
        if not isinstance(value, dict):
            raise RunnerRefused(code)
        rows.append(value)
    return rows


def _reject_constant(_: str) -> Any:
    raise ValueError("non-finite constant")


def encode_row(row: dict[str, Any]) -> bytes:
    return (json.dumps(row, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
            + "\n").encode("ascii")


def append_row(fd: int, row: dict[str, Any], *, code: str) -> None:
    """Append one row and ``fsync``. A short or failed write is cut back to where it began."""
    data = encode_row(row)
    try:
        start = os.fstat(fd).st_size
    except OSError:
        raise RunnerRefused(code) from None
    try:
        written = os.write(fd, data)
        if written != len(data):
            raise OSError("short write")
        os.fsync(fd)
    except OSError:
        try:
            os.ftruncate(fd, start)
        except OSError:
            pass
        raise RunnerRefused(code) from None
