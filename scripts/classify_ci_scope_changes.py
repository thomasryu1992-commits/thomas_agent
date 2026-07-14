#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from gate_matrix import classify_ci_scopes


ROOT = Path(__file__).resolve().parents[1]
ZERO_SHA = "0" * 40


def run_git(args: list[str]) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed ({proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
        )
    return proc.stdout.strip()


def normalize_sha(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if not value or value == ZERO_SHA:
        return None
    return value


def resolve_diff_range(args: argparse.Namespace) -> tuple[str, str]:
    event_name = args.event_name
    if event_name == "pull_request":
        base = normalize_sha(args.base)
        head = normalize_sha(args.pr_head) or normalize_sha(args.head)
    else:
        base = normalize_sha(args.before)
        head = normalize_sha(args.head)

    if head is None:
        head = run_git(["rev-parse", "HEAD"])
    if base is None:
        parent = subprocess.run(
            ["git", "rev-parse", f"{head}^"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        if parent.returncode == 0:
            base = parent.stdout.strip()
        else:
            base = run_git(["hash-object", "-t", "tree", "/dev/null"])
    return base, head


def changed_paths(base: str, head: str) -> list[str]:
    output = run_git([
        "diff",
        "--name-only",
        "--diff-filter=ACDMRTUXB",
        base,
        head,
        "--",
    ])
    return [line.strip().replace("\\", "/") for line in output.splitlines() if line.strip()]


def write_outputs(path: Path, result: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for key in ("active", "deferred", "legacy", "full"):
            handle.write(f"{key}={'true' if result[key] else 'false'}\n")
        handle.write(f"changed_count={result['changed_count']}\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Classify changed repository paths into canonical CI Gate scopes."
    )
    parser.add_argument("--event-name", default="local")
    parser.add_argument("--base", default="")
    parser.add_argument("--pr-head", default="")
    parser.add_argument("--before", default="")
    parser.add_argument("--head", default="")
    parser.add_argument("--github-output")
    parser.add_argument("--paths-file")
    parser.add_argument("--all-scopes", action="store_true")
    args = parser.parse_args()

    if args.all_scopes or args.event_name == "workflow_dispatch":
        paths: list[str] = []
        scopes = {"active": True, "deferred": True, "legacy": True, "full": True}
    elif args.paths_file:
        paths = [
            line.strip().replace("\\", "/")
            for line in Path(args.paths_file).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        scopes = classify_ci_scopes(paths)
    else:
        base, head = resolve_diff_range(args)
        paths = changed_paths(base, head)
        scopes = classify_ci_scopes(paths)

    result: dict[str, object] = {
        **scopes,
        "changed_count": len(paths),
        "changed_paths": paths,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))

    if args.github_output:
        write_outputs(Path(args.github_output), result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
