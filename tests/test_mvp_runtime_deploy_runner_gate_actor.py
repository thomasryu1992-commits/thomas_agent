"""Option C deploy runner, C3a-3: the gatekeeper (G), the actor (X), their sockets, and the closed
Docker argv templates.

All synthetic. Docker and git are a fake host that answers only argv built by ``docker_argv`` (any
other argv fails the test), signatures come from a fake verifier, and every record lives in
``tmp_path``. Nothing here runs Docker, reads ``.env`` or touches the runtime state root. Tests
that open records or sockets need POSIX and skip without it.
"""

from __future__ import annotations

import ast
import base64
import json
import os
import shutil
import socket
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from runtime.mvp_runtime import schema_cache
from runtime.mvp_runtime.deploy_runner import (actor as actor_mod, approval_verify as av, clock, commands,
                                               consumption, docker_argv, door, gate as gate_mod, latch,
                                               private_files, reasons, request_state, startup, trust)
from runtime.mvp_runtime.deploy_runner.audit_chain import AuditLog
from runtime.mvp_runtime.deploy_runner.reasons import RunnerRefused
from runtime.read_only_kernel import integrity

PACKAGE = Path(actor_mod.__file__).resolve().parent
SCHEMAS = av.SCHEMAS_DIR
posix_only = pytest.mark.skipif(not private_files.private_files_supported(),
                                reason="needs POSIX private-file opens (O_NOFOLLOW, dir_fd, flock)")
sockets_only = pytest.mark.skipif(not private_files.private_files_supported()
                                  or not getattr(socket, "SO_PEERCRED", None) or not hasattr(socket, "AF_UNIX"),
                                  reason="needs AF_UNIX sockets with SO_PEERCRED")

NOW = datetime(2026, 10, 16, 12, 0, 0, tzinfo=timezone.utc)
STAMP = "2026-10-16T12:00:00Z"
POLICY = clock.TimePolicy(max_lifetime_s=900, issue_skew_s=60,
                          deadlines_s={"prepare_candidate": 300, "validate_candidate": 300,
                                       "promote": 300, "rollback": 300})
ENV = av.Environment(project="thomas_agent", host_label="synthetic-host", profile="production")
STORE_ID = "1" * 32
INSTALL_SHA = "e" * 40
SHA = "c" * 40
RUNNING = "sha256:" + "b" * 64
LATEST = RUNNING
BUILT = "sha256:" + "a" * 64
PR = 1234
DOCKER = Path("/usr/bin/docker")
GIT = Path("/usr/bin/git")
TREES = Path(os.path.abspath("/srv/runner-trees"))  # absolute on every platform (a drive letter on Windows)
FAKE_SIGNATURE = "-----BEGIN SSH SIGNATURE-----\nU1NIU0lH\n-----END SSH SIGNATURE-----\n"
FAKE_TRUST = trust.TrustMaterial(allowed_signers=b"", revoked_keys=b"", keys=frozenset(),
                                 sha256="sha256:" + "0" * 64)


def stamp(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def approval_b64(action: str = "prepare_candidate", n: int = 0, **target) -> str:
    t = {"pr": PR, "git_commit_sha": SHA, "candidate_tag": f"candidate-{PR}", "candidate_image_id": None,
         "rollback_tag": f"rollback-pre-{PR}", "rollback_image_id": RUNNING}
    if action != "prepare_candidate":
        t["candidate_image_id"] = BUILT
    t.update(target)
    doc = {"schema": "thomas.deploy_approval.v1", "approval_id": "dap_" + "abcdefghij"[n] * 26,
           "nonce": f"{n:032x}", "action": action,
           "environment": {"project": "thomas_agent", "host_label": "synthetic-host", "profile": "production"},
           "target": t, "issued_at": stamp(NOW - timedelta(minutes=1)),
           "expires_at": stamp(NOW + timedelta(minutes=14)), "signature_namespace": "deploy-approval.v1@thomas-agent"}
    return base64.b64encode(integrity.canonical_bytes(doc)).decode("ascii")


class FakeVerifier:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.calls = 0

    def verify(self, document, signature, namespace, trust_material):
        self.calls += 1
        if not self.ok:
            raise RunnerRefused("SIGNATURE_INVALID")


def _closed_argv_set(pr: int = PR) -> dict[tuple[str, ...], str]:
    """Every argv the actor may run for ``pr``, keyed to what it is."""
    out = {tuple(docker_argv.container_inspect(DOCKER, c)): f"container:{c}" for c in docker_argv.RUNTIME_CONTAINERS}
    for ref in (docker_argv.latest_ref(), docker_argv.candidate_ref(pr), docker_argv.rollback_ref(pr)):
        out[tuple(docker_argv.image_inspect(DOCKER, ref))] = f"image:{ref}"
    out[tuple(docker_argv.tag_rollback_point(DOCKER, RUNNING, pr))] = "tag"
    out[tuple(docker_argv.build_candidate(DOCKER, TREES, pr))] = "build"
    out[tuple(docker_argv.git_status(GIT, TREES, pr))] = "git:status"
    out[tuple(docker_argv.git_head(GIT, TREES, pr))] = "git:head"
    out[tuple(docker_argv.git_origin_main(GIT, TREES, pr))] = "git:main"
    return out


class FakeHost:
    """Docker and git for one PR, answering only the closed argv set. ``fail`` maps a kind to an action."""

    def __init__(self, mono) -> None:
        self.mono = mono
        self.allowed = _closed_argv_set()
        self.calls: list[str] = []
        self.containers = {c: (RUNNING, "running", "healthy", "thomas_agent") for c in docker_argv.RUNTIME_CONTAINERS}
        self.tags = {docker_argv.latest_ref(): LATEST}
        self.tree = {"status": "", "head": SHA, "main": SHA}
        self.fail: dict[str, str] = {}

    def __call__(self, argv, *, timeout_s):
        assert isinstance(argv, list) and all(isinstance(a, str) for a in argv)
        kind = self.allowed.get(tuple(argv))
        assert kind is not None, f"argv outside the closed set: {argv}"
        assert timeout_s > 0
        self.calls.append(kind)
        action = self.fail.get(kind)
        if action == "timeout":
            raise commands.CommandTimeout()
        if action == "unavailable":
            raise commands.CommandUnavailable()
        if action == "exit":
            return 1, ""
        if action == "slow":
            self.mono.advance(10_000)
        if action == "garbage":
            return 0, "not an id SECRET_TOKEN=leak123 /root/.env\n"
        if kind.startswith("container:"):
            name = kind.split(":", 1)[1]
            if name not in self.containers:
                return 1, ""
            return 0, " ".join(self.containers[name]) + "\n"
        if kind.startswith("image:"):
            ref = kind.split(":", 1)[1]
            return (0, self.tags[ref] + "\n") if ref in self.tags else (1, "")
        if kind == "tag":
            self.tags[docker_argv.rollback_ref(PR)] = RUNNING
            return 0, ""
        if kind == "build":
            if action == "half":
                return 1, ""
            if action == "exit-tagged":   # the daemon tagged, then the client reported failure
                self.tags[docker_argv.candidate_ref(PR)] = BUILT
                return 1, ""
            self.tags[docker_argv.candidate_ref(PR)] = BUILT
            return 0, ""
        return 0, {"git:status": self.tree["status"], "git:head": self.tree["head"] + "\n",
                   "git:main": self.tree["main"] + "\n"}[kind]

    def effects(self) -> list[str]:
        return [c for c in self.calls if c in ("tag", "build")]


class Mono:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        self.t += 0.001
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


@pytest.fixture
def xdir(tmp_path):
    d = tmp_path / "actor"
    d.mkdir(mode=0o700)
    d.chmod(0o700)
    row = consumption.genesis_row(store_id=STORE_ID, install_sha=INSTALL_SHA, created_at=STAMP)
    (d / consumption.STORE_FILE).write_bytes(private_files.encode_row(row))
    (d / consumption.STORE_FILE).chmod(0o600)
    return d


@pytest.fixture
def gdir(tmp_path):
    d = tmp_path / "gate"
    d.mkdir(mode=0o700)
    d.chmod(0o700)
    return d


def make_actor(xdir, *, verifier=None, synced=True, revoked=frozenset(), host=None):
    mono = host.mono if host is not None else Mono()
    host = host or FakeHost(mono)
    result = startup.reconcile(xdir, runner_install_sha=INSTALL_SHA, trust_config_sha256=FAKE_TRUST.sha256,
                               now=NOW, backstep_tolerance_s=5, expected_store_id=STORE_ID)
    config = actor_mod.ActorConfig(
        directory=xdir, trees_root=TREES, docker=DOCKER, git=GIT, runner_install_sha=INSTALL_SHA,
        environment=ENV, policy=POLICY, load_trust=lambda: FAKE_TRUST, load_revoked_approvals=lambda: revoked,
        verifier=verifier or FakeVerifier(), run=host, wall_now=lambda: NOW, mono_now=mono,
        sync_confirmed=lambda: synced)
    return actor_mod.Actor(config, result), host


def message(verb="prepare_candidate", *, request_id="req-1", seq=1, document="default", signature=FAKE_SIGNATURE,
            **args):
    base_args = {"inspect_candidate": {"pr": PR}, "prepare_candidate": {"pr": PR, "git_commit_sha": SHA},
                 "validate_candidate": {"pr": PR, "git_commit_sha": SHA, "candidate_image_id": BUILT},
                 "promote": {"pr": PR, "git_commit_sha": SHA, "candidate_image_id": BUILT,
                             "rollback_image_id": RUNNING},
                 "rollback": {"pr": PR, "candidate_image_id": BUILT, "rollback_image_id": RUNNING}}[verb]
    base_args.update(args)
    if verb == "inspect_candidate" and document == "default":
        document, signature = None, None
    elif document == "default":
        document = approval_b64(verb)
    return {"proto": 1, "gate_intake_seq": seq, "client_id": "test-client", "request_id": request_id,
            "verb": verb, "args": base_args, "approval_document_b64": document, "approval_signature": signature}


def handle(actor, msg, uid=4242):
    reply = actor.handle(msg, peer_uid=uid)
    schema_cache.validate_against_schema(reply, SCHEMAS / actor_mod.REPLY_SCHEMA, "actor reply")
    return reply


def states(xdir):
    return [r["execution_state"] for r in AuditLog(xdir, runner_install_sha=INSTALL_SHA).read()
            if r["execution_state"] != "RUNNER_START"]


def spent(xdir):
    return [r for r in consumption.ConsumptionStore(xdir).read(expected_store_id=STORE_ID).rows
            if r["type"] == "APPROVAL"]


# --- the closed argv templates (CR-12 conditions 1–3) ----------------------------------------------

_SAFE_LITERALS = {"container", "inspect", "--format", docker_argv.CONTAINER_FORMAT, docker_argv.IMAGE_FORMAT,
                  "image", "tag", "build", "--pull=false", "--tag", "-C", "rev-parse", "HEAD",
                  "refs/remotes/origin/main", "status", "--porcelain", "--untracked-files=all"}


def _argv_element_ok(element: str, pr: int) -> bool:
    return (element in _SAFE_LITERALS or element in (str(DOCKER), str(GIT))
            or element in docker_argv.RUNTIME_CONTAINERS or element == RUNNING
            or element in (docker_argv.latest_ref(), docker_argv.candidate_ref(pr), docker_argv.rollback_ref(pr))
            or element == str(TREES / str(pr)))


@pytest.mark.parametrize("pr", [1, PR, 999_999])
def test_every_argv_element_is_a_fixed_literal_or_a_value_the_actor_derived(pr):
    for argv in _closed_argv_set(pr):
        for element in argv:
            assert _argv_element_ok(element, pr), (argv, element)


def test_no_template_runs_execs_copies_mounts_or_passes_environment():
    forbidden = {"run", "exec", "cp", "create", "-v", "--volume", "--mount", "-e", "--env", "--env-file",
                 "--privileged", "--build-arg", "--secret", "--ssh", "--network", "-u", "--user", "pull", "push",
                 "fetch", "clone", "login", "compose", "rm", "rmi", "kill", "stop", "start", "restart"}
    for argv in _closed_argv_set():
        assert not set(argv) & forbidden, argv


def test_every_template_builder_is_enumerated_by_the_closure_test():
    """A new builder in docker_argv must be added to the closed set above, or this fails."""
    tree = ast.parse((PACKAGE / "docker_argv.py").read_text(encoding="utf-8"))
    builders = {n.name for n in tree.body if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")}
    assert builders == {"candidate_ref", "rollback_ref", "latest_ref", "tree_path", "container_inspect",
                        "image_inspect", "tag_rollback_point", "build_candidate", "git_head", "git_origin_main",
                        "git_status"}


@pytest.mark.parametrize("call", [
    lambda: docker_argv.candidate_ref(0), lambda: docker_argv.candidate_ref(1_000_000),
    lambda: docker_argv.candidate_ref(True), lambda: docker_argv.candidate_ref("12"),
    lambda: docker_argv.container_inspect(DOCKER, "hermes"),
    lambda: docker_argv.container_inspect(DOCKER, "thomas-scheduler; rm -rf /"),
    lambda: docker_argv.image_inspect(DOCKER, "alpine:latest"),
    lambda: docker_argv.image_inspect(DOCKER, "thomas-agent-runtime:candidate-0"),
    lambda: docker_argv.image_inspect(DOCKER, "thomas-agent-runtime:latest --privileged"),
    lambda: docker_argv.tag_rollback_point(DOCKER, "thomas-agent-runtime:latest", PR),
    lambda: docker_argv.tag_rollback_point(DOCKER, "sha256:" + "A" * 64, PR),
    lambda: docker_argv.build_candidate(DOCKER, Path("relative/trees"), PR),
    lambda: docker_argv.git_head(GIT, TREES, -1),
])
def test_a_builder_refuses_any_input_outside_its_form(call):
    with pytest.raises(RunnerRefused) as e:
        call()
    assert e.value.reason_code == "ARGUMENT_INVALID"


def test_only_the_command_module_starts_processes_and_it_passes_no_environment():
    for path in PACKAGE.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
                    for a in getattr(n, "names", [])} | {
            (n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        if path.name in ("commands.py", "approval_verify.py"):
            continue  # approval_verify runs ssh-keygen by argv (C3a-1)
        assert "subprocess" not in imported, path.name
        assert not any(isinstance(n, ast.Attribute) and n.attr in ("system", "popen", "execv", "execve", "spawnv")
                       for n in ast.walk(tree)), path.name


def test_the_command_runner_uses_no_shell_and_a_fixed_environment(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen.update(kw, argv=argv)

        class R:
            returncode, stdout = 0, b"sha256:" + b"a" * 64 + b"\n"
        return R()

    monkeypatch.setenv("DOCKER_HOST", "tcp://elsewhere:2375")
    monkeypatch.setattr(commands.subprocess, "run", fake_run)
    code, out = commands.run_command(["/usr/bin/docker", "image", "inspect"], timeout_s=5)
    assert (code, out.strip()) == (0, "sha256:" + "a" * 64)
    assert seen["env"] == {"PATH": "/usr/bin:/bin", "LANG": "C"}
    assert "shell" not in seen and seen["stdin"] is commands.subprocess.DEVNULL
    with pytest.raises(commands.CommandTimeout):
        commands.run_command(["/usr/bin/docker"], timeout_s=0)


# --- the actor: policy, inspection, latch ----------------------------------------------------------

@posix_only
@pytest.mark.parametrize("verb", ["validate_candidate", "promote", "rollback"])
def test_operational_verbs_are_refused_by_a_code_constant_even_with_a_valid_approval(xdir, verb):
    actor, host = make_actor(xdir)
    reply = handle(actor, message(verb))
    assert reply["reason_code"] == "OPERATION_BLOCKED_BY_POLICY"
    assert host.calls == [] and spent(xdir) == []
    assert actor_mod.OPERATIONAL_VERBS_ENABLED == frozenset()


@posix_only
def test_inspection_reports_fixed_fields_and_needs_no_approval(xdir):
    actor, host = make_actor(xdir)
    host.containers.pop("thomas-switch-bridge")
    reply = handle(actor, message("inspect_candidate"))
    assert reply["ok"] and reply["data"]["tags"] == {"latest": LATEST, "candidate": None, "rollback_pre": None}
    assert reply["data"]["running"]["thomas-switch-bridge"] is None
    assert host.effects() == [] and states(xdir) == ["INSPECT"]


@posix_only
def test_inspection_with_an_approval_attached_is_refused(xdir):
    actor, host = make_actor(xdir)
    msg = message("inspect_candidate", document=approval_b64(), signature=FAKE_SIGNATURE)
    assert handle(actor, msg)["reason_code"] == "ARGUMENT_NOT_ACCEPTED"
    assert host.calls == []


@posix_only
def test_inspection_is_rate_limited(xdir):
    actor, _ = make_actor(xdir)
    assert handle(actor, message("inspect_candidate"))["ok"]
    assert handle(actor, message("inspect_candidate"))["reason_code"] == "INSPECT_RATE_LIMITED"


@posix_only
@pytest.mark.parametrize("output", ["garbage", "exit-latest"])
def test_an_unparseable_or_missing_measurement_is_image_state_unavailable(xdir, output):
    actor, host = make_actor(xdir)
    if output == "garbage":
        host.fail["container:thomas-scheduler"] = "garbage"
    else:
        host.tags.clear()
    reply = handle(actor, message("inspect_candidate"))
    assert reply["reason_code"] == "IMAGE_STATE_UNAVAILABLE"
    # what Docker printed reaches neither the reply nor the audit (C2 §8 additional synthetic test)
    for text in (json.dumps(reply), (xdir / "audit.jsonl").read_text()):
        assert "leak123" not in text and "/root/.env" not in text and "not an id" not in text


@posix_only
def test_a_runner_latched_at_startup_refuses_everything_including_inspection(xdir):
    latch.Latch(xdir).set(reason_code="RECONCILED_UNKNOWN", request_id=None, now=STAMP)
    actor, host = make_actor(xdir)
    for msg in (message("inspect_candidate"), message(), message("promote"), {"garbage": True}, None):
        assert handle(actor, msg)["reason_code"] == "RUNNER_LATCHED"
    assert host.calls == []


@posix_only
def test_a_latch_file_that_cannot_be_read_counts_as_latched(xdir):
    actor, _ = make_actor(xdir)
    (xdir / latch.LATCH_FILE).write_text("not json")
    (xdir / latch.LATCH_FILE).chmod(0o600)
    assert handle(actor, message("inspect_candidate"))["reason_code"] == "RUNNER_LATCHED"


# --- the actor never trusts G ----------------------------------------------------------------------

@posix_only
@pytest.mark.parametrize("change", [
    lambda m: m.update(verified=True),                          # a field G might add to vouch for it
    lambda m: m.pop("gate_intake_seq"),
    lambda m: m.update(proto=2),
    lambda m: m.update(gate_intake_seq=0),
    lambda m: m["args"].update(extra="x"),
    lambda m: m.update(verb="run"),
    lambda m: m.update(request_id="bad id with spaces"),
])
def test_a_message_outside_the_actor_schema_is_malformed_whatever_g_claims(xdir, change):
    actor, host = make_actor(xdir)
    msg = message()
    change(msg)
    assert handle(actor, msg)["reason_code"] == "MALFORMED_REQUEST"
    assert host.calls == [] and spent(xdir) == []


@posix_only
def test_an_unsigned_prepare_that_g_forwarded_is_refused_at_the_actor(xdir):
    """G-compromise: whatever G did, X verifies the approval itself."""
    actor, host = make_actor(xdir)
    reply = handle(actor, message(document=None, signature=None))
    assert reply["reason_code"] == "APPROVAL_REQUIRED"
    assert host.effects() == [] and spent(xdir) == []


@posix_only
def test_a_bad_signature_is_refused_at_the_actor_and_spends_nothing(xdir):
    actor, host = make_actor(xdir, verifier=FakeVerifier(ok=False))
    assert handle(actor, message())["reason_code"] == "SIGNATURE_INVALID"
    assert host.effects() == [] and spent(xdir) == [] and states(xdir) == ["RECEIVED", "REFUSED"]


@posix_only
def test_the_gate_sequence_number_does_not_change_the_actors_fingerprint(xdir):
    actor, host = make_actor(xdir)
    first = handle(actor, message(seq=7))
    again = handle(actor, message(seq=8))
    assert first["ok"] and again == dict(first, replayed=True)
    assert host.effects() == ["tag", "build"]


# --- prepare_candidate -----------------------------------------------------------------------------

@posix_only
def test_prepare_measures_spends_once_then_tags_the_running_image_and_builds(xdir):
    actor, host = make_actor(xdir)
    reply = handle(actor, message())
    assert reply["ok"] and reply["data"] == {"pr": PR, "git_commit_sha": SHA, "rollback_image_id": RUNNING,
                                             "candidate_image_id": BUILT}
    assert host.effects() == ["tag", "build"]
    assert host.calls.index("git:status") < host.calls.index("tag")
    assert states(xdir) == ["RECEIVED", "CONSUMED", "EXECUTING", "COMPLETED"]
    assert len(spent(xdir)) == 1
    audit = AuditLog(xdir, runner_install_sha=INSTALL_SHA).read()
    consumed = next(r for r in audit if r["execution_state"] == "CONSUMED")
    assert consumed["consumption_row_sha256"] == spent(xdir)[0]["row_sha256"]
    # the records agree: a restart is not latched
    assert startup.reconcile(xdir, runner_install_sha=INSTALL_SHA, trust_config_sha256=FAKE_TRUST.sha256, now=NOW,
                             backstep_tolerance_s=5, expected_store_id=STORE_ID).latched is False


@posix_only
def test_a_resend_replays_and_a_new_request_with_a_spent_approval_is_refused(xdir):
    actor, host = make_actor(xdir)
    assert handle(actor, message())["ok"]
    assert handle(actor, message())["replayed"] is True
    host.tags.pop(docker_argv.candidate_ref(PR))
    host.tags.pop(docker_argv.rollback_ref(PR))
    assert handle(actor, message(request_id="req-2"))["reason_code"] == "APPROVAL_ALREADY_CONSUMED"
    assert host.effects() == ["tag", "build"] and len(spent(xdir)) == 1


@posix_only
def test_a_request_id_reused_for_a_different_request_is_refused(xdir):
    actor, _ = make_actor(xdir)
    assert handle(actor, message())["ok"]
    assert handle(actor, message(pr=PR + 1))["reason_code"] == "REQUEST_ID_REUSED"


@posix_only
def test_prepare_without_a_request_id_is_refused(xdir):
    actor, host = make_actor(xdir)
    assert handle(actor, message(request_id=None))["reason_code"] == "REQUEST_ID_REQUIRED"
    assert host.calls == []


@posix_only
def test_an_unconfirmed_clock_refuses_prepare_by_default(xdir):
    actor, host = make_actor(xdir, synced=False)
    assert handle(actor, message())["reason_code"] == "CLOCK_UNTRUSTED"
    assert host.effects() == [] and spent(xdir) == []


@posix_only
def test_a_refusal_before_the_spend_replays_on_resend(xdir):
    actor, host = make_actor(xdir)
    host.tree["status"] = " M Dockerfile\n"
    assert handle(actor, message())["reason_code"] == "TREE_NOT_CLEAN"
    host.tree["status"] = ""
    again = handle(actor, message())
    assert again["reason_code"] == "TREE_NOT_CLEAN" and again["replayed"] is True
    assert host.effects() == []


@posix_only
@pytest.mark.parametrize("setup, code", [
    (lambda h: h.tree.update(status="?? stray\n"), "TREE_NOT_CLEAN"),
    (lambda h: h.tree.update(head="d" * 40), "TREE_NOT_ORIGIN_MAIN"),
    (lambda h: h.tree.update(head="d" * 40, main="d" * 40), "APPROVAL_BINDING_MISMATCH"),
    (lambda h: h.fail.update({"git:status": "exit"}), "TREE_UNAVAILABLE"),
    (lambda h: h.fail.update({"git:head": "garbage"}), "TREE_UNAVAILABLE"),
    (lambda h: h.containers.update({"thomas-operator": (BUILT, "running", "healthy", "thomas_agent")}),
     "RUNNING_IMAGES_INCONSISTENT"),
    (lambda h: h.containers.update({"thomas-operator": (RUNNING, "running", "healthy", "other_project")}),
     "RUNNER_ENVIRONMENT_MISMATCH"),
    (lambda h: h.containers.update({"thomas-operator": (RUNNING, "running", "healthy", "")}),
     "RUNNER_ENVIRONMENT_MISMATCH"),
    (lambda h: h.containers.pop("thomas-operator"), "RUNNING_IMAGES_INCONSISTENT"),
    (lambda h: h.tags.update({docker_argv.candidate_ref(PR): BUILT}), "TAG_ALREADY_EXISTS"),
    (lambda h: h.tags.update({docker_argv.rollback_ref(PR): RUNNING}), "TAG_ALREADY_EXISTS"),
    (lambda h: h.containers.update({c: (BUILT, "running", "healthy", "thomas_agent")
                                    for c in docker_argv.RUNTIME_CONTAINERS}),
     "APPROVAL_BINDING_MISMATCH"),
    (lambda h: h.fail.update({"container:thomas-scheduler": "timeout"}), "DOCKER_TIMEOUT"),
])
def test_a_measurement_that_does_not_hold_refuses_before_the_spend(xdir, setup, code):
    actor, host = make_actor(xdir)
    setup(host)
    assert handle(actor, message())["reason_code"] == code
    assert host.effects() == [] and spent(xdir) == []
    assert states(xdir) == ["RECEIVED", "REFUSED"]


@posix_only
def test_another_operation_in_progress_is_busy(xdir):
    actor, host = make_actor(xdir)
    fd = private_files.open_private(xdir, actor_mod.OPERATION_LOCK_FILE, os.O_RDWR, code="RUNNER_BUSY", create=True)
    try:
        private_files.lock_exclusive(fd, code="RUNNER_BUSY")
        assert handle(actor, message())["reason_code"] == "RUNNER_BUSY"
    finally:
        os.close(fd)
    assert host.effects() == [] and spent(xdir) == []


@posix_only
def test_a_revoked_approval_is_refused(xdir):
    actor, host = make_actor(xdir, revoked=frozenset({"dap_" + "a" * 26}))
    assert handle(actor, message())["reason_code"] == "APPROVAL_REVOKED"
    assert host.calls == []


# --- after the spend: never a retry, always a latch ------------------------------------------------

@posix_only
@pytest.mark.parametrize("fail, code, effects", [
    ({"build": "half"}, "FAILED_EFFECT_PARTIAL", ["tag", "build"]),
    ({"tag": "exit"}, "FAILED_EFFECT_PARTIAL", ["tag"]),
    ({"build": "timeout"}, "FAILED_EFFECT_PARTIAL", ["tag", "build"]),
    ({"tag": "slow"}, "FAILED_EFFECT_PARTIAL", ["tag"]),  # the deadline passes during the tag
])
def test_a_failure_after_the_spend_is_partial_latches_and_never_reruns(xdir, fail, code, effects):
    actor, host = make_actor(xdir)
    host.fail.update(fail)
    assert handle(actor, message())["reason_code"] == code
    assert host.effects() == effects and len(spent(xdir)) == 1
    assert states(xdir)[-2:] == ["FAILED_EFFECT_PARTIAL", "LATCH_SET"]
    assert latch.Latch(xdir).read()["reason_code"] == code
    host.fail.clear()
    for msg in (message(), message("inspect_candidate")):
        assert handle(actor, msg)["reason_code"] == "RUNNER_LATCHED"
    assert host.effects() == effects


@posix_only
@pytest.mark.parametrize("fail, cause", [({"build": "half"}, "BUILD_FAILED"), ({"build": "exit-tagged"}, "BUILD_FAILED"),
                                         ({"tag": "exit"}, "DOCKER_COMMAND_FAILED")])
def test_a_failed_command_is_never_read_as_success_whatever_it_left_behind(xdir, fail, cause):
    actor, host = make_actor(xdir)
    host.fail.update(fail)
    assert handle(actor, message())["reason_code"] == "FAILED_EFFECT_PARTIAL"
    audit = AuditLog(xdir, runner_install_sha=INSTALL_SHA).read()
    assert [r["reason_code"] for r in audit if r["execution_state"] == "FAILED_EFFECT_PARTIAL"] == [cause]


@posix_only
def test_a_build_that_reports_success_without_the_candidate_tag_is_partial(xdir):
    actor, host = make_actor(xdir)
    original = host.__call__

    def build_leaves_no_tag(argv, *, timeout_s):
        code, out = original(argv, timeout_s=timeout_s)
        if host.calls[-1] == "build":
            host.tags.pop(docker_argv.candidate_ref(PR))
        return code, out

    actor.config.run = build_leaves_no_tag
    assert handle(actor, message())["reason_code"] == "FAILED_EFFECT_PARTIAL"
    audit = AuditLog(xdir, runner_install_sha=INSTALL_SHA).read()
    assert [r["reason_code"] for r in audit if r["execution_state"] == "FAILED_EFFECT_PARTIAL"] == ["POST_CHECK_FAILED"]


@posix_only
def test_a_failure_that_cannot_be_remeasured_is_unknown(xdir):
    actor, host = make_actor(xdir)
    host.fail["build"] = "timeout"
    original = host.__call__

    def after_build(argv, *, timeout_s):
        if "build" in host.calls:
            raise commands.CommandUnavailable()
        return original(argv, timeout_s=timeout_s)

    actor.config.run = after_build
    assert handle(actor, message())["reason_code"] == "EFFECT_STATE_UNKNOWN"
    assert latch.Latch(xdir).read()["reason_code"] == "EFFECT_STATE_UNKNOWN"


@posix_only
def test_deleting_the_latch_file_does_not_unlatch_a_running_actor_or_the_next_start(xdir):
    actor, host = make_actor(xdir)
    host.fail["build"] = "half"
    handle(actor, message())
    (xdir / latch.LATCH_FILE).unlink()
    assert handle(actor, message("inspect_candidate"))["reason_code"] == "RUNNER_LATCHED"
    restarted, _ = make_actor(xdir, host=FakeHost(Mono()))
    assert handle(restarted, message("inspect_candidate"))["reason_code"] == "RUNNER_LATCHED"


@posix_only
def test_an_audit_that_cannot_record_the_spend_latches_and_starts_nothing(xdir, monkeypatch):
    actor, host = make_actor(xdir)
    real = actor.audit.append

    def refuse_consumed(**kw):
        if kw["execution_state"] == "CONSUMED":
            raise RunnerRefused("AUDIT_WRITE_FAILED")
        return real(**kw)

    monkeypatch.setattr(actor.audit, "append", refuse_consumed)
    assert handle(actor, message())["reason_code"] == "AUDIT_WRITE_FAILED"
    assert host.effects() == [] and len(spent(xdir)) == 1
    assert latch.Latch(xdir).read()["reason_code"] == "RECONCILIATION_MISMATCH"
    assert request_state.RequestStore(xdir).latest()["req-1"]["state"] == "unknown"
    assert handle(actor, message())["reason_code"] == "RUNNER_LATCHED"


@posix_only
def test_an_audit_that_fails_from_the_spend_onward_ends_unknown_and_latched(xdir, monkeypatch):
    """The real shape of an audit fault: every write from CONSUMED on fails, not just one."""
    actor, host = make_actor(xdir)
    real = actor.audit.append
    broken = []

    def failing(**kw):
        if kw["execution_state"] == "CONSUMED":
            broken.append(True)
        if broken:
            raise RunnerRefused("AUDIT_WRITE_FAILED")
        return real(**kw)

    monkeypatch.setattr(actor.audit, "append", failing)
    assert handle(actor, message())["reason_code"] == "EFFECT_STATE_UNKNOWN"
    assert host.effects() == [] and len(spent(xdir)) == 1
    assert latch.Latch(xdir).read()["reason_code"] == "RECONCILIATION_MISMATCH"
    assert request_state.RequestStore(xdir).latest()["req-1"]["state"] == "unknown"
    monkeypatch.undo()
    assert handle(actor, message())["reason_code"] == "RUNNER_LATCHED"


# --- the gatekeeper --------------------------------------------------------------------------------

def frame(base_verb="prepare_candidate", **over):
    msg = message(base_verb)
    out = {k: msg[k] for k in ("client_id", "request_id", "verb", "args", "approval_document_b64",
                               "approval_signature")}
    out["proto"] = 2
    out.update(over)
    return out


class FakeActor:
    def __init__(self, gdir, reply=None, raise_code=None):
        self.gdir, self.reply, self.raise_code, self.messages, self.logged_before = gdir, reply, raise_code, [], []

    def __call__(self, path, msg, *, deadline_s):
        self.logged_before.append([r["event"] for r in gate_mod.IntakeLog(self.gdir).read()])
        self.messages.append(msg)
        if self.raise_code:
            raise RunnerRefused(self.raise_code)
        return self.reply if self.reply is not None else {"ok": True, "request_id": msg["request_id"],
                                                           "data": {"pr": PR, "git_commit_sha": SHA,
                                                                    "rollback_image_id": RUNNING,
                                                                    "candidate_image_id": BUILT},
                                                           "replayed": False}


def make_gate(gdir, fake):
    return gate_mod.Gate(gate_mod.GateConfig(directory=gdir, actor_socket=gdir / "x.sock", call_actor=fake,
                                             wall_now=lambda: NOW))


def runner_reply_ok(reply):
    schema_cache.validate_against_schema(reply, SCHEMAS / gate_mod.RUNNER_REPLY_SCHEMA, "runner reply")
    return reply


@posix_only
def test_the_gate_logs_forwarded_before_calling_the_actor_and_answers_in_the_runner_shape(gdir):
    fake = FakeActor(gdir)
    reply = runner_reply_ok(make_gate(gdir, fake).serve(frame(), 5000))
    assert reply["ok"] and reply["proto"] == 2 and reply["client_id"] == "test-client"
    assert fake.logged_before == [["FORWARDED"]]
    sent = fake.messages[0]
    assert sent["gate_intake_seq"] == 1 and sent["proto"] == 1
    schema_cache.validate_against_schema(sent, SCHEMAS / actor_mod.REQUEST_SCHEMA, "actor request")
    rows = gate_mod.IntakeLog(gdir).read()
    assert [(r["event"], r["peer_uid"]) for r in rows] == [("FORWARDED", 5000), ("ANSWERED", 5000)]


@posix_only
def test_the_gate_forwards_the_approval_unopened(gdir):
    """G holds no verifier: an unsigned or garbled approval is the actor's to refuse."""
    fake = FakeActor(gdir)
    make_gate(gdir, fake).serve(frame(approval_document_b64=None, approval_signature=None), 5000)
    assert fake.messages[0]["approval_document_b64"] is None
    assert not any("verif" in name.lower() for name in vars(gate_mod))


@posix_only
@pytest.mark.parametrize("over, code", [
    ({"proto": 1}, "MALFORMED_REQUEST"),
    ({"proto": True}, "MALFORMED_REQUEST"),
    ({"gate_intake_seq": 99}, "ARGUMENT_NOT_ACCEPTED"),
    ({"verb": "run"}, "VERB_NOT_PERMITTED"),
    ({"args": {"pr": PR, "git_commit_sha": SHA, "image": "alpine"}}, "ARGUMENT_NOT_ACCEPTED"),
    ({"args": {"pr": "1234", "git_commit_sha": SHA}}, "ARGUMENT_INVALID"),
    ({"client_id": "x" * 65}, "MALFORMED_REQUEST"),
    ({"request_id": "has space"}, "MALFORMED_REQUEST"),
    ({"approval_signature": "s" * 4097}, "APPROVAL_MALFORMED"),
    ({"approval_document_b64": "A" * 2735}, "APPROVAL_MALFORMED"),  # C2 §1.3: 2,048 bytes as base64
    ({"approval_document_b64": ""}, "APPROVAL_MALFORMED"),
])
def test_a_malformed_frame_is_refused_at_the_gate_without_reaching_the_actor(gdir, over, code):
    fake = FakeActor(gdir)
    reply = runner_reply_ok(make_gate(gdir, fake).serve(frame(**over), 5000))
    assert reply["reason_code"] == code and fake.messages == []
    assert [r["event"] for r in gate_mod.IntakeLog(gdir).read()] == ["REFUSED"]


@posix_only
@pytest.mark.parametrize("reply", [
    {"ok": True, "data": {"pr": PR, "secret": "x"}, "replayed": False},
    {"ok": False, "reason_code": "SIGNATURE_INVALID", "reason": "ssh-keygen said /etc/x", "replayed": False,
     "debug": "/root/.ssh"},
    {"ok": False, "reason_code": "NOT_A_CODE", "reason": "x", "replayed": False},
    {"ok": True, "data": None, "replayed": False},
    ["not", "an", "object"],
])
def test_an_actor_reply_outside_its_schema_never_reaches_the_caller(gdir, reply):
    out = runner_reply_ok(make_gate(gdir, FakeActor(gdir, reply=reply)).serve(frame(), 5000))
    assert out["reason_code"] == "ACTOR_REPLY_INVALID"
    assert "/root" not in json.dumps(out) and "secret" not in json.dumps(out)


@posix_only
def test_an_actor_refusal_is_retold_with_the_table_sentence(gdir):
    reply = {"ok": False, "reason_code": "SIGNATURE_INVALID", "reason": "something the actor made up",
             "replayed": False, "request_id": "req-1"}
    out = runner_reply_ok(make_gate(gdir, FakeActor(gdir, reply=reply)).serve(frame(), 5000))
    assert out["reason"] == reasons.RESPONSE_TEXT["SIGNATURE_INVALID"]


@posix_only
def test_an_unreachable_actor_is_actor_unavailable(gdir):
    out = runner_reply_ok(make_gate(gdir, FakeActor(gdir, raise_code="ACTOR_UNAVAILABLE")).serve(frame(), 5000))
    assert out["reason_code"] == "ACTOR_UNAVAILABLE"
    assert [r["event"] for r in gate_mod.IntakeLog(gdir).read()] == ["FORWARDED", "ANSWERED"]


@posix_only
@pytest.mark.parametrize("damage", ["dropped", "edited", "relinked", "renumbered"])
def test_a_damaged_intake_log_takes_no_more_rows_and_forwards_nothing(gdir, damage):
    gate = make_gate(gdir, FakeActor(gdir))
    gate.serve(frame(), 5000)
    path = gdir / gate_mod.INTAKE_FILE
    rows = [json.loads(line) for line in path.read_bytes().splitlines()]
    if damage == "dropped":
        rows = rows[1:]
    elif damage == "edited":
        rows[1]["peer_uid"] = 0                                   # hash no longer matches
    elif damage == "relinked":
        rows[1]["previous_row_sha256"] = "sha256:" + "9" * 64     # link broken, hash recomputed
        rows[1]["row_sha256"] = gate_mod._row_hash(rows[1])
    else:
        rows[1]["seq"] = 3                                        # sequence broken, hash recomputed
        rows[1]["row_sha256"] = gate_mod._row_hash(rows[1])
    path.write_bytes(b"".join(private_files.encode_row(r) for r in rows))
    fake = FakeActor(gdir)
    with pytest.raises(RunnerRefused) as e:
        make_gate(gdir, fake).serve(frame(), 5000)
    assert e.value.reason_code == "AUDIT_WRITE_FAILED" and fake.messages == []


# --- through both sockets --------------------------------------------------------------------------

@pytest.fixture
def sockdir():
    d = Path(tempfile.mkdtemp(prefix="drs"))  # short: AF_UNIX paths are limited to ~107 bytes
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _serve(server):
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    return thread


def _call(path, payload: bytes):
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(10)
    try:
        client.connect(str(path))
        client.sendall(payload)
        data = b""
        while not data.endswith(b"\n"):
            chunk = client.recv(65536)
            if not chunk:
                break
            data += chunk
        return json.loads(data)
    finally:
        client.close()


@sockets_only
def test_end_to_end_through_g_and_x_sockets(xdir, gdir, sockdir, monkeypatch):
    monkeypatch.setenv("MVP_BRIDGE_CLIENT_UID", "")  # the environment names nobody; the doors still check
    me = os.getuid()
    actor, host = make_actor(xdir)
    x = door.RunnerDoor(sockdir / "x.sock", lambda f, uid: actor.handle(f, peer_uid=uid), allowed_uids={me})
    g_logic = gate_mod.Gate(gate_mod.GateConfig(directory=gdir, actor_socket=sockdir / "x.sock",
                                                actor_deadline_s=10, wall_now=lambda: NOW))
    g = door.RunnerDoor(sockdir / "g.sock", g_logic.serve, allowed_uids={me})
    _serve(x), _serve(g)
    try:
        reply = runner_reply_ok(_call(sockdir / "g.sock", (json.dumps(frame()) + "\n").encode()))
        assert reply["ok"] and reply["data"]["candidate_image_id"] == BUILT
        assert host.effects() == ["tag", "build"]
        bad = _call(sockdir / "g.sock", b"{\"proto\": 2, \"proto\": 2}\n")
        assert bad == reasons.refusal_reply("MALFORMED_REQUEST")
    finally:
        g.shutdown(), x.shutdown(), g.server_close(), x.server_close()


@sockets_only
def test_a_peer_not_on_the_literal_list_is_refused_whatever_the_environment_says(sockdir, monkeypatch):
    me = os.getuid()
    monkeypatch.setenv("MVP_BRIDGE_CLIENT_UID", str(me))
    served = []
    x = door.RunnerDoor(sockdir / "x.sock", lambda f, uid: served.append(f) or {"ok": True},
                        allowed_uids={me + 1})
    _serve(x)
    try:
        reply = _call(sockdir / "x.sock", b"{}\n")
        assert reply == reasons.refusal_reply("PEER_NOT_PERMITTED") and served == []
    finally:
        x.shutdown(), x.server_close()


@sockets_only
@pytest.mark.parametrize("uids", [frozenset(), frozenset({-1}), frozenset({True})])
def test_a_runner_socket_without_a_named_peer_refuses_to_exist(sockdir, uids):
    with pytest.raises(RunnerRefused) as e:
        door.RunnerDoor(sockdir / "x.sock", lambda f, uid: {}, allowed_uids=uids)
    assert e.value.reason_code == "RUNNER_STARTUP_BLOCKED"
    assert not (sockdir / "x.sock").exists()


@sockets_only
def test_an_unexpected_exception_leaves_as_the_generic_code_and_nothing_else(sockdir, capsys):
    def explode(f, uid):
        raise ValueError("/root/secret-path token=abc")

    x = door.RunnerDoor(sockdir / "x.sock", explode, allowed_uids={os.getuid()})
    _serve(x)
    try:
        reply = _call(sockdir / "x.sock", b"{}\n")
    finally:
        x.shutdown(), x.server_close()
    assert reply["reason_code"] == "RUNNER_ERROR" and set(reply) == {"ok", "reason_code", "reason", "error_ref"}
    assert "secret" not in json.dumps(reply) and "secret" not in capsys.readouterr().err


@pytest.mark.parametrize("raw", [b"\xff\xfe", b"NaN", b"{\"a\": Infinity}", b"{\"a\":1,\"a\":2}", b"[" * 5000])
def test_a_frame_is_strict_ascii_json(raw):
    with pytest.raises(RunnerRefused) as e:
        door.decode_frame(raw)
    assert e.value.reason_code == "MALFORMED_REQUEST"
