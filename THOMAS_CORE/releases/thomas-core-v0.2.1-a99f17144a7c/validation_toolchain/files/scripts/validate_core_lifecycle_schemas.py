#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []


def validate(rel: str, schema_rel: str, expected: bool) -> None:
    data = yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))
    schema = json.loads((ROOT / schema_rel).read_text(encoding="utf-8"))
    issues = list(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(data)
    )
    if expected and issues:
        ERRORS.append(f"{rel}: {[item.message for item in issues]}")
    if not expected and not issues:
        ERRORS.append(f"{rel}: negative fixture unexpectedly passed")


def main() -> int:
    validate(
        "examples/core_release/core_release_approval_runtime_authoritative_v0.3.yaml",
        "schemas/thomas_core_release_approval.v0.3.schema.json",
        True,
    )
    validate(
        "tests/fixtures/core_release/invalid_unverified_runtime_approval.yaml",
        "schemas/thomas_core_release_approval.v0.3.schema.json",
        False,
    )

    required_schemas = [
        "thomas_core_release_manifest.v0.3.schema.json",
        "thomas_core_release_approval.v0.3.schema.json",
        "core_activation.v0.1.schema.json",
        "core_deactivation.v0.1.schema.json",
        "core_revocation.v0.1.schema.json",
        "current_core_release.v0.2.schema.json",
    ]
    for name in required_schemas:
        path = ROOT / "schemas" / name
        if not path.exists():
            ERRORS.append(f"Missing lifecycle Schema: {name}")
            continue
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            ERRORS.append(f"{name}: {exc}")

    if ERRORS:
        print("FAIL: Core lifecycle Schema validation found errors")
        for item in ERRORS:
            print(f" - {item}")
        return 1

    print("PASS: Core lifecycle Schema validation completed")
    print(
        "Validated one Runtime-authoritative Approval meaning and the Manifest, "
        "Activation, Deactivation, Revocation, and Current pointer Schema set"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
