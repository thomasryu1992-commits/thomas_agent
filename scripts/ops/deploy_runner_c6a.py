"""Option C C6a — install, check and reverse the deploy runner on a host, as root, step by step.

C5 plan (approved 2026-10-19) §3, steps a0–a9; Thomas 2026-10-19: ``결정: C6a 실행 방식 = A(Claude가 설치·되돌리기 스크립트
PR, Thomas가 서버 셸에서 실행), 서명 키 = Thomas가 root authorized_keys 4개 중 지문으로 선택(K1), host_label =
ubuntu-4gb-sin-1``. Claude writes this; Thomas runs it at his server shell::

    python3 scripts/ops/deploy_runner_c6a.py install --repo <clean origin/main tree> --sha <40 hex> \
        --host-label ubuntu-4gb-sin-1 --signer-fingerprint SHA256:… [--dry-run]
    python3 scripts/ops/deploy_runner_c6a.py status
    python3 scripts/ops/deploy_runner_c6a.py uninstall [--dry-run]

Standard library only: it runs under the system ``python3`` before the runner's own interpreter exists, and
calls the install tool (``scripts.ops.deploy_runner_install``) through that interpreter once it does.

**What it never touches:** the running thomas containers, ``.env`` and its copies, the runtime state
directory, root's crontab and ``at`` jobs, SSH, sudo, the Claude session. It reads no secret.

**The signing key (TYPE-B-KEY K1).** Thomas's existing SSH key, restricted to the approval namespace. The
script takes the key's fingerprint and finds that one line in ``/root/.ssh/authorized_keys`` (or ``--pubkey-file``),
keeping only its type and key — no options, no comment. Exactly one ``ssh-ed25519`` line must match.

**Every step checks itself and stops on the first surprise**, printing what was done so far and the
``uninstall`` command; it never repairs. ``--dry-run`` runs the preflight and prints the plan, writing nothing.
``uninstall`` reverses a0–a9 in reverse order, moving the actor's and gate's records to the backup directory
before anything is removed, and refuses to delete the ``claude`` account once C6b has started (an
``authorized_keys`` in its home).
"""

from __future__ import annotations

import argparse
import base64
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_REFUSED = 0, 1, 2, 3

PBS_URL = ("https://github.com/astral-sh/python-build-standalone/releases/download/20260807/"
           "cpython-3.12.13%2B20260807-x86_64-unknown-linux-gnu-install_only.tar.gz")
PBS_SHA256 = "5bd6f36fd7ef02b909234c94dca9994ef0da06ace3bc3cece4fe27870e9cdbbe"   # CR-8b (Thomas 2026-10-17)
PYTHON_VERSION = "3.12.13"

CLAUDE, GATE, ACTOR = "claude", "thomas-deploy-gate", "thomas-deploy-actor"
IDS = {CLAUDE: 2101, GATE: 2102, ACTOR: 2103}                         # C5 plan §2
OPT = Path("/opt/thomas-deploy")
CONFIG_DIR = Path("/etc/thomas-deploy")
LIB = Path("/var/lib/thomas-deploy")
GATE_DIR, ACTOR_DIR, TREES = LIB / "gate", LIB / "actor", LIB / "trees"
UNIT_DIR = Path("/etc/systemd/system")
UNITS = ("thomas-deploy-actor.service", "thomas-deploy-gate.service")
GATE_SOCKET = Path("/run/thomas-deploy-gate/gate.sock")
ACTOR_SOCKET = Path("/run/thomas-deploy-actor/actor.sock")
AUTHORIZED_KEYS = Path("/root/.ssh/authorized_keys")
PRINCIPAL = "thomas-deploy-approver"
NAMESPACE = "deploy-approval.v1@thomas-agent"
ACTOR_DEADLINE_S = 60
# CR-10, CR-11, CR-19 (Thomas 2026-10-19)
POLICY = {"max_lifetime_s": 900, "issue_skew_s": 60, "clock_jump_s": 5, "backstep_tolerance_s": 5,
          "deadlines_s": {"prepare_candidate": 300, "validate_candidate": 300, "promote": 300, "rollback": 300}}
_SHA = re.compile(r"^[0-9a-f]{40}\Z")
_LABEL = re.compile(r"^[a-z0-9-]{1,32}\Z")
_FINGERPRINT = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}\Z")
SETPRIV, SYSTEMCTL, SSH_KEYGEN, GIT = "/usr/bin/setpriv", "/usr/bin/systemctl", "/usr/bin/ssh-keygen", "/usr/bin/git"


class Stop(Exception):
    """A step's check did not hold. The message is what to report; nothing after it ran."""


# --- the signing key ------------------------------------------------------------------------------------

def fingerprint_of(blob_b64: str) -> str:
    """OpenSSH's ``SHA256:`` fingerprint of a public key blob (unpadded base64 of the SHA-256)."""
    raw = base64.b64decode(blob_b64, validate=True)
    return "SHA256:" + base64.b64encode(hashlib.sha256(raw).digest()).decode("ascii").rstrip("=")


def select_signer(text: str, fingerprint: str) -> str:
    """The one ``ssh-ed25519 <blob>`` in ``text`` (authorized_keys or a .pub) whose fingerprint is
    ``fingerprint``, without options or comment. Zero or several matches stop."""
    if not _FINGERPRINT.match(fingerprint):
        raise Stop("the fingerprint is not in the SHA256:… form ssh-keygen -l prints")
    found = []
    for line in text.splitlines():
        parts = line.split()
        for i, part in enumerate(parts[:-1]):
            if part == "ssh-ed25519":
                try:
                    if fingerprint_of(parts[i + 1]) == fingerprint:
                        found.append(parts[i + 1])
                except ValueError:
                    pass
                break
    if len(found) != 1:
        raise Stop(f"{len(found)} ssh-ed25519 keys have that fingerprint; exactly one must")
    return found[0]


def trust_files(blob: str) -> dict[str, str]:
    return {"allowed_signers": f'{PRINCIPAL} namespaces="{NAMESPACE}" ssh-ed25519 {blob}\n',
            "revoked_keys": "", "revoked_approvals": "", "trust_anchor": f"ssh-ed25519 {blob}\n"}


def runner_configs(*, sha: str, host_label: str, fingerprint: dict[str, str], store_id: str) -> dict[str, dict]:
    """The two root-owned configurations, with the decided time policy (CR-10/11/19)."""
    common = {"schema": "thomas.deploy_runner_config.v1", "install_sha": sha,
              "environment": {"project": "thomas_agent", "host_label": host_label, "profile": "production"},
              "fingerprint": fingerprint}
    actor = {**common, "role": "actor", "socket": str(ACTOR_SOCKET), "directory": str(ACTOR_DIR),
             "allowed_uids": [IDS[GATE]],
             "actor": {"trees_root": str(TREES), "docker_bin": "/usr/bin/docker", "git_bin": GIT,
                       "ssh_keygen_bin": SSH_KEYGEN, "trust_anchor": str(CONFIG_DIR / "trust_anchor"),
                       "allowed_signers": str(CONFIG_DIR / "allowed_signers"),
                       "revoked_keys": str(CONFIG_DIR / "revoked_keys"),
                       "revoked_approvals": str(CONFIG_DIR / "revoked_approvals"), "trust_owner_uid": 0,
                       "principal": PRINCIPAL, "store_id": store_id, "policy": json.loads(json.dumps(POLICY)),
                       "inspect_min_interval_s": 1.0}}
    gate = {**common, "role": "gate", "socket": str(GATE_SOCKET), "directory": str(GATE_DIR),
            "allowed_uids": [IDS[CLAUDE]],
            "gate": {"actor_socket": str(ACTOR_SOCKET), "actor_deadline_s": ACTOR_DEADLINE_S}}
    return {"actor": actor, "gate": gate}


# --- the host, behind one seam the tests replace -----------------------------------------------------------

class Host:
    """Everything that touches the machine. ``dry`` records instead of acting (reads still read)."""

    def __init__(self, *, dry: bool, log: Callable[[str], None] = print) -> None:
        self.dry, self.log, self.actions = dry, log, []

    def act(self, what: str, fn: Callable[[], Any] | None = None) -> Any:
        self.actions.append(what)
        self.log(("[dry-run] " if self.dry else "[do] ") + what)
        return None if self.dry or fn is None else fn()

    def run(self, argv: list[str], *, input: bytes | None = None, check: bool = True,
            cwd: str | None = None) -> subprocess.CompletedProcess:
        done = subprocess.run(argv, input=input, capture_output=True, cwd=cwd,
                              stdin=None if input is not None else subprocess.DEVNULL)
        if check and done.returncode != 0:
            raise Stop(f"{' '.join(argv[:3])} … exited {done.returncode}: "
                       f"{done.stderr.decode('utf-8', 'replace').strip().splitlines()[-1:] or ''}")
        return done

    def getent(self, db: str, key: str) -> str | None:
        done = subprocess.run(["getent", db, key], capture_output=True, text=True)
        return done.stdout.strip() or None if done.returncode == 0 else None

    def is_root(self) -> bool:
        return os.geteuid() == 0

    def exists(self, path: Path) -> bool:
        return path.exists() or path.is_symlink()

    def read(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")


@dataclass
class Plan:
    repo: Path
    sha: str
    host_label: str
    signer_fingerprint: str
    pubkey_file: Path = AUTHORIZED_KEYS
    backup: Path = field(default_factory=lambda: Path("/root") / f"c6-backup-{datetime.date.today().isoformat()}")

    @property
    def install(self) -> Path:
        return OPT / self.sha

    @property
    def python(self) -> Path:
        return self.install / "venv" / "bin" / "python"


# --- preflight -------------------------------------------------------------------------------------------

def preflight(plan: Plan, host: Host) -> str:
    """Everything that must hold before a0. Returns the signer blob. Read-only, also under --dry-run."""
    if not host.is_root() and not host.dry:
        raise Stop("run as root (sudo); --dry-run works without")
    if not _SHA.match(plan.sha):
        raise Stop("--sha must be 40 lowercase hex")
    if not _LABEL.match(plan.host_label):
        raise Stop("--host-label must be lowercase letters, digits and '-', at most 32")
    head = host.run([GIT, "-C", str(plan.repo), "rev-parse", "HEAD"]).stdout.decode().strip()
    dirty = host.run([GIT, "-C", str(plan.repo), "status", "--porcelain"]).stdout.decode().strip()
    if head != plan.sha or dirty:
        raise Stop("--repo must be a clean tree checked out at --sha (a fresh origin/main worktree)")
    for name, uid in IDS.items():
        for db, key in (("passwd", str(uid)), ("group", str(uid)), ("passwd", name), ("group", name)):
            if host.getent(db, key):
                raise Stop(f"{db} {key} is already taken; choose other ids before installing (C5 plan §2)")
    if not host.getent("group", "docker"):
        raise Stop("the docker group does not exist")
    for path in (OPT, CONFIG_DIR, LIB, *(UNIT_DIR / u for u in UNITS)):
        if host.exists(path):
            raise Stop(f"{path} already exists; this script installs once (uninstall first)")
    if host.read(Path("/proc/1/comm")).strip() != "systemd":
        raise Stop("systemd is not PID 1")
    blob = select_signer(host.read(plan.pubkey_file), plan.signer_fingerprint)
    host.log(f"[check] signer: ssh-ed25519 key {plan.signer_fingerprint} from {plan.pubkey_file}")
    return blob


# --- install a0–a9 ----------------------------------------------------------------------------------------

def _write(path: Path, text: str, mode: int = 0o644) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        os.write(fd, text.encode("ascii"))
        os.fchmod(fd, mode)
    finally:
        os.close(fd)


def _download(url: str, target: Path) -> None:
    with urllib.request.urlopen(url, timeout=120) as response, target.open("wb") as out:  # noqa: S310 — fixed URL
        shutil.copyfileobj(response, out)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install(plan: Plan, host: Host, *, download: Callable[[str, Path], None] = _download) -> None:
    blob = preflight(plan, host)
    tool = [str(plan.python), "-I", "-m", "scripts.ops.deploy_runner_install"]

    # a0: what is there now, for the reversal
    def a0():
        plan.backup.mkdir(mode=0o700, parents=False, exist_ok=False)
        shutil.copy2("/etc/passwd", plan.backup / "passwd")
        shutil.copy2("/etc/group", plan.backup / "group")
        crontab = host.run(["crontab", "-l"], check=False).stdout
        (plan.backup / "crontab").write_bytes(crontab)
    host.act(f"a0 record /etc/passwd, /etc/group and root's crontab in {plan.backup}", a0)

    # a1: accounts
    def a1():
        for name, uid in IDS.items():
            host.run(["groupadd", "-g", str(uid), name])
        host.run(["useradd", "-u", str(IDS[CLAUDE]), "-g", str(IDS[CLAUDE]), "-m", "-s", "/bin/bash", CLAUDE])
        for name in (GATE, ACTOR):
            host.run(["useradd", "-u", str(IDS[name]), "-g", str(IDS[name]), "-M", "-d", "/nonexistent",
                      "-s", "/usr/sbin/nologin", name])
        host.run(["usermod", "-aG", "docker", ACTOR])
        host.run(["usermod", "-aG", ACTOR, GATE])
        host.run(["usermod", "-aG", GATE, CLAUDE])
        for name, uid in IDS.items():
            if host.run(["id", "-u", name]).stdout.decode().strip() != str(uid):
                raise Stop(f"{name} does not have uid {uid}")
    host.act("a1 groups and accounts claude 2101, thomas-deploy-gate 2102, thomas-deploy-actor 2103 (X in docker)", a1)

    # a2: the interpreter, checked before anything opens it
    def a2():
        with tempfile.TemporaryDirectory(prefix="c6a-pbs-") as tmp:
            tarball = Path(tmp) / "pbs.tar.gz"
            download(PBS_URL, tarball)
            if _sha256(tarball) != PBS_SHA256:
                raise Stop("the interpreter's SHA256 does not match CR-8b; nothing extracted")
            plan.install.mkdir(parents=True)
            host.run(["tar", "-xzf", str(tarball), "-C", str(plan.install)])
        version = host.run([str(plan.install / "python" / "bin" / "python3"), "-I", "-c",
                            "import platform; print(platform.python_version())"]).stdout.decode().strip()
        if version != PYTHON_VERSION:
            raise Stop(f"the interpreter is {version}, not {PYTHON_VERSION}")
    host.act(f"a2 python-build-standalone {PYTHON_VERSION} into {plan.install}/python (SHA256 {PBS_SHA256[:8]}…)", a2)

    # a3: the code, the venv, the .pth; then root-owned and not group/world-writable
    def a3():
        app = plan.install / "app"
        app.mkdir()
        archive = host.run([GIT, "-C", str(plan.repo), "archive", plan.sha, "runtime", "schemas", "scripts"]).stdout
        host.run(["tar", "-x", "-C", str(app)], input=archive)
        host.run([str(plan.install / "python" / "bin" / "python3"), "-I", "-m", "venv", str(plan.install / "venv")])
        host.run([str(plan.python), "-I", "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check", "-q",
                  "-r", str(plan.repo / "requirements-validation.lock")])
        host.run([str(plan.python), "-m", "scripts.ops.deploy_runner_install", "link-install", "--app", str(app)],
                 cwd=str(app))
        host.run(["chown", "-R", "root:root", str(OPT)])
        host.run(["chmod", "-R", "go-w", str(OPT)])
        host.run([str(plan.python), "-I", "-c", "import runtime.mvp_runtime.deploy_runner.serve"], cwd="/")
    host.act(f"a3 runtime, schemas, scripts at {plan.sha[:8]} into {plan.install}/app; venv; link-install; root-owned", a3)

    # a4: directories
    def a4():
        for d, mode, owner in ((CONFIG_DIR, 0o755, 0), (LIB, 0o755, 0), (TREES, 0o755, 0),
                               (GATE_DIR, 0o700, IDS[GATE]), (ACTOR_DIR, 0o700, IDS[ACTOR])):
            d.mkdir()
            os.chown(d, owner, owner)
            d.chmod(mode)
    host.act(f"a4 {CONFIG_DIR}, {LIB}, trees (root 0755), gate (G 0700), actor (X 0700)", a4)

    # a5: trust files from the chosen key
    def a5():
        for name, text in trust_files(blob).items():
            _write(CONFIG_DIR / name, text)
        listed = host.run([SSH_KEYGEN, "-l", "-f", str(CONFIG_DIR / "trust_anchor")]).stdout.decode().split()
        if plan.signer_fingerprint not in listed:
            raise Stop("the written anchor does not have the chosen fingerprint")
    host.act(f"a5 allowed_signers (namespace {NAMESPACE}), revoked_keys, revoked_approvals, trust_anchor — "
             f"key {plan.signer_fingerprint}", a5)

    # a6 + a7: the actor's store, then the configurations that name it
    def a6_a7():
        store_id = host.run(tool + ["genesis", "--dir", str(ACTOR_DIR), "--install-sha", plan.sha,
                                    "--owner-uid", str(IDS[ACTOR])], cwd="/").stdout.decode().strip()
        if not re.match(r"^[0-9a-f]{32}\Z", store_id):
            raise Stop("genesis did not print a store id")
        fingerprint = json.loads(host.run(tool + ["fingerprint"], cwd="/").stdout)
        for role, body in runner_configs(sha=plan.sha, host_label=plan.host_label, fingerprint=fingerprint,
                                         store_id=store_id).items():
            _write(CONFIG_DIR / f"{role}.json", json.dumps(body, indent=1, sort_keys=True) + "\n")
    host.act(f"a6 genesis in {ACTOR_DIR} (with docker-config); a7 actor.json and gate.json "
             f"(host_label {plan.host_label}, policy CR-10/11/19)", a6_a7)

    # a8: units, their own start checks as each account, then start
    def a8():
        host.run(tool + ["render-units", "--out", str(UNIT_DIR), "--install-dir", str(plan.install),
                         "--config-dir", str(CONFIG_DIR), "--gate-user", GATE, "--gate-group", GATE,
                         "--actor-user", ACTOR, "--actor-group", ACTOR, "--gate-dir", str(GATE_DIR),
                         "--actor-dir", str(ACTOR_DIR)], cwd="/")
        for role, account in (("actor", ACTOR), ("gate", GATE)):
            uid = str(IDS[account])
            host.run([SETPRIV, f"--reuid={uid}", f"--regid={uid}", "--init-groups", "--reset-env", "--",
                      str(plan.python), "-I", "-m", "runtime.mvp_runtime.deploy_runner.serve", "--role", role,
                      "--config", str(CONFIG_DIR / f"{role}.json"), "--check"], cwd=str(plan.install))
        host.run([SYSTEMCTL, "daemon-reload"])
        host.run([SYSTEMCTL, "enable", "--now", UNITS[0]])
        host.run([SYSTEMCTL, "enable", "--now", UNITS[1]])
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if all(host.run([SYSTEMCTL, "is-active", "--quiet", u], check=False).returncode == 0 for u in UNITS) \
                    and GATE_SOCKET.is_socket() and ACTOR_SOCKET.is_socket():
                return
            time.sleep(0.5)
        raise Stop("the units did not come up with their sockets within 30 s")
    host.act("a8 render units, serve --check as G and X, enable --now actor then gate", a8)

    # a9: the first check through the real path
    def a9():
        frame = (json.dumps({"proto": 2, "client_id": "c6a", "request_id": "c6a-a9", "verb": "inspect_candidate",
                             "args": {"pr": 1}}, separators=(",", ":")) + "\n").encode("ascii")
        probe = ("import json,socket,sys;s=socket.socket(socket.AF_UNIX);s.settimeout(90);"
                 f"s.connect({str(GATE_SOCKET)!r});s.sendall(sys.stdin.buffer.read());d=b''\n"
                 "while not d.endswith(b'\\n'):\n c=s.recv(65536)\n if not c: break\n d+=c\n"
                 "r=json.loads(d);print(json.dumps({'ok':r.get('ok'),'reason_code':r.get('reason_code')}))")
        uid = str(IDS[CLAUDE])
        as_claude = json.loads(host.run([SETPRIV, f"--reuid={uid}", f"--regid={uid}", "--init-groups", "--reset-env",
                                         "--", "/usr/bin/python3", "-I", "-c", probe], input=frame, cwd="/").stdout)
        as_root = json.loads(host.run(["/usr/bin/python3", "-I", "-c", probe], input=frame, cwd="/").stdout)
        if as_root.get("reason_code") != "PEER_NOT_PERMITTED":
            raise Stop(f"root reached the gate: {as_root}")
        host.log(f"[check] a9 inspect as claude: {as_claude}; as root: PEER_NOT_PERMITTED")
    host.act("a9 one inspection through the gate as claude; the same as root must be PEER_NOT_PERMITTED", a9)


# --- status and reversal ----------------------------------------------------------------------------------

def status(host: Host) -> dict[str, Any]:
    out: dict[str, Any] = {"accounts": {n: bool(host.getent("passwd", n)) for n in IDS},
                           "paths": {str(p): host.exists(p) for p in (OPT, CONFIG_DIR, LIB)},
                           "units": {}, "sockets": {str(GATE_SOCKET): GATE_SOCKET.is_socket(),
                                                    str(ACTOR_SOCKET): ACTOR_SOCKET.is_socket()}}
    for unit in UNITS:
        done = host.run([SYSTEMCTL, "is-active", unit], check=False)
        out["units"][unit] = done.stdout.decode().strip() or "unknown"
    return out


def absent(found: dict[str, Any]) -> bool:
    """Nothing left: no runner account but a claude kept after C6b, no path, no socket, no active unit."""
    return (not found["accounts"][GATE] and not found["accounts"][ACTOR] and not any(found["paths"].values())
            and not any(found["sockets"].values()) and all(v != "active" for v in found["units"].values()))


def uninstall(host: Host, *, backup: Path) -> None:
    """a8 → a0 in reverse. Records are moved to ``backup`` first; the claude account stays once C6b began."""
    def units():
        for unit in reversed(UNITS):
            host.run([SYSTEMCTL, "disable", "--now", unit], check=False)
        for unit in UNITS:
            (UNIT_DIR / unit).unlink(missing_ok=True)
        host.run([SYSTEMCTL, "daemon-reload"])
    host.act("u1 disable --now gate then actor; remove the unit files; daemon-reload", units)

    def records():
        backup.mkdir(mode=0o700, parents=False, exist_ok=True)
        for d in (ACTOR_DIR, GATE_DIR):
            if d.exists():
                shutil.move(str(d), str(backup / f"records-{d.name}"))
    host.act(f"u2 move the actor's and gate's records to {backup}", records)

    def safe_dirs():
        listed = host.run([GIT, "config", "--system", "--get-all", "safe.directory"], check=False).stdout.decode()
        for line in listed.splitlines():
            if line.startswith(str(TREES) + "/"):
                host.run([GIT, "config", "--system", "--unset", "safe.directory", f"^{re.escape(line)}$"])
    host.act(f"u3 remove the system safe.directory entries under {TREES}", safe_dirs)

    def paths():
        for p in (CONFIG_DIR, LIB, OPT):
            if p.exists():
                shutil.rmtree(p)
    host.act(f"u4 remove {CONFIG_DIR}, {LIB}, {OPT}", paths)

    def accounts():
        for name in (ACTOR, GATE):
            if host.getent("passwd", name):
                host.run(["userdel", name])
        started = Path(f"/home/{CLAUDE}/.ssh/authorized_keys")
        if host.getent("passwd", CLAUDE):
            if started.exists():
                host.log("[keep] claude has an authorized_keys (C6b started); the account and its home stay")
            else:
                host.run(["userdel", "-r", CLAUDE])
        for name in IDS:
            if host.getent("group", name) and not (name == CLAUDE and host.getent("passwd", CLAUDE)):
                host.run(["groupdel", name])
    host.act("u5 remove thomas-deploy-actor, thomas-deploy-gate, and claude unless C6b has started", accounts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="deploy_runner_c6a", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    i = sub.add_parser("install")
    i.add_argument("--repo", required=True)
    i.add_argument("--sha", required=True)
    i.add_argument("--host-label", required=True)
    i.add_argument("--signer-fingerprint", required=True)
    i.add_argument("--pubkey-file", default=str(AUTHORIZED_KEYS))
    i.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("status")
    s.add_argument("--expect-absent", action="store_true",
                   help="exit 1 unless nothing of the runner is left (after uninstall)")
    u = sub.add_parser("uninstall")
    u.add_argument("--backup", default=str(Path("/root") / f"c6-backup-{datetime.date.today().isoformat()}"))
    u.add_argument("--dry-run", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return EXIT_USAGE
    host = Host(dry=getattr(args, "dry_run", False))
    try:
        if args.command == "status":
            found = status(host)
            print(json.dumps(found, indent=1, sort_keys=True))
            if args.expect_absent and not absent(found):
                print("STOP: something of the runner is still there", file=sys.stderr)
                return EXIT_FAILED
            return EXIT_OK
        if args.command == "uninstall":
            if not host.is_root() and not host.dry:
                raise Stop("run as root (sudo); --dry-run works without")
            uninstall(host, backup=Path(args.backup))
            print("uninstall: done" if not host.dry else "uninstall: dry run, nothing changed")
            return EXIT_OK
        plan = Plan(repo=Path(args.repo), sha=args.sha, host_label=args.host_label,
                    signer_fingerprint=args.signer_fingerprint, pubkey_file=Path(args.pubkey_file))
        install(plan, host)
        print("install: done — check `status`" if not host.dry else "install: dry run, nothing changed")
        return EXIT_OK
    except Stop as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        if host.actions and not host.dry:
            print(f"stopped in: {host.actions[-1].split(' ', 1)[0]}. Nothing after it ran. To reverse what ran: "
                  "python3 scripts/ops/deploy_runner_c6a.py uninstall --dry-run, then without --dry-run",
                  file=sys.stderr)
        return EXIT_FAILED if host.actions and not host.dry else EXIT_REFUSED


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
