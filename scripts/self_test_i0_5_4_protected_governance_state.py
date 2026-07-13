#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    proc = subprocess.run(
        [sys.executable, "scripts/validate_i0_5_4_protected_governance_state.py"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        timeout=180,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"I0.5.4 protected-state self-test failed with exit code {proc.returncode}")
    print("PASS: I0.5.4 protected local governance state self-test completed")
    print("The self-test writes only synthetic temporary SQLite state and grants no Runtime Entry capability.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
