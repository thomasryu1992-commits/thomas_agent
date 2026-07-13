#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    proc = subprocess.run(
        [
            sys.executable,
            "scripts/validate_i0_5_5_disabled_single_read_only_entry_integration.py",
        ],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        timeout=240,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"I0.5.5 validation failed with exit code {proc.returncode}"
        )
    print(
        "PASS: I0.5.5 disabled single read-only Entry integration "
        "candidate Gate completed"
    )
    print(
        "The Gate constructs review-only candidate envelopes and never "
        "consumes real Approval, writes production state, starts Runtime, "
        "performs handoff, or calls the Kernel."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
