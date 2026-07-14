from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(script: str, *, check_only: bool) -> None:
    command = [sys.executable, script]
    if check_only:
        command.append("--check-only")
    completed = subprocess.run(command, cwd=ROOT, check=False)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Active, Deferred, and Legacy Compatibility Gates as separate scopes."
    )
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()

    run("scripts/run_active_gate.py", check_only=args.check_only)
    run("scripts/run_deferred_architecture_gate.py", check_only=args.check_only)
    run("scripts/run_legacy_compatibility_gate.py", check_only=args.check_only)

    print("\nPASS: Thomas Agent Split Repository Gate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
