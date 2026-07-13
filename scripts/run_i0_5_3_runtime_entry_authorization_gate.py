#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    proc = subprocess.run([sys.executable, "scripts/validate_i0_5_3_runtime_entry_authorization.py"], cwd=ROOT, text=True, encoding="utf-8", timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(f"I0.5.3 validation failed with exit code {proc.returncode}")
    print("PASS: I0.5.3 Exact Entry Authorization and At-Most-Once Transition Design Gate completed")
    print("This Gate grants no Runtime permission/activation/entry, performs no Approval consumption or CAS, and starts no Runtime Session.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
