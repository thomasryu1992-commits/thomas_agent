#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Compatibility ownership markers for historical validators. The canonical owner
# is scripts/validate_deferred_architecture.py --family runtime_entry.
# scripts/validate_i0_5_1_runtime_promotion_readiness.py
# scripts/validate_i0_5_2_runtime_authoritative_read_only_entry.py
# scripts/validate_i0_5_3_runtime_entry_authorization.py
# scripts/validate_i0_5_4_protected_governance_state.py
# scripts/validate_i0_5_5_disabled_single_read_only_entry_integration.py


def run(label: str, command: list[str]) -> None:
    print(f"\n=== {label} ===")
    proc = subprocess.run(command, cwd=ROOT, text=True, encoding="utf-8", timeout=900)
    if proc.returncode != 0:
        raise RuntimeError(f"{label} failed with exit code {proc.returncode}")


def main() -> int:
    run("I0.5 Active Read-only Runtime Validator", [sys.executable, "scripts/validate_i0_5_read_only_runtime.py"])
    run("I0.5 Active Read-only Runtime CLI Self-Test", [sys.executable, "scripts/self_test_i0_5_read_only_runtime.py"])
    run(
        "Deferred Runtime Entry Family",
        [sys.executable, "scripts/validate_deferred_architecture.py", "--family", "runtime_entry"],
    )
    print("\nPASS: Active I0.5 read-only replay and the consolidated Deferred Runtime Entry family completed")
    print("The Deferred family grants no Runtime activation, Approval consumption, state mutation, Session start, Kernel handoff, or execution authority.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
