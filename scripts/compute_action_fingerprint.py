#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from lib.action_fingerprint import compute_action_fingerprint


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compute a deterministic SHA-256 fingerprint for an action payload."
    )
    parser.add_argument("--input", required=True, help="YAML file containing the payload.")
    parser.add_argument(
        "--field",
        default=None,
        help="Optional top-level field containing the payload, such as fingerprint_payload.",
    )
    args = parser.parse_args()

    path = Path(args.input)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if args.field:
        data = data[args.field]
    if not isinstance(data, dict):
        raise ValueError("Selected fingerprint payload must be a YAML mapping")

    print(compute_action_fingerprint(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
