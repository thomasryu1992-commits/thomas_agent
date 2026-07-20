"""Registry resolution must refuse an unverifiable definition, not skip verification.

`ROLE_REGISTRY.yaml` binds each role to its definition file by SHA-256, and both the
planner and the assignment builder advertise that they fail closed on a mismatch. They
did — but only when the registry actually carried a hash: `if expected_hash and ...`
silently disabled the check for an entry whose `definition_sha256` was removed, which is
the "missing or uncertain" case the fail-closed rule says must BLOCK.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.registry_resolution import (
    RegistryResolutionError,
    canonical_sha256,
    load_markdown_yaml_front_matter,
    raw_file_sha256,
)


def _definition(tmp_path: Path) -> Path:
    path = tmp_path / "ROLE.md"
    path.write_text(
        "---\nrole_id: general.specialist\nrole_version: 0.1.0\n---\n\nBody text.\n",
        encoding="utf-8",
    )
    return path


def test_matching_hash_resolves(tmp_path):
    path = _definition(tmp_path)
    data = load_markdown_yaml_front_matter(path=path, expected_hash=raw_file_sha256(path))
    assert data["role_id"] == "general.specialist"


def test_mismatched_hash_fails_closed(tmp_path):
    path = _definition(tmp_path)
    with pytest.raises(RegistryResolutionError) as exc:
        load_markdown_yaml_front_matter(path=path, expected_hash="0" * 64)
    assert "hash mismatch" in str(exc.value)


@pytest.mark.parametrize("missing", [None, "", "   "])
def test_absent_hash_is_refused_not_skipped(tmp_path, missing):
    """A registry entry whose definition_sha256 was deleted must not resolve unverified —
    one local YAML edit away from turning role-definition binding off entirely."""
    path = _definition(tmp_path)
    with pytest.raises(RegistryResolutionError) as exc:
        load_markdown_yaml_front_matter(path=path, expected_hash=missing)
    assert "no definition_sha256" in str(exc.value)


def test_the_verified_bytes_are_the_parsed_bytes(tmp_path):
    """Hash and parse read the file once. Reading twice left a window where the content
    that was verified is not the content that gets parsed — the exact thing the hash
    check exists to rule out."""
    path = _definition(tmp_path)
    expected = raw_file_sha256(path)
    reads: list[str] = []
    original = Path.read_bytes

    def counting_read_bytes(self):
        reads.append(str(self))
        return original(self)

    import pytest as _pytest  # local monkeypatch without the fixture

    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "read_bytes", counting_read_bytes)
        # read_text is not used for the definition any more; if it were, this would show
        # up as a second read of the same path.
        load_markdown_yaml_front_matter(path=path, expected_hash=expected)
    assert reads.count(str(path)) == 1


def test_structured_definition_absent_hash_is_refused(tmp_path):
    from runtime.registry_resolution import _load_structured_definition

    definition = {"tool_id": "search.readonly", "version": "0.1.0"}
    (tmp_path / "TOOL.yaml").write_text("tool_id: search.readonly\n", encoding="utf-8")
    definitions = {"TOOL.yaml": definition}

    # A correct hash resolves...
    resolved = _load_structured_definition(
        repo_root=tmp_path, definition_path="TOOL.yaml",
        definitions=definitions, expected_hash=canonical_sha256(definition),
    )
    assert resolved["tool_id"] == "search.readonly"

    # ...and an absent one is refused rather than waved through.
    with pytest.raises(RegistryResolutionError) as exc:
        _load_structured_definition(
            repo_root=tmp_path, definition_path="TOOL.yaml",
            definitions=definitions, expected_hash=None,
        )
    assert "no definition_sha256" in str(exc.value)
