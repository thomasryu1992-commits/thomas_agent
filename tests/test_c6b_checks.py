"""The C6b b8 checks (``scripts/ops/c6b_checks.py``): synthetic only.

The probes themselves are the C4 harness's. They are proven against real accounts on the C4 runner. These
tests pin what this script decides:
- which targets it probes;
- that "allowed" anywhere is a FAIL;
- that it will not run as any account but ``claude``;
- how it reads the managed settings, the units, the processes and the two doors.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.ops import c6b_checks as c6b

REPO = Path(__file__).resolve().parents[1]
# The script runs on the Linux host as the `claude` account (os.geteuid, /proc, setpriv-made accounts); Windows has none.
pytestmark = pytest.mark.skipif(os.name == "nt", reason="the C6b checks run on the Linux host")
posix_only = pytest.mark.skipif(os.name == "nt", reason="the checks run on the Linux host")


def _paths(tmp_path: Path) -> c6b.Paths:
    return c6b.Paths(checkout=tmp_path / "checkout", env_copies=(tmp_path / "copy-1", tmp_path / "copy-2"),
                     docker_socket=tmp_path / "docker.sock", opt=tmp_path / "opt", config_dir=tmp_path / "etc-td",
                     lib=tmp_path / "lib", unit_dir=tmp_path / "units", gate_socket=tmp_path / "gate.sock",
                     managed_dir=tmp_path / "claude-code", door_socket=tmp_path / "door.sock",
                     capture_root=tmp_path / "capture", capture_program=tmp_path / "sbin" / "thomas-capture-door")


def _run(env="", workdir="/opt/thomas-deploy/x/app", program="/opt/thomas-deploy/x/venv/bin/python", pid="2147483646",
         guard_out='{"hookSpecificOutput":{"permissionDecision":"deny"}}'):
    def run(argv, **kw):
        if argv[:2] == [c6b.SYSTEMCTL, "show"] and "MainPID" in argv:
            return subprocess.CompletedProcess(argv, 0, pid + "\n", "")
        if argv[:2] == [c6b.SYSTEMCTL, "show"]:
            out = f"Environment={env}\nWorkingDirectory={workdir}\nExecStart={{ path={program} ; argv[]={program} -m x ; }}\n"
            return subprocess.CompletedProcess(argv, 0, out, "")
        if argv[0] == c6b.BASH:
            return subprocess.CompletedProcess(argv, 0, guard_out, "")
        raise AssertionError(argv)
    return run


def _managed(paths: c6b.Paths, *, name_both=True) -> None:
    hooks = paths.managed_dir / "hooks"
    hooks.mkdir(parents=True)
    for name in ("guard-local-only.sh", "block-main-commits.sh"):
        (hooks / name).write_text("#!/bin/bash\n", encoding="ascii")
        (hooks / name).chmod(0o755)
    named = ["guard-local-only.sh", "block-main-commits.sh"] if name_both else ["guard-local-only.sh"]
    settings = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": f"bash {hooks / n}"} for n in named]}]}}
    (paths.managed_dir / "managed-settings.json").write_text(json.dumps(settings), encoding="utf-8")


def _checks(tmp_path, *, outcome="denied", run=None, answers=None, **kw) -> c6b.Checks:
    paths = _paths(tmp_path)
    if not (paths.managed_dir / "managed-settings.json").exists():
        _managed(paths)
    (paths.opt / "abc").mkdir(parents=True, exist_ok=True)
    checks = c6b.Checks(paths=paths, ops={op: (lambda target, o=outcome: o) for op in c6b.c4.OPS},
                        run=run or _run(), access=lambda path, mode: False, owner_uid=os.getuid(), **kw)
    answers = {"door": {"ok": True}, "gate": {"ok": True, "reason_code": None},
               "capture": {"ok": False, "reason": c6b.CAPTURE_PROBE_REASON}, **(answers or {})}
    checks.ask = lambda path, frame: answers["door" if path == paths.door_socket else "gate"]
    checks.capture_round_trip = lambda: answers["capture"]
    checks.no_root_claude = lambda proc=None: checks.report.add("root", "Claude processes running as root", c6b.PASS)
    return checks


def _failed(report):
    return [r for r in report.rows if r["result"] == c6b.FAIL]


# --- who may run it ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("uid", [0, 2102, 10001])
def test_it_refuses_to_run_as_any_account_but_claude(tmp_path, uid, capsys):
    checks = _checks(tmp_path)
    assert c6b.main([], checks=checks, euid=uid) == c6b.EXIT_USAGE
    assert checks.report.rows == []


# --- refusals ----------------------------------------------------------------------------------------------

@posix_only
def test_every_refusal_denied_and_both_doors_answering_passes(tmp_path, capsys):
    out = tmp_path / "rows.json"
    assert c6b.main(["--json", str(out)], checks=_checks(tmp_path), euid=c6b.CLAUDE_UID) == c6b.EXIT_OK
    rows = json.loads(out.read_text(encoding="utf-8"))
    assert {r["id"] for r in rows} == {"N-1", "N-2", "N-3", "N-4", "N-5", "N-6", "N-13", "N-14", "N-15", "N-16",
                                       "guard", "root", "door", "gate", "capture"}
    assert "C6B_CHECKS=PASS" in capsys.readouterr().out


@posix_only
@pytest.mark.parametrize("outcome", ["allowed", "missing", "OSError"])
def test_anything_but_denied_on_a_refusal_is_a_fail(tmp_path, outcome):
    report = _checks(tmp_path, outcome=outcome).all()
    refusals = [r for r in report.rows if r["id"].startswith("N-") and r["id"] != "N-13"]
    assert refusals and all(r["result"] == c6b.FAIL for r in refusals)


def test_n1_covers_the_env_and_every_copy(tmp_path):
    checks = _checks(tmp_path)
    checks.refusals()
    labels = [r["label"] for r in checks.report.rows if r["id"] == "N-1"]
    p = checks.paths
    assert labels == [f"read {x}" for x in (p.checkout / ".env", *p.env_copies)]


def test_the_production_targets_are_the_hosts():
    p = c6b.Paths()
    assert p.checkout == Path("/root/thomas_agent") and len(p.env_copies) == 5
    assert p.state_root == Path("/root/thomas_agent/.runtime_governance_state")
    assert p.door_socket == Path("/var/lib/thomas-claude-door/sock/read.sock")
    assert p.capture_root == Path("/var/lib/thomas-capture")
    assert p.capture_program == Path("/usr/local/sbin/thomas-capture-door")
    assert p.gate_socket == Path("/run/thomas-deploy-gate/gate.sock")
    assert p.managed_dir == Path("/etc/claude-code")


def test_a_unit_with_no_running_process_is_a_fail(tmp_path):
    checks = _checks(tmp_path, run=_run(pid="0"))
    checks.refusals()
    assert [r["label"] for r in _failed(checks.report)] == [f"{u} main pid" for u in c6b.UNITS]


# --- configuration -----------------------------------------------------------------------------------------

def test_n13_fails_on_pythonpath_or_a_writable_working_directory(tmp_path):
    checks = _checks(tmp_path, run=_run(env="PYTHONPATH=/home/claude/x LANG=C.UTF-8"))
    checks.import_paths()
    assert any("PYTHONPATH" in r["label"] for r in _failed(checks.report))
    writable = _checks(tmp_path)
    writable.access = lambda path, mode: path.startswith("/opt/thomas-deploy/x/app")
    writable.import_paths()
    assert {r["label"].split(" ", 1)[1] for r in _failed(writable.report)} == {"working directory /opt/thomas-deploy/x/app"}


def test_n13_passes_on_an_unwritable_unit(tmp_path):
    checks = _checks(tmp_path)
    checks.import_paths()
    assert checks.report.rows and not _failed(checks.report)


def test_managed_settings_must_name_both_root_owned_hooks(tmp_path):
    paths = _paths(tmp_path)
    _managed(paths, name_both=False)
    checks = _checks(tmp_path)
    checks.managed_settings()
    assert [r["label"] for r in _failed(checks.report)] == [str(paths.managed_dir / "hooks" / "block-main-commits.sh")]


def test_a_group_writable_hook_copy_is_a_fail(tmp_path):
    checks = _checks(tmp_path)
    (checks.paths.managed_dir / "hooks" / "guard-local-only.sh").chmod(0o775)
    checks.managed_settings()
    assert any("guard-local-only.sh" in r["label"] for r in _failed(checks.report)) or os.name == "nt"


def test_no_managed_settings_is_a_fail(tmp_path):
    checks = _checks(tmp_path)
    (checks.paths.managed_dir / "managed-settings.json").unlink()
    checks.managed_settings()
    assert _failed(checks.report)


def test_a_guard_that_does_not_deny_is_a_fail(tmp_path):
    checks = _checks(tmp_path, run=_run(guard_out=""))
    checks.managed_settings()
    assert [r["label"] for r in _failed(checks.report)] == ["a holdings read fed to the managed guard"]


@posix_only
@pytest.mark.skipif(shutil.which("python3") is None, reason="the guard parses with python3")
def test_the_real_guard_denies_the_probe_this_script_feeds_it(tmp_path):
    """The payload shape is the one Claude Code sends; the repository's guard must deny it."""
    checks = _checks(tmp_path, run=subprocess.run)
    shutil.copy(REPO / ".claude" / "hooks" / "guard-local-only.sh", checks.paths.managed_dir / "hooks" / "guard-local-only.sh")
    checks.managed_settings()
    row = [r for r in checks.report.rows if r["label"] == "a holdings read fed to the managed guard"]
    assert row and row[0]["result"] == c6b.PASS


@posix_only
def test_a_claude_process_running_as_root_is_a_fail(tmp_path):
    proc = tmp_path / "proc"
    for pid, uid, cmd in (("10", 0, b"/root/.claude/remote/ccd-cli/2.1 --resume"), ("11", 2101, b"ccd-cli"),
                          ("12", 0, b"/usr/sbin/sshd")):
        (proc / pid).mkdir(parents=True)
        (proc / pid / "status").write_text(f"Name:\tx\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n", encoding="ascii")
        (proc / pid / "cmdline").write_bytes(cmd.replace(b" ", b"\0"))
    checks = c6b.Checks(paths=_paths(tmp_path))
    checks.no_root_claude(proc)
    assert checks.report.rows[0]["result"] == c6b.FAIL and checks.report.rows[0]["detail"] == "1 found"
    (proc / "10" / "status").write_text("Uid:\t2101\t2101\t2101\t2101\n", encoding="ascii")
    again = c6b.Checks(paths=_paths(tmp_path))
    again.no_root_claude(proc)
    assert again.report.rows[0]["result"] == c6b.PASS


# --- what must be reached ----------------------------------------------------------------------------------

@pytest.mark.parametrize("door, gate, failed", [
    ({"ok": True}, {"ok": False, "reason_code": "CANDIDATE_NOT_FOUND"}, []),   # an answer through the gate
    ({"ok": True}, {"ok": False, "reason_code": "PEER_NOT_PERMITTED"}, ["gate"]),
    ({"ok": True}, None, ["gate"]),
    ({"ok": False, "reason_code": "PEER_NOT_PERMITTED"}, {"ok": True}, ["door"]),
    (None, {"ok": True}, ["door"]),
])
def test_both_doors_must_answer_this_account(tmp_path, door, gate, failed):
    checks = _checks(tmp_path, answers={"door": door, "gate": gate})
    checks.reached()
    assert [r["id"] for r in _failed(checks.report)] == failed


@pytest.mark.parametrize("cap, failed", [
    ({"ok": False, "reason": "문지기 거절: non_global_address"}, []),
    (None, ["capture"]),                                                      # the door never answered
    ({"ok": False, "reason": "캡처 문지기 없음"}, ["capture"]),
    ({"ok": True, "status": 200}, ["capture"]),                               # it took a capture it must refuse
])
def test_the_capture_door_must_refuse_the_probe(tmp_path, cap, failed):
    checks = _checks(tmp_path, answers={"capture": cap})
    checks.reached()
    assert [r["id"] for r in _failed(checks.report)] == failed


def test_n16_covers_the_door_side_claude_must_not_touch(tmp_path):
    checks = _checks(tmp_path)
    checks.refusals()
    p = checks.paths
    labels = {r["label"] for r in checks.report.rows if r["id"] == "N-16"}
    assert labels == {f"read {p.capture_root / 'req'}", f"create {p.capture_root / 'out'}", f"traverse {p.capture_root / 'work'}",
                      f"append {p.capture_program}", f"create {p.capture_program.parent}",
                      *(f"append {p.unit_dir / u}" for u in c6b.CAPTURE_UNITS)}


@posix_only
def test_the_round_trip_drops_a_whole_request_and_reads_the_answer(tmp_path):
    """The real method against a fake door: the request appears under its final name only, and the answer is read."""
    import threading
    paths = _paths(tmp_path)
    (paths.capture_root / "req").mkdir(parents=True)
    (paths.capture_root / "out").mkdir()
    seen = {}

    def door():
        for _ in range(200):
            names = [n for n in os.listdir(paths.capture_root / "req") if not n.startswith(".")]
            if names:
                req = json.loads((paths.capture_root / "req" / names[0]).read_text(encoding="utf-8"))
                seen.update(req)
                (paths.capture_root / "out" / names[0]).write_text(
                    json.dumps({"ok": False, "reason": c6b.CAPTURE_PROBE_REASON}, ensure_ascii=False), encoding="utf-8")
                return
            threading.Event().wait(0.05)

    t = threading.Thread(target=door)
    t.start()
    got = c6b.Checks(paths=paths).capture_round_trip()
    t.join()
    assert got == {"ok": False, "reason": c6b.CAPTURE_PROBE_REASON}
    assert seen["url"] == c6b.CAPTURE_PROBE_URL and set(seen) == {"id", "url", "text", "click"}
    assert not [n for n in os.listdir(paths.capture_root / "req") if n.startswith(".")]


def test_the_script_names_no_secret_and_sends_no_mutating_verb():
    text = (REPO / "scripts" / "ops" / "c6b_checks.py").read_text(encoding="utf-8")
    for verb in ("prepare_candidate", "validate_candidate", "promote", "rollback"):
        assert f'"{verb}"' not in text, verb
    assert "read_text" not in text.split("def refusals")[1].split("def import_paths")[0]   # refusals never read
