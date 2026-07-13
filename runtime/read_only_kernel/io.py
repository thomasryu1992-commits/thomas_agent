from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .integrity import IntegrityError, sha256_bytes


class ReadBoundaryError(ValueError):
    pass


def resolve_read_only_path(repo_root: Path, relative_ref: str) -> Path:
    if not isinstance(relative_ref, str) or not relative_ref.strip():
        raise ReadBoundaryError("record reference must be a non-empty relative path")
    ref = Path(relative_ref)
    if ref.is_absolute():
        raise ReadBoundaryError(f"absolute paths are forbidden: {relative_ref}")
    if any(part in {"..", ""} for part in ref.parts):
        raise ReadBoundaryError(f"path traversal is forbidden: {relative_ref}")

    root = repo_root.resolve(strict=True)
    candidate = (root / ref).resolve(strict=True)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ReadBoundaryError(f"path escapes repo root: {relative_ref}") from exc

    current = root
    for part in ref.parts:
        current = current / part
        if current.is_symlink():
            raise ReadBoundaryError(f"symlink reads are forbidden: {relative_ref}")
    if not candidate.is_file():
        raise ReadBoundaryError(f"record reference is not a file: {relative_ref}")
    return candidate


def _load_markdown_frontmatter(text: str, source: str) -> dict[str, Any]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ReadBoundaryError(f"{source}: markdown record has no YAML frontmatter")
    try:
        end = lines[1:].index("---") + 1
    except ValueError as exc:
        raise ReadBoundaryError(f"{source}: unterminated YAML frontmatter") from exc
    data = yaml.safe_load("\n".join(lines[1:end]))
    if not isinstance(data, dict):
        raise ReadBoundaryError(f"{source}: frontmatter must decode to an object")
    return data


def parse_record_bytes(raw: bytes, path: Path) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReadBoundaryError(f"{path}: record must be valid UTF-8") from exc
    if path.suffix.lower() == ".md":
        data = _load_markdown_frontmatter(text, path.as_posix())
    else:
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ReadBoundaryError(f"{path}: record must decode to an object")
    return data


def read_record_snapshot(path: Path) -> tuple[dict[str, Any], str]:
    """Hash and parse one immutable in-memory byte snapshot."""
    raw = path.read_bytes()
    return parse_record_bytes(raw, path), sha256_bytes(raw)


def load_record(path: Path) -> dict[str, Any]:
    record, _ = read_record_snapshot(path)
    return record


def load_bundle(
    repo_root: Path,
    bundle: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, str], int]:
    refs = bundle.get("refs")
    hashes = bundle.get("sha256")
    if not isinstance(refs, dict) or not isinstance(hashes, dict):
        raise ReadBoundaryError("input bundle requires refs and sha256 objects")

    records: dict[str, dict[str, Any]] = {}
    actual_hashes: dict[str, str] = {}
    for name, ref in refs.items():
        if not isinstance(ref, str):
            raise ReadBoundaryError(f"refs.{name} must be a string")
        path = resolve_read_only_path(repo_root, ref)
        record, actual_hash = read_record_snapshot(path)
        expected_hash = hashes.get(name)
        if expected_hash != actual_hash:
            raise IntegrityError(
                f"input hash mismatch for {name}: expected {expected_hash}, actual {actual_hash}"
            )
        records[name] = record
        actual_hashes[name] = actual_hash
    return records, actual_hashes, len(records)
