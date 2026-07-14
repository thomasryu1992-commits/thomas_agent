#!/usr/bin/env python3
from __future__ import annotations

import compileall
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.registry_resolution import (
    RegistryResolutionError,
    load_resource_definitions,
    resolve_resource_registry,
    resolve_role_registry,
)
from scripts.lib.artifact_boundaries import validate_artifact_boundaries

ERRORS: list[str] = []


def fail(message: str) -> None:
    ERRORS.append(message)


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected YAML mapping: {path}")
    return data


def canonical_hash(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def validate_governance() -> dict[str, Any]:
    policy = load_yaml(ROOT / "governance/GOVERNANCE_POLICY.yaml")
    if policy.get("authoritative") is not True:
        fail("canonical Governance Policy must be authoritative")
    if policy.get("status") != "ACTIVE_POLICY_SOURCE":
        fail("canonical Governance Policy must be ACTIVE_POLICY_SOURCE")
    if policy.get("policy_id") != "thomas.governance.policy":
        fail("canonical Governance Policy ID mismatch")
    effect = policy.get("runtime_effect", {})
    for key in (
        "grants_runtime_execution",
        "grants_tool_or_program_enablement",
        "grants_external_execution",
        "grants_financial_execution",
        "grants_permission_expansion",
        "executor_handoff_allowed",
        "approval_consumption_allowed",
        "core_activation_allowed",
    ):
        if effect.get(key) is not False:
            fail(f"governance runtime_effect.{key} must remain false")
    return policy


def validate_registries(policy: dict[str, Any]) -> None:
    role_registry = load_yaml(ROOT / "03_ROLE_CONTRACTS/ROLE_REGISTRY.yaml")
    if role_registry.get("schema_version") != "role_registry.v0.3":
        fail("Role Registry schema mismatch")
    if role_registry.get("authoritative") is not True:
        fail("Role Registry must be authoritative for status/index fields")
    if "compatibility" in role_registry:
        fail("Role Registry compatibility block must be retired")
    try:
        resolved_roles = resolve_role_registry(
            repo_root=ROOT,
            registry=role_registry,
            governance_policy=policy,
        )
    except RegistryResolutionError as exc:
        fail(f"Role Registry resolution failed closed: {exc}")
    else:
        if resolved_roles.get("_resolution", {}).get("authoritative") is not False:
            fail("resolved Role view must remain non-authoritative")

    for rel, collection, id_key, schema_version in (
        ("05_REGISTRIES/PROGRAM_REGISTRY.yaml", "programs", "program_id", "program_registry.v0.2"),
        ("05_REGISTRIES/TOOL_REGISTRY.yaml", "tools", "tool_id", "tool_registry.v0.2"),
    ):
        registry = load_yaml(ROOT / rel)
        if registry.get("schema_version") != schema_version:
            fail(f"{rel}: schema mismatch")
        if registry.get("authoritative") is not True:
            fail(f"{rel}: must be authoritative for index fields")
        if "compatibility" in registry:
            fail(f"{rel}: compatibility block must be retired")
        for entry in registry.get(collection, []):
            for field in (
                "purpose",
                "required_permission_level",
                "governance",
                "external_action",
                "deterministic",
            ):
                if field in entry:
                    fail(f"{rel}: {entry.get(id_key)} duplicates Definition-owned field {field}")
            if entry.get("enabled") is not False:
                fail(f"{rel}: {entry.get(id_key)} must remain disabled")
            if entry.get("runtime_implementation_available") is not False:
                fail(f"{rel}: {entry.get(id_key)} implementation must remain unavailable")
        try:
            definitions = load_resource_definitions(
                repo_root=ROOT,
                registry=registry,
                collection_key=collection,
            )
            resolved = resolve_resource_registry(
                repo_root=ROOT,
                registry=registry,
                definitions=definitions,
                governance_policy=policy,
                collection_key=collection,
                id_key=id_key,
            )
        except RegistryResolutionError as exc:
            fail(f"{rel}: Registry resolution failed closed: {exc}")
        else:
            if resolved.get("_resolution", {}).get("authoritative") is not False:
                fail(f"{rel}: resolved view must remain non-authoritative")


def validate_active_kernel() -> None:
    required = (
        "kernel.py", "loader.py", "preflight.py", "policy.py", "router.py",
        "worker_port.py", "validation.py", "audit.py", "assembler.py", "orchestrator.py",
    )
    root = ROOT / "runtime/read_only_kernel"
    for name in required:
        if not (root / name).is_file():
            fail(f"active Kernel module missing: runtime/read_only_kernel/{name}")
    if (ROOT / "runtime/kernel_slim").exists():
        fail("parallel kernel_slim candidate must be retired from active Runtime")
    if (root / "slim_candidate.py").exists():
        fail("read_only_kernel slim_candidate.py must be retired from active Runtime")


def main() -> int:
    policy = validate_governance()
    validate_registries(policy)
    validate_active_kernel()
    ERRORS.extend(validate_artifact_boundaries(ROOT))

    for target in (
        ROOT / "runtime/read_only_kernel",
        ROOT / "runtime/registry_resolution.py",
    ):
        if target.is_dir():
            if not compileall.compile_dir(str(target), quiet=1, force=True):
                fail(f"compile failed: {target.relative_to(ROOT)}")
        elif not compileall.compile_file(str(target), quiet=1, force=True):
            fail(f"compile failed: {target.relative_to(ROOT)}")

    if ERRORS:
        print("FAIL: Architecture Slimming validation found errors")
        for item in ERRORS:
            print(f" - {item}")
        return 1
    print("PASS: active Architecture Slimming invariants validated")
    print("One Governance authority, index-only Registries, one active Kernel, and explicit Generated/Historical boundaries are in place.")
    print("Deferred design semantics are owned by the canonical Deferred Gate, not duplicated in the Active Gate.")
    print("No Runtime, Tool, Program, Executor, external, financial, Permission-expansion, Authority-expansion, or Core-activation capability was enabled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
