#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []


def error(message: str) -> None:
    ERRORS.append(message)


def load_json(rel: str):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def load_yaml(rel: str):
    return yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))


def validate(rel: str, schema_rel: str, expected_valid: bool) -> None:
    data = load_yaml(rel)
    schema = load_json(schema_rel)
    issues = list(
        Draft202012Validator(
            schema,
            format_checker=FormatChecker(),
        ).iter_errors(data)
    )

    if expected_valid and issues:
        error(f"{rel}: expected valid: {[item.message for item in issues]}")

    if not expected_valid and not issues:
        error(f"{rel}: expected invalid, but passed")


def main() -> int:
    cases = [
        (
            "examples/programization/programization_observation_v0.1.yaml",
            "schemas/programization_observation.v0.1.schema.json",
            True,
        ),
        (
            "examples/programization/programization_pattern_v0.1.yaml",
            "schemas/programization_pattern.v0.1.schema.json",
            True,
        ),
        (
            "examples/programization/programization_candidate_v0.1.yaml",
            "schemas/programization_candidate.v0.1.schema.json",
            True,
        ),
        (
            "examples/learning/operational_knowledge_v0.1.yaml",
            "schemas/operational_knowledge.v0.1.schema.json",
            True,
        ),
        (
            "tests/fixtures/programization/invalid_synthetic_counted_valid.yaml",
            "schemas/programization_observation.v0.1.schema.json",
            False,
        ),
        (
            "tests/fixtures/programization/invalid_ten_without_trigger.yaml",
            "schemas/programization_pattern.v0.1.schema.json",
            False,
        ),
        (
            "tests/fixtures/programization/invalid_permission_expansion.yaml",
            "schemas/programization_candidate.v0.1.schema.json",
            False,
        ),
    ]

    for rel, schema, valid in cases:
        validate(rel, schema, valid)

    candidate = load_yaml(
        "examples/programization/programization_candidate_v0.1.yaml"
    )

    if candidate.get("activation_eligibility") != (
        "candidate_only_pending_program_registry_and_permission_policy"
    ):
        error("Program Candidate activation eligibility is too permissive")

    if candidate.get("permission_expansion") is not False:
        error("Programization must never expand Permission")

    knowledge = load_yaml(
        "examples/learning/operational_knowledge_v0.1.yaml"
    )

    for key in [
        "validated_at_utc",
        "review_due_at_utc",
        "last_confirmed_at_utc",
        "environment_signature",
        "confidence",
        "status",
    ]:
        if key not in knowledge:
            error(f"Operational Knowledge missing lifecycle field: {key}")

    if ERRORS:
        print("FAIL: Programization and Operational Knowledge validation found errors")
        for item in ERRORS:
            print(f" - {item}")
        return 1

    print("PASS: Programization and Operational Knowledge validation completed")
    print(
        "Validated independent repetition exclusions, 10-count Review trigger, "
        "Candidate-only activation eligibility, no Permission expansion, "
        "and knowledge review/expiry fields"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
