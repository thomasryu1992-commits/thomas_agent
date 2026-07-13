#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    proc = subprocess.run([sys.executable, "scripts/validate_i0_5_2_runtime_authoritative_read_only_entry.py"], cwd=ROOT, text=True, encoding="utf-8", timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(f"I0.5.2 Entry Design validation failed with exit code {proc.returncode}")
    print("PASS: I0.5.2 Runtime-authoritative read-only Entry Design Gate completed")
    print("This Gate grants no Runtime activation, Runtime entry, Approval consumption, or execution authority.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
