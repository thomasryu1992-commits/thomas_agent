from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str]) -> None:
    print("+", " ".join(command))
    completed = subprocess.run(command, cwd=ROOT, check=False)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def main() -> int:
    run([sys.executable, "scripts/validate_slimming_package.py"])
    run([
        sys.executable,
        "-m",
        "unittest",
        "tests.test_architecture_slimming",
        "-v",
    ])
    run([
        sys.executable,
        "-m",
        "compileall",
        "-q",
        "runtime/compat",
        "runtime/kernel_slim",
        "runtime/read_only_kernel/slim_candidate.py",
    ])
    print("THOMAS_AGENT_SLIMMING_GATE: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
