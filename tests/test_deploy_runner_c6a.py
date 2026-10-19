"""The C6a install script (``scripts/ops/deploy_runner_c6a.py``), held before Thomas runs it on the host.

Synthetic: a fake host records what would run and answers what the script asks. The C4 workflow runs the same
script for real on a throwaway runner — install, the N tests, uninstall, ``status --expect-absent`` — which is
where it is proven against systemd and Docker.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from runtime.mvp_runtime import schema_cache
from runtime.mvp_runtime.deploy_runner.approval_verify import SCHEMAS_DIR
from scripts.ops import deploy_runner_c6a as c6a

# The script installs on a Linux host (paths like /etc/thomas-deploy, /proc/1/comm); Windows has none of it.
pytestmark = pytest.mark.skipif(os.name == "nt", reason="the C6a script installs on a Linux host")
SSH_KEYGEN = shutil.which("ssh-keygen")
needs_keygen = pytest.mark.skipif(SSH_KEYGEN is None, reason="needs ssh-keygen")
SHA = "a" * 40
LABEL = "ubuntu-4gb-sin-1"


def _key(directory: Path, name: str) -> tuple[str, str]:
    """A throwaway ed25519 key: its public line and ssh-keygen's own fingerprint of it."""
    path = directory / name
    subprocess.run([SSH_KEYGEN, "-q", "-t", "ed25519", "-N", "", "-C", f"{name}@mac", "-f", str(path)], check=True,
                   stdin=subprocess.DEVNULL)
    line = path.with_suffix(".pub").read_text(encoding="ascii").strip()
    fp = subprocess.run([SSH_KEYGEN, "-lf", str(path.with_suffix(".pub"))], capture_output=True, text=True,
                        check=True).stdout.split()[1]
    return line, fp


class FakeHost(c6a.Host):
    def __init__(self, *, dry=True, taken=(), existing=(), head=SHA, dirty="", files=None, root=True):
        super().__init__(dry=dry, log=lambda _line: None)
        self.taken, self.existing, self.head, self.dirty = set(taken), set(map(str, existing)), head, dirty
        self.files, self.root, self.ran = dict(files or {}), root, []

    def run(self, argv, *, input=None, check=True, cwd=None):
        self.ran.append(list(argv))
        out = b""
        if argv[-2:] == ["rev-parse", "HEAD"]:
            out = self.head.encode()
        elif argv[-1:] == ["--porcelain"]:
            out = self.dirty.encode()
        elif argv[:2] == ["id", "-u"]:
            out = str(c6a.IDS[argv[2]]).encode()
        return subprocess.CompletedProcess(argv, 0, out, b"")

    def getent(self, db, key):
        if (db, key) in self.taken:
            return f"{key}:x"
        return "docker:x:983:" if (db, key) == ("group", "docker") else None

    def exists(self, path):
        return str(path) in self.existing

    def read(self, path):
        return self.files[str(path)]

    def is_root(self):
        return self.root


def _plan(tmp_path, keys_text, fp, **kw):
    pub = tmp_path / "authorized_keys"
    pub.write_text(keys_text, encoding="ascii")
    return c6a.Plan(repo=tmp_path, sha=SHA, host_label=LABEL, signer_fingerprint=fp, pubkey_file=pub,
                    backup=tmp_path / "backup", **kw)


def _files(plan):
    return {"/proc/1/comm": "systemd\n", str(plan.pubkey_file): plan.pubkey_file.read_text(encoding="ascii")}


# --- the signing key (K1: an existing SSH key, chosen by its fingerprint) ---------------------------------

@needs_keygen
def test_the_fingerprint_is_the_one_ssh_keygen_prints(tmp_path):
    line, fp = _key(tmp_path, "k")
    assert c6a.fingerprint_of(line.split()[1]) == fp


@needs_keygen
def test_the_chosen_key_is_found_among_authorized_keys_without_its_options_or_comment(tmp_path):
    keys = [_key(tmp_path, f"k{i}") for i in range(4)]
    text = "\n".join([f'no-port-forwarding,from="10.0.0.1" {keys[0][0]}', keys[1][0], "# a comment",
                      f"command=\"/bin/true\" {keys[2][0]}", keys[3][0]]) + "\n"
    blob = c6a.select_signer(text, keys[2][1])
    assert blob == keys[2][0].split()[1]
    assert c6a.trust_files(blob)["allowed_signers"] == (
        f'thomas-deploy-approver namespaces="deploy-approval.v1@thomas-agent" ssh-ed25519 {blob}\n')
    assert c6a.trust_files(blob)["trust_anchor"] == f"ssh-ed25519 {blob}\n"


@needs_keygen
@pytest.mark.parametrize("case", ["absent", "twice", "not_a_fingerprint"])
def test_anything_but_exactly_one_matching_key_stops(tmp_path, case):
    line, fp = _key(tmp_path, "k")
    other, _ = _key(tmp_path, "o")
    text = {"absent": other, "twice": f"{line}\n{line}", "not_a_fingerprint": line}[case]
    with pytest.raises(c6a.Stop):
        c6a.select_signer(text, "e5hfox0p5" if case == "not_a_fingerprint" else fp)


# --- what is written ------------------------------------------------------------------------------------

def test_the_configurations_hold_their_schema_and_the_decided_policy():
    configs = c6a.runner_configs(sha=SHA, host_label=LABEL, store_id="1" * 32,
                                 fingerprint={"python": "3.12.13", "distributions_sha256": "sha256:" + "0" * 64})
    for body in configs.values():
        schema_cache.validate_against_schema(body, SCHEMAS_DIR / "deploy_runner_config.v0.1.schema.json", "config")
    actor, gate = configs["actor"], configs["gate"]
    assert actor["actor"]["policy"] == {"max_lifetime_s": 900, "issue_skew_s": 60, "clock_jump_s": 5,
                                        "backstep_tolerance_s": 5,
                                        "deadlines_s": {"prepare_candidate": 300, "validate_candidate": 300,
                                                        "promote": 300, "rollback": 300}}       # CR-10/11/19
    assert actor["allowed_uids"] == [2102] and gate["allowed_uids"] == [2101]
    assert actor["actor"]["trust_owner_uid"] == 0 and actor["environment"]["host_label"] == LABEL
    assert gate["gate"]["actor_deadline_s"] == 60


def test_the_interpreter_is_the_one_cr_8b_pinned():
    assert c6a.PYTHON_VERSION == "3.12.13" and "cpython-3.12.13%2B20260807-x86_64-unknown-linux-gnu-install_only" in c6a.PBS_URL
    assert c6a.PBS_SHA256 == "5bd6f36fd7ef02b909234c94dca9994ef0da06ace3bc3cece4fe27870e9cdbbe"


# --- preflight: nothing starts on a host that is not as the plan expects --------------------------------

@needs_keygen
@pytest.mark.parametrize("problem", ["uid_taken", "name_taken", "path_exists", "unit_exists", "not_at_sha",
                                     "dirty", "not_root", "bad_label", "no_systemd"])
def test_preflight_stops_before_anything_is_done(tmp_path, problem):
    line, fp = _key(tmp_path, "k")
    plan = _plan(tmp_path, line + "\n", fp)
    host = FakeHost(dry=False, files=_files(plan),
                    taken={("passwd", "2102")} if problem == "uid_taken" else {("group", "claude")} if problem == "name_taken" else (),
                    existing={c6a.OPT} if problem == "path_exists" else {c6a.UNIT_DIR / c6a.UNITS[0]} if problem == "unit_exists" else (),
                    head="b" * 40 if problem == "not_at_sha" else SHA, dirty=" M x" if problem == "dirty" else "",
                    root=problem != "not_root")
    if problem == "bad_label":
        plan.host_label = "Ubuntu_Host"
    if problem == "no_systemd":
        host.files["/proc/1/comm"] = "init\n"
    with pytest.raises(c6a.Stop):
        c6a.install(plan, host)
    assert host.actions == [] and not plan.backup.exists()


# --- the plan, in order, and nothing done under --dry-run -----------------------------------------------

@needs_keygen
def test_a_dry_run_lists_a0_to_a9_in_order_and_changes_nothing(tmp_path):
    line, fp = _key(tmp_path, "k")
    plan = _plan(tmp_path, line + "\n", fp)
    host = FakeHost(dry=True, files=_files(plan))
    c6a.install(plan, host)
    assert [a.split(" ", 1)[0] for a in host.actions] == ["a0", "a1", "a2", "a3", "a4", "a5", "a6", "a8", "a9"]
    assert all(cmd[0] == "/usr/bin/git" for cmd in host.ran)                 # only the preflight's reads
    assert not plan.backup.exists()


def test_an_uninstall_dry_run_lists_u1_to_u5_in_order_and_changes_nothing(tmp_path):
    host = FakeHost(dry=True)
    c6a.uninstall(host, backup=tmp_path / "backup")
    assert [a.split(" ", 1)[0] for a in host.actions] == ["u1", "u2", "u3", "u4", "u5"]
    assert host.ran == [] and not (tmp_path / "backup").exists()


@needs_keygen
def test_an_interpreter_whose_sha256_does_not_match_is_never_extracted(tmp_path, monkeypatch):
    line, fp = _key(tmp_path, "k")
    plan = _plan(tmp_path, line + "\n", fp)
    host = FakeHost(dry=False, files=_files(plan))

    def tampered(url, target):
        target.write_bytes(b"not the interpreter")

    made, real_mkdir = [], c6a.Path.mkdir

    def mkdir(self, *a, **k):
        if str(self).startswith(str(c6a.OPT)):
            made.append(str(self))                       # recorded, never created on the test host
            return None
        return real_mkdir(self, *a, **k)

    monkeypatch.setattr(c6a.Path, "mkdir", mkdir)
    with pytest.raises(c6a.Stop) as stopped:
        c6a.install(plan, host, download=tampered)
    assert "SHA256" in str(stopped.value)
    assert not any(cmd[:1] == ["tar"] for cmd in host.ran)
    assert str(plan.install) not in made
    assert [a.split(" ", 1)[0] for a in host.actions][-1] == "a2"


def test_absent_means_no_runner_account_path_socket_or_active_unit():
    clean = {"accounts": {"claude": False, "thomas-deploy-gate": False, "thomas-deploy-actor": False},
             "paths": {"/opt/thomas-deploy": False}, "sockets": {"/run/x": False},
             "units": {"thomas-deploy-actor.service": "inactive"}}
    assert c6a.absent(clean)
    assert c6a.absent({**clean, "accounts": {**clean["accounts"], "claude": True}})      # kept after C6b
    for broken in ({**clean, "paths": {"/opt/thomas-deploy": True}},
                   {**clean, "units": {"thomas-deploy-actor.service": "active"}},
                   {**clean, "accounts": {**clean["accounts"], "thomas-deploy-actor": True}}):
        assert not c6a.absent(broken)


def test_the_script_names_no_secret_path_and_touches_no_runtime_state():
    text = Path(c6a.__file__).read_text(encoding="utf-8")
    for forbidden in (".runtime_governance_state", "/root/thomas_agent/.env", "crontab -r", "docker compose",
                      "docker exec"):
        assert forbidden not in text, forbidden
    assert json.dumps(c6a.POLICY)                                             # plain data
