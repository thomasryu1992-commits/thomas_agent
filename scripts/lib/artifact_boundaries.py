from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Iterable

import yaml


GENERATED_INDEX_REL = "generated/GENERATED_ARTIFACT_INDEX.yaml"
HISTORICAL_INDEX_REL = "historical/HISTORICAL_ARTIFACT_INDEX.yaml"

ACTIVE_PYTHON_ROOTS = (
    "runtime",
    "scripts",
    "tests",
)

RETIRED_IMPORT_PREFIXES = (
    "runtime.compat",
    "runtime.kernel_slim",
)


def load_yaml_mapping(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected YAML mapping: {path}")
    return data


def _iter_active_python(repo_root: Path) -> Iterable[Path]:
    for root_rel in ACTIVE_PYTHON_ROOTS:
        root = repo_root / root_rel
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            yield path


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def scan_retired_import_consumers(repo_root: Path) -> list[str]:
    consumers: list[str] = []
    for path in _iter_active_python(repo_root):
        for module in imported_modules(path):
            if module.startswith(RETIRED_IMPORT_PREFIXES):
                consumers.append(
                    f"{path.relative_to(repo_root).as_posix()}: {module}"
                )
    return consumers


def validate_artifact_boundaries(repo_root: Path) -> list[str]:
    errors: list[str] = []
    generated_path = repo_root / GENERATED_INDEX_REL
    historical_path = repo_root / HISTORICAL_INDEX_REL
    if not generated_path.is_file():
        errors.append(f"missing generated index: {GENERATED_INDEX_REL}")
        return errors
    if not historical_path.is_file():
        errors.append(f"missing historical index: {HISTORICAL_INDEX_REL}")
        return errors

    generated = load_yaml_mapping(generated_path)
    historical = load_yaml_mapping(historical_path)
    for label, record, expected_schema in (
        ("generated", generated, "thomas_generated_artifact_index.v0.1"),
        ("historical", historical, "thomas_historical_artifact_index.v0.1"),
    ):
        if record.get("schema_version") != expected_schema:
            errors.append(f"{label} index schema_version mismatch")
        if record.get("authoritative") is not False:
            errors.append(f"{label} index must remain non-authoritative")
        if record.get("runtime_use_allowed") is not False:
            errors.append(f"{label} index must prohibit Runtime use")
        for family in record.get("families", []):
            if family.get("active_authority") is not False:
                errors.append(
                    f"{label} family {family.get('family_id')} must not be active authority"
                )

    for rel in generated.get("retired_active_locations", []):
        if (repo_root / rel).exists():
            errors.append(f"retired generated location still exists: {rel}")
    for rel in historical.get("retired_active_locations", []):
        if (repo_root / rel).exists():
            errors.append(f"retired historical/compatibility location still exists: {rel}")

    required_paths = (
        "runtime/registry_resolution.py",
        "runtime/read_only_kernel/orchestrator.py",
        "deferred/DEFERRED_ARCHITECTURE.yaml",
        "docs/ACTIVE_ARCHITECTURE.md",
        "generated/README.md",
        "historical/README.md",
    )
    for rel in required_paths:
        if not (repo_root / rel).exists():
            errors.append(f"required final architecture path missing: {rel}")

    archived_paths = (
        "historical/compatibility/runtime_compat/legacy_registry_projection.py",
        "historical/compatibility/kernel_slim_candidate",
        "historical/compatibility/read_only_kernel_slim_candidate.py",
        "historical/compatibility/registry_candidates/ROLE_REGISTRY_SLIM_CANDIDATE.yaml",
        "historical/compatibility/registry_candidates/PROGRAM_REGISTRY_SLIM_CANDIDATE.yaml",
        "historical/compatibility/registry_candidates/TOOL_REGISTRY_SLIM_CANDIDATE.yaml",
    )
    for rel in archived_paths:
        if not (repo_root / rel).exists():
            errors.append(f"retired compatibility evidence missing: {rel}")

    consumers = scan_retired_import_consumers(repo_root)
    if consumers:
        errors.extend(f"retired compatibility import remains active: {item}" for item in consumers)

    for rel in (
        "03_ROLE_CONTRACTS/ROLE_REGISTRY.yaml",
        "05_REGISTRIES/PROGRAM_REGISTRY.yaml",
        "05_REGISTRIES/TOOL_REGISTRY.yaml",
    ):
        registry = load_yaml_mapping(repo_root / rel)
        if "compatibility" in registry:
            errors.append(f"{rel}: compatibility block must be retired")
        resolution = registry.get("resolution", {})
        if resolution.get("resolver_module") != "runtime/registry_resolution.py":
            errors.append(f"{rel}: canonical resolver binding missing")
        if resolution.get("resolved_view_authoritative") is not False:
            errors.append(f"{rel}: resolved view must remain non-authoritative")

    active_text = (repo_root / "docs/ACTIVE_ARCHITECTURE.md").read_text(encoding="utf-8")
    for token in (
        "## Architecture on One Screen",
        "## Repository Boundaries",
        "Runtime-authoritative execution: Disabled",
        "Generated evidence grants no authority",
        "Historical evidence grants no authority",
    ):
        if token not in active_text:
            errors.append(f"docs/ACTIVE_ARCHITECTURE.md missing final reference token: {token}")

    release_policy = historical.get("core_release_integrity", {})
    manifests = release_policy.get("immutable_manifests", {})
    copied = release_policy.get("copied_source_snapshots", {})
    if manifests.get("move_or_rewrite_allowed") is not False:
        errors.append("immutable release manifests must not be moved or rewritten")
    if copied.get("must_not_be_interpreted_as_current_source") is not True:
        errors.append("copied release snapshots must be classified as non-current source")

    return errors
