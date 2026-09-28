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


def test_a_wait_past_the_deadline_fails_closed_and_the_holder_keeps_its_lock(tmp_path, monkeypatch):
    """A stuck holder turns into the contender's refusal, never an unbounded wait (review A1:
    on Linux `flock(LOCK_EX)` used to block forever). The holder is unaffected: its section
    runs to the end and its lock is released and re-acquirable."""
    import time

    from runtime.mvp_runtime import filelock

    monkeypatch.setattr(filelock, "ACQUIRE_DEADLINE_SECONDS", 0.3)
    lock_path = tmp_path / ".test.lock"
    entered = threading.Event()
    release = threading.Event()
    holder_done: list[str] = []

    def holder():
        with locked(lock_path, code="LOCK_TEST", label="test store"):
            entered.set()
            release.wait(timeout=10)
        holder_done.append("released")

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert entered.wait(timeout=10)
        started = time.monotonic()
        with pytest.raises(PersistenceError) as exc:
            with locked(lock_path, code="LOCK_TEST", label="test store"):
                pytest.fail("entered a lock another holder still owns")
        waited = time.monotonic() - started
    finally:
        release.set()
        thread.join(timeout=30)
    assert exc.value.reason_code == "LOCK_TEST"
    assert "test store" in str(exc.value) and "0.3s" in str(exc.value)
    assert 0.3 <= waited < 5
    assert holder_done == ["released"]
    with locked(lock_path, code="LOCK_TEST", label="test store"):
        pass


def test_a_holder_that_releases_inside_the_deadline_is_waited_for(tmp_path, monkeypatch):
    """The deadline refuses only a wait that outlives it; a normal hand-over still succeeds."""
    from runtime.mvp_runtime import filelock

    monkeypatch.setattr(filelock, "ACQUIRE_DEADLINE_SECONDS", 10.0)
    lock_path = tmp_path / ".test.lock"
    entered = threading.Event()

    def holder():
        with locked(lock_path, code="LOCK_TEST", label="test store"):
            entered.set()
            threading.Event().wait(0.3)

    thread = threading.Thread(target=holder)
    thread.start()
    assert entered.wait(timeout=10)
    with locked(lock_path, code="LOCK_TEST", label="test store"):
        pass
    thread.join(timeout=30)
