#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.read_only_kernel.kernel import run_bundle

FIXED_NOW = "2026-07-13T04:02:00Z"
EXAMPLES = {
    "examples/read_only_runtime/input/read_only_runtime_input_bundle_v0.1.yaml":
        "examples/read_only_runtime/output/read_only_runtime_run_completed_v0.1.yaml",
    "examples/read_only_runtime/input/read_only_runtime_input_bundle_tool_request_blocked_v0.1.yaml":
        "examples/read_only_runtime/output/read_only_runtime_run_blocked_tool_request_v0.1.yaml",
}


def main() -> int:
    for input_rel, output_rel in EXAMPLES.items():
        result = run_bundle(ROOT, ROOT / input_rel, now=FIXED_NOW)
        output_path = ROOT / output_rel
        output_path.write_text(
            yaml.safe_dump(result, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
            newline="\n",
        )
        print(f"wrote {output_rel}: {result['summary']['result']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
