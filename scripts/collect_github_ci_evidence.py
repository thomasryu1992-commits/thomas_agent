#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib.runtime_promotion_readiness import (
    EXPECTED_JOB_NAMES,
    EXPECTED_WORKFLOW_NAME,
    WORKFLOW_REL,
    build_github_ci_evidence,
    git_head_sha,
    git_origin_repository,
    sha256_file,
    utc_now,
    validate_github_ci_evidence_semantics,
)


def gh_json(args: list[str]) -> dict:
    gh = shutil.which("gh")
    if not gh:
        raise RuntimeError("GitHub CLI 'gh' is required")
    proc = subprocess.run(
        [gh, *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gh command failed ({proc.returncode}): {proc.stderr.strip()}")
    value = json.loads(proc.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("GitHub API response must be an object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect hash-bound GitHub Actions evidence from the live GitHub API using existing gh authentication."
    )
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--repository", default=None, help="owner/repo; defaults to the local origin repository")
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--output", default="build/i0_5_1_runtime_promotion/GITHUB_CI_EVIDENCE.yaml")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve(strict=True)
    repository = args.repository or git_origin_repository(repo_root)
    if repository != git_origin_repository(repo_root):
        raise RuntimeError("--repository must match the local Git origin")

    run = gh_json(["api", f"repos/{repository}/actions/runs/{args.run_id}"])
    jobs_response = gh_json(["api", f"repos/{repository}/actions/runs/{args.run_id}/jobs?per_page=100"])
    jobs = jobs_response.get("jobs")
    if not isinstance(jobs, list):
        raise RuntimeError("GitHub jobs response does not contain a jobs list")

    if run.get("name") != EXPECTED_WORKFLOW_NAME:
        raise RuntimeError(f"unexpected workflow name: {run.get('name')!r}")
    if run.get("path") != WORKFLOW_REL:
        raise RuntimeError(f"unexpected workflow path: {run.get('path')!r}")
    if run.get("head_sha") != git_head_sha(repo_root):
        raise RuntimeError("GitHub Actions run head_sha does not match local HEAD")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        raise RuntimeError("GitHub Actions run must be completed with success")

    selected: dict[str, dict] = {}
    for key, expected_name in EXPECTED_JOB_NAMES.items():
        matches = [item for item in jobs if isinstance(item, dict) and item.get("name") == expected_name]
        if len(matches) != 1:
            raise RuntimeError(f"expected exactly one successful {key} job named {expected_name!r}")
        item = matches[0]
        if item.get("status") != "completed" or item.get("conclusion") != "success":
            raise RuntimeError(f"GitHub Actions {key} job did not complete successfully")
        selected[key] = item

    evidence = build_github_ci_evidence(
        repository_full_name=repository,
        workflow_name=run["name"],
        workflow_path=run["path"],
        workflow_sha256=sha256_file(repo_root / WORKFLOW_REL),
        run_id=int(run["id"]),
        run_attempt=int(run.get("run_attempt", 1)),
        event=str(run["event"]),
        head_sha=str(run["head_sha"]),
        html_url=str(run["html_url"]),
        created_at=str(run["created_at"]),
        completed_at=str(run["updated_at"]),
        ubuntu_job_id=int(selected["ubuntu"]["id"]),
        ubuntu_job_name=str(selected["ubuntu"]["name"]),
        ubuntu_completed_at=str(selected["ubuntu"]["completed_at"]),
        windows_job_id=int(selected["windows"]["id"]),
        windows_job_name=str(selected["windows"]["name"]),
        windows_completed_at=str(selected["windows"]["completed_at"]),
        collected_at=utc_now(),
    )
    validate_github_ci_evidence_semantics(evidence)

    output = Path(args.output)
    if not output.is_absolute():
        output = repo_root / output
    output = output.resolve()
    try:
        output.relative_to(repo_root)
    except ValueError as exc:
        raise RuntimeError("output path must remain inside the repository") from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        yaml.safe_dump(evidence, sort_keys=False, allow_unicode=True, width=120),
        encoding="utf-8",
        newline="\n",
    )
    print(f"PASS: GitHub CI evidence collected from live API: {output.relative_to(repo_root).as_posix()}")
    print(f"Run: {run['html_url']}")
    print("Ubuntu and Windows validation jobs: success")
    print("No credential values were read, printed, or stored by this script.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
