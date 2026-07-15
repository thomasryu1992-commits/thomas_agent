"""R2.2 Thomas Prime Planner — classification + role selection (first increment).

Thomas Prime interprets a RECEIVED task, classifies it, and selects the single
routable specialist Role. It does not perform the specialist work itself.

This module holds the two pure planning decisions that need no live governance
state yet:
  - ``classify_task``: RECEIVED/UNCLASSIFIED task -> classification decision
    (execution_mode / complexity / priority / risk / required authority / required
    capabilities). MVP scope is internal read-only analysis, so it fails closed on
    anything that would require external action.
  - ``select_role``: pick the active, routable Role whose capabilities cover the
    required set and whose permission ceiling admits the required level. For the
    MVP this resolves to ``general.specialist``.

Role resolution reuses ``runtime.registry_resolution.resolve_role_registry``, which
loads Role Definitions and fails closed on a definition-hash mismatch. Binding the
task to the active Core, the PermissionDecision record, and the role_assignment
assembly are the next increments (they consume live governance state).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from runtime import registry_resolution
from runtime.registry_resolution import RegistryResolutionError

from .errors import PlannerBlocked

# Authority levels, least to most privileged. Ranking is used to check that a
# Role's permission ceiling admits the task's required level.
_LEVEL_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4, "P5": 5, "P6": 6}

# MVP classification: the only use case is internal, read-only business analysis.
MVP_EXECUTION_MODE = "AGENT"          # judgment/interpretation, not a deterministic program
MVP_COMPLEXITY = "NORMAL"
MVP_RISK_LEVEL = "GREEN"              # internal analysis, no external/financial effect
MVP_REQUIRED_PERMISSION_LEVEL = "P3"  # produces an analysis + recommendation draft (CREATE)
MVP_REQUIRED_CAPABILITIES = ("research", "analysis")
_READ_ONLY_CONSTRAINT = "no_external_action"


def classify_task(task: Mapping[str, Any]) -> dict[str, Any]:
    """Classify a RECEIVED/UNCLASSIFIED task. Pure and fail-closed.

    Returns a decision dict (not a mutated task): ``classification``, ``authority``,
    and ``required_capabilities``. Raises ``PlannerBlocked`` if the task is not in the
    expected pre-classification state or is outside the read-only MVP scope.
    """
    lifecycle = task.get("lifecycle", {})
    classification = task.get("classification", {})
    if lifecycle.get("status") != "RECEIVED":
        raise PlannerBlocked("NOT_RECEIVED", "classify_task expects a RECEIVED task")
    if classification.get("classification_status") != "UNCLASSIFIED":
        raise PlannerBlocked("ALREADY_CLASSIFIED", "task is already classified")

    constraints = task.get("scope", {}).get("constraints", [])
    if _READ_ONLY_CONSTRAINT not in constraints:
        # The MVP only handles read-only analysis; refuse to classify anything that
        # was not intaken with the no-external-action constraint.
        raise PlannerBlocked(
            "OUT_OF_MVP_SCOPE", f"task scope must carry the '{_READ_ONLY_CONSTRAINT}' constraint"
        )

    priority = classification.get("priority", "NORMAL")
    return {
        "classification": {
            "classification_status": "CLASSIFIED",
            "execution_mode": MVP_EXECUTION_MODE,
            "complexity": MVP_COMPLEXITY,
            "priority": priority,
            "risk_level": MVP_RISK_LEVEL,
            "classification_reasons": [
                "internal_read_only_analysis",
                "judgment_required_not_deterministic_program",
            ],
        },
        "authority": {
            "required_permission_level": MVP_REQUIRED_PERMISSION_LEVEL,
            "authority_reason": "Internal analysis and recommendation draft (read-only).",
        },
        "required_capabilities": list(MVP_REQUIRED_CAPABILITIES),
    }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_resolved_roles(repo_root: Path | None = None) -> dict[str, Any]:
    """Load and resolve the Role Registry (definition fields + hash verification).

    Fails closed via RegistryResolutionError -> PlannerBlocked on a missing/mismatched
    Role Definition.
    """
    root = repo_root if repo_root is not None else _repo_root()
    try:
        registry = yaml.safe_load((root / "03_ROLE_CONTRACTS" / "ROLE_REGISTRY.yaml").read_text(encoding="utf-8"))
        policy = yaml.safe_load((root / "governance" / "GOVERNANCE_POLICY.yaml").read_text(encoding="utf-8"))
        return registry_resolution.resolve_role_registry(
            repo_root=root, registry=registry, governance_policy=policy
        )
    except (OSError, yaml.YAMLError) as exc:
        raise PlannerBlocked("REGISTRY_UNAVAILABLE", f"cannot load role registry/policy: {exc}") from exc
    except RegistryResolutionError as exc:
        raise PlannerBlocked("ROLE_DEFINITION_INVALID", str(exc)) from exc


def select_role(
    resolved_roles: Mapping[str, Any],
    *,
    required_capabilities: Sequence[str],
    required_permission_level: str,
) -> dict[str, Any]:
    """Select the single active, routable Role that satisfies the requirements.

    A Role qualifies when: status == active, routable is True, its capabilities cover
    every required capability, and its permission ceiling admits the required level.
    Fails closed if no Role (or an ambiguous set) qualifies.
    """
    required_rank = _LEVEL_RANK.get(required_permission_level)
    if required_rank is None:
        raise PlannerBlocked("INVALID_REQUIRED_LEVEL", f"unknown permission level {required_permission_level!r}")
    needed = set(required_capabilities)

    candidates = []
    for role in resolved_roles.get("roles", []):
        if role.get("status") != "active" or role.get("routable") is not True:
            continue
        capabilities = set(role.get("capabilities", []))
        if not needed.issubset(capabilities):
            continue
        ceiling = role.get("permission_ceiling")
        ceiling_rank = _LEVEL_RANK.get(ceiling)
        if ceiling_rank is None or ceiling_rank < required_rank:
            continue
        candidates.append(role)

    if not candidates:
        raise PlannerBlocked(
            "NO_ROUTABLE_ROLE",
            f"no active routable role covers {sorted(needed)} at ceiling >= {required_permission_level}",
        )
    if len(candidates) > 1:
        ids = sorted(r.get("role_id") for r in candidates)
        raise PlannerBlocked("AMBIGUOUS_ROLE", f"multiple roles qualify: {ids}")
    return candidates[0]
