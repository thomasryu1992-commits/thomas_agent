from __future__ import annotations

import argparse
from pathlib import Path

from gate_matrix import DEFERRED_CHECKS
from lib.gate_runner import run_matrix


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PATH = ROOT / "generated/release_gate/DEFERRED_ARCHITECTURE_GATE_EVIDENCE.yaml"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate deferred Runtime Entry, Executor, Operations, Control, and Sandbox designs."
    )
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()

    run_matrix(
        root=ROOT,
        checks=DEFERRED_CHECKS,
        gate_id="thomas.deferred_architecture",
        evidence_path=None if args.check_only else EVIDENCE_PATH,
    )

    print("\nPASS: Thomas Agent Deferred Architecture Gate")
    print("Deferred validation does not activate or authorize any Runtime capability.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
