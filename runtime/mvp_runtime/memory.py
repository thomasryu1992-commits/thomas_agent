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

from .errors import MemoryBlocked

CANDIDATE_STATUS = "CANDIDATE"          # never VALIDATED / CORE
CANDIDATE_SCOPE = "task_working_memory"
PREFERRED_TYPE = "reusable_knowledge"
MAX_CANDIDATES = 5
MAX_RETRIEVED = 5

VALIDATED_STATUS = "VALIDATED"
VALIDATED_SCOPE = "related_validated_memory"
PROMOTION_DISPOSITION = "EXECUTE_AND_REPORT"   # governance tier for validated-knowledge promotion


ORIGIN_FIELDS = ("task_id", "task_revision", "trace_id", "core_context_binding_id", "data_sensitivity")


def _normalize_origin(origin: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Return a candidate's origin provenance, or ``None`` when no usable origin was supplied.

    Fail-closed: an origin that is present but incomplete raises ``MemoryBlocked`` rather than
    stamping a half-populated lineage that cannot be audited later. A candidate with no origin
    at all is allowed (older/opt-out callers) — its promotion simply fails closed downstream."""
    if origin is None:
        return None
    provenance = {field: origin.get(field) for field in ORIGIN_FIELDS}
    missing = [k for k in ("task_id", "trace_id", "core_context_binding_id", "data_sensitivity")
               if not (isinstance(provenance[k], str) and provenance[k])]
    if not (isinstance(provenance["task_revision"], int) and provenance["task_revision"] >= 1):
        missing.append("task_revision")
    if missing:
        raise MemoryBlocked("INVALID_ORIGIN", f"candidate origin provenance is incomplete: {sorted(missing)}")
    return provenance


def build_memory_candidates(
    analysis: Mapping[str, Any],
    assignment: Mapping[str, Any],
    *,
    now: str,
    seed: Mapping[str, Any] | None = None,
    origin: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Propose working-memory candidates from an analysis, or none.

    Fail-closed to an empty list when the assignment does not permit candidate creation or
    declares no allowed candidate types. Raises ``MemoryBlocked`` on a secret-bearing
    candidate. Candidates are proposals: ``status=CANDIDATE``, ``validated=False``,
    ``promotable=False`` — never validated, core, or auto-promoted.

    ``origin`` (optional) stamps each candidate with the identity of the task that produced it
    (``task_id``/``task_revision``/``trace_id``/``core_context_binding_id``/``data_sensitivity``).
    That provenance is what lets a later, off-run-path promotion be audited against the real
    originating task (R5.4). It never affects ``candidate_id`` (derived from ``seed``), so
    stamping provenance is deterministic and leaves existing ids unchanged."""
    origin_provenance = _normalize_origin(origin)
    memory_scope = assignment.get("memory_scope", {}) if isinstance(assignment, Mapping) else {}
    if not memory_scope.get("memory_candidate_creation_allowed"):
        return []
    allowed = [t for t in memory_scope.get("allowed_candidate_types", []) if isinstance(t, str) and t]
    if not allowed:
        return []
    candidate_type = PREFERRED_TYPE if PREFERRED_TYPE in allowed else sorted(allowed)[0]

    findings = [f.strip() for f in (analysis.get("key_findings") or [])
                if isinstance(f, str) and f.strip()][:MAX_CANDIDATES]
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


def retrieve_working_memory(
    assignment: Mapping[str, Any],
    store: Any,
    *,
    limit: int = MAX_RETRIEVED,
) -> list[dict[str, Any]]:
    """Return recent ``task_working_memory`` candidates for context, or none.

    Read-only and governance-scoped: reads only when the assignment's ``readable_scopes``
    admits ``task_working_memory``; returns only CANDIDATE-status entries in that scope that
    are not in the assignment's ``prohibited_scopes``; most-recent-first, capped at ``limit``.
    Never mutates the store and never promotes anything. Propagates the store's fail-closed
    ``PersistenceError`` on a corrupt store (the caller turns it into a BLOCK)."""
    memory_scope = assignment.get("memory_scope", {}) if isinstance(assignment, Mapping) else {}
    readable = set(memory_scope.get("readable_scopes", []))
    prohibited = set(memory_scope.get("prohibited_scopes", []))
    if CANDIDATE_SCOPE not in readable or CANDIDATE_SCOPE in prohibited:
        return []

    # CANDIDATE_SCOPE is already known to be absent from `prohibited` (guarded above), so
    # every selected entry — all in that one scope — is by construction not prohibited.
    entries = store.read_all()
    selected = [
        e for e in entries
        if isinstance(e, dict)
        and e.get("scope") == CANDIDATE_SCOPE
        and e.get("status") == CANDIDATE_STATUS
    ]
    # Deterministic recency order; take the most recent `limit`.
    selected.sort(key=lambda e: (str(e.get("created_at", "")), str(e.get("candidate_id", ""))))
    return selected[-limit:] if limit > 0 else []


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
    try:
        integrity.scan_for_secret_bearing_keys(validated)
    except IntegrityError as exc:
        raise MemoryBlocked("SECRET_IN_VALIDATED", str(exc)) from exc
    return validated
