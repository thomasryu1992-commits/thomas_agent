"""Shared test configuration.

The suite must behave identically on a bare CI checkout and on the operator's machine —
the one with real safety-flag activations and gate env vars exported. Without this
isolation, a test that exercises a real entry point (the CLI, the operator loop) would
select the *capable* implementation there: live model calls burning quota, real network
egress, real workspace writes. Same class of leak as the ledger-pollution fix (cd520b5),
closed at the suite root instead of per test file.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest

# The per-machine state directory. On a developer box it is a scratch copy; on the live
# server it is the volume the running containers read and write as uid 10001.
_STATE_DIR = Path(__file__).resolve().parents[1] / ".runtime_governance_state"
# Reported once per session. Concurrent container writes would otherwise make every
# remaining test fail with the same finding, burying it under 1900 identical errors.
_state_leak_reported = False

# Every Safety-Flag Gate opt-in env var. Tests that exercise a gate opt-in set the var
# themselves via monkeypatch.setenv, which applies after this autouse teardown-safe clear.
_GATE_ENV_VARS = (
    "MVP_HOSTED_PROVIDER",
    "MVP_SEARCH_TOOL",
    "MVP_OPERATOR_CHANNEL",
    "MVP_WORKSPACE_WRITER",
    "MVP_APPROVAL_CONSUMPTION",
    "MVP_FRONTDESK_PROVIDER",
)


@pytest.fixture(autouse=True)
def _isolate_safety_gate_env(monkeypatch):
    """No test inherits the operator's gate opt-ins from the environment."""
    for var in _GATE_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _default_state_lands_in_tmp(tmp_path_factory, monkeypatch):
    """Point every *default* state location at a throwaway directory, for every test.

    The runtime's defaults resolve to the REPO's ``.runtime_governance_state/`` — correct
    in production, wrong in a test, and a test that omits ``repo_root`` gets them silently.
    Rather than asking ~18 call sites to remember an argument (and every future one too),
    the default itself is redirected here, centrally.

    Only the *default* moves: an explicitly passed root still wins, so tests that already
    inject a store or a ``repo_root`` are unaffected. And only the state moves — schemas,
    the role registry and the governance policy are still read from the real repo, because
    those are committed source the tests are meant to validate against.
    """
    state_root = tmp_path_factory.mktemp("state_root")

    for module_name, class_name in (
        ("store", "LedgerStore"),
        ("working_memory", "WorkingMemoryStore"),
        ("programization", "ProgramizationStore"),
        ("control", "ControlStore"),
        ("approval_store", "ApprovalStore"),
        ("scheduler", "ScheduleStore"),
        ("task_registry", "TaskRegistryStore"),
    ):
        module = importlib.import_module(f"runtime.mvp_runtime.{module_name}")
        store_cls = getattr(module, class_name)
        original = store_cls.default.__func__

        def _default(cls, root=None, *, _original=original, _fallback=state_root):
            return _original(cls, root if root is not None else _fallback)

        monkeypatch.setattr(store_cls, "default", classmethod(_default))

    # The two state writers that are plain functions rather than store classes.
    heartbeat = importlib.import_module("runtime.mvp_runtime.heartbeat")
    original_beat = heartbeat.write_heartbeat

    def _write_heartbeat(service, *, interval_seconds, root=None, **kwargs):
        return original_beat(service, interval_seconds=interval_seconds,
                             root=root if root is not None else state_root, **kwargs)

    monkeypatch.setattr(heartbeat, "write_heartbeat", _write_heartbeat)

    feedback = importlib.import_module("runtime.mvp_runtime.operator_feedback")
    original_record = feedback.record_delivery

    def _record_delivery(trace_id, *, now=None, repo_root=None, **kwargs):
        return original_record(trace_id, now=now,
                               repo_root=repo_root if repo_root is not None else state_root,
                               **kwargs)

    monkeypatch.setattr(feedback, "record_delivery", _record_delivery)
    return state_root


def _state_snapshot() -> dict[Path, tuple[int, int]] | None:
    """Every file under the state directory with its mtime and size, or None if absent.

    ~0.4ms for a live directory, so it can run around every test without being felt."""
    if not _STATE_DIR.is_dir():
        return None
    snapshot: dict[Path, tuple[int, int]] = {}
    for parent, _dirnames, filenames in os.walk(_STATE_DIR):
        for name in filenames:
            path = Path(parent) / name
            try:
                stat = path.stat()
            except OSError:
                continue        # vanished mid-walk; the next comparison will catch it
            snapshot[path] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


@pytest.fixture(autouse=True)
def _tests_never_touch_the_real_state_dir():
    """Fail the test that writes to the repo's own ``.runtime_governance_state/``.

    A runtime helper called without an explicit ``repo_root`` resolves to the REPO's state
    directory, not ``tmp_path`` — so a test that forgets the argument writes to real state.
    Harmless on a laptop; on the live server that directory is the volume two containers
    read and write as uid 10001, and a suite run there as root leaves root-owned files
    those containers can no longer write. Observed twice on 2026-07-25: `/feedback` stopped
    binding silently (``last_delivered.json``), and every queued task submission was
    refused (``REGISTRY_WRITE_FAILED``). Since the startup guard landed, the same condition
    stops the services from booting at all.

    The fix belongs here rather than in the runtime: the runtime's defaults are correct for
    production, and it is the *test* that must not use them. Detection is per-test so the
    failure names the culprit instead of leaving a whole-suite mystery.

    Reported once per session: on a machine where the services are running concurrently,
    their own writes trip this too — which is itself the correct answer (do not run the
    suite against live state), but it should be said once, not 1900 times.
    """
    before = _state_snapshot()
    yield
    global _state_leak_reported
    if before is None or _state_leak_reported:
        return
    after = _state_snapshot() or {}
    touched = sorted(
        path for path in set(before) | set(after) if before.get(path) != after.get(path)
    )
    if not touched:
        return
    _state_leak_reported = True
    shown = "\n".join(f"  - {path.relative_to(_STATE_DIR)}" for path in touched[:10])
    more = f"\n  … and {len(touched) - 10} more" if len(touched) > 10 else ""
    pytest.fail(
        f"this test wrote to the repo's real state directory ({_STATE_DIR.name}/):\n"
        f"{shown}{more}\n"
        "Pass an explicit repo_root/tmp_path to whatever runtime helper was called — its "
        "default resolves to the REPO's state, which on the live server is the volume the "
        "running containers own as uid 10001.\n"
        "If the services are running on this machine, that is the finding: run the suite "
        "in a git worktree, not in the deployed tree.",
        pytrace=False,
    )
