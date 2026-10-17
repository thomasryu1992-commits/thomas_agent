"""Option C deploy runner, C3a-1: reason codes, canonical bytes, trust files, time rules, and
approval verification.

Everything here is synthetic. Signing tests make a throwaway ed25519 key in ``tmp_path`` for the
duration of one test; no real key, trust file or approval is read or written. Tests that need a
POSIX descriptor open (``O_NOFOLLOW``) or the ``ssh-keygen`` binary skip where those are absent.
"""

from __future__ import annotations

import ast
import base64
import json
import os
import shutil
import stat
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from runtime.mvp_runtime import schema_cache
from runtime.mvp_runtime.deploy_runner import approval_verify as av
from runtime.mvp_runtime.deploy_runner import canonical, clock, reasons, trust
from runtime.mvp_runtime.deploy_runner.reasons import RunnerRefused
from runtime.read_only_kernel import integrity
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

SCHEMAS = av.SCHEMAS_DIR
PACKAGE = Path(av.__file__).resolve().parent

posix_only = pytest.mark.skipif(not trust.protected_files_supported(),
                                reason="needs O_NOFOLLOW descriptor opens (POSIX)")
SSH_KEYGEN = shutil.which("ssh-keygen")
needs_ssh = pytest.mark.skipif(SSH_KEYGEN is None or not trust.protected_files_supported(),
                               reason="needs ssh-keygen and POSIX descriptor opens")

NOW = datetime(2026, 10, 16, 12, 0, 0, tzinfo=timezone.utc)
POLICY = clock.TimePolicy(max_lifetime_s=900, issue_skew_s=60,
                          deadlines_s={"prepare_candidate": 300, "validate_candidate": 300,
                                       "promote": 300, "rollback": 300})
ENV = av.Environment(project="thomas_agent", host_label="synthetic-host", profile="production")
PRINCIPAL = "thomas-deploy-approver"
IMG_A = "sha256:" + "a" * 64
IMG_B = "sha256:" + "b" * 64
SHA = "c" * 40


def code_of(excinfo) -> str:
    return excinfo.value.reason_code


def stamp(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_doc(action: str = "promote", **target_overrides) -> dict:
    target = {"pr": 1234, "git_commit_sha": SHA, "candidate_tag": "candidate-1234",
              "candidate_image_id": None if action == "prepare_candidate" else IMG_A,
              "rollback_tag": "rollback-pre-1234", "rollback_image_id": IMG_B}
    target.update(target_overrides)
    return {"schema": "thomas.deploy_approval.v1", "approval_id": "dap_" + "a" * 26, "nonce": "0" * 32,
            "action": action, "environment": {"project": "thomas_agent", "host_label": "synthetic-host",
                                              "profile": "production"},
            "target": target, "issued_at": stamp(NOW - timedelta(minutes=1)),
            "expires_at": stamp(NOW + timedelta(minutes=14)),
            "signature_namespace": "deploy-approval.v1@thomas-agent"}


def raw_of(doc: dict) -> bytes:
    return integrity.canonical_bytes(doc)


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


ARGS = {
    "prepare_candidate": {"pr": 1234, "git_commit_sha": SHA},
    "validate_candidate": {"pr": 1234, "git_commit_sha": SHA, "candidate_image_id": IMG_A},
    "promote": {"pr": 1234, "git_commit_sha": SHA, "candidate_image_id": IMG_A, "rollback_image_id": IMG_B},
    "rollback": {"pr": 1234, "candidate_image_id": IMG_A, "rollback_image_id": IMG_B},
}

FAKE_SIGNATURE = "-----BEGIN SSH SIGNATURE-----\nU1NIU0lH\n-----END SSH SIGNATURE-----\n"
FAKE_TRUST = trust.TrustMaterial(allowed_signers=b"", revoked_keys=b"", keys=frozenset(),
                                 sha256="sha256:" + "0" * 64)


class FakeVerifier:
    """Stands in for ssh-keygen: records the call, passes or fails as told."""

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.calls: list[tuple[bytes, str]] = []

    def verify(self, document, signature, namespace, trust_material):
        self.calls.append((document, namespace))
        if not self.ok:
            raise RunnerRefused("SIGNATURE_INVALID")


def verify(doc_or_raw, *, action="promote", args=None, verifier=None, revoked=frozenset(), cutoff=None,
           signature=FAKE_SIGNATURE, now=NOW, env=ENV, trust_material=FAKE_TRUST):
    raw = doc_or_raw if isinstance(doc_or_raw, bytes) else raw_of(doc_or_raw)
    return av.verify_deploy_approval(
        action=action, args=ARGS[action] if args is None else args, document_b64=b64(raw),
        signature=signature, environment=env, trust=trust_material, verifier=verifier or FakeVerifier(),
        policy=POLICY, now=now, revoked_approvals=revoked, cutoff_at=cutoff)


# --- reasons ------------------------------------------------------------------------------------

def test_every_code_is_a_reply_code_or_audit_only_and_has_one_fixed_sentence():
    assert not set(reasons.RESPONSE_TEXT) & set(reasons.AUDIT_TEXT)
    for code, text in {**reasons.RESPONSE_TEXT, **reasons.AUDIT_TEXT}.items():
        assert code.isupper() and text and len(text) <= 200
        assert not any(ch.isdigit() for ch in text) and "/" not in text, code


def test_the_reply_schema_enum_is_exactly_the_reply_codes_and_the_audit_enum_is_all_codes():
    reply = json.loads((SCHEMAS / "deploy_runner_reply.v0.1.schema.json").read_text(encoding="utf-8"))
    audit = json.loads((SCHEMAS / "deploy_audit.v0.1.schema.json").read_text(encoding="utf-8"))
    assert set(reply["properties"]["reason_code"]["enum"]) == set(reasons.RESPONSE_TEXT)
    audit_enum = audit["properties"]["reason_code"]["anyOf"][1]["enum"]
    assert set(audit_enum) == set(reasons.ALL_CODES)


@pytest.mark.parametrize("code", sorted(reasons.RESPONSE_TEXT))
def test_every_reply_code_forms_a_reply_that_satisfies_the_schema(code):
    reply = reasons.refusal_reply(code, request_id="req-1", error_ref="1234-0001")
    assert reply["reason"] == reasons.RESPONSE_TEXT[code]
    assert ("error_ref" in reply) == (code == reasons.GENERIC_CODE)
    schema_cache.validate_against_schema(reply, SCHEMAS / "deploy_runner_reply.v0.1.schema.json", "reply")


@pytest.mark.parametrize("code", ["NOT_A_CODE", None, 7, "STORE_GENESIS_MISSING", "RECOVERY_DOCUMENT_INVALID"])
def test_unknown_and_audit_only_codes_never_leave_as_themselves(code):
    reply = reasons.refusal_reply(code)
    assert reply["reason_code"] == reasons.GENERIC_CODE


@pytest.mark.parametrize("request_id", ["has space", "x" * 129, "slash/inside", "\n", 5])
def test_only_a_plain_request_id_is_echoed(request_id):
    assert "request_id" not in reasons.refusal_reply("RUNNER_BUSY", request_id=request_id)


def test_a_refusal_message_is_the_fixed_sentence_and_nothing_else():
    exc = RunnerRefused("SIGNATURE_INVALID")
    assert str(exc) == "SIGNATURE_INVALID: " + reasons.RESPONSE_TEXT["SIGNATURE_INVALID"]
    assert RunnerRefused("made-up").reason_code == reasons.GENERIC_CODE


def test_a_success_reply_carries_data_and_no_reason():
    schema = SCHEMAS / "deploy_runner_reply.v0.1.schema.json"
    schema_cache.validate_against_schema({"ok": True, "data": {}}, schema, "reply")
    with pytest.raises(RuntimeSchemaError):
        schema_cache.validate_against_schema({"ok": True, "data": {}, "reason_code": "RUNNER_BUSY",
                                              "reason": "x"}, schema, "reply")
    with pytest.raises(RuntimeSchemaError):
        schema_cache.validate_against_schema({"ok": False, "reason_code": "RUNNER_BUSY", "reason": "x",
                                              "error_ref": "1"}, schema, "reply")


# --- canonical bytes ----------------------------------------------------------------------------

def test_canonical_bytes_round_trip():
    raw = raw_of(make_doc())
    value = canonical.parse_strict(raw)
    canonical.require_canonical(raw, value)


@pytest.mark.parametrize("mutate", [
    lambda raw: json.dumps(json.loads(raw), indent=1).encode(),
    lambda raw: json.dumps(json.loads(raw), sort_keys=False, separators=(", ", ": ")).encode(),
    lambda raw: raw.replace(b'"thomas_agent"', b'"thomas\\u005fagent"'),
])
def test_any_other_spelling_of_the_same_document_is_not_canonical(mutate):
    raw = raw_of(make_doc())
    other = mutate(raw)
    if other == raw:
        other = raw[:-1] + b" }"
    with pytest.raises(RunnerRefused) as e:
        canonical.require_canonical(other, canonical.parse_strict(other))
    assert code_of(e) == "APPROVAL_NOT_CANONICAL"


@pytest.mark.parametrize("raw", [
    b'{"a":1,"a":2}', b'{"a":1.5}', b'{"a":NaN}', b'{"a":Infinity}', b'[1]', b'"x"',
    '{"a":"é"}'.encode("utf-8"), b'{"a":', b"", b"{" + b" " * 3000 + b"}",
])
def test_ambiguous_or_oversized_documents_are_malformed(raw):
    with pytest.raises(RunnerRefused) as e:
        canonical.parse_strict(raw)
    assert code_of(e) == "APPROVAL_MALFORMED"


@pytest.mark.parametrize("text", ["", "not base64!", "QQ", "QUJD\n", None, 5, "A" * 4000])
def test_the_document_carrier_must_be_strict_base64(text):
    with pytest.raises(RunnerRefused) as e:
        canonical.decode_document_b64(text)
    assert code_of(e) == "APPROVAL_MALFORMED"


@pytest.mark.parametrize("text", ["", "garbage", FAKE_SIGNATURE.replace("SSH SIGNATURE", "PGP SIGNATURE"),
                                  FAKE_SIGNATURE + "trailing", FAKE_SIGNATURE.replace("U1NI", "U1Né"), 7])
def test_the_signature_must_be_an_armored_ssh_signature(text):
    with pytest.raises(RunnerRefused) as e:
        canonical.check_signature_text(text)
    assert code_of(e) == "APPROVAL_MALFORMED"


# --- time ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("policy", [
    clock.TimePolicy(max_lifetime_s=200, issue_skew_s=60, deadlines_s={"promote": 300}),
    clock.TimePolicy(max_lifetime_s=900, issue_skew_s=0, deadlines_s={"promote": 300}),
    clock.TimePolicy(max_lifetime_s=900, issue_skew_s=60, deadlines_s={}),
    clock.TimePolicy(max_lifetime_s=900, issue_skew_s=60, deadlines_s={"promote": True}),
    clock.TimePolicy(max_lifetime_s=900, issue_skew_s=60, deadlines_s={"promote": -1}),
])
def test_a_lifetime_shorter_than_the_longest_deadline_or_a_bad_value_refuses_to_start(policy):
    with pytest.raises(RunnerRefused) as e:
        clock.check_time_policy(policy)
    assert code_of(e) == "RUNNER_STARTUP_BLOCKED"


def test_the_working_policy_passes():
    clock.check_time_policy(POLICY)


@pytest.mark.parametrize("issued,expires,now,cutoff,code", [
    (NOW, NOW, NOW, None, "APPROVAL_LIFETIME_EXCEEDS_POLICY"),
    (NOW, NOW + timedelta(seconds=901), NOW, None, "APPROVAL_LIFETIME_EXCEEDS_POLICY"),
    (NOW, NOW + timedelta(minutes=14), NOW, NOW + timedelta(seconds=1), "APPROVAL_BEFORE_CUTOFF"),
    (NOW + timedelta(seconds=61), NOW + timedelta(minutes=14), NOW, None, "APPROVAL_NOT_YET_VALID"),
    (NOW - timedelta(minutes=14), NOW, NOW, None, "APPROVAL_EXPIRED"),
    (NOW - timedelta(minutes=5), NOW + timedelta(seconds=299), NOW, None, "APPROVAL_TOO_SHORT_FOR_ACTION"),
])
def test_the_approval_window_is_checked_in_its_order(issued, expires, now, cutoff, code):
    with pytest.raises(RunnerRefused) as e:
        clock.check_approval_window(POLICY, action="promote", issued_at=issued, expires_at=expires,
                                    now=now, cutoff_at=cutoff)
    assert code_of(e) == code


def test_an_approval_inside_its_window_with_room_for_the_deadline_passes():
    clock.check_approval_window(POLICY, action="promote", issued_at=NOW + timedelta(seconds=60),
                                expires_at=NOW + timedelta(minutes=10), now=NOW, cutoff_at=NOW)


@pytest.mark.parametrize("value", ["2026-10-16T12:00:00+00:00", "2026-10-16T12:00:00Z\n", "2026-13-01T00:00:00Z",
                                   "2026-10-16 12:00:00Z", None])
def test_only_a_plain_utc_second_parses(value):
    with pytest.raises(RunnerRefused):
        clock.parse_utc_second(value)


ANCHOR = clock.ClockAnchor(wall=NOW, mono=1000.0)


@pytest.mark.parametrize("now_wall,now_mono,last_seen,synced", [
    (NOW + timedelta(seconds=10), 1010.0, None, False),
    (NOW + timedelta(seconds=10), 1010.0, NOW + timedelta(seconds=16), True),
    (NOW + timedelta(seconds=60), 1010.0, None, True),
    (NOW - timedelta(seconds=60), 1010.0, None, True),
    (NOW, 999.0, None, True),
])
def test_an_unsynchronised_backstepped_or_jumping_clock_is_untrusted(now_wall, now_mono, last_seen, synced):
    with pytest.raises(RunnerRefused) as e:
        clock.check_clock(POLICY, ANCHOR, now_wall=now_wall, now_mono=now_mono,
                          last_seen_wall=last_seen, sync_confirmed=synced)
    assert code_of(e) == "CLOCK_UNTRUSTED"


def test_a_steady_clock_within_tolerance_is_trusted():
    clock.check_clock(POLICY, ANCHOR, now_wall=NOW + timedelta(seconds=12), now_mono=1010.0,
                      last_seen_wall=NOW + timedelta(seconds=14), sync_confirmed=True)


# --- request arguments and the order of checks --------------------------------------------------

@pytest.mark.parametrize("action,args,code", [
    ("exec", {}, "VERB_NOT_PERMITTED"),
    ("promote", {**ARGS["promote"], "tree": "/root"}, "ARGUMENT_NOT_ACCEPTED"),
    ("promote", {k: v for k, v in ARGS["promote"].items() if k != "rollback_image_id"}, "ARGUMENT_INVALID"),
    ("promote", {**ARGS["promote"], "pr": "1234"}, "ARGUMENT_INVALID"),
    ("promote", {**ARGS["promote"], "pr": True}, "ARGUMENT_INVALID"),
    ("promote", {**ARGS["promote"], "pr": 0}, "ARGUMENT_INVALID"),
    ("promote", {**ARGS["promote"], "git_commit_sha": "../../etc"}, "ARGUMENT_INVALID"),
    ("promote", {**ARGS["promote"], "candidate_image_id": "thomas-agent-runtime:latest"}, "ARGUMENT_INVALID"),
    ("promote", [], "ARGUMENT_INVALID"),
])
def test_request_arguments_are_a_closed_set_of_plain_values(action, args, code):
    with pytest.raises(RunnerRefused) as e:
        av.check_args(action, args)
    assert code_of(e) == code


def test_an_approval_is_never_accepted_for_inspection():
    with pytest.raises(RunnerRefused) as e:
        av.verify_deploy_approval(action="inspect_candidate", args={"pr": 1}, document_b64="x",
                                  signature="x", environment=ENV, trust=FAKE_TRUST, verifier=FakeVerifier(),
                                  policy=POLICY, now=NOW, revoked_approvals=frozenset())
    assert code_of(e) == "ARGUMENT_NOT_ACCEPTED"


def test_no_document_means_approval_required():
    with pytest.raises(RunnerRefused) as e:
        av.verify_deploy_approval(action="promote", args=ARGS["promote"], document_b64=None, signature=None,
                                  environment=ENV, trust=FAKE_TRUST, verifier=FakeVerifier(), policy=POLICY,
                                  now=NOW, revoked_approvals=frozenset())
    assert code_of(e) == "APPROVAL_REQUIRED"


def test_the_signature_is_checked_before_a_single_unsigned_byte_is_parsed():
    garbage = b'{"schema":"thomas.deploy_approval.v1","a":1,"a":2}'
    fake = FakeVerifier(ok=False)
    with pytest.raises(RunnerRefused) as e:
        verify(garbage, verifier=fake)
    assert code_of(e) == "SIGNATURE_INVALID"
    assert fake.calls == [(garbage, trust.DEPLOY_NAMESPACE)]


def test_a_signed_but_non_canonical_document_is_refused_on_the_verify_path():
    raw = raw_of(make_doc()).replace(b'"thomas_agent"', b'"thomas\\u005fagent"')
    with pytest.raises(RunnerRefused) as e:
        verify(raw)
    assert code_of(e) == "APPROVAL_NOT_CANONICAL"


def test_a_valid_document_verifies_and_is_identified_by_its_bytes():
    raw = raw_of(make_doc())
    result = verify(raw)
    assert result.approval_sha256 == integrity.sha256_bytes(raw)
    assert result.approval_id == "dap_" + "a" * 26 and result.action == "promote"


@pytest.mark.parametrize("action", av.APPROVED_ACTIONS)
def test_each_approved_action_verifies_with_its_own_arguments(action):
    verify(make_doc(action), action=action)


@pytest.mark.parametrize("change,code", [
    (lambda d: d.pop("nonce"), "APPROVAL_FIELD_MISSING"),
    (lambda d: d["target"].pop("rollback_tag"), "APPROVAL_FIELD_MISSING"),
    (lambda d: d.update(extra=1), "APPROVAL_UNKNOWN_FIELD"),
    (lambda d: d["environment"].update(path="/root/thomas_agent/.env"), "APPROVAL_UNKNOWN_FIELD"),
    (lambda d: d.update(schema="thomas.deploy_approval.v2"), "APPROVAL_SCHEMA_UNSUPPORTED"),
    (lambda d: d.pop("schema"), "APPROVAL_FIELD_MISSING"),
    (lambda d: d.update(approval_id="dap_short"), "APPROVAL_MALFORMED"),
    (lambda d: d.update(signature_namespace="file"), "APPROVAL_MALFORMED"),
    (lambda d: d["target"].update(candidate_image_id=None), "APPROVAL_MALFORMED"),
    (lambda d: d.update(environment="x"), "APPROVAL_MALFORMED"),
])
def test_the_signed_document_is_held_to_its_closed_schema(change, code):
    doc = make_doc()
    change(doc)
    with pytest.raises(RunnerRefused) as e:
        verify(doc)
    assert code_of(e) == code


def test_a_prepare_approval_may_not_name_a_candidate_image():
    doc = make_doc("prepare_candidate", candidate_image_id=IMG_A)
    with pytest.raises(RunnerRefused) as e:
        verify(doc, action="prepare_candidate")
    assert code_of(e) == "APPROVAL_MALFORMED"


@pytest.mark.parametrize("kwargs,code", [
    ({"action": "rollback"}, "APPROVAL_ACTION_MISMATCH"),
    ({"env": av.Environment("thomas_agent", "other-host", "production")}, "APPROVAL_ENVIRONMENT_MISMATCH"),
    ({"revoked": frozenset({"dap_" + "a" * 26})}, "APPROVAL_REVOKED"),
    ({"cutoff": NOW}, "APPROVAL_BEFORE_CUTOFF"),
    ({"now": NOW + timedelta(minutes=15)}, "APPROVAL_EXPIRED"),
])
def test_meaning_checks_after_the_signature(kwargs, code):
    with pytest.raises(RunnerRefused) as e:
        verify(make_doc(), **kwargs)
    assert code_of(e) == code


@pytest.mark.parametrize("target_change,args_change", [
    ({"candidate_tag": "candidate-999"}, {}),
    ({"rollback_tag": "rollback-pre-999"}, {}),
    ({}, {"pr": 1235}),
    ({}, {"git_commit_sha": "d" * 40}),
    ({}, {"candidate_image_id": IMG_B}),
    ({}, {"rollback_image_id": IMG_A}),
])
def test_the_signed_target_must_equal_the_requested_target(target_change, args_change):
    with pytest.raises(RunnerRefused) as e:
        verify(make_doc(**target_change), args={**ARGS["promote"], **args_change})
    assert code_of(e) == "APPROVAL_BINDING_MISMATCH"


# --- measured binding ---------------------------------------------------------------------------

def _verified(action: str):
    return verify(make_doc(action), action=action)


@pytest.mark.parametrize("action,measured", [
    ("prepare_candidate", dict(head_sha=SHA, origin_main_sha=SHA, running_image_id=IMG_B)),
    ("validate_candidate", dict(head_sha=SHA, origin_main_sha=SHA, candidate_image_id=IMG_A, rollback_image_id=IMG_B)),
    ("promote", dict(head_sha=SHA, origin_main_sha=SHA, candidate_image_id=IMG_A, rollback_image_id=IMG_B)),
    ("rollback", dict(head_sha=None, origin_main_sha=None, running_image_id=IMG_A, candidate_image_id=IMG_A,
                      rollback_image_id=IMG_B)),
])
def test_measured_binding_passes_only_on_every_measurement_equal(action, measured):
    approval = _verified(action)
    av.check_measured_binding(approval, **measured)
    for key, value in measured.items():
        if value is None:
            continue
        broken = {**measured, key: None}
        with pytest.raises(RunnerRefused) as e:
            av.check_measured_binding(approval, **broken)
        assert code_of(e) == "APPROVAL_BINDING_MISMATCH", (action, key)


def test_a_tree_that_is_not_origin_main_does_not_bind():
    approval = _verified("promote")
    with pytest.raises(RunnerRefused):
        av.check_measured_binding(approval, head_sha=SHA, origin_main_sha="d" * 40,
                                  candidate_image_id=IMG_A, rollback_image_id=IMG_B)


# --- trust files --------------------------------------------------------------------------------

def _pub(blob_char: str = "A") -> tuple[str, str]:
    return ("ssh-ed25519", "AAAAC3NzaC1lZDI1NTE5AAAAI" + blob_char * 43)


def _line(key=None, opts='namespaces="deploy-approval.v1@thomas-agent"', principal=PRINCIPAL) -> str:
    key = key or _pub()
    return f"{principal} {opts} {key[0]} {key[1]} synthetic"


@pytest.mark.parametrize("line", [
    _line(opts='valid-after="20261016"'),
    _line(opts='namespaces="file"'),
    _line(opts='namespaces="deploy-approval.v1@thomas-agent,deploy-approval.v1@thomas-agent"'),
    _line(opts='cert-authority,namespaces="deploy-approval.v1@thomas-agent"'),
    _line(opts='namespaces="deploy-approval.v1@thomas-agent",namespaces="file"'),
    _line(opts='namespaces="deploy-approval.v1@thomas-agent",valid-before="tomorrow"'),
    _line(principal="someone-else"),
    _line(key=("ssh-rsa", "AAAAB3NzaC1yc2EAAAADAQAB" + "A" * 40)),
    "",
])
def test_allowed_signers_is_held_to_a_closed_format(line):
    with pytest.raises(RunnerRefused) as e:
        trust.parse_allowed_signers(line.encode("ascii"), principal=PRINCIPAL,
                                    permitted_namespaces=trust.KNOWN_NAMESPACES)
    assert code_of(e) == "TRUST_CONFIG_INSECURE"


def test_a_well_formed_allowed_signers_lists_its_keys():
    raw = ("# comment\n" + _line() + "\n" + _line(_pub("B"), 'namespaces="latch-resolution.v1@thomas-agent,'
                                                   'store-reinit.v1@thomas-agent",valid-after="20261016Z"') + "\n")
    keys = trust.parse_allowed_signers(raw.encode("ascii"), principal=PRINCIPAL,
                                       permitted_namespaces=trust.KNOWN_NAMESPACES)
    assert keys == {_pub(), _pub("B")}


@pytest.mark.parametrize("raw", [b"krl\x00binary", b"ssh-rsa AAAAB3 x\n", b"\xff"])
def test_revoked_keys_must_be_plain_ed25519_lines(raw):
    with pytest.raises(RunnerRefused) as e:
        trust.parse_revoked_keys(raw)
    assert code_of(e) == "TRUST_CONFIG_INSECURE"


def _write(path: Path, data: bytes, mode: int = 0o644) -> Path:
    path.write_bytes(data)
    path.chmod(mode)
    return path


@posix_only
def test_trust_files_load_and_hash_when_they_match_the_anchor(tmp_path):
    allowed = _write(tmp_path / "allowed", (_line() + "\n").encode())
    revoked = _write(tmp_path / "revoked", b"")
    material = trust.load_trust(allowed, revoked, owner_uid=os.getuid(), principal=PRINCIPAL,
                                anchor=frozenset({_pub()}))
    assert material.keys == {_pub()} and material.sha256.startswith("sha256:")
    _write(revoked, (" ".join(_pub("C")) + "\n").encode())
    assert trust.load_trust(allowed, revoked, owner_uid=os.getuid(), principal=PRINCIPAL,
                            anchor=frozenset({_pub()})).sha256 != material.sha256


@posix_only
def test_a_key_added_to_the_file_but_not_the_anchor_refuses_the_file(tmp_path):
    allowed = _write(tmp_path / "allowed", (_line() + "\n" + _line(_pub("Z")) + "\n").encode())
    revoked = _write(tmp_path / "revoked", b"")
    with pytest.raises(RunnerRefused) as e:
        trust.load_trust(allowed, revoked, owner_uid=os.getuid(), principal=PRINCIPAL, anchor=frozenset({_pub()}))
    assert code_of(e) == "TRUST_ANCHOR_MISMATCH"


@posix_only
def test_an_empty_anchor_is_never_accepted(tmp_path):
    allowed = _write(tmp_path / "allowed", (_line() + "\n").encode())
    revoked = _write(tmp_path / "revoked", b"")
    with pytest.raises(RunnerRefused) as e:
        trust.load_trust(allowed, revoked, owner_uid=os.getuid(), principal=PRINCIPAL, anchor=frozenset())
    assert code_of(e) == "TRUST_CONFIG_UNAVAILABLE"


@posix_only
@pytest.mark.parametrize("damage,code", [
    ("missing_revoked", "TRUST_CONFIG_UNAVAILABLE"),
    ("symlink", "TRUST_CONFIG_UNAVAILABLE"),
    ("hardlink", "TRUST_CONFIG_INSECURE"),
    ("group_writable", "TRUST_CONFIG_INSECURE"),
    ("other_owner", "TRUST_CONFIG_INSECURE"),
    ("fifo", "TRUST_CONFIG_INSECURE"),
    ("oversize", "TRUST_CONFIG_INSECURE"),
])
def test_a_trust_file_that_is_not_a_private_regular_file_is_refused(tmp_path, damage, code):
    real = _write(tmp_path / "real", (_line() + "\n").encode())
    allowed = tmp_path / "allowed"
    revoked = _write(tmp_path / "revoked", b"")
    owner = os.getuid()
    if damage == "missing_revoked":
        allowed = real
        revoked.unlink()
    elif damage == "symlink":
        allowed.symlink_to(real)
    elif damage == "hardlink":
        os.link(real, allowed)
    elif damage == "group_writable":
        allowed = _write(allowed, real.read_bytes(), 0o664)
    elif damage == "other_owner":
        allowed = real
        owner = os.getuid() + 1
    elif damage == "fifo":
        os.mkfifo(allowed)
    elif damage == "oversize":
        allowed = _write(allowed, b"# x\n" * (trust.MAX_TRUST_FILE_BYTES // 4 + 1))
    with pytest.raises(RunnerRefused) as e:
        trust.load_trust(allowed, revoked, owner_uid=owner, principal=PRINCIPAL, anchor=frozenset({_pub()}))
    assert code_of(e) == code


@posix_only
def test_the_revocation_list_reads_only_with_certainty(tmp_path):
    good = _write(tmp_path / "revoked_approvals", b"# withdrawn\ndap_" + b"b" * 26 + b"\n")
    assert trust.load_revoked_approvals(good, owner_uid=os.getuid()) == {"dap_" + "b" * 26}
    for name, data, mode in (("bad", b"not-an-id\n", 0o644), ("open", b"", 0o666)):
        path = _write(tmp_path / name, data, mode)
        with pytest.raises(RunnerRefused) as e:
            trust.load_revoked_approvals(path, owner_uid=os.getuid())
        assert code_of(e) == "REVOCATION_LIST_UNAVAILABLE"
    with pytest.raises(RunnerRefused) as e:
        trust.load_revoked_approvals(tmp_path / "absent", owner_uid=os.getuid())
    assert code_of(e) == "REVOCATION_LIST_UNAVAILABLE"


# --- ssh-keygen, end to end, with throwaway keys ------------------------------------------------

class Signer:
    def __init__(self, directory: Path, name: str) -> None:
        self.key = directory / name
        subprocess.run([SSH_KEYGEN, "-q", "-t", "ed25519", "-N", "", "-C", "synthetic", "-f", str(self.key)],
                       check=True, stdin=subprocess.DEVNULL, timeout=30)
        kind, blob = self.key.with_suffix(".pub").read_text().split()[:2]
        self.pub = (kind, blob)

    def sign(self, data: bytes, namespace: str) -> str:
        out = subprocess.run([SSH_KEYGEN, "-Y", "sign", "-q", "-f", str(self.key), "-n", namespace],
                             input=data, capture_output=True, check=True, timeout=30)
        return out.stdout.decode("ascii")


@pytest.fixture
def keys(tmp_path):
    return Signer(tmp_path, "thomas"), Signer(tmp_path, "attacker")


def _trust_for(tmp_path, lines: list[str], anchor, revoked: bytes = b""):
    allowed = _write(tmp_path / "allowed_signers", ("\n".join(lines) + "\n").encode())
    revoked_path = _write(tmp_path / "revoked_keys", revoked)
    return trust.load_trust(allowed, revoked_path, owner_uid=os.getuid(), principal=PRINCIPAL,
                            anchor=frozenset(anchor))


def _real_verifier():
    return av.SshKeygenVerifier(Path(SSH_KEYGEN).resolve(), PRINCIPAL,
                                exe_owner_uid=os.stat(Path(SSH_KEYGEN).resolve()).st_uid)


@needs_ssh
def test_a_thomas_signature_in_the_deploy_namespace_verifies(tmp_path, keys):
    thomas, _ = keys
    material = _trust_for(tmp_path, [_line(thomas.pub)], {thomas.pub})
    raw = raw_of(make_doc())
    result = verify(raw, verifier=_real_verifier(), trust_material=material,
                    signature=thomas.sign(raw, trust.DEPLOY_NAMESPACE))
    assert result.trust_config_sha256 == material.sha256


@needs_ssh
@pytest.mark.parametrize("case", ["other_namespace", "unknown_key", "restricted_key", "revoked_key", "tampered"])
def test_a_signature_that_should_not_count_does_not(tmp_path, keys, case):
    thomas, attacker = keys
    raw = raw_of(make_doc())
    lines, anchor, revoked, signer, ns, body = [_line(thomas.pub)], {thomas.pub}, b"", thomas, trust.DEPLOY_NAMESPACE, raw
    if case == "other_namespace":
        ns = trust.LATCH_RESOLUTION_NAMESPACE
    elif case == "unknown_key":
        signer = attacker
    elif case == "restricted_key":
        lines = [_line(thomas.pub, 'namespaces="latch-resolution.v1@thomas-agent"')]
    elif case == "revoked_key":
        revoked = (" ".join(thomas.pub) + "\n").encode()
    elif case == "tampered":
        body = raw_of(make_doc(pr=4321, candidate_tag="candidate-4321", rollback_tag="rollback-pre-4321"))
    material = _trust_for(tmp_path, lines, anchor, revoked)
    signature = signer.sign(raw, ns)
    with pytest.raises(RunnerRefused) as e:
        verify(body, verifier=_real_verifier(), trust_material=material, signature=signature,
               args={**ARGS["promote"], "pr": 4321} if case == "tampered" else None)
    assert code_of(e) == "SIGNATURE_INVALID"


@needs_ssh
def test_an_attacker_key_registered_beside_thomas_is_refused_before_any_signature(tmp_path, keys):
    """C2X-02 under T3: the attacker's key is in allowed_signers but not in the installed anchor."""
    thomas, attacker = keys
    with pytest.raises(RunnerRefused) as e:
        _trust_for(tmp_path, [_line(thomas.pub), _line(attacker.pub)], {thomas.pub})
    assert code_of(e) == "TRUST_ANCHOR_MISMATCH"


@posix_only
@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired(cmd="x", timeout=1), OSError("gone")])
def test_a_verifier_that_cannot_run_is_unavailable_never_a_pass(tmp_path, failure):
    exe = _write(tmp_path / "ssh-keygen", b"#!/bin/sh\n", 0o755)

    def run(*_, **__):
        raise failure

    verifier = av.SshKeygenVerifier(exe, PRINCIPAL, exe_owner_uid=os.getuid(), run=run)
    with pytest.raises(RunnerRefused) as e:
        verifier.verify(b"{}", FAKE_SIGNATURE, trust.DEPLOY_NAMESPACE, FAKE_TRUST)
    assert code_of(e) == "VERIFIER_UNAVAILABLE"


@posix_only
@pytest.mark.parametrize("setup", ["relative", "group_writable", "other_owner", "missing", "symlink"])
def test_the_verifier_executable_must_be_a_fixed_protected_file(tmp_path, setup):
    exe = _write(tmp_path / "ssh-keygen", b"#!/bin/sh\n", 0o755)
    owner = os.getuid()
    if setup == "relative":
        exe = Path("ssh-keygen")
    elif setup == "group_writable":
        exe.chmod(0o775)
    elif setup == "other_owner":
        owner += 1
    elif setup == "missing":
        exe = tmp_path / "absent"
    elif setup == "symlink":
        link = tmp_path / "link"
        link.symlink_to(exe)
        exe = link
    verifier = av.SshKeygenVerifier(exe, PRINCIPAL, exe_owner_uid=owner,
                                    run=lambda *a, **k: pytest.fail("must not run"))
    with pytest.raises(RunnerRefused) as e:
        verifier.verify(b"{}", FAKE_SIGNATURE, trust.DEPLOY_NAMESPACE, FAKE_TRUST)
    assert code_of(e) == "VERIFIER_UNAVAILABLE"


@posix_only
def test_the_verifier_runs_by_argv_with_no_output_kept_and_no_inherited_environment(tmp_path):
    exe = _write(tmp_path / "ssh-keygen", b"#!/bin/sh\n", 0o755)
    seen = {}

    def run(argv, **kwargs):
        seen.update(argv=argv, **kwargs)
        seen["files"] = {name: Path(kwargs["cwd"], name).read_bytes()
                         for name in ("allowed_signers", "revoked_keys", "signature")}
        seen["modes"] = {name: stat.S_IMODE(os.stat(Path(kwargs["cwd"], name)).st_mode)
                         for name in ("allowed_signers", "revoked_keys", "signature")}
        return subprocess.CompletedProcess(argv, 0)

    material = trust.TrustMaterial(allowed_signers=b"A", revoked_keys=b"R", keys=frozenset(), sha256="x")
    av.SshKeygenVerifier(exe, PRINCIPAL, exe_owner_uid=os.getuid(), run=run).verify(
        b"DOC", FAKE_SIGNATURE, trust.DEPLOY_NAMESPACE, material)
    argv = seen["argv"]
    assert argv[:3] == [str(exe), "-Y", "verify"] and argv[argv.index("-n") + 1] == trust.DEPLOY_NAMESPACE
    assert argv[argv.index("-I") + 1] == PRINCIPAL
    assert b"DOC" not in " ".join(argv).encode()
    assert seen["input"] == b"DOC"
    assert seen["stdout"] is subprocess.DEVNULL and seen["stderr"] is subprocess.DEVNULL
    assert seen["env"] == {"PATH": "/usr/bin:/bin", "LANG": "C"} and seen["check"] is False
    assert "shell" not in seen
    assert seen["files"] == {"allowed_signers": b"A", "revoked_keys": b"R", "signature": FAKE_SIGNATURE.encode()}
    assert set(seen["modes"].values()) == {0o600}
    assert not Path(seen["cwd"]).exists(), "the private copies are removed after the call"


# --- the package's boundary ---------------------------------------------------------------------

def _sources():
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(PACKAGE.glob("*.py"))}


def _code_strings(text: str) -> list[str]:
    """Every string constant in the code, leaving out docstrings (which describe, not do)."""
    tree = ast.parse(text)
    docstrings = set()
    for node in [tree, *ast.walk(tree)]:
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings]


def test_the_package_reads_no_env_file_and_drives_no_container():
    for name, text in _sources().items():
        for value in _code_strings(text):
            lowered = value.lower()
            assert ".env" not in value, (name, value)
            assert lowered != "docker" and not lowered.startswith("docker ") and not lowered.endswith("/docker") \
                and "docker.sock" not in lowered, (name, value)


def test_the_package_imports_nothing_that_defaults_to_the_state_root():
    forbidden = {"paths", "store", "jsonl", "filelock", "state_guard", "control", "scheduler",
                 "bridge_idempotency", "socket_door", "holdings", "crypto", "knowledge"}
    # C3a-3: the two sockets sit on the shared door transport, as the C2 design says (§0N). Only the
    # door and the gatekeeper may import it, and neither may ask it for a default path.
    transport = {"door.py", "gate.py"}
    for name, text in _sources().items():
        if name in transport:
            assert not any(isinstance(n, (ast.Attribute, ast.Name)) and "resolve_socket_path" in
                           (getattr(n, "attr", None), getattr(n, "id", None)) for n in ast.walk(ast.parse(text))), name
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                parts = set(module.split(".")) | {a.name for a in node.names}
                allowed = {"socket_door"} if name in transport else set()
                assert not parts & (forbidden - allowed), (name, module)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    assert not set(a.name.split(".")) & forbidden, (name, a.name)


def test_only_the_verifier_starts_a_process_and_never_through_a_shell():
    for name, text in _sources().items():
        tree = ast.parse(text)
        uses = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr in
                {"run", "Popen", "call", "check_output", "system", "popen"}
                and isinstance(n.value, ast.Name) and n.value.id in {"subprocess", "os"}]
        if name not in ("approval_verify.py", "commands.py"):  # ssh-keygen (C3a-1); the docker argv (C3a-3)
            assert not uses, name
        assert "shell=True" not in text, name


def test_a_sample_audit_row_satisfies_the_audit_schema():
    row = {"seq": 1, "event_id": "dae_" + "x" * 20, "recorded_at": stamp(NOW), "generation": 1,
           "request_id": "req-1", "action": "promote", "caller_uid": 2001, "client_id": None,
           "gate_intake_seq": 3, "approval_ref": {"approval_id": "dap_" + "a" * 26,
                                                  "approval_sha256": "sha256:" + "1" * 64,
                                                  "signer_principal": PRINCIPAL},
           "trust_config_sha256": "sha256:" + "2" * 64, "signer_key_fingerprint": None,
           "target": {"pr": 1234, "git_commit_sha": SHA, "candidate_image_id": IMG_A, "rollback_image_id": IMG_B},
           "validation_result": "REFUSED", "reason_code": "OPERATION_BLOCKED_BY_POLICY",
           "execution_state": "REFUSED", "runner_install_sha": SHA, "latch_id": None, "store_id": "0" * 32,
           "consumption_seq": None, "consumption_row_sha256": None, "previous_event_sha256": None,
           "event_sha256": "sha256:" + "3" * 64}
    schema = SCHEMAS / "deploy_audit.v0.1.schema.json"
    schema_cache.validate_against_schema(row, schema, "audit")
    for bad in ({"execution_state": "LATCH_CLEARED"}, {"reason_code": "made-up"}, {"extra": 1}):
        with pytest.raises(RuntimeSchemaError):
            schema_cache.validate_against_schema({**row, **bad}, schema, "audit")
