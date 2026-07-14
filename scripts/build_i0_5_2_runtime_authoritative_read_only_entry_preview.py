#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.read_only_entry.planner import build_entry_plan
from runtime.read_only_entry.disabled_adapter import build_disabled_entry_evidence


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_yaml(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected YAML object")
    return value


def write_yaml(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build I0.5.2 review-only Entry Plan and disabled adapter evidence.")
    parser.add_argument("--readiness", default="generated/deferred/runtime_entry/i0_5_1_runtime_promotion/RUNTIME_PROMOTION_READINESS.yaml")
    parser.add_argument("--output-dir", default="build/runtime_authoritative_read_only_entry")
    parser.add_argument("--now", default=None)
    args = parser.parse_args()
    readiness_path = (ROOT / args.readiness).resolve() if not Path(args.readiness).is_absolute() else Path(args.readiness).resolve()
    readiness = load_yaml(readiness_path)
    created_at = args.now or utc_now()
    plan = build_entry_plan(readiness, readiness_ref=readiness_path.relative_to(ROOT).as_posix(), created_at=created_at)
    output_dir = (ROOT / args.output_dir).resolve()
    plan_path = output_dir / "RUNTIME_AUTHORITATIVE_READ_ONLY_ENTRY_PLAN.yaml"
    evidence_path = output_dir / "DISABLED_ENTRY_EVIDENCE.yaml"
    write_yaml(plan_path, plan)
    evidence = build_disabled_entry_evidence(plan, plan_ref=plan_path.relative_to(ROOT).as_posix(), created_at=created_at)
    write_yaml(evidence_path, evidence)
    print("PASS: I0.5.2 review-only Entry Plan and disabled adapter evidence built")
    print("Entry Plan: " + plan_path.relative_to(ROOT).as_posix())
    print("Disabled Evidence: " + evidence_path.relative_to(ROOT).as_posix())
    print("No Runtime session, Approval consumption, Executor handoff, external action, or Runtime mutation occurred.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
