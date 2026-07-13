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
    print("\nPASS: I0.5 read-only runtime kernel candidate gate completed")
    print("This gate validates DEVELOPMENT_REPLAY only and grants no Runtime activation or execution authority.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
