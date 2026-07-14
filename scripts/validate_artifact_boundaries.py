#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from lib.artifact_boundaries import validate_artifact_boundaries


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    errors = validate_artifact_boundaries(ROOT)
    if errors:
        print("FAIL: Generated/Historical/Compatibility boundary validation found errors")
        for item in errors:
            print(f" - {item}")
        return 1
    print("PASS: Generated, Historical, release-snapshot, and compatibility-retirement boundaries validated")
    print("Generated or Historical evidence grants no Runtime, Permission, Approval, Authority, or activation capability.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
