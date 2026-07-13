#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(label: str, command: list[str]) -> None:
    print(f"\n=== {label} ===")
    proc = subprocess.run(command, cwd=ROOT, text=True, encoding="utf-8", timeout=240)
    if proc.returncode != 0:
        raise RuntimeError(f"{label} failed with exit code {proc.returncode}")


def main() -> int:
    run(
        "I0.5.4 Protected Local Governance State Candidate",
        [sys.executable, "scripts/validate_i0_5_4_protected_governance_state.py"],
    )
    print("\nPASS: I0.5.4 protected local governance state and durable at-most-once transition candidate Gate completed")
    print("This Gate validates synthetic temporary-state mechanics only and grants no Runtime activation, real Approval consumption, Session start, or Kernel call.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
