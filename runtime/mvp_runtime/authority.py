"""Single authority for the permission-level order, the authority invariant,
and the canonical no-grant effect blocks of the live MVP runtime.

Before this module, three safety-critical encodings were copy-pasted across the
runtime and had already started to drift:

  - the P0-P6 rank map (``_LEVEL_RANK`` in planner.py / permission.py / assignment.py),
  - the authority invariant ``required <= effective <= granted <= ceiling``,
  - the REVIEW_ONLY / EVIDENCE_ONLY effect blocks (every grant flag false).

This module now OWNS all three for ``runtime/mvp_runtime``. One concept = one
authority: modules import from here and keep no local copies.

Scope and intentional divergence
--------------------------------
The read-only replay kernel (``runtime/read_only_kernel/``) keeps its OWN copies
(``constants.AUTHORITY_ORDER``, ``preflight.py``) and enforces a STRICTER replay
invariant: ``Task required == Assignment required <= effective <= granted <=
ceiling == Role ceiling <= P3``. The kernel is frozen (do not modify) and off the
live path; that divergence is deliberate — this module is the authority for the
live runtime only, the kernel remains its own authority for the replay path.

The committed closed schemas stay the authority for record *shape* (their
``const`` values are a governance surface, not code). The factories here align
the Python to the schemas; they do not replace them — every produced record is
still schema-validated by its builder.
"""

from __future__ import annotations

from typing import Any

# Authority levels, least (P0) to most (P6) privileged. Governance semantics live
# in governance/GOVERNANCE_POLICY.yaml; this map only fixes the total order.
LEVEL_RANK: dict[str, int] = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4, "P5": 5, "P6": 6}

REVIEW_ONLY = "REVIEW_ONLY"
EVIDENCE_ONLY = "EVIDENCE_ONLY"

# Risk levels, least to most severe. Policy §10 defines both the order and the disposition
# each level defaults to, and says to take the HIGHEST across perspectives — so a total order
# and a "stricter wins" reducer are what the policy actually asks code to have.
RISK_ORDER: dict[str, int] = {"GREEN": 0, "YELLOW": 1, "ORANGE": 2, "RED": 3}

# Policy §10's risk -> default disposition mapping, read backwards: the LEAST risk level that
# can honestly carry a given disposition.
#
# Read backwards on purpose. Forwards ("this risk implies that disposition") would make risk a
# second source of permission, which §10 explicitly forbids — "Risk does not independently
# prove Authority sufficiency, grant Permission…". Backwards it grants nothing and only catches
# a contradiction: a decision whose disposition says it changes internal state while its own
# risk_level says it is a plain read. That contradiction is not hypothetical — it is how the
# R8 workspace write shipped, GREEN and EXECUTE_AND_REPORT at once, until this floor was added.
MINIMUM_RISK_FOR_DISPOSITION: dict[str, str] = {
    "ALLOW": "GREEN",                 # 읽기 · 분석 · 임시 생성
    "EXECUTE_AND_REPORT": "YELLOW",   # 복구 가능한 내부 영향 (내부 문서 생성 …)
    "APPROVAL_REQUIRED": "ORANGE",    # 중요한 외부 영향
    "BLOCK": "RED",                   # 허용 불가
}


def risk_rank(level: Any) -> int | None:
    """The severity rank of a risk level, or None if it is not a known level."""
    return RISK_ORDER.get(level) if isinstance(level, str) else None


def stricter_risk(*levels: Any) -> str:
    """The most severe of the given risk levels (policy §10: use the highest).

    Fails closed: an unknown or missing level is not silently skipped, it resolves the whole
    reduction to ORANGE — §10's own rule for classifying with insufficient information
    ("판단에 필요한 정보가 부족하면 ORANGE로 분류한다"). Callers therefore cannot lower a
    classification by passing something unrecognized.
    """
    if not levels:
        return "GREEN"
    if any(risk_rank(level) is None for level in levels):
        return "ORANGE"
    return max(levels, key=lambda level: RISK_ORDER[level])


def risk_floor_for_disposition(disposition: Any) -> str:
    """The least risk level a decision with this disposition may honestly declare.

    An unknown disposition floors at RED rather than at GREEN: not recognizing what an action
    does is never a reason to record it as harmless."""
    if not isinstance(disposition, str):
        return "RED"
    return MINIMUM_RISK_FOR_DISPOSITION.get(disposition, "RED")


def rank_of(level: Any) -> int | None:
    """Rank of a P0-P6 permission level, or None for anything else."""
    if not isinstance(level, str):
        return None
    return LEVEL_RANK.get(level)


def authority_invariant_holds(required: str, effective: str, granted: str, ceiling: str) -> bool:
    """Live-runtime authority invariant: required <= effective <= granted <= ceiling.

    Raises ``ValueError`` if any level is not P0-P6 so an unknown level can never
    satisfy the invariant by accident; callers map that to their own fail-closed
    reason codes.
    """
    ranks = [rank_of(x) for x in (required, effective, granted, ceiling)]
    if any(r is None for r in ranks):
        raise ValueError(f"authority levels must be P0..P6: {required!r}, {effective!r}, {granted!r}, {ceiling!r}")
    return ranks[0] <= ranks[1] <= ranks[2] <= ranks[3]


def permission_decision_runtime_effect() -> dict[str, Any]:
    """``runtime_effect`` block for a permission_decision record: REVIEW_ONLY, nothing enabled.

    ALLOW is never an executor token — a fresh dict per record, all flags false.
    """
    return {
        "mode": REVIEW_ONLY,
        "executor_handoff_allowed": False,
        "external_execution_allowed": False,
        "financial_execution_allowed": False,
        "runtime_mutation_allowed": False,
        "tool_enablement_allowed": False,
        "program_enablement_allowed": False,
        "permission_expansion_allowed": False,
    }


def validation_result_permission_boundary() -> dict[str, Any]:
    """``permission_boundary`` block for validation_result.v0.1: validation only reports."""
    return {
        "grants_permission": False,
        "grants_approval": False,
        "grants_authority": False,
        "grants_execution": False,
        "grants_activation": False,
        "mutates_subject": False,
    }


def validation_result_runtime_effect() -> dict[str, Any]:
    """``runtime_effect`` block for validation_result.v0.1: REVIEW_ONLY, grants nothing."""
    return {
        "mode": REVIEW_ONLY,
        "grants_permission": False,
        "grants_approval": False,
        "grants_authority": False,
        "grants_execution": False,
        "grants_activation": False,
        "executor_handoff_allowed": False,
        "side_effects_allowed": False,
        "runtime_mutation_allowed": False,
    }


def audit_event_runtime_effect() -> dict[str, Any]:
    """``runtime_effect`` block for audit_event.v0.1: EVIDENCE_ONLY — audit is not Authority."""
    return {
        "mode": EVIDENCE_ONLY,
        "grants_permission": False,
        "grants_approval": False,
        "grants_authority": False,
        "grants_execution": False,
        "grants_activation": False,
        "mutates_runtime": False,
    }
