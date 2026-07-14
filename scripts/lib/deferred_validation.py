from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

import yaml


MANIFEST_REL = "deferred/DEFERRED_ARCHITECTURE.yaml"
FAMILY_ORDER = ("runtime_entry", "executor", "operations", "control_channel", "sandbox")
EXPECTED_FALSE_GLOBALS = (
    "runtime_authoritative_activation_allowed",
    "production_state_mutation_allowed",
    "real_approval_consumption_allowed",
    "runtime_session_start_allowed",
    "kernel_handoff_allowed",
    "executor_activation_allowed",
    "executor_handoff_allowed",
    "tool_execution_allowed",
    "program_execution_allowed",
    "model_invocation_allowed",
    "network_access_allowed",
    "filesystem_write_allowed",
    "scheduler_dispatch_allowed",
    "control_channel_dispatch_allowed",
    "external_action_allowed",
    "financial_action_allowed",
    "permission_expansion_allowed",
    "authority_expansion_allowed",
    "core_activation_allowed",
    "passing_validation_grants_activation",
    "generated_evidence_grants_activation",
    "candidate_status_grants_activation",
    "deferred_artifact_may_block_unrelated_active_work",
)
EXPECTED_TRUE_GLOBALS = (
    "explicit_separate_activation_required",
    "fail_closed_on_missing_or_ambiguous_authority",
)


class DeferredValidationError(RuntimeError):
    pass


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise DeferredValidationError(f"expected YAML mapping: {path}")
    return data


def load_manifest(repo_root: Path) -> dict[str, Any]:
    return load_yaml(repo_root / MANIFEST_REL)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DeferredValidationError(message)


def _all_references(family: dict[str, Any]) -> Iterable[str]:
    yield str(family["boundary_ref"])
    for key in ("component_indexes", "implementation_candidates"):
        for value in family.get(key, []):
            yield str(value)
    for contract in family.get("contracts", []):
        yield str(contract["contract_ref"])
        yield str(contract["schema_ref"])
    for command in family.get("detailed_validators", []):
        yield str(command[0])


def _path_exists(repo_root: Path, rel: str) -> bool:
    path = repo_root / rel
    if rel.endswith("/"):
        return path.is_dir()
    return path.exists()


def validate_manifest_structure(repo_root: Path, manifest: dict[str, Any]) -> None:
    _require(manifest.get("schema_version") == "thomas_deferred_architecture.v0.1", "unexpected deferred schema_version")
    _require(manifest.get("architecture_id") == "thomas.deferred_architecture", "unexpected architecture_id")
    _require(manifest.get("status") == "DEFERRED_REVIEW_ONLY", "deferred status must remain DEFERRED_REVIEW_ONLY")
    _require(manifest.get("owner") == "Thomas", "deferred owner must remain Thomas")
    _require(manifest.get("authoritative_for_deferred_design") is True, "one deferred design authority is required")
    _require(manifest.get("runtime_authoritative") is False, "deferred architecture must not be runtime-authoritative")

    constraints = manifest.get("global_constraints")
    _require(isinstance(constraints, dict), "global_constraints must be a mapping")
    for key in EXPECTED_FALSE_GLOBALS:
        _require(constraints.get(key) is False, f"global_constraints.{key} must remain false")
    for key in EXPECTED_TRUE_GLOBALS:
        _require(constraints.get(key) is True, f"global_constraints.{key} must remain true")

    family_order = manifest.get("family_order")
    _require(family_order == list(FAMILY_ORDER), "family_order must be canonical and complete")
    families = manifest.get("families")
    _require(isinstance(families, dict), "families must be a mapping")
    _require(tuple(families) == FAMILY_ORDER, "families must preserve canonical order")

    seen_contracts: set[str] = set()
    seen_schemas: set[str] = set()
    for family_id in FAMILY_ORDER:
        family = families[family_id]
        _require(family.get("status") == "DEFERRED_DISABLED", f"{family_id} must remain DEFERRED_DISABLED")
        _require(isinstance(family.get("family_constraints"), dict), f"{family_id}.family_constraints missing")
        for key, value in family["family_constraints"].items():
            _require(value is False, f"{family_id}.family_constraints.{key} must remain false")
        for rel in _all_references(family):
            _require(_path_exists(repo_root, rel), f"{family_id}: referenced path missing: {rel}")

        boundary = (repo_root / family["boundary_ref"]).read_text(encoding="utf-8")
        for token in ("Canonical deferred authority", "No activation authority", "Deferred and disabled"):
            _require(token in boundary, f"{family_id} boundary missing token: {token}")

        for item in family.get("contracts", []):
            contract = str(item["contract_ref"])
            schema = str(item["schema_ref"])
            _require(contract not in seen_contracts, f"contract appears in multiple deferred families: {contract}")
            _require(schema not in seen_schemas, f"schema appears in multiple deferred families: {schema}")
            seen_contracts.add(contract)
            seen_schemas.add(schema)
            json.loads((repo_root / schema).read_text(encoding="utf-8"))

    _require(
        families["runtime_entry"].get("phase_aliases") == ["I0.5.1", "I0.5.2", "I0.5.3", "I0.5.4", "I0.5.5"],
        "runtime_entry must consolidate I0.5.1-I0.5.5 in order",
    )

    legacy = manifest.get("legacy_compatibility", {})
    _require(legacy.get("phase_specific_contracts_remain_authoritative") is False, "legacy phase contracts must not remain authoritative")
    _require(legacy.get("phase_specific_validators_are_gate_owners") is False, "phase validators must not remain Gate owners")
    _require(legacy.get("detailed_validators_are_subordinate_checks") is True, "detailed validators must remain subordinate checks")


def _table_fields(text: str, start_heading: str = "## 2. Required Fields") -> set[str]:
    start = text.find(start_heading)
    if start < 0:
        return set()
    next_heading = re.search(r"(?m)^## 3\.", text[start + len(start_heading):])
    end = len(text) if next_heading is None else start + len(start_heading) + next_heading.start()
    return set(re.findall(r"^\|\s*`([^`]+)`\s*\|", text[start:end], re.MULTILINE))


def validate_contract_schema_parity(repo_root: Path, manifest: dict[str, Any]) -> None:
    for family_id, family in manifest["families"].items():
        for item in family.get("contracts", []):
            if item.get("required_field_parity") is not True:
                continue
            contract_rel = str(item["contract_ref"])
            schema_rel = str(item["schema_ref"])
            documented = _table_fields((repo_root / contract_rel).read_text(encoding="utf-8"))
            schema = json.loads((repo_root / schema_rel).read_text(encoding="utf-8"))
            required = set(schema.get("required", []))
            if not documented:
                raise DeferredValidationError(f"{contract_rel}: required field table was not found")
            if documented != required:
                missing_doc = sorted(required - documented)
                missing_schema = sorted(documented - required)
                raise DeferredValidationError(
                    f"{family_id}: contract/schema parity mismatch for {contract_rel}: "
                    f"missing_in_doc={missing_doc} missing_in_schema={missing_schema}"
                )


def validate_gate_ownership(repo_root: Path) -> None:
    sys.path.insert(0, str(repo_root / "scripts"))
    try:
        from gate_matrix import ACTIVE_CHECKS, DEFERRED_CHECKS
    finally:
        sys.path.pop(0)

    expected = [("Deferred Architecture", ["scripts/validate_deferred_architecture.py"])]
    _require(DEFERRED_CHECKS == expected, "Deferred Gate must have one canonical harness entry")

    active = {label: command for label, command in ACTIVE_CHECKS}
    _require(
        active.get("Execution Validation and Audit Foundation")
        == ["scripts/validate_execution_validation_audit_contracts.py", "--scope", "active"],
        "Active Gate must validate only active Validation/Audit scope",
    )

    manifest = load_manifest(repo_root)
    detail_commands = {
        tuple(str(part) for part in command)
        for family in manifest["families"].values()
        for command in family.get("detailed_validators", [])
    }
    active_commands = {tuple(str(part) for part in command) for _, command in ACTIVE_CHECKS}
    _require(
        not detail_commands.intersection(active_commands),
        "an identical deferred detail command leaked into Active Gate",
    )


def validate_precedence_boundary(repo_root: Path) -> None:
    text = (repo_root / "docs/runtime-contracts/RUNTIME_CONTRACT_PRECEDENCE_ADDENDUM_v0.4.md").read_text(encoding="utf-8")
    _require("../../deferred/DEFERRED_ARCHITECTURE.yaml" in text, "Runtime precedence must reference canonical Deferred Architecture")
    _require("Deferred artifacts are not current Runtime authority" in text, "Runtime precedence must state Deferred non-authority")


def run_detailed_validators(repo_root: Path, manifest: dict[str, Any], families: list[str]) -> int:
    commands: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for family_id in families:
        for command in manifest["families"][family_id].get("detailed_validators", []):
            key = tuple(str(part) for part in command)
            if key not in seen:
                seen.add(key)
                commands.append(list(key))

    for command in commands:
        rendered = " ".join(command)
        print(f"\n=== Deferred detail: {rendered} ===")
        proc = subprocess.run([sys.executable, *command], cwd=repo_root, text=True, encoding="utf-8", timeout=600)
        if proc.returncode != 0:
            raise DeferredValidationError(f"detailed validator failed ({proc.returncode}): {rendered}")
    return len(commands)
