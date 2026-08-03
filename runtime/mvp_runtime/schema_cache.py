"""Process-lifetime cached schema validation for the live runtime.

The kernel's ``validate_against_schema`` rebuilds the referencing registry by re-reading
every ``schemas/*.schema.json`` on EVERY call — on the order of 900 schema-file reads and
parses per single MVP run (intake, planning, 2-4 permission decisions, assignments, worker,
validations, and 6-10 audit events each revalidate). The kernel is frozen, so the caching
lives here: compiled validators are cached per directory *signature* (resolved path + every
schema file's name, size, and mtime), which preserves fail-closed semantics —

- a missing or unparseable schema still raises :class:`RuntimeSchemaError`;
- ANY change to the schemas directory (edit, add, remove) changes the signature and misses
  the cache, so a stale validator can never be used;
- validation behavior is byte-identical to the kernel's (same Draft 2020-12 validator, same
  registry construction, same error rendering).

The schemas directory is committed source and immutable at runtime, so in practice one
build per process serves every validation.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

# One generation per directory signature; a signature miss rebuilds and replaces it.
_cache: dict[tuple[Any, ...], dict[str, Draft202012Validator]] = {}


def _directory_signature(directory: Path) -> tuple[Any, ...]:
    """The cache key: the resolved directory plus every schema file's name, size and mtime.

    Runs on EVERY validation — it is the check that decides whether the cached generation may
    be reused — so it is the one thing in this module that must not cost more than it saves.
    A run validates 15-20 records against ~75 schemas.

    ``os.scandir`` rather than ``Path.glob`` + ``Path.stat``. The result is byte-identical;
    what changes is that one directory read serves the whole sweep and no ``Path`` object or
    ``fnmatch`` call is built per entry. Measured on the live host, 75 schemas: **0.411 ms ->
    0.213 ms per call**, so ~7.4 ms -> ~3.8 ms per run. (The previous note here recorded the
    same lesson one step earlier: ``path.stat()`` caches nothing, so reading size and mtime
    from two calls had been doubling this. Same shape, one layer out.)

    **The remaining ~75 stat syscalls are the floor, not an oversight.** The property this
    module promises is that ANY change to the directory — edit, add, remove — misses the cache,
    which is what makes a stale validator impossible and is pinned by
    ``test_any_directory_change_invalidates_the_cache``. A coarser check is available and was
    rejected: the *directory's* own mtime is one syscall, but it does not move when a file is
    edited in place, so it would trade the guarantee for the last 3.8 ms. Detecting an in-place
    edit means asking every file, and asking every file is 75 syscalls.
    """
    entries: list[tuple[str, int, int]] = []
    with os.scandir(directory) as it:
        for entry in it:
            if entry.name.endswith(".schema.json"):
                info = entry.stat()
                entries.append((entry.name, info.st_size, info.st_mtime_ns))
    return (str(directory.resolve()), tuple(sorted(entries)))


def _validators_for(directory: Path) -> dict[str, Draft202012Validator]:
    signature = _directory_signature(directory)
    cached = _cache.get(signature)
    if cached is not None:
        return cached
    registry = Registry()
    schemas: dict[str, Any] = {}
    for candidate in sorted(directory.glob("*.schema.json")):
        contents = json.loads(candidate.read_text(encoding="utf-8"))
        schemas[candidate.name] = contents
        resource = Resource.from_contents(contents)
        registry = registry.with_resource(candidate.name, resource)
        schema_id = contents.get("$id")
        if isinstance(schema_id, str) and schema_id:
            registry = registry.with_resource(schema_id, resource)
    validators = {
        name: Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())
        for name, schema in schemas.items()
    }
    _cache.clear()  # keep exactly one live generation per process
    _cache[signature] = validators
    return validators


def validate_against_schema(value: Any, schema_path: Path, label: str) -> int:
    """Drop-in replacement for the kernel's function, minus the per-call directory re-read.

    Returns the number of schemas in the (possibly cached) registry. No caller reads it —
    it is kept because *drop-in* is the point: the kernel's ``validate_against_schema``
    returns a read count, and a substitute that changed the signature would stop being
    substitutable for it, which is the property that lets this exist outside the frozen
    kernel at all.
    """
    schema_path = Path(schema_path)
    if not schema_path.is_file():
        raise RuntimeSchemaError(f"{label}: required schema is missing: {schema_path.as_posix()}")
    try:
        validators = _validators_for(schema_path.parent)
        validator = validators.get(schema_path.name)
        if validator is None:
            raise RuntimeSchemaError(f"{label}: schema could not be loaded: {schema_path.as_posix()}")
        errors = sorted(validator.iter_errors(value), key=lambda item: list(item.absolute_path))
    except RuntimeSchemaError:
        raise
    except Exception as exc:
        raise RuntimeSchemaError(f"{label}: schema resolution failed: {exc}") from exc
    if errors:
        rendered = "; ".join(
            f"{'.'.join(str(part) for part in item.absolute_path) or '$'}: {item.message}"
            for item in errors[:5]
        )
        raise RuntimeSchemaError(f"{label}: schema validation failed: {rendered}")
    return len(validators)
