"""The C4 harness (``scripts/ops/deploy_runner_c4.py``) and its workflow, held here before any runner sees them.

The runner is where C4's evidence comes from; this file only makes sure the harness asks the right questions:
every frame it sends gets the code it expects from the real gate and actor (in-process, a real ``ssh-keygen``
and throwaway keys, Docker never called), it refuses to run anywhere but a GitHub-hosted runner, its probes
report kinds and never content, and the workflow keeps the interpreter pinned and the job without secrets.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from runtime.mvp_runtime import socket_door
from runtime.mvp_runtime.deploy_runner import (actor as actor_mod, approval_verify as av, clock, config as config_mod,
                                               door, gate as gate_mod, private_files, startup, trust)
from runtime.mvp_runtime.deploy_runner.reasons import RunnerRefused
from scripts.ops import deploy_runner_c4 as c4
from scripts.ops import deploy_runner_install as install

REPO = Path(c4.__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "option-c-c4-separation.yml"
SSH_KEYGEN = shutil.which("ssh-keygen")
needs_keys = pytest.mark.skipif(SSH_KEYGEN is None or not private_files.private_files_supported(),
                                reason="needs ssh-keygen and POSIX private-file opens")
ME = os.getuid() if hasattr(os, "getuid") else 0
SHA = "d" * 40
RUNNING = "sha256:" + "b" * 64


def _keypair(directory: Path, name: str) -> Path:
    key = directory / name
    subprocess.run([SSH_KEYGEN, "-q", "-t", "ed25519", "-N", "", "-C", "synthetic", "-f", str(key)], check=True,
                   stdin=subprocess.DEVNULL, timeout=30)
    return key


def _signer(key: Path):
    def sign(data: bytes) -> str:
        return subprocess.run([SSH_KEYGEN, "-Y", "sign", "-q", "-f", str(key), "-n", c4.NAMESPACE], input=data,
                              capture_output=True, check=True, timeout=30).stdout.decode("ascii")
    return sign


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="ascii")
    path.chmod(0o644)
    return path


@pytest.fixture
def gate_and_actor(tmp_path):
    """The real G and X, wired in-process, with trust files naming one throwaway key; Docker refuses to be run."""
    keys = tmp_path / "keys"
    keys.mkdir(mode=0o700)
    thomas, attacker = _keypair(keys, "thomas"), _keypair(keys, "attacker")
    blob = thomas.with_suffix(".pub").read_text(encoding="ascii").split()[1]
    etc = tmp_path / "etc"
    etc.mkdir()
    allowed = _write(etc / "allowed_signers", f'{c4.PRINCIPAL} namespaces="{c4.NAMESPACE}" ssh-ed25519 {blob}\n')
    revoked = _write(etc / "revoked_keys", "")
    anchor = config_mod.load_anchor(_write(etc / "trust_anchor", f"ssh-ed25519 {blob}\n"), owner_uid=ME)
    xdir, gdir = tmp_path / "actor", tmp_path / "gate"
    for d in (xdir, gdir):
        d.mkdir(mode=0o700)
        d.chmod(0o700)
    store_id = install.genesis(xdir, install_sha=SHA, owner_uid=None)
    policy = clock.TimePolicy(max_lifetime_s=900, issue_skew_s=60, clock_jump_s=5, backstep_tolerance_s=5,
                              deadlines_s={"prepare_candidate": 300, "validate_candidate": 300, "promote": 300,
                                           "rollback": 300})

    def load_trust():
        return trust.load_trust(allowed, revoked, owner_uid=ME, principal=c4.PRINCIPAL, anchor=anchor)

    material = load_trust()
    result = startup.reconcile(xdir, runner_install_sha=SHA, trust_config_sha256=material.sha256,
                               now=datetime.now(timezone.utc), backstep_tolerance_s=5, expected_store_id=store_id)
    docker_calls = []

    def no_docker(*argv, **kw):
        docker_calls.append(argv)
        raise AssertionError("a refused request reached Docker")

    exe = Path(SSH_KEYGEN).resolve()
    actor = actor_mod.Actor(actor_mod.ActorConfig(
        directory=xdir, trees_root=Path("/srv/runner-trees"), docker=Path("/usr/bin/docker"), git=Path("/usr/bin/git"),
        runner_install_sha=SHA, environment=av.Environment("thomas_agent", c4.HOST_LABEL, "production"),
        policy=policy, load_trust=load_trust, load_revoked_approvals=lambda: frozenset(),
        verifier=av.SshKeygenVerifier(exe, c4.PRINCIPAL, exe_owner_uid=os.stat(exe).st_uid), run=no_docker), result)
    gate = gate_mod.Gate(gate_mod.GateConfig(
        directory=gdir, actor_socket=gdir / "x.sock",
        call_actor=lambda path, message, *, deadline_s: actor.handle(message, peer_uid=c4.IDS[c4.GATE])))
    cases = c4.build_cases(_signer(thomas), _signer(attacker), now=datetime.now(timezone.utc), sha=SHA,
                           running_id=RUNNING)
    return gate, cases, docker_calls, xdir


@needs_keys
def test_every_frame_gets_the_code_the_harness_expects_and_nothing_reaches_docker(gate_and_actor):
    gate, cases, docker_calls, xdir = gate_and_actor
    seen = set()
    for case in cases:
        if case.expect == "NO_REPLY":
            assert len(case.payload) > socket_door.MAX_FRAME_BYTES and b"\n" not in case.payload, case.label
            continue
        line = case.payload.split(b"\n", 1)[0]
        try:
            reply = gate.serve(door.decode_frame(line), c4.IDS[c4.CLAUDE])
        except RunnerRefused as exc:            # the door's own refusal, before G
            reply = {"ok": False, "reason_code": exc.reason_code}
        result, got = c4.judge_case(case, {"connected": True, "reply": reply})
        assert result == c4.PASS, (case.id, case.label, got)
        seen.add(case.id)
    assert seen == {"N-7", "N-8", "N-9", "N-10", "N-12"}
    assert docker_calls == []
    rows = (xdir / "consumed.jsonl").read_bytes().splitlines()
    assert len(rows) == 1                                   # nothing was spent: only the genesis row


def test_every_refusal_frame_is_refused_before_the_clock_so_none_spends_anything():
    """The refusal set must stay refusals whatever the runner's clock says (CLOCK-SOURCE = A): the frames that
    pass the clock are the effect tests', built on the runner against its own trees and images."""
    cases = c4.build_cases(lambda b: "sig", lambda b: "other", now=datetime.now(timezone.utc), sha=SHA,
                           running_id=RUNNING)
    assert not [c for c in cases if c.expect in ("OK", "CLOCK_UNTRUSTED")]


@pytest.mark.parametrize("environ, flag", [
    ({}, True),
    ({"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "self-hosted"}, True),
    ({"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted"}, False),
])
def test_run_refuses_anywhere_but_a_github_hosted_runner(environ, flag):
    with pytest.raises(c4.Refused):
        c4.require_throwaway_runner(flag, environ)


def test_run_refuses_before_touching_the_host(monkeypatch, tmp_path, capsys):
    for name in ("GITHUB_ACTIONS", "RUNNER_ENVIRONMENT"):
        monkeypatch.delenv(name, raising=False)

    def nothing(*a, **k):
        raise AssertionError("the harness started a process before its refusal")

    monkeypatch.setattr(c4.subprocess, "run", nothing)
    code = c4.main(["run", "--throwaway-runner", "--install-dir", str(tmp_path), "--install-sha", SHA,
                    "--out", str(tmp_path / "out")])
    assert code == c4.EXIT_REFUSED and "refused:" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


@pytest.mark.skipif(not private_files.private_files_supported(), reason="POSIX opens")
def test_a_probe_reports_kinds_and_leaves_nothing_behind(tmp_path):
    secret = _write(tmp_path / "secret", "C4_SENTINEL_value\n")
    results = c4.probe([{"op": "create", "target": str(tmp_path)}, {"op": "append", "target": str(tmp_path / "no")},
                        {"op": "read", "target": str(secret)}, {"op": "unknown", "target": "x"}])
    assert [r["outcome"] for r in results] == ["allowed", "missing", "allowed", "KeyError"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["secret"]
    assert "C4_SENTINEL" not in json.dumps(results).replace(str(secret), "")


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX") or os.name == "nt", reason="AF_UNIX")
def test_send_returns_the_reply_code_only_and_names_a_silent_close():
    directory = Path(tempfile.mkdtemp(prefix="c4s"))
    try:
        for answer, want in ((b'{"ok":false,"reason_code":"X_CODE","message":"text"}\n',
                              {"ok": False, "reason_code": "X_CODE"}), (b"", None)):
            path = directory / "s.sock"
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(str(path))
            server.listen(1)

            def serve():
                conn, _ = server.accept()
                conn.recv(65536)
                if answer:
                    conn.sendall(answer)
                conn.close()

            t = threading.Thread(target=serve)
            t.start()
            got = c4.send(str(path), b'{"proto":2}\n', timeout=10)
            t.join(10)
            server.close()
            path.unlink()
            assert got == {"connected": True, "reply": want}
        assert c4.send(str(directory / "absent.sock"), b"x\n")["connected"] is False
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_the_workflow_pins_the_interpreter_and_holds_no_secret():
    text = WORKFLOW.read_text(encoding="utf-8").replace("\r\n", "\n")      # a Windows checkout may convert
    sha = re.search(r'PBS_SHA256: "([0-9a-f]+)"', text)
    assert sha and len(sha.group(1)) == 64
    assert f"cpython-{c4.PYTHON_VERSION}+" in text and "x86_64-unknown-linux-gnu-install_only" in text
    assert text.index("sha256sum -c") < text.index("tar -xzf")          # checked before anything opens it
    assert "secrets." not in text and "persist-credentials: false" in text
    assert re.search(r"^permissions:\n  contents: read\n\n", text, re.M)
    assert "--throwaway-runner" in text and "scripts.ops.deploy_runner_c4 run" in text
    for line in text.splitlines():                                     # every action pinned to a commit
        if line.strip().startswith(("- uses:", "uses:")):
            assert re.search(r"@[0-9a-f]{40}\b", line), line


@pytest.mark.skipif(os.name == "nt" or not Path("/bin/cat").exists(), reason="POSIX cat")
def test_a_command_given_input_gets_it_as_its_stdin():
    """The first runner pass stopped here: stdin=DEVNULL beside input= is a ValueError in subprocess."""
    assert c4._sh(["/bin/cat"], input=b"signed bytes", capture_output=True).stdout == b"signed bytes"
    assert c4._sh(["/bin/cat"], capture_output=True).stdout == b""


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX") or os.name == "nt", reason="AF_UNIX")
def test_rtt_reports_each_code_and_a_wall_time_and_nothing_else():
    """CR-19 input: the round trip is a measurement, never a verdict, and carries no reply text."""
    directory = Path(tempfile.mkdtemp(prefix="c4r"))
    try:
        path = directory / "s.sock"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(path))
        server.listen(3)

        def serve():
            for answer in (b'{"ok":true}\n', b'{"ok":false,"reason_code":"INSPECT_RATE_LIMITED","message":"x"}\n'):
                conn, _ = server.accept()
                conn.recv(65536)
                conn.sendall(answer)
                conn.close()

        t = threading.Thread(target=serve)
        t.start()
        got = c4.rtt(str(path), b'{"proto":2}\n', count=2, spacing=0.01)
        t.join(10)
        server.close()
        assert got["codes"] == ["OK", "INSPECT_RATE_LIMITED"] and set(got) == {"codes", "ms"}
        assert all(isinstance(v, float) and v >= 0 for v in got["ms"])
    finally:
        shutil.rmtree(directory, ignore_errors=True)
