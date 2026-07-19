"""Cross-process file-lock primitive tests (``runtime.mvp_runtime.filelock``).

The lock guards read-modify-write sections over the shared JSONL stores (most critically
the single-use approval spend), so what matters is: it actually excludes, it releases, and
acquisition failure is a fail-closed ``PersistenceError``, never an unlocked proceed.
"""

from __future__ import annotations

import threading

import pytest

from runtime.mvp_runtime.errors import PersistenceError
from runtime.mvp_runtime.filelock import locked


def test_lock_is_exclusive_across_holders(tmp_path):
    """A second acquire (fresh handle, as another process would use) waits until release —
    the two critical sections never interleave."""
    lock_path = tmp_path / "store" / ".test.lock"
    order: list[str] = []
    entered = threading.Event()
    release = threading.Event()

    def holder():
        with locked(lock_path, code="LOCK_TEST", label="test store"):
            order.append("holder_in")
            entered.set()
            release.wait(timeout=10)
            order.append("holder_out")

    def contender():
        entered.wait(timeout=10)
        with locked(lock_path, code="LOCK_TEST", label="test store"):
            order.append("contender_in")

    threads = [threading.Thread(target=holder), threading.Thread(target=contender)]
    for t in threads:
        t.start()
    entered.wait(timeout=10)
    # Give the contender a moment to reach (and block on) the acquire, then release.
    threading.Timer(0.3, release.set).start()
    for t in threads:
        t.join(timeout=30)
    assert order == ["holder_in", "holder_out", "contender_in"]


def test_lock_releases_on_exception(tmp_path):
    lock_path = tmp_path / ".test.lock"
    with pytest.raises(RuntimeError):
        with locked(lock_path, code="LOCK_TEST", label="test store"):
            raise RuntimeError("boom")
    # Re-acquiring immediately proves the first hold was released.
    with locked(lock_path, code="LOCK_TEST", label="test store"):
        pass


def test_unopenable_lock_path_fails_closed(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")  # a file where a directory parent is expected
    with pytest.raises(PersistenceError) as exc:
        with locked(blocker / "sub" / ".lock", code="LOCK_TEST", label="test store"):
            pass
    assert exc.value.reason_code == "LOCK_TEST"
