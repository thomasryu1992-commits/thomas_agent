#!/usr/bin/env python3
from __future__ import annotations

import tempfile
from pathlib import Path

import yaml

from lib.safe_io import SafeIOError, safe_child_path, safe_repo_path

ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []


def error(message: str) -> None:
    ERRORS.append(message)


def require_rejected(function, *args, label: str) -> None:
    try:
        function(*args)
    except (SafeIOError, FileNotFoundError, ValueError):
        return
    error(f"Unsafe path unexpectedly accepted: {label}")


def main() -> int:
    require_rejected(safe_repo_path, ROOT, "../outside.yaml", label="parent traversal")
    require_rejected(safe_repo_path, ROOT, "/absolute/outside.yaml", label="absolute path")
    with tempfile.TemporaryDirectory() as temp:
        require_rejected(
            safe_child_path,
            Path(temp),
            "../outside.yaml",
            label="child parent traversal",
        )

    template = yaml.safe_load(
        (ROOT / "THOMAS_CORE/CORE_RELEASE_MANIFEST_TEMPLATE.yaml").read_text(encoding="utf-8")
    )
    if "required_validation_commands" in template:
        error("Release Template contains executable command strings")
    if template.get("release_gate_evidence") != "generated/release_gate/RELEASE_GATE_EVIDENCE.yaml":
        error("Release Template must reference canonical Release Gate evidence")
    if "required_validators" in template:
        error("Release Template must not duplicate the Release Gate validator list")

    for rel in ["THOMAS_CORE/MVP_ACTIVE_CORE.yaml", "THOMAS_CORE/CORE_METADATA.yaml"]:
        data = yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))
        for key in ["status", "package_status", "validation_status", "release_status", "runtime_activation_status"]:
            if key in data:
                error(f"{rel}: dynamic lifecycle key remains in semantic Core: {key}")

    script_requirements = {
        "scripts/build_core_release_manifest.py": [
            "exclusive_lock", "os.replace", "artifacts", "validation_toolchain"
        ],
        "scripts/approve_core_release.py": [
            "require_clean_worktree", "require_tree_tracked_at_head",
            "verified_by_protected_review", "immutable_write_text"
        ],
        "scripts/activate_core_release.py": [
            "require_file_tracked_at_head", "immutable_write_text", "atomic_write_text",
            "activation_type"
        ],
        "scripts/deactivate_core_release.py": [
            "require_file_tracked_at_head", "immutable_write_text", "atomic_write_text"
        ],
        "scripts/create_core_context_binding.py": [
            "--task-file", "safe_repo_path", "immutable_write_text", "atomic_write_text",
            "active_core_rule_ids"
        ],
    }
    for rel, tokens in script_requirements.items():
        path = ROOT / rel
        if not path.exists():
            error(f"Missing security-critical script: {rel}")
            continue
        text = path.read_text(encoding="utf-8")
        for token in tokens:
            if token not in text:
                error(f"{rel}: missing security marker: {token}")

    for rel in [
        "scripts/prepare_core_activation.py",
        "scripts/prepare_core_deactivation.py",
        "scripts/set_current_core_release.py",
        "THOMAS_CORE/CORE_RELEASE_MANIFEST.yaml",
        "THOMAS_CORE/CORE_RELEASE_APPROVAL.yaml",
    ]:
        if (ROOT / rel).exists():
            error(f"Deprecated duplicate path exists: {rel}")

    if ERRORS:
        print("FAIL: I0.4.1 security hardening validation found errors")
        for item in ERRORS:
            print(f" - {item}")
        return 1

    print("PASS: I0.4.1 security hardening validation completed")
    print(
        "Checked path traversal rejection, single-source Release Gate ownership, semantic/dynamic status separation, "
        "committed Approval provenance, atomic Activation/Deactivation pointers, Task-file Binding, and duplicate path removal"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
