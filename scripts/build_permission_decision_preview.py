#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker

from lib.action_fingerprint import compute_action_fingerprint
from validate_permission_approval_contracts import POLICY_REL, load_yaml, validate_permission_record


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build and validate a review-only Permission Decision candidate. "
            "This command does not choose a decision or grant execution permission."
        )
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    source = Path(args.input)
    output = Path(args.output)
    if output.exists() and not args.force:
        raise FileExistsError(f"Refusing to overwrite existing file: {output}")

    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Input must be a YAML mapping")

    payload = data.get("fingerprint_payload")
    if not isinstance(payload, dict):
        raise ValueError("fingerprint_payload is required")
    data["action_fingerprint"] = compute_action_fingerprint(payload)

    schema = json.loads(
        (root / "schemas/permission_decision.v0.3.schema.json").read_text(
            encoding="utf-8"
        )
    )
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    schema_errors = sorted(validator.iter_errors(data), key=lambda item: list(item.path))
    policy = load_yaml(POLICY_REL)
    semantic_errors = validate_permission_record(data, policy)
    if schema_errors or semantic_errors:
        print("FAIL: Permission Decision preview is invalid")
        for issue in schema_errors:
            path = ".".join(str(part) for part in issue.path) or "<root>"
            print(f" - {path}: {issue.message}")
        for issue in semantic_errors:
            print(f" - {issue}")
        return 1

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=110),
        encoding="utf-8",
        newline="\n",
    )
    print(f"PASS: review-only Permission Decision preview written to {output}")
    print("No Approval, executor handoff, external action, or Runtime mutation was granted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
