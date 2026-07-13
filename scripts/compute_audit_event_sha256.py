#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import yaml

from lib.execution_foundation import compute_audit_event_sha256


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute an Audit Event v0.1 SHA-256")
    parser.add_argument("payload")
    args = parser.parse_args()
    data = yaml.safe_load(Path(args.payload).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("payload must be a YAML mapping")
    print(compute_audit_event_sha256(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
