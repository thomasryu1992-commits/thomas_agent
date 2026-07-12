from __future__ import annotations

import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class SafeIOError(ValueError):
    pass


def safe_repo_path(
    root: Path,
    value: str | Path,
    *,
    must_exist: bool = False,
    allow_directory: bool = False,
) -> Path:
    root = root.resolve()
    candidate = Path(value)

    if candidate.is_absolute():
        raise SafeIOError(f"Absolute path is not allowed: {value}")

    resolved = (root / candidate).resolve()

    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise SafeIOError(f"Path escapes Repository root: {value}") from exc

    if must_exist and not resolved.exists():
        raise FileNotFoundError(resolved)

    if resolved.exists() and resolved.is_dir() and not allow_directory:
        raise SafeIOError(f"Expected a file path, got directory: {value}")

    return resolved


def safe_child_path(
    parent: Path,
    value: str | Path,
    *,
    must_exist: bool = False,
    allow_directory: bool = False,
) -> Path:
    parent = parent.resolve()
    candidate = Path(value)

    if candidate.is_absolute():
        raise SafeIOError(f"Absolute child path is not allowed: {value}")

    resolved = (parent / candidate).resolve()

    try:
        resolved.relative_to(parent)
    except ValueError as exc:
        raise SafeIOError(f"Path escapes allowed directory: {value}") from exc

    if must_exist and not resolved.exists():
        raise FileNotFoundError(resolved)

    if resolved.exists() and resolved.is_dir() and not allow_directory:
        raise SafeIOError(f"Expected a file path, got directory: {value}")

    return resolved


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )

    temp_path = Path(temp_name)

    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(temp_path, path)

        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None

        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def immutable_write_bytes(path: Path, data: bytes) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o644)

    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def immutable_write_text(path: Path, text: str) -> None:
    immutable_write_bytes(path, text.encode("utf-8"))


@contextmanager
def exclusive_lock(
    lock_path: Path,
    *,
    timeout_seconds: float = 30.0,
    poll_interval_seconds: float = 0.1,
    stale_after_seconds: float = 900.0,
) -> Iterator[None]:
    lock_path = lock_path.resolve()
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()

    while True:
        try:
            fd = os.open(
                lock_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o644,
            )
            payload = (
                f"pid={os.getpid()}\n"
                f"created_unix={time.time()}\n"
            ).encode("utf-8")
            os.write(fd, payload)
            os.fsync(fd)
            os.close(fd)
            break
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
                if age > stale_after_seconds:
                    lock_path.unlink()
                    continue
            except FileNotFoundError:
                continue

            if time.monotonic() - started >= timeout_seconds:
                raise TimeoutError(f"Timed out waiting for lock: {lock_path}")

            time.sleep(poll_interval_seconds)

    try:
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass
