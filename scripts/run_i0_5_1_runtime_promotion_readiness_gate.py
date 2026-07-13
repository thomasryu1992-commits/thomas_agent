#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    command = [sys.executable, "scripts/validate_i0_5_1_runtime_promotion_readiness.py"]
    proc = subprocess.run(command, cwd=ROOT, text=True, encoding="utf-8", timeout=240)
    if proc.returncode != 0:
        raise RuntimeError(f"I0.5.1 readiness validation failed with exit code {proc.returncode}")
    print("PASS: I0.5.1 Rev3 split-readiness gate completed")
    print("This gate validates Design Readiness independently from Current-Core-dependent Activation Readiness.")
    print("It grants no Runtime activation, Core activation, execution permission, Tool/Program enablement, or external effect.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
