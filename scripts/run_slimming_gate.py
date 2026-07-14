#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(label: str, command: list[str], timeout: int = 900) -> None:
    print(f"\n=== {label} ===")
    proc = subprocess.run(command, cwd=ROOT, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"{label} failed with exit code {proc.returncode}")


def main() -> int:
    python = sys.executable
    run(
        "Compile Active Slim Architecture",
        [
            python,
            "-m",
            "compileall",
            "-q",
            "runtime/read_only_kernel",
            "runtime/registry_resolution.py",
            "scripts",
            "tests",
        ],
    )
    run("Final Slimming Invariants", [python, "scripts/validate_slimming_package.py"])
    run("Artifact Boundaries", [python, "scripts/validate_artifact_boundaries.py"])
    run("Gate Separation", [python, "scripts/validate_gate_separation.py"])
    run("Active Kernel Decomposition", [python, "scripts/validate_active_kernel_decomposition.py"])
    run("Deferred Architecture Structure", [python, "scripts/validate_deferred_architecture.py", "--structure-only"])
    run(
        "Focused Architecture Tests",
        [
            python,
            "-m",
            "unittest",
            "tests.test_architecture_slimming",
            "tests.test_artifact_boundaries",
            "tests.test_active_kernel_decomposition",
            "tests.test_gate_separation",
            "tests.test_deferred_architecture",
            "-v",
        ],
        timeout=1200,
    )
    print("\nPASS: final Architecture Slimming Gate completed")
    print("Generated, Historical, Deferred, compatibility, Runtime, and authority boundaries remain separate and fail closed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
