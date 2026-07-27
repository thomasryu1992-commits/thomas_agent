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

from .authority import rank_of, stricter_risk
from .errors import PlannerBlocked
from .paths import repo_root as _repo_root

# MVP classification: the only use case is internal, read-only business analysis.
MVP_EXECUTION_MODE = "AGENT"          # judgment/interpretation, not a deterministic program
MVP_COMPLEXITY = "NORMAL"
# The BASE risk of the specialist's own action — not the task's final risk.
#
# GREEN is correct here and is not a placeholder: policy §10 classifies the ACTION, and lists
# "내부 분석" among its GREEN examples. What was wrong was treating it as the whole answer.
# §10 also says to evaluate every perspective and take the HIGHEST, and a run plans more than
# the analysis: R3 search, R7 validation, R7.2 triage (all GREEN) and the R8 controlled write
# (YELLOW). So a run that wrote a file was recorded as a plain read-only analysis.
# ``classify_task`` now folds in the risk of the other actions the run will actually plan.
MVP_RISK_LEVEL = "GREEN"
MVP_PERMISSION_SCOPE = "INTERNAL_ANALYSIS"  # governance effect class (ALLOW disposition)
MVP_REQUIRED_PERMISSION_LEVEL = "P2"  # ANALYZE — internal read-only analysis (least privilege)
MVP_REQUIRED_CAPABILITIES = ("research", "analysis")

# Architecture §8.5 — routing to more than one specialist Role.
#
# A request kind names the CAPABILITIES the work needs; it never names a Role. That layering is
# the point: the Role Registry stays the only thing that decides which Role covers a capability
# set, so activating, renaming or retiring a Role changes routing without touching this table.
#
# Derived from an explicit marker rather than inferred from the request text. Inferring "this
# looks like a translation" is a guess, and a wrong guess silently routes work to a Role with a
# different output contract and different quality criteria — the reader would get a confident
# answer of the wrong shape. The `!중요` / `--important` precedent already established that an
# explicit operator marker is how this runtime takes a routing signal from a person.
#
# Each set must be covered by exactly one active routable Role or `select_role` fails closed
# (NO_ROUTABLE_ROLE / AMBIGUOUS_ROLE); `test_every_request_kind_resolves_to_exactly_one_role`
# pins that against the live registry, so activating a Role whose capabilities overlap an
# existing kind fails in CI rather than at run time.
REQUEST_KIND_ANALYSIS = "analysis"
# The Role the analysis kind resolves to. Named once so the pipeline can ask "is this the
# business analyst?" without re-deriving it from a capability set.
DEFAULT_SPECIALIST_ROLE_ID = "general.specialist"
REQUEST_KIND_CAPABILITIES: dict[str, tuple[str, ...]] = {
    REQUEST_KIND_ANALYSIS: MVP_REQUIRED_CAPABILITIES,          # general.specialist
    "research": ("evidence_collection", "source_comparison"),  # research.general
    "translation": ("translation", "ambiguity_disclosure"),    # translation.general
}
_READ_ONLY_CONSTRAINT = "no_external_action"

# R7: capabilities that select the independent validator role (validation.independent).
# These are validator-only capabilities, so select_role resolves them uniquely; the
# validator's ceiling is P2 (ANALYZE) — it reviews, it never creates or acts.
VALIDATOR_REQUIRED_CAPABILITIES = (
    "objective_alignment_check", "evidence_check", "logic_check", "output_contract_check",
)
VALIDATOR_REQUIRED_PERMISSION_LEVEL = "P2"


def load_role_definition(root: Path, role: Mapping[str, Any]) -> dict[str, Any]:
    """The hash-verified full Role Definition (the resolved registry view deliberately
    carries only routing fields). One loader for the whole runtime — assignment.py, the
    candidate trial and the normal routing path all use this one — so no caller can read a
    definition the registry does not vouch for."""
    try:
        return registry_resolution.load_markdown_yaml_front_matter(
            path=root / str(role["definition_path"]),
            expected_hash=role.get("definition_sha256"),
        )
    except registry_resolution.RegistryResolutionError as exc:
        raise PlannerBlocked("ROLE_DEFINITION_INVALID", str(exc)) from exc


def role_output_spec(definition: Mapping[str, Any]) -> dict[str, str]:
    """The role's declared ``role_specific_output`` contract: {field: type}. The single
    source for the role's prompt, the worker's output mapping, and the validation
    requirement — the Role Definition, not any calling module, owns what the role returns."""
    contract = definition.get("output_contract", {}).get("role_specific_output", {})
    if not isinstance(contract, Mapping) or not contract:
        raise PlannerBlocked(
            "NO_ROLE_OUTPUT_CONTRACT",
            f"role {definition.get('role_id')} declares no role_specific_output contract",
        )
    return {str(k): str(v) for k, v in contract.items()}


def capabilities_for_request_kind(request_kind: str | None) -> tuple[str, tuple[str, ...]]:
    """``(kind, required_capabilities)`` for a request kind. Fails closed on an unknown one.

    ``None`` resolves to the analysis kind — the runtime's original behavior, so a caller that
    passes nothing routes exactly where it did before §8.5. An *unknown* kind is refused rather
    than defaulted: silently analyzing a request someone asked to have translated is the wrong
    answer delivered confidently, which is worse than a refusal naming the kinds that exist."""
    if request_kind is None:
        return REQUEST_KIND_ANALYSIS, REQUEST_KIND_CAPABILITIES[REQUEST_KIND_ANALYSIS]
    capabilities = REQUEST_KIND_CAPABILITIES.get(request_kind)
    if capabilities is None:
        raise PlannerBlocked(
            "UNKNOWN_REQUEST_KIND",
            f"request kind {request_kind!r} is not routable; known kinds: "
            f"{sorted(REQUEST_KIND_CAPABILITIES)}",
        )
    return request_kind, capabilities


def classify_task(
    task: Mapping[str, Any],
    *,
    request_kind: str | None = None,
    planned_action_risks: Sequence[str] = (),
) -> dict[str, Any]:
    """Classify a RECEIVED/UNCLASSIFIED task. Pure and fail-closed.

    Returns a decision dict (not a mutated task): ``classification``, ``authority``,
    and ``required_capabilities``. Raises ``PlannerBlocked`` if the task is not in the
    expected pre-classification state or is outside the read-only MVP scope.

    ``planned_action_risks`` are the risk levels of the *other* governed actions this run
    will plan besides the specialist's analysis (today: the R8 controlled write). Policy §10
    says to take the highest across perspectives, so the task's ``risk_level`` is the
    strictest of the base level and these — derived, never asserted.

    The derivation is **monotone by construction**: :func:`~runtime.mvp_runtime.authority.
    stricter_risk` can only return something at or above ``MVP_RISK_LEVEL``, and an
    unrecognized level resolves the whole reduction to ORANGE rather than being skipped. So a
    caller can raise the review requirement and cannot lower it — which is what makes it safe
    to feed this into the R7.1 reviewer decision downstream.

    Deliberately still constant, each for a reason rather than by omission:

    * ``complexity`` — nothing reads it, and deriving it from free request text would be a
      guess. §10's rule for a judgement made on insufficient information is to *not lower*
      the classification, so the honest move is to leave it until something needs it.
    ``request_kind`` (§8.5) selects the capabilities the work needs — never a Role; the Role
    Registry decides which Role covers them. ``None`` keeps the original analysis routing.

    ``complexity`` is deliberately still constant: nothing reads it, and deriving it from free
    request text would be a guess. §10's rule for a judgement made on insufficient information
    is to *not lower* the classification, so the honest move is to leave it until a consumer
    exists.
    """
    if not isinstance(task, Mapping):
        raise PlannerBlocked("INVALID_TASK", "task must be a mapping")
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

    kind, required_capabilities = capabilities_for_request_kind(request_kind)
    priority = classification.get("priority", "NORMAL")
    risk_level = stricter_risk(MVP_RISK_LEVEL, *planned_action_risks)
    reasons = [
        "internal_read_only_analysis",
        "judgment_required_not_deterministic_program",
    ]
    if kind != REQUEST_KIND_ANALYSIS:
        reasons.append(f"request_kind_{kind}")
    if risk_level != MVP_RISK_LEVEL:
        # Say what raised it. A classification that differs from the base without explaining
        # why is the kind of record someone later "corrects" back to GREEN.
        reasons.append(f"raised_to_{risk_level.lower()}_by_planned_action_risk")
    return {
        "classification": {
            "classification_status": "CLASSIFIED",
            "execution_mode": MVP_EXECUTION_MODE,
            "complexity": MVP_COMPLEXITY,
            "priority": priority,
            "risk_level": risk_level,
            "classification_reasons": reasons,
        },
        "authority": {
            "required_permission_level": MVP_REQUIRED_PERMISSION_LEVEL,
            "authority_reason": "Internal read-only analysis within the assigned Task scope.",
        },
        "required_capabilities": list(required_capabilities),
        "request_kind": kind,
        "permission_scope": MVP_PERMISSION_SCOPE,
    }


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


def select_candidate_role(
    resolved_roles: Mapping[str, Any],
    *,
    role_id: str,
    version: str | None = None,
) -> dict[str, Any]:
    """Select one CANDIDATE role for an explicit trial assignment — never normal routing.

    The inverse gate of :func:`select_role`: the entry must exist, its status must be
    exactly ``candidate``, and it must be non-routable. When ``version`` is given it must
    match exactly (the Candidate Trial Policy's ``exact_candidate_role_version``). An
    ACTIVE role is refused with its own code — a trial of an already-activated role is a
    contradiction, and the caller should route normally instead. Fails closed otherwise.
    """
    for role in resolved_roles.get("roles", []):
        if role.get("role_id") != role_id:
            continue
        status = role.get("status")
        if status == "active":
            raise PlannerBlocked(
                "ROLE_ALREADY_ACTIVE", f"role {role_id} is active; a candidate trial does not apply"
            )
        if status != "candidate" or role.get("routable") is not False:
            raise PlannerBlocked(
                "NOT_A_CANDIDATE",
                f"role {role_id} is {status!r} (routable={role.get('routable')!r}); "
                "only a non-routable candidate role can be trialled",
            )
        if version is not None and role.get("version") != version:
            raise PlannerBlocked(
                "CANDIDATE_VERSION_MISMATCH",
                f"role {role_id} is at version {role.get('version')}, trial requires exactly {version}",
            )
        return role
    raise PlannerBlocked("UNKNOWN_ROLE", f"no role with id {role_id} in the registry")


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
    required_rank = rank_of(required_permission_level)
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
        ceiling_rank = rank_of(ceiling)
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
