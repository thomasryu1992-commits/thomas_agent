"""A runner service's configuration: one root-owned JSON file per service, read once at start.

C3b (Thomas 2026-10-17: service entry point, config loader, install tool and unit templates; synthetic
tests only). The file says everything the service may not take from anywhere else — the environment it
serves, the uids allowed at its socket, its paths, the time policy, and the environment fingerprint
it must be running in (CR-8 condition 6). Nothing is read from the process environment.

The file is opened without following a link and checked on its descriptor (``trust.read_protected_file``:
regular, one link, owned by ``owner_uid`` — root in production — and writable by nobody else), parsed
strictly (ASCII, no duplicate keys, no non-finite numbers) and held to the closed
``deploy_runner_config.v0.1`` schema. Any doubt is ``RUNNER_STARTUP_BLOCKED``: a service that cannot be
sure what it was configured to do does not start.

The trust anchor (CR-12 T3) is a separate root-owned file in the reviewed install, one
``ssh-ed25519 <key>`` per line — the format of ``revoked_keys``, read with the same parser — and must
name at least one key.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from .. import schema_cache
from .approval_verify import SCHEMAS_DIR, Environment
from .clock import TimePolicy
from .reasons import RunnerRefused
from .trust import SignerKey, parse_revoked_keys, read_protected_file

SCHEMA_FILE = "deploy_runner_config.v0.1.schema.json"
_BLOCKED = "RUNNER_STARTUP_BLOCKED"


@dataclass(frozen=True)
class RunnerConfig:
    role: str
    install_sha: str
    environment: Environment
    fingerprint: dict[str, str]
    socket: Path
    allowed_uids: frozenset[int]
    directory: Path
    body: dict[str, Any]

    @property
    def gate(self) -> dict[str, Any]:
        return self.body["gate"]

    @property
    def actor(self) -> dict[str, Any]:
        return self.body["actor"]

    def policy(self) -> TimePolicy:
        p = self.actor["policy"]
        return TimePolicy(max_lifetime_s=p["max_lifetime_s"], issue_skew_s=p["issue_skew_s"],
                          deadlines_s=dict(p["deadlines_s"]), clock_jump_s=p["clock_jump_s"],
                          backstep_tolerance_s=p["backstep_tolerance_s"])


def _strict_json(raw: bytes) -> Any:
    def no_duplicates(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError("duplicate key")
            out[key] = value
        return out

    def no_constant(_):
        raise ValueError("non-finite number")

    try:
        return json.loads(raw.decode("ascii"), object_pairs_hook=no_duplicates, parse_constant=no_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise RunnerRefused(_BLOCKED) from None


def _paths(body: dict[str, Any]) -> list[str]:
    found = [body["socket"], body["directory"]]
    found += [body["gate"]["actor_socket"]] if "gate" in body else []
    if "actor" in body:
        a = body["actor"]
        found += [a[k] for k in ("trees_root", "docker_bin", "git_bin", "ssh_keygen_bin", "trust_anchor", "allowed_signers",
                                 "revoked_keys", "revoked_approvals")]
    return found


def load_config(path: Path, *, role: str, owner_uid: int = 0) -> RunnerConfig:
    """The configuration for ``role`` at ``path``, or ``RUNNER_STARTUP_BLOCKED``."""
    raw = read_protected_file(Path(path), owner_uid=owner_uid, unavailable=_BLOCKED, insecure=_BLOCKED)
    body = _strict_json(raw)
    try:
        schema_cache.validate_against_schema(body, SCHEMAS_DIR / SCHEMA_FILE, "runner config")
    except RuntimeSchemaError:
        raise RunnerRefused(_BLOCKED) from None
    if body["role"] != role:
        raise RunnerRefused(_BLOCKED)
    # The pattern admits only absolute paths of plain characters; a ``..`` segment would still walk out.
    if any(".." in Path(p).parts or "//" in p for p in _paths(body)):
        raise RunnerRefused(_BLOCKED)
    env = body["environment"]
    return RunnerConfig(role=body["role"], install_sha=body["install_sha"],
                        environment=Environment(project=env["project"], host_label=env["host_label"],
                                                profile=env["profile"]),
                        fingerprint=dict(body["fingerprint"]), socket=Path(body["socket"]),
                        allowed_uids=frozenset(body["allowed_uids"]), directory=Path(body["directory"]), body=body)


def load_anchor(path: Path, *, owner_uid: int = 0) -> frozenset[SignerKey]:
    """The installed anchor's keys (CR-12 T3). An unreadable, unsafe or empty anchor refuses to start."""
    raw = read_protected_file(Path(path), owner_uid=owner_uid, unavailable=_BLOCKED, insecure=_BLOCKED)
    try:
        keys = parse_revoked_keys(raw)
    except RunnerRefused:
        raise RunnerRefused(_BLOCKED) from None
    if not keys:
        raise RunnerRefused(_BLOCKED)
    return keys


def environment_fingerprint() -> dict[str, str]:
    """This interpreter's version and one hash over every installed distribution's name and version.

    CR-8 condition 6: the service compares it with the install's expectation before it does anything
    else. The install tool prints it from the installed interpreter so the expectation is measured, not
    typed."""
    pairs = sorted({f"{(d.metadata['Name'] or '').lower()}=={d.version}" for d in importlib.metadata.distributions()})
    digest = "sha256:" + hashlib.sha256("\n".join(pairs).encode("utf-8")).hexdigest()
    return {"python": platform.python_version(), "distributions_sha256": digest}


def check_environment(config: RunnerConfig, *, measured: dict[str, str] | None = None) -> None:
    """``RUNNER_ENVIRONMENT_MISMATCH`` unless this interpreter and its packages are the installed ones."""
    if (measured or environment_fingerprint()) != config.fingerprint:
        raise RunnerRefused("RUNNER_ENVIRONMENT_MISMATCH")
