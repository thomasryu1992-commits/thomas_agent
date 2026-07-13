#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any
import yaml


def load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected YAML mapping")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an I0.4.4 no-execution Result preview")
    parser.add_argument("--execution-request", required=True)
    parser.add_argument("--execution-result-id", required=True)
    parser.add_argument("--status", choices=["NOT_EXECUTED", "BLOCKED", "PREVIEWED", "EXPIRED", "SUPERSEDED"], default="BLOCKED")
    parser.add_argument("--summary", required=True)
    parser.add_argument("--created-at", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    request_path = Path(args.execution_request)
    request = load(request_path)
    block_reasons = request.get("validation", {}).get("block_reasons", []) if args.status == "BLOCKED" else []
    record = {
        "schema_version": "execution_result.v0.1",
        "execution_result_id": args.execution_result_id,
        "execution_request_id": request["execution_request_id"],
        "execution_request_ref": request_path.as_posix(),
        "execution_request_fingerprint": request["request_fingerprint"],
        "trace_id": request["trace_id"],
        "task_id": request["task_id"],
        "task_revision": request["task_revision"],
        "core_context_binding_id": request["core_context_binding_id"],
        "result_status": args.status,
        "execution_evidence": {
            "execution_performed": False,
            "executor_called": False,
            "execution_attempt_id": None,
            "started_at": None,
            "finished_at": None,
            "tool_execution_performed": False,
            "program_execution_performed": False,
            "external_side_effect_performed": False,
            "financial_side_effect_performed": False,
            "runtime_mutation_performed": False,
            "side_effect_summary": [],
        },
        "output": {
            "output_refs": [],
            "output_sha256": [],
            "preview_summary": args.summary,
            "block_reasons": block_reasons,
        },
        "metrics": {
            "runtime_seconds": 0,
            "tool_calls": 0,
            "program_calls": 0,
            "external_calls": 0,
            "cost_decimal": "0",
            "cost_currency": "USD",
        },
        "error": {"error_code": None, "error_message": None},
        "runtime_effect": {
            "mode": "REVIEW_ONLY",
            "grants_permission": False,
            "grants_approval": False,
            "grants_authority": False,
            "grants_execution": False,
            "grants_activation": False,
            "executor_handoff_allowed": False,
            "side_effects_allowed": False,
            "runtime_mutation_allowed": False,
        },
        "lifecycle": {"created_at": args.created_at, "supersedes": []},
        "audit_refs": [f"audit:execution_result:{args.execution_result_id}"],
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(record, sort_keys=False, allow_unicode=True, width=110), encoding="utf-8", newline="\n")
    print(f"WROTE: {output}")
    print("NO_EXECUTION: all execution and side-effect flags remain false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
