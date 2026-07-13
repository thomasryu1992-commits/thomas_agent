#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import yaml

from lib.execution_foundation import compute_execution_request_fingerprint


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute an I0.4.4 Review-only Execution Request fingerprint")
    parser.add_argument("payload")
    args = parser.parse_args()
    data = yaml.safe_load(Path(args.payload).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("payload must be a YAML mapping")
    print(compute_execution_request_fingerprint(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
