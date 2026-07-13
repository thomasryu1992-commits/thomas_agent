#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []


def error(message: str) -> None:
    ERRORS.append(message)


required_files = [
    "THOMAS_CORE/THOMAS_IDENTITY.md",
    "THOMAS_CORE/THOMAS_VALUES.yaml",
    "THOMAS_CORE/THOMAS_GOALS.yaml",
    "THOMAS_CORE/THOMAS_DECISION_MODEL.yaml",
    "THOMAS_CORE/THOMAS_PREFERENCE_PROFILE.yaml",
    "THOMAS_CORE/THOMAS_REVENUE_PREFERENCE_MODEL.yaml",
    "THOMAS_CORE/MVP_CORE_SCOPE.md",
    "THOMAS_CORE/README.md",
    "docs/MVP_OPERATING_POLICY.md",
    "03_ROLE_CONTRACTS/ROLE_REGISTRY.yaml",
    "03_ROLE_CONTRACTS/ROLE_DEFINITION_TEMPLATE.yaml",
    "schemas/task.v0.3.schema.json",
    "schemas/core_context_binding.v0.3.schema.json",
    "schemas/role_assignment.v0.2.schema.json",
    "schemas/agent_output.v0.2.schema.json",
    "requirements-validation.lock",
]

for rel in required_files:
    if not (ROOT / rel).exists():
        error(
            "Required integrated Repository file "
            f"is missing: {rel}"
        )

for rel in [
    "THOMAS_CORE/CORE_RELEASE_MANIFEST.yaml",
    "THOMAS_CORE/CORE_RELEASE_APPROVAL.yaml",
]:
    if (ROOT / rel).exists():
        error(
            f"Deprecated fixed Release file exists: "
            f"{rel}"
        )

task_schema_path = (
    ROOT
    / "schemas/task.v0.3.schema.json"
)

if task_schema_path.exists():
    try:
        schema = json.loads(
            task_schema_path.read_text(
                encoding="utf-8"
            )
        )

        if (
            schema.get("$id")
            != "task.v0.3.schema.json"
        ):
            error(
                "Task Schema $id must remain "
                "task.v0.3.schema.json"
            )

    except Exception as exc:
        error(
            f"Task Schema parse failed: {exc}"
        )

registry_path = (
    ROOT
    / "03_ROLE_CONTRACTS/"
    "ROLE_REGISTRY.yaml"
)

if registry_path.exists():
    try:
        registry = yaml.safe_load(
            registry_path.read_text(
                encoding="utf-8"
            )
        )

        if (
            registry.get(
                "schema_version"
            )
            != "role_registry.v0.2"
        ):
            error(
                "Role Registry must use "
                "role_registry.v0.2"
            )

        if (
            registry.get(
                "governance",
                {},
            ).get(
                "runtime_assignment",
                {},
            ).get(
                "minimum_task_contract"
            )
            != "task.v0.3"
        ):
            error(
                "Role Registry must require "
                "Task v0.3"
            )

    except Exception as exc:
        error(
            f"Role Registry parse failed: {exc}"
        )

if ERRORS:
    print(
        "FAIL: I0.4.1 Lean integrated Repository "
        "preconditions not met"
    )

    for item in ERRORS:
        print(f" - {item}")

    raise SystemExit(1)

print(
    "PASS: I0.4.1 Lean integrated Repository "
    "preconditions met"
)
print(
    "Checked detailed Core sources, Runtime "
    "Contract v0.4 baseline, Task v0.3, Binding "
    "v0.3, Assignment/Output Schemas, Role "
    "Registry v0.2, validation lock, and "
    "deprecated Release paths"
)
