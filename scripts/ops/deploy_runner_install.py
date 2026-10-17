"""Option C deploy runner install tool — the steps of an install that write, as code instead of prose.

C3b (Thomas 2026-10-17: install tool implemented and tested on synthetic paths only). The install itself
is Thomas's, at C5/C6, as root on the target host. Claude does not run this on a real host::

    python -m scripts.ops.deploy_runner_install fingerprint
    python -m scripts.ops.deploy_runner_install genesis --dir /var/lib/thomas-deploy/actor --install-sha <sha> --owner-uid <X uid>
    python -m scripts.ops.deploy_runner_install render-units --out <dir> --install-dir /opt/thomas-deploy/<sha> \
        --config-dir /etc/thomas-deploy --gate-user <G> --gate-group <G group> --actor-user <X> --actor-group <X group> \
        --gate-dir /var/lib/thomas-deploy/gate --actor-dir /var/lib/thomas-deploy/actor
    python -m scripts.ops.deploy_runner_install check-config --role actor --config /etc/thomas-deploy/actor.json

- ``fingerprint`` prints, from the interpreter it runs under, the value a config's ``fingerprint`` must hold
  (CR-8 condition 6). Run it with the installed interpreter so the expectation is measured, not typed.
- ``genesis`` writes the consumption store's first row (C2 §3.4: the install makes it, X never does) with a
  fresh 128-bit ``store_id``, mode 0600, never over an existing file, and prints the ``store_id`` the actor's
  config must name. It refuses a directory that is not private.
- ``render-units`` fills the two unit templates. Every value is held to a closed form; a template that keeps
  a placeholder, or an output file that already exists, refuses.
- ``check-config`` is the service's own start check without listening (``serve --check``).

Every path must be absolute, and none may lie under the runtime state root: this tool never touches
``.runtime_governance_state``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import stat
import sys
from pathlib import Path

from runtime.mvp_runtime.deploy_runner import consumption, private_files, serve

try:  # module form from the repo root, or the script's own directory on sys.path
    from scripts.lib.utctime import utc_now
except ImportError:  # pragma: no cover
    from lib.utctime import utc_now
from runtime.mvp_runtime.cli_common import force_utf8_io
from runtime.mvp_runtime.deploy_runner.config import environment_fingerprint

UNITS_DIR = Path(__file__).resolve().parent / "deploy_runner_units"
TEMPLATES = ("thomas-deploy-gate.service.in", "thomas-deploy-actor.service.in")
_ACCOUNT = re.compile(r"^[a-z_][a-z0-9_-]{0,31}\Z")
_SHA = re.compile(r"^[0-9a-f]{40}\Z")
_PATH = re.compile(r"^/[A-Za-z0-9._/-]{0,255}\Z")
_PLACEHOLDER = re.compile(r"@[A-Z_]+@")
EXIT_OK, EXIT_USAGE, EXIT_REFUSED = 0, 2, 3


class InstallRefused(Exception):
    """A step that would have written something it must not. The message is a fixed phrase."""


def _abs(value: str) -> Path:
    if not _PATH.match(value) or ".." in Path(value).parts or "//" in value or ".runtime_governance_state" in value:
        raise InstallRefused("path must be absolute, plain, and outside the runtime state root")
    return Path(value)


def genesis(directory: Path, *, install_sha: str, owner_uid: int | None, now: str | None = None) -> str:
    """Write the GENESIS row into ``directory`` and return its ``store_id``.

    The install runs as root and the directory belongs to the actor, so this does not use the actor's own
    ``private_files`` opener (which requires the caller to own the directory). It applies the same rules
    itself: the directory opened without following a link and checked on its descriptor (owned by the
    actor, private), the file created exclusively through that descriptor, mode 0600, handed to the
    actor, ``fsync``-ed with its directory."""
    if not _SHA.match(install_sha):
        raise InstallRefused("install sha must be 40 lowercase hex")
    owner = os.geteuid() if owner_uid is None else owner_uid
    try:
        dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        raise InstallRefused("directory missing or not a plain directory") from None
    try:
        st = os.fstat(dir_fd)
        if st.st_uid != owner or stat.S_IMODE(st.st_mode) & 0o077:
            raise InstallRefused("directory must be owned by the actor and private (0700)")
        store_id = secrets.token_hex(16)
        row = consumption.genesis_row(store_id=store_id, install_sha=install_sha,
                                      created_at=now or utc_now())
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(consumption.STORE_FILE, flags, 0o600, dir_fd=dir_fd)
        except FileExistsError:
            raise InstallRefused("a consumption store already exists; an install never replaces one") from None
        except OSError:
            raise InstallRefused("the store file could not be created") from None
        try:
            data = private_files.encode_row(row)
            if os.write(fd, data) != len(data):
                raise InstallRefused("short write")
            os.fchmod(fd, 0o600)
            if owner != os.geteuid():
                os.fchown(fd, owner, -1)        # the install runs as root; the actor owns its store
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    return store_id


def render_units(out: Path, values: dict[str, str]) -> list[Path]:
    """Fill both templates into ``out``. Accounts and paths are held to closed forms first."""
    for key in ("GATE_USER", "GATE_GROUP", "ACTOR_USER", "ACTOR_GROUP"):
        if not _ACCOUNT.match(values[key]):
            raise InstallRefused("account names must be plain lowercase names")
    if values["GATE_USER"] == values["ACTOR_USER"] or values["GATE_GROUP"] == values["ACTOR_GROUP"]:
        raise InstallRefused("G and X run as separate accounts and groups (D-10a)")
    for key in ("INSTALL_DIR", "CONFIG_DIR", "GATE_DIR", "ACTOR_DIR"):
        _abs(values[key])
    written: list[Path] = []
    for name in TEMPLATES:
        text = (UNITS_DIR / name).read_text(encoding="ascii")
        for key, value in values.items():
            text = text.replace(f"@{key}@", value)
        if _PLACEHOLDER.search(text):
            raise InstallRefused("a template placeholder was left unfilled")
        target = out / name.removesuffix(".in")
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        except FileExistsError:
            raise InstallRefused("a unit file already exists at the output path") from None
        try:
            os.write(fd, text.encode("ascii"))
        finally:
            os.close(fd)
        written.append(target)
    return written


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    parser = argparse.ArgumentParser(prog="deploy_runner_install")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("fingerprint")
    g = sub.add_parser("genesis")
    g.add_argument("--dir", required=True)
    g.add_argument("--install-sha", required=True)
    g.add_argument("--owner-uid", type=int)
    r = sub.add_parser("render-units")
    for flag in ("out", "install-dir", "config-dir", "gate-user", "gate-group", "actor-user", "actor-group",
                 "gate-dir", "actor-dir"):
        r.add_argument(f"--{flag}", required=True)
    c = sub.add_parser("check-config")
    c.add_argument("--role", choices=("gate", "actor"), required=True)
    c.add_argument("--config", required=True)
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return EXIT_USAGE
    try:
        if args.command == "fingerprint":
            print(json.dumps(environment_fingerprint(), sort_keys=True))
            return EXIT_OK
        if args.command == "genesis":
            print(genesis(_abs(args.dir), install_sha=args.install_sha, owner_uid=args.owner_uid))
            return EXIT_OK
        if args.command == "render-units":
            out = _abs(args.out)
            if not out.is_dir() or out.is_symlink() or stat.S_IMODE(out.stat().st_mode) & 0o022:
                raise InstallRefused("output directory must exist and be writable only by its owner")
            values = {"INSTALL_DIR": args.install_dir, "CONFIG_DIR": args.config_dir, "GATE_USER": args.gate_user,
                      "GATE_GROUP": args.gate_group, "ACTOR_USER": args.actor_user, "ACTOR_GROUP": args.actor_group,
                      "GATE_DIR": args.gate_dir, "ACTOR_DIR": args.actor_dir}
            for path in render_units(out, values):
                print(path)
            return EXIT_OK
        return serve.main(["--role", args.role, "--config", args.config, "--check"])
    except InstallRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
