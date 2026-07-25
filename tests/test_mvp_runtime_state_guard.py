"""Startup guard tests — a service must refuse to run on state it cannot write.

The production failure: the deployed services run as uid 10001, a CLI was run on the host
as root, and the root-owned files it left in the mounted state directory made every
capability that touched one refuse *individually* — one reason code per request, no single
startup signal. Each refusal was correct; what was missing was one loud "this process
cannot write its own state" at the door.

Writability is injected in these tests rather than simulated with chmod: the suite often
runs as root, where every real permission check passes and the failure cannot be
reproduced at all.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from runtime.mvp_runtime import state_guard
from runtime.mvp_runtime.errors import PersistenceError
from runtime.mvp_runtime.state_guard import (
    STATE_DIR_REL,
    assert_state_writable,
    unwritable_paths,
)


def _state(tmp_path: Path) -> Path:
    base = tmp_path / STATE_DIR_REL
    (base / "runtime_ledger").mkdir(parents=True)
    (base / "runtime_ledger" / "audit_events.jsonl").write_text("{}\n", encoding="utf-8")
    (base / "task_registry.jsonl").write_text("{}\n", encoding="utf-8")
    return base


def _all_writable(_path: Path) -> bool:
    return True


def _none_writable(_path: Path) -> bool:
    return False


def _blocking(*names: str):
    """Writable everywhere except the named basenames — the real shape of the incident,
    where a couple of stale root-owned files sat in an otherwise healthy directory."""
    blocked = set(names)
    return lambda path: path.name not in blocked


# --- detection ---------------------------------------------------------------

def test_healthy_state_has_no_findings(tmp_path):
    _state(tmp_path)
    assert unwritable_paths(tmp_path, writable=_all_writable) == []


def test_absent_state_directory_is_not_a_finding(tmp_path):
    """A fresh machine creates it on first write; refusing there would block every new
    deployment for a condition that is normal."""
    assert unwritable_paths(tmp_path, writable=_none_writable) == []


def test_a_single_stale_file_is_found(tmp_path):
    base = _state(tmp_path)
    found = unwritable_paths(tmp_path, writable=_blocking("task_registry.jsonl"))
    assert found == [base / "task_registry.jsonl"]


def test_a_nested_file_is_found(tmp_path):
    """The ledger lives one level down; a guard that only checked the top level would
    have missed the audit trail itself."""
    base = _state(tmp_path)
    found = unwritable_paths(tmp_path, writable=_blocking("audit_events.jsonl"))
    assert found == [base / "runtime_ledger" / "audit_events.jsonl"]


def test_an_unwritable_directory_is_found(tmp_path):
    """A read-only directory blocks CREATION of the next ledger segment — invisible until
    the first append, and exactly as fatal as an unwritable existing file."""
    base = _state(tmp_path)
    found = unwritable_paths(tmp_path, writable=_blocking("runtime_ledger"))
    assert found == [base / "runtime_ledger"]


def test_the_state_root_itself_is_checked(tmp_path):
    base = _state(tmp_path)
    found = unwritable_paths(tmp_path, writable=_blocking(STATE_DIR_REL))
    assert base in found


# --- refusal -----------------------------------------------------------------

def test_writable_state_passes_silently(tmp_path):
    _state(tmp_path)
    assert_state_writable(tmp_path, writable=_all_writable) is None


def test_unwritable_state_fails_closed(tmp_path):
    _state(tmp_path)
    with pytest.raises(PersistenceError) as exc:
        assert_state_writable(tmp_path, writable=_blocking("task_registry.jsonl"))
    assert exc.value.reason_code == "STATE_NOT_WRITABLE"


def test_the_message_names_the_offender_and_the_fix(tmp_path):
    """The message has one job: let whoever reads the log fix it without investigating.

    A remedy on EVERY platform, not only POSIX — a refusal without one just relocates the
    investigation. (The first version emitted the fix line only where ``os.getuid`` exists,
    so Windows got a refusal with no way out; CI caught it.)"""
    _state(tmp_path)
    with pytest.raises(PersistenceError) as exc:
        assert_state_writable(tmp_path, writable=_blocking("task_registry.jsonl"))
    message = exc.value.reason
    assert "task_registry.jsonl" in message
    assert STATE_DIR_REL in message
    assert "Fix:" in message
    if hasattr(os, "getuid"):
        assert f"chown -R {os.getuid()}:{os.getgid()}" in message


def test_a_long_list_is_capped_but_counted(tmp_path):
    base = _state(tmp_path)
    for index in range(state_guard.MAX_REPORTED + 5):
        (base / f"junk_{index}.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(PersistenceError) as exc:
        assert_state_writable(tmp_path, writable=_none_writable)
    message = exc.value.reason
    assert "more" in message                      # the remainder is acknowledged
    assert message.count("\n  - ") == state_guard.MAX_REPORTED


# --- the services refuse to start --------------------------------------------

def test_operator_loop_refuses_to_start(tmp_path, monkeypatch, capsys):
    from runtime.mvp_runtime import operator_cli

    _state(tmp_path)
    monkeypatch.setattr(operator_cli, "assert_state_writable",
                        lambda root: (_ for _ in ()).throw(
                            PersistenceError("STATE_NOT_WRITABLE", "state is not writable")))
    rc = operator_cli.main([], repo_root=tmp_path)
    assert rc != 0
    assert "STATE_NOT_WRITABLE" in capsys.readouterr().err


def test_scheduler_loop_refuses_to_start(tmp_path, monkeypatch, capsys):
    from runtime.mvp_runtime import scheduler_cli

    _state(tmp_path)
    monkeypatch.setattr(scheduler_cli, "assert_state_writable",
                        lambda root: (_ for _ in ()).throw(
                            PersistenceError("STATE_NOT_WRITABLE", "state is not writable")))
    rc = scheduler_cli.main(["list"], repo_root=tmp_path)
    assert rc != 0
    assert "STATE_NOT_WRITABLE" in capsys.readouterr().err


def test_the_guard_does_not_repair_anything(tmp_path):
    """Deliberately not self-healing: a process that silently widened its own access to
    governed state would be the wrong direction in a repo where nothing grants itself
    anything. The file's mode is untouched by the check."""
    base = _state(tmp_path)
    target = base / "task_registry.jsonl"
    before = target.stat().st_mode
    with pytest.raises(PersistenceError):
        assert_state_writable(tmp_path, writable=_blocking("task_registry.jsonl"))
    assert target.stat().st_mode == before
