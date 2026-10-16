"""The factory fire leaves the pass (#705 remedy: process separation).

Approved Thomas 2026-08-17, as proposed: the KIND_FACTORY handler fetches in-pass, hands the
pure ``run_factory`` call to a child, and the pass continues; ``_collect_factory_child``
closes the occurrence bracket on a later pass. These tests pin the orchestration — the
bracket, the spool, the failure directions, and concurrency 1 — with a tiny injected
compute so the machinery is what runs, not the mining.

The synthetic-registry tests (timeout / died / orphan / foreign root) inject the module
state directly instead of arranging a real slow or dead child, so they hold on platforms
without fork exactly as on production Linux — no skips, per the suite's skip floor. The one
exception is the diagnostics-file protections (review E-04): they test POSIX link and permission
behaviour that a platform without O_NOFOLLOW cannot have, where the row is refused instead — and that
refusal is tested on every platform.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from runtime.mvp_runtime import scheduler
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.crypto import factory
from runtime.mvp_runtime.scheduler import (
    KIND_FACTORY,
    ScheduleStore,
    build_schedule,
    run_due,
    schedule_run_id,
)
from runtime.mvp_runtime.store import LEDGER_REL, SCHEDULER_FILE, LedgerStore

T0 = "2026-07-22T10:00:00Z"
T1 = "2026-07-23T11:00:00Z"
T2 = "2026-07-23T11:01:00Z"


@pytest.fixture(autouse=True)
def _no_leftover_child():
    yield
    scheduler._reset_factory_child()


def _factory_store(tmp_path, *, count=1):
    store = ScheduleStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        store.add(build_schedule(kind=KIND_FACTORY, request="", interval_seconds=86400,
                                 created_by=f"op{i}", now=T0))
    return store


def _events(tmp_path):
    path = tmp_path / LEDGER_REL / SCHEDULER_FILE
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _tiny_result(**overrides):
    base = {"candidates": [], "generation_id": "GEN-777", "accepted_count": 0,
            "requested_count": 0, "fused_count": 0, "seeded_topup_count": 0,
            "ablated_count": 0, "luck_filtered_count": 0}
    base.update(overrides)
    return base


def _tiny_factory(snapshot, **kwargs):
    return _tiny_result()


def test_a_spawn_pass_leaves_the_bracket_open_and_a_collect_pass_closes_it(tmp_path, monkeypatch):
    monkeypatch.setattr(factory, "run_factory", _tiny_factory)
    store = _factory_store(tmp_path)
    ledger = LedgerStore(tmp_path / LEDGER_REL)

    first = run_due(store, now=T1, control_store=ControlStore(tmp_path),
                    ledger=ledger, repo_root=tmp_path)
    assert first["spawned"] == 1 and first["fired"] == 0 and first["failed"] == 0
    assert first["results"][0]["action"] == "spawned"
    run_id = first["results"][0]["schedule_run_id"]
    # The bracket is open: a started event exists under this run id, no terminal yet.
    events = [e for e in _events(tmp_path) if e.get("schedule_run_id") == run_id]
    assert [e["action"] for e in events] == ["started"]
    assert scheduler.factory_child_busy()

    assert scheduler.factory_child_join(60), "the child never wrote its result"
    second = run_due(store, now=T2, control_store=ControlStore(tmp_path),
                     ledger=ledger, repo_root=tmp_path)
    assert second["fired"] == 1 and second["spawned"] == 0
    entry = second["results"][0]
    assert entry["schedule_run_id"] == run_id, "the collect must close the SAME occurrence"
    assert entry["status"].startswith("generated=0/0")
    events = [e for e in _events(tmp_path) if e.get("schedule_run_id") == run_id]
    assert [e["action"] for e in events] == ["started", "fired"]
    assert not scheduler.factory_child_busy()
    # The spool is empty — nothing survives a completed occurrence.
    spool = scheduler._factory_spool_dir(tmp_path)
    assert not any(spool.glob("*.json"))
    # And the ledger carries the factory record the parent (single writer) appended.
    rows = (tmp_path / LEDGER_REL / "records.jsonl").read_text(encoding="utf-8").splitlines()
    assert any(json.loads(r)["kind"] == "crypto_factory" for r in rows)


def test_a_second_due_factory_fire_defers_unclaimed_while_a_child_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(factory, "run_factory", _tiny_factory)
    store = _factory_store(tmp_path, count=2)
    ledger = LedgerStore(tmp_path / LEDGER_REL)

    first = run_due(store, now=T1, control_store=ControlStore(tmp_path),
                    ledger=ledger, repo_root=tmp_path)
    # One spawned, the sibling deferred — and the deferral claimed nothing, so it stays due.
    assert first["spawned"] == 1 and first["deferred"] == 1
    deferral = [r for r in first["results"] if r["action"] == "deferred"]
    assert deferral and deferral[0]["status"] == "deferred_factory_child_running"
    assert sum(1 for s in store.list() if s.next_run_at <= T1) == 1

    assert scheduler.factory_child_join(60)
    second = run_due(store, now=T2, control_store=ControlStore(tmp_path),
                     ledger=ledger, repo_root=tmp_path)
    # The collect lands first, then the loop spawns the sibling the deferral preserved.
    assert second["fired"] == 1 and second["spawned"] == 1


def test_a_child_that_raises_is_a_terminal_failed_with_no_candidates(tmp_path, monkeypatch):
    def _boom(snapshot, **kwargs):
        raise ValueError("lattice exploded")
    monkeypatch.setattr(factory, "run_factory", _boom)
    store = _factory_store(tmp_path)
    ledger = LedgerStore(tmp_path / LEDGER_REL)

    run_due(store, now=T1, control_store=ControlStore(tmp_path), ledger=ledger, repo_root=tmp_path)
    assert scheduler.factory_child_join(60)
    summary = run_due(store, now=T2, control_store=ControlStore(tmp_path),
                      ledger=ledger, repo_root=tmp_path)
    assert summary["failed"] == 1
    status = summary["results"][0]["status"]
    assert status.startswith("failed:FACTORY_CHILD:") and "ValueError" in status
    assert not (tmp_path / LEDGER_REL / "records.jsonl").exists() or not any(
        json.loads(r)["kind"] == "crypto_factory"
        for r in (tmp_path / LEDGER_REL / "records.jsonl").read_text(encoding="utf-8").splitlines()
    ), "a failed child must append nothing"


class _FakeProcess:
    """Quacks like multiprocessing.Process for the synthetic-registry tests."""

    def __init__(self, *, alive, exitcode=None):
        self._alive = alive
        self.exitcode = exitcode
        self.killed = False

    def is_alive(self):
        return self._alive and not self.killed

    def kill(self):
        self.killed = True

    def join(self, timeout=None):
        return None


def _inject_child(tmp_path, store, *, process, deadline_offset=3600.0):
    """Register a synthetic in-flight child for the claimed first schedule."""
    schedule = store.list()[0]
    claimed = store.claim_due(schedule.schedule_id, now=T1)
    run_id = schedule_run_id(claimed, claimed_at=T1)
    spool = scheduler._factory_spool_dir(tmp_path)
    spool.mkdir(parents=True, exist_ok=True)
    meta_path = spool / f"{run_id}.meta.json"
    meta_path.write_text(json.dumps({
        "schedule_run_id": run_id, "schedule_id": claimed.schedule_id,
        "kind": claimed.kind, "spawned_at": T1,
        "timeout_seconds": scheduler.FACTORY_CHILD_TIMEOUT_SECONDS, "pid": None,
    }), encoding="utf-8")
    scheduler._factory_child = {
        "run_id": run_id, "schedule": claimed, "previous_status": claimed.last_status,
        "process": process, "spawned_monotonic": time.monotonic(),
        "deadline_monotonic": time.monotonic() + deadline_offset,
        "meta_path": meta_path, "result_path": spool / f"{run_id}.result.json",
        "root": tmp_path,
    }
    return run_id


def test_a_child_past_its_deadline_is_killed_and_failed(tmp_path):
    store = _factory_store(tmp_path)
    ledger = LedgerStore(tmp_path / LEDGER_REL)
    hung = _FakeProcess(alive=True)
    _inject_child(tmp_path, store, process=hung, deadline_offset=-1.0)

    summary = run_due(store, now=T2, control_store=ControlStore(tmp_path),
                      ledger=ledger, repo_root=tmp_path)
    assert summary["failed"] == 1
    assert summary["results"][0]["status"] == "failed:FACTORY_CHILD_TIMEOUT"
    assert hung.killed, "a timed-out child must not keep computing"
    assert not scheduler.factory_child_busy()


def test_a_child_that_died_without_a_result_is_failed_with_its_exit_code(tmp_path):
    store = _factory_store(tmp_path)
    ledger = LedgerStore(tmp_path / LEDGER_REL)
    _inject_child(tmp_path, store, process=_FakeProcess(alive=False, exitcode=1))

    summary = run_due(store, now=T2, control_store=ControlStore(tmp_path),
                      ledger=ledger, repo_root=tmp_path)
    assert summary["failed"] == 1
    assert summary["results"][0]["status"] == "failed:FACTORY_CHILD_DIED:exit=1"
    assert not scheduler.factory_child_busy()


def test_an_orphaned_spool_from_a_dead_process_fails_closed_and_is_cleaned(tmp_path):
    """A meta file with no registered child is a fire whose parent died: the next process
    fails it, never resumes it — even a result the child wrote after the fact is discarded,
    because nobody can vouch for a computation whose supervisor is gone."""
    store = _factory_store(tmp_path)
    ledger = LedgerStore(tmp_path / LEDGER_REL)
    schedule = store.list()[0]
    claimed = store.claim_due(schedule.schedule_id, now=T1)
    run_id = schedule_run_id(claimed, claimed_at=T1)
    spool = scheduler._factory_spool_dir(tmp_path)
    spool.mkdir(parents=True, exist_ok=True)
    (spool / f"{run_id}.meta.json").write_text(json.dumps({
        "schedule_run_id": run_id, "schedule_id": claimed.schedule_id, "kind": claimed.kind,
        "spawned_at": T1, "timeout_seconds": 900.0, "pid": None,
    }), encoding="utf-8")
    (spool / f"{run_id}.result.json").write_text(
        json.dumps({"ok": True, "result": _tiny_result()}), encoding="utf-8")
    assert scheduler._factory_child is None

    summary = run_due(store, now=T2, control_store=ControlStore(tmp_path),
                      ledger=ledger, repo_root=tmp_path)
    assert summary["failed"] == 1
    assert summary["results"][0]["status"] == "failed:FACTORY_CHILD_ORPHANED"
    assert summary["results"][0]["schedule_run_id"] == run_id
    assert not any(spool.glob("*.json")), "an orphaned spool must not survive"
    assert store.list()[0].last_status == "failed:FACTORY_CHILD_ORPHANED"


def test_a_foreign_roots_child_is_not_collected_into_this_store(tmp_path):
    """Module state meets a different repo root (a test-only construction, but the guard is
    what keeps one context's child from writing into another's ledger)."""
    store = _factory_store(tmp_path)
    other = tmp_path / "elsewhere"
    other.mkdir()
    _inject_child(tmp_path, store, process=_FakeProcess(alive=True))
    scheduler._factory_child["root"] = other

    summary = run_due(store, now=T2, control_store=ControlStore(tmp_path),
                      ledger=LedgerStore(tmp_path / LEDGER_REL), repo_root=tmp_path)
    assert summary["failed"] == 0 and summary["fired"] == 0
    assert scheduler.factory_child_busy(), "a foreign child is left alone, not consumed"


# === the failure message stays out of everything a reader can reach (D-11 e2, E-02..E-04) ===========
# Why: docs/history/2026-10-16-factory-child-failure-type-only.md.
# Thomas confirmed e2 and E-02..E-04 on 2026-10-16 as a repository security implementation only (the
# crypto-pause security-bug-fix exemption); a passing test here is not an operational approval. The
# status carries the exception TYPE (or UNKNOWN); the message goes to the operator's private diagnostics
# file only. Every marker below is fake.

_FAKE = "FAKE-SECRET-7f3a"
_FAKE_PATH = "/root/fake-state/holdings/x.json"


def test_a_child_failure_message_reaches_only_the_diagnostics_file(tmp_path, monkeypatch):
    def _boom(snapshot, **kwargs):
        raise FileNotFoundError(f"{_FAKE_PATH} token={_FAKE}")
    monkeypatch.setattr(factory, "run_factory", _boom)
    store = _factory_store(tmp_path)
    ledger = LedgerStore(tmp_path / LEDGER_REL)
    sent = []
    notifier = lambda *args: sent.append(args)  # noqa: E731

    run_due(store, now=T1, control_store=ControlStore(tmp_path), ledger=ledger, repo_root=tmp_path, notifier=notifier)
    assert scheduler.factory_child_join(60)
    summary = run_due(store, now=T2, control_store=ControlStore(tmp_path), ledger=ledger, repo_root=tmp_path,
                      notifier=notifier)

    assert summary["results"][0]["status"] == "failed:FACTORY_CHILD:FileNotFoundError"
    reachable = {
        "ledger": (tmp_path / LEDGER_REL / SCHEDULER_FILE).read_text(encoding="utf-8"),
        "last_status": store.path.read_text(encoding="utf-8"),
        "alert": json.dumps(sent, ensure_ascii=False),
    }
    assert sent, "the failure transition still alerts the operator"
    for where, text in reachable.items():
        assert _FAKE not in text and _FAKE_PATH not in text, where
    diagnostics_path = tmp_path / scheduler.FACTORY_CHILD_ERRORS_REL
    if scheduler.factory_child_diagnostics_supported():
        (row,) = [json.loads(line) for line in diagnostics_path.read_text(encoding="utf-8").splitlines()]
        assert _FAKE in row["error"] and row["schedule_id"] == store.list()[0].schedule_id
    else:                                       # no O_NOFOLLOW (Windows): the row is refused, never written loosely
        assert not diagnostics_path.exists()
    assert not list((tmp_path / scheduler.FACTORY_SPOOL_REL).glob("*.result.json")), "the spool is emptied as before"


@pytest.mark.parametrize("error, expected", [
    ("ValueError: lattice exploded", "failed:FACTORY_CHILD:ValueError"),
    ("CORRUPT_RESULT", "failed:FACTORY_CHILD:CORRUPT_RESULT"),
    ("two words: then a colon", "failed:FACTORY_CHILD:UNKNOWN"),
    (f"../{_FAKE}: x", "failed:FACTORY_CHILD:UNKNOWN"),
    (None, "failed:FACTORY_CHILD:UNKNOWN"),                      # E-03: not a string the child writes
    ("", "failed:FACTORY_CHILD:UNKNOWN"),
    ("KeyError: ", "failed:FACTORY_CHILD:KeyError"),             # an empty message is still the child's shape
    (_FAKE.replace("-", "_"), "failed:FACTORY_CHILD:UNKNOWN"),   # E-03: identifier-shaped but no ": " separator
    ("ValueError", "failed:FACTORY_CHILD:UNKNOWN"),
    ("ValueError:no space", "failed:FACTORY_CHILD:UNKNOWN"),
    ("Value\nError: x", "failed:FACTORY_CHILD:UNKNOWN"),
    ("ValueError\n: x", "failed:FACTORY_CHILD:UNKNOWN"),
    ("\x00Err: x", "failed:FACTORY_CHILD:UNKNOWN"),
    ("  ValueError: x", "failed:FACTORY_CHILD:UNKNOWN"),
    ("ÉrreurDeValeur: x", "failed:FACTORY_CHILD:UNKNOWN"),       # non-ASCII
    ("\udcffError: x", "failed:FACTORY_CHILD:UNKNOWN"),          # lone surrogate
    ("A" * 65 + ": x", "failed:FACTORY_CHILD:UNKNOWN"),
    (f"{_FAKE_PATH}: x", "failed:FACTORY_CHILD:UNKNOWN"),
    ({"error": _FAKE}, "failed:FACTORY_CHILD:UNKNOWN"),
    (7, "failed:FACTORY_CHILD:UNKNOWN"),
])
def test_only_a_type_name_survives_into_the_status(error, expected):
    status = scheduler.factory_child_failure_status(error)
    assert status == expected and status.startswith("failed:")   # the prefix every reader keys on is kept
    assert _FAKE not in status and "/" not in status


def test_a_result_left_without_its_meta_is_deleted_unread(tmp_path):
    store = ScheduleStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    spool = tmp_path / scheduler.FACTORY_SPOOL_REL
    spool.mkdir(parents=True)
    (spool / "srun_left.result.json").write_text(json.dumps({"ok": False, "error": f"ValueError: {_FAKE}"}))
    (spool / "srun_half.result.json.tmp").write_text(f"ValueError: {_FAKE}")
    ledger = LedgerStore(tmp_path / LEDGER_REL)

    scheduler._collect_factory_child(store, ledger=ledger, notifier=None, now=T2, repo_root=tmp_path)

    assert list(spool.iterdir()) == []
    assert _events(tmp_path) == [], "an unclaimed leftover records nothing — it belonged to no occurrence"
    assert not (tmp_path / scheduler.FACTORY_CHILD_ERRORS_REL).exists(), "deleted unread, not recorded"


# --- E-02: a diagnostics write that fails never costs the occurrence its terminal failure ------------------

def _fail_one_child(tmp_path, monkeypatch, *, notifier=None):
    def _boom(snapshot, **kwargs):
        raise FileNotFoundError(f"{_FAKE_PATH} token={_FAKE}")
    monkeypatch.setattr(factory, "run_factory", _boom)
    store = _factory_store(tmp_path)
    ledger = LedgerStore(tmp_path / LEDGER_REL)
    run_due(store, now=T1, control_store=ControlStore(tmp_path), ledger=ledger, repo_root=tmp_path, notifier=notifier)
    assert scheduler.factory_child_join(60)
    summary = run_due(store, now=T2, control_store=ControlStore(tmp_path), ledger=ledger, repo_root=tmp_path,
                      notifier=notifier)
    return store, summary


@pytest.mark.parametrize("raised", [OSError(f"disk full at {_FAKE_PATH}"),
                                    UnicodeEncodeError("utf-8", "\udcff", 0, 1, _FAKE),
                                    RuntimeError(f"{_FAKE}\nTraceback (most recent call last):")])
def test_e02_a_diagnostics_write_that_raises_still_records_the_failure(tmp_path, monkeypatch, capsys, raised):
    def _raise(path, data):
        raise raised
    monkeypatch.setattr(scheduler, "_append_diagnostics_row", _raise)
    sent = []
    store, summary = _fail_one_child(tmp_path, monkeypatch, notifier=lambda *a: sent.append(a))
    (entry,) = summary["results"]
    assert entry["action"] == "failed" and entry["status"] == "failed:FACTORY_CHILD:FileNotFoundError"
    events = _events(tmp_path)
    assert events[-1]["action"] == "failed", "the terminal event is still written"
    assert store.list()[0].last_status == "failed:FACTORY_CHILD:FileNotFoundError"
    err = capsys.readouterr().err
    assert scheduler.FACTORY_CHILD_DIAGNOSTICS_UNAVAILABLE in err
    for text in (err, json.dumps(sent, ensure_ascii=False), json.dumps(events, ensure_ascii=False)):
        assert _FAKE not in text and _FAKE_PATH not in text and "Traceback" not in text


def test_e04_a_platform_that_cannot_refuse_links_refuses_the_row(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(scheduler, "factory_child_diagnostics_supported", lambda: False)
    ok = scheduler._record_factory_child_error(tmp_path, schedule_id="s", run_id="r",
                                               error=f"ValueError: {_FAKE}", now=T2)
    assert ok is False and not (tmp_path / scheduler.FACTORY_CHILD_ERRORS_REL).exists()
    err = capsys.readouterr().err
    assert scheduler.FACTORY_CHILD_DIAGNOSTICS_UNAVAILABLE in err and _FAKE not in err


# --- E-04: the diagnostics file is private, and what is not provably private is refused (POSIX) ------------

posix_diagnostics = pytest.mark.skipif(not scheduler.factory_child_diagnostics_supported(),
                                       reason="the diagnostics file needs O_NOFOLLOW, O_DIRECTORY and dir_fd (POSIX)")


def _diag(tmp_path):
    return tmp_path / scheduler.FACTORY_CHILD_ERRORS_REL


def _record(tmp_path, error=f"ValueError: {_FAKE}"):
    return scheduler._record_factory_child_error(tmp_path, schedule_id="s", run_id="r", error=error, now=T2)


@posix_diagnostics
def test_e04_the_file_and_directory_are_created_private(tmp_path):
    assert _record(tmp_path) is True and _record(tmp_path) is True
    path = _diag(tmp_path)
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    assert oct(os.stat(path.parent).st_mode & 0o777) == "0o700"
    rows = [json.loads(line) for line in path.read_text(encoding="ascii").splitlines()]
    assert len(rows) == 2 and _FAKE in rows[0]["error"]


@posix_diagnostics
def test_e02_an_unencodable_or_control_character_message_is_escaped_not_fatal(tmp_path):
    assert _record(tmp_path, error="ValueError: \udcff bad \x00 bytes \x1b[31m") is True
    (row,) = [json.loads(line) for line in _diag(tmp_path).read_text(encoding="ascii").splitlines()]
    assert row["error"].startswith("ValueError: ")


@posix_diagnostics
def test_e02_a_torn_write_is_cut_back(tmp_path, monkeypatch):
    assert _record(tmp_path) is True
    before = _diag(tmp_path).read_bytes()
    real = os.write

    def torn(fd, data):
        if isinstance(data, (bytes, bytearray)) and data.startswith(b'{"at"'):
            return real(fd, data[: len(data) // 2])
        return real(fd, data)
    monkeypatch.setattr(scheduler.os, "write", torn)
    assert _record(tmp_path) is False
    monkeypatch.undo()
    assert _diag(tmp_path).read_bytes() == before


@posix_diagnostics
@pytest.mark.parametrize("plant", ["dir_symlink", "dir_open_to_group", "dir_not_ours", "file_symlink",
                                   "file_hardlink", "file_open_to_group", "file_not_ours", "file_fifo",
                                   "file_fifo_with_reader"])
def test_e04_what_is_not_provably_private_is_refused_and_left_as_it_was(tmp_path, monkeypatch, capsys, plant):
    path = _diag(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    (elsewhere / path.name).write_bytes(b"precious\n")
    # Private in every other respect, so each case below fails on its own check and nothing else.
    os.chmod(elsewhere / path.name, 0o600)
    path.parent.parent.mkdir(parents=True, exist_ok=True)
    if plant == "dir_symlink":
        path.parent.symlink_to(elsewhere, target_is_directory=True)
    else:
        path.parent.mkdir(mode=0o700)
    if plant == "dir_open_to_group":
        os.chmod(path.parent, 0o750)
    elif plant == "dir_not_ours":
        monkeypatch.setattr(scheduler.os, "geteuid", lambda: os.stat(path.parent).st_uid + 1)
    elif plant == "file_symlink":
        path.symlink_to(elsewhere / path.name)
    elif plant == "file_hardlink":
        os.link(elsewhere / path.name, path)
    elif plant in ("file_open_to_group", "file_not_ours"):
        path.write_bytes(b"precious\n")
        os.chmod(path, 0o660 if plant == "file_open_to_group" else 0o600)
        if plant == "file_not_ours":
            # The directory check asks for the euid first and gets the real one; the file check asks
            # second and is told the process is someone else — so only the FILE's ownership fails.
            real_uid = os.stat(path).st_uid
            answers = iter([real_uid])
            monkeypatch.setattr(scheduler.os, "geteuid", lambda: next(answers, real_uid + 1))
    elif plant in ("file_fifo", "file_fifo_with_reader"):
        os.mkfifo(path, 0o600)
    reader = os.open(path, os.O_RDONLY | os.O_NONBLOCK) if plant == "file_fifo_with_reader" else None
    modes = {p: os.lstat(p).st_mode for p in (path.parent, path) if os.path.lexists(p)}
    try:
        assert _record(tmp_path) is False
    finally:
        if reader is not None:
            os.close(reader)
    monkeypatch.undo()
    assert (elsewhere / path.name).read_bytes() == b"precious\n", "nothing was written through a link"
    assert {p: os.lstat(p).st_mode for p in modes} == modes, "nothing was chmod-ed"
    if plant in ("file_open_to_group", "file_not_ours"):
        assert path.read_bytes() == b"precious\n"
    err = capsys.readouterr().err
    assert scheduler.FACTORY_CHILD_DIAGNOSTICS_UNAVAILABLE in err and _FAKE not in err
