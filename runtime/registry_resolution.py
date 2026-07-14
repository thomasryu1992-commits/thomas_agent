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


def load_markdown_yaml_front_matter(
    *,
    path: Path,
    expected_hash: str | None,
) -> dict[str, Any]:
    if not path.is_file():
        raise RegistryResolutionError(f"definition path does not exist: {path}")

    actual_hash = raw_file_sha256(path)
    if expected_hash and actual_hash != expected_hash:
        raise RegistryResolutionError(
            f"definition hash mismatch for {path}: expected={expected_hash} actual={actual_hash}"
        )

    lines = path.read_text(encoding="utf-8").splitlines()
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
    result = deepcopy(dict(_require_mapping(definition, definition_path)))
    actual_hash = canonical_sha256(result)
    if expected_hash and actual_hash != expected_hash:
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
