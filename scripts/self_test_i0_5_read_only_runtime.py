#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    command = [
        sys.executable,
        "scripts/run_read_only_runtime_kernel.py",
        "--repo-root",
        str(ROOT),
        "--bundle",
        "examples/read_only_runtime/input/read_only_runtime_input_bundle_v0.1.yaml",
        "--now",
        "2026-07-13T04:02:00Z",
        "--format",
        "yaml",
    ]
    proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr)
        raise RuntimeError(f"read-only kernel CLI failed with exit code {proc.returncode}")
    required = [
        "result: COMPLETED_READ_ONLY_REPLAY",
        "model_calls: 0",
        "tool_calls: 0",
        "program_calls: 0",
        "network_calls: 0",
        "filesystem_writes: 0",
        "runtime_authoritative: false",
    ]
    for token in required:
        if token not in proc.stdout:
            raise AssertionError(f"CLI output missing required token: {token}")
    print("PASS: I0.5 read-only runtime kernel CLI self-test completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
