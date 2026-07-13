#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.read_only_entry.authorization import EXPECTED_OUTPUT_SCHEMAS, build_entry_authorization
from runtime.read_only_entry.atomic_transition import build_atomic_transition_preview


def load_yaml(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected YAML object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Build I0.5.3 exact-entry Authorization and atomic-transition previews.")
    parser.add_argument("--entry-plan", default="build/runtime_authoritative_read_only_entry/RUNTIME_AUTHORITATIVE_READ_ONLY_ENTRY_PLAN.yaml")
    parser.add_argument("--input", required=True, help="Review-only YAML containing design_decision, exact_bindings, component_bindings, nonce_sha256, resource_limits, and allowed_read_paths.")
    parser.add_argument("--created-at", required=True)
    parser.add_argument("--output-dir", default="build/i0_5_3_runtime_entry_authorization")
    args = parser.parse_args()
    plan = load_yaml(ROOT / args.entry_plan)
    values = load_yaml(ROOT / args.input)
    record = build_entry_authorization(
        plan,
        entry_plan_ref=args.entry_plan,
        design_decision=values["design_decision"],
        exact_bindings=values["exact_bindings"],
        component_bindings=values["component_bindings"],
        nonce_sha256=values["nonce_sha256"],
        resource_limits=values["resource_limits"],
        allowed_read_paths=values["allowed_read_paths"],
        expected_output_schemas=EXPECTED_OUTPUT_SCHEMAS,
        created_at=args.created_at,
    )
    out = ROOT / args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    auth_path = out / "RUNTIME_ENTRY_AUTHORIZATION.yaml"
    auth_path.write_text(yaml.safe_dump(record, sort_keys=False, allow_unicode=True, width=120), encoding="utf-8", newline="\n")
    print(f"Entry Authorization: {auth_path.relative_to(ROOT).as_posix()}")
    print(f"Authorization status: {record['status']}")
    print("No Approval was created or consumed, no CAS or state write occurred, and no Runtime Session was started.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
