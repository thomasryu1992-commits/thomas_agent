
#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from lib.resource_request import compute_request_fingerprint, load_yaml

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a review-only Program Request packet from a complete draft")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    source = load_yaml((ROOT / args.input).resolve() if not Path(args.input).is_absolute() else Path(args.input))
    source["request_fingerprint"] = compute_request_fingerprint(source["request_fingerprint_payload"])
    output = (ROOT / args.output).resolve() if not Path(args.output).is_absolute() else Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(source, sort_keys=False, allow_unicode=True, width=110), encoding="utf-8", newline="\n")
    print(f"WROTE: {output}")
    print("Review-only Program Request created; no Program execution or executor handoff occurred")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
