#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(label: str, command: list[str]) -> None:
    print(f"\n=== {label} ===")
    proc = subprocess.run(command, cwd=ROOT, text=True, encoding="utf-8", timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(f"{label} failed with exit code {proc.returncode}")


def main() -> int:
    run("I0.5 Read-only Runtime Validator", [sys.executable, "scripts/validate_i0_5_read_only_runtime.py"])
    run("I0.5 Read-only Runtime CLI Self-Test", [sys.executable, "scripts/self_test_i0_5_read_only_runtime.py"])
    run(
        "I0.5.1 Runtime Promotion Readiness",
        [sys.executable, "scripts/validate_i0_5_1_runtime_promotion_readiness.py"],
    )
    run(
        "I0.5.2 Runtime-Authoritative Read-only Entry Design",
        [sys.executable, "scripts/validate_i0_5_2_runtime_authoritative_read_only_entry.py"],
    )
    run(
        "I0.5.3 Exact Entry Authorization and At-Most-Once Transition Design",
        [sys.executable, "scripts/validate_i0_5_3_runtime_entry_authorization.py"],
    )
    run(
        "I0.5.4 Protected Local Governance State and Durable CAS Candidate",
        [sys.executable, "scripts/validate_i0_5_4_protected_governance_state.py"],
    )
    run(
        "I0.5.5 Disabled Single Read-only Entry Integration Candidate",
        [sys.executable, "scripts/validate_i0_5_5_disabled_single_read_only_entry_integration.py"],
    )
    print("\nPASS: I0.5/I0.5.1/I0.5.2/I0.5.3/I0.5.4/I0.5.5 read-only Runtime, readiness, Entry Design, exact-entry authorization, protected-state, and disabled integration Candidate Gate completed")
    print("This gate validates DEVELOPMENT_REPLAY only and grants no Runtime activation or execution authority.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
