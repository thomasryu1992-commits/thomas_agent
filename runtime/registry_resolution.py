from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from json import dumps
from pathlib import Path
from typing import Any, Mapping

import yaml


class RegistryResolutionError(RuntimeError):
    """Raised when Registry and Definition sources cannot be resolved safely."""


PROHIBITED_ROLE_FIELDS = {
    "capabilities",
    "capability_set_sha256",
    "permission_ceiling",
    "restrictions",
    "validation_default",
    "promotion_requirements",
    "selection_policy",
}

PROHIBITED_RESOURCE_FIELDS = {
    "required_permission_level",
    "purpose",
    "governance",
    "external_action",
    "deterministic",
}


def canonical_sha256(value: Any) -> str:
    payload = dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def raw_file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RegistryResolutionError(f"{name} must be a mapping")
    return value


def _require_registry_authority(registry: Mapping[str, Any], label: str) -> bool:
    if registry.get("authoritative") is not True:
        raise RegistryResolutionError(f"{label} must be the active authoritative index")
    return True


def _require_no_fields(
    record: Mapping[str, Any],
    prohibited: set[str],
    label: str,
) -> None:
    duplicated = sorted(set(record).intersection(prohibited))
    if duplicated:
        raise RegistryResolutionError(
            f"{label} contains Definition- or Governance-owned fields: {duplicated}"
        )


def _normalize_sha256_digest(value: str | None, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise RegistryResolutionError(f"{label} must be a non-empty SHA-256 value")
    digest = value.removeprefix("sha256:")
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest.lower()):
        raise RegistryResolutionError(f"{label} must contain a 64-character hexadecimal SHA-256 digest")
    return digest.lower()


def _require_expected_hash(expected_hash: str | None, target: str) -> str:
    """A registry entry with no ``definition_sha256`` is refused, not waved through.

    The old ``if expected_hash and ...`` guard meant deleting the field silently disabled
    verification for that entry — the exact "missing or uncertain" case the fail-closed
    rule says must BLOCK, reachable by one local edit to a YAML file. Every live entry
    carries a hash, so this only ever fires on a registry that stopped being verifiable.
    """
    if not (isinstance(expected_hash, str) and expected_hash.strip()):
        raise RegistryResolutionError(
            f"registry entry for {target} carries no definition_sha256; "
            "an unverifiable definition is refused (fail-closed)"
        )
    return expected_hash


def load_markdown_yaml_front_matter(
    *,
    path: Path,
    expected_hash: str | None,
) -> dict[str, Any]:
    if not path.is_file():
        raise RegistryResolutionError(f"definition path does not exist: {path}")
    expected_hash = _require_expected_hash(expected_hash, str(path))

    # Hash and parse the SAME bytes. Reading twice left a window in which the content that
    # was verified is not the content that gets parsed — small, but the hash check exists
    # precisely to rule that out.
    raw = path.read_bytes()
    actual_hash = sha256(raw).hexdigest()
    if actual_hash != expected_hash:
        raise RegistryResolutionError(
            f"definition hash mismatch for {path}: expected={expected_hash} actual={actual_hash}"
        )

    lines = raw.decode("utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        raise RegistryResolutionError(f"missing YAML front matter: {path}")
    try:
        closing = next(
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        )
    except StopIteration as exc:
        raise RegistryResolutionError(f"unterminated YAML front matter: {path}") from exc

    data = yaml.safe_load("\n".join(lines[1:closing]))
    return dict(_require_mapping(data, str(path)))


def load_resource_definitions(
    *,
    repo_root: Path,
    registry: Mapping[str, Any],
    collection_key: str,
) -> dict[str, dict[str, Any]]:
    definitions: dict[str, dict[str, Any]] = {}
    for raw in registry.get(collection_key, []):
        item = dict(_require_mapping(raw, f"{collection_key} entry"))
        definition_path = str(item.get("definition_path", ""))
        if not definition_path:
            raise RegistryResolutionError(
                f"{collection_key} entry is missing definition_path"
            )
        path = repo_root / definition_path
        if not path.is_file():
            raise RegistryResolutionError(
                f"definition path does not exist: {definition_path}"
            )
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        definitions[definition_path] = dict(
            _require_mapping(data, definition_path)
        )
    return definitions


def _load_structured_definition(
    *,
    repo_root: Path,
    definition_path: str,
    definitions: Mapping[str, Mapping[str, Any]],
    expected_hash: str | None,
) -> dict[str, Any]:
    path = repo_root / definition_path
    if not path.is_file():
        raise RegistryResolutionError(
            f"definition path does not exist: {definition_path}"
        )
    definition = definitions.get(definition_path)
    if definition is None:
        raise RegistryResolutionError(f"missing parsed definition: {definition_path}")
    expected_hash = _require_expected_hash(expected_hash, definition_path)
    result = deepcopy(dict(_require_mapping(definition, definition_path)))
    actual_hash = canonical_sha256(result)
    if actual_hash != expected_hash:
        raise RegistryResolutionError(
            f"definition hash mismatch for {definition_path}: expected={expected_hash} actual={actual_hash}"
        )
    return result


def resolve_role_registry(
    *,
    repo_root: Path,
    registry: Mapping[str, Any],
    governance_policy: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve Role Definition fields without creating a second authority source."""

    source = dict(_require_mapping(registry, "registry"))
    _require_registry_authority(source, "Role Registry")
    resolved_roles: list[dict[str, Any]] = []

    for entry in source.get("roles", []):
        item = dict(_require_mapping(entry, "role entry"))
        _require_no_fields(item, PROHIBITED_ROLE_FIELDS, str(item.get("role_id", "role")))
        definition_path = str(item["definition_path"])
        definition = load_markdown_yaml_front_matter(
            path=repo_root / definition_path,
            expected_hash=item.get("definition_sha256"),
        )
        identity_pairs = {
            "role_id": item.get("role_id"),
            "role_version": item.get("version"),
            "status": item.get("status"),
            "routable": item.get("routable"),
            "role_type": item.get("role_type"),
        }
        for key, expected in identity_pairs.items():
            if definition.get(key) != expected:
                raise RegistryResolutionError(
                    f"{item.get('role_id')}: Definition mismatch for {key}: "
                    f"expected={expected} actual={definition.get(key)}"
                )
        resolved_roles.append(
            {
                **item,
                "role_name": definition.get("role_name"),
                "capabilities": deepcopy(definition.get("capabilities", [])),
                "permission_ceiling": definition.get("permission_ceiling"),
                "validation_policy": deepcopy(definition.get("validation_policy", {})),
                "memory_policy": deepcopy(definition.get("memory_policy", {})),
                "_resolution": {
                    "authoritative": False,
                    "persistent": False,
                    "definition_source": definition_path,
                    "governance_policy_id": governance_policy.get("policy_id"),
                },
            }
        )

    return {
        "schema_version": "role_registry.resolved_view.v0.1",
        "roles": resolved_roles,
        "non_dynamic_roles": deepcopy(source.get("non_dynamic_roles", [])),
        "_resolution": {
            "authoritative": False,
            "persistent": False,
            "may_expand_authority": False,
            "source_registry_authoritative": True,
        },
    }


def resolve_role_registry_snapshot(
    *,
    registry: Mapping[str, Any],
    role_definition: Mapping[str, Any],
    definition_ref: str,
    definition_content_sha256: str,
) -> dict[str, Any]:
    """Resolve one hash-bound replay Role snapshot through the slim Registry shape.

    Development replay carries immutable Registry and Definition records inside the
    input bundle. This resolver verifies the same index-only boundary as the active
    Registry resolver without reading unbound live repository state.
    """

    source = dict(_require_mapping(registry, "registry snapshot"))
    _require_registry_authority(source, "Role Registry snapshot")
    if source.get("schema_version") != "role_registry.v0.3":
        raise RegistryResolutionError(
            "Role Registry snapshot must use schema_version role_registry.v0.3"
        )

    raw_entries = source.get("roles", [])
    if not isinstance(raw_entries, list) or len(raw_entries) != 1:
        raise RegistryResolutionError(
            "Role Registry snapshot must contain exactly one Task-bound Role entry"
        )

    item = dict(_require_mapping(raw_entries[0], "role snapshot entry"))
    _require_no_fields(item, PROHIBITED_ROLE_FIELDS, str(item.get("role_id", "role")))
    definition = deepcopy(dict(_require_mapping(role_definition, "role definition snapshot")))

    expected_hash = _normalize_sha256_digest(
        item.get("definition_sha256"),
        "Role Registry snapshot definition_sha256",
    )
    actual_hash = _normalize_sha256_digest(
        definition_content_sha256,
        "input bundle role_definition SHA-256",
    )
    if actual_hash != expected_hash:
        raise RegistryResolutionError(
            "Role Definition snapshot hash does not match the slim Registry entry: "
            f"expected={expected_hash} actual={actual_hash}"
        )

    identity_pairs = {
        "role_id": item.get("role_id"),
        "role_version": item.get("version"),
        "status": item.get("status"),
        "routable": item.get("routable"),
        "role_type": item.get("role_type"),
    }
    for key, expected in identity_pairs.items():
        if definition.get(key) != expected:
            raise RegistryResolutionError(
                f"{item.get('role_id')}: Definition snapshot mismatch for {key}: "
                f"expected={expected} actual={definition.get(key)}"
            )

    definition_path = str(item.get("definition_path", ""))
    if not definition_path:
        raise RegistryResolutionError("Role Registry snapshot entry is missing definition_path")
    if definition_path != definition_ref:
        raise RegistryResolutionError(
            "Role Registry snapshot definition_path does not match the exact input bundle ref: "
            f"expected={definition_ref} actual={definition_path}"
        )

    resolved_role = {
        **item,
        "role_name": definition.get("role_name"),
        "capabilities": deepcopy(definition.get("capabilities", [])),
        "permission_ceiling": definition.get("permission_ceiling"),
        "validation_policy": deepcopy(definition.get("validation_policy", {})),
        "memory_policy": deepcopy(definition.get("memory_policy", {})),
        "_resolution": {
            "authoritative": False,
            "persistent": False,
            "snapshot_bound": True,
            "definition_hash_verified": True,
            "definition_source": definition_path,
            "governance_policy_ref": source.get("governance_refs", {}).get(
                "canonical_governance_policy"
            ),
        },
    }
    return {
        "schema_version": "role_registry.resolved_view.v0.1",
        "roles": [resolved_role],
        "non_dynamic_roles": deepcopy(source.get("non_dynamic_roles", [])),
        "_resolution": {
            "authoritative": False,
            "persistent": False,
            "snapshot_bound": True,
            "may_expand_authority": False,
            "source_registry_authoritative": True,
        },
    }


def resolve_resource_registry(
    *,
    repo_root: Path,
    registry: Mapping[str, Any],
    definitions: Mapping[str, Mapping[str, Any]],
    governance_policy: Mapping[str, Any],
    collection_key: str,
    id_key: str,
) -> dict[str, Any]:
    """Resolve Tool or Program Definition fields at the consumer boundary."""

    source = dict(_require_mapping(registry, "registry"))
    _require_registry_authority(source, f"{collection_key} Registry")
    resolved: list[dict[str, Any]] = []

    for entry in source.get(collection_key, []):
        item = dict(_require_mapping(entry, f"{collection_key} entry"))
        _require_no_fields(
            item,
            PROHIBITED_RESOURCE_FIELDS,
            str(item.get(id_key, collection_key)),
        )
        definition_path = str(item["definition_path"])
        definition = _load_structured_definition(
            repo_root=repo_root,
            definition_path=definition_path,
            definitions=definitions,
            expected_hash=item.get("definition_sha256"),
        )
        for key, expected in {
            id_key: item.get(id_key),
            "version": item.get("version"),
            "status": item.get("status"),
        }.items():
            if definition.get(key) != expected:
                raise RegistryResolutionError(
                    f"{item.get(id_key)}: Definition mismatch for {key}: "
                    f"expected={expected} actual={definition.get(key)}"
                )
        runtime = definition.get("runtime", {})
        if runtime.get("implementation_available") != item.get(
            "runtime_implementation_available"
        ):
            raise RegistryResolutionError(
                f"{item.get(id_key)}: runtime implementation availability mismatch"
            )
        if runtime.get("enabled") != item.get("enabled"):
            raise RegistryResolutionError(f"{item.get(id_key)}: runtime enabled mismatch")

        effects = definition.get("effects", {})
        resolved_item = {
            **item,
            "purpose": definition.get("purpose"),
            "required_permission_level": definition.get("required_permission_level"),
            "external_action": effects.get("external_action", False),
            "_resolution": {
                "authoritative": False,
                "persistent": False,
                "definition_source": definition_path,
                "governance_policy_id": governance_policy.get("policy_id"),
            },
        }
        if "deterministic" in definition:
            resolved_item["deterministic"] = definition.get("deterministic")
        if "tool_class" in definition:
            if item.get("tool_class") != definition.get("tool_class"):
                raise RegistryResolutionError(
                    f"{item.get(id_key)}: tool_class mismatch between Registry and Definition"
                )
            resolved_item["tool_class"] = definition.get("tool_class")
        resolved.append(resolved_item)

    return {
        "schema_version": f"{collection_key}.resolved_view.v0.1",
        collection_key: resolved,
        "_resolution": {
            "authoritative": False,
            "persistent": False,
            "may_expand_authority": False,
            "source_registry_authoritative": True,
        },
    }
