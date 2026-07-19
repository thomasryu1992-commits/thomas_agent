#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import shutil
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib.runtime_promotion_readiness import (
    CURRENT_CORE_REL,
    EXPECTED_JOB_NAMES,
    EXPECTED_WORKFLOW_NAME,
    GATE_EVIDENCE_REL,
    REGISTRY_REL,
    REQUIRED_GATE_CHECKS,
    WORKFLOW_REL,
    _gate_evidence_status,
    _expected_design_blocking_reasons,
    _expected_activation_blocking_reasons,
    build_component_attestation,
    build_github_ci_evidence,
    build_runtime_promotion_readiness,
    load_yaml,
    sha256_file,
    validate_component_attestation_semantics,
    validate_github_ci_evidence_semantics,
    validate_runtime_promotion_readiness_semantics,
    verify_current_core_release,
    verify_github_ci_evidence,
)

FIXED_NOW = "2026-07-13T08:00:00Z"
REGISTRY_SCHEMA_REL = "schemas/i0_5_read_only_runtime_components.v0.1.schema.json"
ATTESTATION_SCHEMA_REL = "schemas/runtime_component_attestation.v0.1.schema.json"
CI_SCHEMA_REL = "schemas/github_ci_evidence.v0.1.schema.json"
READINESS_SCHEMA_REL = "schemas/runtime_promotion_readiness.v0.1.schema.json"


def validate_schema(value: Any, schema_path: Path, label: str) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        rendered = "; ".join(
            f"{'.'.join(str(part) for part in item.absolute_path) or '$'}: {item.message}"
            for item in errors[:5]
        )
        raise AssertionError(f"{label}: schema validation failed: {rendered}")


def set_path(record: Any, dotted_path: str, value: Any) -> None:
    current = record
    parts = dotted_path.split(".")
    for part in parts[:-1]:
        current = current[int(part)] if isinstance(current, list) else current[part]
    last = parts[-1]
    if isinstance(current, list):
        current[int(last)] = value
    else:
        current[last] = value


def assert_gate_integration() -> None:
    repo_gate = (ROOT / "scripts/run_repository_release_gate.py").read_text(encoding="utf-8")
    if "--check-only" not in repo_gate:
        raise AssertionError("run_repository_release_gate.py must expose --check-only for CI")
    if "validate_i0_5_1_runtime_promotion_readiness.py" not in repo_gate:
        raise AssertionError("Repository Gate must include I0.5.1 readiness validation")

    focused_gate = (ROOT / "scripts/run_i0_5_read_only_runtime_gate.py").read_text(encoding="utf-8")
    if "validate_i0_5_1_runtime_promotion_readiness.py" not in focused_gate:
        raise AssertionError("I0.5 focused gate must include I0.5.1 readiness validation")

    release_evidence = (ROOT / "scripts/lib/release_gate_evidence.py").read_text(encoding="utf-8")
    if '".github/workflows"' not in release_evidence:
        raise AssertionError("GitHub workflow source must be included in repository source fingerprint")
    if '"runtime"' not in release_evidence:
        raise AssertionError("Runtime implementation source must be included in repository source fingerprint")

    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    for token in ["runtime/** text eol=lf", ".github/workflows/** text eol=lf"]:
        if token not in attributes:
            raise AssertionError(f".gitattributes missing Gate-owned LF rule: {token}")


def assert_workflow() -> None:
    path = ROOT / WORKFLOW_REL
    if not path.is_file():
        raise AssertionError("GitHub Actions runtime validation workflow is missing")
    text = path.read_text(encoding="utf-8")
    required = [
        "actions/checkout@v6",
        "actions/setup-python@v6",
        "python -m pip install -r requirements-validation.lock",
        "python scripts/run_repository_release_gate.py --full --check-only",
        "ubuntu-latest",
        "windows-latest",
        "contents: read",
        "persist-credentials: false",
    ]
    for token in required:
        if token not in text:
            raise AssertionError(f"workflow missing required token: {token}")
    forbidden = ["contents: write", "pull-requests: write", "id-token: write", "secrets."]
    for token in forbidden:
        if token in text:
            raise AssertionError(f"workflow contains forbidden token: {token}")


def assert_builder_no_boolean_bypass() -> None:
    source = (ROOT / "scripts/build_i0_5_1_runtime_promotion_readiness.py").read_text(encoding="utf-8")
    forbidden = ["--current-core-verified", "--github-ci-status", "current_core_release_verified="]
    for token in forbidden:
        if token in source:
            raise AssertionError(f"readiness builder retains forbidden manual verification bypass: {token}")
    if "--github-ci-evidence" not in source:
        raise AssertionError("readiness builder must accept a structured GitHub CI evidence record")


def assert_current_core_uses_existing_verifier() -> None:
    source = (ROOT / "scripts/lib/runtime_promotion_readiness.py").read_text(encoding="utf-8")
    for token in [
        "verify_current_pointer",
        "verify_activation_record",
        "require_file_tracked_at_head",
        "approved_via_activation_registry",
    ]:
        if token not in source:
            raise AssertionError(f"Current Core verification is missing required existing verifier binding: {token}")


def assert_no_runtime_enablement() -> None:
    registry = load_yaml(ROOT / REGISTRY_REL)
    if registry.get("runtime_source_of_truth") is not False:
        raise AssertionError("I0.5 registry must remain non-authoritative")
    if registry.get("runtime_authoritative_mode_enabled") is not False:
        raise AssertionError("I0.5 authoritative mode must remain disabled")
    if any(item.get("status") != "candidate" for item in registry.get("components", [])):
        raise AssertionError("I0.5 components must remain candidates")
    effects = registry.get("review_only_effects", {})
    if not effects or any(value is not False for value in effects.values()):
        raise AssertionError("I0.5 registry review-only effects must all remain false")


def validate_positive() -> tuple[dict[str, Any], dict[str, Any]]:
    registry = load_yaml(ROOT / REGISTRY_REL)
    validate_schema(registry, ROOT / REGISTRY_SCHEMA_REL, "component registry")

    attestation = build_component_attestation(ROOT, created_at=FIXED_NOW)
    validate_schema(attestation, ROOT / ATTESTATION_SCHEMA_REL, "component attestation")
    validate_component_attestation_semantics(attestation)
    if attestation["summary"]["result"] != "PASS":
        raise AssertionError(f"component attestation must PASS: {attestation['components']}")

    versions = {item["component_id"]: item["implementation_version"] for item in attestation["components"]}
    if versions.get("thomas.read_only_runtime_kernel") != "0.1.1":
        raise AssertionError("kernel implementation must attest as 0.1.1")
    if versions.get("kernel.contract_inspection.readonly") != "0.1.0":
        raise AssertionError("worker implementation must attest as 0.1.0")

    readiness = build_runtime_promotion_readiness(ROOT, created_at=FIXED_NOW)
    validate_schema(readiness, ROOT / READINESS_SCHEMA_REL, "runtime promotion readiness")
    validate_runtime_promotion_readiness_semantics(readiness)
    if readiness["summary"]["design_readiness"]["result"] != "BLOCKED_NOT_READY":
        raise AssertionError("default Design Readiness must remain blocked until verified CI exists")
    if "GITHUB_CI_EVIDENCE_MISSING" not in readiness["summary"]["design_readiness"]["blocking_reasons"]:
        raise AssertionError("default Design Readiness must contain the CI evidence blocker")
    if "CURRENT_CORE_RELEASE_MISSING" in readiness["summary"]["design_readiness"]["blocking_reasons"]:
        raise AssertionError("Current Core must not block Design Readiness")
    if "CURRENT_CORE_RELEASE_MISSING" not in readiness["summary"]["activation_readiness"]["blocking_reasons"]:
        raise AssertionError("Current Core must block Activation Readiness")
    return attestation, readiness


def _create_fake_git_repo(root: Path, sha: str, repository: str) -> None:
    git = root / ".git"
    (git / "refs/heads").mkdir(parents=True, exist_ok=True)
    (git / "HEAD").write_text("ref: refs/heads/test\n", encoding="utf-8")
    (git / "refs/heads/test").write_text(sha + "\n", encoding="utf-8")
    (git / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n"
        f"[remote \"origin\"]\n\turl = https://github.com/{repository}.git\n",
        encoding="utf-8",
    )


def _valid_ci_evidence(repo: Path, *, head_sha: str, repository: str) -> dict[str, Any]:
    return build_github_ci_evidence(
        repository_full_name=repository,
        workflow_name=EXPECTED_WORKFLOW_NAME,
        workflow_path=WORKFLOW_REL,
        workflow_sha256=sha256_file(repo / WORKFLOW_REL),
        run_id=123456,
        run_attempt=1,
        event="push",
        head_sha=head_sha,
        html_url=f"https://github.com/{repository}/actions/runs/123456",
        created_at="2026-07-13T07:00:00Z",
        completed_at="2026-07-13T07:10:00Z",
        ubuntu_job_id=111,
        ubuntu_job_name=EXPECTED_JOB_NAMES["ubuntu"],
        ubuntu_completed_at="2026-07-13T07:08:00Z",
        windows_job_id=222,
        windows_job_name=EXPECTED_JOB_NAMES["windows"],
        windows_completed_at="2026-07-13T07:09:00Z",
        collected_at="2026-07-13T07:11:00Z",
    )


def validate_ci_evidence() -> int:
    mutation_count = 0
    with tempfile.TemporaryDirectory(prefix="i0_5_1_ci_") as tmp:
        repo = Path(tmp)
        workflow = repo / WORKFLOW_REL
        workflow.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / WORKFLOW_REL, workflow)
        ci_schema = repo / CI_SCHEMA_REL
        ci_schema.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / CI_SCHEMA_REL, ci_schema)
        head_sha = "a" * 40
        repository = "thomasryu1992-commits/thomas_agent"
        _create_fake_git_repo(repo, head_sha, repository)
        evidence = _valid_ci_evidence(repo, head_sha=head_sha, repository=repository)
        validate_schema(evidence, ROOT / CI_SCHEMA_REL, "GitHub CI evidence")
        validate_github_ci_evidence_semantics(evidence)
        evidence_path = repo / "generated/deferred/runtime_entry/i0_5_1_runtime_promotion/GITHUB_CI_EVIDENCE.yaml"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(yaml.safe_dump(evidence, sort_keys=False), encoding="utf-8")
        result = verify_github_ci_evidence(repo, evidence_path.relative_to(repo).as_posix())
        if not result["verified"]:
            raise AssertionError(f"valid GitHub CI evidence must verify: {result['reasons']}")

        cases = [
            ("run.head_sha", "b" * 40),
            ("workflow.sha256", "sha256:" + "0" * 64),
            ("repository_full_name", "other/repo"),
            ("run.conclusion", "failure"),
            ("jobs.ubuntu.conclusion", "failure"),
            ("jobs.windows.conclusion", "failure"),
            ("jobs.ubuntu.name", "wrong ubuntu job"),
            ("jobs.windows.name", "wrong windows job"),
            ("jobs.windows.job_id", 111),
            ("source.live_api_verified", False),
            ("source.source_type", "MANUAL"),
            ("integrity.evidence_sha256", "sha256:" + "0" * 64),
        ]
        for path, value in cases:
            mutated = deepcopy(evidence)
            set_path(mutated, path, value)
            if not path.startswith("integrity."):
                # Deliberately keep the original fingerprint to prove mutation detection.
                pass
            try:
                validate_github_ci_evidence_semantics(mutated)
            except Exception:
                mutation_count += 1
            else:
                raise AssertionError(f"GitHub CI mutation must block: {path}")
    return mutation_count


def validate_gate_required_checks() -> int:
    with tempfile.TemporaryDirectory(prefix="i0_5_1_gate_") as tmp:
        repo = Path(tmp)
        # Copy enough Gate-owned source for the shared fingerprint helper.
        (repo / "scripts/lib").mkdir(parents=True, exist_ok=True)
        (repo / "docs").mkdir(parents=True, exist_ok=True)
        (repo / ".gitattributes").write_text("runtime/** text eol=lf\n", encoding="utf-8")
        from lib.release_gate_evidence import repository_source_fingerprint

        fingerprint, entries = repository_source_fingerprint(repo)
        checks = [
            {
                "check_id": check_id,
                "label": label,
                "command": "python validator.py",
                "result": "PASS",
                "output_sha256": "sha256:" + "1" * 64,
            }
            for label, check_id in REQUIRED_GATE_CHECKS.items()
        ]
        evidence = {
            "schema_version": "thomas_release_gate_evidence.v0.1",
            "result": "PASS",
            "repository_source_fingerprint": fingerprint,
            "source_file_count": len(entries),
            "checks": checks,
        }
        path = repo / GATE_EVIDENCE_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(evidence, sort_keys=False), encoding="utf-8")
        status = _gate_evidence_status(repo)
        if not status["required_checks_pass"]:
            raise AssertionError(f"complete required Gate check set must pass: {status}")

        evidence["checks"] = checks[:-1]
        path.write_text(yaml.safe_dump(evidence, sort_keys=False), encoding="utf-8")
        status = _gate_evidence_status(repo)
        if status["required_checks_pass"]:
            raise AssertionError("missing required Gate check must block readiness")
        if "RELEASE_GATE_REQUIRED_CHECKS_MISSING_OR_NOT_PASS" not in status["reasons"]:
            raise AssertionError("missing required Gate check must produce explicit reason")
    return 2


def validate_current_core_fail_closed() -> int:
    with tempfile.TemporaryDirectory(prefix="i0_5_1_current_") as tmp:
        repo = Path(tmp)
        path = repo / CURRENT_CORE_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "schema_version: current_core_release.v0.2\n"
            "runtime_activation_status: approved_via_activation_registry\n"
            "activation_path: THOMAS_CORE/activations/not-real.yaml\n",
            encoding="utf-8",
        )
        result = verify_current_core_release(repo)
        if not result["present"] or result["verified"] or result["committed"]:
            raise AssertionError("invalid Current Core pointer must be present but unverified and uncommitted")
        if not result["reasons"]:
            raise AssertionError("invalid Current Core pointer must produce an explicit reason")
    return 1


def validate_readiness_split(base: dict[str, Any]) -> int:
    requirements = deepcopy(base["requirements"])
    for name in [
        "component_attestation_pass",
        "i0_4_contract_lock_present",
        "release_gate_evidence_present",
        "release_gate_evidence_pass",
        "release_gate_fingerprint_current",
        "release_gate_required_checks_pass",
        "github_workflow_present",
        "github_ci_evidence_present",
        "github_ci_evidence_verified",
        "github_ci_head_matches",
        "github_ci_workflow_matches",
        "github_ci_repository_matches",
        "github_ci_ubuntu_pass",
        "github_ci_windows_pass",
        "review_core_release_present",
        "tool_registry_no_enabled_tools",
        "program_registry_no_enabled_programs",
    ]:
        requirements[name] = True
    requirements["github_ci_evidence_ref"] = "generated/deferred/runtime_entry/i0_5_1_runtime_promotion/GITHUB_CI_EVIDENCE.yaml"
    requirements["runtime_registry_source_of_truth"] = False
    requirements["runtime_authoritative_mode_enabled"] = False
    requirements["current_core_release_present"] = False
    requirements["current_core_release_verified"] = False
    requirements["current_core_release_committed"] = False

    design_blockers = _expected_design_blocking_reasons(requirements)
    activation_blockers = _expected_activation_blocking_reasons(requirements)
    if design_blockers:
        raise AssertionError(f"all Design prerequisites except Current Core must produce Design READY: {design_blockers}")
    if activation_blockers != ["CURRENT_CORE_RELEASE_MISSING"]:
        raise AssertionError(f"missing Current Core must block Activation only: {activation_blockers}")

    requirements["current_core_release_present"] = True
    requirements["current_core_release_verified"] = True
    requirements["current_core_release_committed"] = True
    if _expected_activation_blocking_reasons(requirements):
        raise AssertionError("verified Current Core must remove the final Activation Readiness blocker")
    return 4


def validate_readiness_mutations(base: dict[str, Any]) -> int:
    cases = [
        ("runtime_effect.grants_runtime_activation", True, "schema"),
        ("summary.result", "READY_FOR_THOMAS_DESIGN_DECISION", "semantic"),
        ("summary.blocking_reasons", [], "semantic"),
        ("summary.design_readiness.result", "READY_FOR_THOMAS_DESIGN_DECISION", "semantic"),
        ("summary.design_readiness.blocking_reasons", [], "semantic"),
        ("summary.activation_readiness.result", "READY_FOR_RUNTIME_ACTIVATION_REVIEW", "semantic"),
        ("summary.activation_readiness.blocking_reasons", [], "semantic"),
        ("summary.ready_for_runtime_activation_review", True, "semantic"),
        ("component_attestation.result", "BLOCK", "semantic"),
        ("requirements.github_ci_evidence_verified", True, "semantic"),
        ("requirements.github_ci_evidence_present", True, "semantic"),
        ("requirements.current_core_release_verified", True, "semantic"),
        ("requirements.current_core_release_committed", True, "semantic"),
        ("requirements.runtime_registry_source_of_truth", True, "schema"),
        ("summary.ready_for_runtime_activation", True, "schema"),
    ]
    count = 0
    for path, value, kind in cases:
        mutated = deepcopy(base)
        set_path(mutated, path, value)
        try:
            if kind == "schema":
                validate_schema(mutated, ROOT / READINESS_SCHEMA_REL, path)
            else:
                validate_runtime_promotion_readiness_semantics(mutated)
        except Exception:
            count += 1
        else:
            raise AssertionError(f"readiness mutation must block: {path}")
    return count


def validate_component_mutations() -> int:
    cases = [
        ("components.0.version", "0.1.0"),
        ("components.1.version", "0.1.1"),
        ("components.0.component_id", "thomas.wrong_kernel"),
        ("components.1.component_id", "kernel.wrong_worker"),
        ("components.0.implementation_ref", "runtime/read_only_kernel/worker.py"),
        ("runtime_source_of_truth", True),
        ("runtime_authoritative_mode_enabled", True),
        ("components.0.status", "active"),
        ("components.0.network_allowed", True),
        ("components.0.filesystem_write_allowed", True),
        ("components.1.network_calls", 1),
        ("review_only_effects.grants_runtime_activation", True),
    ]
    count = 0
    with tempfile.TemporaryDirectory(prefix="i0_5_1_component_") as tmp:
        repo = Path(tmp)
        for rel in [
            REGISTRY_REL,
            "runtime/read_only_kernel/kernel.py",
            "runtime/read_only_kernel/constants.py",
            "runtime/read_only_kernel/worker.py",
        ]:
            source = ROOT / rel
            target = repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        original = load_yaml(repo / REGISTRY_REL)
        for path, value in cases:
            mutated = deepcopy(original)
            set_path(mutated, path, value)
            (repo / REGISTRY_REL).write_text(yaml.safe_dump(mutated, sort_keys=False), encoding="utf-8")
            # Fail-closed means: the mutation is detected either by a raised error or by a
            # non-PASS attestation. The AssertionError must be raised OUTSIDE the try, so
            # the broad except cannot swallow the very failure this check exists to catch.
            try:
                record = build_component_attestation(repo, created_at=FIXED_NOW)
            except Exception:
                detected = True
            else:
                detected = record["summary"]["result"] != "PASS"
            if not detected:
                raise AssertionError(f"mutated component registry must fail closed: {path}")
            count += 1
        (repo / REGISTRY_REL).write_text(yaml.safe_dump(original, sort_keys=False), encoding="utf-8")
    return count



def assert_fixture_count(actual: int) -> None:
    fixture = load_yaml(ROOT / "tests/fixtures/runtime_promotion/mutation_cases.yaml")
    expected = fixture.get("expected_fail_closed_cases")
    groups = fixture.get("groups", {})
    listed = sum(len(items) for items in groups.values()) if isinstance(groups, dict) else 0
    if expected != actual or listed != actual:
        raise AssertionError(f"Rev3 fixture count mismatch: expected={expected}, listed={listed}, actual={actual}")

def static_script_review() -> None:
    runtime_paths = [
        ROOT / "scripts/lib/runtime_promotion_readiness.py",
        ROOT / "scripts/build_i0_5_1_runtime_promotion_readiness.py",
    ]
    forbidden_imports = {"requests", "httpx", "socket", "urllib3", "aiohttp", "openai", "anthropic", "subprocess"}
    for path in runtime_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in forbidden_imports:
                        raise AssertionError(f"{path}: forbidden import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in forbidden_imports:
                    raise AssertionError(f"{path}: forbidden import {node.module}")

    collector = (ROOT / "scripts/collect_github_ci_evidence.py").read_text(encoding="utf-8")
    for forbidden in ["os.environ", "GITHUB_TOKEN", "GH_TOKEN", "api_key", "password"]:
        if forbidden in collector:
            raise AssertionError(f"CI collector must not read or name credential values: {forbidden}")
    for required in ["gh", "api", "live GitHub API", "No credential values"]:
        if required not in collector:
            raise AssertionError(f"CI collector missing required evidence behavior: {required}")


def main() -> int:
    assert_gate_integration()
    assert_workflow()
    assert_builder_no_boolean_bypass()
    assert_current_core_uses_existing_verifier()
    assert_no_runtime_enablement()
    static_script_review()
    attestation, readiness = validate_positive()
    component_mutations = validate_component_mutations()
    ci_mutations = validate_ci_evidence()
    gate_cases = validate_gate_required_checks()
    current_core_cases = validate_current_core_fail_closed()
    split_cases = validate_readiness_split(readiness)
    readiness_mutations = validate_readiness_mutations(readiness)
    total_negative = component_mutations + ci_mutations + gate_cases + current_core_cases + split_cases + readiness_mutations
    assert_fixture_count(total_negative)
    print("PASS: I0.5.1 Rev3 readiness-split validation completed")
    print(f"Component attestations: {len(attestation['components'])} PASS")
    print(f"Fail-closed negative cases: {total_negative} PASS")
    print(f"Default Design Readiness: {readiness['summary']['design_readiness']['result']}")
    print(f"Default Activation Readiness: {readiness['summary']['activation_readiness']['result']}")
    print("Manual CI/Core verification booleans are removed; Gate evidence, CI evidence, and Current Core verification fail closed.")
    print("No Runtime activation, external execution, financial execution, Tool/Program enablement, or authority expansion occurred.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
