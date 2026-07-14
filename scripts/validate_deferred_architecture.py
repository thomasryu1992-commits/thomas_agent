#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib.deferred_validation import (
    FAMILY_ORDER,
    DeferredValidationError,
    load_manifest,
    run_detailed_validators,
    validate_contract_schema_parity,
    validate_gate_ownership,
    validate_manifest_structure,
    validate_precedence_boundary,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the one canonical Thomas Agent Deferred Architecture boundary.")
    parser.add_argument("--family", choices=["all", *FAMILY_ORDER], default="all")
    parser.add_argument("--structure-only", action="store_true")
    args = parser.parse_args()

    try:
        manifest = load_manifest(ROOT)
        validate_manifest_structure(ROOT, manifest)
        validate_contract_schema_parity(ROOT, manifest)
        validate_gate_ownership(ROOT)
        validate_precedence_boundary(ROOT)

        selected = list(FAMILY_ORDER) if args.family == "all" else [args.family]
        detail_count = 0
        if not args.structure_only:
            detail_count = run_detailed_validators(ROOT, manifest, selected)
            test = subprocess.run(
                [sys.executable, "-m", "unittest", "tests.test_deferred_architecture", "-v"],
                cwd=ROOT,
                text=True,
                encoding="utf-8",
                timeout=300,
            )
            if test.returncode != 0:
                raise DeferredValidationError("tests.test_deferred_architecture failed")
    except (OSError, ValueError, DeferredValidationError) as exc:
        print(f"FAIL: Deferred Architecture validation failed: {exc}")
        return 1

    print("PASS: canonical Deferred Architecture validation completed")
    print(f"Families: {', '.join(selected)}")
    print(f"Subordinate detailed validators: {detail_count}")
    print("No Runtime Entry, Executor, Approval consumption, state mutation, Session start, Kernel handoff, Tool/Program execution, Scheduler/Control dispatch, external action, financial action, Permission expansion, Authority expansion, or Core activation was enabled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
