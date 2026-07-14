from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import yaml


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def run_check(
    *,
    root: Path,
    python: str,
    label: str,
    command: list[str],
    timeout: int = 300,
) -> dict[str, str]:
    resolved = [python, *command]
    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }

    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as output_file:
        proc = subprocess.run(
            resolved,
            cwd=root,
            stdout=output_file,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            timeout=timeout,
            check=False,
        )
        output_file.flush()
        output_file.seek(0)
        output = output_file.read().strip()

    print(f"\n=== {label} ===")
    if output:
        print(output)

    if proc.returncode != 0:
        raise RuntimeError(
            f"{label} failed with exit code {proc.returncode}: "
            + " ".join(resolved)
        )

    return {
        "check_id": label.lower().replace(" ", "_"),
        "label": label,
        "command": " ".join(resolved),
        "result": "PASS",
        "output_sha256": "sha256:"
        + hashlib.sha256(output.encode("utf-8")).hexdigest(),
    }


def run_matrix(
    *,
    root: Path,
    checks: Iterable[tuple[str, list[str]]],
    gate_id: str,
    evidence_path: Path | None,
    python: str | None = None,
) -> dict:
    python = python or os.environ.get("THOMAS_VALIDATION_PYTHON", sys.executable)
    results = [
        run_check(
            root=root,
            python=python,
            label=label,
            command=command,
        )
        for label, command in checks
    ]

    evidence = {
        "schema_version": "thomas_split_gate_evidence.v0.1",
        "gate_id": gate_id,
        "result": "PASS",
        "generated_at_utc": utc_now(),
        "checks": results,
        "scope": {
            "grants_core_approval": False,
            "grants_core_activation": False,
            "grants_runtime_activation": False,
            "grants_execution_permission": False,
            "grants_tool_or_program_enablement": False,
            "grants_external_execution": False,
            "grants_financial_execution": False,
        },
    }

    if evidence_path is not None:
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            yaml.safe_dump(
                evidence,
                sort_keys=False,
                allow_unicode=True,
                width=120,
            ),
            encoding="utf-8",
        )

    return evidence
