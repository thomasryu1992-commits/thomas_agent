"""`scripts/c7_blocked_selftest.py`: the PR-D self-test door, pinned to D-1–D-10 of
`HOLDINGS_C7_PR_D_DEPLOY_VALIDATION_DESIGN_V0.1.md` §2.

The door is acceptable only while it stays shut to everything but its own synthetic lane. These tests check
that by calling it: it passes on the merged code, refuses input, prints a closed vocabulary, touches nothing
outside its temporary root under ``/tmp``, fails closed when that root cannot be made or a covered source cannot
be hashed, loads no network or delivery module, and its checks are not vacuous (a broken PR-D behaviour turns
them red). Synthetic only: a failing source is made in a copied tree, never by touching the repository.
"""

from __future__ import annotations

import ast
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace

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
LINE = re.compile(r"^(?:(?:PASS|FAIL) [a-z_]+|SOURCE [0-9a-f]{64} [\w./-]+|RESULT (?:PASS|FAIL))$")


@pytest.fixture
def temp_root(tmp_path, monkeypatch):
    """Stand in for ``/tmp`` with a pytest directory, so cleanup can be checked and Windows CI (no ``/tmp``)
    runs the same tests. The door's constant is the only seam; nothing at run time can move it."""
    root = tmp_path / "tmp"
    root.mkdir()
    monkeypatch.setattr(selftest, "SYNTHETIC_PARENT", root)
    return root


@pytest.fixture
def source_copy(tmp_path, monkeypatch):
    """A copy of the covered sources as the door's source root, so a failing source never touches the tree."""
    copy = tmp_path / "src"
    for rel in selftest.COVERED_SOURCES:
        (copy / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, copy / rel)
    monkeypatch.setattr(selftest, "ROOT", copy.resolve())
    return copy.resolve()


def _lines(capsys) -> list[str]:
    return capsys.readouterr().out.splitlines()


# --- it passes on the merged code, and says only what it may ------------------------------------------------

def test_every_check_passes_on_the_merged_code(temp_root, capsys):
    assert selftest.main([]) == 0
    lines = _lines(capsys)
    assert {line.split()[1] for line in lines if line.startswith("PASS ")} == EXPECTED_CHECKS | {"source_integrity"}
    assert not [line for line in lines if line.startswith("FAIL ")]
    assert lines[-1] == "RESULT PASS"


def test_the_output_is_a_closed_vocabulary(temp_root, capsys):
    selftest.main([])
    lines = _lines(capsys)
    assert lines and all(LINE.match(line) for line in lines), [line for line in lines if not LINE.match(line)]
    sources = [line.split()[2] for line in lines if line.startswith("SOURCE ")]
    assert sources == list(selftest.COVERED_SOURCES)


def test_source_hashes_match_the_files_in_this_tree():
    import hashlib
    hashes, complete = selftest.source_hashes()
    assert complete and [rel for _digest, rel in hashes] == list(selftest.COVERED_SOURCES)
    for digest, rel in hashes:
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

def test_every_lane_lives_under_the_temporary_root_and_is_removed(temp_root, tmp_path, monkeypatch, capsys):
    """Every lane opens in the fixed parent and is removed; Python's own temporary-directory choice
    (``tempfile.tempdir``, i.e. ``TMPDIR``) is never consulted, so it cannot pull a lane elsewhere."""
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(decoy))
    seen: list[pathlib.Path] = []
    real = cash_flows.ledger_path

    def recording(state_dir):
        seen.append(pathlib.Path(state_dir).resolve())
        return real(state_dir)
    monkeypatch.setattr(cash_flows, "ledger_path", recording)
    assert selftest.main([]) == 0
    assert seen and all(path.is_relative_to(temp_root.resolve()) for path in seen)
    assert list(temp_root.iterdir()) == []                                     # every lane cleaned up
    assert list(decoy.iterdir()) == []                                         # and none went elsewhere


def test_the_synthetic_parent_is_tmp():
    """The candidate run's tmpfs (design §2.5). Not resolved: on Windows ``/tmp`` would become a drive path."""
    assert selftest.SYNTHETIC_PARENT.as_posix() == "/tmp"


def test_every_lane_is_asked_for_in_the_fixed_parent(temp_root, monkeypatch, capsys):
    asked: list[object] = []
    real = tempfile.TemporaryDirectory

    def recording(*args, **kwargs):
        asked.append(kwargs.get("dir"))
        return real(*args, **kwargs)
    monkeypatch.setattr(tempfile, "TemporaryDirectory", recording)
    assert selftest.main([]) == 0
    assert asked and all(pathlib.Path(d) == temp_root.resolve() for d in asked)


def test_a_root_that_cannot_be_made_fails_closed_without_a_fallback(temp_root, monkeypatch, capsys):
    calls: list[object] = []

    def refuse(*_a, **_k):
        raise OSError("no temporary directory")
    monkeypatch.setattr(tempfile, "TemporaryDirectory", refuse)
    monkeypatch.setattr(cash_flows, "set_cutover", lambda *a, **k: calls.append(a))
    assert selftest.main([]) == 1
    lines = _lines(capsys)
    assert [line for line in lines if line.startswith("FAIL ")] and lines[-1] == "RESULT FAIL"
    assert calls == []                                                          # nothing ran anywhere else


def test_a_parent_under_a_state_directory_is_refused_before_any_lane(tmp_path, monkeypatch, capsys):
    trap = tmp_path / selftest.STATE_ROOT_NAME / "tmp"
    trap.mkdir(parents=True)
    calls: list[object] = []
    monkeypatch.setattr(selftest, "SYNTHETIC_PARENT", trap)
    monkeypatch.setattr(cash_flows, "set_cutover", lambda *a, **k: calls.append(a))
    assert selftest.main([]) == 1
    assert _lines(capsys) == ["FAIL synthetic_root_isolated", "RESULT FAIL"]
    assert calls == [] and list(trap.iterdir()) == []                           # no lane was started there


def test_a_missing_parent_fails_without_a_fallback(tmp_path, monkeypatch, capsys):
    """No ``/tmp`` (or an unusable one) is a FAIL; the door does not go looking for another directory."""
    absent = tmp_path / "absent"
    calls: list[object] = []
    monkeypatch.setattr(selftest, "SYNTHETIC_PARENT", absent)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(cash_flows, "set_cutover", lambda *a, **k: calls.append(a))
    assert selftest.main([]) == 1
    assert _lines(capsys) == ["FAIL synthetic_root_isolated", "RESULT FAIL"]
    assert calls == [] and not absent.exists() and list(tmp_path.iterdir()) == []


def test_a_root_made_outside_the_parent_starts_no_lane(temp_root, tmp_path, monkeypatch, capsys):
    """Should the directory come back somewhere else than asked, the lane refuses it before the cutover."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    calls: list[object] = []
    real = tempfile.TemporaryDirectory
    monkeypatch.setattr(tempfile, "TemporaryDirectory", lambda **kw: real(prefix="x-", dir=elsewhere))
    monkeypatch.setattr(cash_flows, "set_cutover", lambda *a, **k: calls.append(a))
    assert selftest.main([]) == 1
    lines = _lines(capsys)
    assert {line.split()[1] for line in lines if line.startswith("FAIL ")} == EXPECTED_CHECKS
    assert lines[-1] == "RESULT FAIL" and calls == [] and list(elsewhere.iterdir()) == []


# --- a covered source that cannot be hashed fails the whole run ----------------------------------------------

def _source_failure(capsys, code: int = 1) -> list[str]:
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert all(LINE.match(line) for line in lines), lines
    assert "FAIL source_integrity" in lines and lines[-1] == "RESULT FAIL"
    assert "Traceback" not in captured.out + captured.err and captured.err == ""
    return lines


def test_a_complete_copy_passes_and_hashes_like_the_tree(temp_root, source_copy, capsys):
    import hashlib
    assert selftest.main([]) == 0
    lines = _lines(capsys)
    assert "PASS source_integrity" in lines and lines[-1] == "RESULT PASS"
    for line in (line for line in lines if line.startswith("SOURCE ")):
        _word, digest, rel = line.split()
        assert digest == hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()


@pytest.mark.parametrize("gone", [
    ("runtime/mvp_runtime/holdings/cash_flows.py",),
    ("scripts/c7_blocked_selftest.py",),
    ("runtime/mvp_runtime/holdings/store.py", "runtime/mvp_runtime/scheduler.py", "scripts/holdings_board.py"),
])
def test_a_missing_source_fails_although_every_check_passes(temp_root, source_copy, capsys, gone):
    for rel in gone:
        (source_copy / rel).unlink()
    assert selftest.main([]) == 1
    lines = _source_failure(capsys)
    assert {line.split()[1] for line in lines if line.startswith("PASS ")} == EXPECTED_CHECKS  # checks all passed
    assert sorted(line.split()[2] for line in lines if line.startswith("SOURCE ")) == sorted(
        set(selftest.COVERED_SOURCES) - set(gone))                                # a gone file is not reported


@pytest.mark.parametrize("error", [OSError("disk detail"), PermissionError("permission detail")])
def test_a_source_that_cannot_be_read_fails(temp_root, source_copy, monkeypatch, capsys, error):
    real = pathlib.Path.read_bytes

    def failing(self):
        if self.name == "scheduler.py" and self.is_relative_to(source_copy):
            raise error
        return real(self)
    monkeypatch.setattr(pathlib.Path, "read_bytes", failing)
    assert selftest.main([]) == 1
    assert "detail" not in "\n".join(_source_failure(capsys))


def test_a_hash_that_fails_fails(temp_root, monkeypatch, capsys):
    def boom(*_a):
        raise ValueError("hash detail")
    monkeypatch.setattr(selftest, "hashlib", SimpleNamespace(sha256=boom))   # the door's name only
    assert selftest.main([]) == 1
    lines = _source_failure(capsys)
    assert "detail" not in "\n".join(lines) and not [line for line in lines if line.startswith("SOURCE ")]


@pytest.mark.parametrize("rel", ["../outside.py", "scripts/../../outside.py", "ABSOLUTE",
                                 ".runtime_governance_state/x.py", "scripts"])
def test_a_source_path_outside_the_root_or_not_a_file_fails(temp_root, source_copy, monkeypatch, capsys, rel):
    (source_copy.parent / "outside.py").write_text("x = 1\n", encoding="utf-8")
    rel = (source_copy.parent / "outside.py").as_posix() if rel == "ABSOLUTE" else rel
    monkeypatch.setattr(selftest, "COVERED_SOURCES", selftest.COVERED_SOURCES + (rel,))
    assert selftest.main([]) == 1
    lines = _source_failure(capsys)
    assert rel not in [line.split()[2] for line in lines if line.startswith("SOURCE ")]   # never reported


def test_a_symlinked_source_is_not_followed_out_of_the_root(temp_root, source_copy, monkeypatch, capsys):
    """A real symlink where the platform allows one; otherwise the same redirection, simulated — never a skip
    (the Windows skip ceiling is exact)."""
    outside = source_copy.parent / "outside.py"
    outside.write_text("x = 1\n", encoding="utf-8")
    link = source_copy / "scripts" / "holdings_board.py"
    link.unlink()
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        link.write_text("placeholder\n", encoding="utf-8")
        real = pathlib.Path.resolve
        monkeypatch.setattr(pathlib.Path, "resolve",
                            lambda self, strict=False: outside if self == link else real(self, strict=strict))
    assert selftest.main([]) == 1
    _source_failure(capsys)


def test_the_covered_sources_are_fixed_code_files():
    """Only repository code: relative, inside ``runtime/`` or ``scripts/``, Python, never a state file."""
    for rel in selftest.COVERED_SOURCES:
        path = pathlib.PurePosixPath(rel)
        assert not path.is_absolute() and ".." not in path.parts and path.suffix == ".py", rel
        assert path.parts[0] in {"runtime", "scripts"} and selftest.STATE_ROOT_NAME not in path.parts, rel


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
    for forbidden in ("environ", "getenv", "stdin", "STATE_REL", "store", "tempdir", "gettempdir"):
        assert forbidden not in attributes | names, forbidden
    called = {getattr(node.func, "attr", getattr(node.func, "id", None)) for node in ast.walk(tree)
              if isinstance(node, ast.Call)}
    banned = {"state_dir", "input", "getenv", "gettempdir"}
    assert not called & banned, called & banned
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
