from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable

import yaml

from lib.safe_io import atomic_write_text, safe_repo_path

EXCLUDED_PARTS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".runtime_locks",
    ".runtime_governance_state",
    "releases",
    "approvals",
    "activations",
    "revocations",
    "deactivations",
    "release_gate",
}

OWNED_ROOTS = [
    "THOMAS_CORE",
    "03_ROLE_CONTRACTS",
    "05_REGISTRIES",
    "docs",
    "schemas",
    "scripts",
    "examples",
    "tests",
    "runtime",
    "governance",
    "programs",
    "tools",
    ".github/workflows",
]

ROOT_FILES = [
    ".gitattributes",
    "requirements-validation.in",
    "requirements-validation.lock",
]


def owned_files(root: Path) -> list[Path]:
    root = root.resolve()
    paths: set[Path] = set()

    for rel in OWNED_ROOTS:
        directory = root / rel
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            if any(part in EXCLUDED_PARTS for part in relative.parts):
                continue
            if relative.as_posix() in {
                "THOMAS_CORE/REVIEW_CORE_RELEASE.yaml",
                "THOMAS_CORE/CURRENT_CORE_RELEASE.yaml",
            }:
                continue
            paths.add(path.resolve())

    for rel in ROOT_FILES:
        path = root / rel
        if path.exists() and path.is_file():
            paths.add(path.resolve())

    return sorted(paths, key=lambda p: p.relative_to(root).as_posix())


def repository_source_fingerprint(root: Path) -> tuple[str, list[dict[str, object]]]:
    root = root.resolve()
    entries: list[dict[str, object]] = []

    for path in owned_files(root):
        rel = path.relative_to(root).as_posix()
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        entries.append({
            "path": rel,
            "sha256": f"sha256:{digest}",
            "size_bytes": len(data),
        })

    payload = "".join(
        f"{item['path']}\0{item['sha256']}\0{item['size_bytes']}\n"
        for item in entries
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest(), entries


def write_gate_evidence(root: Path, rel: str, evidence: dict) -> Path:
    path = safe_repo_path(root, rel)
    atomic_write_text(
        path,
        yaml.safe_dump(evidence, sort_keys=False, allow_unicode=True, width=120),
    )
    return path


def load_gate_evidence(root: Path, rel: str) -> tuple[Path, dict]:
    path = safe_repo_path(root, rel, must_exist=True)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Release Gate evidence must be a YAML mapping")
    return path, data


def verify_gate_evidence(root: Path, rel: str) -> tuple[Path, dict]:
    path, evidence = load_gate_evidence(root, rel)

    if evidence.get("schema_version") != "thomas_release_gate_evidence.v0.1":
        raise ValueError("Release Gate evidence schema must be v0.1")
    if evidence.get("result") != "PASS":
        raise ValueError("Release Gate evidence result must be PASS")

    expected = evidence.get("repository_source_fingerprint")
    actual, entries = repository_source_fingerprint(root)
    if expected != actual:
        raise ValueError(
            "Repository source changed after the Release Gate. "
            "Run scripts/run_repository_release_gate.py again before building a Release."
        )

    if evidence.get("source_file_count") != len(entries):
        raise ValueError("Release Gate source file count mismatch")

    checks = evidence.get("checks")
    if not isinstance(checks, list) or not checks:
        raise ValueError("Release Gate evidence must contain completed checks")
    for item in checks:
        if not isinstance(item, dict) or item.get("result") != "PASS":
            raise ValueError("Every Release Gate check must be PASS")

    return path, evidence
