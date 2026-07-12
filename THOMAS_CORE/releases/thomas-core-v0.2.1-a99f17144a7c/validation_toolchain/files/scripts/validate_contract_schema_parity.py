#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []


def load_schema(rel: str) -> dict:
    path = ROOT / rel
    return json.loads(
        path.read_text(encoding="utf-8")
    )


def table_fields(
    text: str,
    start_heading: str,
    end_heading: str,
) -> set[str]:
    start = text.find(start_heading)

    if start < 0:
        ERRORS.append(
            f"Missing heading: {start_heading}"
        )
        return set()

    end = text.find(
        end_heading,
        start + len(start_heading),
    )

    section = (
        text[start:]
        if end < 0
        else text[start:end]
    )

    return set(
        re.findall(
            r"^\|\s*`([^`]+)`\s*\|",
            section,
            re.MULTILINE,
        )
    )


def compare_required(
    doc_rel: str,
    schema_rel: str,
    start_heading: str,
    end_heading: str,
) -> None:
    text = (
        ROOT / doc_rel
    ).read_text(encoding="utf-8")

    schema = load_schema(
        schema_rel
    )

    documented = table_fields(
        text,
        start_heading,
        end_heading,
    )
    required = set(
        schema.get("required", [])
    )

    missing_in_doc = sorted(
        required - documented
    )
    missing_in_schema = sorted(
        documented - required
    )

    if missing_in_doc:
        ERRORS.append(
            f"{doc_rel}: required Schema fields "
            f"missing from table: {missing_in_doc}"
        )

    if missing_in_schema:
        ERRORS.append(
            f"{schema_rel}: documented required "
            f"fields missing from Schema: "
            f"{missing_in_schema}"
        )


def main() -> int:
    compare_required(
        "03_ROLE_CONTRACTS/ROLE_ASSIGNMENT_CONTRACT.md",
        "schemas/role_assignment.v0.2.schema.json",
        "## 2. Required Fields",
        "## Core Binding Lineage",
    )

    compare_required(
        "docs/runtime-contracts/AGENT_OUTPUT_CONTRACT_V0.2.md",
        "schemas/agent_output.v0.2.schema.json",
        "## 2. Required Fields",
        "## Core Binding Lineage",
    )

    task_text = (
        ROOT
        / "docs/runtime-contracts/TASK_CONTRACT_V0.3.md"
    ).read_text(encoding="utf-8")
    task_schema = load_schema(
        "schemas/task.v0.3.schema.json"
    )

    for field in task_schema.get(
        "required",
        [],
    ):
        if field not in task_text:
            ERRORS.append(
                "TASK_CONTRACT_V0.3.md missing "
                f"top-level Schema field marker: {field}"
            )

    binding_text = (
        ROOT
        / "docs/runtime-contracts/"
        "CORE_CONTEXT_BINDING_V0.3.md"
    ).read_text(encoding="utf-8")
    binding_schema = load_schema(
        "schemas/core_context_binding.v0.3.schema.json"
    )

    for field in binding_schema.get(
        "required",
        [],
    ):
        if field not in binding_text:
            ERRORS.append(
                "CORE_CONTEXT_BINDING_V0.3.md "
                f"missing Schema field marker: {field}"
            )

    role_template = (
        ROOT
        / "03_ROLE_CONTRACTS/"
        "ROLE_DEFINITION_TEMPLATE.yaml"
    ).read_text(encoding="utf-8")

    for token in [
        "task_contract: task.v0.3",
        "task_contract_minimum: task.v0.3",
        "core_context_binding_required: true",
    ]:
        if token not in role_template:
            ERRORS.append(
                "ROLE_DEFINITION_TEMPLATE.yaml "
                f"missing: {token}"
            )

    if ERRORS:
        print(
            "FAIL: contract/schema parity validation "
            "found errors"
        )

        for item in ERRORS:
            print(f" - {item}")

        return 1

    print(
        "PASS: contract/schema parity validation "
        "completed"
    )
    print(
        "Checked Task, Core Binding, Role Assignment, "
        "Agent Output, and Role Definition input "
        "contract parity"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
