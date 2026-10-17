"""X, the privileged actor: the only part of the runner that decides, spends, and acts.

C2 specification §0N, §3.1a, §6 and §7 (N1, Thomas DESIGN_ONLY 2026-10-16). The actor trusts
nothing the gatekeeper says: every message is re-validated against the closed actor schema, the
request fingerprint is the actor's own (``gate_intake_seq`` is kept out of it and never used to
decide), and an approval is verified, bound and spent here alone.

One request, in order (§3.1a):

1. latched (or latch state unreadable) → every request, inspection included, is ``RUNNER_LATCHED``;
2. validate, verify, refuse policy-blocked verbs (validate, promote, rollback: ``OPERATION_BLOCKED_BY_POLICY``
   — a code constant, CR-7; nothing in the environment or the request can open it);
3. claim the ``request_id`` (a resend replays, never re-runs);
4. for ``prepare_candidate``: verify the approval, take the operation lock, measure the tree and the
   running images, check the measured binding, **spend** the approval, write CONSUMED, then act —
   tag the measured running image as the rollback point and build the runner's own tree;
5. any failure after the spend ends as ``FAILED_EFFECT_PARTIAL`` or ``EFFECT_STATE_UNKNOWN`` and latches.

Replies are ``thomas.actor_reply.v1`` (§6.1b): ``{ok, reason_code, request_id, state, data, replayed}``.
``state`` is this request's record in X's request store — ``claimed``, ``completed``,
``refused_before_consume``, ``unknown`` or ``partial`` — or null when no record answers it (an
inspection, which claims nothing, or a refusal before any claim). A ``FAILED_EFFECT_PARTIAL`` refusal
carries the measured ids and nothing else (§5.2, §6.3); every other refusal carries no data.

C3a-3 boundary: the runner's tree must already exist (no clone, no fetch — a network read is not
decided); ``.env`` is never read; ``validate_candidate``, ``promote`` and ``rollback`` are not
implemented, only refused. Clock synchronisation has no decided source yet, so the default answer is
"not confirmed", and ``prepare_candidate`` is refused with ``CLOCK_UNTRUSTED`` until one is wired.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from runtime.read_only_kernel import integrity
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from .. import schema_cache
from . import docker_argv
from .approval_verify import (SCHEMAS_DIR, Environment, SshKeygenVerifier, VerifiedApproval, check_args,
                              check_measured_binding, verify_deploy_approval)
from .audit_chain import AuditLog
from .clock import ClockAnchor, TimePolicy, check_clock
from .commands import CommandTimeout, CommandUnavailable, run_command
from .consumption import ConsumptionStore
from .latch import Latch
from .private_files import lock_exclusive, open_private
from .reasons import RunnerRefused, response_code
from .request_state import CLAIMED, COMPLETED, PARTIAL, REFUSED_BEFORE_CONSUME, UNKNOWN, RequestStore
from .startup import StartupResult
from .trust import TrustMaterial

REQUEST_SCHEMA = "deploy_actor_request.v0.1.schema.json"
MESSAGE_SCHEMA = "thomas.actor_request.v1"
REPLY_SCHEMA = "deploy_actor_reply.v0.1.schema.json"
POLICY_BLOCKED_VERBS = frozenset({"validate_candidate", "promote", "rollback"})
# CR-7: the operational verbs this build may perform. A code constant, by decision; empty in C3a.
OPERATIONAL_VERBS_ENABLED: frozenset[str] = frozenset()
OPERATION_LOCK_FILE = "operation.lock"
SIGNER_PRINCIPAL = "thomas-deploy-approver"
_SHA = re.compile(r"^[0-9a-f]{40}\Z")
_IMAGE = re.compile(r"^sha256:[0-9a-f]{64}\Z")
_STATES = frozenset({"running", "exited", "restarting", "paused", "created", "dead"})   # §6.2, closed
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}\Z")
# What a refusal from the request store says about the request it found.
_BEGIN_STATES = {"REQUEST_IN_FLIGHT": CLAIMED, "EFFECT_STATE_UNKNOWN": UNKNOWN, "FAILED_EFFECT_PARTIAL": PARTIAL}
_HEALTH = frozenset({"healthy", "unhealthy", "starting", "none"})


def _stamp(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def actor_refusal(code: Any, *, request_id: Any = None, state: str | None = None,
                  data: dict[str, Any] | None = None, error_ref: Any = None) -> dict[str, Any]:
    """X's refusal in ``thomas.actor_reply.v1``. No sentence and no error reference travel to G: G answers
    the caller with the table's sentence for the code (``error_ref`` is accepted for the door's signature
    and dropped; the reference stays in X's own log line)."""
    rc = response_code(code)
    reply: dict[str, Any] = {"ok": False, "reason_code": rc, "state": state, "replayed": False,
                             "request_id": request_id if isinstance(request_id, str) and _REQUEST_ID.match(request_id)
                             else None}
    if rc == "FAILED_EFFECT_PARTIAL" and data is not None:
        reply["data"] = data
    return reply


class _Refused(RunnerRefused):
    """A refusal that knows the request record it found."""

    def __init__(self, reason_code: str, state: str | None) -> None:
        super().__init__(reason_code)
        self.state = state


@dataclass
class ActorConfig:
    directory: Path
    trees_root: Path
    docker: Path
    git: Path
    runner_install_sha: str
    environment: Environment
    policy: TimePolicy
    load_trust: Callable[[], TrustMaterial]
    load_revoked_approvals: Callable[[], frozenset[str]]
    verifier: SshKeygenVerifier
    run: Callable[..., tuple[int, str]] = run_command
    wall_now: Callable[[], datetime] = field(default=lambda: datetime.now(timezone.utc))
    mono_now: Callable[[], float] = field(default=time.monotonic)
    sync_confirmed: Callable[[], bool] = field(default=lambda: False)
    inspect_min_interval_s: float = 1.0


class Actor:
    def __init__(self, config: ActorConfig, startup: StartupResult) -> None:
        self.config = config
        self.startup = startup
        self.audit = AuditLog(config.directory, runner_install_sha=config.runner_install_sha)
        self.requests = RequestStore(config.directory)
        self.store = ConsumptionStore(config.directory)
        self.latch = Latch(config.directory)
        self.anchor = ClockAnchor(wall=config.wall_now(), mono=config.mono_now())
        self._last_inspect = float("-inf")
        self._latched = startup.latched

    # -- the entry ---------------------------------------------------------------------------------

    def handle(self, message: Any, *, peer_uid: int | None) -> dict[str, Any]:
        """One actor message in, one actor reply out. Never raises: a refusal is a reply."""
        try:
            return self._handle(message, peer_uid)
        except RunnerRefused as exc:
            return self._refusal(exc.reason_code, message, state=getattr(exc, "state", None))

    @staticmethod
    def _refusal(code: str, message: Any, *, state: str | None = None,
                 data: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = message.get("request_id") if isinstance(message, dict) else None
        return actor_refusal(code, request_id=request_id, state=state, data=data)

    @staticmethod
    def _ok(message: dict[str, Any], data: dict[str, Any], *, state: str | None) -> dict[str, Any]:
        return {"ok": True, "request_id": message["request_id"], "state": state, "data": data, "replayed": False}

    def _handle(self, message: Any, peer_uid: int | None) -> dict[str, Any]:
        if not self._latched:
            try:
                self._latched = self.latch.read() is not None
            except RunnerRefused:
                self._latched = True  # a latch that cannot be read is a latch
        if self._latched:
            raise RunnerRefused("RUNNER_LATCHED")
        if not isinstance(message, dict):
            raise RunnerRefused("MALFORMED_REQUEST")
        try:
            schema_cache.validate_against_schema(message, SCHEMAS_DIR / REQUEST_SCHEMA, "actor request")
        except RuntimeSchemaError:
            raise RunnerRefused("MALFORMED_REQUEST") from None
        verb = message["verb"]
        args = check_args(verb, message["args"])
        now = self.config.wall_now()
        # §6.1b carries no client_id to X: X's audit keeps it null, and G's intake row (same
        # gate_intake_seq) is where the attribution lives (GAP-2).
        base = {"request_id": message["request_id"], "action": verb, "caller_uid": peer_uid,
                "client_id": None, "gate_intake_seq": message["gate_intake_seq"],
                "store_id": self.startup.store_id}

        if verb in POLICY_BLOCKED_VERBS and verb not in OPERATIONAL_VERBS_ENABLED:
            self.audit.append(execution_state="REFUSED", validation_result="REFUSED", now=_stamp(now),
                              reason_code="OPERATION_BLOCKED_BY_POLICY", **base)
            raise RunnerRefused("OPERATION_BLOCKED_BY_POLICY")

        if verb == "inspect_candidate":
            # A read: no approval, no claim, nothing to replay (D-4). Each one is audited.
            if "approval_document_b64" in message or "approval_signature" in message:
                raise RunnerRefused("ARGUMENT_NOT_ACCEPTED")
            return self._inspect(message, args, base, now)

        # prepare_candidate. The fingerprint is the actor's own; the gatekeeper's sequence number is
        # bookkeeping it reports, so it is kept out: a resend through G gets a new one.
        fingerprint = integrity.sha256_value({k: v for k, v in message.items() if k != "gate_intake_seq"})
        request_id = message["request_id"]

        if request_id is None:
            raise RunnerRefused("REQUEST_ID_REQUIRED")
        try:
            begun = self.requests.begin(request_id, fingerprint, now=_stamp(now))
        except RunnerRefused as exc:
            raise _Refused(exc.reason_code, _BEGIN_STATES.get(exc.reason_code)) from None
        if begun.replay is not None:
            return dict(begun.replay, replayed=True)
        self.audit.append(execution_state="RECEIVED", validation_result="NOT_REQUIRED", now=_stamp(now), **base)
        return self._prepare(message, args, base, fingerprint)

    # -- inspection (D-4: no approval, fixed fields only) --------------------------------------------

    def _inspect(self, message, args, base, now) -> dict[str, Any]:
        mono = self.config.mono_now()
        if mono - self._last_inspect < self.config.inspect_min_interval_s:
            self.audit.append(execution_state="INSPECT", validation_result="REFUSED", now=_stamp(now),
                              reason_code="INSPECT_RATE_LIMITED", **base)
            return self._refusal("INSPECT_RATE_LIMITED", message)
        self._last_inspect = mono
        try:
            data = self._measure_images(args["pr"])
        except RunnerRefused as exc:
            self.audit.append(execution_state="INSPECT", validation_result="NOT_REQUIRED", now=_stamp(now),
                              reason_code=exc.reason_code, **base)
            return self._refusal(exc.reason_code, message)
        self.audit.append(execution_state="INSPECT", validation_result="NOT_REQUIRED", now=_stamp(now), **base)
        return self._ok(message, {"pr": args["pr"], **data, "measured_at": _stamp(now)}, state=None)

    def _run(self, argv: list[str], *, timeout_s: float = 30.0) -> tuple[int, str]:
        try:
            return self.config.run(argv, timeout_s=timeout_s)
        except CommandTimeout:
            raise RunnerRefused("DOCKER_TIMEOUT") from None
        except CommandUnavailable:
            raise RunnerRefused("IMAGE_STATE_UNAVAILABLE") from None

    def _image_id(self, ref: str) -> str | None:
        code, out = self._run(docker_argv.image_inspect(self.config.docker, ref))
        if code != 0:
            return None
        value = out.strip()
        if not _IMAGE.match(value):
            raise RunnerRefused("IMAGE_STATE_UNAVAILABLE")
        return value

    def _measure_images(self, pr: int) -> dict[str, Any]:
        containers, running = [], {}
        for name in docker_argv.RUNTIME_CONTAINERS:
            code, out = self._run(docker_argv.container_inspect(self.config.docker, name))
            if code != 0:
                running[name] = None             # absent: null in ``running``, no entry in ``containers`` (§6.2)
                continue
            parts = out.rstrip("\n").split(" ")
            if len(parts) != 4 or not _IMAGE.match(parts[0]) or parts[1] not in _STATES or parts[2] not in _HEALTH:
                raise RunnerRefused("IMAGE_STATE_UNAVAILABLE")
            if parts[3] != self.config.environment.project:
                # The container answering to this name belongs to another compose project (§6.3 item 7;
                # the code is the C2 revision of 2026-10-17).
                raise RunnerRefused("COMPOSE_PROJECT_MISMATCH")
            containers.append({"name": name, "state": parts[1], "health": parts[2]})
            running[name] = parts[0]
        latest = self._image_id(docker_argv.latest_ref())
        if latest is None:
            raise RunnerRefused("IMAGE_STATE_UNAVAILABLE")  # the daemon answered nothing it must know
        tags = {"latest": latest, "candidate": self._image_id(docker_argv.candidate_ref(pr)),
                "rollback_pre": self._image_id(docker_argv.rollback_ref(pr))}
        return {"running": running, "tags": tags, "containers": containers}

    # -- prepare_candidate ---------------------------------------------------------------------------

    def _tree(self, pr: int) -> tuple[str, str]:
        cfg = self.config
        code, status = self._run(docker_argv.git_status(cfg.git, cfg.trees_root, pr))
        if code != 0:
            raise RunnerRefused("TREE_UNAVAILABLE")
        if status.strip():
            raise RunnerRefused("TREE_NOT_CLEAN")
        code_h, head = self._run(docker_argv.git_head(cfg.git, cfg.trees_root, pr))
        code_m, main = self._run(docker_argv.git_origin_main(cfg.git, cfg.trees_root, pr))
        head, main = head.strip(), main.strip()
        if code_h != 0 or code_m != 0 or not _SHA.match(head) or not _SHA.match(main):
            raise RunnerRefused("TREE_UNAVAILABLE")
        if head != main:
            raise RunnerRefused("TREE_NOT_ORIGIN_MAIN")
        return head, main

    def _verify(self, message, args) -> VerifiedApproval:
        cfg = self.config
        trust = cfg.load_trust()
        revoked = cfg.load_revoked_approvals()
        return verify_deploy_approval(action="prepare_candidate", args=args,
                                      document_b64=message.get("approval_document_b64"),
                                      signature=message.get("approval_signature"), environment=cfg.environment,
                                      trust=trust, verifier=cfg.verifier, policy=cfg.policy,
                                      now=cfg.wall_now(), revoked_approvals=revoked)

    def _prepare(self, message, args, base, fingerprint) -> dict[str, Any]:
        cfg = self.config
        pr = args["pr"]
        request_id = message["request_id"]
        try:
            approval = self._verify(message, args)
            check_clock(cfg.policy, self.anchor, now_wall=cfg.wall_now(), now_mono=cfg.mono_now(),
                        last_seen_wall=None, sync_confirmed=cfg.sync_confirmed() and self.startup.clock_trusted)
        except RunnerRefused as exc:
            return self._refuse_before_consume(message, base, fingerprint, exc.reason_code)
        ref = {"approval_id": approval.approval_id, "approval_sha256": approval.approval_sha256,
               "signer_principal": SIGNER_PRINCIPAL}
        signed = {**base, "approval_ref": ref, "trust_config_sha256": approval.trust_config_sha256}

        try:
            lock_fd = open_private(cfg.directory, OPERATION_LOCK_FILE, os.O_RDWR, code="RUNNER_BUSY", create=True)
        except (FileNotFoundError, FileExistsError):
            return self._refuse_before_consume(message, signed, fingerprint, "RUNNER_BUSY")
        except RunnerRefused as exc:
            return self._refuse_before_consume(message, signed, fingerprint, exc.reason_code)
        try:
            try:
                lock_exclusive(lock_fd, code="RUNNER_BUSY", timeout_s=0.0)
                head, main = self._tree(pr)
                images = self._measure_images(pr)
                running = set(images["running"].values())
                if None in running or len(running) != 1:
                    raise RunnerRefused("RUNNING_IMAGES_INCONSISTENT")
                running_id = running.pop()
                if images["tags"]["candidate"] is not None or images["tags"]["rollback_pre"] is not None:
                    raise RunnerRefused("TAG_ALREADY_EXISTS")
                check_measured_binding(approval, head_sha=head, origin_main_sha=main, running_image_id=running_id)
            except RunnerRefused as exc:
                return self._refuse_before_consume(message, signed, fingerprint, exc.reason_code)
            target = {"pr": pr, "git_commit_sha": head, "candidate_image_id": None, "rollback_image_id": running_id}
            stamp = _stamp(cfg.wall_now())
            try:
                row = self.store.consume(approval, request_id=request_id, now=stamp,
                                         expected_store_id=self.startup.store_id)
            except RunnerRefused as exc:
                return self._refuse_before_consume(message, signed, fingerprint, exc.reason_code)
            try:
                self.audit.append(execution_state="CONSUMED", validation_result="PASS", now=stamp, target=target,
                                  consumption_seq=row["seq"], consumption_row_sha256=row["row_sha256"], **signed)
            except RunnerRefused:
                # Spent, nothing started: the effect is known to be absent, but the records now disagree,
                # and the next startup would latch on it. Latch now, the same way.
                return self._fail_after_spend(message, signed, fingerprint, "AUDIT_WRITE_FAILED", known_absent=True)
            return self._act(message, signed, fingerprint, pr, head, running_id)
        finally:
            os.close(lock_fd)

    def _act(self, message, signed, fingerprint, pr, head, running_id) -> dict[str, Any]:
        cfg = self.config
        deadline = cfg.mono_now() + cfg.policy.deadlines_s["prepare_candidate"]
        target = {"pr": pr, "git_commit_sha": head, "candidate_image_id": None, "rollback_image_id": running_id}
        try:
            self.audit.append(execution_state="EXECUTING", validation_result="PASS",
                              now=_stamp(cfg.wall_now()), target=target, **signed)
        except RunnerRefused:
            return self._fail_after_spend(message, signed, fingerprint, "AUDIT_WRITE_FAILED", known_absent=True)
        try:
            remaining = deadline - cfg.mono_now()
            if remaining <= 0:
                raise RunnerRefused("DEADLINE_EXCEEDED")
            code, _ = self._run(docker_argv.tag_rollback_point(cfg.docker, running_id, pr), timeout_s=remaining)
            if code != 0:
                raise RunnerRefused("DOCKER_COMMAND_FAILED")
            remaining = deadline - cfg.mono_now()
            if remaining <= 0:
                raise RunnerRefused("DEADLINE_EXCEEDED")
            code, _ = self._run(docker_argv.build_candidate(cfg.docker, cfg.trees_root, pr), timeout_s=remaining)
            if code != 0:
                raise RunnerRefused("BUILD_FAILED")
            candidate = self._image_id(docker_argv.candidate_ref(pr))
            if candidate is None:
                raise RunnerRefused("POST_CHECK_FAILED")
        except RunnerRefused as exc:
            return self._fail_after_spend(message, signed, fingerprint, exc.reason_code, pr=pr, head=head)
        target["candidate_image_id"] = candidate
        data = {"pr": pr, "git_commit_sha": head, "rollback_image_id": running_id, "candidate_image_id": candidate}
        reply = self._ok(message, data, state=COMPLETED)
        try:
            self.audit.append(execution_state="COMPLETED", validation_result="PASS", now=_stamp(cfg.wall_now()),
                              target=target, **signed)
        except RunnerRefused:
            return self._fail_after_spend(message, signed, fingerprint, "AUDIT_WRITE_FAILED", pr=pr, head=head)
        self.requests.finish(message["request_id"], fingerprint, COMPLETED, now=_stamp(cfg.wall_now()), result=reply)
        return reply

    # -- endings -------------------------------------------------------------------------------------

    def _refuse_before_consume(self, message, base, fingerprint, code) -> dict[str, Any]:
        """Nothing spent, nothing done: the request ends refused, and a resend replays the refusal."""
        now = _stamp(self.config.wall_now())
        reply = self._refusal(code, message, state=REFUSED_BEFORE_CONSUME)
        self.audit.append(execution_state="REFUSED", validation_result="REFUSED", now=now, reason_code=reply["reason_code"],
                          **base)
        self.requests.finish(message["request_id"], fingerprint, REFUSED_BEFORE_CONSUME, now=now, result=reply)
        return reply

    def _fail_after_spend(self, message, signed, fingerprint, cause, *, pr=None, head=None,
                          known_absent=False) -> dict[str, Any]:
        """The approval is spent and the outcome is not a clean success: never a retry, always a latch.

        ``known_absent`` — nothing was started, but a record the startup check cross-checks could not be
        written, so the records already disagree: the request ends ``unknown`` and the runner latches
        on ``RECONCILIATION_MISMATCH``, as the next startup would. Otherwise the actor re-measures: a
        measurement makes it PARTIAL (what is there is recorded), none makes it UNKNOWN. Neither ever
        becomes success, and a resend of either never runs again. A PARTIAL reply carries the measured ids
        (validated forms only: a PR number, a commit hash, ``sha256:`` image ids), never Docker's text."""
        cfg = self.config
        now = _stamp(cfg.wall_now())
        request_id = message["request_id"]
        target = data = None
        if known_absent:
            state, code, audit_state, latch_code = UNKNOWN, cause, "FAILED_NOT_STARTED", "RECONCILIATION_MISMATCH"
        else:
            try:
                tags = self._measure_images(pr)["tags"]
                target = {"pr": pr, "git_commit_sha": head, "candidate_image_id": tags["candidate"],
                          "rollback_image_id": tags["rollback_pre"]}
                data = {"pr": pr, "git_commit_sha": head, "rollback_image_id": tags["rollback_pre"],
                        "candidate_image_id": tags["candidate"]}
                state, code = PARTIAL, "FAILED_EFFECT_PARTIAL"
            except RunnerRefused:
                state, code = UNKNOWN, "EFFECT_STATE_UNKNOWN"
            audit_state = latch_code = code
        self._latched = True
        try:
            self.audit.append(execution_state=audit_state, validation_result="PASS", now=now, reason_code=cause,
                              target=target, **signed)
        except RunnerRefused:
            state, code, data = UNKNOWN, "EFFECT_STATE_UNKNOWN", None
        try:
            record = self.latch.set(reason_code=latch_code, request_id=request_id, now=now)
            self.audit.append(execution_state="LATCH_SET", validation_result="NOT_REQUIRED", now=now,
                              reason_code=record["reason_code"], latch_id=record["latch_id"],
                              request_id=request_id, store_id=self.startup.store_id)
        except RunnerRefused:
            pass  # latched in memory; the next startup re-derives the latch from the records
        try:
            self.requests.finish(request_id, fingerprint, state, now=now, result=None)
        except RunnerRefused:
            pass  # left claimed: the next startup turns it to unknown
        return self._refusal(code, message, state=state, data=data)
