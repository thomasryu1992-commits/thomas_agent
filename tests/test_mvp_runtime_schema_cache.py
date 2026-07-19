"""schema_cache tests — the cached validator that sits on EVERY record validation.

The cache trades ~900 schema-file reads per run for one build per process, but a caching
bug here (wrong schema served for a path, stale validators after a schema edit) would
silently weaken every closed-schema check in the runtime — so the properties under test
are correctness of selection, fail-closed behavior, and cache invalidation, not speed.
"""

from __future__ import annotations

import json
import os

import pytest

from runtime.mvp_runtime import schema_cache
from runtime.mvp_runtime.schema_cache import validate_against_schema
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError


def _write_schema(directory, name, schema):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps(schema), encoding="utf-8")
    return path


def _closed(properties, required):
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def test_valid_record_passes_and_invalid_fails(tmp_path):
    path = _write_schema(tmp_path / "schemas", "thing.v0.schema.json",
                         _closed({"name": {"type": "string"}}, ["name"]))
    validate_against_schema({"name": "ok"}, path, "thing")            # no raise
    with pytest.raises(RuntimeSchemaError) as exc:
        validate_against_schema({"name": 5}, path, "thing")
    assert "schema validation failed" in str(exc.value)
    with pytest.raises(RuntimeSchemaError):
        validate_against_schema({"name": "ok", "extra": 1}, path, "thing")  # closed schema


def test_missing_schema_fails_closed(tmp_path):
    with pytest.raises(RuntimeSchemaError) as exc:
        validate_against_schema({}, tmp_path / "schemas" / "nope.schema.json", "thing")
    assert "required schema is missing" in str(exc.value)


def test_unparseable_sibling_schema_fails_closed(tmp_path):
    """One broken file in the directory must fail the build, never silently skip — a
    silently missing schema is a validation that quietly stopped happening."""
    directory = tmp_path / "schemas"
    path = _write_schema(directory, "thing.v0.schema.json", _closed({}, []))
    (directory / "broken.schema.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(RuntimeSchemaError) as exc:
        validate_against_schema({}, path, "thing")
    assert "schema resolution failed" in str(exc.value)


def test_the_right_schema_is_served_for_each_path(tmp_path):
    """Validator selection is by filename within the directory: a value valid for schema A
    and invalid for schema B must fail exactly when validated against B."""
    directory = tmp_path / "schemas"
    a = _write_schema(directory, "a.v0.schema.json", _closed({"a": {"type": "string"}}, ["a"]))
    b = _write_schema(directory, "b.v0.schema.json", _closed({"b": {"type": "string"}}, ["b"]))
    validate_against_schema({"a": "x"}, a, "a")
    with pytest.raises(RuntimeSchemaError):
        validate_against_schema({"a": "x"}, b, "b")


def test_cross_schema_ref_resolves_within_the_directory(tmp_path):
    directory = tmp_path / "schemas"
    _write_schema(directory, "leaf.v0.schema.json", _closed({"n": {"type": "integer"}}, ["n"]))
    ref = _write_schema(directory, "root.v0.schema.json", {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"child": {"$ref": "leaf.v0.schema.json"}},
        "required": ["child"],
        "additionalProperties": False,
    })
    validate_against_schema({"child": {"n": 1}}, ref, "root")
    with pytest.raises(RuntimeSchemaError):
        validate_against_schema({"child": {"n": "not-an-int"}}, ref, "root")


def test_same_directory_signature_reuses_the_cached_generation(tmp_path):
    directory = tmp_path / "schemas"
    path = _write_schema(directory, "thing.v0.schema.json", _closed({}, []))
    validate_against_schema({}, path, "thing")
    first = schema_cache._cache[schema_cache._directory_signature(directory)]
    validate_against_schema({}, path, "thing")
    second = schema_cache._cache[schema_cache._directory_signature(directory)]
    assert first is second                              # no rebuild on an unchanged directory


@pytest.mark.parametrize("change", ["edit", "add", "remove"])
def test_any_directory_change_invalidates_the_cache(tmp_path, change):
    """The fail-closed property of the cache: after ANY change to the schemas directory a
    stale validator must never be used — validation behavior must reflect the current files."""
    directory = tmp_path / "schemas"
    path = _write_schema(directory, "thing.v0.schema.json", _closed({"name": {"type": "string"}}, []))
    other = _write_schema(directory, "other.v0.schema.json", _closed({}, []))
    validate_against_schema({"name": "ok"}, path, "thing")   # cache the lenient generation

    if change == "edit":
        # Tighten the schema (size changes, so the signature changes even on coarse mtime).
        _write_schema(directory, "thing.v0.schema.json",
                      _closed({"name": {"type": "string"}, "must": {"type": "integer"}},
                              ["name", "must"]))
        with pytest.raises(RuntimeSchemaError):
            validate_against_schema({"name": "ok"}, path, "thing")
    elif change == "add":
        _write_schema(directory, "new.v0.schema.json", _closed({}, []))
        validate_against_schema({"name": "ok"}, path, "thing")
        assert any("new.v0.schema.json" in sig_entry[0]
                   for sig_entry in schema_cache._directory_signature(directory)[1])
    else:
        os.remove(other)
        validate_against_schema({"name": "ok"}, path, "thing")
    # Exactly one live generation per process, keyed by the CURRENT signature.
    assert list(schema_cache._cache.keys()) == [schema_cache._directory_signature(directory)]


def test_matches_a_real_runtime_schema(tmp_path):
    """Smoke against the actual repo schemas dir: the cache must accept what the kernel
    accepts for a real closed schema (approval.v0.2 requires far more than this)."""
    from runtime.mvp_runtime.paths import repo_root

    schema_path = repo_root() / "schemas" / "approval.v0.2.schema.json"
    with pytest.raises(RuntimeSchemaError):
        validate_against_schema({"definitely": "not an approval"}, schema_path, "approval")
