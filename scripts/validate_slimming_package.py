from __future__ import annotations

import compileall
import json
from hashlib import sha256
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]

ROLE_REGISTRY = ROOT / "03_ROLE_CONTRACTS/ROLE_REGISTRY_SLIM_CANDIDATE.yaml"
PROGRAM_REGISTRY = ROOT / "05_REGISTRIES/PROGRAM_REGISTRY_SLIM_CANDIDATE.yaml"
TOOL_REGISTRY = ROOT / "05_REGISTRIES/TOOL_REGISTRY_SLIM_CANDIDATE.yaml"
GOVERNANCE = ROOT / "governance/GOVERNANCE_POLICY.yaml"
MEMORY_POLICY = ROOT / "governance/MEMORY_POLICY.yaml"

ROLE_PROHIBITED = {
    "capabilities",
    "capability_set_sha256",
    "permission_ceiling",
    "restrictions",
    "validation_default",
    "promotion_requirements",
    "selection_policy",
}
RESOURCE_PROHIBITED = {
    "required_permission_level",
    "purpose",
    "governance",
    "external_action",
}


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def load_yaml(path: Path) -> Any:
    if not path.exists():
        fail(f"missing file: {path.relative_to(ROOT)}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def canonical_hash(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(raw).hexdigest()


def raw_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def markdown_front_matter(path: Path) -> dict[str, Any]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        fail(f"missing YAML front matter: {path.relative_to(ROOT)}")
    try:
        closing = next(
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        )
    except StopIteration:
        fail(f"unterminated YAML front matter: {path.relative_to(ROOT)}")
    data = yaml.safe_load("\n".join(lines[1:closing]))
    if not isinstance(data, dict):
        fail(f"front matter must be a mapping: {path.relative_to(ROOT)}")
    return data


def validate_candidate_authority(record: dict[str, Any], label: str) -> None:
    if record.get("authoritative") is not False:
        fail(f"{label} must remain non-authoritative during migration")
    target = record.get("target_authority") or {}
    if target and target.get("active_source_replaced") is not False:
        fail(f"{label} must not claim active source replacement")


def validate_governance() -> None:
    policy = load_yaml(GOVERNANCE)
    validate_candidate_authority(policy, "Governance Policy")

    memory = load_yaml(MEMORY_POLICY)
    validate_candidate_authority(memory, "Memory Policy")

    effect = policy.get("runtime_effect", {})
    required_false = (
        "grants_runtime_execution",
        "grants_tool_or_program_enablement",
        "grants_external_execution",
        "grants_financial_execution",
        "grants_permission_expansion",
    )
    for field in required_false:
        if effect.get(field) is not False:
            fail(f"governance runtime boundary must keep {field}=false")


def validate_registry_candidate(
    registry: dict[str, Any],
    label: str,
) -> None:
    if registry.get("authoritative") is not False:
        fail(f"{label} must remain non-authoritative")
    activation = registry.get("activation", {})
    if activation.get("active_registry_replaced") is not False:
        fail(f"{label} must not replace the active Registry")


def validate_role_registry() -> None:
    registry = load_yaml(ROLE_REGISTRY)
    validate_registry_candidate(registry, "Role Registry candidate")

    for role in registry.get("roles", []):
        duplicated = ROLE_PROHIBITED.intersection(role)
        if duplicated:
            fail(
                f"role {role.get('role_id')} duplicates authoritative fields: "
                f"{sorted(duplicated)}"
            )
        if role.get("status") == "active" and role.get("routable") is not True:
            fail(f"active role must be routable: {role.get('role_id')}")
        if role.get("status") == "candidate" and role.get("routable") is not False:
            fail(f"candidate role must remain non-routable: {role.get('role_id')}")

        definition_path = ROOT / role["definition_path"]
        if not definition_path.exists():
            fail(f"missing role definition: {role['definition_path']}")

        actual_hash = raw_hash(definition_path)
        if actual_hash != role.get("definition_sha256"):
            fail(
                f"role definition hash mismatch for {role.get('role_id')}: "
                f"expected={role.get('definition_sha256')} actual={actual_hash}"
            )

        definition = markdown_front_matter(definition_path)
        if definition.get("role_id") != role.get("role_id"):
            fail(f"role_id mismatch: {role.get('role_id')}")
        if definition.get("role_version") != role.get("version"):
            fail(f"role_version mismatch: {role.get('role_id')}")


def validate_resource_registry(
    path: Path,
    collection: str,
    id_key: str,
) -> None:
    registry = load_yaml(path)
    validate_registry_candidate(registry, f"{collection} Registry candidate")

    for item in registry.get(collection, []):
        duplicated = RESOURCE_PROHIBITED.intersection(item)
        if duplicated:
            fail(
                f"{item.get(id_key)} duplicates authoritative fields: "
                f"{sorted(duplicated)}"
            )
        if item.get("enabled") is not False:
            fail(f"{item.get(id_key)} must remain disabled")
        if item.get("runtime_implementation_available") is not False:
            fail(f"{item.get(id_key)} implementation must remain unavailable")

        definition_path = ROOT / item["definition_path"]
        definition = load_yaml(definition_path)
        actual = canonical_hash(definition)
        if actual != item.get("definition_sha256"):
            fail(
                f"definition hash mismatch for {item.get(id_key)}: "
                f"expected={item.get('definition_sha256')} actual={actual}"
            )


def validate_imports() -> None:
    targets = [
        ROOT / "runtime" / "compat",
        ROOT / "runtime" / "kernel_slim",
    ]
    for target in targets:
        if not compileall.compile_dir(str(target), quiet=1, force=True):
            fail(f"compile failed: {target.relative_to(ROOT)}")

    candidate = ROOT / "runtime" / "read_only_kernel" / "slim_candidate.py"
    if not compileall.compile_file(str(candidate), quiet=1, force=True):
        fail("compile failed: runtime/read_only_kernel/slim_candidate.py")


def main() -> int:
    validate_governance()
    validate_role_registry()
    validate_resource_registry(PROGRAM_REGISTRY, "programs", "program_id")
    validate_resource_registry(TOOL_REGISTRY, "tools", "tool_id")
    validate_imports()
    print("THOMAS_AGENT_SLIMMING_VALIDATION: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
