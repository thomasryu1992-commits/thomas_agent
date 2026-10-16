"""The runner's closed reason-code table: one failure, one code, one fixed sentence.

Two sets, kept apart on purpose:

- ``RESPONSE_TEXT`` — codes that may leave the runner in a reply. Each has exactly one sentence,
  and that sentence is all the reply ever says about the failure: no exception message, no path,
  no Docker or ``ssh-keygen`` output, no value from the request.
- ``AUDIT_TEXT`` — codes that only ever reach the runner's own audit. They describe states that
  show to a caller as ``RUNNER_STARTUP_BLOCKED`` or ``RUNNER_LATCHED``, or recovery-inbox
  outcomes that have no reply path at all.

A code that is in neither set, or an audit-only code that reaches the reply builder, is answered
as ``RUNNER_ERROR``. Failing to the generic code is the safe direction: it says less, never more.
"""

from __future__ import annotations

import re
from types import MappingProxyType
from typing import Any, Mapping

from ..errors import MvpRuntimeError

GENERIC_CODE = "RUNNER_ERROR"

RESPONSE_TEXT: Mapping[str, str] = MappingProxyType({
    # transport and request shape
    "MALFORMED_REQUEST": "the request is not a well-formed frame",
    "FRAME_TOO_LARGE": "the request is larger than the runner accepts",
    "PEER_NOT_PERMITTED": "this caller is not permitted at this socket",
    "CREDENTIALS_UNAVAILABLE": "the caller's credentials could not be read",
    "VERB_NOT_PERMITTED": "this action is not one the runner performs",
    "ARGUMENT_NOT_ACCEPTED": "the request carries a field this action does not accept",
    "ARGUMENT_INVALID": "a request field is not in the accepted form",
    "REQUEST_ID_REQUIRED": "this action needs a request_id",
    "REQUEST_ID_REUSED": "this request_id was already used for a different request",
    "REQUEST_IN_FLIGHT": "this request is still being carried out",
    "RUNNER_BUSY": "another operational action is in progress",
    "INSPECT_RATE_LIMITED": "inspection requests are arriving faster than permitted",
    # gatekeeper and actor
    "ACTOR_UNAVAILABLE": "the privileged actor could not be reached",
    "ACTOR_REPLY_INVALID": "the privileged actor's answer was not in the accepted form",
    "ACTOR_PEER_NOT_PERMITTED": "this caller is not permitted at the actor socket",
    # approval document and signature
    "APPROVAL_REQUIRED": "this action needs a signed approval",
    "APPROVAL_MALFORMED": "the approval is not in the accepted form",
    "APPROVAL_FIELD_MISSING": "the approval is missing a required field",
    "APPROVAL_UNKNOWN_FIELD": "the approval carries a field the schema does not define",
    "APPROVAL_SCHEMA_UNSUPPORTED": "the approval's schema version is not supported",
    "APPROVAL_NOT_CANONICAL": "the approval's bytes are not in canonical form",
    "SIGNATURE_INVALID": "the approval's signature did not verify",
    "VERIFIER_UNAVAILABLE": "the signature verifier could not be run",
    "TRUST_CONFIG_UNAVAILABLE": "the signer trust configuration could not be read",
    "TRUST_CONFIG_INSECURE": "the signer trust configuration is not in a safe state",
    "TRUST_ANCHOR_MISMATCH": "the signer trust configuration does not match the installed anchor",
    "TRUST_CONFIG_UNAUTHORIZED_CHANGE": "the signer trust configuration changed without authorisation",
    # approval meaning
    "APPROVAL_NOT_YET_VALID": "the approval is not valid yet",
    "APPROVAL_EXPIRED": "the approval has expired",
    "APPROVAL_LIFETIME_EXCEEDS_POLICY": "the approval's lifetime exceeds the permitted maximum",
    "APPROVAL_TOO_SHORT_FOR_ACTION": "the approval expires before this action could finish",
    "APPROVAL_BEFORE_CUTOFF": "the approval was issued before the current consumption cutoff",
    "APPROVAL_ACTION_MISMATCH": "the approval is for a different action",
    "APPROVAL_ENVIRONMENT_MISMATCH": "the approval is for a different environment",
    "APPROVAL_BINDING_MISMATCH": "the approval does not match the requested or measured target",
    "APPROVAL_REVOKED": "the approval has been revoked",
    "REVOCATION_LIST_UNAVAILABLE": "the revocation list could not be read",
    "APPROVAL_ALREADY_CONSUMED": "the approval has already been used",
    "CONSUMPTION_STORE_UNAVAILABLE": "the consumption record could not be read",
    "CONSUMPTION_WRITE_FAILED": "the consumption record could not be written",
    "CLOCK_UNTRUSTED": "the host clock cannot be trusted for this action",
    # measurement
    "TREE_UNAVAILABLE": "the runner's own source tree could not be read",
    "TREE_NOT_CLEAN": "the runner's own source tree has local changes",
    "TREE_NOT_ORIGIN_MAIN": "the runner's own source tree is not at the approved commit",
    "ORIGIN_FETCH_FAILED": "the runner could not fetch the main branch",
    "IMAGE_STATE_UNAVAILABLE": "the image state could not be measured",
    "RUNNING_IMAGES_INCONSISTENT": "the running services are not on one image",
    "TAG_ALREADY_EXISTS": "a tag this action would create already exists",
    "ROLLBACK_POINT_MISSING": "the rollback point does not exist",
    # policy
    "OPERATION_BLOCKED_BY_POLICY": "this action is blocked by the current operating policy",
    # execution
    "DOCKER_COMMAND_FAILED": "a container command failed",
    "DOCKER_TIMEOUT": "a container command did not finish in time",
    "BUILD_FAILED": "the image build failed",
    "DEADLINE_EXCEEDED": "the action did not finish before its deadline",
    "POST_CHECK_FAILED": "the post-action check did not pass",
    "FAILED_EFFECT_PARTIAL": "the action failed after part of its effect took place",
    "EFFECT_STATE_UNKNOWN": "whether the action took effect cannot be determined",
    # runner state
    "RUNNER_STARTUP_BLOCKED": "the runner is not accepting requests until its records are resolved",
    "RUNNER_LATCHED": "the runner is latched and accepts no requests until an operator resolves it",
    "INSTANCE_LOCK_HELD": "another runner instance holds the instance lock",
    "RUNNER_ENVIRONMENT_MISMATCH": "the runner's installed environment does not match its expectation",
    # audit and internal
    "AUDIT_WRITE_FAILED": "the audit record could not be written",
    "REPLY_SCHEMA_INVALID": "the runner could not form a valid reply",
    GENERIC_CODE: "the runner could not complete this request",
})

AUDIT_TEXT: Mapping[str, str] = MappingProxyType({
    "STORE_GENESIS_MISSING": "the consumption store has no genesis record",
    "STORE_ID_MISMATCH": "the consumption store does not match the recorded store identity",
    "STORE_CHAIN_BROKEN": "the consumption store's hash chain is broken",
    "AUDIT_CHAIN_BROKEN": "the audit hash chain is broken",
    "RECONCILIATION_MISMATCH": "the consumption store and the audit disagree",
    "RECONCILED_UNKNOWN": "a consumed request has no terminal record",
    "RECOVERY_DOCUMENT_INVALID": "a recovery document did not verify",
    "RECOVERY_DOCUMENT_REPLAYED": "a recovery document was already used",
    "RECOVERY_EVIDENCE_MISMATCH": "a recovery document's evidence does not match the measurement",
    "RECOVERY_INBOX_UNSAFE": "the recovery inbox is not in a safe state",
    "REINIT_EVIDENCE_INSUFFICIENT": "store reinitialisation lacks the required evidence",
    "REINIT_ALREADY_PERFORMED": "this store was already reinitialised",
})

assert not set(RESPONSE_TEXT) & set(AUDIT_TEXT), "a code is either a reply code or audit-only, never both"

ALL_CODES = frozenset(RESPONSE_TEXT) | frozenset(AUDIT_TEXT)

_ECHO_TOKEN = re.compile(r"^[A-Za-z0-9._:-]{1,128}\Z")
_ERROR_REF = re.compile(r"^[A-Za-z0-9-]{1,32}\Z")


class RunnerRefused(MvpRuntimeError):
    """A refusal carrying a code from the closed table and that code's fixed sentence only.

    The message is never built from the failure: a caller that wants to say why something failed
    says it with the code. An unknown code becomes ``RUNNER_ERROR``."""

    def __init__(self, reason_code: str) -> None:
        code = reason_code if reason_code in ALL_CODES else GENERIC_CODE
        super().__init__(code, RESPONSE_TEXT.get(code) or AUDIT_TEXT[code])


def response_code(code: Any) -> str:
    """The code a reply may carry for ``code``: itself when it is a reply code, otherwise the generic one."""
    return code if isinstance(code, str) and code in RESPONSE_TEXT else GENERIC_CODE


def refusal_reply(code: Any, *, request_id: Any = None, error_ref: Any = None) -> dict[str, Any]:
    """The only shape a refusal reply takes: ``{ok, reason_code, reason}`` plus a well-formed echo.

    ``request_id`` is echoed only when it is a plain token; ``error_ref`` only on the generic code."""
    rc = response_code(code)
    reply: dict[str, Any] = {"ok": False, "reason_code": rc, "reason": RESPONSE_TEXT[rc]}
    if isinstance(request_id, str) and _ECHO_TOKEN.match(request_id):
        reply["request_id"] = request_id
    if rc == GENERIC_CODE and isinstance(error_ref, str) and _ERROR_REF.match(error_ref):
        reply["error_ref"] = error_ref
    return reply
