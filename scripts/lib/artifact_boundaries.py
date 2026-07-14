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


def _markdown_section(text: str, heading: str) -> str | None:
    marker = f"## {heading}"
    start = text.find(marker)
    if start < 0:
        return None
    body_start = start + len(marker)
    next_heading = text.find("\n## ", body_start)
    return text[body_start:] if next_heading < 0 else text[body_start:next_heading]


def _fenced_text_after(section: str, label: str) -> str | None:
    label_index = section.find(label)
    if label_index < 0:
        return None
    fence_start = section.find("```text", label_index)
    if fence_start < 0:
        return None
    body_start = section.find("\n", fence_start)
    if body_start < 0:
        return None
    fence_end = section.find("\n```", body_start + 1)
    if fence_end < 0:
        return None
    return section[body_start + 1:fence_end]


def _tokens_in_order(text: str, tokens: tuple[str, ...]) -> bool:
    position = -1
    for token in tokens:
        next_position = text.find(token, position + 1)
        if next_position < 0:
            return False
        position = next_position
    return True


def validate_architecture_reference_boundaries(repo_root: Path) -> list[str]:
    errors: list[str] = []
    active_path = repo_root / "docs/ACTIVE_ARCHITECTURE.md"
    constitution_path = repo_root / "governance/SYSTEM_CONSTITUTION.md"
    docs_index_path = repo_root / "docs/README.md"

    for path in (active_path, constitution_path, docs_index_path):
        if not path.is_file():
            errors.append(
                f"architecture reference boundary file missing: {path.relative_to(repo_root).as_posix()}"
            )
    if errors:
        return errors

    active_text = active_path.read_text(encoding="utf-8")
    one_screen = _markdown_section(active_text, "Architecture on One Screen")
    if one_screen is None:
        errors.append("docs/ACTIVE_ARCHITECTURE.md missing Architecture on One Screen section")
    else:
        active_label = "Active authority and execution lane:"
        candidate_label = "Inactive candidate lane — not part of the active dependency chain:"
        active_lane = _fenced_text_after(one_screen, active_label)
        candidate_lane = _fenced_text_after(one_screen, candidate_label)

        if active_lane is None:
            errors.append("Active Architecture missing explicit active authority and execution lane")
        else:
            if "System Constitution" in active_lane:
                errors.append("inactive System Constitution must not appear in the active authority lane")
            active_order = (
                "Thomas",
                "Thomas Core",
                "Governance Policy",
                "Thomas Prime",
                "Thin Read-only Runtime Kernel",
                "Router",
                "Role / Program / Tool Definitions",
                "Validation",
                "Memory Candidate / Append-only Audit",
            )
            if not _tokens_in_order(active_lane, active_order):
                errors.append("Active Architecture lane order is incomplete or inconsistent")

        if candidate_lane is None:
            errors.append("Active Architecture missing separate inactive Constitution candidate lane")
        else:
            for token in (
                "System Constitution",
                "status: Migration Candidate",
                "authoritative: No",
                "active dependency: none",
                "proposed future position: between Thomas Core and Governance Policy",
                "cutover: separate review and explicit Thomas approval required",
            ):
                if token not in candidate_lane:
                    errors.append(f"inactive Constitution candidate lane missing token: {token}")

    for token in (
        "Runtime-authoritative execution: Disabled",
        "## Repository Boundaries",
        "Generated evidence grants no authority",
        "Historical evidence grants no authority",
        "### Non-authoritative Candidate Reference",
        "The active lane above is the only current authority and dependency chain.",
    ):
        if token not in active_text:
            errors.append(f"docs/ACTIVE_ARCHITECTURE.md missing final reference token: {token}")

    constitution_text = constitution_path.read_text(encoding="utf-8")
    for token in (
        "**Status:** Migration Candidate",
        "**Authoritative:** No — explicit cutover required",
        "**Active dependency:** None",
        "## Current Active Authority Boundary",
        "## Proposed Future Authority Order After Explicit Cutover",
        "## No Active Dependency Rule",
        "## Cutover Preconditions",
    ):
        if token not in constitution_text:
            errors.append(f"governance/SYSTEM_CONSTITUTION.md missing candidate boundary token: {token}")

    current_boundary = _markdown_section(constitution_text, "Current Active Authority Boundary")
    proposed_boundary = _markdown_section(
        constitution_text,
        "Proposed Future Authority Order After Explicit Cutover",
    )
    current_lane = (
        _fenced_text_after(current_boundary, "active authority and execution lane remains:")
        if current_boundary is not None
        else None
    )
    proposed_lane = (
        _fenced_text_after(proposed_boundary, "following order is a proposal only")
        if proposed_boundary is not None
        else None
    )

    if current_lane is None:
        errors.append("System Constitution missing current active authority lane")
    else:
        if "System Constitution" in current_lane:
            errors.append("System Constitution must not insert itself into the current active authority lane")
        if not _tokens_in_order(
            current_lane,
            ("Thomas", "Thomas Core", "Governance Policy", "Thomas Prime", "Runtime Kernel"),
        ):
            errors.append("System Constitution current active boundary order is incomplete")

    if proposed_lane is None:
        errors.append("System Constitution missing proposed future authority lane")
    elif not _tokens_in_order(
        proposed_lane,
        ("Thomas", "Thomas Core", "System Constitution", "Governance Policy", "Thomas Prime"),
    ):
        errors.append("System Constitution proposed future authority order is incomplete")

    docs_index_text = docs_index_path.read_text(encoding="utf-8")
    candidate_section = _markdown_section(docs_index_text, "Non-active Candidate Reference")
    active_sources_section = _markdown_section(docs_index_text, "Active Source Families")
    if candidate_section is None or "governance/SYSTEM_CONSTITUTION.md" not in candidate_section:
        errors.append("docs/README.md must list System Constitution under Non-active Candidate Reference")
    if active_sources_section is None:
        errors.append("docs/README.md missing Active Source Families section")
    elif "SYSTEM_CONSTITUTION.md" in active_sources_section:
        errors.append("docs/README.md must not list System Constitution as an Active Source Family")

    return errors


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
        "governance/SYSTEM_CONSTITUTION.md",
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

    errors.extend(validate_architecture_reference_boundaries(repo_root))

    release_policy = historical.get("core_release_integrity", {})
    manifests = release_policy.get("immutable_manifests", {})
    copied = release_policy.get("copied_source_snapshots", {})
    if manifests.get("move_or_rewrite_allowed") is not False:
        errors.append("immutable release manifests must not be moved or rewritten")
    if copied.get("must_not_be_interpreted_as_current_source") is not True:
        errors.append("copied release snapshots must be classified as non-current source")

    return errors
