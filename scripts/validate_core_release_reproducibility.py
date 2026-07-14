#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker

from lib.core_release_verifier import (
    load_yaml,
    verify_activation_record,
    verify_approval,
    verify_current_pointer,
    verify_deactivation_record,
    verify_manifest,
)
from lib.git_provenance import (
    require_file_tracked_at_head,
)
from lib.safe_io import (
    SafeIOError,
    safe_repo_path,
)

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = ROOT / "THOMAS_CORE/CORE_RELEASE_MANIFEST_TEMPLATE.yaml"
REVIEW_POINTER_PATH = ROOT / "THOMAS_CORE/REVIEW_CORE_RELEASE.yaml"
DEFAULT_CURRENT_POINTER = ROOT / "THOMAS_CORE/CURRENT_CORE_RELEASE.yaml"
BINDING_SCHEMA_PATH = ROOT / "schemas/core_context_binding.v0.3.schema.json"

ERRORS: list[str] = []


def error(message: str) -> None:
    ERRORS.append(message)


def resolve_optional(
    value: str | None,
    *,
    must_exist: bool = False,
) -> Path | None:
    if not value:
        return None

    try:
        return safe_repo_path(
            ROOT,
            value,
            must_exist=must_exist,
        )
    except Exception as exc:
        error(f"Unsafe or invalid path {value!r}: {exc}")
        return None


def validate_actual_schema(
    path: Path,
    schema_rel: str,
    label: str,
) -> None:
    try:
        instance = load_yaml(path)
        schema = json.loads(
            (ROOT / schema_rel).read_text(encoding="utf-8")
        )
        issues = list(
            Draft202012Validator(
                schema,
                format_checker=FormatChecker(),
            ).iter_errors(instance)
        )
        if issues:
            error(
                f"{label} Schema validation failed: "
                + "; ".join(item.message for item in issues)
            )
    except Exception as exc:
        error(f"{label} Schema validation failed: {exc}")


def validate_binding(
    instance: dict[str, Any],
    label: str,
    *,
    expected_valid: bool,
) -> None:
    schema = json.loads(
        BINDING_SCHEMA_PATH.read_text(
            encoding="utf-8"
        )
    )

    validator = Draft202012Validator(
        schema,
        format_checker=FormatChecker(),
    )

    issues = [
        item.message
        for item in sorted(
            validator.iter_errors(instance),
            key=lambda item: list(item.path),
        )
    ]

    valid = not issues

    if expected_valid and not valid:
        error(
            f"{label}: expected valid, got {issues}"
        )

    if not expected_valid and valid:
        error(
            f"{label}: expected invalid, but passed"
        )


def check_template() -> None:
    try:
        template = load_yaml(TEMPLATE_PATH)
    except Exception as exc:
        error(
            f"Release template load failed: {exc}"
        )
        return

    if (
        template.get("schema_version")
        != "thomas_core_release_manifest_template.v0.3"
    ):
        error(
            "Release template schema must be v0.3"
        )

    if template.get("runtime_use_allowed") is not False:
        error(
            "Release template must not allow Runtime use"
        )

    if (
        template.get("definition_status")
        != "thomas_approved"
    ):
        error(
            "Release template definition_status "
            "must be thomas_approved"
        )

    file_set = template.get("release_file_set")

    if not isinstance(file_set, list) or not file_set:
        error(
            "Release template file set must be non-empty"
        )

    if (
        isinstance(file_set, list)
        and "docs/MVP_OPERATING_POLICY.md"
        in file_set
    ):
        error(
            "Full MVP Operating Policy must not be "
            "part of the Core semantic bundle"
        )

    if (
        isinstance(file_set, list)
        and "THOMAS_CORE/"
        "CORE_RUNTIME_POLICY_PROJECTION.yaml"
        not in file_set
    ):
        error(
            "Core Runtime Policy Projection must be "
            "part of the Core Release"
        )

    gate_evidence = template.get(
        "release_gate_evidence"
    )

    if gate_evidence != "generated/release_gate/RELEASE_GATE_EVIDENCE.yaml":
        error(
            "Template must reference the canonical Release Gate evidence path"
        )

    if "required_validators" in template:
        error(
            "Validator lists are owned by the Release Gate code and must not be duplicated in Core YAML"
        )

    if (
        "required_validation_commands"
        in template
    ):
        error(
            "Executable validation command strings "
            "must not be stored in Core YAML"
        )

    if (
        template.get("validation_lock_file")
        != "requirements-validation.lock"
    ):
        error(
            "Release template must use the exact "
            "validation lock file"
        )


def check_binding_contract() -> None:
    if not BINDING_SCHEMA_PATH.exists():
        error(
            "Missing Core Context Binding v0.3 schema"
        )
        return

    positive = (
        ROOT
        / "examples/core_context/"
        "core_context_binding_v0.3.yaml"
    )

    if not positive.exists():
        error(
            "Missing positive Core Context Binding "
            "v0.3 example"
        )
    else:
        validate_binding(
            load_yaml(positive),
            positive.relative_to(ROOT).as_posix(),
            expected_valid=True,
        )

    fixtures = [
        "tests/fixtures/core_context/"
        "invalid_empty_loaded_rules.yaml",
        "tests/fixtures/core_context/"
        "invalid_silent_mid_task_rebind.yaml",
        "tests/fixtures/core_context/"
        "invalid_rebind_lineage.yaml",
    ]

    for rel in fixtures:
        path = ROOT / rel

        if not path.exists():
            error(
                f"Missing Binding negative fixture: {rel}"
            )
            continue

        validate_binding(
            load_yaml(path),
            rel,
            expected_valid=False,
        )


def resolve_manifest(
    explicit: str | None,
    *,
    required: bool,
) -> Path | None:
    if explicit:
        return resolve_optional(
            explicit,
            must_exist=True,
        )

    if REVIEW_POINTER_PATH.exists():
        try:
            review = load_yaml(
                REVIEW_POINTER_PATH
            )
            value = review.get(
                "manifest_path"
            )

            if isinstance(value, str):
                return resolve_optional(
                    value,
                    must_exist=True,
                )
        except Exception as exc:
            error(
                f"Review pointer is invalid: {exc}"
            )

    if required:
        error(
            "A Core Release Manifest is required. "
            "Build one or pass --manifest."
        )

    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate self-contained Core Release "
            "snapshots, Approval authority, Activation "
            "history, Current pointer, and Binding v0.3."
        )
    )
    parser.add_argument("--manifest")
    parser.add_argument("--approval")
    parser.add_argument("--activation")
    parser.add_argument("--deactivation")
    parser.add_argument("--current-pointer")
    parser.add_argument(
        "--require-manifest",
        action="store_true",
    )
    parser.add_argument(
        "--require-approved",
        action="store_true",
    )
    parser.add_argument(
        "--require-activation",
        action="store_true",
    )
    parser.add_argument(
        "--require-current",
        action="store_true",
    )
    parser.add_argument(
        "--require-current-committed",
        action="store_true",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
    )
    args = parser.parse_args()

    for rel in [
        "THOMAS_CORE/CORE_RELEASE_MANIFEST.yaml",
        "THOMAS_CORE/CORE_RELEASE_APPROVAL.yaml",
    ]:
        if (ROOT / rel).exists():
            error(
                f"Deprecated fixed Release file exists: "
                f"{rel}"
            )

    check_template()
    check_binding_contract()

    manifest_path = resolve_manifest(
        args.manifest,
        required=(
            args.require_manifest
            or args.require_approved
            or args.require_activation
            or args.strict
        ),
    )

    manifest = None

    if manifest_path is not None:
        try:
            manifest = verify_manifest(
                ROOT,
                manifest_path,
            )
            validate_actual_schema(
                manifest_path,
                "schemas/thomas_core_release_manifest.v0.3.schema.json",
                "Release Manifest",
            )
        except Exception as exc:
            error(
                f"Manifest verification failed: {exc}"
            )

    approval_path = resolve_optional(
        args.approval,
        must_exist=bool(args.approval),
    )

    if (
        args.require_approved
    ) and approval_path is None:
        error(
            "Approval is required but --approval "
            "was not provided"
        )

    if (
        manifest_path is not None
        and approval_path is not None
    ):
        try:
            verify_approval(
                ROOT,
                manifest_path,
                approval_path,
            )
            validate_actual_schema(
                approval_path,
                "schemas/thomas_core_release_approval.v0.3.schema.json",
                "Release Approval",
            )
        except Exception as exc:
            error(
                f"Approval verification failed: {exc}"
            )

    activation_path = resolve_optional(
        args.activation,
        must_exist=bool(args.activation),
    )

    if (
        args.require_activation
        and activation_path is None
    ):
        error(
            "Activation is required but "
            "--activation was not provided"
        )

    if activation_path is not None:
        try:
            verify_activation_record(
                ROOT,
                activation_path,
            )
            validate_actual_schema(
                activation_path,
                "schemas/core_activation.v0.1.schema.json",
                "Core Activation",
            )
        except Exception as exc:
            error(
                f"Activation verification failed: {exc}"
            )

    deactivation_path = resolve_optional(
        args.deactivation,
        must_exist=bool(args.deactivation),
    )

    if deactivation_path is not None:
        try:
            verify_deactivation_record(
                ROOT,
                deactivation_path,
            )
            validate_actual_schema(
                deactivation_path,
                "schemas/core_deactivation.v0.1.schema.json",
                "Core Deactivation",
            )
        except Exception as exc:
            error(
                f"Deactivation verification failed: {exc}"
            )

    current_path = resolve_optional(
        args.current_pointer,
        must_exist=bool(args.current_pointer),
    )

    if (
        args.require_current
        or args.require_current_committed
    ) and current_path is None:
        current_path = DEFAULT_CURRENT_POINTER

    if current_path is not None:
        if not current_path.exists():
            error(
                "Current Core pointer is required "
                "but missing"
            )
        else:
            try:
                verify_current_pointer(
                    ROOT,
                    current_path,
                )
                validate_actual_schema(
                    current_path,
                    "schemas/current_core_release.v0.2.schema.json",
                    "Current Core pointer",
                )
            except Exception as exc:
                error(
                    "Current Core pointer verification "
                    f"failed: {exc}"
                )

            if args.require_current_committed:
                try:
                    require_file_tracked_at_head(
                        ROOT,
                        current_path,
                    )
                except Exception as exc:
                    error(
                        "Current Core pointer is not "
                        f"committed exactly at HEAD: {exc}"
                    )

    if ERRORS:
        print(
            "FAIL: Thomas Core Release "
            "reproducibility validation found errors"
        )

        for item in ERRORS:
            print(f" - {item}")

        return 1

    print(
        "PASS: Thomas Core Release "
        "reproducibility validation completed"
    )
    print(
        "Release Manifest: "
        + (
            "self-contained and reproducible"
            if manifest is not None
            else "not built yet"
        )
    )
    print(
        "Approval: "
        + (
            "present and valid"
            if approval_path is not None
            else "not provided"
        )
    )
    print(
        "Activation: "
        + (
            "present and valid"
            if activation_path is not None
            else "not provided"
        )
    )
    print(
        "Current Runtime pointer: "
        + (
            "present and valid"
            if current_path is not None
            and current_path.exists()
            else "not provided"
        )
    )
    print(
        "Checked immutable artifact snapshots, "
        "historical validator snapshots, dependency "
        "lock evidence, safe paths, Approval authority, "
        "Revocation, Activation/Deactivation, Current "
        "pointer, and Binding v0.3 fixtures"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
