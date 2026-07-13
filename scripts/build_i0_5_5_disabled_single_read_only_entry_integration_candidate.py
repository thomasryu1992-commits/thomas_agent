#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.read_only_entry.integration_candidate import (
    build_disabled_single_entry_integration_candidate,
)


def load_yaml(path: Path):
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected YAML object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build an I0.5.5 disabled single read-only Entry integration candidate."
        )
    )
    parser.add_argument(
        "--authorization",
        default=(
            "examples/runtime_entry_authorization/"
            "runtime_entry_authorization_ready_for_thomas_action_approval_review_v0.1.yaml"
        ),
    )
    parser.add_argument("--durable-transition", default=None)
    parser.add_argument("--created-at", default="2026-07-13T11:00:00Z")
    parser.add_argument(
        "--output",
        default=(
            "build/i0_5_5_single_read_only_entry/"
            "DISABLED_SINGLE_READ_ONLY_ENTRY_INTEGRATION_CANDIDATE.yaml"
        ),
    )
    args = parser.parse_args()
    authorization_path = ROOT / args.authorization
    transition_path = ROOT / args.durable_transition if args.durable_transition else None
    record = build_disabled_single_entry_integration_candidate(
        load_yaml(authorization_path),
        authorization_ref=args.authorization,
        durable_transition=load_yaml(transition_path) if transition_path else None,
        durable_transition_ref=args.durable_transition,
        created_at=args.created_at,
    )
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        yaml.safe_dump(record, sort_keys=False, allow_unicode=True, width=120),
        encoding="utf-8",
        newline="\n",
    )
    print(f"Integration candidate: {output.relative_to(ROOT).as_posix()}")
    print(f"Result: {record['decision']['result']}")
    print(
        "No real Approval consumption, Runtime state write, Session start, "
        "handoff, or Kernel call occurred."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
