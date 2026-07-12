#!/usr/bin/env python3
from __future__ import annotations

import importlib.metadata
import json
import re
from pathlib import Path
from urllib.parse import unquote

import yaml

ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def construct_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key: {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    construct_mapping,
)

YAML_FENCE = re.compile(r"```(?:yaml|yml)\s*\n(.*?)```", re.IGNORECASE | re.DOTALL)
MD_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


def error(message: str) -> None:
    ERRORS.append(message)


def validate_dependency_lock() -> None:
    path = ROOT / "requirements-validation.lock"
    if not path.exists():
        error("requirements-validation.lock is missing")
        return

    expected: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "==" not in line:
            error(f"Unpinned dependency: {line}")
            continue
        name, version = line.split("==", 1)
        expected[name] = version

    for name, version in expected.items():
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            error(f"Dependency not installed: {name}")
            continue
        if actual != version:
            error(f"{name}: expected {version}, installed {actual}")


def owned_files():
    roots = [
        ROOT / "THOMAS_CORE",
        ROOT / "03_ROLE_CONTRACTS",
        ROOT / "05_REGISTRIES",
        ROOT / "docs",
        ROOT / "schemas",
        ROOT / "examples",
        ROOT / "tests",
    ]
    paths: set[Path] = set()
    for directory in roots:
        if directory.exists():
            paths.update(directory.rglob("*"))
    paths.update(
        path for path in ROOT.iterdir()
        if path.is_file() and path.suffix.lower() in {".yaml", ".yml", ".json"}
    )
    return sorted(path for path in paths if path.is_file())


def validate_structured_files() -> None:
    for path in owned_files():
        if any(part in {".git", "__pycache__", ".pytest_cache"} for part in path.parts):
            continue
        suffix = path.suffix.lower()
        try:
            if suffix in {".yaml", ".yml"}:
                yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
            elif suffix == ".json":
                json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            error(f"{path.relative_to(ROOT)}: {exc}")


def validate_markdown_yaml() -> None:
    for directory in [ROOT / "THOMAS_CORE", ROOT / "03_ROLE_CONTRACTS", ROOT / "docs"]:
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*.md")):
            text = path.read_text(encoding="utf-8")
            rel = path.relative_to(ROOT)
            if text.startswith("---\n"):
                end = text.find("\n---\n", 4)
                if end < 0:
                    error(f"{rel}: unterminated front matter")
                else:
                    try:
                        yaml.load(text[4:end], Loader=UniqueKeyLoader)
                    except Exception as exc:
                        error(f"{rel}: front matter YAML error: {exc}")
            for index, match in enumerate(YAML_FENCE.finditer(text), start=1):
                try:
                    yaml.load(match.group(1), Loader=UniqueKeyLoader)
                except Exception as exc:
                    error(f"{rel}: yaml_block_{index}: {exc}")


def validate_document_links() -> None:
    for directory in [ROOT / "THOMAS_CORE", ROOT / "03_ROLE_CONTRACTS", ROOT / "05_REGISTRIES", ROOT / "docs"]:
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*.md")):
            text = path.read_text(encoding="utf-8")
            rel = path.relative_to(ROOT)
            for raw in MD_LINK.findall(text):
                target = raw.strip()
                if not target or target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                target = unquote(target.split("#", 1)[0])
                if not target:
                    continue
                candidate = (path.parent / target).resolve()
                try:
                    candidate.relative_to(ROOT.resolve())
                except ValueError:
                    error(f"{rel}: link escapes Repository: {raw}")
                    continue
                if not candidate.exists():
                    error(f"{rel}: missing linked file: {raw}")


def validate_python_syntax() -> None:
    for path in sorted((ROOT / "scripts").rglob("*.py")):
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except Exception as exc:
            error(f"{path.relative_to(ROOT)}: {exc}")


def main() -> int:
    validate_dependency_lock()
    validate_structured_files()
    validate_markdown_yaml()
    validate_document_links()
    validate_python_syntax()

    if ERRORS:
        print("FAIL: static integrity validation found errors")
        for item in ERRORS:
            print(f" - {item}")
        return 1

    print("PASS: static integrity validation completed")
    print(
        "Checked exact dependency lock, YAML/JSON parsing, duplicate YAML keys, Markdown YAML, "
        "local links, and Python syntax"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
