#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []


def error(message: str) -> None:
    ERRORS.append(message)


def load_yaml(
    rel: str,
    *,
    required: bool = True,
) -> dict[str, Any]:
    path = ROOT / rel

    if not path.exists():
        if required:
            error(f"Missing file: {rel}")
        return {}

    try:
        data = yaml.safe_load(
            path.read_text(encoding="utf-8")
        )
    except Exception as exc:
        error(
            f"{rel}: YAML parse failed: {exc}"
        )
        return {}

    if not isinstance(data, dict):
        error(
            f"{rel}: expected YAML mapping"
        )
        return {}

    return data


def read_text(
    rel: str,
    *,
    required: bool = True,
) -> str:
    path = ROOT / rel

    if not path.exists():
        if required:
            error(f"Missing file: {rel}")
        return ""

    return path.read_text(
        encoding="utf-8"
    )


def require_tokens(
    rel: str,
    tokens: list[str],
) -> None:
    text = read_text(rel)

    for token in tokens:
        if token not in text:
            error(
                f"{rel}: missing required token: "
                f"{token}"
            )


def reject_keys(
    data: dict[str, Any],
    rel: str,
    keys: list[str],
) -> None:
    for key in keys:
        if key in data:
            error(
                f"{rel}: dynamic lifecycle key "
                f"must not be in semantic Core: {key}"
            )


def yaml_path(
    data: Any,
    path: str,
) -> Any:
    current = data

    for part in path.split("."):
        if (
            not isinstance(current, dict)
            or part not in current
        ):
            return None

        current = current[part]

    return current


def expect(
    data: dict[str, Any],
    rel: str,
    path: str,
    expected: Any,
) -> None:
    actual = yaml_path(
        data,
        path,
    )

    if actual != expected:
        error(
            f"{rel}: {path} expected "
            f"{expected!r}, got {actual!r}"
        )


def full_repository_present() -> bool:
    return all(
        (
            ROOT / rel
        ).exists()
        for rel in [
            "THOMAS_CORE/THOMAS_IDENTITY.md",
            "THOMAS_CORE/THOMAS_VALUES.yaml",
            "THOMAS_CORE/THOMAS_GOALS.yaml",
            "THOMAS_CORE/THOMAS_DECISION_MODEL.yaml",
            "THOMAS_CORE/THOMAS_PREFERENCE_PROFILE.yaml",
            "THOMAS_CORE/THOMAS_REVENUE_PREFERENCE_MODEL.yaml",
            "THOMAS_CORE/MVP_CORE_SCOPE.md",
            "THOMAS_CORE/README.md",
            "docs/MVP_OPERATING_POLICY.md",
        ]
    )


metadata_rel = "THOMAS_CORE/CORE_METADATA.yaml"
metadata = load_yaml(metadata_rel)

expect(
    metadata,
    metadata_rel,
    "schema_version",
    "thomas_core_metadata.v0.4",
)
expect(
    metadata,
    metadata_rel,
    "core_version",
    "0.2.1",
)
expect(
    metadata,
    metadata_rel,
    "definition_status",
    "thomas_approved",
)
expect(
    metadata,
    metadata_rel,
    "canonical_human_readable_source",
    "THOMAS_CORE_PHILOSOPHY.md",
)
reject_keys(
    metadata,
    metadata_rel,
    [
        "status",
        "package_status",
        "validation_status",
        "release_status",
        "runtime_activation_status",
    ],
)

for source_name in [
    "philosophy",
    "identity",
    "values",
    "goals",
    "decision_model",
    "preference_profile",
]:
    expect(
        metadata,
        metadata_rel,
        f"canonical_source_status.{source_name}",
        "thomas_approved",
    )

expect(
    metadata,
    metadata_rel,
    "learning_stance.default",
    "actively_encouraged",
)
expect(
    metadata,
    metadata_rel,
    "learning_stance.learning_is_permission_expansion",
    False,
)
expect(
    metadata,
    metadata_rel,
    "programization_stance.review_trigger.minimum_valid_repetitions",
    10,
)
expect(
    metadata,
    metadata_rel,
    "programization_stance.programization_expands_permission",
    False,
)
expect(
    metadata,
    metadata_rel,
    "release_governance.artifact_snapshot_required",
    True,
)
expect(
    metadata,
    metadata_rel,
    "release_governance.validation_toolchain_snapshot_required",
    True,
)
expect(
    metadata,
    metadata_rel,
    "release_governance.runtime_authoritative_approval_required",
    True,
)
expect(
    metadata,
    metadata_rel,
    "release_governance.unverified_manual_approval_runtime_activation_allowed",
    False,
)
expect(
    metadata,
    metadata_rel,
    "release_governance.release_manifest_self_approval",
    False,
)
expect(
    metadata,
    metadata_rel,
    "release_governance.running_task_silent_rebind",
    False,
)

active_rel = "THOMAS_CORE/MVP_ACTIVE_CORE.yaml"
active = load_yaml(active_rel)

expect(
    active,
    active_rel,
    "schema_version",
    "thomas_mvp_active_core.v0.4",
)
expect(
    active,
    active_rel,
    "core_version",
    "0.2.1",
)
expect(
    active,
    active_rel,
    "definition_status",
    "thomas_approved",
)
expect(
    active,
    active_rel,
    "content_status",
    "thomas_approved",
)
reject_keys(
    active,
    active_rel,
    [
        "status",
        "validation_status",
        "release_status",
        "runtime_activation_status",
    ],
)

if (
    "explicit Thomas approval"
    not in active.get(
        "promotion_rule",
        "",
    )
):
    error(
        f"{active_rel}: promotion_rule must "
        "require explicit Thomas approval"
    )

expected_activation_requirements = {
    "explicit_thomas_approval",
    "versioned_core_update",
    "audit_record",
    "runtime_projection_update",
}

if set(
    active.get(
        "activation_requirements",
        [],
    )
) != expected_activation_requirements:
    error(
        f"{active_rel}: activation_requirements "
        "compatibility mismatch"
    )

rules = active.get(
    "active_rules",
    [],
)

expected_ids = [
    f"MVP_RULE_{index:03d}"
    for index in range(1, 14)
]

actual_ids = [
    item.get("id")
    for item in rules
    if isinstance(item, dict)
]

if actual_ids != expected_ids:
    error(
        f"{active_rel}: Active Rule IDs/order "
        f"expected {expected_ids}, got {actual_ids}"
    )

rule_013 = next(
    (
        item
        for item in rules
        if isinstance(item, dict)
        and item.get("id")
        == "MVP_RULE_013"
    ),
    {},
)

rule_013_text = str(
    rule_013.get(
        "rule",
        "",
    )
)

for token in [
    "Program Candidate",
    "Program Registry",
    "Permission Policy",
    "권한을 확대하지 않는다",
]:
    if token not in rule_013_text:
        error(
            f"{active_rel}: MVP_RULE_013 "
            f"missing boundary token: {token}"
        )

if "자동 활성화" in rule_013_text:
    error(
        f"{active_rel}: MVP_RULE_013 must "
        "not directly authorize automatic activation"
    )

expect(
    active,
    active_rel,
    "learning_policy.default",
    "actively_encouraged",
)
expect(
    active,
    active_rel,
    "learning_policy.learning_does_not_expand_permission",
    True,
)
expect(
    active,
    active_rel,
    "learning_policy.protected_core_auto_change",
    False,
)
expect(
    active,
    active_rel,
    "programization_policy.minimum_valid_repetitions_for_review",
    10,
)
expect(
    active,
    active_rel,
    "programization_policy.automatic_conversion",
    False,
)
expect(
    active,
    active_rel,
    "programization_policy.permission_expansion",
    False,
)
expect(
    active,
    active_rel,
    "release_binding_policy.core_context_binding_contract",
    "core_context_binding.v0.3",
)
expect(
    active,
    active_rel,
    "release_binding_policy.core_approval_grants_execution_permission",
    False,
)

policy_rel = (
    "THOMAS_CORE/"
    "CORE_RUNTIME_POLICY_PROJECTION.yaml"
)
policy = load_yaml(policy_rel)

expect(
    policy,
    policy_rel,
    "schema_version",
    "core_runtime_policy_projection.v0.1",
)
expect(
    policy,
    policy_rel,
    "projection_version",
    "0.2.0",
)
expect(
    policy,
    policy_rel,
    "definition_status",
    "thomas_approved",
)
expect(
    policy,
    policy_rel,
    "invariants.learning.learning_expands_permission",
    False,
)
expect(
    policy,
    policy_rel,
    "invariants.programization.review_trigger.minimum_independent_valid_repetitions",
    10,
)
expect(
    policy,
    policy_rel,
    "invariants.programization.automatic_conversion",
    False,
)
expect(
    policy,
    policy_rel,
    "invariants.programization.permission_expansion",
    False,
)
expect(
    policy,
    policy_rel,
    "invariants.core_release.artifact_snapshot_required",
    True,
)
expect(
    policy,
    policy_rel,
    "invariants.core_release.runtime_authoritative_approval_required",
    True,
)
expect(
    policy,
    policy_rel,
    "invariants.core_release.unverified_manual_approval_runtime_activation_allowed",
    False,
)
expect(
    policy,
    policy_rel,
    "invariants.core_binding.contract",
    "core_context_binding.v0.3",
)
expect(
    policy,
    policy_rel,
    "invariants.core_binding.binding_must_be_created_from_task_record",
    True,
)
expect(
    policy,
    policy_rel,
    "invariants.core_binding.runtime_rule_membership_resolves_through_bound_release",
    True,
)

require_tokens(
    "THOMAS_CORE/THOMAS_CORE_PHILOSOPHY.md",
    [
        "**Version:** `0.2.1`",
        "## 1. Identity",
        "## 2. Mission",
        "## 3. Vision",
        "## 4. Values",
        "## 5. Thinking Model",
        "## 6. Decision Model",
        "## 7. Operating Style",
        "## 8. Risk Philosophy",
        "## 9. Learning Philosophy",
        "Programization from Repeated Work",
        "10회 이상의 유효한 반복",
        "Program 자동 전환이 아니라",
        "Agent 전체를 Program으로 대체하지 않는다",
        "Program Candidate",
        "Program Registry",
        "Permission Policy",
        "Program 활성화는 Actor, Role, Agent 또는 Program의 권한을 확대하지 않는다",
        "Learning Is Not Permission",
        "Compounding",
    ],
)

philosophy_text = read_text(
    "THOMAS_CORE/THOMAS_CORE_PHILOSOPHY.md"
)

if (
    "검증된 저위험 내부 Program은 "
    "명시된 Role, Task 유형, 입력 범위와 "
    "환경 안에서 자동 활성화할 수 있다."
    in philosophy_text
):
    error(
        "THOMAS_CORE_PHILOSOPHY.md: "
        "legacy direct automatic Program activation "
        "wording remains"
    )

full_repo = full_repository_present()

if full_repo:
    identity = read_text(
        "THOMAS_CORE/THOMAS_IDENTITY.md"
    )

    for token in [
        "Core Version: 0.2.1",
        "Learning, Efficiency, and Periodic Review Identity",
        "계속 배우고",
        "더 쉽고 효율적인 방법",
        "정기 검토",
    ]:
        if token not in identity:
            error(
                "THOMAS_IDENTITY.md missing "
                f"required token: {token}"
            )

    for rel in [
        "THOMAS_CORE/THOMAS_VALUES.yaml",
        "THOMAS_CORE/THOMAS_GOALS.yaml",
        "THOMAS_CORE/THOMAS_DECISION_MODEL.yaml",
        "THOMAS_CORE/THOMAS_PREFERENCE_PROFILE.yaml",
    ]:
        data = load_yaml(rel)

        if data.get("version") != "0.2.1":
            error(
                f"{rel}: version must be 0.2.1"
            )

    goals = load_yaml(
        "THOMAS_CORE/THOMAS_GOALS.yaml"
    )

    mission = (
        goals.get("mission", {})
        .get("statements", [])
    )

    for statement in [
        "내 시간이 아니라 시스템이 돈을 버는 구조를 만든다.",
        "사람이 항상 개입하지 않아도 24시간 운영되는 시스템을 만든다.",
    ]:
        if statement not in mission:
            error(
                "THOMAS_GOALS.yaml missing "
                f"Mission statement: {statement}"
            )

    pillars = (
        goals.get("vision", {})
        .get("pillars", [])
    )

    for pillar in [
        "AI Organization",
        "Autonomous Company",
        "Autonomous Investor",
    ]:
        if pillar not in pillars:
            error(
                "THOMAS_GOALS.yaml missing "
                f"Vision pillar: {pillar}"
            )

    preference = load_yaml(
        "THOMAS_CORE/"
        "THOMAS_PREFERENCE_PROFILE.yaml"
    )

    expect(
        preference,
        "THOMAS_CORE/THOMAS_PREFERENCE_PROFILE.yaml",
        "learning_preferences.philosophy.default",
        "actively_encourage_learning",
    )
    expect(
        preference,
        "THOMAS_CORE/THOMAS_PREFERENCE_PROFILE.yaml",
        "learning_preferences.philosophy.learning_is_permission_expansion",
        False,
    )

    require_tokens(
        "docs/MVP_OPERATING_POLICY.md",
        [
            "학습은 Thomas Autonomous Organization의 기본 기능이며 적극적으로 장려한다",
            "Validated Operating Knowledge",
            "학습은 권한을 자동 확대하지 않는다",
            "Programization from Repeated Work",
            "Program Registry",
            "Permission Policy",
        ],
    )

    role_template = load_yaml(
        "03_ROLE_CONTRACTS/"
        "ROLE_DEFINITION_TEMPLATE.yaml"
    )

    expect(
        role_template,
        "03_ROLE_CONTRACTS/ROLE_DEFINITION_TEMPLATE.yaml",
        "input_contract.task_contract",
        "task.v0.3",
    )
    expect(
        role_template,
        "03_ROLE_CONTRACTS/ROLE_DEFINITION_TEMPLATE.yaml",
        "input_contract.core_context_binding_required",
        True,
    )

if ERRORS:
    print(
        "FAIL: Thomas Core validation "
        "found errors"
    )

    for item in ERRORS:
        print(f" - {item}")

    raise SystemExit(1)

print(
    "PASS: Thomas Core v0.2.1 "
    "I0.4.1 Lean validation completed"
)
print(
    "Mode: "
    + (
        "strict full Repository projection"
        if full_repo
        else "overlay package"
    )
)
print(
    "Checked semantic status, no dynamic lifecycle "
    "state in hashed Core, learning, Programization "
    "Candidate boundary, Release snapshots, "
    "authoritative Approval, Binding v0.3, Mission, "
    "Vision, Compounding, and Task v0.3 Role input"
)
