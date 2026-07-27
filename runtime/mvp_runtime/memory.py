"""R5.1 Memory candidate creation.

Per the canonical Governance Policy (`memory_learning`): working-memory candidate creation
is **ALLOW**, but validated/core memory writes are gated, `automatic_runtime_promotion_allowed`
is false, and `secret_storage_in_memory_allowed` is false. So the specialist may *propose*
memory candidates from its analysis; it may never promote them, and a candidate never carries
runtime permission.

`build_memory_candidates` derives candidate proposals from an analysis, honoring the
**assignment's** memory scope (creation must be allowed; only the role's allowed candidate
types are used) and failing closed on a secret-bearing candidate. Every candidate is stamped
`status="CANDIDATE"`, `validated=False`, `promotable=False` — it is a proposal, nothing more.
The candidates ride on `agent_output.memory_candidates`, so they are persisted and audited
with the output (the output fingerprint covers them); explicit promotion is out of scope and
requires separate governance.
"""

from __future__ import annotations

from typing import Any, Mapping

from runtime.read_only_kernel import integrity
from runtime.read_only_kernel.integrity import IntegrityError

from . import timeutil
from .errors import MemoryBlocked
from .events import stamped_event

CANDIDATE_STATUS = "CANDIDATE"          # never VALIDATED / CORE
PROMOTED_STATUS = "PROMOTED"            # retirement marker: the candidate was promoted (R5/R10)
CANDIDATE_SCOPE = "task_working_memory"
PREFERRED_TYPE = "reusable_knowledge"
MAX_CANDIDATES = 5
MAX_RETRIEVED = 5

# Working-memory retention (policy §12.4: working memory expires when its task ends). Each
# candidate is stamped with an ``expires_at``; expired candidates are never returned as context
# and are deleted by the retention pass. The default is a conservative finite window that still
# lets a candidate serve as cross-task context for a few days (R5.2). Overridable per call.
EXPIRES_AT = "expires_at"
WORKING_MEMORY_TTL_MINUTES = 7 * 24 * 60  # 7 days

VALIDATED_STATUS = "VALIDATED"
VALIDATED_SCOPE = "related_validated_memory"
PROMOTION_DISPOSITION = "EXECUTE_AND_REPORT"   # governance tier for validated-knowledge promotion

# M5a — correction-learning candidates. When a run is *corrected* — the M3 revision loop
# turns a REVISE into a PASS, or the operator leaves a ``/feedback bad <note>`` — the
# correction is minted as a working-memory CANDIDATE so a later similar request can start
# closer to the accepted answer (Thomas's "next time go A→D directly"). Like every
# candidate it is ALLOW-tier and nothing more: ``status=CANDIDATE``, never auto-promoted —
# the operator's M5b approval is the only door to VALIDATED. The delta is captured; trust
# is still earned separately.
LEARNING_SOURCE_REVISION = "revision"
LEARNING_SOURCE_FEEDBACK = "operator_feedback"
LEARNING_EVENT_TYPE = "working_memory_learning_event.v0"
MAX_CORRECTION_CHARS = 2000   # cap the stored delta: a correction is guidance, not a transcript


ORIGIN_FIELDS = ("task_id", "task_revision", "trace_id", "core_context_binding_id", "data_sensitivity")


def missing_origin_fields(origin: Mapping[str, Any]) -> list[str]:
    """Names of required origin-provenance fields missing or invalid in ``origin``, sorted.

    THE completeness rule for candidate provenance — candidate creation (here) and the
    promotion audit (audit.py) must agree on what "complete" means, so both call this
    instead of keeping their own field lists that could drift apart."""
    missing = [k for k in ("task_id", "trace_id", "core_context_binding_id", "data_sensitivity")
               if not (isinstance(origin.get(k), str) and origin.get(k))]
    if not (isinstance(origin.get("task_revision"), int) and origin.get("task_revision", 0) >= 1):
        missing.append("task_revision")
    return sorted(missing)


def is_expired(entry: Mapping[str, Any], now: str) -> bool:
    """True if a working-memory entry's ``expires_at`` is at or before ``now``.

    An entry with no ``expires_at`` (a candidate created before retention existed) is treated as
    **not** expired — retention never surprises-deletes data it did not stamp; only entries with a
    concrete expiry are aged out. Timestamps are the fixed RFC3339 UTC form, so a string compare
    is a correct time compare."""
    expires_at = entry.get(EXPIRES_AT) if isinstance(entry, Mapping) else None
    if not (isinstance(expires_at, str) and expires_at):
        return False
    return expires_at <= now


def _normalize_origin(origin: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Return a candidate's origin provenance, or ``None`` when no usable origin was supplied.

    Fail-closed: an origin that is present but incomplete raises ``MemoryBlocked`` rather than
    stamping a half-populated lineage that cannot be audited later. A candidate with no origin
    at all is allowed (older/opt-out callers) — its promotion simply fails closed downstream."""
    if origin is None:
        return None
    provenance = {field: origin.get(field) for field in ORIGIN_FIELDS}
    missing = missing_origin_fields(provenance)
    if missing:
        raise MemoryBlocked("INVALID_ORIGIN", f"candidate origin provenance is incomplete: {missing}")
    return provenance


def candidate_type_for(assignment: Mapping[str, Any]) -> str | None:
    """The candidate type a run's assignment permits, or None when creation is disallowed.

    THE type-selection rule, shared so :func:`build_memory_candidates` (the specialist's
    proposals) and :func:`build_correction_candidate` (M5a corrections) never drift: prefer
    ``reusable_knowledge`` when the role allows it, else the first allowed type. None when the
    assignment does not permit candidate creation or declares no allowed types — the caller
    fails closed to minting nothing."""
    memory_scope = assignment.get("memory_scope", {}) if isinstance(assignment, Mapping) else {}
    if not memory_scope.get("memory_candidate_creation_allowed"):
        return None
    allowed = [t for t in memory_scope.get("allowed_candidate_types", []) if isinstance(t, str) and t]
    if not allowed:
        return None
    return PREFERRED_TYPE if PREFERRED_TYPE in allowed else sorted(allowed)[0]


def build_memory_candidates(
    analysis: Mapping[str, Any],
    assignment: Mapping[str, Any],
    *,
    now: str,
    seed: Mapping[str, Any] | None = None,
    origin: Mapping[str, Any] | None = None,
    ttl_minutes: int = WORKING_MEMORY_TTL_MINUTES,
) -> list[dict[str, Any]]:
    """Propose working-memory candidates from an analysis, or none.

    Fail-closed to an empty list when the assignment does not permit candidate creation or
    declares no allowed candidate types. Raises ``MemoryBlocked`` on a secret-bearing
    candidate. Candidates are proposals: ``status=CANDIDATE``, ``validated=False``,
    ``promotable=False`` — never validated, core, or auto-promoted. Each candidate is stamped
    with ``expires_at`` (``now`` + ``ttl_minutes``) for retention (policy §12.4).

    ``origin`` (optional) stamps each candidate with the identity of the task that produced it
    (``task_id``/``task_revision``/``trace_id``/``core_context_binding_id``/``data_sensitivity``).
    That provenance is what lets a later, off-run-path promotion be audited against the real
    originating task (R5.4). It never affects ``candidate_id`` (derived from ``seed``), so
    stamping provenance is deterministic and leaves existing ids unchanged."""
    origin_provenance = _normalize_origin(origin)
    candidate_type = candidate_type_for(assignment)
    if candidate_type is None:
        return []

    findings = [f.strip() for f in (analysis.get("key_findings") or [])
                if isinstance(f, str) and f.strip()][:MAX_CANDIDATES]
    expires_at = timeutil.plus_minutes(now, ttl_minutes)
    base = dict(seed or {})
    candidates: list[dict[str, Any]] = []
    for index, finding in enumerate(findings, start=1):
        candidate = {
            "candidate_id": integrity.short_id(
                "memcand", {**base, "candidate_type": candidate_type, "index": index, "content": finding}
            ),
            "candidate_type": candidate_type,
            "scope": CANDIDATE_SCOPE,
            "status": CANDIDATE_STATUS,
            "validated": False,
            "promotable": False,
            "content": finding,
            "evidence_refs": ["model:analysis"],
            "created_at": now,
            EXPIRES_AT: expires_at,
        }
        if origin_provenance is not None:
            candidate["origin"] = origin_provenance
        candidates.append(candidate)

    # Secrets must never be stored in memory (governance). Fail closed on a secret-bearing key.
    for candidate in candidates:
        try:
            integrity.scan_for_secret_bearing_keys(candidate)
        except IntegrityError as exc:
            raise MemoryBlocked("SECRET_IN_CANDIDATE", str(exc)) from exc
    return candidates


def build_correction_candidate(
    content: str,
    *,
    source: str,
    now: str,
    candidate_type: str = PREFERRED_TYPE,
    seed: Mapping[str, Any] | None = None,
    origin: Mapping[str, Any] | None = None,
    correction_ref: str | None = None,
    ttl_minutes: int = WORKING_MEMORY_TTL_MINUTES,
) -> dict[str, Any] | None:
    """Mint one correction CANDIDATE from a delta, or None when there is nothing to store (M5a).

    Same shape/scope/stamping as a :func:`build_memory_candidates` proposal — so retrieval,
    promotion, and retention treat it identically — with two additive fields: ``learning_source``
    (``revision`` or ``operator_feedback``) and, when known, ``correction_ref`` (the run the
    correction is about). ``status=CANDIDATE``, ``validated=False``, ``promotable=False``: a
    correction is captured, never auto-trusted (the M5b operator approval is the only door to
    VALIDATED). Empty content returns None — a correction with no delta is nothing to learn.
    ``origin`` (optional) stamps the originating task's provenance for a later audited promotion,
    exactly as for the specialist's proposals; None is allowed (e.g. the operator-feedback path,
    which has no live assignment). Fails closed (``MemoryBlocked``) on a secret-bearing entry."""
    text = content.strip() if isinstance(content, str) else ""
    if not text:
        return None
    text = text[:MAX_CORRECTION_CHARS]
    origin_provenance = _normalize_origin(origin)
    candidate: dict[str, Any] = {
        "candidate_id": integrity.short_id(
            "memcorr", {**dict(seed or {}), "source": source, "content": text}
        ),
        "candidate_type": candidate_type,
        "scope": CANDIDATE_SCOPE,
        "status": CANDIDATE_STATUS,
        "validated": False,
        "promotable": False,
        "content": text,
        "evidence_refs": [f"correction:{source}"],
        "learning_source": source,
        "created_at": now,
        EXPIRES_AT: timeutil.plus_minutes(now, ttl_minutes),
    }
    if correction_ref:
        candidate["correction_ref"] = correction_ref
    if origin_provenance is not None:
        candidate["origin"] = origin_provenance
    try:
        integrity.scan_for_secret_bearing_keys(candidate)
    except IntegrityError as exc:
        raise MemoryBlocked("SECRET_IN_CANDIDATE", str(exc)) from exc
    return candidate


LEARNING_ACTION = "mint_correction_candidate"


def build_learning_event(
    candidate: Mapping[str, Any],
    *,
    source: str,
    now: str,
    trace_id: str | None = None,
    operator_id: str | None = None,
) -> dict[str, Any]:
    """A tamper-evident memory event recording that a correction candidate was minted (M5a).

    On the memory-event stream, the retention-event precedent: a working-memory maintenance
    action is audited standalone, not on a run's task-bound audit chain. ALLOW-tier — this
    records that a candidate was captured, never that anything was trusted or promoted."""
    return stamped_event(
        LEARNING_EVENT_TYPE, action=LEARNING_ACTION,
        candidate_id=str(candidate.get("candidate_id", "")),
        learning_source=source, trace_id=trace_id, operator_id=operator_id,
        created_at=now,
    )


def retrieve_working_memory(
    assignment: Mapping[str, Any],
    store: Any,
    *,
    limit: int = MAX_RETRIEVED,
    now: str | None = None,
) -> list[dict[str, Any]]:
    """Return recent, unexpired ``task_working_memory`` candidates for context, or none.

    Read-only and governance-scoped: reads only when the assignment's ``readable_scopes``
    admits ``task_working_memory``; returns only CANDIDATE-status entries in that scope that
    are not in the assignment's ``prohibited_scopes`` and are **not expired** as of ``now``
    (default: current time); most-recent-first, capped at ``limit``. Expired candidates are
    never served as context even before the retention pass deletes them. Never mutates the store
    and never promotes anything. Propagates the store's fail-closed ``PersistenceError`` on a
    corrupt store (the caller turns it into a BLOCK)."""
    memory_scope = assignment.get("memory_scope", {}) if isinstance(assignment, Mapping) else {}
    readable = set(memory_scope.get("readable_scopes", []))
    prohibited = set(memory_scope.get("prohibited_scopes", []))
    if CANDIDATE_SCOPE not in readable or CANDIDATE_SCOPE in prohibited:
        return []

    stamp = now or timeutil.utc_now_iso()
    # CANDIDATE_SCOPE is already known to be absent from `prohibited` (guarded above), so
    # every selected entry — all in that one scope — is by construction not prohibited.
    entries = store.read_all()
    selected = [
        e for e in entries
        if isinstance(e, dict)
        and e.get("scope") == CANDIDATE_SCOPE
        and e.get("status") == CANDIDATE_STATUS
        and not is_expired(e, stamp)
    ]
    # Deterministic recency order; take the most recent `limit`.
    selected.sort(key=lambda e: (str(e.get("created_at", "")), str(e.get("candidate_id", ""))))
    return selected[-limit:] if limit > 0 else []


def retrieve_validated_memory(
    assignment: Mapping[str, Any],
    store: Any,
    *,
    limit: int = MAX_RETRIEVED,
) -> list[dict[str, Any]]:
    """Return promoted (VALIDATED) memory entries for context, or none.

    The read leg of the memory loop the governance already grants: the role's
    ``memory_policy.readable_scopes`` includes ``related_validated_memory``, so
    operator-promoted knowledge feeds later runs as context. Read-only and
    governance-scoped exactly like ``retrieve_working_memory`` (reads only when the
    assignment's ``readable_scopes`` admits the scope and it is not prohibited).
    VALIDATED memory carries no ``expires_at`` — retention never touches it (§12.4) —
    so there is no expiry filter; most-recent-first by promotion time, capped at
    ``limit``. Never mutates the store. Propagates the store's fail-closed
    ``PersistenceError`` on a corrupt store (the caller turns it into a BLOCK)."""
    memory_scope = assignment.get("memory_scope", {}) if isinstance(assignment, Mapping) else {}
    readable = set(memory_scope.get("readable_scopes", []))
    prohibited = set(memory_scope.get("prohibited_scopes", []))
    if VALIDATED_SCOPE not in readable or VALIDATED_SCOPE in prohibited:
        return []

    entries = store.read_validated()
    selected = [
        e for e in entries
        if isinstance(e, dict)
        and e.get("scope") == VALIDATED_SCOPE
        and e.get("status") == VALIDATED_STATUS
    ]
    # Deterministic recency order (promotion time); take the most recent `limit`.
    selected.sort(key=lambda e: (str(e.get("promoted_at", "")), str(e.get("validated_memory_id", ""))))
    return selected[-limit:] if limit > 0 else []


# --------------------------------------------------------------------------------------
# Core Candidate — the fourth rung of the memory ladder (policy §12.2/§12.6, architecture §8.8)
# --------------------------------------------------------------------------------------
# The ladder is Session -> Working -> Validated -> Core Candidate -> Thomas Core. The first
# three rungs existed; this one did not, so `Validated Memory -> Thomas Core` had no modelled
# step between "useful knowledge" and "Thomas's identity". That missing rung is the whole point
# of §12.6: a proposed Core change must be *visible as a proposal* before anyone argues about it.
#
# Naming, because two different things in this repo are called a candidate for the Core:
#   - a **Core Release Candidate** is a whole versioned Core document in the release lifecycle
#     (`THOMAS_CORE/releases/`, approve -> activate scripts). Not this.
#   - a **Core Candidate** (here) is a memory-level *proposal* that some Core rule might need to
#     change. It is a sentence with provenance, not a document.
# The scope string says `core_change_candidate` so the two can never be confused in a store.
#
# What it may do: record the proposal, carry its lineage, and be accepted or rejected by the
# operator. What it may never do: touch Thomas Core. Governance pins that from two directions —
# `memory_learning.protected_identity_or_policy_change: APPROVAL_REQUIRED` and
# `runtime_effect.core_activation_allowed: false` — so ACCEPTED here means "Thomas agrees this is
# worth carrying into a Core release", and the release lifecycle remains the only thing that can
# actually change a Core file. Accordingly every record carries `applied_to_core: False` and
# `promotion_effect: "NONE"`, and `decide_core_candidate` refuses to emit a decision that claims
# otherwise.
#
# Creation is operator-authored by construction (`proposed_by` + `rationale` are required, and
# the source must be an already-promoted VALIDATED entry). `automatic_runtime_promotion_allowed`
# is false, so no pipeline stage may mint one — a test pins the run-path modules against this
# module's Core-Candidate surface, the same tripwire the live order path uses.
CORE_CANDIDATE_STATUS = "CORE_CANDIDATE"
CORE_CANDIDATE_SCOPE = "core_change_candidate"
CORE_CANDIDATE_ACCEPTED = "ACCEPTED"
CORE_CANDIDATE_REJECTED = "REJECTED"
CORE_CANDIDATE_DECISIONS = (CORE_CANDIDATE_ACCEPTED, CORE_CANDIDATE_REJECTED)
# The four kinds policy §12.6 names, and nothing else: an open-ended type field would let this
# rung quietly become a second working memory.
CORE_CANDIDATE_TYPES = (
    "new_disposition",            # 새로운 성향
    "value_priority_change",      # 새로운 가치 우선순위
    "long_term_goal_change",      # 장기 목표 변경 가능성
    "risk_preference_change",     # 위험 선호 변화
)
MAX_CORE_CANDIDATE_CHARS = 2000
CORE_CANDIDATE_EVENT_TYPE = "core_change_candidate_event.v0"
CORE_CANDIDATE_PROPOSE_ACTION = "propose_core_candidate"
CORE_CANDIDATE_DECIDE_ACTION = "decide_core_candidate"


def build_core_candidate(
    validated_entry: Mapping[str, Any],
    *,
    candidate_type: str,
    rationale: str,
    proposed_by: str,
    now: str,
) -> dict[str, Any]:
    """Propose one Core Candidate from a VALIDATED memory entry. Operator action only.

    The source must be a genuine promoted VALIDATED entry — the rung directly below. That is
    the rule that keeps this from becoming a shortcut: a raw model finding cannot become a
    proposed change to Thomas's values without first surviving the M5b promotion someone had
    to say yes to. Fails closed (``MemoryBlocked``) on anything else, on an unknown
    ``candidate_type``, on a missing operator identity or rationale, and on a secret-bearing
    record. Carries the source entry's task lineage forward when it has one, so a proposal can
    always be traced back to the run that first suggested it.

    The returned record grants nothing and says so in fields, not only in prose.
    """
    if not isinstance(validated_entry, Mapping):
        raise MemoryBlocked("NOT_VALIDATED_MEMORY", "a Core Candidate must come from a VALIDATED entry")
    if (validated_entry.get("status") != VALIDATED_STATUS
            or validated_entry.get("scope") != VALIDATED_SCOPE):
        raise MemoryBlocked(
            "NOT_VALIDATED_MEMORY",
            "only a promoted VALIDATED memory entry may be proposed as a Core Candidate",
        )
    validated_id = validated_entry.get("validated_memory_id")
    content = validated_entry.get("content")
    if not (isinstance(validated_id, str) and validated_id):
        raise MemoryBlocked("INVALID_VALIDATED_MEMORY", "source entry is missing a validated_memory_id")
    if not (isinstance(content, str) and content.strip()):
        raise MemoryBlocked("INVALID_VALIDATED_MEMORY", "source entry is missing content")
    if candidate_type not in CORE_CANDIDATE_TYPES:
        raise MemoryBlocked(
            "UNKNOWN_CORE_CANDIDATE_TYPE",
            f"candidate_type must be one of {list(CORE_CANDIDATE_TYPES)}, got {candidate_type!r}",
        )
    if not (isinstance(proposed_by, str) and proposed_by.strip()):
        raise MemoryBlocked("MISSING_OPERATOR", "a Core Candidate requires an operator identity (proposed_by)")
    if not (isinstance(rationale, str) and rationale.strip()):
        raise MemoryBlocked(
            "MISSING_RATIONALE",
            "a Core Candidate requires a rationale — why this knowledge implies a Core change",
        )

    candidate = {
        "core_candidate_id": integrity.short_id(
            "corecand",
            {"validated_memory_id": validated_id, "candidate_type": candidate_type,
             "proposed_by": proposed_by.strip(), "created_at": now},
        ),
        "source_validated_memory_id": validated_id,
        "candidate_type": candidate_type,
        "scope": CORE_CANDIDATE_SCOPE,
        "status": CORE_CANDIDATE_STATUS,
        "content": content.strip()[:MAX_CORE_CANDIDATE_CHARS],
        "rationale": rationale.strip()[:MAX_CORE_CANDIDATE_CHARS],
        "proposed_by": proposed_by.strip(),
        "evidence_refs": [f"validated_memory:{validated_id}"],
        # Stated as data, not prose, so a reader of the store never has to infer it.
        "applied_to_core": False,
        "promotable": False,
        "promotion_effect": "NONE",
        "permission_expansion": False,
        "created_at": now,
    }
    # Lineage: the VALIDATED entry carries the originating task's provenance when the candidate
    # it was promoted from had one. Absent lineage is allowed here (older entries predate R5.4)
    # and is simply not fabricated.
    source_origin = validated_entry.get("source_origin")
    if isinstance(source_origin, Mapping):
        candidate["source_origin"] = dict(source_origin)
    try:
        integrity.scan_for_secret_bearing_keys(candidate)
    except IntegrityError as exc:
        raise MemoryBlocked("SECRET_IN_CORE_CANDIDATE", str(exc)) from exc
    return candidate


def decide_core_candidate(
    candidate: Mapping[str, Any],
    *,
    decision: str,
    decided_by: str,
    reason: str,
    now: str,
) -> dict[str, Any]:
    """Accept or reject a Core Candidate. Forward-only, terminal, operator action only.

    The store is append-only, so a decision is a new row whose status is no longer
    ``CORE_CANDIDATE`` — the :func:`~runtime.mvp_runtime.working_memory.find_core_candidate`
    lookup stops returning it, which is what makes the decision terminal (the PROMOTED-marker
    precedent). Fails closed on a non-live candidate, an unknown decision, or a missing
    operator identity/reason.

    ACCEPTED is a **review milestone, not an effect**: it records that Thomas wants this carried
    into a future Core release. It writes no Core file, and the returned record still says
    ``applied_to_core: False`` / ``promotion_effect: "NONE"`` — asserted here rather than
    assumed, so an accepted proposal can never read as an applied one.
    """
    if not isinstance(candidate, Mapping) or candidate.get("scope") != CORE_CANDIDATE_SCOPE:
        raise MemoryBlocked("NOT_A_CORE_CANDIDATE", "decision input must be a Core Candidate record")
    if candidate.get("status") != CORE_CANDIDATE_STATUS:
        raise MemoryBlocked(
            "CORE_CANDIDATE_ALREADY_DECIDED",
            f"candidate is {candidate.get('status')!r}; a decision is forward-only and terminal",
        )
    candidate_id = candidate.get("core_candidate_id")
    if not (isinstance(candidate_id, str) and candidate_id):
        raise MemoryBlocked("INVALID_CORE_CANDIDATE", "candidate is missing a core_candidate_id")
    if decision not in CORE_CANDIDATE_DECISIONS:
        raise MemoryBlocked(
            "UNKNOWN_CORE_CANDIDATE_DECISION",
            f"decision must be one of {list(CORE_CANDIDATE_DECISIONS)}, got {decision!r}",
        )
    if not (isinstance(decided_by, str) and decided_by.strip()):
        raise MemoryBlocked("MISSING_OPERATOR", "a Core Candidate decision requires an operator identity")
    if not (isinstance(reason, str) and reason.strip()):
        raise MemoryBlocked("MISSING_REASON", "a Core Candidate decision requires an operator reason")

    decided = {
        **dict(candidate),
        "status": decision,
        "decided_by": decided_by.strip(),
        "decision_reason": reason.strip()[:MAX_CORE_CANDIDATE_CHARS],
        "decided_at": now,
        # Re-stated on the decision row itself. An ACCEPTED row is the one a later reader is
        # most likely to mistake for "this is in the Core now".
        "applied_to_core": False,
        "promotion_effect": "NONE",
        "permission_expansion": False,
    }
    try:
        integrity.scan_for_secret_bearing_keys(decided)
    except IntegrityError as exc:
        raise MemoryBlocked("SECRET_IN_CORE_CANDIDATE", str(exc)) from exc
    return decided


def build_core_candidate_event(
    candidate: Mapping[str, Any],
    *,
    action: str,
    now: str,
    operator_id: str | None = None,
) -> dict[str, Any]:
    """A tamper-evident memory event for a Core Candidate proposal or decision.

    The retention/learning-event precedent: an operator memory action off the run path is
    audited standalone on the memory-event stream, not on a task-bound audit chain. No new
    ledger stream and no new event type — this rung changes nothing, so it earns no heavier
    machinery than the rung that deletes data.
    """
    return stamped_event(
        CORE_CANDIDATE_EVENT_TYPE, action=action,
        core_candidate_id=str(candidate.get("core_candidate_id", "")),
        source_validated_memory_id=str(candidate.get("source_validated_memory_id", "")),
        candidate_type=str(candidate.get("candidate_type", "")),
        status=str(candidate.get("status", "")),
        applied_to_core=False, promotion_effect="NONE",
        operator_id=operator_id, created_at=now,
    )


MEMORY_EVENT_TYPE = "working_memory_retention_event.v0"


def build_retention_event(removed: list[Mapping[str, Any]], *, now: str, reason: str) -> dict[str, Any]:
    """Build a tamper-evident memory-retention event for the durable ledger (policy §15)."""
    return stamped_event(
        MEMORY_EVENT_TYPE, action="prune_working_memory",
        removed_count=len(removed),
        removed_candidate_ids=[str(e.get("candidate_id", "")) for e in removed],
        reason=reason, created_at=now,
    )


def prune_working_memory(store: Any, ledger: Any, *, now: str, reason: str = "") -> dict[str, Any]:
    """Delete expired working-memory candidates and audit the deletion. Returns a summary.

    ``store`` is a ``WorkingMemoryStore`` (needs ``prune_expired``); ``ledger`` is a
    ``LedgerStore`` (needs ``append_memory_event``) or ``None``. A retention event is recorded
    only when something was actually deleted (nothing removed = nothing to audit). Duck-typed to
    avoid importing the stores here (``working_memory`` imports this module)."""
    removed = store.prune_expired(now)
    event = build_retention_event(removed, now=now, reason=reason)
    if ledger is not None and removed:
        ledger.append_memory_event(event)
    return {"removed": removed, "removed_count": len(removed), "event": event}


def promote_candidate(
    candidate: Mapping[str, Any],
    *,
    promoted_by: str,
    reason: str,
    now: str,
) -> dict[str, Any]:
    """Promote a working-memory CANDIDATE to VALIDATED memory. Explicit operator action only.

    Governance: promotion of validated low-risk operational knowledge is EXECUTE_AND_REPORT and
    ``automatic_runtime_promotion_allowed`` is false — so this is NEVER called from the run
    pipeline; only the operator promotion tool invokes it, with an operator identity + reason
    (the "report"). Fails closed (``MemoryBlocked``) unless the input is a genuine
    CANDIDATE-status working-memory entry with content, and on a secret-bearing entry.
    """
    if not isinstance(candidate, Mapping):
        raise MemoryBlocked("NOT_A_CANDIDATE", "promotion input must be a candidate mapping")
    if candidate.get("status") != CANDIDATE_STATUS or candidate.get("scope") != CANDIDATE_SCOPE:
        raise MemoryBlocked("NOT_A_CANDIDATE", "only a task_working_memory CANDIDATE may be promoted")
    candidate_id = candidate.get("candidate_id")
    content = candidate.get("content")
    if not (isinstance(candidate_id, str) and candidate_id):
        raise MemoryBlocked("INVALID_CANDIDATE", "candidate is missing a candidate_id")
    if not (isinstance(content, str) and content.strip()):
        raise MemoryBlocked("INVALID_CANDIDATE", "candidate is missing content")
    if not (isinstance(promoted_by, str) and promoted_by.strip()):
        raise MemoryBlocked("MISSING_OPERATOR", "promotion requires an operator identity (promoted_by)")
    if not (isinstance(reason, str) and reason.strip()):
        raise MemoryBlocked("MISSING_REASON", "promotion requires an operator reason (EXECUTE_AND_REPORT)")

    validated = {
        "validated_memory_id": integrity.short_id(
            "valmem", {"candidate_id": candidate_id, "promoted_by": promoted_by, "promoted_at": now}
        ),
        "source_candidate_id": candidate_id,
        "candidate_type": candidate.get("candidate_type", "reusable_knowledge"),
        "scope": VALIDATED_SCOPE,
        "status": VALIDATED_STATUS,
        "disposition": PROMOTION_DISPOSITION,
        "content": content.strip(),
        "evidence_refs": [f"working_memory:{candidate_id}"],
        "promoted_by": promoted_by.strip(),
        "promotion_reason": reason.strip(),
        "promoted_at": now,
    }
    # Carry the originating-task lineage forward (R5.4): the promotion audit event is anchored
    # to this same origin. Present only when the candidate was stamped with provenance.
    origin = candidate.get("origin")
    if isinstance(origin, Mapping):
        validated["source_origin"] = dict(origin)
    # M5c: carry the correction marker forward so a promoted M5a correction feeds back to a
    # later run *as a correction to apply* ([V#]), not as generic reusable knowledge. Additive
    # and present only for a correction candidate (``build_correction_candidate`` stamps it).
    learning_source = candidate.get("learning_source")
    if isinstance(learning_source, str) and learning_source:
        validated["learning_source"] = learning_source
        correction_ref = candidate.get("correction_ref")
        if isinstance(correction_ref, str) and correction_ref:
            validated["correction_ref"] = correction_ref
    try:
        integrity.scan_for_secret_bearing_keys(validated)
    except IntegrityError as exc:
        raise MemoryBlocked("SECRET_IN_VALIDATED", str(exc)) from exc
    return validated
