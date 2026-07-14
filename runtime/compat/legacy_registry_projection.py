from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from json import dumps
from pathlib import Path
from typing import Any, Mapping


class ProjectionError(RuntimeError):
    """Raised when a legacy registry projection cannot be built safely."""


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
}


def canonical_sha256(value: Any) -> str:
    payload = dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProjectionError(f"{name} must be a mapping")
    return value


def _require_no_fields(record: Mapping[str, Any], prohibited: set[str], label: str) -> None:
    duplicated = sorted(set(record).intersection(prohibited))
    if duplicated:
        raise ProjectionError(
            f"{label} contains duplicated authoritative fields: {duplicated}"
        )


def _load_definition(
    *,
    repo_root: Path,
    definition_path: str,
    definitions: Mapping[str, Mapping[str, Any]],
    expected_hash: str | None,
) -> dict[str, Any]:
    definition = definitions.get(definition_path)
    if definition is None:
        raise ProjectionError(f"missing definition: {definition_path}")

    result = deepcopy(dict(_require_mapping(definition, definition_path)))
    actual_hash = canonical_sha256(result)

    if expected_hash and actual_hash != expected_hash:
        raise ProjectionError(
            f"definition hash mismatch for {definition_path}: "
            f"expected={expected_hash} actual={actual_hash}"
        )

    path = repo_root / definition_path
    if not path.exists():
        raise ProjectionError(f"definition path does not exist: {definition_path}")

    return result


def project_role_registry(
    *,
    repo_root: Path,
    slim_registry: Mapping[str, Any],
    role_definitions: Mapping[str, Mapping[str, Any]],
    governance_policy: Mapping[str, Any],
) -> dict[str, Any]:
    """Build an in-memory legacy role registry projection.

    The returned structure is non-authoritative and must never be persisted as
    a new source of truth.
    """
    registry = dict(_require_mapping(slim_registry, "slim_registry"))
    if registry.get("source_of_truth_for") is None:
        raise ProjectionError("role registry ownership declaration is missing")

    projected_roles: list[dict[str, Any]] = []
    for entry in registry.get("roles", []):
        item = dict(_require_mapping(entry, "role entry"))
        _require_no_fields(item, PROHIBITED_ROLE_FIELDS, item.get("role_id", "role"))

        definition = _load_definition(
            repo_root=repo_root,
            definition_path=str(item["definition_path"]),
            definitions=role_definitions,
            expected_hash=item.get("definition_sha256"),
        )

        projected = {
            **item,
            "capabilities": deepcopy(definition.get("capabilities", [])),
            "permission_ceiling": definition.get("permission_ceiling"),
            "restrictions": deepcopy(definition.get("restrictions", {})),
            "validation_default": definition.get("validation_default"),
            "_projection": {
                "authoritative": False,
                "generated_in_memory": True,
                "definition_source": item["definition_path"],
                "governance_policy_id": governance_policy.get("policy_id"),
            },
        }
        projected_roles.append(projected)

    return {
        "schema_version": "role_registry.legacy_projection.v0.1",
        "status": registry.get("status"),
        "owner": registry.get("owner"),
        "active_roles": [
            role for role in projected_roles if role.get("status") == "active"
        ],
        "candidate_roles": [
            role for role in projected_roles if role.get("status") == "candidate"
        ],
        "non_dynamic_roles": deepcopy(registry.get("non_dynamic_roles", [])),
        "_projection": {
            "authoritative": False,
            "persistent": False,
            "may_expand_authority": False,
        },
    }


def project_resource_registry(
    *,
    repo_root: Path,
    slim_registry: Mapping[str, Any],
    definitions: Mapping[str, Mapping[str, Any]],
    governance_policy: Mapping[str, Any],
    collection_key: str,
    id_key: str,
) -> dict[str, Any]:
    registry = dict(_require_mapping(slim_registry, "slim_registry"))
    projected: list[dict[str, Any]] = []

    for entry in registry.get(collection_key, []):
        item = dict(_require_mapping(entry, f"{collection_key} entry"))
        _require_no_fields(
            item,
            PROHIBITED_RESOURCE_FIELDS,
            str(item.get(id_key, collection_key)),
        )

        definition = _load_definition(
            repo_root=repo_root,
            definition_path=str(item["definition_path"]),
            definitions=definitions,
            expected_hash=item.get("definition_sha256"),
        )

        projected.append(
            {
                **item,
                "purpose": definition.get("purpose"),
                "required_permission_level": definition.get(
                    "required_permission_level"
                ),
                "_projection": {
                    "authoritative": False,
                    "generated_in_memory": True,
                    "definition_source": item["definition_path"],
                    "governance_policy_id": governance_policy.get("policy_id"),
                },
            }
        )

    return {
        "schema_version": f"{collection_key}.legacy_projection.v0.1",
        "status": registry.get("status"),
        "owner": registry.get("owner"),
        collection_key: projected,
        "_projection": {
            "authoritative": False,
            "persistent": False,
            "may_expand_authority": False,
        },
    }
