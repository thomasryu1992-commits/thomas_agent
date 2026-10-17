"""The runner's service entry point: ``python -I -m runtime.mvp_runtime.deploy_runner.serve``.

C3b (Thomas 2026-10-17; synthetic tests only — nothing is installed or run as a service here). One
process per role, started by its systemd unit::

    python -I -m runtime.mvp_runtime.deploy_runner.serve --role actor --config /etc/thomas-deploy/actor.json
    python -I -m runtime.mvp_runtime.deploy_runner.serve --role gate  --config /etc/thomas-deploy/gate.json

In order, and any failure stops before a socket exists:

1. the configuration (``config.load_config``, root-owned, closed schema);
2. the environment fingerprint (CR-8 condition 6, ``RUNNER_ENVIRONMENT_MISMATCH``);
3. **X only:** the time policy invariants (``check_time_policy``), the installed trust anchor and the
   trust files against it (CR-12 T3), the instance lock, and the startup check
   (``startup.reconcile`` — a latched runner still starts, and refuses every request);
4. the socket, with the configuration's literal uid list (``door.RunnerDoor``): G answers in the
   caller's shape, X in ``thomas.actor_reply.v1`` with ``ACTOR_PEER_NOT_PERMITTED``.

``--check`` runs 1–3 and exits without listening: what an install verifies before enabling a unit.
Exit codes: 0 served or checked, 2 usage, 3 refused (the code, and nothing else, goes to stderr).

Clock synchronisation has no decided source (CLOCK-SOURCE), so the actor is built with the default
"not confirmed" and ``prepare_candidate`` answers ``CLOCK_UNTRUSTED``. Nothing here reads ``.env``.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..cli_common import force_utf8_io
from . import startup, trust
from .actor import Actor, ActorConfig, actor_refusal
from .approval_verify import SshKeygenVerifier
from .clock import check_time_policy
from .commands import run_command
from .config import RunnerConfig, check_environment, load_anchor, load_config
from .door import RunnerDoor
from .gate import Gate, GateConfig
from .reasons import RunnerRefused

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_REFUSED = 3


def build_actor(config: RunnerConfig, *, now: datetime | None = None, run: Callable[..., Any] = run_command,
                verifier: Any = None) -> tuple[Actor, int]:
    """X ready to serve, and the held instance lock's descriptor (keep it open for the process's life)."""
    a = config.actor
    policy = config.policy()
    check_time_policy(policy)
    owner = a["trust_owner_uid"]
    anchor = load_anchor(Path(a["trust_anchor"]), owner_uid=owner)

    def load_trust() -> trust.TrustMaterial:
        return trust.load_trust(Path(a["allowed_signers"]), Path(a["revoked_keys"]), owner_uid=owner,
                                principal=a["principal"], anchor=anchor)

    def load_revoked() -> frozenset[str]:
        return trust.load_revoked_approvals(Path(a["revoked_approvals"]), owner_uid=owner)

    material = load_trust()          # a start against trust files the anchor refuses does not start
    lock_fd = startup.acquire_instance_lock(config.directory)
    try:
        result = startup.reconcile(config.directory, runner_install_sha=config.install_sha,
                                   trust_config_sha256=material.sha256, now=now or datetime.now(timezone.utc),
                                   backstep_tolerance_s=policy.backstep_tolerance_s, expected_store_id=a["store_id"])
        actor_config = ActorConfig(
            directory=config.directory, trees_root=Path(a["trees_root"]), docker=Path(a["docker_bin"]),
            git=Path(a["git_bin"]), runner_install_sha=config.install_sha, environment=config.environment,
            policy=policy, load_trust=load_trust, load_revoked_approvals=load_revoked,
            verifier=verifier or SshKeygenVerifier(Path(a["ssh_keygen_bin"]), a["principal"], exe_owner_uid=owner),
            run=run, inspect_min_interval_s=float(a["inspect_min_interval_s"]))
        return Actor(actor_config, result), lock_fd
    except BaseException:
        os.close(lock_fd)
        raise


def build_gate(config: RunnerConfig) -> Gate:
    g = config.gate
    return Gate(GateConfig(directory=config.directory, actor_socket=Path(g["actor_socket"]),
                           actor_deadline_s=float(g["actor_deadline_s"])))


def open_door(config: RunnerConfig, serve: Callable[[Any, int], dict[str, Any]]) -> RunnerDoor:
    if config.role == "actor":
        return RunnerDoor(config.socket, serve, allowed_uids=config.allowed_uids,
                          peer_refusal="ACTOR_PEER_NOT_PERMITTED", refuse=actor_refusal)
    return RunnerDoor(config.socket, serve, allowed_uids=config.allowed_uids)


def prepare(config: RunnerConfig) -> tuple[Callable[[Any, int], dict[str, Any]], int | None]:
    """Steps 2–3: the serve function for the role, and the instance lock X holds (None for G)."""
    check_environment(config)
    if config.role == "actor":
        actor, lock_fd = build_actor(config)
        return (lambda frame, uid: actor.handle(frame, peer_uid=uid)), lock_fd
    gate = build_gate(config)
    return gate.serve, None


def _stop(_signum, _frame):
    raise KeyboardInterrupt


def main(argv: list[str] | None = None, *, owner_uid: int = 0) -> int:
    force_utf8_io()
    parser = argparse.ArgumentParser(prog="deploy_runner.serve", add_help=True)
    parser.add_argument("--role", choices=("gate", "actor"), required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--check", action="store_true", help="run the start checks and exit without listening")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return EXIT_USAGE
    lock_fd = None
    try:
        config = load_config(Path(args.config), role=args.role, owner_uid=owner_uid)
        serve, lock_fd = prepare(config)
        if args.check:
            print(f"{args.role}: start checks passed", file=sys.stderr)
            return EXIT_OK
        door = open_door(config, serve)
    except RunnerRefused as exc:
        print(f"{args.role}: {exc.reason_code}", file=sys.stderr)
        return EXIT_REFUSED
    finally:
        if lock_fd is not None and args.check:
            os.close(lock_fd)
    signal.signal(signal.SIGTERM, _stop)
    try:
        door.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        door.server_close()
        if lock_fd is not None:
            os.close(lock_fd)
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover — the unit's entry point
    sys.exit(main())
