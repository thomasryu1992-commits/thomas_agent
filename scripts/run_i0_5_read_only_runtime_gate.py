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
    print("\nPASS: I0.5/I0.5.1/I0.5.2 read-only Runtime, readiness, and Entry Design Gate completed")
    print("This gate validates DEVELOPMENT_REPLAY only and grants no Runtime activation or execution authority.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
