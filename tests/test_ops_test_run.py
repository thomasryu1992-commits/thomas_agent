"""The local test runner (`scripts/ops/test_run.sh`).

Every case copies the script into a scratch git repository and hands it a stub interpreter that
records how it was called, so nothing here runs the real suite, activates a Core, or reads the
host's checkout. The stub answers the four calls the runner makes (Core activation, pytest, the
skip check, the release gate) with exit codes the test chooses.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "ops" / "test_run.sh"

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="test_run.sh is a bash script")

# One line per call: the pycache prefix it saw, then argv joined by a unit separator.
STUB = r"""#!/bin/bash
printf '%s\t%s\n' "${PYTHONPYCACHEPREFIX:-}" "$(IFS=$'\x1f'; echo "$*")" >> "$STUB_LOG"
case "$1" in
  scripts/ci_activate_core_for_tests.py) exit "${STUB_ACTIVATE_RC:-0}" ;;
  scripts/check_test_skips.py) exit "${STUB_SKIPS_RC:-0}" ;;
  scripts/run_repository_release_gate.py) exit "${STUB_GATE_RC:-0}" ;;
  -m)
    for a in "$@"; do
      case "$a" in --junitxml=*) printf '<testsuites/>' > "${a#--junitxml=}" ;; esac
    done
    if [ -n "${STUB_EDIT:-}" ]; then echo edited >> "$STUB_EDIT"; fi
    exit "${STUB_PYTEST_RC:-0}" ;;
esac
exit 99
"""


def _repo(tmp_path: Path, *, core_active: bool = False) -> Path:
    repo = tmp_path / "repo"
    (repo / "scripts" / "ops").mkdir(parents=True)
    script = repo / "scripts" / "ops" / "test_run.sh"
    script.write_bytes(SCRIPT.read_bytes())
    script.chmod(0o755)
    (repo / "README").write_text("x\n", encoding="utf-8")
    git = ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(git[:3] + ["init", "-q"], check=True)
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "init"], check=True)
    if core_active:
        state = repo / ".runtime_governance_state"
        state.mkdir()
        (state / "CURRENT_CORE_RELEASE.yaml").write_text("x\n", encoding="utf-8")
        (repo / ".git" / "info" / "exclude").write_text(".runtime_governance_state/\n", encoding="utf-8")
    return repo


def _run(tmp_path: Path, repo: Path, *args: str, **env: str):
    stub = tmp_path / "stub-python"
    stub.write_text(STUB, encoding="utf-8")
    stub.chmod(0o755)
    log = tmp_path / "calls.log"
    result = subprocess.run(
        [str(repo / "scripts" / "ops" / "test_run.sh"), *args],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "THOMAS_TEST_PY": str(stub), "STUB_LOG": str(log),
             "TEST_RUN_MIN_TMP_MB": "0", "TEST_RUN_MIN_MEM_MB": "0", **env},
    )
    calls = []
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            prefix, _, argv = line.partition("\t")
            calls.append((prefix, argv.split("\x1f")))
    return result, calls


def _called(calls, first: str) -> list[list[str]]:
    return [argv for _, argv in calls if argv[:1] == [first] or argv[:2] == ["-m", first]]


@posix_only
def test_a_passing_run_uses_one_short_work_dir_and_removes_it(tmp_path):
    result, calls = _run(tmp_path, _repo(tmp_path, core_active=True))
    assert result.returncode == 0, result.stderr
    assert result.stdout.rstrip().splitlines()[-1] == "TEST_RUN_EXIT=0"
    (pytest_argv,) = _called(calls, "pytest")
    assert pytest_argv[-1] == "tests/"                      # a bare pytest collects scripts/ too
    basetemp = next(a for a in pytest_argv if a.startswith("--basetemp=")).split("=", 1)[1]
    work = Path(basetemp).parent
    assert len(basetemp) < 30                               # AF_UNIX paths stop at 108 bytes
    assert all(prefix == str(work / "pyc") for prefix, _ in calls)
    assert not work.exists()
    assert _called(calls, "scripts/check_test_skips.py")
    assert not _called(calls, "scripts/ci_activate_core_for_tests.py")


@posix_only
def test_a_missing_core_is_activated_and_a_refused_activation_stops_the_run(tmp_path):
    result, calls = _run(tmp_path, _repo(tmp_path))
    assert result.returncode == 0, result.stderr
    assert _called(calls, "scripts/ci_activate_core_for_tests.py") == [["scripts/ci_activate_core_for_tests.py"]]

    (tmp_path / "calls.log").unlink()
    result, calls = _run(tmp_path, _repo(tmp_path / "b"), STUB_ACTIVATE_RC="3")
    assert result.returncode == 4
    assert not _called(calls, "pytest")


@posix_only
def test_resources_below_the_floor_refuse_unless_forced(tmp_path):
    repo = _repo(tmp_path, core_active=True)
    result, calls = _run(tmp_path, repo, TEST_RUN_MIN_TMP_MB="999999999")
    assert result.returncode == 3 and "below the floor" in result.stderr
    assert calls == []

    result, calls = _run(tmp_path, repo, TEST_RUN_MIN_TMP_MB="999999999", TEST_RUN_FORCE="1")
    assert result.returncode == 0 and _called(calls, "pytest")


@posix_only
def test_a_tree_edited_during_the_run_fails_it_even_when_pytest_passed(tmp_path):
    repo = _repo(tmp_path, core_active=True)
    result, _ = _run(tmp_path, repo, STUB_EDIT=str(repo / "README"))
    assert result.returncode == 5 and "the tree changed" in result.stderr
    assert result.stdout.rstrip().splitlines()[-1] == "TEST_RUN_EXIT=5"


@posix_only
def test_a_pytest_failure_keeps_its_code_and_skips_the_later_checks(tmp_path):
    result, calls = _run(tmp_path, _repo(tmp_path, core_active=True), "tests/test_x.py", STUB_PYTEST_RC="1")
    assert result.returncode == 1
    assert _called(calls, "pytest")[0][-1] == "tests/test_x.py"
    assert not _called(calls, "scripts/check_test_skips.py")


@posix_only
def test_the_skip_check_failing_fails_the_run_and_root_is_held_to_two(tmp_path):
    result, calls = _run(tmp_path, _repo(tmp_path, core_active=True), STUB_SKIPS_RC="1")
    assert result.returncode == 6
    (skips_argv,) = _called(calls, "scripts/check_test_skips.py")
    assert ("--ceilings" in skips_argv) == (os.geteuid() == 0 and sys.platform == "linux")


@posix_only
def test_the_gate_runs_only_when_asked_and_its_failure_is_its_own_code(tmp_path):
    repo = _repo(tmp_path, core_active=True)
    result, calls = _run(tmp_path, repo)
    assert result.returncode == 0 and not _called(calls, "scripts/run_repository_release_gate.py")

    (tmp_path / "calls.log").unlink()
    result, calls = _run(tmp_path, repo, "--gate", STUB_GATE_RC="1")
    assert result.returncode == 7
    assert _called(calls, "scripts/run_repository_release_gate.py")
    assert "--gate" not in _called(calls, "pytest")[0]
