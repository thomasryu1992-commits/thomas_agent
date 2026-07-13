from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .kernel import run_bundle


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the Thomas Agent I0.5 non-authoritative read-only development replay kernel."
    )
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--now", default=None, help="Optional deterministic RFC3339 timestamp for tests.")
    parser.add_argument("--format", choices=["yaml", "json"], default="yaml")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve(strict=True)
    bundle_path = Path(args.bundle)
    if not bundle_path.is_absolute():
        bundle_path = repo_root / bundle_path
    result = run_bundle(repo_root, bundle_path, now=args.now or utc_now())

    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=False))
    else:
        print(yaml.safe_dump(result, allow_unicode=True, sort_keys=False), end="")
    return 0 if result["summary"]["result"] == "COMPLETED_READ_ONLY_REPLAY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
