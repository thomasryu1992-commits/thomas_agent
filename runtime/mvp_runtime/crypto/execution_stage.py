"""The machine's one execution stage (PR1a — Thomas decisions 1, 4, 8, 9, 2026-09-15).

Until this record existed, "what stage is this machine at" had no owner. The environment
(``MVP_LIVE_TRADING=real``) authorized the order adapter, eight durable records could refuse an
entry, and the readiness board re-derived an answer from all of them on every read — which is how
it could read all-PASS with no strategy armed (execution-authority audit, 2026-09-15). This module
is the record that answers the question, the verified read, and the transition rules:

    READ_ONLY -> SHADOW -> PAPER -> SIGNED_TESTNET -> LIVE_AUTONOMOUS -> LIVE_SCALED

There is no canary rung (Thomas 2026-07-29: no further canaries are placed) and no stage expires
(Thomas 2026-07-28 / 2026-08-10 retired every renewal on the money path: an expiry that lands on
an open position is the failure, not the safeguard). A stage stands until it is demoted or the
policy it was approved under changes.

- **BOOTSTRAP** — the machine has no binding stage above READ_ONLY (no record, a demotion to
  READ_ONLY, or a record that does not bind for a reason other than a policy change): a record at
  SHADOW or PAPER, approved once, carrying an attestation of the evidence behind it (decision 1:
  this host starts at PAPER). A record it replaces is named in the ask.
- **CLIMB** — exactly one rung up from a binding record, approved once. No skip, ever.
  SIGNED_TESTNET -> LIVE_AUTONOMOUS needs a reconciled signed testnet order (decision 2) and is
  refused until that path exists (PR1d); LIVE_SCALED has no entry rule yet.
- **REBIND** — the same rung again, approved once, and only for a record whose sole defect is that
  the policy version or its safety semantic fingerprint changed (decision 4).
- **DEMOTE** — any rung down, no approval, immediate (decision 8). A demotion keeps the approval
  it descends from as its witness and the policy identity that approval bound, so it can lower a
  stage but never mint one. To READ_ONLY it needs no witness at all and works from any state,
  including a record that cannot be read.

A record that is missing, unreadable, tampered, schema-invalid, not yet effective, for another
venue, internally inconsistent, not witnessed by the Thomas-verified CONSUMED approval whose
content it carries, or bound to a different policy version or safety fingerprint reads as
**READ_ONLY** (decision 9) — with the reason, never an exception.

**The witness and its limit.** The self-hash proves a file was not edited after it was written; it
cannot prove the door wrote it, because anyone who can write the state directory can recompute it
(audit FO-12). The approval ledger is the second witness, checked for a coherent verified
lifecycle: CONSUMED, decided by Thomas on the verified channel, a snapshot that still fingerprints
to the bound value, and content that equals the record. The ledger lives in the same state
directory and carries no secret, so a writer able to forge that whole lifecycle can still forge a
stage — and one who kept a copy of an earlier witnessed record can put it back, undoing a demotion.
What the witness removes is the one-hash forgery. The state directory is writable only by the
service uid, which could already place orders directly.

**What the stage gates (PR1b):** new exposure only. The entry guard
(``live_order.evaluate_live_order_guard``, one chokepoint for the autonomous leg and the slippage
probe) refuses below the rung a purpose needs, and the readiness board reports the same answer. The
close guard, settlement, the protection re-check, the time exit and reconciliation never read it —
a demotion must never trap an open position, which is why the stage is passed to the entry guard
rather than to the adapter that selects a venue.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterator, Mapping

from runtime.read_only_kernel import integrity
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from ..errors import ToolError
from ..filelock import locked
from ..paths import repo_root as _repo_root
from ..policy_fingerprint import policy_safety_identity
from ..schema_cache import validate_against_schema
from .state import state_dir

EXECUTION_STAGE_SCHEMA_VERSION = "execution_stage.v0.1"
EXECUTION_STAGE_SCHEMA_FILE = "execution_stage.v0.1.schema.json"
EXECUTION_STAGE_FILENAME = "execution_stage.json"
EXECUTION_STAGE_LOCK_FILENAME = "execution_stage.lock"
TRANSITION_EVENT_TYPE = "execution_stage_transition.v0"
SUPPORTED_VENUE = "binance_futures"
# Whether the entry doors read the stage. False in PR1a, when the record was kept and reported
# only; PR1b flipped it in the same change that made `evaluate_live_order_guard` require a
# `StageStatus`, so no text can claim enforcement that does not exist (or deny enforcement that
# does). What reads it: the entry guard (autonomous leg and slippage probe) and the readiness
# board's row and dry-run. What does not, and must not: the close guard, settlement, protection,
# the time exit and reconciliation.
STAGE_ENFORCED = True


class ExecutionStage(str, Enum):
    READ_ONLY = "READ_ONLY"
    SHADOW = "SHADOW"
    PAPER = "PAPER"
    SIGNED_TESTNET = "SIGNED_TESTNET"
    LIVE_AUTONOMOUS = "LIVE_AUTONOMOUS"
    LIVE_SCALED = "LIVE_SCALED"


LADDER: tuple[str, ...] = tuple(s.value for s in ExecutionStage)
BOOTSTRAP_STAGES = frozenset({ExecutionStage.SHADOW.value, ExecutionStage.PAPER.value})
LIVE_STAGES = frozenset({ExecutionStage.LIVE_AUTONOMOUS.value, ExecutionStage.LIVE_SCALED.value})

T_BOOTSTRAP = "BOOTSTRAP"
T_CLIMB = "CLIMB"
T_REBIND = "REBIND"
T_DEMOTE = "DEMOTE"

# What a door's purpose needs (PR1b reads these; recorded here so the ladder has one owner).
PURPOSE_PROBE = "probe"
PURPOSE_AUTONOMOUS = "autonomous"
PURPOSE_LIVE_ARM = "live_arm"
_REQUIRED_STAGE = {
    PURPOSE_PROBE: ExecutionStage.LIVE_AUTONOMOUS.value,
    PURPOSE_AUTONOMOUS: ExecutionStage.LIVE_AUTONOMOUS.value,
    PURPOSE_LIVE_ARM: ExecutionStage.LIVE_AUTONOMOUS.value,
}

# Why a record reads as READ_ONLY.
STAGE_RECORD_MISSING = "EXECUTION_STAGE_RECORD_MISSING"
STAGE_RECORD_UNREADABLE = "EXECUTION_STAGE_RECORD_UNREADABLE"
STAGE_RECORD_TAMPERED = "EXECUTION_STAGE_RECORD_TAMPERED"
STAGE_RECORD_INVALID = "EXECUTION_STAGE_RECORD_INVALID"
STAGE_NOT_YET_EFFECTIVE = "EXECUTION_STAGE_NOT_YET_EFFECTIVE"
STAGE_VENUE_MISMATCH = "EXECUTION_STAGE_VENUE_MISMATCH"
STAGE_TRANSITION_INVALID = "EXECUTION_STAGE_TRANSITION_INVALID"
STAGE_POLICY_UNREADABLE = "EXECUTION_STAGE_POLICY_UNREADABLE"
STAGE_POLICY_VERSION_CHANGED = "EXECUTION_STAGE_POLICY_VERSION_CHANGED"
STAGE_POLICY_SAFETY_CHANGED = "EXECUTION_STAGE_POLICY_SAFETY_CHANGED"
STAGE_APPROVAL_NOT_CONSUMED = "EXECUTION_STAGE_APPROVAL_NOT_CONSUMED"
STAGE_APPROVAL_UNREADABLE = "EXECUTION_STAGE_APPROVAL_UNREADABLE"
STAGE_WITNESS_MISMATCH = "EXECUTION_STAGE_WITNESS_MISMATCH"
# The only defects a REBIND may cure: the record was witnessed, and the policy moved under it.
REBINDABLE_REASONS = frozenset({STAGE_POLICY_VERSION_CHANGED, STAGE_POLICY_SAFETY_CHANGED})

# Why the registration door refuses a transition.
STAGE_SKIP_REFUSED = "EXECUTION_STAGE_SKIP_REFUSED"
STAGE_USE_DEMOTE = "EXECUTION_STAGE_USE_DEMOTE"
STAGE_ALREADY_BINDS = "EXECUTION_STAGE_ALREADY_BINDS"
STAGE_NOT_LOWER = "EXECUTION_STAGE_NOT_LOWER"
STAGE_NOTHING_TO_DEMOTE = "EXECUTION_STAGE_NOTHING_TO_DEMOTE"
STAGE_DEMOTE_FROM_UNBOUND = "EXECUTION_STAGE_DEMOTE_FROM_UNBOUND_RECORD"
STAGE_BOOTSTRAP_ONLY_SHADOW_OR_PAPER = "EXECUTION_STAGE_BOOTSTRAP_ONLY_SHADOW_OR_PAPER"
STAGE_REBIND_FIRST = "EXECUTION_STAGE_REBIND_FIRST"
STAGE_ATTESTATION_REQUIRED = "EXECUTION_STAGE_ATTESTATION_REQUIRED"
STAGE_SIGNED_TESTNET_EVIDENCE_REQUIRED = "EXECUTION_STAGE_SIGNED_TESTNET_EVIDENCE_REQUIRED"
STAGE_NOT_DEFINED = "EXECUTION_STAGE_NOT_DEFINED"
STAGE_CHANGED = "EXECUTION_STAGE_CHANGED"
STAGE_POLICY_CHANGED_SINCE_ASK = "EXECUTION_STAGE_POLICY_CHANGED_SINCE_ASK"
STAGE_LOCK_FAILED = "EXECUTION_STAGE_LOCK_FAILED"
STAGE_WRITE_FAILED_AFTER_SPEND = "EXECUTION_STAGE_WRITE_FAILED_AFTER_SPEND"


def rank(stage: str) -> int:
    """Position on the ladder. An unknown stage has no position and raises."""
    return LADDER.index(stage)


def required_stage(purpose: str) -> str:
    """The lowest stage at which a door opened for ``purpose`` may create new exposure."""
    return _REQUIRED_STAGE[purpose]


def stage_path(root: Path | None = None) -> Path:
    return state_dir(root) / EXECUTION_STAGE_FILENAME


@contextmanager
def stage_lock(root: Path | None = None) -> Iterator[None]:
    """One writer of the stage record at a time: a --confirm and a --demote that interleave would
    otherwise let the climb land on top of the stop (decision 8's stop must never be lost)."""
    with locked(state_dir(root) / EXECUTION_STAGE_LOCK_FILENAME, code=STAGE_LOCK_FAILED,
                label="the execution stage record"):
        yield


@dataclass(frozen=True)
class StageStatus:
    """What the machine's stage IS, right now — the one fact every consumer reads.

    ``stage`` is the effective stage: the recorded stage when the record is valid, READ_ONLY
    otherwise. ``recorded_stage`` is what the file says, kept apart so the door can name the rung a
    record that no longer binds was at. ``record_present`` separates "no file" from "a file that
    cannot be read" (the latter is an incident a replacing ask must show)."""

    stage: str
    valid: bool
    reason_code: str | None
    recorded_stage: str | None = None
    stage_id: str | None = None
    record_sha256: str | None = None
    policy_version: str | None = None
    record_present: bool = False
    # The witness a demotion carries forward (the approval the binding record descends from).
    approval_id: str | None = None
    action_fingerprint: str | None = None
    witness_stage_id: str | None = None
    policy_safety_sha256: str | None = None

    def allows(self, purpose: str) -> bool:
        return self.valid and rank(self.stage) >= rank(required_stage(purpose))

    @property
    def binding(self) -> bool:
        """A stage above READ_ONLY that binds — what a CLIMB or a witnessed DEMOTE starts from."""
        return self.valid and self.stage != ExecutionStage.READ_ONLY.value

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage, "valid": self.valid, "reason_code": self.reason_code,
            "recorded_stage": self.recorded_stage, "stage_id": self.stage_id,
            "record_sha256": self.record_sha256, "policy_version": self.policy_version,
            "approval_id": self.approval_id,
        }


def _from_record(record: Mapping[str, Any], *, valid: bool, reason: str | None) -> StageStatus:
    recorded = record.get("stage") if record.get("stage") in LADDER else None
    return StageStatus(
        stage=recorded if (valid and recorded) else ExecutionStage.READ_ONLY.value,
        valid=valid, reason_code=reason, recorded_stage=recorded,
        stage_id=_str_or_none(record.get("stage_id")), record_sha256=_str_or_none(record.get("record_sha256")),
        policy_version=_str_or_none(record.get("policy_version")), record_present=True,
        approval_id=_str_or_none(record.get("approval_id")),
        action_fingerprint=_str_or_none(record.get("action_fingerprint")),
        witness_stage_id=_str_or_none(record.get("witness_stage_id")),
        policy_safety_sha256=_str_or_none(record.get("policy_safety_sha256")),
    )


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _read_only(reason: str, record: Mapping[str, Any] | None = None, *, present: bool = True) -> StageStatus:
    if record is None:
        return StageStatus(stage=ExecutionStage.READ_ONLY.value, valid=False, reason_code=reason,
                           record_present=present)
    return _from_record(record, valid=False, reason=reason)


def _schema_path(repo_root: Path | None = None) -> Path:
    return (repo_root if repo_root is not None else _repo_root()) / "schemas" / EXECUTION_STAGE_SCHEMA_FILE


def _transition_consistent(record: Mapping[str, Any]) -> bool:
    stage, previous, transition = record.get("stage"), record.get("previous_stage"), record.get("transition")
    if stage not in LADDER or (previous is not None and previous not in LADDER):
        return False
    witness = (record.get("approval_id"), record.get("action_fingerprint"), record.get("witness_stage_id"))
    witnessed = all(isinstance(v, str) for v in witness)
    unwitnessed = all(v is None for v in witness)
    policy_bound = isinstance(record.get("policy_version"), str) and isinstance(record.get("policy_safety_sha256"), str)
    own_witness = witnessed and record.get("witness_stage_id") == record.get("stage_id")
    read_only = ExecutionStage.READ_ONLY.value
    if transition == T_BOOTSTRAP:
        return (previous is None and stage in BOOTSTRAP_STAGES and own_witness and policy_bound
                and bool((record.get("evidence") or {}).get("attestation")))
    if transition == T_CLIMB:
        return (previous is not None and previous != read_only and rank(stage) == rank(previous) + 1
                and own_witness and policy_bound)
    if transition == T_REBIND:
        return previous is not None and stage == previous and stage != read_only and own_witness and policy_bound
    if transition == T_DEMOTE:
        if stage == read_only:
            # The floor needs no witness, and may replace a record that could not even be read.
            return (previous is None or previous != read_only) and unwitnessed
        return (previous is not None and rank(stage) < rank(previous) and witnessed and not own_witness
                and policy_bound)
    return False


def read_registered_stage(root: Path | None = None, *, repo_root: Path | None = None) -> dict[str, Any] | None:
    """The stage record, VERIFIED (self-hash + schema) — or None when none is registered."""
    path = stage_path(root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        raise ToolError(STAGE_RECORD_UNREADABLE, f"execution stage record is unreadable: {exc}") from None
    if not isinstance(data, dict):
        raise ToolError(STAGE_RECORD_UNREADABLE, "execution stage record is not a JSON object")
    stored = data.get("record_sha256")
    body = {k: v for k, v in data.items() if k != "record_sha256"}
    try:
        recomputed = integrity.sha256_record(body)
    except (ValueError, TypeError, RecursionError) as exc:
        # A NaN, a secret-shaped key or a pathological nesting cannot be canonicalised: unreadable.
        raise ToolError(STAGE_RECORD_UNREADABLE, f"execution stage record cannot be hashed: {exc}") from None
    if not isinstance(stored, str) or recomputed != stored:
        raise ToolError(STAGE_RECORD_TAMPERED, "execution stage record fails its self-hash")
    try:
        validate_against_schema(dict(data), _schema_path(repo_root), "execution stage")
    except RuntimeSchemaError as exc:
        raise ToolError(STAGE_RECORD_INVALID, f"execution stage record is not schema-valid: {exc}") from None
    return data


# The record fields an approved transition copies from the content Thomas approved.
_CONTENT_BOUND_FIELDS = (
    ("venue", "venue"), ("transition", "transition"), ("stage", "to_stage"),
    ("policy_version", "policy_version"), ("policy_safety_sha256", "policy_safety_sha256"),
    ("registered_by", "registered_by"), ("reason", "reason"), ("evidence", "evidence"),
)


def _witness_reason(record: Mapping[str, Any], approval_store: Any) -> str | None:
    """None when the record stands on a real, spent, Thomas-verified stage approval; else the reason.

    An approved transition (BOOTSTRAP / CLIMB / REBIND) must carry exactly the content that approval
    was granted for, and the approval must have been spent writing this record. A DEMOTE carries the
    approval of the record it descends from, sits strictly below that approval's stage, and keeps
    that approval's policy identity — so it can lower a stage, never raise, rebind or invent one.
    A DEMOTE to READ_ONLY grants nothing and needs no witness."""
    if record.get("transition") == T_DEMOTE and record.get("stage") == ExecutionStage.READ_ONLY.value:
        return None
    # approval first: it puts the repository's `lib/` on the path the fingerprint helper lives on.
    from .. import approval as approval_mod
    from ..permission import EXECUTION_STAGE_PERMISSION_SCOPE, EXECUTION_STAGE_TARGET_PREFIX
    from lib.action_fingerprint import compute_action_fingerprint

    try:
        approval = approval_store.get(str(record.get("approval_id")))
    except Exception:  # noqa: BLE001 — an unreadable ledger is not a witness
        return STAGE_APPROVAL_UNREADABLE
    if not isinstance(approval, Mapping):
        return STAGE_APPROVAL_NOT_CONSUMED
    consumption = approval.get("consumption") if isinstance(approval.get("consumption"), Mapping) else {}
    approver = approval.get("approver") if isinstance(approval.get("approver"), Mapping) else {}
    if (approval.get("status") != approval_mod.STATUS_CONSUMED
            or approver.get("approved_by") != approval_mod.REQUIRED_APPROVER
            or approver.get("verification_status") != "VERIFIED"
            or approver.get("identity_verification_method") != approval_mod.TELEGRAM_VERIFICATION_METHOD
            or approval.get("action_fingerprint") != record.get("action_fingerprint")
            or consumption.get("consumption_ref") != consumption_ref(str(record.get("witness_stage_id")))):
        return STAGE_APPROVAL_NOT_CONSUMED
    snapshot = approval.get("approved_action_snapshot")
    if not isinstance(snapshot, Mapping):
        return STAGE_WITNESS_MISMATCH
    try:
        if compute_action_fingerprint(dict(snapshot)) != record.get("action_fingerprint"):
            return STAGE_WITNESS_MISMATCH
    except (ValueError, TypeError):
        return STAGE_WITNESS_MISMATCH
    content = snapshot.get("normalized_parameters")
    if (not isinstance(content, Mapping)
            or snapshot.get("permission_scope") != EXECUTION_STAGE_PERMISSION_SCOPE
            or snapshot.get("target_ref") != f"{EXECUTION_STAGE_TARGET_PREFIX}{content.get('venue')}:{content.get('to_stage')}"
            or content.get("to_stage") not in LADDER
            or stage_id_for(content) != record.get("witness_stage_id")):
        return STAGE_WITNESS_MISMATCH
    if record.get("transition") != T_DEMOTE:
        if any(record.get(r) != content.get(c) for r, c in _CONTENT_BOUND_FIELDS):
            return STAGE_WITNESS_MISMATCH
        if (record.get("previous_stage") or "NONE") != content.get("from_stage"):
            return STAGE_WITNESS_MISMATCH
        if record.get("effective_from") != consumption.get("consumed_at"):
            return STAGE_WITNESS_MISMATCH
        return None
    if (record.get("venue") != content.get("venue")
            or content.get("transition") == T_DEMOTE
            or rank(str(record.get("stage"))) >= rank(str(content.get("to_stage")))
            or record.get("policy_version") != content.get("policy_version")
            or record.get("policy_safety_sha256") != content.get("policy_safety_sha256")):
        return STAGE_WITNESS_MISMATCH
    return None


def consumption_ref(stage_id: str) -> str:
    return f"execution_stage:{stage_id}"


def resolve_execution_stage(
    root: Path | None = None,
    *,
    now: str,
    approval_store: Any | None = None,
    repo_root: Path | None = None,
) -> StageStatus:
    """The effective stage. Never raises: every failure reads READ_ONLY with its reason.

    The live leg reads this before it settles and protects open positions, so an exception here
    would halt the one path that must keep working — hence the catch-all around the whole read."""
    try:
        return _resolve(root, now=now, approval_store=approval_store, repo_root=repo_root)
    except Exception:  # noqa: BLE001 — "never raises" is this function's contract with the live leg
        return _read_only(STAGE_RECORD_UNREADABLE)


def _resolve(root: Path | None, *, now: str, approval_store: Any | None, repo_root: Path | None) -> StageStatus:
    try:
        record = read_registered_stage(root, repo_root=repo_root)
    except ToolError as exc:
        return _read_only(exc.reason_code)
    if record is None:
        return _read_only(STAGE_RECORD_MISSING, present=False)
    if record.get("venue") != SUPPORTED_VENUE:
        return _read_only(STAGE_VENUE_MISMATCH, record)
    if not _transition_consistent(record):
        return _read_only(STAGE_TRANSITION_INVALID, record)
    if record["effective_from"] > now:
        return _read_only(STAGE_NOT_YET_EFFECTIVE, record)
    if approval_store is None:
        from ..approval_store import ApprovalStore

        approval_store = ApprovalStore.default(root)
    # The witness BEFORE the running policy: a record that reads "policy changed" is thereby known to
    # have been witnessed, which is what makes that reason — and only that one — REBINDable.
    witness = _witness_reason(record, approval_store)
    if witness is not None:
        return _read_only(witness, record)
    if record["stage"] != ExecutionStage.READ_ONLY.value:
        identity = policy_safety_identity(repo_root)
        if identity is None:
            return _read_only(STAGE_POLICY_UNREADABLE, record)
        if identity["policy_version"] != record["policy_version"]:
            return _read_only(STAGE_POLICY_VERSION_CHANGED, record)
        if identity["policy_safety_sha256"] != record["policy_safety_sha256"]:
            return _read_only(STAGE_POLICY_SAFETY_CHANGED, record)
    return _from_record(record, valid=True, reason=None)


def stage_ref(status: StageStatus) -> str:
    """The id an approval binds to: the record it was asked against (the switch door's
    ``stop_ref`` idea). A transition approved against record A refuses to apply over record B."""
    return integrity.short_id("stageref", {
        "stage_id": status.stage_id or "none",
        "recorded_stage": status.recorded_stage or "none",
        "record_sha256": status.record_sha256 or "none",
    })


def plan_transition(
    status: StageStatus,
    *,
    target: str,
    registered_by: str,
    reason: str,
    attestation: str | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """What an approved transition to ``target`` would write — the content an ask binds.

    Raises ``ToolError`` with the refusal reason when the transition is not one the ladder allows.
    Pure apart from reading the policy identity; writes nothing. The door runs it again at spend
    time and refuses unless it returns the same content, so every rule here holds at both moments."""
    if target not in LADDER:
        raise ToolError(STAGE_NOT_DEFINED, f"{target!r} is not a stage; the ladder is {', '.join(LADDER)}")
    if not (isinstance(registered_by, str) and registered_by.strip() and isinstance(reason, str) and reason.strip()):
        raise ToolError(STAGE_RECORD_INVALID, "a transition names who registers it and why")
    recorded = status.recorded_stage
    rebindable = not status.valid and status.reason_code in REBINDABLE_REASONS
    evidence: dict[str, Any] = {}
    from_stage = recorded
    if status.binding:
        if target == recorded:
            raise ToolError(STAGE_ALREADY_BINDS, f"the {recorded} record already binds; there is nothing to rebind")
        if rank(target) < rank(recorded):
            raise ToolError(STAGE_USE_DEMOTE, f"{target} is below {recorded}; demotion needs no approval (--demote)")
        if rank(target) != rank(recorded) + 1:
            raise ToolError(STAGE_SKIP_REFUSED, f"{recorded} -> {target} skips a stage; climb one rung at a time")
        if target == ExecutionStage.LIVE_AUTONOMOUS.value:
            raise ToolError(STAGE_SIGNED_TESTNET_EVIDENCE_REQUIRED,
                            "LIVE_AUTONOMOUS needs a reconciled signed testnet order (Thomas decision 2); "
                            "the testnet execution and evidence path is not built yet (PR1d)")
        if target == ExecutionStage.LIVE_SCALED.value:
            raise ToolError(STAGE_NOT_DEFINED, "LIVE_SCALED has no entry rule yet; it is a separate decision")
        transition = T_CLIMB
    elif rebindable and target == recorded:
        transition = T_REBIND
        evidence["replaced_reason_code"] = status.reason_code
    elif target in BOOTSTRAP_STAGES:
        if not (isinstance(attestation, str) and attestation.strip()):
            raise ToolError(STAGE_ATTESTATION_REQUIRED,
                            "a BOOTSTRAP names the evidence it stands on (--attest)")
        transition = T_BOOTSTRAP
        from_stage = None
        if status.record_present and not (status.valid and recorded == ExecutionStage.READ_ONLY.value):
            # The record this replaces did not bind: say so in the ask, never "from nothing".
            if recorded is not None:
                evidence["replaced_stage"] = recorded
            evidence["replaced_reason_code"] = status.reason_code
    elif rebindable:
        raise ToolError(STAGE_REBIND_FIRST,
                        f"the {recorded} record is bound to another policy ({status.reason_code}); "
                        f"REBIND it at {recorded}, or BOOTSTRAP at SHADOW or PAPER")
    else:
        why = status.reason_code or "READ_ONLY"
        raise ToolError(STAGE_BOOTSTRAP_ONLY_SHADOW_OR_PAPER,
                        f"no stage above READ_ONLY binds ({why}); the next record may only be SHADOW or PAPER, not {target}")
    if isinstance(attestation, str) and attestation.strip():
        evidence["attestation"] = attestation.strip()[:2000]

    identity = policy_safety_identity(repo_root)
    if identity is None:
        raise ToolError(STAGE_POLICY_UNREADABLE, "the governance policy cannot be read, so nothing can be bound to it")
    return {
        "venue": SUPPORTED_VENUE,
        "transition": transition,
        "from_stage": from_stage or "NONE",
        "to_stage": target,
        "stage_ref": stage_ref(status),
        "policy_version": identity["policy_version"],
        "policy_safety_sha256": identity["policy_safety_sha256"],
        "registered_by": registered_by.strip(),
        "reason": reason.strip()[:600],
        "evidence": evidence,
    }


def _finish(body: dict[str, Any], *, repo_root: Path | None) -> dict[str, Any]:
    body["record_sha256"] = integrity.sha256_record(body)
    try:
        validate_against_schema(dict(body), _schema_path(repo_root), "execution stage")
    except RuntimeSchemaError as exc:
        raise ToolError(STAGE_RECORD_INVALID, f"execution stage record would not be schema-valid: {exc}") from None
    return body


def stage_id_for(content: Mapping[str, Any]) -> str:
    """The id a transition's record will carry — known before the approval is spent, so the
    CONSUMED record can point at it (``consumption_ref``)."""
    return integrity.short_id("stage", dict(content))


def record_from_approved(
    content: Mapping[str, Any],
    *,
    status_now: StageStatus,
    approval_id: str,
    action_fingerprint: str,
    now: str,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """The record an approved transition writes, after re-planning it against the machine as it is.

    Refuses ``STAGE_CHANGED`` when the stage record moved since the ask (or the transition is no
    longer the one the ladder allows), and ``STAGE_POLICY_CHANGED_SINCE_ASK`` when the policy the ask
    was bound to is not the one running. ``now`` becomes both ``effective_from`` and the CONSUMED
    record's ``consumed_at`` — the witness checks they agree."""
    if content.get("stage_ref") != stage_ref(status_now):
        raise ToolError(STAGE_CHANGED, "the stage record changed after this transition was asked for; ask again")
    identity = policy_safety_identity(repo_root)
    if (identity is None or identity["policy_version"] != content.get("policy_version")
            or identity["policy_safety_sha256"] != content.get("policy_safety_sha256")):
        raise ToolError(STAGE_POLICY_CHANGED_SINCE_ASK, "the governance policy changed after this transition was asked for")
    replanned = plan_transition(
        status_now, target=str(content.get("to_stage")), registered_by=str(content.get("registered_by")),
        reason=str(content.get("reason")), attestation=(content.get("evidence") or {}).get("attestation"),
        repo_root=repo_root,
    )
    if replanned != dict(content):
        raise ToolError(STAGE_CHANGED, "this transition is no longer the one the ladder allows from the current record; ask again")
    stage_id = stage_id_for(content)
    body: dict[str, Any] = {
        "schema_version": EXECUTION_STAGE_SCHEMA_VERSION,
        "stage_id": stage_id,
        "venue": content["venue"],
        "stage": content["to_stage"],
        "previous_stage": None if content["from_stage"] == "NONE" else content["from_stage"],
        "transition": content["transition"],
        "effective_from": now,
        "registered_by": content["registered_by"],
        "reason": content["reason"],
        "approval_id": approval_id,
        "action_fingerprint": action_fingerprint,
        "witness_stage_id": stage_id,
        "policy_version": content["policy_version"],
        "policy_safety_sha256": content["policy_safety_sha256"],
        "evidence": dict(content.get("evidence") or {}),
        "registered_at": now,
    }
    return _finish(body, repo_root=repo_root)


def demote_record(
    status: StageStatus,
    *,
    target: str,
    registered_by: str,
    reason: str,
    now: str,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """A demotion: any rung below a binding stage, no approval (decision 8). It changes only which
    NEW exposure a door may create; it never gates a close.

    To READ_ONLY it works from anything — a record that does not bind, or cannot even be read, can
    always be stopped. To any other rung it needs a record that binds, and carries that record's
    witness and policy identity forward: a demotion lowers a stage, it cannot rebind one after a
    policy change or launder one that was never approved."""
    if target not in LADDER:
        raise ToolError(STAGE_NOT_DEFINED, f"{target!r} is not a stage")
    if not (isinstance(registered_by, str) and registered_by.strip() and isinstance(reason, str) and reason.strip()):
        raise ToolError(STAGE_RECORD_INVALID, "a demotion names who made it and why")
    read_only = ExecutionStage.READ_ONLY.value
    common = {
        "schema_version": EXECUTION_STAGE_SCHEMA_VERSION,
        "venue": SUPPORTED_VENUE,
        "stage": target,
        "transition": T_DEMOTE,
        "effective_from": now,
        "registered_by": registered_by.strip(),
        "reason": reason.strip()[:600],
        "evidence": {},
        "registered_at": now,
    }
    if target == read_only:
        if not status.record_present:
            raise ToolError(STAGE_NOTHING_TO_DEMOTE, "no stage is recorded; the machine already reads READ_ONLY")
        if status.valid and status.recorded_stage == read_only:
            raise ToolError(STAGE_NOT_LOWER, "the recorded stage is already READ_ONLY")
        identity = policy_safety_identity(repo_root) or {}
        body = {
            **common,
            "previous_stage": status.recorded_stage if status.recorded_stage != read_only else None,
            "approval_id": None, "action_fingerprint": None, "witness_stage_id": None,
            # Informational on the floor: a READ_ONLY record grants nothing, so it binds no policy and
            # must be writable when the policy cannot be read.
            "policy_version": identity.get("policy_version"),
            "policy_safety_sha256": identity.get("policy_safety_sha256"),
        }
        if not status.valid and status.reason_code:
            body["evidence"] = {"replaced_reason_code": status.reason_code}
    else:
        if not status.binding:
            raise ToolError(STAGE_DEMOTE_FROM_UNBOUND,
                            f"the recorded stage does not bind ({status.reason_code or 'READ_ONLY'}); "
                            "it can only be demoted to READ_ONLY")
        if rank(target) >= rank(status.recorded_stage):
            raise ToolError(STAGE_NOT_LOWER, f"{target} is not below the recorded {status.recorded_stage}")
        body = {
            **common,
            "previous_stage": status.recorded_stage,
            "approval_id": status.approval_id, "action_fingerprint": status.action_fingerprint,
            "witness_stage_id": status.witness_stage_id,
            "policy_version": status.policy_version,
            "policy_safety_sha256": status.policy_safety_sha256,
        }
    body["stage_id"] = stage_id_for({**body, "from_stage_id": status.stage_id or "none"})
    return _finish(body, repo_root=repo_root)


def write_stage_record(record: Mapping[str, Any], root: Path | None = None) -> Path:
    """Atomically replace the record (temp file + replace). The previous record lives on in the
    control ledger's transition event, which the door writes beside this."""
    path = stage_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(dict(record), ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def transition_event(record: Mapping[str, Any], *, previous: StageStatus, now: str) -> dict[str, Any]:
    """The control-ledger event a transition leaves (the history the single record file does not keep)."""
    from ..events import stamped_event

    return stamped_event(
        TRANSITION_EVENT_TYPE,
        stage_id=record["stage_id"], stage=record["stage"], previous_stage=record["previous_stage"],
        transition=record["transition"], approval_id=record["approval_id"],
        registered_by=record["registered_by"], reason=record["reason"],
        record_sha256=record["record_sha256"], replaced_stage_id=previous.stage_id,
        replaced_recorded_stage=previous.recorded_stage,
        replaced_valid=previous.valid, replaced_reason_code=previous.reason_code, created_at=now,
    )
