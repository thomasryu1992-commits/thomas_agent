#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from lib.safe_io import safe_repo_path

ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []


def error(message: str) -> None:
    ERRORS.append(message)


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        error(f"{path.relative_to(ROOT)}: YAML parse failed: {exc}")
        return {}

    if not isinstance(data, dict):
        error(f"{path.relative_to(ROOT)}: expected mapping")
        return {}

    return data


def load_schema(rel: str) -> dict[str, Any]:
    path = ROOT / rel
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        error(f"{rel}: JSON parse failed: {exc}")
        return {}


def validate_schema(
    instance: dict[str, Any],
    schema: dict[str, Any],
    label: str,
    store: dict[str, Any],
) -> None:
    if not schema:
        return

    registry = Registry()

    for schema_id, schema_value in store.items():
        registry = registry.with_resource(
            schema_id,
            Resource.from_contents(schema_value),
        )

    validator = Draft202012Validator(
        schema,
        registry=registry,
        format_checker=FormatChecker(),
    )

    for issue in sorted(
        validator.iter_errors(instance),
        key=lambda item: list(item.path),
    ):
        location = ".".join(
            str(item)
            for item in issue.path
        ) or "<root>"
        error(f"{label} [{location}]: {issue.message}")


def p_level(value: str) -> int:
    if (
        not isinstance(value, str)
        or len(value) != 2
        or value[0] != "P"
        or not value[1].isdigit()
    ):
        raise ValueError(f"Invalid P-level: {value!r}")

    return int(value[1])


def validate_bundle(
    task: dict[str, Any],
    binding: dict[str, Any],
    assignment: dict[str, Any],
    output: dict[str, Any],
    label: str,
) -> None:
    ids = task.get("identity", {})
    context = task.get("context", {})
    routing = task.get("routing", {})

    binding_identity = binding.get("identity", {})
    binding_rules = binding.get("rules", {})

    required_equalities = [
        (
            "Task/Binding task_id",
            ids.get("task_id"),
            binding_identity.get("task_id"),
        ),
        (
            "Task/Binding trace_id",
            ids.get("trace_id"),
            binding_identity.get("trace_id"),
        ),
        (
            "Task/Binding task_revision",
            ids.get("task_revision"),
            binding_identity.get("task_revision"),
        ),
        (
            "Task/Binding ID",
            context.get("core_context_binding_id"),
            binding_identity.get("core_context_binding_id"),
        ),
        (
            "Task/Assignment task_id",
            ids.get("task_id"),
            assignment.get("task_id"),
        ),
        (
            "Task/Assignment trace_id",
            ids.get("trace_id"),
            assignment.get("trace_id"),
        ),
        (
            "Task/Assignment Binding",
            context.get("core_context_binding_id"),
            assignment.get("core_context_binding_id"),
        ),
        (
            "Task/Output task_id",
            ids.get("task_id"),
            output.get("task_id"),
        ),
        (
            "Task/Output trace_id",
            ids.get("trace_id"),
            output.get("trace_id"),
        ),
        (
            "Task/Output Binding",
            context.get("core_context_binding_id"),
            output.get("core_context_binding_id"),
        ),
        (
            "Assignment/Output assignment_id",
            assignment.get("assignment_id"),
            output.get("assignment_id"),
        ),
        (
            "Assignment/Output actor_instance_id",
            assignment.get("actor_instance_id"),
            output.get("actor_instance_id"),
        ),
        (
            "Assignment/Output role_id",
            assignment.get("role_id"),
            output.get("role_id"),
        ),
        (
            "Assignment/Output role_version",
            assignment.get("role_version"),
            output.get("role_version"),
        ),
    ]

    for name, left, right in required_equalities:
        if left != right:
            error(f"{label}: {name} mismatch: {left!r} != {right!r}")

    task_rules = context.get("active_core_rule_ids", [])
    loaded_rules = binding_rules.get("loaded_rule_ids", [])
    assignment_rules = assignment.get("active_core_rule_ids", [])

    if task_rules != loaded_rules:
        error(f"{label}: Task Rule IDs must exactly equal Binding loaded_rule_ids")

    if not set(assignment_rules).issubset(set(loaded_rules)):
        error(f"{label}: Assignment Rules are not a subset of Task/Binding loaded Rules")

    assignment_id = assignment.get("assignment_id")
    if assignment_id not in routing.get("role_assignment_ids", []):
        error(f"{label}: Assignment ID is not routed by the Task")

    role_id = assignment.get("role_id")
    if role_id not in routing.get("assigned_role_ids", []):
        error(f"{label}: Assignment Role ID is not routed by the Task")

    actor_id = assignment.get("actor_instance_id")
    if actor_id not in routing.get("assigned_actor_ids", []):
        error(f"{label}: Assignment actor is not routed by the Task")

    task_permission = task.get("permission", {})
    assignment_permission = assignment.get("permission", {})

    if (
        task_permission.get("permission_decision")
        != assignment_permission.get("permission_decision")
    ):
        error(f"{label}: Task and Assignment Permission Decisions differ")

    if (
        task_permission.get("permission_decision_ref")
        != assignment_permission.get("permission_decision_ref")
    ):
        error(f"{label}: Task and Assignment Permission refs differ")

    authority = assignment.get("authority", {})

    try:
        required = p_level(authority.get("required_permission_level"))
        effective = p_level(authority.get("effective_permission_level"))
        granted = p_level(authority.get("assignment_granted_permission_level"))
        ceiling = p_level(authority.get("role_permission_ceiling"))

        if not (required <= effective <= granted <= ceiling):
            error(
                f"{label}: authority invariant violated: "
                f"{required} <= {effective} <= {granted} <= {ceiling}"
            )
    except ValueError as exc:
        error(f"{label}: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate Task, Core Binding, Role Assignment, and Agent Output lineage."
    )
    parser.add_argument("--task")
    parser.add_argument("--binding")
    parser.add_argument("--assignment")
    parser.add_argument("--output")
    args = parser.parse_args()

    task_schema = load_schema("schemas/task.v0.3.schema.json")
    budget_schema = load_schema("schemas/execution_budget.v0.1.schema.json")
    binding_schema = load_schema("schemas/core_context_binding.v0.3.schema.json")
    assignment_schema = load_schema("schemas/role_assignment.v0.2.schema.json")
    output_schema = load_schema("schemas/agent_output.v0.2.schema.json")

    store = {
        item.get("$id"): item
        for item in [
            task_schema,
            budget_schema,
            binding_schema,
            assignment_schema,
            output_schema,
        ]
        if isinstance(item, dict) and item.get("$id")
    }

    supplied = [args.task, args.binding, args.assignment, args.output]

    if any(supplied) and not all(supplied):
        error("Provide all of --task, --binding, --assignment, and --output")

    if all(supplied):
        paths = [
            safe_repo_path(ROOT, value, must_exist=True)
            for value in [
                args.task,
                args.binding,
                args.assignment,
                args.output,
            ]
        ]
        labels = ["Task", "Binding", "Assignment", "Output"]
        objects = [load_yaml(path) for path in paths]
        schemas = [
            task_schema, binding_schema, assignment_schema, output_schema
        ]

        for label, obj, schema in zip(labels, objects, schemas):
            validate_schema(obj, schema, label, store)

        validate_bundle(*objects, label="CLI bundle")

    else:
        task = load_yaml(ROOT / "examples/tasks/task_v0.3_internal_analysis.yaml")
        binding = load_yaml(
            ROOT / "examples/runtime/core_context_binding_v0.3_internal_analysis.yaml"
        )
        assignment = load_yaml(
            ROOT / "examples/runtime/role_assignment_v0.2_internal_analysis.yaml"
        )
        output = load_yaml(
            ROOT / "examples/runtime/agent_output_v0.2_internal_analysis.yaml"
        )

        for label, obj, schema in [
            ("Task example", task, task_schema),
            ("Binding example", binding, binding_schema),
            ("Assignment example", assignment, assignment_schema),
            ("Output example", output, output_schema),
        ]:
            validate_schema(obj, schema, label, store)

        validate_bundle(task, binding, assignment, output, "Positive example")

        bad_assignment = load_yaml(
            ROOT / "tests/fixtures/runtime/invalid_assignment_binding_mismatch.yaml"
        )
        before = len(ERRORS)
        validate_bundle(task, binding, bad_assignment, output, "Negative assignment")
        if len(ERRORS) == before:
            error("Negative assignment Binding mismatch unexpectedly passed")
        else:
            del ERRORS[before:]

        bad_output = load_yaml(
            ROOT / "tests/fixtures/runtime/invalid_output_role_version_mismatch.yaml"
        )
        before = len(ERRORS)
        validate_bundle(task, binding, assignment, bad_output, "Negative output")
        if len(ERRORS) == before:
            error("Negative output Role mismatch unexpectedly passed")
        else:
            del ERRORS[before:]

    if ERRORS:
        print("FAIL: Runtime lineage validation found errors")
        for item in ERRORS:
            print(f" - {item}")
        return 1

    print("PASS: Runtime lineage bundle validation completed")
    print(
        "Validated Task, minimal Binding v0.3, Role Assignment v0.2, "
        "Agent Output v0.2, loaded-Rule lineage, Permission lineage, "
        "Role lineage, and authority ordering"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
