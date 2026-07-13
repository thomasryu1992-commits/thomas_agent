#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib.runtime_promotion_readiness import (
    DEFAULT_CI_EVIDENCE_REL,
    build_component_attestation,
    build_runtime_promotion_readiness,
    utc_now,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build I0.5.1 Rev3 split Design/Activation readiness evidence.")
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--created-at", default=None)
    parser.add_argument(
        "--github-ci-evidence",
        default=None,
        help=(
            "Repository-relative GitHub CI evidence YAML produced by collect_github_ci_evidence.py. "
            f"The conventional path is {DEFAULT_CI_EVIDENCE_REL}."
        ),
    )
    parser.add_argument("--output-dir", default="build/i0_5_1_runtime_promotion")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve(strict=True)
    created_at = args.created_at or utc_now()
    attestation = build_component_attestation(repo_root, created_at=created_at)
    readiness = build_runtime_promotion_readiness(
        repo_root,
        created_at=created_at,
        github_ci_evidence_ref=args.github_ci_evidence,
    )
    output_dir = repo_root / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    attestation_path = output_dir / "RUNTIME_COMPONENT_ATTESTATION.yaml"
    readiness_path = output_dir / "RUNTIME_PROMOTION_READINESS.yaml"
    attestation_path.write_text(
        yaml.safe_dump(attestation, sort_keys=False, allow_unicode=True, width=120),
        encoding="utf-8",
        newline="\n",
    )
    readiness_path.write_text(
        yaml.safe_dump(readiness, sort_keys=False, allow_unicode=True, width=120),
        encoding="utf-8",
        newline="\n",
    )
    print(f"Component attestation: {attestation_path.relative_to(repo_root).as_posix()}")
    print(f"Runtime readiness: {readiness_path.relative_to(repo_root).as_posix()}")
    print(f"Attestation result: {attestation['summary']['result']}")
    print(f"Design readiness: {readiness['summary']['design_readiness']['result']}")
    print(f"Activation readiness: {readiness['summary']['activation_readiness']['result']}")
    print("No CLI Boolean may mark GitHub CI or Current Core as verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
