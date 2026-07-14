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


def validate_governance() -> None:
    policy = load_yaml(GOVERNANCE)
    if policy.get("source_of_truth") is not True:
        fail("governance source_of_truth must be true")

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


def validate_role_registry() -> None:
    registry = load_yaml(ROLE_REGISTRY)
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


def validate_resource_registry(
    path: Path,
    collection: str,
    id_key: str,
) -> None:
    registry = load_yaml(path)
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
