"""The patch-reach census (`scripts/ops/patch_reach.py`, crypto PR7).

It is evidence only if it counts a patch as missed exactly when a moved function no longer sees it, and
it must not change what it observes. So a fake split runs under the plugin in a subprocess: one test
patches the old home of a name a moved function reads, one patches the new home, one patches both with
one object, and one patches a name read by a function that stayed. All of them pass under the plugin,
and the census names the first as the only miss.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = "scripts.ops.patch_reach"

OLD = '''
from reach_fake.new import helper as helper, moved as moved


def stayed():
    return helper()
'''
NEW = '''
def helper():
    return "real"


def moved():
    return helper()


def never_called():
    return helper()
'''
TESTS = '''
from reach_fake import new, old


def test_patch_on_the_old_home(monkeypatch):
    monkeypatch.setattr(old, "helper", lambda: "fake")
    assert old.moved() == "real"          # the move cut the patch off, and the test still passes


def test_patch_on_the_new_home(monkeypatch):
    monkeypatch.setattr(new, "helper", lambda: "fake")
    assert old.moved() == "fake"


def test_one_object_on_both_homes(monkeypatch):
    fake = lambda: "fake"
    monkeypatch.setattr(old, "helper", fake)
    monkeypatch.setattr(new, "helper", fake)
    assert old.moved() == "fake"


def test_patch_read_by_a_function_that_stayed(monkeypatch):
    monkeypatch.setattr(old, "helper", lambda: "fake")
    assert old.stayed() == "fake"


def test_no_patch():
    assert old.moved() == "real"
'''


def _tree(tmp_path: Path) -> Path:
    package = tmp_path / "src" / "reach_fake"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "old.py").write_text(textwrap.dedent(OLD), encoding="utf-8")
    (package / "new.py").write_text(textwrap.dedent(NEW), encoding="utf-8")
    tests = tmp_path / "inner"
    tests.mkdir()
    (tests / "test_fake_split.py").write_text(textwrap.dedent(TESTS), encoding="utf-8")
    return tests


def _run(tmp_path: Path, *options: str) -> subprocess.CompletedProcess:
    tests = _tree(tmp_path)
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(tmp_path / 'src'), str(ROOT)])}
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", PLUGIN,
                           f"--rootdir={tests}", f"--basetemp={tmp_path / 'bt'}", *options, str(tests)],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)


def _read(out: Path) -> tuple[list[dict], dict[str, int]]:
    lines = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    return ([line for line in lines if "kind" in line],
            {line["calls"]: line["tests"] for line in lines if "calls" in line})


def test_a_patch_the_move_cut_off_is_the_only_miss(tmp_path):
    out = tmp_path / "reach.jsonl"
    run = _run(tmp_path, "--patch-reach-old=reach_fake.old", "--patch-reach-new=reach_fake.new",
               f"--patch-reach-out={out}")
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-2000:]     # every test passes under it
    assert "5 passed" in run.stdout
    hits, calls = _read(out)
    test = "test_fake_split.py::"
    assert sorted((hit["test"], hit["fn"], hit["kind"], tuple(hit["patched"])) for hit in hits) == sorted([
        (test + "test_patch_on_the_old_home", "reach_fake.new.moved", "missed", ("helper",)),
        (test + "test_patch_on_the_new_home", "reach_fake.new.moved", "own", ("helper",)),
        (test + "test_one_object_on_both_homes", "reach_fake.new.moved", "own", ("helper",)),
        (test + "test_patch_read_by_a_function_that_stayed", "reach_fake.old.stayed", "own", ("helper",)),
    ])
    # every watched function has a calls line, the uncalled one with 0
    assert calls == {"reach_fake.old.stayed": 1, "reach_fake.new.moved": 4, "reach_fake.new.helper": 2,
                     "reach_fake.new.never_called": 0}
    assert "1 missed hits" in run.stdout


def test_without_new_modules_it_counts_patches_in_the_old_module_only(tmp_path):
    """The base-commit run: before a move, only the functions still defined in the old module count."""
    out = tmp_path / "reach.jsonl"
    run = _run(tmp_path, "--patch-reach-old=reach_fake.old", f"--patch-reach-out={out}")
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-2000:]
    hits, calls = _read(out)
    assert [(hit["fn"], hit["kind"]) for hit in hits] == [("reach_fake.old.stayed", "own")]
    assert calls == {"reach_fake.old.stayed": 1}


def test_loaded_without_its_options_the_run_stops(tmp_path):
    """Loaded on purpose and told nothing to watch, it refuses rather than measure nothing."""
    run = _run(tmp_path)
    assert run.returncode == 4, run.stdout[-2000:] + run.stderr[-2000:]
    assert "--patch-reach-old" in run.stderr


def test_it_is_not_loaded_in_a_normal_run():
    for config in ("pytest.ini", "pyproject.toml", "setup.cfg", "tests/conftest.py", "conftest.py"):
        path = ROOT / config
        if path.exists():
            assert "patch_reach" not in path.read_text(encoding="utf-8"), config
