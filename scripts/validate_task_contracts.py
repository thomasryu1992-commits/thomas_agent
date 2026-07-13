#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []


def error(message: str) -> None:
    ERRORS.append(message)


def load_yaml(
    rel: str,
) -> dict[str, Any]:
    path = ROOT / rel

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


def load_json(
    rel: str,
) -> dict[str, Any]:
    path = ROOT / rel

    try:
        data = json.loads(
            path.read_text(encoding="utf-8")
        )
    except Exception as exc:
        error(
            f"{rel}: JSON parse failed: {exc}"
        )
        return {}

    if not isinstance(data, dict):
        error(
            f"{rel}: expected JSON object"
        )
        return {}

    return data


def build_validator():
    task_schema = load_json(
        "schemas/task.v0.3.schema.json"
    )
    budget_schema = load_json(
        "schemas/execution_budget.v0.1.schema.json"
    )

    registry = Registry()

    for schema in [
        task_schema,
        budget_schema,
    ]:
        schema_id = schema.get("$id")

        if isinstance(schema_id, str):
            registry = registry.with_resource(
                schema_id,
                Resource.from_contents(
                    schema
                ),
            )

    return (
        Draft202012Validator(
            task_schema,
            registry=registry,
            format_checker=FormatChecker(),
        ),
        task_schema,
    )


def schema_issues(
    validator,
    data: dict[str, Any],
) -> list[str]:
    return [
        (
            ".".join(
                str(part)
                for part in issue.path
            )
            or "<root>"
        )
        + ": "
        + issue.message
        for issue in sorted(
            validator.iter_errors(
                data
            ),
            key=lambda item: list(
                item.path
            ),
        )
    ]


def active_core_ids() -> set[str]:
    active = load_yaml(
        "THOMAS_CORE/MVP_ACTIVE_CORE.yaml"
    )

    return {
        item.get("id")
        for item in active.get(
            "active_rules",
            [],
        )
        if isinstance(item, dict)
        and isinstance(
            item.get("id"),
            str,
        )
    }


def main() -> int:
    validator, _ = build_validator()
    active_ids = active_core_ids()

    positive_examples = [
        "examples/tasks/"
        "task_v0.3_received_unbound.yaml",
        "examples/tasks/"
        "task_v0.3_internal_analysis.yaml",
        "examples/tasks/"
        "task_v0.3_waiting_approval.yaml",
        "examples/tasks/"
        "task_v0.3_learning_programization_review.yaml",
    ]

    for rel in positive_examples:
        data = load_yaml(rel)
        issues = schema_issues(
            validator,
            data,
        )

        if issues:
            error(
                f"{rel}: expected valid, got "
                f"{issues}"
            )

        context = data.get(
            "context",
            {},
        )
        lifecycle = data.get(
            "lifecycle",
            {},
        )
        binding_id = context.get(
            "core_context_binding_id"
        )

        if (
            lifecycle.get("status")
            == "RECEIVED"
        ):
            if binding_id is not None:
                error(
                    f"{rel}: RECEIVED example "
                    "should demonstrate null Binding"
                )
        elif not (
            isinstance(binding_id, str)
            and binding_id.startswith("ccb-")
        ):
            error(
                f"{rel}: post-RECEIVED Task "
                "must have a Core Binding"
            )

        rules = context.get(
            "active_core_rule_ids",
            [],
        )

        unknown = sorted(
            set(rules)
            - active_ids
        )

        if unknown:
            error(
                f"{rel}: design-time Rule IDs "
                f"are not in Active Core: {unknown}"
            )

    negative_schema_fixtures = [
        "tests/fixtures/tasks/"
        "invalid_permission_type_mix.yaml",
        "tests/fixtures/tasks/"
        "invalid_unbound_after_received.yaml",
        "tests/fixtures/tasks/"
        "invalid_allow_with_approval.yaml",
        "tests/fixtures/tasks/"
        "invalid_block_queued.yaml",
    ]

    for rel in negative_schema_fixtures:
        data = load_yaml(rel)

        if not schema_issues(
            validator,
            data,
        ):
            error(
                f"{rel}: negative fixture "
                "unexpectedly passed Schema"
            )

    unknown_rel = (
        "tests/fixtures/tasks/"
        "invalid_unknown_core_rule.yaml"
    )
    unknown_task = load_yaml(
        unknown_rel
    )
    unknown_schema_issues = schema_issues(
        validator,
        unknown_task,
    )

    if unknown_schema_issues:
        error(
            f"{unknown_rel}: generic Rule syntax "
            "should pass Schema and fail membership, "
            f"got {unknown_schema_issues}"
        )
    else:
        rules = (
            unknown_task.get(
                "context",
                {},
            ).get(
                "active_core_rule_ids",
                [],
            )
        )
        unknown = sorted(
            set(rules)
            - active_ids
        )

        if not unknown:
            error(
                f"{unknown_rel}: unknown Rule "
                "membership unexpectedly passed"
            )

    state_machine = load_yaml(
        "docs/runtime-contracts/"
        "TASK_STATE_MACHINE_V0.1.yaml"
    )
    states = state_machine.get(
        "states",
        {},
    )
    terminals = set(
        state_machine.get(
            "terminal_states",
            [],
        )
    )

    expected_states = {
        "RECEIVED",
        "CLASSIFIED",
        "PLANNED",
        "AUTHORIZING",
        "WAITING_APPROVAL",
        "QUEUED",
        "RUNNING",
        "VALIDATING",
        "REVISING",
        "RETRYING",
        "PAUSED",
        "BLOCKED",
        "FAILED",
        "CANCELED",
        "COMPLETED",
        "MEMORY_REVIEW",
        "CLOSED",
    }

    if set(states) != expected_states:
        error(
            "Task state set mismatch"
        )

    for state, config in states.items():
        for target in config.get(
            "allowed_next",
            [],
        ):
            if target not in states:
                error(
                    f"{state}: unknown transition "
                    f"target {target}"
                )

        if (
            not isinstance(
                config.get(
                    "required_guards"
                ),
                list,
            )
        ):
            error(
                f"{state}: required_guards "
                "must be a list"
            )

    for terminal in terminals:
        if (
            states.get(
                terminal,
                {},
            ).get(
                "allowed_next"
            )
            != []
        ):
            error(
                f"{terminal}: terminal state "
                "must not transition"
            )

    contract_text = (
        ROOT
        / "docs/runtime-contracts/"
        "TASK_CONTRACT_V0.3.md"
    ).read_text(encoding="utf-8")

    for token in [
        "task.v0.3",
        "core_context_binding_id",
        "null` only while its lifecycle status is `RECEIVED`",
        "bound Release snapshot",
        "required_permission_level",
        "permission_decision",
        "permission_decision_ref",
        "role_assignment_ids",
        "execution_budget.v0.1",
        "task_revision",
        "SUPERSEDED",
        "WAITING_APPROVAL",
        "action_fingerprint",
    ]:
        if token not in contract_text:
            error(
                "TASK_CONTRACT_V0.3.md "
                f"missing required token: {token}"
            )

    if ERRORS:
        print(
            "FAIL: Task Contract v0.3 "
            "validation found errors"
        )

        for item in ERRORS:
            print(f" - {item}")

        return 1

    print(
        "PASS: Task Contract v0.3 "
        "validation completed"
    )
    print(
        "Validated 4 positive examples, "
        "5 negative cases, RECEIVED-only null "
        "Binding, Permission invariants, "
        "design-time Rule membership, Task states, "
        "and bound-Release Runtime guidance"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
