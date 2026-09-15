"""The machine's one execution stage (PR1a — Thomas decisions 1, 4, 8, 9, 2026-09-15).

Until this record existed, "what stage is this machine at" had no owner. The environment
(``MVP_LIVE_TRADING=real``) authorized the order adapter, eight durable records could refuse an
entry, and the readiness board re-derived an answer from all of them on every read — which is how
it could read all-PASS with no strategy armed (execution-authority audit, 2026-09-15). This module
is the record that answers the question, the verified read, and the transition rules:

    READ_ONLY -> SHADOW -> PAPER -> SIGNED_TESTNET -> LIVE_CANARY -> LIVE_AUTONOMOUS -> LIVE_SCALED

- **BOOTSTRAP** — the first record, at SHADOW or PAPER, approved once, carrying an attestation of
  the evidence behind it (decision 1: this host starts at PAPER).
- **CLIMB** — exactly one rung up, approved once. No skip, ever.
- **REBIND** — the same rung again, approved once: after a policy change (the record is bound to
  the policy it was approved under) or to renew a LIVE stage's validity.
- **DEMOTE** — any rung down, no approval, immediate (decision 8). A stop must be cheap.

A record that is missing, unreadable, tampered, schema-invalid, expired, not yet effective, for
another venue, internally inconsistent, bound to a different policy version or safety semantic
fingerprint (decision 4), or not backed by its CONSUMED approval, reads as **READ_ONLY** (decision
9) — with the reason, never an exception.

**What this module does NOT do (PR1a):** it enforces nothing. No entry door reads it yet; that is
PR1b, which also decides nothing about closing — a stage gates new exposure only, never the close
path (a demotion that stranded an open position would be the failure the close-guard exemptions
exist to prevent). ``LIVE_CANARY`` cannot be climbed to yet: its evidence is a reconciled signed
testnet order (decision 2), and the testnet execution path is PR1d.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from runtime.read_only_kernel import integrity
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from .. import timeutil
from ..errors import ToolError
from ..paths import repo_root as _repo_root
from ..policy_fingerprint import policy_safety_identity
from ..schema_cache import validate_against_schema
from .state import state_dir

EXECUTION_STAGE_SCHEMA_VERSION = "execution_stage.v0.1"
EXECUTION_STAGE_SCHEMA_FILE = "execution_stage.v0.1.schema.json"
EXECUTION_STAGE_FILENAME = "execution_stage.json"
TRANSITION_EVENT_TYPE = "execution_stage_transition.v0"
SUPPORTED_VENUE = "binance_futures"
# Whether any entry door reads the stage yet. False in PR1a: the record is kept and reported, and
# the ask, the board and the door say so. PR1b flips it in the same change that makes the doors read
# it, so no text can claim enforcement that does not exist (or deny enforcement that does).
STAGE_ENFORCED = False


class ExecutionStage(str, Enum):
    READ_ONLY = "READ_ONLY"
    SHADOW = "SHADOW"
    PAPER = "PAPER"
    SIGNED_TESTNET = "SIGNED_TESTNET"
    LIVE_CANARY = "LIVE_CANARY"
    LIVE_AUTONOMOUS = "LIVE_AUTONOMOUS"
    LIVE_SCALED = "LIVE_SCALED"


LADDER: tuple[str, ...] = tuple(s.value for s in ExecutionStage)
BOOTSTRAP_STAGES = frozenset({ExecutionStage.SHADOW.value, ExecutionStage.PAPER.value})
LIVE_STAGES = frozenset({ExecutionStage.LIVE_CANARY.value, ExecutionStage.LIVE_AUTONOMOUS.value,
                         ExecutionStage.LIVE_SCALED.value})
# A LIVE stage carries an end date, like the registered budget, so no approval to trade real money
# stands forever. Renewal is a REBIND — approved again.
MAX_LIVE_VALIDITY_DAYS = 30
# The canary evidence a LIVE_AUTONOMOUS climb requires, as a floor in code (audit CA-4: the
# ">= 3" in the policy was only ever a budget-registered number).
MIN_CLEAN_CANARY_ORDERS_FOR_AUTONOMOUS = 3

T_BOOTSTRAP = "BOOTSTRAP"
T_CLIMB = "CLIMB"
T_REBIND = "REBIND"
T_DEMOTE = "DEMOTE"

# What a door's purpose needs (PR1b reads these; recorded here so the ladder has one owner).
PURPOSE_CANARY = "canary"
PURPOSE_PROBE = "probe"
PURPOSE_AUTONOMOUS = "autonomous"
PURPOSE_LIVE_ARM = "live_arm"
_REQUIRED_STAGE = {
    PURPOSE_CANARY: ExecutionStage.LIVE_CANARY.value,
    PURPOSE_PROBE: ExecutionStage.LIVE_CANARY.value,
    PURPOSE_AUTONOMOUS: ExecutionStage.LIVE_AUTONOMOUS.value,
    PURPOSE_LIVE_ARM: ExecutionStage.LIVE_AUTONOMOUS.value,
}

# Why a record reads as READ_ONLY.
STAGE_RECORD_MISSING = "EXECUTION_STAGE_RECORD_MISSING"
STAGE_RECORD_UNREADABLE = "EXECUTION_STAGE_RECORD_UNREADABLE"
STAGE_RECORD_TAMPERED = "EXECUTION_STAGE_RECORD_TAMPERED"
STAGE_RECORD_INVALID = "EXECUTION_STAGE_RECORD_INVALID"
STAGE_RECORD_EXPIRED = "EXECUTION_STAGE_RECORD_EXPIRED"
STAGE_NOT_YET_EFFECTIVE = "EXECUTION_STAGE_NOT_YET_EFFECTIVE"
STAGE_VENUE_MISMATCH = "EXECUTION_STAGE_VENUE_MISMATCH"
STAGE_TRANSITION_INVALID = "EXECUTION_STAGE_TRANSITION_INVALID"
STAGE_POLICY_UNREADABLE = "EXECUTION_STAGE_POLICY_UNREADABLE"
STAGE_POLICY_VERSION_CHANGED = "EXECUTION_STAGE_POLICY_VERSION_CHANGED"
STAGE_POLICY_SAFETY_CHANGED = "EXECUTION_STAGE_POLICY_SAFETY_CHANGED"
STAGE_APPROVAL_NOT_CONSUMED = "EXECUTION_STAGE_APPROVAL_NOT_CONSUMED"
STAGE_APPROVAL_UNREADABLE = "EXECUTION_STAGE_APPROVAL_UNREADABLE"

# Why the registration door refuses a transition.
STAGE_SKIP_REFUSED = "EXECUTION_STAGE_SKIP_REFUSED"
STAGE_USE_DEMOTE = "EXECUTION_STAGE_USE_DEMOTE"
STAGE_NOT_LOWER = "EXECUTION_STAGE_NOT_LOWER"
STAGE_NOTHING_TO_DEMOTE = "EXECUTION_STAGE_NOTHING_TO_DEMOTE"
STAGE_BOOTSTRAP_ONLY_SHADOW_OR_PAPER = "EXECUTION_STAGE_BOOTSTRAP_ONLY_SHADOW_OR_PAPER"
STAGE_CLIMB_FROM_INVALID = "EXECUTION_STAGE_CLIMB_FROM_INVALID_RECORD"
STAGE_ATTESTATION_REQUIRED = "EXECUTION_STAGE_ATTESTATION_REQUIRED"
STAGE_SIGNED_TESTNET_EVIDENCE_REQUIRED = "EXECUTION_STAGE_SIGNED_TESTNET_EVIDENCE_REQUIRED"
STAGE_CANARY_EVIDENCE_REQUIRED = "EXECUTION_STAGE_CANARY_EVIDENCE_REQUIRED"
STAGE_NOT_DEFINED = "EXECUTION_STAGE_NOT_DEFINED"
STAGE_VALIDITY_INVALID = "EXECUTION_STAGE_VALIDITY_INVALID"
STAGE_CHANGED = "EXECUTION_STAGE_CHANGED"
STAGE_POLICY_CHANGED_SINCE_ASK = "EXECUTION_STAGE_POLICY_CHANGED_SINCE_ASK"


def rank(stage: str) -> int:
    """Position on the ladder. An unknown stage has no position and raises."""
    return LADDER.index(stage)


def required_stage(purpose: str) -> str:
    """The lowest stage at which a door opened for ``purpose`` may create new exposure."""
    return _REQUIRED_STAGE[purpose]


def stage_path(root: Path | None = None) -> Path:
    return state_dir(root) / EXECUTION_STAGE_FILENAME


@dataclass(frozen=True)
class StageStatus:
    """What the machine's stage IS, right now — the one fact every consumer reads.

    ``stage`` is the effective stage: the recorded stage when the record is valid, READ_ONLY
    otherwise. ``recorded_stage`` is what the file says, kept apart so a REBIND or a DEMOTE can
    name the rung a record that no longer binds was at."""

    stage: str
    valid: bool
    reason_code: str | None
    recorded_stage: str | None = None
    stage_id: str | None = None
    record_sha256: str | None = None
    valid_until: str | None = None
    policy_version: str | None = None

    def allows(self, purpose: str) -> bool:
        return self.valid and rank(self.stage) >= rank(required_stage(purpose))

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage, "valid": self.valid, "reason_code": self.reason_code,
            "recorded_stage": self.recorded_stage, "stage_id": self.stage_id,
            "record_sha256": self.record_sha256, "valid_until": self.valid_until,
            "policy_version": self.policy_version,
        }


def _read_only(reason: str, record: Mapping[str, Any] | None = None) -> StageStatus:
    record = record or {}
    recorded = record.get("stage") if record.get("stage") in LADDER else None
    return StageStatus(
        stage=ExecutionStage.READ_ONLY.value, valid=False, reason_code=reason,
        recorded_stage=recorded, stage_id=record.get("stage_id"),
        record_sha256=record.get("record_sha256"), valid_until=record.get("valid_until"),
        policy_version=record.get("policy_version"),
    )


def _schema_path(repo_root: Path | None = None) -> Path:
    return (repo_root if repo_root is not None else _repo_root()) / "schemas" / EXECUTION_STAGE_SCHEMA_FILE


def _transition_consistent(record: Mapping[str, Any]) -> bool:
    stage, previous, transition = record.get("stage"), record.get("previous_stage"), record.get("transition")
    approved = isinstance(record.get("approval_id"), str) and isinstance(record.get("action_fingerprint"), str)
    if stage not in LADDER or (previous is not None and previous not in LADDER):
        return False
    if transition == T_BOOTSTRAP:
        return previous is None and stage in BOOTSTRAP_STAGES and approved
    if transition == T_CLIMB:
        return previous is not None and rank(stage) == rank(previous) + 1 and approved
    if transition == T_REBIND:
        return previous is not None and stage == previous and approved
    if transition == T_DEMOTE:
        return previous is not None and rank(stage) < rank(previous)
    return False


def read_registered_stage(root: Path | None = None, *, repo_root: Path | None = None) -> dict[str, Any] | None:
    """The stage record, VERIFIED (self-hash + schema) — or None when none is registered."""
    path = stage_path(root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError(STAGE_RECORD_UNREADABLE, f"execution stage record is unreadable: {exc}") from None
    if not isinstance(data, dict):
        raise ToolError(STAGE_RECORD_UNREADABLE, "execution stage record is not a JSON object")
    stored = data.get("record_sha256")
    body = {k: v for k, v in data.items() if k != "record_sha256"}
    if not isinstance(stored, str) or integrity.sha256_record(body) != stored:
        raise ToolError(STAGE_RECORD_TAMPERED, "execution stage record fails its self-hash")
    try:
        validate_against_schema(dict(data), _schema_path(repo_root), "execution stage")
    except RuntimeSchemaError as exc:
        raise ToolError(STAGE_RECORD_INVALID, f"execution stage record is not schema-valid: {exc}") from None
    return data


def _approval_backs(record: Mapping[str, Any], approval_store: Any) -> str | None:
    """None when the record's approval is CONSUMED for exactly this record; else the reason.

    The self-hash proves the file was not edited after it was written; it cannot prove the file
    was written by the door, because anyone who can write the state directory can recompute it
    (audit FO-12). The approval ledger is the second witness: a non-DEMOTE record must name an
    approval that was spent, with this record's fingerprint, by the transition that wrote it."""
    if record.get("transition") == T_DEMOTE:
        return None
    try:
        approval = approval_store.get(str(record.get("approval_id")))
    except Exception:  # noqa: BLE001 — an unreadable ledger is not a witness
        return STAGE_APPROVAL_UNREADABLE
    if not isinstance(approval, Mapping):
        return STAGE_APPROVAL_NOT_CONSUMED
    consumption = approval.get("consumption") or {}
    if (approval.get("status") != "CONSUMED"
            or approval.get("action_fingerprint") != record.get("action_fingerprint")
            or consumption.get("consumption_ref") != consumption_ref(str(record.get("stage_id")))):
        return STAGE_APPROVAL_NOT_CONSUMED
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
    """The effective stage. Never raises: every failure reads READ_ONLY with its reason."""
    try:
        record = read_registered_stage(root, repo_root=repo_root)
    except ToolError as exc:
        return _read_only(exc.reason_code)
    if record is None:
        return _read_only(STAGE_RECORD_MISSING)
    if record.get("venue") != SUPPORTED_VENUE:
        return _read_only(STAGE_VENUE_MISMATCH, record)
    if not _transition_consistent(record):
        return _read_only(STAGE_TRANSITION_INVALID, record)
    if record["stage"] in LIVE_STAGES and not record.get("valid_until"):
        return _read_only(STAGE_RECORD_INVALID, record)
    if record["effective_from"] > now:
        return _read_only(STAGE_NOT_YET_EFFECTIVE, record)
    if record.get("valid_until") and now > record["valid_until"]:
        return _read_only(STAGE_RECORD_EXPIRED, record)
    identity = policy_safety_identity(repo_root)
    if identity is None:
        return _read_only(STAGE_POLICY_UNREADABLE, record)
    if identity["policy_version"] != record["policy_version"]:
        return _read_only(STAGE_POLICY_VERSION_CHANGED, record)
    if identity["policy_safety_sha256"] != record["policy_safety_sha256"]:
        return _read_only(STAGE_POLICY_SAFETY_CHANGED, record)
    if approval_store is None:
        from ..approval_store import ApprovalStore

        approval_store = ApprovalStore.default(root)
    backing = _approval_backs(record, approval_store)
    if backing is not None:
        return _read_only(backing, record)
    return StageStatus(
        stage=record["stage"], valid=True, reason_code=None, recorded_stage=record["stage"],
        stage_id=record["stage_id"], record_sha256=record["record_sha256"],
        valid_until=record.get("valid_until"), policy_version=record["policy_version"],
    )


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
    now: str,
    registered_by: str,
    reason: str,
    valid_days: int | None = None,
    attestation: str | None = None,
    clean_canary_orders: int | None = None,
    canary_registry_error: str | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """What an approved transition to ``target`` would write — the content an ask binds.

    Raises ``ToolError`` with the refusal reason when the transition is not one the ladder allows.
    Pure apart from reading the policy identity; writes nothing."""
    if target not in LADDER:
        raise ToolError(STAGE_NOT_DEFINED, f"{target!r} is not a stage; the ladder is {', '.join(LADDER)}")
    if not (isinstance(registered_by, str) and registered_by.strip() and isinstance(reason, str) and reason.strip()):
        raise ToolError(STAGE_RECORD_INVALID, "a transition names who registers it and why")
    recorded = status.recorded_stage
    evidence: dict[str, Any] = {}
    if recorded is None:
        if target not in BOOTSTRAP_STAGES:
            raise ToolError(STAGE_BOOTSTRAP_ONLY_SHADOW_OR_PAPER,
                            f"no stage is recorded; the first record may only be SHADOW or PAPER, not {target}")
        if not (isinstance(attestation, str) and attestation.strip()):
            raise ToolError(STAGE_ATTESTATION_REQUIRED,
                            "the first record names the evidence it stands on (--attest)")
        transition = T_BOOTSTRAP
    elif target == recorded:
        transition = T_REBIND
    elif rank(target) == rank(recorded) + 1:
        if not status.valid:
            raise ToolError(STAGE_CLIMB_FROM_INVALID,
                            f"the {recorded} record does not bind ({status.reason_code}); REBIND it first")
        transition = T_CLIMB
    elif rank(target) < rank(recorded):
        raise ToolError(STAGE_USE_DEMOTE, f"{target} is below {recorded}; demotion needs no approval (--demote)")
    else:
        raise ToolError(STAGE_SKIP_REFUSED, f"{recorded} -> {target} skips a stage; climb one rung at a time")

    if transition != T_REBIND:
        if target == ExecutionStage.LIVE_CANARY.value:
            raise ToolError(STAGE_SIGNED_TESTNET_EVIDENCE_REQUIRED,
                            "LIVE_CANARY needs a reconciled signed testnet order (Thomas decision 2); "
                            "the testnet execution path is not built yet (PR1d)")
        if target == ExecutionStage.LIVE_AUTONOMOUS.value:
            if clean_canary_orders is None or clean_canary_orders < MIN_CLEAN_CANARY_ORDERS_FOR_AUTONOMOUS:
                raise ToolError(STAGE_CANARY_EVIDENCE_REQUIRED,
                                f"LIVE_AUTONOMOUS needs >= {MIN_CLEAN_CANARY_ORDERS_FOR_AUTONOMOUS} clean canary "
                                f"orders, have {clean_canary_orders} ({canary_registry_error or 'registry read'})")
            evidence["clean_canary_orders"] = int(clean_canary_orders)
            evidence["canary_registry_error"] = canary_registry_error
        if target == ExecutionStage.LIVE_SCALED.value:
            raise ToolError(STAGE_NOT_DEFINED, "LIVE_SCALED has no entry rule yet; it is a separate decision")
    if isinstance(attestation, str) and attestation.strip():
        evidence["attestation"] = attestation.strip()[:2000]

    valid_until: str | None = None
    if target in LIVE_STAGES:
        if not (isinstance(valid_days, int) and 1 <= valid_days <= MAX_LIVE_VALIDITY_DAYS):
            raise ToolError(STAGE_VALIDITY_INVALID,
                            f"a LIVE stage ends within {MAX_LIVE_VALIDITY_DAYS} days (--valid-days 1..{MAX_LIVE_VALIDITY_DAYS})")
        valid_until = (timeutil.parse_iso(now) + timedelta(days=valid_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    elif valid_days is not None:
        raise ToolError(STAGE_VALIDITY_INVALID, f"{target} carries no end date")

    identity = policy_safety_identity(repo_root)
    if identity is None:
        raise ToolError(STAGE_POLICY_UNREADABLE, "the governance policy cannot be read, so nothing can be bound to it")
    return {
        "venue": SUPPORTED_VENUE,
        "transition": transition,
        "from_stage": recorded or "NONE",
        "to_stage": target,
        "stage_ref": stage_ref(status),
        "valid_until": valid_until or "NONE",
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
    """The record an approved transition writes, after re-checking what it was approved against.

    Refuses ``STAGE_CHANGED`` when the stage record moved since the ask, and
    ``STAGE_POLICY_CHANGED_SINCE_ASK`` when the policy the ask was bound to is not the one running."""
    if content.get("stage_ref") != stage_ref(status_now):
        raise ToolError(STAGE_CHANGED, "the stage record changed after this transition was asked for; ask again")
    identity = policy_safety_identity(repo_root)
    if (identity is None or identity["policy_version"] != content.get("policy_version")
            or identity["policy_safety_sha256"] != content.get("policy_safety_sha256")):
        raise ToolError(STAGE_POLICY_CHANGED_SINCE_ASK, "the governance policy changed after this transition was asked for")
    valid_until = content.get("valid_until")
    body: dict[str, Any] = {
        "schema_version": EXECUTION_STAGE_SCHEMA_VERSION,
        "stage_id": stage_id_for(content),
        "venue": content["venue"],
        "stage": content["to_stage"],
        "previous_stage": None if content["from_stage"] == "NONE" else content["from_stage"],
        "transition": content["transition"],
        "effective_from": now,
        "valid_until": None if valid_until in (None, "NONE") else valid_until,
        "registered_by": content["registered_by"],
        "reason": content["reason"],
        "approval_id": approval_id,
        "action_fingerprint": action_fingerprint,
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
    """A demotion: any rung below what is recorded, no approval (decision 8). It changes only which
    NEW exposure a door may create; it never gates a close."""
    if target not in LADDER:
        raise ToolError(STAGE_NOT_DEFINED, f"{target!r} is not a stage")
    if status.recorded_stage is None:
        raise ToolError(STAGE_NOTHING_TO_DEMOTE, "no stage is recorded; the machine already reads READ_ONLY")
    if rank(target) >= rank(status.recorded_stage):
        raise ToolError(STAGE_NOT_LOWER, f"{target} is not below the recorded {status.recorded_stage}")
    if not (isinstance(registered_by, str) and registered_by.strip() and isinstance(reason, str) and reason.strip()):
        raise ToolError(STAGE_RECORD_INVALID, "a demotion names who made it and why")
    identity = policy_safety_identity(repo_root)
    if identity is None:
        raise ToolError(STAGE_POLICY_UNREADABLE, "the governance policy cannot be read")
    content = {
        "venue": SUPPORTED_VENUE, "transition": T_DEMOTE, "from_stage": status.recorded_stage,
        "to_stage": target, "stage_ref": stage_ref(status), "registered_by": registered_by.strip(),
        "reason": reason.strip()[:600], "at": now,
    }
    body: dict[str, Any] = {
        "schema_version": EXECUTION_STAGE_SCHEMA_VERSION,
        "stage_id": stage_id_for(content),
        "venue": SUPPORTED_VENUE,
        "stage": target,
        "previous_stage": status.recorded_stage,
        "transition": T_DEMOTE,
        "effective_from": now,
        # A demotion never extends a LIVE window: it keeps the end date the approved record had.
        "valid_until": status.valid_until if target in LIVE_STAGES else None,
        "registered_by": registered_by.strip(),
        "reason": reason.strip()[:600],
        "approval_id": None,
        "action_fingerprint": None,
        "policy_version": identity["policy_version"],
        "policy_safety_sha256": identity["policy_safety_sha256"],
        "evidence": {},
        "registered_at": now,
    }
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
        replaced_valid=previous.valid, replaced_reason_code=previous.reason_code, created_at=now,
    )
