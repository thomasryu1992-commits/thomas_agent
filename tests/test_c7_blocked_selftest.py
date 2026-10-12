"""`scripts/c7_blocked_selftest.py`: the PR-D self-test door, pinned to D-1–D-10 of
`HOLDINGS_C7_PR_D_DEPLOY_VALIDATION_DESIGN_V0.1.md` §2.

The door is acceptable only while it stays shut to everything but its own synthetic lane. These tests check
that by calling it: it passes on the merged code, refuses input, prints a closed vocabulary, touches nothing
outside its temporary root, fails closed when that root cannot be made, loads no network or delivery module,
and its checks are not vacuous (a broken PR-D behaviour turns them red). Synthetic only.
"""

from __future__ import annotations

import ast
import pathlib
import re
import subprocess
import sys
import tempfile

import pytest

from runtime.mvp_runtime.holdings import cash_flows

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "c7_blocked_selftest.py"

sys.path.insert(0, str(ROOT))
from scripts import c7_blocked_selftest as selftest  # noqa: E402

EXPECTED_CHECKS = {
    "synthetic_root_isolated", "healthy_is_quiet", "broken_is_told_once_and_stays_quiet",
    "failed_delivery_is_offered_again", "failed_record_is_offered_again",
    "repair_is_cleared_once_and_recurrence_told", "vanished_is_not_cleared", "stale_token_is_refused",
    "lock_is_not_blocked", "alert_text_carries_no_key",
}
LINE = re.compile(r"^(?:(?:PASS|FAIL) [a-z_]+|SOURCE (?:[0-9a-f]{64}|missing) [\w./-]+|RESULT (?:PASS|FAIL))$")


@pytest.fixture
def temp_root(tmp_path, monkeypatch):
    """Point the door's TemporaryDirectory at a pytest directory so its cleanup can be checked."""
    root = tmp_path / "tmp"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    return root


def _lines(capsys) -> list[str]:
    return capsys.readouterr().out.splitlines()


# --- it passes on the merged code, and says only what it may ------------------------------------------------

def test_every_check_passes_on_the_merged_code(temp_root, capsys):
    assert selftest.main([]) == 0
    lines = _lines(capsys)
    assert {line.split()[1] for line in lines if line.startswith("PASS ")} == EXPECTED_CHECKS
    assert not [line for line in lines if line.startswith("FAIL ")]
    assert lines[-1] == "RESULT PASS"


def test_the_output_is_a_closed_vocabulary(temp_root, capsys):
    selftest.main([])
    lines = _lines(capsys)
    assert lines and all(LINE.match(line) for line in lines), [line for line in lines if not LINE.match(line)]
    sources = [line.split()[2] for line in lines if line.startswith("SOURCE ")]
    assert sources == list(selftest.COVERED_SOURCES)
    assert not [line for line in lines if line.startswith("SOURCE missing")]


def test_source_hashes_match_the_files_in_this_tree():
    import hashlib
    for digest, rel in selftest.source_hashes():
        assert digest == hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()


@pytest.mark.parametrize("argv", [["--root", "/x"], ["anything"], [""]])
def test_any_input_is_refused_before_anything_runs(temp_root, capsys, monkeypatch, argv):
    ran = []
    monkeypatch.setattr(selftest, "run_selftest_checks", lambda: ran.append(1) or [])
    assert selftest.main(argv) == 2
    assert _lines(capsys) == ["FAIL input_refused", "RESULT FAIL"]
    assert ran == []


def test_a_check_that_raises_is_a_bare_fail(temp_root, capsys, monkeypatch):
    def boom() -> bool:
        raise RuntimeError("detail that must not be printed")
    monkeypatch.setattr(selftest, "CHECKS", (("healthy_is_quiet", boom),))
    assert selftest.main([]) == 1
    out = "\n".join(_lines(capsys))
    assert "FAIL healthy_is_quiet" in out and "detail" not in out and "Traceback" not in out


# --- it touches nothing outside its temporary root ----------------------------------------------------------

def test_every_lane_lives_under_the_temporary_root_and_is_removed(temp_root, monkeypatch, capsys):
    seen: list[pathlib.Path] = []
    real = cash_flows.ledger_path

    def recording(state_dir):
        seen.append(pathlib.Path(state_dir).resolve())
        return real(state_dir)
    monkeypatch.setattr(cash_flows, "ledger_path", recording)
    assert selftest.main([]) == 0
    assert seen and all(path.is_relative_to(temp_root.resolve()) for path in seen)
    assert list(temp_root.iterdir()) == []                                     # every lane cleaned up


def test_a_root_that_cannot_be_made_fails_closed_without_a_fallback(monkeypatch, capsys):
    calls: list[object] = []

    def refuse(*_a, **_k):
        raise OSError("no temporary directory")
    monkeypatch.setattr(tempfile, "TemporaryDirectory", refuse)
    monkeypatch.setattr(cash_flows, "set_cutover", lambda *a, **k: calls.append(a))
    assert selftest.main([]) == 1
    lines = _lines(capsys)
    assert [line for line in lines if line.startswith("FAIL ")] and lines[-1] == "RESULT FAIL"
    assert calls == []                                                          # nothing ran anywhere else


def test_a_root_under_a_state_directory_is_refused(tmp_path, monkeypatch, capsys):
    trap = tmp_path / selftest.STATE_ROOT_NAME / "tmp"
    trap.mkdir(parents=True)
    monkeypatch.setattr(tempfile, "tempdir", str(trap))
    assert selftest.main([]) == 1
    assert not [line for line in _lines(capsys) if line.startswith("PASS ")]
    assert list((tmp_path / selftest.STATE_ROOT_NAME).rglob("*.jsonl")) == []  # no lane was started there


# --- its checks are not vacuous ------------------------------------------------------------------------------

def test_a_lost_clearing_rule_fails_the_vanished_check(temp_root, monkeypatch, capsys):
    monkeypatch.setattr(cash_flows, "_clearing_withheld", lambda *_a: None)
    assert selftest.main([]) == 1
    assert "FAIL vanished_is_not_cleared" in _lines(capsys)


def test_a_lost_stale_guard_fails_the_stale_check(temp_root, monkeypatch, capsys):
    real = cash_flows._write_blocked_told

    def no_guard(state_dir, *, kind, key, now):
        cash_flows.blocked_told_path(state_dir).write_text("{}", encoding="utf-8")
        return True
    monkeypatch.setattr(cash_flows, "_write_blocked_told", no_guard)
    assert selftest.main([]) == 1
    assert "FAIL stale_token_is_refused" in _lines(capsys)
    monkeypatch.setattr(cash_flows, "_write_blocked_told", real)


def test_a_silent_judgement_fails_the_told_checks(temp_root, monkeypatch, capsys):
    monkeypatch.setattr(cash_flows, "blocked_state", lambda *_a: {"blocked": False, "verified_since": None})
    assert selftest.main([]) == 1
    lines = _lines(capsys)
    assert "FAIL broken_is_told_once_and_stays_quiet" in lines and "FAIL failed_delivery_is_offered_again" in lines


# --- no input surface, no network, no default state path, runtime untouched (static) -------------------------

def _tree() -> ast.Module:
    return ast.parse(MODULE_PATH.read_text(encoding="utf-8"))


def test_it_imports_only_what_a_synthetic_lane_needs():
    allowed = {"__future__", "hashlib", "json", "sys", "tempfile", "contextlib", "datetime", "pathlib", "typing",
               "runtime.mvp_runtime.holdings"}
    imported = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            if node.module == "runtime.mvp_runtime.holdings":
                assert [alias.name for alias in node.names] == ["cash_flows"]
    assert imported <= allowed, imported - allowed


def test_it_reads_no_environment_stdin_or_default_state_path():
    """No environment, no stdin, and no way to the real state: neither the module that defaults to it
    (``store``), nor its constant, nor any ``state_dir(...)`` call. The lane's directory is a local name."""
    source = MODULE_PATH.read_text(encoding="utf-8")
    tree = _tree()
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    for forbidden in ("environ", "getenv", "stdin", "STATE_REL", "store"):
        assert forbidden not in attributes | names, forbidden
    called = {getattr(node.func, "attr", getattr(node.func, "id", None)) for node in ast.walk(tree)
              if isinstance(node, ast.Call)}
    assert not called & {"state_dir", "input", "getenv"}, called & {"state_dir", "input", "getenv"}
    assert "os." not in source


def test_no_network_or_delivery_module_is_loaded_by_the_door():
    """A fresh interpreter that imports only the door loads no module that can open a connection or send a
    message. ``urllib.parse`` (a string parser that ``pathlib`` loads) is not one; ``urllib.request`` is."""
    probe = ("import sys; sys.path.insert(0, sys.argv[1]); import scripts.c7_blocked_selftest; "
             "bad = ('socket', 'ssl', 'http.client', 'urllib.request', 'requests', 'httpx', 'telegram', "
             "'runtime.mvp_runtime.operator', 'runtime.mvp_runtime.providers', 'runtime.mvp_runtime.scheduler', "
             "'runtime.mvp_runtime.holdings.store'); "
             "print(sorted(m for m in sys.modules if any(m == b or m.startswith(b + '.') for b in bad)))")
    done = subprocess.run([sys.executable, "-c", probe, str(ROOT)], capture_output=True, text=True, cwd=ROOT,
                          timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "[]"


def test_nothing_in_the_runtime_imports_the_door():
    offenders = [path.relative_to(ROOT).as_posix() for path in (ROOT / "runtime").rglob("*.py")
                 if "c7_blocked_selftest" in path.read_text(encoding="utf-8")]
    assert offenders == []


def test_the_covered_sources_exist_and_include_pr_d():
    for rel in selftest.COVERED_SOURCES:
        assert (ROOT / rel).is_file(), rel
    assert "runtime/mvp_runtime/holdings/cash_flows.py" in selftest.COVERED_SOURCES
