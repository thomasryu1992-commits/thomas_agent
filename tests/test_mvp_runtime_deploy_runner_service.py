"""Option C deploy runner, C3b: the service entry point, its configuration, the install tool, and the
systemd unit templates.

All synthetic, in ``tmp_path`` and a short socket directory. Nothing is installed, no unit is enabled, no
Docker command runs (the actor is built with the closed-argv fake host of the C3a-3 tests), and no real
key is made: the trust files carry a syntactically valid key that signs nothing. The production owner of
every root-owned file is uid 0; the tests pass their own uid as the owner and keep each file's mode exactly
what production requires.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import socket
import stat
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from runtime.mvp_runtime import schema_cache
from runtime.mvp_runtime.deploy_runner import (actor as actor_mod, config as config_mod, consumption, latch,
                                               private_files, serve)
from runtime.mvp_runtime.deploy_runner.reasons import RunnerRefused
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError
from scripts.ops import deploy_runner_install as install
from tests.test_mvp_runtime_deploy_runner_gate_actor import (DOCKER, GIT, TREES, FakeHost, FakeVerifier, Mono,
                                                              _call, frame, runner_reply_ok)

posix_only = pytest.mark.skipif(not private_files.private_files_supported(),
                                reason="needs POSIX private-file opens (O_NOFOLLOW, dir_fd, flock)")
sockets_only = pytest.mark.skipif(not private_files.private_files_supported()
                                  or not getattr(socket, "SO_PEERCRED", None) or not hasattr(socket, "AF_UNIX"),
                                  reason="needs AF_UNIX sockets with SO_PEERCRED")

ME = os.getuid() if hasattr(os, "getuid") else 0
INSTALL_SHA = "e" * 40
KEY = "AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
OTHER_KEY = "AAAAC3NzaC1lZDI1NTE5AAAAIBbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
PRINCIPAL = "thomas-deploy-approver"
REPO = Path(serve.__file__).resolve().parents[3]
UNITS = REPO / "scripts" / "ops" / "deploy_runner_units"


def private_dir(path: Path) -> Path:
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    return path


def write(path: Path, text: str, mode: int = 0o644) -> Path:
    path.write_text(text, encoding="ascii")
    path.chmod(mode)
    return path


@pytest.fixture
def sockdir():
    d = Path(tempfile.mkdtemp(prefix="drc"))   # AF_UNIX paths are short
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def site(tmp_path, sockdir):
    """One synthetic install: trust files, anchor, the actor's store (genesis), the gate's directory, configs."""
    etc = tmp_path / "etc"
    etc.mkdir()
    write(etc / "allowed_signers", f'{PRINCIPAL} namespaces="deploy-approval.v1@thomas-agent" ssh-ed25519 {KEY}\n')
    write(etc / "revoked_keys", "")
    write(etc / "revoked_approvals", "")
    write(etc / "trust_anchor", f"ssh-ed25519 {KEY}\n")
    xdir = private_dir(tmp_path / "actor")
    gdir = private_dir(tmp_path / "gate")
    store_id = install.genesis(xdir, install_sha=INSTALL_SHA, owner_uid=None,
                               now=datetime(2026, 10, 17, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    fp = config_mod.environment_fingerprint()
    common = {"schema": "thomas.deploy_runner_config.v1", "install_sha": INSTALL_SHA,
              "environment": {"project": "thomas_agent", "host_label": "synthetic-host", "profile": "production"},
              "fingerprint": fp, "allowed_uids": [ME]}
    actor = {**common, "role": "actor", "socket": str(sockdir / "x.sock"), "directory": str(xdir),
             "actor": {"trees_root": str(TREES), "docker_bin": str(DOCKER), "git_bin": str(GIT), "ssh_keygen_bin": "/usr/bin/ssh-keygen",
                       "trust_anchor": str(etc / "trust_anchor"), "allowed_signers": str(etc / "allowed_signers"),
                       "revoked_keys": str(etc / "revoked_keys"), "revoked_approvals": str(etc / "revoked_approvals"),
                       "trust_owner_uid": ME, "principal": PRINCIPAL, "store_id": store_id,
                       "policy": {"max_lifetime_s": 900, "issue_skew_s": 60, "clock_jump_s": 5, "backstep_tolerance_s": 5,
                                  "deadlines_s": {"prepare_candidate": 300, "validate_candidate": 300, "promote": 300,
                                                  "rollback": 300}},
                       "inspect_min_interval_s": 1.0}}
    gate = {**common, "role": "gate", "socket": str(sockdir / "g.sock"), "directory": str(gdir),
            "gate": {"actor_socket": str(sockdir / "x.sock"), "actor_deadline_s": 30}}
    return {"tmp": tmp_path, "etc": etc, "xdir": xdir, "gdir": gdir, "store_id": store_id, "actor": actor, "gate": gate}


def save(site, role, body=None, *, raw: str | None = None, mode: int = 0o644) -> Path:
    path = site["etc"] / f"{role}.json"
    if path.exists():
        path.unlink()
    return write(path, raw if raw is not None else json.dumps(body or site[role]), mode)


def code_of(excinfo) -> str:
    return excinfo.value.reason_code


# --- configuration ---------------------------------------------------------------------------------

@posix_only
@pytest.mark.parametrize("role", ["actor", "gate"])
def test_a_well_formed_config_loads(site, role):
    cfg = config_mod.load_config(save(site, role), role=role, owner_uid=ME)
    assert cfg.role == role and cfg.allowed_uids == frozenset({ME}) and cfg.install_sha == INSTALL_SHA


def _mutated(site, role, change):
    body = json.loads(json.dumps(site[role]))
    change(body)
    return body


@posix_only
@pytest.mark.parametrize("role, change", [
    ("actor", lambda b: b.update(extra=1)),
    ("actor", lambda b: b.update(role="gate")),                     # a gate config handed to the actor
    ("actor", lambda b: b.pop("actor")),
    ("actor", lambda b: b.update(gate={"actor_socket": "/run/x.sock", "actor_deadline_s": 5})),
    ("actor", lambda b: b.update(allowed_uids=[])),
    ("actor", lambda b: b.update(socket="relative/x.sock")),
    ("actor", lambda b: b.update(directory="/var/lib/../etc")),
    ("actor", lambda b: b.update(directory="/var//lib")),
    ("actor", lambda b: b["actor"].update(docker_bin="/usr/bin/docker --privileged")),
    ("actor", lambda b: b["actor"]["policy"].update(max_lifetime_s=0)),
    ("actor", lambda b: b["environment"].update(profile="staging")),
    ("gate", lambda b: b["gate"].update(actor_socket="actor.sock")),
    ("gate", lambda b: b.update(schema="thomas.deploy_runner_config.v2")),
])
def test_a_config_outside_its_closed_form_refuses_to_start(site, role, change):
    path = save(site, role, _mutated(site, role, change))
    with pytest.raises(RunnerRefused) as e:
        config_mod.load_config(path, role=role, owner_uid=ME)
    assert code_of(e) == "RUNNER_STARTUP_BLOCKED"


@posix_only
def test_a_valid_config_for_the_other_role_refuses_to_start(site):
    with pytest.raises(RunnerRefused) as e:
        config_mod.load_config(save(site, "gate"), role="actor", owner_uid=ME)
    assert code_of(e) == "RUNNER_STARTUP_BLOCKED"


@posix_only
def test_a_duplicate_key_in_an_otherwise_valid_config_refuses_to_start(site):
    text = json.dumps(site["gate"])
    raw = text.replace('"install_sha": ', '"install_sha": "%s", "install_sha": ' % INSTALL_SHA, 1)
    assert json.loads(raw) == site["gate"]                                     # a lax parser would accept it
    with pytest.raises(RunnerRefused) as e:
        config_mod.load_config(save(site, "gate", raw=raw), role="gate", owner_uid=ME)
    assert code_of(e) == "RUNNER_STARTUP_BLOCKED"


@posix_only
@pytest.mark.parametrize("raw", ['{"a": 1, "a": 2}', '{"a": NaN}', '{"schema": "é"}', "not json", ""])
def test_a_config_that_is_not_strict_ascii_json_refuses_to_start(site, raw):
    path = site["etc"] / "actor.json"
    path.write_bytes(raw.encode("utf-8"))
    path.chmod(0o644)
    with pytest.raises(RunnerRefused) as e:
        config_mod.load_config(path, role="actor", owner_uid=ME)
    assert code_of(e) == "RUNNER_STARTUP_BLOCKED"


@posix_only
@pytest.mark.parametrize("unsafe", ["group_writable", "other_writable", "symlink", "wrong_owner", "missing"])
def test_a_config_that_is_not_a_private_root_file_refuses_to_start(site, unsafe):
    path = save(site, "actor")
    owner = ME
    if unsafe == "group_writable":
        path.chmod(0o664)
    elif unsafe == "other_writable":
        path.chmod(0o646)
    elif unsafe == "symlink":
        link = site["etc"] / "link.json"
        link.symlink_to(path)
        path = link
    elif unsafe == "wrong_owner":
        owner = ME + 1
    else:
        path = site["etc"] / "absent.json"
    with pytest.raises(RunnerRefused) as e:
        config_mod.load_config(path, role="actor", owner_uid=owner)
    assert code_of(e) == "RUNNER_STARTUP_BLOCKED"


def test_the_config_schema_is_closed_and_demands_one_role_section():
    schema = REPO / "schemas" / config_mod.SCHEMA_FILE
    body = json.loads(schema.read_text(encoding="utf-8"))
    assert body["additionalProperties"] is False and body["properties"]["actor"]["additionalProperties"] is False
    with pytest.raises(RuntimeSchemaError):
        schema_cache.validate_against_schema({"schema": "thomas.deploy_runner_config.v1"}, schema, "config")


# --- environment fingerprint (CR-8 condition 6) ------------------------------------------------------

@posix_only
@pytest.mark.parametrize("field, value", [("python", "3.12.0"), ("distributions_sha256", "sha256:" + "0" * 64)])
def test_a_different_interpreter_or_package_set_refuses_to_start(site, field, value):
    cfg = config_mod.load_config(save(site, "gate"), role="gate", owner_uid=ME)
    config_mod.check_environment(cfg)                                         # the measured one passes
    measured = {**config_mod.environment_fingerprint(), field: value}
    with pytest.raises(RunnerRefused) as e:
        config_mod.check_environment(cfg, measured=measured)
    assert code_of(e) == "RUNNER_ENVIRONMENT_MISMATCH"


def test_the_fingerprint_is_a_python_version_and_one_hash():
    fp = config_mod.environment_fingerprint()
    assert set(fp) == {"python", "distributions_sha256"} and fp["distributions_sha256"].startswith("sha256:")
    assert fp == config_mod.environment_fingerprint()                          # deterministic


# --- building the actor -----------------------------------------------------------------------------

def _actor(site, **kw):
    cfg = config_mod.load_config(save(site, "actor"), role="actor", owner_uid=ME)
    return serve.build_actor(cfg, run=kw.pop("run", FakeHost(Mono())), verifier=FakeVerifier(), **kw)


@posix_only
def test_the_actor_builds_from_its_config_and_holds_the_instance_lock(site):
    actor, lock_fd = _actor(site)
    try:
        assert actor.startup.latched is False and actor.startup.store_id == site["store_id"]
        with pytest.raises(RunnerRefused) as e:
            _actor(site)
        assert code_of(e) == "INSTANCE_LOCK_HELD"
    finally:
        os.close(lock_fd)


@posix_only
@pytest.mark.parametrize("damage, code", [
    (lambda s: (s["xdir"] / consumption.STORE_FILE).unlink(), "RUNNER_STARTUP_BLOCKED"),
    (lambda s: s["actor"]["actor"].update(store_id="f" * 32), "RUNNER_STARTUP_BLOCKED"),
    (lambda s: s["actor"]["actor"]["policy"].update(max_lifetime_s=60), "RUNNER_STARTUP_BLOCKED"),
    (lambda s: write(s["etc"] / "trust_anchor", f"ssh-ed25519 {OTHER_KEY}\n"), "TRUST_ANCHOR_MISMATCH"),
    (lambda s: write(s["etc"] / "trust_anchor", ""), "RUNNER_STARTUP_BLOCKED"),
    (lambda s: (s["etc"] / "trust_anchor").chmod(0o666), "RUNNER_STARTUP_BLOCKED"),
    (lambda s: (s["etc"] / "revoked_keys").unlink(), "TRUST_CONFIG_UNAVAILABLE"),
])
def test_an_actor_whose_records_policy_or_trust_do_not_hold_does_not_start(site, damage, code):
    damage(site)
    with pytest.raises(RunnerRefused) as e:
        _actor(site)
    assert code_of(e) == code


@posix_only
def test_a_latched_actor_still_starts_and_refuses_every_request(site):
    latch.Latch(site["xdir"]).set(reason_code="RECONCILED_UNKNOWN", request_id=None, now="2026-10-17T00:00:00Z")
    actor, lock_fd = _actor(site)
    try:
        assert actor.startup.latched is True
        reply = actor.handle({"schema": "thomas.actor_request.v1", "request_id": None, "verb": "inspect_candidate",
                              "args": {"pr": 1}, "gate_intake_seq": 1}, peer_uid=ME)
        assert reply["reason_code"] == "RUNNER_LATCHED"
    finally:
        os.close(lock_fd)


@posix_only
def test_the_actor_asks_its_clock_source_at_each_request(site):
    """CLOCK-SOURCE = A (Thomas 2026-10-17): the served actor's answer is the kernel's, read at each ask."""
    answers = iter([False, True])
    actor, lock_fd = _actor(site, sync_confirmed=lambda: next(answers))
    try:
        assert actor.config.sync_confirmed() is False and actor.config.sync_confirmed() is True
    finally:
        os.close(lock_fd)


@posix_only
def test_by_default_the_served_actor_uses_the_kernel_source():
    import inspect

    from runtime.mvp_runtime.deploy_runner import clock_source

    default = inspect.signature(serve.build_actor).parameters["sync_confirmed"].default
    assert default is clock_source.kernel_sync_confirmed


@posix_only
def test_the_served_actor_runs_its_commands_with_docker_config_in_its_own_records_directory(site):
    """BUILD-HOME = A (Thomas 2026-10-17): the directory is X's private one, from the root-owned config."""
    from runtime.mvp_runtime.deploy_runner import commands

    cfg = config_mod.load_config(save(site, "actor"), role="actor", owner_uid=ME)
    actor, lock_fd = serve.build_actor(cfg, verifier=FakeVerifier())
    try:
        run = actor.config.run
        assert run.func is commands.run_command
        assert run.keywords == {"docker_config": site["xdir"] / commands.DOCKER_CONFIG_DIR}
    finally:
        os.close(lock_fd)


# --- the entry point ---------------------------------------------------------------------------------

@posix_only
@pytest.mark.parametrize("role", ["gate", "actor"])
def test_check_runs_the_start_checks_and_releases_the_lock(site, role, capsys):
    path = save(site, role)
    assert serve.main(["--role", role, "--config", str(path), "--check"], owner_uid=ME) == serve.EXIT_OK
    assert serve.main(["--role", role, "--config", str(path), "--check"], owner_uid=ME) == serve.EXIT_OK
    assert not (site["etc"].parent / "x.sock").exists()


@posix_only
@pytest.mark.parametrize("role", ["gate", "actor"])
def test_the_entry_point_refuses_a_foreign_interpreter_before_anything_else(site, role, capsys):
    body = json.loads(json.dumps(site[role]))
    body["fingerprint"]["python"] = "3.12.0" if body["fingerprint"]["python"] != "3.12.0" else "3.12.1"
    path = save(site, role, body)
    assert serve.main(["--role", role, "--config", str(path), "--check"], owner_uid=ME) == serve.EXIT_REFUSED
    assert capsys.readouterr().err.strip() == f"{role}: RUNNER_ENVIRONMENT_MISMATCH"


@posix_only
def test_a_refusal_prints_its_code_and_nothing_else(site, capsys):
    path = save(site, "actor")
    assert serve.main(["--role", "actor", "--config", str(path), "--check"], owner_uid=ME + 1) == serve.EXIT_REFUSED
    assert capsys.readouterr().err.strip() == "actor: RUNNER_STARTUP_BLOCKED"


@pytest.mark.parametrize("argv", [[], ["--role", "both", "--config", "/x"], ["--role", "gate"]])
def test_usage_errors_exit_two(argv):
    assert serve.main(argv) == serve.EXIT_USAGE


@sockets_only
def test_g_and_x_built_from_their_configs_answer_through_their_sockets(site):
    host = FakeHost(Mono())
    xcfg = config_mod.load_config(save(site, "actor"), role="actor", owner_uid=ME)
    gcfg = config_mod.load_config(save(site, "gate"), role="gate", owner_uid=ME)
    actor, lock_fd = serve.build_actor(xcfg, run=host, verifier=FakeVerifier())
    x = serve.open_door(xcfg, lambda f, uid: actor.handle(f, peer_uid=uid))
    g = serve.open_door(gcfg, serve.build_gate(gcfg).serve)
    threads = [threading.Thread(target=d.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True) for d in (x, g)]
    for t in threads:
        t.start()
    try:
        reply = runner_reply_ok(_call(gcfg.socket, (json.dumps(frame("inspect_candidate", args={"pr": 1234})) + "\n").encode()))
        assert reply["ok"] and reply["data"]["pr"] == 1234
        assert x.peer_refusal == "ACTOR_PEER_NOT_PERMITTED" and g.peer_refusal == "PEER_NOT_PERMITTED"
        assert host.effects() == []
    finally:
        for d in (g, x):
            d.shutdown()
            d.server_close()
            d._pool.shutdown(wait=True)      # no worker left alive for a later test's fork()
        for t in threads:
            t.join(timeout=5)
        os.close(lock_fd)


def test_the_service_package_reads_no_process_environment():
    package = Path(serve.__file__).resolve().parent
    for path in sorted(package.glob("*.py")) + [Path(install.__file__)]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert not any(isinstance(n, ast.Attribute) and n.attr in ("environ", "getenv", "environb")
                       for n in ast.walk(tree)), path.name


# --- install tool ------------------------------------------------------------------------------------

@posix_only
def test_genesis_writes_the_store_the_actor_expects_privately_and_only_once(tmp_path):
    d = private_dir(tmp_path / "x")
    store_id = install.genesis(d, install_sha=INSTALL_SHA, owner_uid=None)
    store = d / consumption.STORE_FILE
    assert len(store_id) == 32 and int(store_id, 16) >= 0
    assert stat.S_IMODE(store.stat().st_mode) == 0o600
    state = consumption.ConsumptionStore(d).read(expected_store_id=store_id)
    assert state.store_id == store_id and state.rows[0]["install_sha"] == INSTALL_SHA
    before = store.read_bytes()
    with pytest.raises(install.InstallRefused):
        install.genesis(d, install_sha=INSTALL_SHA, owner_uid=None)
    assert store.read_bytes() == before                                       # never replaced
    assert install.genesis(private_dir(tmp_path / "y"), install_sha=INSTALL_SHA, owner_uid=None) != store_id


@posix_only
@pytest.mark.parametrize("problem", ["not_private", "wrong_owner", "missing", "symlink", "bad_sha"])
def test_genesis_refuses_an_unsafe_target(tmp_path, problem):
    d = private_dir(tmp_path / "x")
    owner, sha = None, INSTALL_SHA
    if problem == "not_private":
        d.chmod(0o750)
    elif problem == "wrong_owner":
        owner = ME + 1
    elif problem == "missing":
        d = tmp_path / "absent"
    elif problem == "symlink":
        link = tmp_path / "link"
        link.symlink_to(d)
        d = link
    else:
        sha = "E" * 40
    with pytest.raises(install.InstallRefused):
        install.genesis(d, install_sha=sha, owner_uid=owner)


VALUES = {"INSTALL_DIR": "/opt/thomas-deploy/" + INSTALL_SHA, "CONFIG_DIR": "/etc/thomas-deploy",
          "GATE_USER": "thomas-deploy-gate", "GATE_GROUP": "tdg", "ACTOR_USER": "thomas-deploy-actor",
          "ACTOR_GROUP": "tdx", "GATE_DIR": "/var/lib/thomas-deploy/gate", "ACTOR_DIR": "/var/lib/thomas-deploy/actor"}


@posix_only
def test_render_units_fills_both_templates_and_leaves_no_placeholder(tmp_path):
    written = install.render_units(tmp_path, dict(VALUES))
    assert sorted(p.name for p in written) == ["thomas-deploy-actor.service", "thomas-deploy-gate.service"]
    actor = (tmp_path / "thomas-deploy-actor.service").read_text()
    gate = (tmp_path / "thomas-deploy-gate.service").read_text()
    assert "@" not in actor.replace("@thomas-agent", "") and "@" not in gate
    assert f"User={VALUES['ACTOR_USER']}" in actor and f"User={VALUES['GATE_USER']}" in gate
    assert f"ReadWritePaths={VALUES['ACTOR_DIR']}" in actor and f"ReadWritePaths={VALUES['GATE_DIR']}" in gate
    with pytest.raises(install.InstallRefused):
        install.render_units(tmp_path, dict(VALUES))                           # never over an existing unit


@pytest.mark.parametrize("change", [
    {"GATE_USER": "thomas-deploy-actor"}, {"GATE_GROUP": "tdx"}, {"ACTOR_USER": "Root"}, {"ACTOR_GROUP": "a b"},
    {"INSTALL_DIR": "opt/relative"}, {"CONFIG_DIR": "/etc/../root"},
    {"ACTOR_DIR": "/root/thomas_agent/.runtime_governance_state/x"},
])
def test_render_units_refuses_a_value_outside_its_form(tmp_path, change):
    with pytest.raises(install.InstallRefused):
        install.render_units(tmp_path, {**VALUES, **change})
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("name", install.TEMPLATES)
def test_each_unit_runs_the_isolated_entry_point_with_no_environment_file(name):
    text = (UNITS / name).read_text(encoding="ascii")
    role = "actor" if "actor" in name else "gate"
    assert f"python -I -m runtime.mvp_runtime.deploy_runner.serve --role {role} --config" in text
    for directive in ("NoNewPrivileges=yes", "ProtectSystem=strict", "PrivateTmp=yes", "UMask=0077",
                      "CapabilityBoundingSet=\n", "Environment=PATH=/usr/bin:/bin"):
        assert directive in text, (name, directive)
    assert "UnsetEnvironment=MVP_BRIDGE_CLIENT_GID MVP_BRIDGE_CLIENT_UID" in text
    assert "EnvironmentFile" not in text and ".env" not in text.replace("Environment", "")
    groups = [token for line in text.splitlines() if line.startswith("SupplementaryGroups=")
              for token in line.split("=", 1)[1].split()]
    assert ("docker" in groups) == (role == "actor")                       # only X holds Docker (D-10a)
    assert "User=root" not in text and "Group=root" not in text


def test_the_install_tool_cli(tmp_path, capsys):
    assert install.main(["fingerprint"]) == install.EXIT_OK
    assert json.loads(capsys.readouterr().out) == config_mod.environment_fingerprint()
    assert install.main([]) == install.EXIT_USAGE
    assert install.main(["genesis", "--dir", "relative", "--install-sha", INSTALL_SHA]) == install.EXIT_REFUSED
