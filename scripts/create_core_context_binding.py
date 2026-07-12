#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import yaml

from lib.core_release_verifier import sha256_file, verify_activation_record, verify_current_pointer
from lib.safe_io import atomic_write_text, exclusive_lock, immutable_write_text, safe_repo_path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POINTER = "THOMAS_CORE/CURRENT_CORE_RELEASE.yaml"
LOCK_PATH = ROOT / ".runtime_locks/core_context_binding.lock"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_task(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Task file must contain a YAML mapping")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create minimal Core Context Binding v0.3 from an actual Task record."
    )
    parser.add_argument("--task-file", required=True)
    parser.add_argument("--binding-output", required=True)
    parser.add_argument("--updated-task-output", required=True)
    parser.add_argument("--current-pointer", default=DEFAULT_POINTER)
    parser.add_argument("--bound-by", default="Thomas Prime Runtime")
    parser.add_argument("--previous-binding-id")
    parser.add_argument(
        "--change-type",
        choices=["root_binding", "task_revision_same_core", "core_rebind"],
    )
    parser.add_argument("--change-reason")
    parser.add_argument("--material-change-ref")
    args = parser.parse_args()

    task_path = safe_repo_path(ROOT, args.task_file, must_exist=True)
    binding_path = safe_repo_path(ROOT, args.binding_output)
    updated_task_path = safe_repo_path(ROOT, args.updated_task_output)
    pointer_path = safe_repo_path(ROOT, args.current_pointer, must_exist=True)

    task = load_task(task_path)
    identity = task.get("identity")
    context = task.get("context")
    lifecycle = task.get("lifecycle")

    if not isinstance(identity, dict) or not isinstance(context, dict) or not isinstance(lifecycle, dict):
        raise ValueError("Task identity, context, and lifecycle mappings are required")

    task_id = identity.get("task_id")
    task_revision = identity.get("task_revision")
    trace_id = identity.get("trace_id")
    existing_binding_id = context.get("core_context_binding_id")
    loaded_rule_ids = context.get("active_core_rule_ids")
    task_status = lifecycle.get("status")

    if not isinstance(task_id, str) or not task_id:
        raise ValueError("Task identity.task_id is invalid")
    if not isinstance(task_revision, int) or task_revision < 1:
        raise ValueError("Task identity.task_revision is invalid")
    if not isinstance(trace_id, str) or not trace_id:
        raise ValueError("Task identity.trace_id is invalid")
    if not isinstance(loaded_rule_ids, list) or not loaded_rule_ids or any(not isinstance(x, str) for x in loaded_rule_ids):
        raise ValueError("Task context.active_core_rule_ids must be an explicit non-empty list")

    loaded_rule_ids = list(dict.fromkeys(loaded_rule_ids))

    if existing_binding_id is None:
        if task_status != "RECEIVED":
            raise ValueError("A null Core Binding is allowed only while Task lifecycle.status is RECEIVED")
    elif not (isinstance(existing_binding_id, str) and existing_binding_id.startswith("ccb-")):
        raise ValueError("Task Core Binding ID is invalid")

    current = verify_current_pointer(ROOT, pointer_path)
    if current.get("runtime_activation_status") != "approved_via_activation_registry":
        raise ValueError("Current Core is deactivated; new Task Binding is prohibited")

    activation_path = safe_repo_path(ROOT, current["activation_path"], must_exist=True)
    activation, manifest, approval, manifest_path, _ = verify_activation_record(ROOT, activation_path)

    active_rule_ids = manifest.get("active_runtime", {}).get("active_rule_ids")
    if not isinstance(active_rule_ids, list) or not active_rule_ids:
        raise ValueError("Bound Release Active Rule set is invalid")

    unknown = sorted(set(loaded_rule_ids) - set(active_rule_ids))
    if unknown:
        raise ValueError(f"Task requests Rules that are not active in the bound Release snapshot: {unknown}")

    if task_revision == 1:
        if args.change_type not in {None, "root_binding"}:
            raise ValueError("Task revision 1 must use root_binding")
        if any([args.previous_binding_id, args.change_reason, args.material_change_ref]):
            raise ValueError("Root Binding must not include previous Binding lineage")
        lineage = {
            "previous_binding_id": None,
            "change_type": "root_binding",
            "change_reason": None,
            "material_change_ref": None,
        }
    else:
        if args.change_type not in {"task_revision_same_core", "core_rebind"}:
            raise ValueError("Task revision > 1 requires --change-type")
        for name, value in [
            ("--previous-binding-id", args.previous_binding_id),
            ("--change-reason", args.change_reason),
            ("--material-change-ref", args.material_change_ref),
        ]:
            if not value:
                raise ValueError(f"{name} is required for Task revision lineage")
        lineage = {
            "previous_binding_id": args.previous_binding_id,
            "change_type": args.change_type,
            "change_reason": args.change_reason,
            "material_change_ref": args.material_change_ref,
        }

    seed = (
        task_id + "\0" + str(task_revision) + "\0" + trace_id + "\0"
        + manifest["release_id"] + "\0" + approval["approval_id"] + "\0"
        + activation["activation_id"] + "\0" + ",".join(loaded_rule_ids) + "\0"
        + str(lineage["previous_binding_id"]) + "\0" + lineage["change_type"]
    ).encode("utf-8")
    binding_id = "ccb-" + hashlib.sha256(seed).hexdigest()[:24]

    if existing_binding_id is not None and existing_binding_id != binding_id:
        raise ValueError("Task already references a different Core Context Binding")

    binding = {
        "schema_version": "core_context_binding.v0.3",
        "identity": {
            "core_context_binding_id": binding_id,
            "task_id": task_id,
            "task_revision": task_revision,
            "trace_id": trace_id,
        },
        "release": {
            "release_id": manifest["release_id"],
            "core_version": manifest["core_version"],
            "manifest_path": manifest_path.relative_to(ROOT).as_posix(),
            "manifest_sha256": sha256_file(manifest_path),
            "approval_id": approval["approval_id"],
            "activation_id": activation["activation_id"],
        },
        "rules": {
            "loaded_rule_ids": loaded_rule_ids,
        },
        "binding": {
            "bound_at_utc": utc_now(),
            "bound_by": args.bound_by,
            "binding_reason": "Bind Task record to one exact approved and active Core Release.",
        },
        "inheritance": {
            "child_tasks_inherit_binding": True,
            "assignments_reference_binding": True,
            "outputs_reference_binding": True,
        },
        "lineage": lineage,
        "rebind_policy": {
            "silent_mid_task_rebind_allowed": False,
            "explicit_task_revision_required": True,
            "replan_required": True,
            "reauthorization_required": True,
        },
    }

    updated_task = dict(task)
    updated_context = dict(context)
    updated_context["core_context_binding_id"] = binding_id
    updated_task["context"] = updated_context

    with exclusive_lock(LOCK_PATH):
        immutable_write_text(
            binding_path,
            yaml.safe_dump(binding, sort_keys=False, allow_unicode=True, width=120),
        )
        atomic_write_text(
            updated_task_path,
            yaml.safe_dump(updated_task, sort_keys=False, allow_unicode=True, width=120),
        )

    print("PASS: created minimal Core Context Binding v0.3 from the Task record")
    print(f"Binding ID: {binding_id}")
    print(f"Task output: {updated_task_path}")
    print(f"Binding output: {binding_path}")
    print("The Binding stores lineage references, not a duplicate Release Manifest, and grants no execution Permission.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
