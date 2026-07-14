#!/usr/bin/env python3
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []


def error(message: str) -> None:
    ERRORS.append(message)


def load_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(
            path.read_text(encoding="utf-8")
        )
    except Exception as exc:
        error(
            f"{path.relative_to(ROOT)}: "
            f"YAML parse failed: {exc}"
        )
        return None


def parse_front_matter(
    path: Path,
) -> tuple[dict[str, Any] | None, str]:
    text = path.read_text(
        encoding="utf-8"
    )

    if not text.startswith("---\n"):
        error(
            f"{path.relative_to(ROOT)}: "
            "missing YAML front matter"
        )
        return None, text

    end = text.find(
        "\n---\n",
        4,
    )

    if end < 0:
        error(
            f"{path.relative_to(ROOT)}: "
            "unterminated YAML front matter"
        )
        return None, text

    try:
        data = yaml.safe_load(
            text[4:end]
        )
    except Exception as exc:
        error(
            f"{path.relative_to(ROOT)}: "
            f"front matter parse failed: {exc}"
        )
        return None, text

    if not isinstance(data, dict):
        error(
            f"{path.relative_to(ROOT)}: "
            "front matter must be a mapping"
        )
        return None, text

    return data, text


def sha256_text(
    text: str,
) -> str:
    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


def capability_hash(
    values: list[str],
) -> str:
    return hashlib.sha256(
        "\n".join(
            sorted(values)
        ).encode("utf-8")
    ).hexdigest()


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


registry_path = ROOT / "03_ROLE_CONTRACTS/ROLE_REGISTRY.yaml"
registry = load_yaml(registry_path)

if not isinstance(registry, dict):
    raise SystemExit(1)

if registry.get("schema_version") != "role_registry.v0.3":
    error("ROLE_REGISTRY.yaml must use role_registry.v0.3")

if registry.get("authoritative") is not True:
    error("ROLE_REGISTRY.yaml must be the active authoritative status/index source")

for prohibited_key in ("governance", "selection_policy", "activation", "rules"):
    if prohibited_key in registry:
        error(f"Role Registry must not own policy block: {prohibited_key}")

required_role_fields = {
    "schema_version",
    "role_id",
    "role_name",
    "role_version",
    "status",
    "routable",
    "role_type",
    "purpose",
    "capabilities",
    "input_contract",
    "active_core",
    "permission_ceiling",
    "allowed_program_ids",
    "allowed_tool_ids",
    "memory_policy",
    "output_contract",
    "validation_policy",
    "budget_caps",
    "stop_conditions",
    "completion_criteria",
    "quality_criteria",
    "escalation",
    "change_control",
}

prohibited_registry_fields = {
    "capabilities",
    "capability_set_sha256",
    "permission_ceiling",
    "restrictions",
    "validation_default",
    "promotion_requirements",
    "selection_policy",
}

entries = list(registry.get("roles", []))
seen: set[str] = set()

for entry in entries:
    role_id = entry.get("role_id")
    if role_id in seen:
        error(f"Duplicate Role ID: {role_id}")
    seen.add(role_id)

    duplicated = sorted(prohibited_registry_fields.intersection(entry))
    if duplicated:
        error(f"{role_id}: Registry duplicates Definition-owned fields: {duplicated}")

    rel = entry.get("definition_path")
    if not isinstance(rel, str):
        error(f"{role_id}: invalid definition_path")
        continue

    path = ROOT / rel
    if not path.exists():
        error(f"{role_id}: Role Definition does not exist: {rel}")
        continue

    data, full_text = parse_front_matter(path)
    if not isinstance(data, dict):
        continue

    missing = sorted(required_role_fields - set(data))
    if missing:
        error(f"{role_id}: missing Role fields: {missing}")

    registry_to_definition = {
        "role_id": "role_id",
        "version": "role_version",
        "status": "status",
        "routable": "routable",
        "role_type": "role_type",
    }
    for registry_field, definition_field in registry_to_definition.items():
        if data.get(definition_field) != entry.get(registry_field):
            error(
                f"{role_id}: Registry/Definition mismatch for "
                f"{registry_field}->{definition_field}"
            )

    if entry.get("definition_sha256") != sha256_text(full_text):
        error(f"{role_id}: definition_sha256 mismatch")

    input_contract = data.get("input_contract", {})
    if input_contract.get("task_contract") != "task.v0.3":
        error(f"{role_id}: task_contract must be task.v0.3")
    if input_contract.get("task_contract_minimum") != "task.v0.3":
        error(f"{role_id}: minimum Task Contract must be task.v0.3")
    if input_contract.get("core_context_binding_required") is not True:
        error(f"{role_id}: Core Context Binding must be required")
    if input_contract.get("assignment_contract") != "role_assignment.v0.2":
        error(f"{role_id}: Assignment Contract must be role_assignment.v0.2")

    if data.get("output_contract", {}).get("base_contract") != "agent_output.v0.2":
        error(f"{role_id}: Output Contract must be agent_output.v0.2")
    if data.get("budget_caps", {}).get("schema_version") != "execution_budget.v0.1":
        error(f"{role_id}: budget schema must be execution_budget.v0.1")

    if data.get("status") == "active" and data.get("routable") is not True:
        error(f"{role_id}: active Role must be routable")
    if data.get("status") == "candidate" and data.get("routable") is not False:
        error(f"{role_id}: Candidate Role must not be routable")

    if data.get("allowed_program_ids", []):
        error(f"{role_id}: Program allowlist must remain empty in I0.4.1 Lean")
    if data.get("allowed_tool_ids", []):
        error(f"{role_id}: Tool allowlist must remain empty in I0.4.1 Lean")

for entry in registry.get("non_dynamic_roles", []):
    rel = entry.get("definition_path")
    if not isinstance(rel, str) or not (ROOT / rel).exists():
        error(f"Non-dynamic Role definition is missing: {rel}")
    if entry.get("role_id") == "thomas.prime" and entry.get("routable") is not False:
        error("Thomas Prime must remain non-routable")

for rel in [
    "schemas/task.v0.3.schema.json",
    "schemas/core_context_binding.v0.3.schema.json",
    "schemas/role_assignment.v0.2.schema.json",
    "schemas/agent_output.v0.2.schema.json",
    "schemas/execution_budget.v0.1.schema.json",
]:
    if not (
        ROOT / rel
    ).exists():
        error(
            f"Missing Runtime Schema: {rel}"
        )


contract_markers = {
    (
        "03_ROLE_CONTRACTS/"
        "ROLE_ASSIGNMENT_CONTRACT.md"
    ): [
        "role_assignment.v0.2",
        "core_context_binding_id",
        "same `core_context_binding_id`",
        "task.v0.3",
    ],
    (
        "docs/runtime-contracts/"
        "AGENT_OUTPUT_CONTRACT_V0.2.md"
    ): [
        "agent_output.v0.2",
        "core_context_binding_id",
        "Task Binding",
        "Agent Output Binding",
    ],
    (
        "docs/runtime-contracts/"
        "CORE_CONTEXT_BINDING_V0.3.md"
    ): [
        "core_context_binding.v0.3",
        "Task file",
        "Release snapshot",
        "does not grant execution",
    ],
    (
        "docs/runtime-contracts/"
        "TASK_CONTRACT_V0.3.md"
    ): [
        "task.v0.3",
        "core_context_binding_id",
        "required_permission_level",
        "permission_decision_ref",
        "execution_budget.v0.1",
    ],
}

for rel, tokens in contract_markers.items():
    path = ROOT / rel

    if not path.exists():
        error(
            f"Missing contract: {rel}"
        )
        continue

    text = path.read_text(
        encoding="utf-8"
    )

    for token in tokens:
        if token not in text:
            error(
                f"{rel}: missing contract token: "
                f"{token}"
            )


core = load_yaml(
    ROOT
    / "THOMAS_CORE/"
    "MVP_ACTIVE_CORE.yaml"
)

if isinstance(core, dict):
    if (
        core.get(
            "schema_version"
        )
        != "thomas_mvp_active_core.v0.4"
    ):
        error(
            "MVP Active Core schema must be v0.4"
        )

    if (
        core.get(
            "definition_status"
        )
        != "thomas_approved"
    ):
        error(
            "MVP Active Core definition status "
            "must be thomas_approved"
        )

    for key in [
        "status",
        "validation_status",
        "release_status",
        "runtime_activation_status",
    ]:
        if key in core:
            error(
                "MVP Active Core must not contain "
                f"dynamic status key: {key}"
            )

    if (
        "explicit Thomas approval"
        not in core.get(
            "promotion_rule",
            "",
        )
    ):
        error(
            "MVP Active Core promotion must "
            "require explicit Thomas approval"
        )

    ids = [
        item.get("id")
        for item in core.get(
            "active_rules",
            [],
        )
        if isinstance(item, dict)
    ]

    expected = [
        f"MVP_RULE_{index:03d}"
        for index in range(1, 14)
    ]

    if ids != expected:
        error(
            "MVP Active Core Rule set "
            "must be 001 through 013"
        )


metadata = load_yaml(
    ROOT
    / "THOMAS_CORE/"
    "CORE_METADATA.yaml"
)

if isinstance(metadata, dict):
    if (
        metadata.get(
            "schema_version"
        )
        != "thomas_core_metadata.v0.4"
    ):
        error(
            "Core Metadata schema must be v0.4"
        )

    for key in [
        "status",
        "package_status",
        "validation_status",
        "release_status",
        "runtime_activation_status",
    ]:
        if key in metadata:
            error(
                "Core Metadata must not contain "
                f"dynamic status key: {key}"
            )

    if (
        metadata.get(
            "release_governance",
            {},
        ).get(
            "artifact_snapshot_required"
        )
        is not True
    ):
        error(
            "Core Release must require "
            "artifact snapshots"
        )


for registry_rel, key in [
    (
        "05_REGISTRIES/PROGRAM_REGISTRY.yaml",
        "programs",
    ),
    (
        "05_REGISTRIES/TOOL_REGISTRY.yaml",
        "tools",
    ),
]:
    data = load_yaml(
        ROOT / registry_rel
    )

    if not isinstance(data, dict):
        continue

    for item in data.get(
        key,
        [],
    ):
        if (
            item.get("status")
            == "active"
            or item.get("enabled")
            is True
        ):
            error(
                f"{registry_rel}: no Runtime "
                "resource may be active in I0.4.1 Lean"
            )


if ERRORS:
    print(
        "FAIL: Runtime contract consistency "
        "checks found errors"
    )

    for item in ERRORS:
        print(f" - {item}")

    raise SystemExit(1)

print(
    "PASS: Runtime contract consistency "
    "checks completed"
)
print(
    f"Checked {len(entries)} Role Definitions, "
    "Task v0.3 minimum, Core Binding v0.3, "
    "Registry hashes, Assignment/Output Schemas, "
    "Core semantic status, and disabled "
    "Tool/Program boundaries"
)
