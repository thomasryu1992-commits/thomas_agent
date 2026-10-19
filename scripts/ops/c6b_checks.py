"""Option C C6b step b8: what the ``claude`` account must be refused, and what it must still reach.

    python3 -I scripts/ops/c6b_checks.py              # in a Claude session running as `claude` (uid 2101)
    python3 -I scripts/ops/c6b_checks.py --json PATH   # also write the rows

Runs as the account it is about, after the switch (b1), from that account's own checkout. Read-only: every
refused probe tries to open, append, create or connect and records the *kind* of answer, never content.
- A file that opens is closed unread and is a FAIL.
- A probe file that does get created is removed again.
- No frame is sent to any door except the two below.

The probes come from the C4 harness (``deploy_runner_c4.OPS``); the targets here are the production ones
(``docs/proposals/HOST_PRIVILEGE_SEPARATION_C6B_EXECUTION_V0.1.md`` b8; the tests are Option C §4.6).

What it checks:
- **Refused** (each expected as ``denied``):
  - N-1: `.env` and its five copies;
  - N-2: the Docker socket;
  - N-3: sudo;
  - N-4: the runner's code, trees and audit;
  - N-5: the runner processes;
  - N-6: the state root and holdings;
  - N-14: the runner's trust files and units;
  - N-15: the managed settings.
- **Configuration:**
  - N-13: the runner units carry no import path ``claude`` can write.
  - The managed-settings hooks point at root-owned copies under ``/etc/claude-code/hooks``.
  - That LOCAL_ONLY guard copy denies a holdings read when fed one.
  - No Claude process runs as root.
- **Reached:**
  - Claude's read door answers ``schedules``;
  - the deploy gate answers ``inspect_candidate`` (anything but ``PEER_NOT_PERMITTED`` or no answer).

Exit 0 when every row is PASS, 1 on any FAIL, 2 when not run as the ``claude`` account. Run as root, every
refusal would be a false pass.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops import deploy_runner_c4 as c4  # noqa: E402

CLAUDE_UID = 2101
EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 2
PASS, FAIL = c4.PASS, c4.FAIL
SYSTEMCTL, BASH = "/usr/bin/systemctl", "/bin/bash"
UNITS = ("thomas-deploy-actor.service", "thomas-deploy-gate.service")


@dataclass(frozen=True)
class Paths:
    """The production targets. Tests point them into a temporary tree."""

    checkout: Path = Path("/root/thomas_agent")
    env_copies: tuple[Path, ...] = (Path("/root/backups/.env.bak-20260829"),
                                    Path("/root/backups/.env.bak-20260904-pr2"),
                                    Path("/root/thomas_agent/.env.bak-20260928-blogmodel"),
                                    Path("/root/thomas_agent/.env.bak-20260928-vault"),
                                    Path("/root/thomas_agent/.env.bak-pre-wallet-20261008"))
    docker_socket: Path = Path("/var/run/docker.sock")
    opt: Path = Path("/opt/thomas-deploy")
    config_dir: Path = Path("/etc/thomas-deploy")
    lib: Path = Path("/var/lib/thomas-deploy")
    unit_dir: Path = Path("/etc/systemd/system")
    gate_socket: Path = Path("/run/thomas-deploy-gate/gate.sock")
    managed_dir: Path = Path("/etc/claude-code")
    door_socket: Path = Path("/var/lib/thomas-claude-door/sock/read.sock")

    @property
    def state_root(self) -> Path:
        return self.checkout / ".runtime_governance_state"


@dataclass
class Checks:
    paths: Paths = field(default_factory=Paths)
    ops: dict[str, Callable[[str], str]] = field(default_factory=lambda: dict(c4.OPS))
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run
    access: Callable[[str, int], bool] = os.access
    owner_uid: int = 0                      # who must own the managed hook copies
    report: c4.Report = field(default_factory=c4.Report)

    # -- refusals ------------------------------------------------------------------------------------------
    def expect(self, test: str, op: str, target: Path | str, want: str = "denied") -> None:
        try:
            got = self.ops[op](str(target))
        except Exception as exc:  # noqa: BLE001 — the kind is the result
            got = c4._kind(exc)
        self.report.add(test, f"{op} {target}", PASS if got == want else FAIL, f"want {want}, got {got}")

    def main_pid(self, unit: str) -> int:
        done = self.run([SYSTEMCTL, "show", "-p", "MainPID", "--value", unit], capture_output=True, text=True)
        try:
            return int(done.stdout.strip())
        except ValueError:
            return 0

    def refusals(self) -> None:
        p = self.paths
        for env in (p.checkout / ".env", *p.env_copies):
            self.expect("N-1", "read", env)
        self.expect("N-2", "connect", p.docker_socket)
        self.expect("N-3", "sudo", "-")
        installs = sorted(d for d in p.opt.glob("*") if d.is_dir()) if self.access(str(p.opt), os.R_OK) else []
        for target in [*(d / "app" for d in installs), *(d / "venv" for d in installs), p.lib / "trees"]:
            self.expect("N-4", "create", target)
        self.expect("N-4", "create", p.lib / "actor")
        self.expect("N-4", "append", p.lib / "actor" / "audit.jsonl")
        for unit in UNITS:
            pid = self.main_pid(unit)
            if pid <= 0:
                self.report.add("N-5", f"{unit} main pid", FAIL, "the unit has no running process to probe")
                continue
            self.expect("N-5", "environ", str(pid))
            self.expect("N-5", "ptrace", str(pid))
        self.expect("N-6", "create", p.state_root)
        self.expect("N-6", "traverse", p.state_root / "holdings")
        for name in ("allowed_signers", "revoked_keys", "revoked_approvals", "trust_anchor", "actor.json", "gate.json"):
            self.expect("N-14", "append", p.config_dir / name)
        for unit in UNITS:
            self.expect("N-14", "append", p.unit_dir / unit)
        self.expect("N-14", "create", p.config_dir)
        self.expect("N-14", "create", p.unit_dir)
        self.expect("N-15", "append", p.managed_dir / "managed-settings.json")
        self.expect("N-15", "create", p.managed_dir / "hooks")

    # -- configuration -------------------------------------------------------------------------------------
    def import_paths(self) -> None:
        """N-13: nothing on a unit's import path or working directory is writable by this account."""
        for unit in UNITS:
            done = self.run([SYSTEMCTL, "show", "-p", "Environment", "-p", "WorkingDirectory", "-p", "ExecStart",
                             unit], capture_output=True, text=True)
            props = dict(line.split("=", 1) for line in done.stdout.splitlines() if "=" in line)
            env = props.get("Environment", "")
            if "PYTHONPATH=" in env:
                self.report.add("N-13", f"{unit} PYTHONPATH", FAIL, "the unit sets PYTHONPATH")
            work = props.get("WorkingDirectory", "") or "/"
            program = next((w.split("=", 1)[1] for w in props.get("ExecStart", "").replace(";", " ").split()
                            if w.startswith("path=")), "")
            for label, target in (("working directory", work), ("interpreter", program)):
                if not target:
                    self.report.add("N-13", f"{unit} {label}", FAIL, "systemctl did not report it")
                    continue
                writable = self.access(target, os.W_OK) or self.access(os.path.dirname(target) or "/", os.W_OK)
                self.report.add("N-13", f"{unit} {label} {target}", FAIL if writable else PASS,
                                "writable by this account" if writable else "not writable by this account")

    def managed_settings(self) -> None:
        """The guard and the main-commit block run from root-owned copies this account cannot change."""
        p = self.paths
        settings = p.managed_dir / "managed-settings.json"
        try:
            hooks = json.loads(settings.read_text(encoding="utf-8")).get("hooks", {}).get("PreToolUse", [])
        except (OSError, ValueError) as exc:
            self.report.add("guard", f"read {settings}", FAIL, c4._kind(exc))
            return
        commands = " ".join(h.get("command", "") for entry in hooks for h in entry.get("hooks", []))
        for name in ("guard-local-only.sh", "block-main-commits.sh"):
            script = p.managed_dir / "hooks" / name
            named = str(script) in commands
            try:
                st = script.stat()
                owned = st.st_uid == self.owner_uid and not st.st_mode & 0o022
            except OSError:
                owned = False
            self.report.add("guard", f"{script}", PASS if named and owned else FAIL,
                            f"named in managed settings: {named}; root-owned, not group/world-writable: {owned}")
        payload = json.dumps({"tool_name": "Read",
                              "tool_input": {"file_path": str(p.state_root / "holdings" / "probe.json")}})
        done = self.run([BASH, str(p.managed_dir / "hooks" / "guard-local-only.sh")], input=payload,
                        capture_output=True, text=True, timeout=15)
        denied = '"permissionDecision":"deny"' in done.stdout.replace(" ", "")
        self.report.add("guard", "a holdings read fed to the managed guard", PASS if denied else FAIL,
                        "denied" if denied else f"not denied (exit {done.returncode})")

    def no_root_claude(self, proc: Path = Path("/proc")) -> None:
        """No Claude Code process runs as root once the switch is done."""
        found = 0
        for entry in proc.iterdir() if proc.is_dir() else []:
            if not entry.name.isdigit():
                continue
            try:
                uid = int(next(l.split()[1] for l in (entry / "status").read_text().splitlines() if l.startswith("Uid:")))
                cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ")
            except (OSError, StopIteration, ValueError):
                continue
            if uid == 0 and b"ccd-cli" in cmdline:
                found += 1
        self.report.add("root", "Claude processes running as root", PASS if found == 0 else FAIL, f"{found} found")

    # -- what must still be reached ------------------------------------------------------------------------
    def ask(self, path: Path, frame: dict[str, Any]) -> dict[str, Any] | None:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(90)
        try:
            client.connect(str(path))
            client.sendall((json.dumps(frame, separators=(",", ":")) + "\n").encode("ascii"))
            data = b""
            while not data.endswith(b"\n"):
                chunk = client.recv(65536)
                if not chunk:
                    break
                data += chunk
            return json.loads(data.split(b"\n", 1)[0])
        except (OSError, ValueError):
            return None
        finally:
            client.close()

    def reached(self) -> None:
        p = self.paths
        door = self.ask(p.door_socket, {"command": "schedules", "client_id": "c6b-b8"})
        self.report.add("door", "read door schedules", PASS if door and door.get("ok") is True else FAIL,
                        "no answer" if door is None else f"ok={door.get('ok')} reason_code={door.get('reason_code')}")
        gate = self.ask(p.gate_socket, {"proto": 2, "client_id": "c6b-b8", "request_id": "c6b-b8-inspect",
                                        "verb": "inspect_candidate", "args": {"pr": 1}})
        through = gate is not None and gate.get("reason_code") != "PEER_NOT_PERMITTED"
        self.report.add("gate", "inspect_candidate through the gate", PASS if through else FAIL,
                        "no answer" if gate is None else f"ok={gate.get('ok')} reason_code={gate.get('reason_code')}")

    def all(self) -> c4.Report:
        self.refusals()
        self.import_paths()
        self.managed_settings()
        self.no_root_claude()
        self.reached()
        return self.report


def main(argv: list[str] | None = None, *, checks: Checks | None = None, euid: int | None = None) -> int:
    parser = argparse.ArgumentParser(prog="c6b_checks", description=__doc__.splitlines()[0])
    parser.add_argument("--json", type=Path, help="also write the rows to this file")
    args = parser.parse_args(argv)
    if (os.geteuid() if euid is None else euid) != CLAUDE_UID:
        print(f"c6b_checks: run this as the claude account (uid {CLAUDE_UID}); as any other account a refusal "
              f"proves nothing", file=sys.stderr)
        return EXIT_USAGE
    report = (checks or Checks()).all()
    if args.json:
        args.json.write_text(json.dumps(report.rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    failed = sum(r["result"] == FAIL for r in report.rows)
    print(f"C6B_CHECKS={'FAIL' if failed else 'PASS'} ({len(report.rows)} rows, {failed} failed)")
    return EXIT_FAILED if failed else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
