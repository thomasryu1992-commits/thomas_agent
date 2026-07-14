from __future__ import annotations

import argparse
from pathlib import Path

from gate_matrix import LEGACY_COMPATIBILITY_CHECKS
from lib.gate_runner import run_matrix


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PATH = ROOT / "generated/release_gate/LEGACY_COMPATIBILITY_GATE_EVIDENCE.yaml"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate frozen I0.4 and Core release compatibility."
    )
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()

    run_matrix(
        root=ROOT,
        checks=LEGACY_COMPATIBILITY_CHECKS,
        gate_id="thomas.legacy_compatibility",
        evidence_path=None if args.check_only else EVIDENCE_PATH,
    )

    print("\nPASS: Thomas Agent Legacy Compatibility Gate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
