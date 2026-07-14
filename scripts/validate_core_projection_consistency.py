#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
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
            "THOMAS_CORE/README.md",
            "docs/MVP_OPERATING_POLICY.md",
        ]
    )


def check_projection_map() -> dict[str, Any]:
    rel = "generated/docs/CORE_PROJECTION_MAP.yaml"
    data = load_yaml(rel)

    expect(
        data,
        rel,
        "schema_version",
        "core_projection_map.v0.3",
    )
    expect(
        data,
        rel,
        "map_version",
        "0.3.1",
    )
    expect(
        data,
        rel,
        "core_version",
        "0.2.1",
    )
    expect(
        data,
        rel,
        "canonical_source.path",
        "THOMAS_CORE/THOMAS_CORE_PHILOSOPHY.md",
    )
    expect(
        data,
        rel,
        "exact_rule_meaning.rule_id_alone_is_sufficient",
        False,
    )
    expect(
        data,
        rel,
        "release_reproducibility.historical_release_verification_uses_current_worktree",
        False,
    )
    expect(
        data,
        rel,
        "release_reproducibility.context_binding_contract",
        "docs/runtime-contracts/CORE_CONTEXT_BINDING_V0.3.md",
    )
    expect(
        data,
        rel,
        "release_reproducibility.current_runtime_pointer",
        "THOMAS_CORE/CURRENT_CORE_RELEASE.yaml",
    )

    concepts = data.get(
        "concept_ownership",
        {},
    )

    expected_concepts = {
        "identity",
        "mission",
        "vision",
        "values",
        "thinking_model",
        "decision_model",
        "operating_style",
        "risk_philosophy",
        "learning_philosophy",
        "programization",
        "communication",
        "opportunity",
    }

    if not isinstance(concepts, dict):
        error(
            f"{rel}: concept_ownership must "
            "be a mapping"
        )
    else:
        missing = sorted(
            expected_concepts - set(concepts)
        )

        if missing:
            error(
                f"{rel}: missing concepts: {missing}"
            )

    return data


def check_versions() -> None:
    philosophy = read_text(
        "THOMAS_CORE/THOMAS_CORE_PHILOSOPHY.md"
    )
    metadata = load_yaml(
        "THOMAS_CORE/CORE_METADATA.yaml"
    )
    active = load_yaml(
        "THOMAS_CORE/MVP_ACTIVE_CORE.yaml"
    )
    projection = load_yaml(
        "generated/docs/CORE_PROJECTION_MAP.yaml"
    )
    policy = load_yaml(
        "THOMAS_CORE/CORE_RUNTIME_POLICY_PROJECTION.yaml"
    )

    if "**Version:** `0.2.1`" not in philosophy:
        error(
            "THOMAS_CORE_PHILOSOPHY.md: "
            "version mismatch"
        )

    for rel, data in [
        ("CORE_METADATA.yaml", metadata),
        ("MVP_ACTIVE_CORE.yaml", active),
        ("generated/docs/CORE_PROJECTION_MAP.yaml", projection),
        ("CORE_RUNTIME_POLICY_PROJECTION.yaml", policy),
    ]:
        if data.get("core_version") != "0.2.1":
            error(
                f"{rel}: "
                "core_version mismatch"
            )


def check_active_core(
    projection: dict[str, Any],
) -> set[str]:
    rel = "THOMAS_CORE/MVP_ACTIVE_CORE.yaml"
    active = load_yaml(rel)

    expect(
        active,
        rel,
        "schema_version",
        "thomas_mvp_active_core.v0.4",
    )
    expect(
        active,
        rel,
        "definition_status",
        "thomas_approved",
    )

    for key in [
        "status",
        "validation_status",
        "release_status",
        "runtime_activation_status",
    ]:
        if key in active:
            error(
                f"{rel}: dynamic status key "
                f"must not be hashed into Core: {key}"
            )

    rules = active.get(
        "active_rules",
        [],
    )

    ids = [
        item.get("id")
        for item in rules
        if isinstance(item, dict)
    ]

    expected_ids = [
        f"MVP_RULE_{index:03d}"
        for index in range(1, 14)
    ]

    if ids != expected_ids:
        error(
            f"{rel}: Rule IDs/order mismatch"
        )

    if len(set(ids)) != len(ids):
        error(
            f"{rel}: duplicate Rule IDs"
        )

    expected_runtime_use = yaml_path(
        projection,
        "active_rule_contract.expected_runtime_use",
    )

    if isinstance(
        expected_runtime_use,
        dict,
    ):
        actual_runtime_use = {
            item.get("id"): item.get(
                "runtime_use"
            )
            for item in rules
            if isinstance(item, dict)
        }

        for rule_id, expected in (
            expected_runtime_use.items()
        ):
            if (
                actual_runtime_use.get(
                    rule_id
                )
                != expected
            ):
                error(
                    f"{rel}: {rule_id} runtime_use "
                    "does not match Projection Map"
                )

    expect(
        active,
        rel,
        "programization_policy.automatic_conversion",
        False,
    )
    expect(
        active,
        rel,
        "programization_policy.permission_expansion",
        False,
    )
    expect(
        active,
        rel,
        "release_binding_policy.core_context_binding_contract",
        "core_context_binding.v0.3",
    )

    return set(ids)


def check_metadata() -> None:
    rel = "THOMAS_CORE/CORE_METADATA.yaml"
    data = load_yaml(rel)

    expect(
        data,
        rel,
        "schema_version",
        "thomas_core_metadata.v0.4",
    )
    expect(
        data,
        rel,
        "definition_status",
        "thomas_approved",
    )

    for key in [
        "status",
        "package_status",
        "validation_status",
        "release_status",
        "runtime_activation_status",
    ]:
        if key in data:
            error(
                f"{rel}: dynamic status key "
                f"must not be hashed into Core: {key}"
            )

    expect(
        data,
        rel,
        "release_governance.artifact_snapshot_required",
        True,
    )
    expect(
        data,
        rel,
        "release_governance.validation_toolchain_snapshot_required",
        True,
    )
    expect(
        data,
        rel,
        "release_governance.runtime_authoritative_approval_required",
        True,
    )
    expect(
        data,
        rel,
        "release_governance.unverified_manual_approval_runtime_activation_allowed",
        False,
    )


def check_runtime_policy() -> None:
    rel = (
        "THOMAS_CORE/"
        "CORE_RUNTIME_POLICY_PROJECTION.yaml"
    )
    data = load_yaml(rel)

    expect(
        data,
        rel,
        "schema_version",
        "core_runtime_policy_projection.v0.1",
    )
    expect(
        data,
        rel,
        "projection_version",
        "0.2.0",
    )
    expect(
        data,
        rel,
        "invariants.learning.learning_expands_permission",
        False,
    )
    expect(
        data,
        rel,
        "invariants.programization.automatic_conversion",
        False,
    )
    expect(
        data,
        rel,
        "invariants.programization.permission_expansion",
        False,
    )
    expect(
        data,
        rel,
        "invariants.core_release.artifact_snapshot_required",
        True,
    )
    expect(
        data,
        rel,
        "invariants.core_binding.binding_must_be_created_from_task_record",
        True,
    )


def check_strict_projections(
    active_rule_ids: set[str],
) -> None:
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

    role_template_rel = (
        "03_ROLE_CONTRACTS/"
        "ROLE_DEFINITION_TEMPLATE.yaml"
    )
    role_template = load_yaml(
        role_template_rel
    )

    expect(
        role_template,
        role_template_rel,
        "input_contract.task_contract",
        "task.v0.3",
    )
    expect(
        role_template,
        role_template_rel,
        "input_contract.task_contract_minimum",
        "task.v0.3",
    )
    expect(
        role_template,
        role_template_rel,
        "input_contract.core_context_binding_required",
        True,
    )

    allowed = yaml_path(
        role_template,
        "active_core.allowed_rule_ids",
    )

    if set(allowed or []) != active_rule_ids:
        error(
            f"{role_template_rel}: allowed Rule IDs "
            "must match Active Core"
        )

    for path in sorted(
        (
            ROOT
            / "03_ROLE_CONTRACTS/ROLES"
        ).rglob("*.md")
    ):
        text = path.read_text(
            encoding="utf-8"
        )

        if (
            "task_contract: task.v0.3"
            not in text
        ):
            error(
                f"{path.relative_to(ROOT)}: "
                "must require Task v0.3"
            )

        if (
            "core_context_binding_required: true"
            not in text
        ):
            error(
                f"{path.relative_to(ROOT)}: "
                "must require Core Context Binding"
            )


def scan_stale_text(
    projection: dict[str, Any],
) -> None:
    patterns = projection.get(
        "stale_text_patterns",
        [],
    )

    if not isinstance(patterns, list):
        error(
            "CORE_PROJECTION_MAP.yaml: "
            "stale_text_patterns must be a list"
        )
        return

    owned_paths = [
        ROOT / "THOMAS_CORE",
        ROOT / "03_ROLE_CONTRACTS",
        ROOT / "docs",
    ]

    for directory in owned_paths:
        if not directory.exists():
            continue

        for path in directory.rglob("*"):
            if (
                not path.is_file()
                or path.suffix.lower()
                not in {
                    ".md",
                    ".yaml",
                    ".yml",
                    ".py",
                    ".json",
                }
            ):
                continue

            rel = path.relative_to(ROOT)
            rel_posix = rel.as_posix()

            if rel_posix == "generated/docs/CORE_PROJECTION_MAP.yaml":
                continue

            if (
                rel_posix.startswith(
                    "docs/PHASE_"
                )
                or rel_posix.startswith(
                    "tests/fixtures/"
                )
            ):
                continue

            text = path.read_text(
                encoding="utf-8",
                errors="replace",
            )

            for pattern in patterns:
                if pattern in text:
                    error(
                        f"{rel}: stale text found: "
                        f"{pattern}"
                    )


def scan_unknown_rule_ids(
    active_rule_ids: set[str],
) -> None:
    pattern = re.compile(
        r"\bMVP_RULE_\d{3}\b"
    )

    for directory in [
        ROOT / "THOMAS_CORE",
        ROOT / "03_ROLE_CONTRACTS",
        ROOT / "docs",
        ROOT / "schemas",
        ROOT / "examples",
        ROOT / "scripts",
    ]:
        if not directory.exists():
            continue

        for path in directory.rglob("*"):
            if (
                not path.is_file()
                or path.suffix.lower()
                not in {
                    ".md",
                    ".yaml",
                    ".yml",
                    ".json",
                    ".py",
                }
            ):
                continue

            rel = path.relative_to(ROOT)

            if "tests/fixtures" in str(rel):
                continue

            refs = set(
                pattern.findall(
                    path.read_text(
                        encoding="utf-8",
                        errors="replace",
                    )
                )
            )

            unknown = sorted(
                refs - active_rule_ids
            )

            if unknown:
                error(
                    f"{rel}: unknown Active Rule "
                    f"references: {unknown}"
                )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate Thomas Core projection "
            "ownership and cross-file consistency."
        )
    )
    parser.add_argument(
        "--strict",
        action="store_true",
    )
    parser.add_argument(
        "--overlay",
        action="store_true",
    )
    args = parser.parse_args()

    detected_full = full_repository_present()
    strict = (
        args.strict
        or (
            detected_full
            and not args.overlay
        )
    )

    projection = check_projection_map()
    check_versions()
    check_metadata()
    check_runtime_policy()
    active_rule_ids = check_active_core(
        projection
    )

    if strict:
        check_strict_projections(
            active_rule_ids
        )

    scan_stale_text(
        projection
    )
    scan_unknown_rule_ids(
        active_rule_ids
    )

    if ERRORS:
        print(
            "FAIL: Thomas Core projection "
            "consistency validation found errors"
        )

        for item in ERRORS:
            print(f" - {item}")

        return 1

    print(
        "PASS: Thomas Core projection "
        "consistency validation completed"
    )
    print(
        "Mode: "
        + (
            "strict full-repository"
            if strict
            else "overlay package"
        )
    )
    print(
        "Checked canonical ownership, semantic "
        "status, Rule meaning, Mission/Vision "
        "projections, learning, Programization, "
        "Task v0.3 Role inputs, immutable Release "
        "snapshot policy, Binding v0.3, stale text, "
        "and unknown Rule references"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
