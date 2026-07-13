from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


class RuntimeSchemaError(ValueError):
    pass


def validate_against_schema(value: Any, schema_path: Path, label: str) -> int:
    if not schema_path.is_file():
        raise RuntimeSchemaError(f"{label}: required schema is missing: {schema_path.as_posix()}")
    registry = Registry()
    schema = None
    read_count = 0
    try:
        for candidate in sorted(schema_path.parent.glob("*.schema.json")):
            contents = json.loads(candidate.read_text(encoding="utf-8"))
            read_count += 1
            if candidate == schema_path:
                schema = contents
            resource = Resource.from_contents(contents)
            registry = registry.with_resource(candidate.name, resource)
            schema_id = contents.get("$id")
            if isinstance(schema_id, str) and schema_id:
                registry = registry.with_resource(schema_id, resource)
        if schema is None:
            raise RuntimeSchemaError(f"{label}: schema could not be loaded: {schema_path.as_posix()}")
        errors = sorted(
            Draft202012Validator(
                schema,
                registry=registry,
                format_checker=FormatChecker(),
            ).iter_errors(value),
            key=lambda item: list(item.absolute_path),
        )
    except Exception as exc:
        raise RuntimeSchemaError(f"{label}: schema resolution failed: {exc}") from exc
    if errors:
        rendered = "; ".join(
            f"{'.'.join(str(part) for part in item.absolute_path) or '$'}: {item.message}"
            for item in errors[:5]
        )
        raise RuntimeSchemaError(f"{label}: schema validation failed: {rendered}")
    return read_count


def validate_record_when_schema_exists(repo_root: Path, value: dict[str, Any], label: str) -> int:
    schema_version = value.get("schema_version")
    if not isinstance(schema_version, str) or not schema_version:
        raise RuntimeSchemaError(f"{label}: schema_version is required")
    schema_path = repo_root / "schemas" / f"{schema_version}.schema.json"
    if not schema_path.is_file():
        return 0
    return validate_against_schema(value, schema_path, label)
