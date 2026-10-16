"""Verifying a deploy approval: signature first, then meaning, never the other way round.

The order is the C2 contract (specification §2.4, steps 3–8 and 10):

1. the request arguments are in the closed shape for the action (no paths, no free text);
2. the carried document and signature are present and in their accepted forms;
3. ``ssh-keygen -Y verify`` passes on the document's bytes as they arrived, in the deploy namespace,
   against the checked trust material — nothing in the unsigned bytes chooses this path;
4. only then are the bytes parsed, held to the closed schema and to canonical form;
5. action, environment, time window and revocation;
6. the signed target equals the requested target; the measured target is compared by the caller
   with ``check_measured_binding`` just before any effect.

Consumption (the one-time use of ``approval_id`` and ``nonce``) is not here: it belongs to the
consumption store, which comes after every check above has passed.
"""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from runtime.read_only_kernel import integrity
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from .. import schema_cache
from .canonical import check_signature_text, decode_document_b64, parse_strict, require_canonical
from .clock import TimePolicy, check_approval_window, parse_utc_second
from .reasons import RunnerRefused
from .trust import DEPLOY_NAMESPACE, TrustMaterial, protected_files_supported

SCHEMA_ID = "thomas.deploy_approval.v1"
SCHEMA_FILE = "deploy_approval.v0.1.schema.json"
SCHEMAS_DIR = Path(__file__).resolve().parents[3] / "schemas"

APPROVED_ACTIONS = ("prepare_candidate", "validate_candidate", "promote", "rollback")

_TOP_KEYS = frozenset({"schema", "approval_id", "nonce", "action", "environment", "target",
                       "issued_at", "expires_at", "signature_namespace"})
_ENVIRONMENT_KEYS = frozenset({"project", "host_label", "profile"})
_TARGET_KEYS = frozenset({"pr", "git_commit_sha", "candidate_tag", "candidate_image_id",
                          "rollback_tag", "rollback_image_id"})

# The request arguments each action accepts (specification §6.3–6.6). A key outside the set is
# ARGUMENT_NOT_ACCEPTED; a missing one is ARGUMENT_INVALID.
ARGS_BY_ACTION: Mapping[str, frozenset[str]] = {
    "inspect_candidate": frozenset({"pr"}),
    "prepare_candidate": frozenset({"pr", "git_commit_sha"}),
    "validate_candidate": frozenset({"pr", "git_commit_sha", "candidate_image_id"}),
    "promote": frozenset({"pr", "git_commit_sha", "candidate_image_id", "rollback_image_id"}),
    "rollback": frozenset({"pr", "candidate_image_id", "rollback_image_id"}),
}

_SHA = frozenset("0123456789abcdef")


def _is_sha(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 40 and set(value) <= _SHA


def _is_image_id(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 71 and value.startswith("sha256:")
            and set(value[7:]) <= _SHA)


def _is_pr(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 999_999


def check_args(action: Any, args: Any) -> dict[str, Any]:
    """The request arguments for ``action``, held to their closed set and formats."""
    if not isinstance(action, str) or action not in ARGS_BY_ACTION:
        raise RunnerRefused("VERB_NOT_PERMITTED")
    if not isinstance(args, dict):
        raise RunnerRefused("ARGUMENT_INVALID")
    allowed = ARGS_BY_ACTION[action]
    if set(args) - allowed:
        raise RunnerRefused("ARGUMENT_NOT_ACCEPTED")
    if set(args) != allowed:
        raise RunnerRefused("ARGUMENT_INVALID")
    if not _is_pr(args["pr"]):
        raise RunnerRefused("ARGUMENT_INVALID")
    if "git_commit_sha" in args and not _is_sha(args["git_commit_sha"]):
        raise RunnerRefused("ARGUMENT_INVALID")
    for key in ("candidate_image_id", "rollback_image_id"):
        if key in args and not _is_image_id(args[key]):
            raise RunnerRefused("ARGUMENT_INVALID")
    return dict(args)


@dataclass(frozen=True)
class Environment:
    """The environment this runner serves, from its own root-owned configuration."""

    project: str
    host_label: str
    profile: str


@dataclass(frozen=True)
class VerifiedApproval:
    approval_id: str
    nonce: str
    action: str
    approval_sha256: str
    trust_config_sha256: str
    target: Mapping[str, Any]
    issued_at: datetime
    expires_at: datetime


Runner = Callable[..., Any]


class SshKeygenVerifier:
    """``ssh-keygen -Y verify`` by argv, on a private copy of the trust material.

    Only the exit status is read; stdout and stderr go nowhere. The executable must be an absolute
    path to a regular file owned by ``exe_owner_uid`` (root by default) that nobody else can write;
    it is never looked up on ``PATH``."""

    def __init__(self, ssh_keygen: Path, principal: str, *, exe_owner_uid: int = 0, timeout_s: float = 10.0,
                 run: Runner = subprocess.run) -> None:
        self._exe = Path(ssh_keygen)
        self._principal = principal
        self._exe_owner_uid = exe_owner_uid
        self._timeout_s = timeout_s
        self._run = run

    def _check_executable(self) -> None:
        if not self._exe.is_absolute() or not protected_files_supported():
            raise RunnerRefused("VERIFIER_UNAVAILABLE")
        try:
            st = os.lstat(self._exe)
        except OSError:
            raise RunnerRefused("VERIFIER_UNAVAILABLE") from None
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != self._exe_owner_uid
                or st.st_mode & (stat.S_IWGRP | stat.S_IWOTH) or not st.st_mode & stat.S_IXUSR):
            raise RunnerRefused("VERIFIER_UNAVAILABLE")

    def verify(self, document: bytes, signature: str, namespace: str, trust: TrustMaterial) -> None:
        self._check_executable()
        with tempfile.TemporaryDirectory(prefix="deploy-verify-") as work:
            paths = {}
            for name, data in (("allowed_signers", trust.allowed_signers),
                               ("revoked_keys", trust.revoked_keys),
                               ("signature", signature.encode("ascii"))):
                path = os.path.join(work, name)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0), 0o600)
                try:
                    os.write(fd, data)
                finally:
                    os.close(fd)
                paths[name] = path
            argv = [str(self._exe), "-Y", "verify", "-f", paths["allowed_signers"], "-I", self._principal,
                    "-n", namespace, "-s", paths["signature"], "-r", paths["revoked_keys"]]
            try:
                result = self._run(argv, input=document, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   timeout=self._timeout_s, cwd=work, env={"PATH": "/usr/bin:/bin", "LANG": "C"},
                                   check=False)
            except (subprocess.TimeoutExpired, OSError):
                raise RunnerRefused("VERIFIER_UNAVAILABLE") from None
        if getattr(result, "returncode", None) != 0:
            raise RunnerRefused("SIGNATURE_INVALID")


def _check_structure(document: dict[str, Any]) -> None:
    """Missing, unknown and unsupported, told apart before the schema's format checks."""
    if "schema" not in document:
        raise RunnerRefused("APPROVAL_FIELD_MISSING")
    if document["schema"] != SCHEMA_ID:
        raise RunnerRefused("APPROVAL_SCHEMA_UNSUPPORTED")
    for keys, value in ((_TOP_KEYS, document), (_ENVIRONMENT_KEYS, document.get("environment")),
                        (_TARGET_KEYS, document.get("target"))):
        if not isinstance(value, dict):
            raise RunnerRefused("APPROVAL_FIELD_MISSING" if value is None else "APPROVAL_MALFORMED")
        if set(value) - keys:
            raise RunnerRefused("APPROVAL_UNKNOWN_FIELD")
        if keys - set(value):
            raise RunnerRefused("APPROVAL_FIELD_MISSING")
    try:
        schema_cache.validate_against_schema(document, SCHEMAS_DIR / SCHEMA_FILE, "deploy approval")
    except RuntimeSchemaError:
        raise RunnerRefused("APPROVAL_MALFORMED") from None


def _check_requested_binding(action: str, args: Mapping[str, Any], target: Mapping[str, Any]) -> None:
    pr = target["pr"]
    if target["candidate_tag"] != f"candidate-{pr}" or target["rollback_tag"] != f"rollback-pre-{pr}":
        raise RunnerRefused("APPROVAL_BINDING_MISMATCH")
    for key, value in args.items():
        if target.get(key) != value:
            raise RunnerRefused("APPROVAL_BINDING_MISMATCH")


def verify_deploy_approval(*, action: str, args: Any, document_b64: Any, signature: Any,
                           environment: Environment, trust: TrustMaterial, verifier: SshKeygenVerifier,
                           policy: TimePolicy, now: datetime, revoked_approvals: frozenset[str],
                           cutoff_at: datetime | None = None) -> VerifiedApproval:
    """Verify one deploy approval for ``action`` and ``args``, or raise ``RunnerRefused``."""
    if action not in APPROVED_ACTIONS:
        raise RunnerRefused("ARGUMENT_NOT_ACCEPTED" if action in ARGS_BY_ACTION else "VERB_NOT_PERMITTED")
    request = check_args(action, args)
    if document_b64 is None or signature is None:
        raise RunnerRefused("APPROVAL_REQUIRED")
    raw = decode_document_b64(document_b64)
    sig = check_signature_text(signature)

    verifier.verify(raw, sig, DEPLOY_NAMESPACE, trust)

    document = parse_strict(raw)
    _check_structure(document)
    require_canonical(raw, document)

    if document["action"] != action:
        raise RunnerRefused("APPROVAL_ACTION_MISMATCH")
    env = document["environment"]
    if (env["project"], env["host_label"], env["profile"]) != (
            environment.project, environment.host_label, environment.profile):
        raise RunnerRefused("APPROVAL_ENVIRONMENT_MISMATCH")
    issued_at = parse_utc_second(document["issued_at"])
    expires_at = parse_utc_second(document["expires_at"])
    check_approval_window(policy, action=action, issued_at=issued_at, expires_at=expires_at,
                          now=now, cutoff_at=cutoff_at)
    if document["approval_id"] in revoked_approvals:
        raise RunnerRefused("APPROVAL_REVOKED")
    _check_requested_binding(action, request, document["target"])

    return VerifiedApproval(
        approval_id=document["approval_id"], nonce=document["nonce"], action=action,
        approval_sha256=integrity.sha256_bytes(raw), trust_config_sha256=trust.sha256,
        target=dict(document["target"]), issued_at=issued_at, expires_at=expires_at,
    )


def check_measured_binding(approval: VerifiedApproval, *, head_sha: str | None, origin_main_sha: str | None,
                           running_image_id: str | None = None, candidate_image_id: str | None = None,
                           rollback_image_id: str | None = None) -> None:
    """The signed target against what the actor measured itself, just before any effect.

    Every measurement the action depends on must be present and equal; a missing measurement is a
    mismatch, never a pass. What is compared per action follows specification §1.1."""
    target = approval.target
    action = approval.action
    if action != "rollback":
        if not head_sha or head_sha != origin_main_sha or head_sha != target["git_commit_sha"]:
            raise RunnerRefused("APPROVAL_BINDING_MISMATCH")
    if action == "prepare_candidate":
        if not running_image_id or running_image_id != target["rollback_image_id"]:
            raise RunnerRefused("APPROVAL_BINDING_MISMATCH")
        return
    if not candidate_image_id or candidate_image_id != target["candidate_image_id"]:
        raise RunnerRefused("APPROVAL_BINDING_MISMATCH")
    if action in ("promote", "rollback", "validate_candidate"):
        if not rollback_image_id or rollback_image_id != target["rollback_image_id"]:
            raise RunnerRefused("APPROVAL_BINDING_MISMATCH")
    if action == "rollback" and running_image_id != target["candidate_image_id"]:
        raise RunnerRefused("APPROVAL_BINDING_MISMATCH")
