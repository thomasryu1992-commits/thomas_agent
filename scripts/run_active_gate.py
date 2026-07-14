from __future__ import annotations

import argparse
from pathlib import Path

from gate_matrix import ACTIVE_CHECKS
from lib.gate_runner import run_matrix


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PATH = ROOT / "generated/release_gate/ACTIVE_GATE_EVIDENCE.yaml"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run only the active Thomas Agent architecture and read-only runtime checks."
    )
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()

    run_matrix(
        root=ROOT,
        checks=ACTIVE_CHECKS,
        gate_id="thomas.active_architecture",
        evidence_path=None if args.check_only else EVIDENCE_PATH,
    )

    print("\nPASS: Thomas Agent Active Gate")
    print("This Gate grants no Core, Runtime, Tool, Program, external, or financial authority.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
