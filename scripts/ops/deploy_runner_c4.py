"""Option C C4 — the separation tests on a throwaway GitHub Actions runner.

Thomas 2026-10-17: ``결정: … D-8 C4 환경 GitHub Actions 임시 러너(무료 한도 안, 비필수 workflow — CI 변경은 별도
승인)`` and ``승인: C4 시험 workflow 구현 및 PR 생성(GitHub Actions 임시 러너, 비필수, Python 3.12.13 PBS SHA256 고정,
N-1~N-14·누출 검사; 병합·운영 제외)``. The plan is ``docs/proposals/HOST_PRIVILEGE_SEPARATION_C4_PLAN_V0.1.md``.

The workflow (``.github/workflows/option-c-c4-separation.yml``) verifies and unpacks the pinned interpreter,
builds the install at ``/opt/thomas-deploy/<sha>``, and then runs, as root and with that install's own
interpreter::

    /opt/thomas-deploy/<sha>/venv/bin/python -I -m scripts.ops.deploy_runner_c4 run \
        --install-dir /opt/thomas-deploy/<sha> --install-sha <sha> --out <dir>

``run`` creates three accounts, the root-owned configuration and synthetic trust files, the actor's store
(the C3b install tool's ``genesis``), the two units (its ``render-units``), starts them under systemd, and
then tests N-1–N-14 and the leak sweep. Every probe that must be refused runs as the account it is about
(``setpriv``), never as root. Results are kinds and codes, never values: no sentinel, no key, no file
content leaves a probe.

**It refuses to run anywhere but a GitHub-hosted runner** (``GITHUB_ACTIONS``/``RUNNER_ENVIRONMENT``, root,
systemd as PID 1, ``--throwaway-runner``). It creates accounts and systemd units; on this project's host that
would be a host privilege change, which no approval covers.

What cannot be tested here, and why, is reported as BLOCKED or NOT_TESTED rather than bent into a pass:
- the clock has no decided source (CLOCK-SOURCE), so every validly signed ``prepare_candidate`` stops at
  ``CLOCK_UNTRUSTED`` before the operation lock, the measurement and the spend (C4 plan P-5). The spend,
  its reuse (N-11), the measured binding (the image half of N-10) and Docker failures after the spend
  (part of N-12) are BLOCKED;
- ``promote`` is refused by a code constant (CR-7), so N-8's promote case is that refusal.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import errno
import json
import os
import platform
import secrets
import socket
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_REFUSED = 0, 1, 2, 3
PYTHON_VERSION = "3.12.13"                     # CR-8b (Thomas 2026-10-17)

# Accounts. Fixed ids so the configuration's uid lists are literals, as on the target host.
CLAUDE, GATE, ACTOR = "claude-c4", "thomas-deploy-gate", "thomas-deploy-actor"
IDS = {CLAUDE: 2101, GATE: 2102, ACTOR: 2103}
STATE_UID = 10001                              # the services' uid on the real host; owns the synthetic state root

CONFIG_DIR = Path("/etc/thomas-deploy")
UNIT_DIR = Path("/etc/systemd/system")
LIB = Path("/var/lib/thomas-deploy")
GATE_DIR, ACTOR_DIR, TREES = LIB / "gate", LIB / "actor", LIB / "trees"
GATE_SOCKET = Path("/run/thomas-deploy-gate/gate.sock")     # under each unit's RuntimeDirectory
ACTOR_SOCKET = Path("/run/thomas-deploy-actor/actor.sock")
SITE = Path("/srv/thomas-c4/thomas_agent")                  # stands where the real checkout would
FAKE_ENV = SITE / ".env"
STATE_ROOT = SITE / ".runtime_governance_state"
HOLDINGS = STATE_ROOT / "holdings"
KEYS = Path("/root/c4-keys")
CASES_DIR = Path("/var/tmp/c4-cases")
POISON = Path("/var/tmp/c4-poison")
DOCKER_SOCKET = Path("/var/run/docker.sock")
SETPRIV, SUDO, SSH_KEYGEN, SYSTEMCTL = "/usr/bin/setpriv", "/usr/bin/sudo", "/usr/bin/ssh-keygen", "/usr/bin/systemctl"

PRINCIPAL = "thomas-deploy-approver"
NAMESPACE = "deploy-approval.v1@thomas-agent"
HOST_LABEL = "c4-runner"
PR = 4242
ACTOR_DEADLINE_S = 60      # a prepare builds an image; the frozen-actor case waits this long
BASE_CTX = Path("/var/tmp/c4-base")
GOOD_PR, BAD_PR = 4242, 4244
PASS, FAIL, BLOCKED, NOT_TESTED, INFO = "PASS", "FAIL", "BLOCKED", "NOT_TESTED", "INFO"


class Refused(Exception):
    """The harness will not run here. The message is a fixed phrase."""


# --- the frames N-7–N-12 send through the gate socket -------------------------------------------------

@dataclass(frozen=True)
class Case:
    id: str              # the test it belongs to (N-7 … N-12, P-5)
    label: str
    payload: bytes
    expect: str          # a reason code, or "NO_REPLY"


def _stamp(dt: datetime) -> str:
    from scripts.lib.utctime import format_utc

    return format_utc(dt)


def approval_document(n: int, *, now: datetime, pr: int = PR, sha: str, running_id: str,
                      host_label: str = HOST_LABEL, issued: timedelta = timedelta(minutes=-1),
                      lifetime: timedelta = timedelta(minutes=14)) -> bytes:
    """A synthetic ``prepare_candidate`` approval's canonical bytes."""
    from runtime.read_only_kernel import integrity

    doc = {"schema": "thomas.deploy_approval.v1", "approval_id": "dap_" + "abcdefghijklmnopqrstuvwxyz"[n] * 26,
           "nonce": f"{n + 1:032x}", "action": "prepare_candidate",
           "environment": {"project": "thomas_agent", "host_label": host_label, "profile": "production"},
           "target": {"pr": pr, "git_commit_sha": sha, "candidate_tag": f"candidate-{pr}", "candidate_image_id": None,
                      "rollback_tag": f"rollback-pre-{pr}", "rollback_image_id": running_id},
           "issued_at": _stamp(now + issued), "expires_at": _stamp(now + issued + lifetime),
           "signature_namespace": NAMESPACE}
    return integrity.canonical_bytes(doc)


def _frame(request_id: str, verb: str, args: dict[str, Any], document: bytes | None = None,
           signature: str | None = None, **extra: Any) -> bytes:
    body: dict[str, Any] = {"proto": 2, "client_id": "c4", "request_id": request_id, "verb": verb, "args": args}
    if document is not None:
        body["approval_document"] = base64.b64encode(document).decode("ascii")
    if signature is not None:
        body["approval_signature"] = signature
    body.update(extra)
    return (json.dumps(body, separators=(",", ":")) + "\n").encode("ascii")


def build_cases(sign: Callable[[bytes], str], sign_other: Callable[[bytes], str], *, now: datetime,
                sha: str, running_id: str) -> list[Case]:
    """Every frame the gate is sent, with the code it must answer. ``sign`` is the trusted synthetic key,
    ``sign_other`` a key the trust files do not name. The expected codes are the C3 synthetic results; the
    suite holds them against the in-process gate and actor with a real ``ssh-keygen``."""
    prep = {"pr": PR, "git_commit_sha": sha}
    promote = {"pr": PR, "git_commit_sha": sha, "candidate_image_id": "sha256:" + "a" * 64,
               "rollback_image_id": running_id}
    good = approval_document(0, now=now, sha=sha, running_id=running_id)
    expired = approval_document(1, now=now, sha=sha, running_id=running_id, issued=timedelta(minutes=-20))
    elsewhere = approval_document(2, now=now, sha=sha, running_id=running_id, host_label="another-host")
    return [
        Case("N-7", "unknown verb", _frame("c4-n7-1", "exec", {}), "VERB_NOT_PERMITTED"),
        Case("N-7", "extra argument key", _frame("c4-n7-2", "promote", {**promote, "tree": "/"}),
             "ARGUMENT_NOT_ACCEPTED"),
        Case("N-7", "extra frame key", _frame("c4-n7-3", "inspect_candidate", {"pr": PR}, tree="/"),
             "ARGUMENT_NOT_ACCEPTED"),
        Case("N-7", "a path as the PR", _frame("c4-n7-4", "prepare_candidate", {"pr": "/etc/passwd",
                                                                               "git_commit_sha": sha}),
             "ARGUMENT_INVALID"),
        Case("N-8", "promote without an approval", _frame("c4-n8-1", "promote", promote),
             "OPERATION_BLOCKED_BY_POLICY"),
        Case("N-8", "prepare without an approval", _frame("c4-n8-2", "prepare_candidate", prep),
             "APPROVAL_REQUIRED"),
        Case("N-8", "prepare signed by an untrusted key",
             _frame("c4-n8-3", "prepare_candidate", prep, good, sign_other(good)), "SIGNATURE_INVALID"),
        Case("N-9", "expired approval", _frame("c4-n9-1", "prepare_candidate", prep, expired, sign(expired)),
             "APPROVAL_EXPIRED"),
        Case("N-10", "approval for another PR",
             _frame("c4-n10-1", "prepare_candidate", {**prep, "pr": PR + 1}, good, sign(good)),
             "APPROVAL_BINDING_MISMATCH"),
        Case("N-10", "approval for another commit",
             _frame("c4-n10-2", "prepare_candidate", {**prep, "git_commit_sha": "f" * 40}, good, sign(good)),
             "APPROVAL_BINDING_MISMATCH"),
        Case("N-10", "approval for another environment",
             _frame("c4-n10-3", "prepare_candidate", prep, elsewhere, sign(elsewhere)),
             "APPROVAL_ENVIRONMENT_MISMATCH"),
        Case("N-12", "bytes that are not a frame", b"\x00\xffnot json\n", "MALFORMED_REQUEST"),
        Case("N-12", "a frame past the door's ceiling", b"{" + b"a" * 9000, "NO_REPLY"),
    ]


# --- what a probe does, as the account it runs as -----------------------------------------------------

def _kind(exc: BaseException) -> str:
    if isinstance(exc, PermissionError) or getattr(exc, "errno", None) in (errno.EACCES, errno.EPERM):
        return "denied"
    if isinstance(exc, FileNotFoundError):
        return "missing"
    return type(exc).__name__


def _op_read(path: str) -> str:
    if os.access(path, os.R_OK):
        return "allowed"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        return _kind(exc)
    os.close(fd)             # opened and closed unread: still a FAIL
    return "allowed"


def _op_create(directory: str) -> str:
    target = os.path.join(directory, f".c4-probe-{secrets.token_hex(4)}")
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except OSError as exc:
        return _kind(exc)
    os.close(fd)
    os.unlink(target)
    return "allowed"


def _op_append(path: str) -> str:
    try:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)   # never O_TRUNC; nothing is written
    except OSError as exc:
        return _kind(exc)
    os.close(fd)
    return "allowed"


def _op_traverse(path: str) -> str:
    return "allowed" if os.access(path, os.R_OK) or os.access(path, os.X_OK) else "denied"


def _op_writable(path: str) -> str:
    return "allowed" if os.access(path, os.W_OK) else "denied"


def _op_connect(path: str) -> str:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(5)
    try:
        client.connect(path)
    except OSError as exc:
        return _kind(exc)
    finally:
        client.close()
    return "allowed"


def _op_sudo(_: str) -> str:
    done = subprocess.run([SUDO, "-n", "true"], stdin=subprocess.DEVNULL, capture_output=True, timeout=15)
    return "allowed" if done.returncode == 0 else "denied"


def _op_environ(pid: str) -> str:
    return _op_read(f"/proc/{int(pid)}/environ")


def _op_ptrace(pid: str) -> str:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.ptrace.restype = ctypes.c_long
    if libc.ptrace(16, int(pid), None, None) == 0:          # PTRACE_ATTACH
        libc.ptrace(17, int(pid), None, None)               # PTRACE_DETACH
        return "allowed"
    return "denied" if ctypes.get_errno() in (errno.EPERM, errno.EACCES) else errno.errorcode.get(ctypes.get_errno(), "?")


OPS: dict[str, Callable[[str], str]] = {
    "read": _op_read, "create": _op_create, "append": _op_append, "traverse": _op_traverse,
    "writable": _op_writable, "connect": _op_connect, "sudo": _op_sudo, "environ": _op_environ,
    "ptrace": _op_ptrace,
}


def probe(checks: list[dict[str, str]]) -> list[dict[str, str]]:
    """Run each ``{"op", "target"}`` as this process's account; the outcome is a kind, never content."""
    out = []
    for check in checks:
        try:
            outcome = OPS[check["op"]](check["target"])
        except Exception as exc:  # noqa: BLE001 — the kind is the result
            outcome = _kind(exc)
        out.append({**check, "outcome": outcome})
    return out


def send(path: str, payload: bytes, *, timeout: float = 180.0) -> dict[str, Any]:
    """One frame to a socket; the reply's ``ok`` and ``reason_code`` only."""
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(path)
    except OSError as exc:
        return {"connected": False, "kind": _kind(exc)}
    try:
        client.sendall(payload)
        data = b""
        while not data.endswith(b"\n") and len(data) < 65536:
            chunk = client.recv(65536)
            if not chunk:
                break
            data += chunk
    except OSError as exc:
        return {"connected": True, "kind": _kind(exc)}
    finally:
        client.close()
    if not data:
        return {"connected": True, "reply": None}
    try:
        reply = json.loads(data)
    except ValueError:
        return {"connected": True, "reply": "unparseable"}
    return {"connected": True, "reply": {"ok": reply.get("ok"), "reason_code": reply.get("reason_code")}}


def judge_case(case: Case, answer: dict[str, Any]) -> tuple[str, str]:
    reply = answer.get("reply")
    if case.expect == "NO_REPLY":
        got = "NO_REPLY" if answer.get("connected") and reply is None else json.dumps(answer, sort_keys=True)
    elif isinstance(reply, dict):
        got = "OK" if reply.get("ok") else str(reply.get("reason_code"))
    else:
        got = json.dumps(answer, sort_keys=True)
    return (PASS if got == case.expect else FAIL), got


# --- the run on the throwaway runner ------------------------------------------------------------------

def require_throwaway_runner(flag: bool, environ: dict[str, str] | None = None) -> None:
    env = os.environ if environ is None else environ
    if not flag:
        raise Refused("run needs --throwaway-runner")
    if env.get("GITHUB_ACTIONS") != "true" or env.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise Refused("run is for a GitHub-hosted runner only")
    if os.geteuid() != 0:
        raise Refused("run needs root on the runner")
    try:
        pid1 = Path("/proc/1/comm").read_text(encoding="ascii").strip()
    except OSError:
        pid1 = ""
    if pid1 != "systemd":
        raise Refused("systemd is not PID 1 here; the units cannot be tested (NOT_TESTED)")


@dataclass
class Report:
    rows: list[dict[str, str]] = field(default_factory=list)

    def add(self, test: str, label: str, result: str, detail: str = "") -> None:
        self.rows.append({"id": test, "label": label, "result": result, "detail": detail})
        print(f"[{result}] {test} {label} {detail}".rstrip(), flush=True)

    @property
    def failed(self) -> bool:
        return any(r["result"] == FAIL for r in self.rows)


def _sh(argv: list[str], **kw: Any) -> subprocess.CompletedProcess:
    if "input" not in kw:                     # ``input`` is the stdin; the two cannot both be given
        kw["stdin"] = subprocess.DEVNULL
    return subprocess.run(argv, check=True, **kw)


def _as(account: str, argv: list[str], *, cwd: str = "/", env: dict[str, str] | None = None,
        **kw: Any) -> subprocess.CompletedProcess:
    uid = IDS[account]
    return subprocess.run([SETPRIV, f"--reuid={uid}", f"--regid={uid}", "--init-groups", "--reset-env", "--",
                           *(["/usr/bin/env", *[f"{k}={v}" for k, v in (env or {}).items()]] if env else []),
                           *argv], cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, timeout=120, **kw)


class Runner:
    def __init__(self, install_dir: Path, install_sha: str, out: Path) -> None:
        self.install = install_dir
        self.sha = install_sha
        self.out = out
        self.python = str(install_dir / "venv" / "bin" / "python")
        self.app = install_dir / "app"
        self.report = Report()
        self.sentinel = "C4_SENTINEL_" + secrets.token_hex(16)

    # setup -------------------------------------------------------------------------------------------
    def accounts(self) -> None:
        for name, uid in IDS.items():
            _sh(["groupadd", "-g", str(uid), name])
            _sh(["useradd", "-u", str(uid), "-g", str(uid), "-M", "-d", "/nonexistent", "-s", "/usr/sbin/nologin",
                 name])
        # The probes take their groups from /etc/group (setpriv --init-groups), so the groups the units add
        # are written there too: X in docker (the actor unit), G in X's group (the gate unit), and the
        # caller in G's group so it can reach the gate socket's directory.
        _sh(["usermod", "-aG", "docker", ACTOR])
        _sh(["usermod", "-aG", ACTOR, GATE])
        _sh(["usermod", "-aG", GATE, CLAUDE])

    def files(self) -> tuple[str, Path, Path]:
        from runtime.mvp_runtime.deploy_runner.config import environment_fingerprint
        from scripts.ops import deploy_runner_install as install

        for d, mode, owner in ((CONFIG_DIR, 0o755, 0), (LIB, 0o755, 0), (TREES, 0o755, 0),
                               (GATE_DIR, 0o700, IDS[GATE]), (ACTOR_DIR, 0o700, IDS[ACTOR]),
                               (SITE, 0o755, 0), (STATE_ROOT, 0o700, STATE_UID), (HOLDINGS, 0o700, STATE_UID),
                               (KEYS, 0o700, 0)):
            d.mkdir(parents=True, exist_ok=True)
            os.chown(d, owner, owner)
            d.chmod(mode)
        fd = os.open(FAKE_ENV, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, f"C4_FAKE_SECRET={self.sentinel}\n".encode("ascii"))
        finally:
            os.close(fd)
        for name in ("thomas", "attacker"):
            _sh([SSH_KEYGEN, "-q", "-t", "ed25519", "-N", "", "-C", "c4-synthetic", "-f", str(KEYS / name)],
                capture_output=True)
        blob = (KEYS / "thomas.pub").read_text(encoding="ascii").split()[1]
        self._write(CONFIG_DIR / "allowed_signers", f'{PRINCIPAL} namespaces="{NAMESPACE}" ssh-ed25519 {blob}\n')
        for name in ("revoked_keys", "revoked_approvals"):
            self._write(CONFIG_DIR / name, "")
        self._write(CONFIG_DIR / "trust_anchor", f"ssh-ed25519 {blob}\n")
        store_id = install.genesis(ACTOR_DIR, install_sha=self.sha, owner_uid=IDS[ACTOR])
        common = {"schema": "thomas.deploy_runner_config.v1", "install_sha": self.sha,
                  "environment": {"project": "thomas_agent", "host_label": HOST_LABEL, "profile": "production"},
                  "fingerprint": environment_fingerprint()}
        actor = {**common, "role": "actor", "socket": str(ACTOR_SOCKET), "directory": str(ACTOR_DIR),
                 "allowed_uids": [IDS[GATE]],
                 "actor": {"trees_root": str(TREES), "docker_bin": "/usr/bin/docker", "git_bin": "/usr/bin/git",
                           "ssh_keygen_bin": SSH_KEYGEN, "trust_anchor": str(CONFIG_DIR / "trust_anchor"),
                           "allowed_signers": str(CONFIG_DIR / "allowed_signers"),
                           "revoked_keys": str(CONFIG_DIR / "revoked_keys"),
                           "revoked_approvals": str(CONFIG_DIR / "revoked_approvals"), "trust_owner_uid": 0,
                           "principal": PRINCIPAL, "store_id": store_id,
                           "policy": {"max_lifetime_s": 900, "issue_skew_s": 60, "clock_jump_s": 5,
                                      "backstep_tolerance_s": 5,
                                      "deadlines_s": {"prepare_candidate": 300, "validate_candidate": 300,
                                                      "promote": 300, "rollback": 300}},
                           "inspect_min_interval_s": 1.0}}
        gate = {**common, "role": "gate", "socket": str(GATE_SOCKET), "directory": str(GATE_DIR),
                "allowed_uids": [IDS[CLAUDE]],
                "gate": {"actor_socket": str(ACTOR_SOCKET), "actor_deadline_s": ACTOR_DEADLINE_S}}
        self._write(CONFIG_DIR / "actor.json", json.dumps(actor, indent=1))
        self._write(CONFIG_DIR / "gate.json", json.dumps(gate, indent=1))
        units = install.render_units(UNIT_DIR, {
            "INSTALL_DIR": str(self.install), "CONFIG_DIR": str(CONFIG_DIR), "GATE_USER": GATE, "GATE_GROUP": GATE,
            "ACTOR_USER": ACTOR, "ACTOR_GROUP": ACTOR, "GATE_DIR": str(GATE_DIR), "ACTOR_DIR": str(ACTOR_DIR)})
        return store_id, units[0], units[1]

    def install_tool_checks(self) -> None:
        """What the install tool must have left (C5 preparation): the ``.pth`` naming the tree, and the
        actor's private ``docker-config`` directory from ``genesis``."""
        import sysconfig

        from runtime.mvp_runtime.deploy_runner.commands import DOCKER_CONFIG_DIR
        from scripts.ops.deploy_runner_install import PTH_NAME

        pth = Path(sysconfig.get_paths()["purelib"]) / PTH_NAME
        ok = pth.is_file() and pth.read_text(encoding="ascii").strip() == str(self.app) and pth.stat().st_uid == 0
        self.report.add("SETUP", "install tool: the .pth names the install's tree (root-owned)", PASS if ok else FAIL)
        d = ACTOR_DIR / DOCKER_CONFIG_DIR
        st = d.lstat() if d.exists() else None
        ok = st is not None and stat.S_ISDIR(st.st_mode) and stat.S_IMODE(st.st_mode) == 0o700 and st.st_uid == IDS[ACTOR]
        self.report.add("SETUP", "install tool: genesis made the actor's docker-config (0700, X's)",
                        PASS if ok else FAIL)

    @staticmethod
    def _write(path: Path, text: str) -> None:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        try:
            os.write(fd, text.encode("ascii"))
        finally:
            os.close(fd)

    def start(self) -> bool:
        for role, account in (("actor", ACTOR), ("gate", GATE)):
            done = _as(account, [self.python, "-I", "-m", "runtime.mvp_runtime.deploy_runner.serve", "--role", role,
                                 "--config", str(CONFIG_DIR / f"{role}.json"), "--check"], cwd=str(self.install))
            self.report.add("SETUP", f"{role} start checks as its account (serve --check)",
                            PASS if done.returncode == 0 else FAIL, done.stderr.decode("ascii", "replace").strip())
            if done.returncode:
                return False
        _sh([SYSTEMCTL, "daemon-reload"])
        _sh([SYSTEMCTL, "start", "thomas-deploy-actor.service"])
        _sh([SYSTEMCTL, "start", "thomas-deploy-gate.service"])
        return self._wait_active() and self._wait_socket(GATE_SOCKET) and self._wait_socket(ACTOR_SOCKET)

    def _wait_active(self, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            states = [self._active(u) for u in ("thomas-deploy-actor", "thomas-deploy-gate")]
            if all(states):
                self.report.add("SETUP", "both units active under systemd", PASS)
                return True
            time.sleep(0.5)
        self.report.add("SETUP", "both units active under systemd", FAIL, "not active")
        return False

    @staticmethod
    def _active(unit: str) -> bool:
        return subprocess.run([SYSTEMCTL, "is-active", "--quiet", f"{unit}.service"]).returncode == 0

    def _wait_socket(self, path: Path, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.is_socket():
                return True
            time.sleep(0.5)
        self.report.add("SETUP", f"socket {path.name} present", FAIL, "absent")
        return False

    @staticmethod
    def main_pid(unit: str) -> int:
        out = subprocess.run([SYSTEMCTL, "show", "-p", "MainPID", "--value", f"{unit}.service"], capture_output=True,
                             text=True, check=True).stdout.strip()
        return int(out or 0)

    # probes ------------------------------------------------------------------------------------------
    def run_probe(self, account: str, checks: list[dict[str, str]]) -> list[dict[str, str]]:
        done = _as(account, [self.python, "-I", "-m", "scripts.ops.deploy_runner_c4", "probe",
                             "--checks", json.dumps(checks)])
        if done.returncode:
            return [{**c, "outcome": "probe_failed"} for c in checks]
        return json.loads(done.stdout)

    def expect(self, test: str, account: str, checks: list[tuple[str, str, str]]) -> None:
        """``checks`` = (op, target, expected outcome)."""
        results = self.run_probe(account, [{"op": op, "target": target} for op, target, _ in checks])
        for (op, target, want), got in zip(checks, results):
            self.report.add(test, f"{account} {op} {target}", PASS if got["outcome"] == want else FAIL,
                            f"want {want}, got {got['outcome']}")

    def host_tests(self, store: Path, units: tuple[Path, Path]) -> None:
        x_pid, g_pid = self.main_pid("thomas-deploy-actor"), self.main_pid("thomas-deploy-gate")
        self.expect("N-1", CLAUDE, [("read", str(FAKE_ENV), "denied")])
        for account in (CLAUDE, GATE):
            self.expect("N-2", account, [("connect", str(DOCKER_SOCKET), "denied")])
        self.expect("N-2", ACTOR, [("connect", str(DOCKER_SOCKET), "allowed")])     # control: X has Docker
        for account in (CLAUDE, GATE, ACTOR):
            self.expect("N-3", account, [("sudo", "-", "denied")])
        targets = [str(self.app), str(self.app / "runtime" / "mvp_runtime" / "deploy_runner"),
                   str(self.install / "venv" / "lib"), str(TREES)]
        for account in (CLAUDE, GATE, ACTOR):
            self.expect("N-4", account, [("create", t, "denied") for t in targets])
        for account in (CLAUDE, GATE):
            self.expect("N-4", account, [("create", str(ACTOR_DIR), "denied"),
                                         ("append", str(ACTOR_DIR / "audit.jsonl"), "denied")])
        self.expect("N-4", ACTOR, [("writable", str(ACTOR_DIR), "allowed")])          # its own records only
        for account in (CLAUDE, GATE):
            self.expect("N-5", account, [("environ", str(x_pid), "denied"), ("ptrace", str(x_pid), "denied")])
        self.expect("N-5", CLAUDE, [("environ", str(g_pid), "denied"), ("ptrace", str(g_pid), "denied")])
        self.expect("N-6", CLAUDE, [("create", str(STATE_ROOT), "denied"), ("traverse", str(HOLDINGS), "denied")])
        protected = [CONFIG_DIR / n for n in ("allowed_signers", "revoked_keys", "revoked_approvals", "trust_anchor",
                                              "actor.json", "gate.json")] + [store, *units]
        for account in (CLAUDE, GATE, ACTOR):
            self.expect("N-14", account, [("append", str(p), "denied") for p in protected
                                          if not (account == ACTOR and p == store)])
            self.expect("N-14", account, [("create", str(CONFIG_DIR), "denied"), ("create", str(UNIT_DIR), "denied")])

    def socket_tests(self, store: Path) -> None:
        sha, running = self.sha, "sha256:" + "b" * 64     # the binding checked before the clock is the request's

        def signer(name: str) -> Callable[[bytes], str]:
            def sign(data: bytes) -> str:
                return _sh([SSH_KEYGEN, "-Y", "sign", "-q", "-f", str(KEYS / name), "-n", NAMESPACE], input=data,
                           capture_output=True, timeout=30).stdout.decode("ascii")
            return sign

        cases = build_cases(signer("thomas"), signer("attacker"), now=datetime.now(timezone.utc), sha=sha,
                            running_id=running)
        before = self.docker_state()
        CASES_DIR.mkdir(mode=0o755, exist_ok=True)
        for i, case in enumerate(cases):
            path = CASES_DIR / f"{i:02d}"
            path.write_bytes(case.payload)
            path.chmod(0o644)
            done = _as(CLAUDE, [self.python, "-I", "-m", "scripts.ops.deploy_runner_c4", "send",
                                "--socket", str(GATE_SOCKET), "--payload", str(path)])
            answer = json.loads(done.stdout) if done.returncode == 0 else {"kind": "send_failed"}
            result, got = judge_case(case, answer)
            self.report.add(case.id, case.label, result, f"want {case.expect}, got {got}")
        # N-12: the actor frozen behind the gate (it stays a unit; the gate's deadline is the failure).
        _sh([SYSTEMCTL, "kill", "-s", "SIGSTOP", "thomas-deploy-actor.service"])
        try:
            frozen = Case("N-12", "the actor does not answer within the gate's deadline",
                          _frame("c4-n12-3", "inspect_candidate", {"pr": PR}), "ACTOR_UNAVAILABLE")
            path = CASES_DIR / "frozen"
            path.write_bytes(frozen.payload)
            path.chmod(0o644)
            done = _as(CLAUDE, [self.python, "-I", "-m", "scripts.ops.deploy_runner_c4", "send",
                                "--socket", str(GATE_SOCKET), "--payload", str(path)])
            answer = json.loads(done.stdout) if done.returncode == 0 else {"kind": "send_failed"}
            result, got = judge_case(frozen, answer)
            self.report.add(frozen.id, frozen.label, result, f"want {frozen.expect}, got {got}")
        finally:
            _sh([SYSTEMCTL, "kill", "-s", "SIGCONT", "thomas-deploy-actor.service"])
        time.sleep(2)
        after = self.docker_state()
        self.report.add("N-7..N-12", "Docker images and tags unchanged by every refused request",
                        PASS if before == after else FAIL, "same" if before == after else "changed")
        rows = len(store.read_bytes().splitlines())
        self.report.add("N-8..N-10", "the consumption store holds only its genesis row (nothing spent)",
                        PASS if rows == 1 else FAIL, f"{rows} rows")
        self.effect_tests(store, signer("thomas"))
        self.report.add("N-12", "an actor reply outside its schema", NOT_TESTED,
                        "needs a modified actor; covered by the C3a-3 synthetic tests")

    # -- past the clock: the measured binding, the spend, its reuse, a failure after it ---------------------
    def send_case(self, case: Case, name: str) -> str:
        path = CASES_DIR / name
        path.write_bytes(case.payload)
        path.chmod(0o644)
        done = _as(CLAUDE, [self.python, "-I", "-m", "scripts.ops.deploy_runner_c4", "send",
                            "--socket", str(GATE_SOCKET), "--payload", str(path)])
        answer = json.loads(done.stdout) if done.returncode == 0 else {"kind": "send_failed"}
        result, got = judge_case(case, answer)
        self.report.add(case.id, case.label, result, f"want {case.expect}, got {got}")
        return got

    @staticmethod
    def image_id(ref: str) -> str | None:
        done = subprocess.run(["/usr/bin/docker", "image", "inspect", "--format", "{{.Id}}", ref],
                              capture_output=True, text=True)
        return done.stdout.strip() if done.returncode == 0 else None

    def docker_fixture(self) -> str:
        """What the actor measures, made small: one image tagged ``latest`` (``FROM scratch``, so no pull) and
        the eight service names as ``created`` containers of it under the compose project."""
        from runtime.mvp_runtime.deploy_runner import docker_argv

        BASE_CTX.mkdir(mode=0o755, exist_ok=True)
        (BASE_CTX / "marker").write_text("c4 base\n", encoding="ascii")
        (BASE_CTX / "Dockerfile").write_text('FROM scratch\nCOPY marker /marker\nCMD ["/marker"]\n', encoding="ascii")
        _sh(["/usr/bin/docker", "image", "build", "-q", "--tag", docker_argv.latest_ref(), str(BASE_CTX)],
            capture_output=True, timeout=300)
        for name in docker_argv.RUNTIME_CONTAINERS:
            _sh(["/usr/bin/docker", "container", "create", "--name", name, "--label",
                 "com.docker.compose.project=thomas_agent", docker_argv.latest_ref()], capture_output=True, timeout=60)
        return self.image_id(docker_argv.latest_ref()) or ""

    @staticmethod
    def tree(pr: int, dockerfile: str) -> str:
        """The runner's own tree for ``pr``: root-owned, clean, ``HEAD`` = ``origin/main``. X reads it with no
        HOME, so the install tool's ``trust-tree`` names it in the system git config's ``safe.directory``."""
        d = TREES / str(pr)
        d.mkdir(mode=0o755)
        (d / "Dockerfile").write_text(dockerfile, encoding="ascii")
        (d / "marker").write_text(f"c4 tree {pr}\n", encoding="ascii")
        git = ["/usr/bin/git", "-C", str(d), "-c", "user.name=c4", "-c", "user.email=c4@invalid"]
        _sh(git[:3] + ["init", "-q"], capture_output=True)
        _sh(git + ["add", "-A"], capture_output=True)
        _sh(git + ["commit", "-q", "-m", "c4"], capture_output=True)
        head = _sh(git[:3] + ["rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        _sh(git[:3] + ["update-ref", "refs/remotes/origin/main", head], capture_output=True)
        from scripts.ops import deploy_runner_install as install

        install.trust_tree(d)                    # the install tool's step (C5 preparation), not a hand edit
        _sh(["/usr/bin/chmod", "-R", "go-w", str(d)])
        return head

    def effect_tests(self, store: Path, sign: Callable[[bytes], str]) -> None:
        from runtime.mvp_runtime.deploy_runner import clock_source, docker_argv

        clock = clock_source.describe()
        self.report.add("P-5", "the kernel's clock verdict on this runner (CLOCK-SOURCE = A)", INFO,
                        " ".join(f"{k}={v}" for k, v in sorted(clock.items())))
        if not clock.get("confirmed"):
            for test, label in (("N-10", "measured binding"), ("N-11", "reuse of a spent approval"),
                                ("N-12", "Docker failure after the spend")):
                self.report.add(test, label, BLOCKED, "the runner's kernel does not report the clock synchronised")
            return
        running = self.docker_fixture()
        head = self.tree(GOOD_PR, 'FROM scratch\nCOPY marker /marker\nCMD ["/marker"]\n')
        bad_head = self.tree(BAD_PR, "FROM scratch\nCOPY absent-file /absent\n")
        self.report.add("SETUP", "synthetic image, eight created containers, two runner trees",
                        PASS if running.startswith("sha256:") else FAIL)
        now = datetime.now(timezone.utc)

        def case(test, label, n, *, request_id, pr=GOOD_PR, sha=head, running_id=running, args_sha=None, expect):
            doc = approval_document(n, now=now, pr=pr, sha=sha, running_id=running_id)
            frame = _frame(request_id, "prepare_candidate", {"pr": pr, "git_commit_sha": args_sha or sha}, doc,
                           sign(doc))
            return Case(test, label, frame, expect), doc

        rows = lambda: len(store.read_bytes().splitlines())  # noqa: E731
        c, _ = case("N-10", "approval for a commit the runner's tree is not at (measured)", 10,
                    request_id="c4-n10-4", sha="e" * 40, expect="APPROVAL_BINDING_MISMATCH")
        self.send_case(c, "n10-tree")
        c, _ = case("N-10", "approval for another running image (measured)", 11, request_id="c4-n10-5",
                    running_id="sha256:" + "b" * 64, expect="APPROVAL_BINDING_MISMATCH")
        self.send_case(c, "n10-image")
        self.report.add("N-10", "nothing spent and no tag made by a measured mismatch",
                        PASS if rows() == 1 and self.image_id(docker_argv.rollback_ref(GOOD_PR)) is None else FAIL,
                        f"{rows()} rows")
        good, _ = case("P-5", "a valid approval passes the clock, is spent once, tags and builds", 12,
                       request_id="c4-ok-1", expect="OK")
        if self.send_case(good, "ok") != "OK":
            self.diagnose_effect_failure()
            for test, label in (("N-11", "reuse of a spent approval"), ("N-12", "Docker failure after the spend")):
                self.report.add(test, label, BLOCKED, "the success path did not complete; see the P-5 row")
            return
        built = (self.image_id(docker_argv.rollback_ref(GOOD_PR)) == running
                 and (self.image_id(docker_argv.candidate_ref(GOOD_PR)) or "").startswith("sha256:"))
        self.report.add("P-5", "the rollback point is the running image and the candidate exists",
                        PASS if built else FAIL, f"{rows()} rows")
        self.send_case(Case("N-11", "the same request again replays", good.payload, "OK"), "replay")
        reuse = Case("N-11", "the spent approval under a new request id, its tags still there",
                     good.payload.replace(b'"c4-ok-1"', b'"c4-n11-1"'), "TAG_ALREADY_EXISTS")
        self.send_case(reuse, "reuse-tags")
        for ref in (docker_argv.rollback_ref(GOOD_PR), docker_argv.candidate_ref(GOOD_PR)):
            _sh(["/usr/bin/docker", "image", "rm", ref], capture_output=True)
        reuse = Case("N-11", "the spent approval under a new request id, tags removed",
                     good.payload.replace(b'"c4-ok-1"', b'"c4-n11-2"'), "APPROVAL_ALREADY_CONSUMED")
        self.send_case(reuse, "reuse")
        self.report.add("N-11", "spent once: one consumption row beyond genesis", PASS if rows() == 2 else FAIL,
                        f"{rows()} rows")
        bad, _ = case("N-12", "a build that fails after the spend", 13, request_id="c4-n12-5", pr=BAD_PR,
                      sha=bad_head, expect="FAILED_EFFECT_PARTIAL")
        self.send_case(bad, "bad")
        self.send_case(Case("N-12", "after a failure past the spend the runner is latched",
                            _frame("c4-n12-6", "inspect_candidate", {"pr": GOOD_PR}), "RUNNER_LATCHED"), "latched")
        self.report.add("N-12", "the failed spend is recorded, never retried", PASS if rows() == 3 else FAIL,
                        f"{rows()} rows")

    def diagnose_effect_failure(self) -> None:
        """Why the spend's effect failed, as codes and Docker's first error line: the actor's own audit rows
        (reason codes only), and the same tag and build re-run as X with the actor's exact environment."""
        from runtime.mvp_runtime.deploy_runner import docker_argv

        try:
            rows = [json.loads(line) for line in (ACTOR_DIR / "audit.jsonl").read_text(encoding="ascii").splitlines()]
            codes = [f"{r.get('execution_state')}:{r.get('reason_code')}" for r in rows[-4:]]
        except (OSError, ValueError) as exc:
            codes = [type(exc).__name__]
        self.report.add("P-5", "the actor's last audit rows (state:reason)", INFO, " ".join(codes))
        env = {"PATH": "/usr/bin:/bin", "LANG": "C"}
        for label, argv in (("tag", docker_argv.tag_rollback_point(Path("/usr/bin/docker"),
                                                                    self.image_id(docker_argv.latest_ref()) or "", 1)),
                            ("build", docker_argv.build_candidate(Path("/usr/bin/docker"), TREES, GOOD_PR))):
            done = subprocess.run([SETPRIV, f"--reuid={IDS[ACTOR]}", f"--regid={IDS[ACTOR]}", "--init-groups",
                                   "--reset-env", "--",
                                   "/usr/bin/env", "-i", *[f"{k}={v}" for k, v in env.items()], *argv],
                                  cwd="/", stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)
            lines = [ln for ln in done.stderr.splitlines() if ln.strip()]
            self.report.add("P-5", f"the actor's {label} re-run as X with its environment", INFO,
                            f"rc={done.returncode} {(lines[-1] if lines else '')[:200]}")
            if label == "tag" and done.returncode == 0:
                subprocess.run(["/usr/bin/docker", "image", "rm", docker_argv.rollback_ref(1)], capture_output=True)

    @staticmethod
    def docker_state() -> list[str]:
        out = subprocess.run(["/usr/bin/docker", "image", "ls", "-a", "--no-trunc", "--format",
                              "{{.ID}} {{.Repository}}:{{.Tag}}"], capture_output=True, text=True, check=True).stdout
        return sorted(out.splitlines())

    def import_tests(self) -> None:
        """N-13: the services import only from the install, whatever a writable directory or the environment
        offers."""
        for unit in ("thomas-deploy-actor", "thomas-deploy-gate"):
            pid = self.main_pid(unit)
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
            environ = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
            cwd = os.readlink(f"/proc/{pid}/cwd")
            ok = (b"-I" in cmdline and not any(e.startswith((b"PYTHONPATH=", b"PYTHONHOME=")) for e in environ)
                  and cwd == str(self.install))
            self.report.add("N-13", f"{unit}: -I, no PYTHONPATH/PYTHONHOME, cwd is the install", PASS if ok else FAIL,
                            f"-I={b'-I' in cmdline} cwd_is_install={cwd == str(self.install)}")
        POISON.mkdir(mode=0o755, exist_ok=True)
        os.chown(POISON, IDS[CLAUDE], IDS[CLAUDE])
        poison = "raise SystemExit('C4 poison imported')\n"
        made = _as(CLAUDE, ["/bin/sh", "-c", f"mkdir -p {POISON}/runtime && printf %s \"$0\" > {POISON}/runtime/__init__.py"
                            f" && printf %s \"$0\" > {POISON}/jsonschema.py", poison])
        self.report.add("N-13", "the caller planted same-name modules in a directory it can write",
                        PASS if made.returncode == 0 else FAIL)
        probe = ("import runtime.mvp_runtime.deploy_runner.serve as s, jsonschema, sys;"
                 "print(s.__file__); print(jsonschema.__file__)")
        for account in (ACTOR, GATE):
            done = _as(account, [self.python, "-I", "-c", probe], cwd=str(POISON), env={"PYTHONPATH": str(POISON)})
            files = done.stdout.decode("ascii", "replace").split()
            ok = done.returncode == 0 and len(files) == 2 and all(f.startswith(str(self.install) + "/") for f in files)
            self.report.add("N-13", f"{account}: -I with the poison as cwd and PYTHONPATH imports from the install",
                            PASS if ok else FAIL, "install" if ok else f"rc={done.returncode}")
        _sh([SYSTEMCTL, "restart", "thomas-deploy-actor.service", "thomas-deploy-gate.service"])
        ok = self._wait_active() and self._wait_socket(GATE_SOCKET)
        self.report.add("N-13", "both units restart with the poison present", PASS if ok else FAIL)

    def store_unreadable(self, store: Path) -> None:
        """N-12, last: the actor's store unreadable at start — it must not come up. Run last: it may latch."""
        store.chmod(0o000)
        try:
            subprocess.run([SYSTEMCTL, "restart", "thomas-deploy-actor.service"], stdin=subprocess.DEVNULL)
            time.sleep(3)
            up = self._active("thomas-deploy-actor") and ACTOR_SOCKET.is_socket()
            status = subprocess.run([SYSTEMCTL, "show", "-p", "ExecMainStatus", "--value",
                                     "thomas-deploy-actor.service"], capture_output=True, text=True).stdout.strip()
            self.report.add("N-12", "an unreadable consumption store stops the actor at start",
                            PASS if not up else FAIL, f"active={up} exit={status}")
        finally:
            subprocess.run([SYSTEMCTL, "stop", "thomas-deploy-actor.service", "thomas-deploy-gate.service"])
            store.chmod(0o600)

    def leak_sweep(self) -> None:
        secrets_ = [self.sentinel.encode("ascii")]
        for name in ("thomas", "attacker"):
            body = [ln for ln in (KEYS / name).read_text(encoding="ascii").splitlines() if not ln.startswith("-----")]
            secrets_ += [ln.encode("ascii") for ln in body if len(ln) >= 32]
        haystacks: list[tuple[str, bytes]] = []
        for directory in (ACTOR_DIR, GATE_DIR):
            for path in sorted(directory.iterdir()):
                if path.is_file():
                    haystacks.append((str(path), path.read_bytes()))
        for unit in ("thomas-deploy-actor", "thomas-deploy-gate"):
            journal = subprocess.run(["journalctl", "-u", f"{unit}.service", "--no-pager", "-o", "cat"],
                                     capture_output=True).stdout
            haystacks.append((f"journal:{unit}", journal))
        haystacks.append(("report", json.dumps(self.report.rows).encode("utf-8")))
        hits = [name for name, data in haystacks for s in secrets_ if s in data]
        self.report.add("LEAK", f"sentinel and synthetic private keys in {len(haystacks)} runner outputs",
                        PASS if not hits else FAIL, f"{len(hits)} hits" + (f" in {sorted(set(hits))}" if hits else ""))

    # the whole run -----------------------------------------------------------------------------------
    def run(self) -> int:
        self.report.add("SETUP", f"interpreter {platform.python_version()} (CR-8b {PYTHON_VERSION})",
                        PASS if platform.python_version() == PYTHON_VERSION and sys.flags.isolated else FAIL)
        store = ACTOR_DIR / "consumed.jsonl"
        try:
            self.accounts()
            _, gate_unit, actor_unit = self.files()
            self.install_tool_checks()
            if self.start():
                self.host_tests(store, (gate_unit, actor_unit))
                self.socket_tests(store)
                self.import_tests()
                self.store_unreadable(store)
                self.leak_sweep()
        except Exception as exc:  # noqa: BLE001 — the report says where it stopped; nothing after it counts
            where = " ".join(map(str, getattr(exc, "cmd", [])[:2]))
            self.report.add("SETUP", "harness stopped", FAIL, f"{type(exc).__name__} {where} {str(exc)[:160]}".strip())
        finally:
            self.write()
        return EXIT_FAILED if self.report.failed or not self.report.rows else EXIT_OK

    def write(self) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "c4-results.json").write_text(json.dumps(self.report.rows, indent=1), encoding="utf-8")
        lines = ["| ID | 시험 | 결과 | 근거(종류) |", "|---|---|---|---|"]
        lines += [f"| {r['id']} | {r['label']} | {r['result']} | {r['detail']} |" for r in self.report.rows]
        (self.out / "c4-results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="deploy_runner_c4")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("probe")
    p.add_argument("--checks", required=True)
    s = sub.add_parser("send")
    s.add_argument("--socket", required=True)
    s.add_argument("--payload", required=True)
    r = sub.add_parser("run")
    r.add_argument("--install-dir", required=True)
    r.add_argument("--install-sha", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--throwaway-runner", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return EXIT_USAGE
    if args.command == "probe":
        print(json.dumps(probe(json.loads(args.checks))))
        return EXIT_OK
    if args.command == "send":
        print(json.dumps(send(args.socket, Path(args.payload).read_bytes())))
        return EXIT_OK
    try:
        require_throwaway_runner(args.throwaway_runner)
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    return Runner(Path(args.install_dir), args.install_sha, Path(args.out)).run()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
