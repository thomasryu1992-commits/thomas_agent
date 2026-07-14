from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from json import dumps
from pathlib import Path
from typing import Any, Mapping

import yaml


class ProjectionError(RuntimeError):
    """Raised when a legacy Registry projection cannot be built safely."""


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


def raw_file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProjectionError(f"{name} must be a mapping")
    return value


def _require_no_fields(
    record: Mapping[str, Any],
    prohibited: set[str],
    label: str,
) -> None:
    duplicated = sorted(set(record).intersection(prohibited))
    if duplicated:
        raise ProjectionError(
            f"{label} contains duplicated authoritative fields: {duplicated}"
        )


def load_markdown_yaml_front_matter(
    *,
    path: Path,
    expected_hash: str | None,
) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        raise ProjectionError(f"definition path does not exist: {path}")

    actual_hash = raw_file_sha256(path)
    if expected_hash and actual_hash != expected_hash:
        raise ProjectionError(
            f"definition hash mismatch for {path}: "
            f"expected={expected_hash} actual={actual_hash}"
        )

    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        raise ProjectionError(f"missing YAML front matter: {path}")

    try:
        closing = next(
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        )
    except StopIteration as exc:
        raise ProjectionError(f"unterminated YAML front matter: {path}") from exc

    data = yaml.safe_load("\n".join(lines[1:closing]))
    return dict(_require_mapping(data, str(path)))


def _load_structured_definition(
    *,
    repo_root: Path,
    definition_path: str,
    definitions: Mapping[str, Mapping[str, Any]],
    expected_hash: str | None,
) -> dict[str, Any]:
    path = repo_root / definition_path
    if not path.exists():
        raise ProjectionError(f"definition path does not exist: {definition_path}")

    definition = definitions.get(definition_path)
    if definition is None:
        raise ProjectionError(f"missing parsed definition: {definition_path}")

    result = deepcopy(dict(_require_mapping(definition, definition_path)))
    actual_hash = canonical_sha256(result)
    if expected_hash and actual_hash != expected_hash:
        raise ProjectionError(
            f"definition hash mismatch for {definition_path}: "
            f"expected={expected_hash} actual={actual_hash}"
        )
    return result


def project_role_registry(
    *,
    repo_root: Path,
    slim_registry: Mapping[str, Any],
    governance_policy: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a non-authoritative legacy Role Registry view from Markdown front matter."""
    registry = dict(_require_mapping(slim_registry, "slim_registry"))
    if registry.get("authoritative") is not False:
        raise ProjectionError("migration Role Registry must be non-authoritative")

    projected_roles: list[dict[str, Any]] = []
    for entry in registry.get("roles", []):
        item = dict(_require_mapping(entry, "role entry"))
        _require_no_fields(
            item,
            PROHIBITED_ROLE_FIELDS,
            str(item.get("role_id", "role")),
        )

        definition = load_markdown_yaml_front_matter(
            path=repo_root / str(item["definition_path"]),
            expected_hash=item.get("definition_sha256"),
        )

        if definition.get("role_id") != item.get("role_id"):
            raise ProjectionError(f"role_id mismatch: {item.get('role_id')}")
        if definition.get("role_version") != item.get("version"):
            raise ProjectionError(f"role_version mismatch: {item.get('role_id')}")

        projected_roles.append(
            {
                **item,
                "capabilities": deepcopy(definition.get("capabilities", [])),
                "permission_ceiling": definition.get("permission_ceiling"),
                "restrictions": {
                    "unsupported_capabilities": deepcopy(
                        definition.get("unsupported_capabilities", [])
                    ),
                    "external_action_allowed": definition.get(
                        "external_action_allowed",
                        False,
                    ),
                },
                "validation_default": (
                    definition.get("validation_policy", {}).get("default_mode")
                ),
                "_projection": {
                    "authoritative": False,
                    "generated_in_memory": True,
                    "definition_source": item["definition_path"],
                    "definition_format": "markdown_yaml_front_matter",
                    "governance_policy_id": governance_policy.get("policy_id"),
                },
            }
        )

    return {
        "schema_version": "role_registry.legacy_projection.v0.2",
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
    if registry.get("authoritative") is not False:
        raise ProjectionError("migration Resource Registry must be non-authoritative")

    projected: list[dict[str, Any]] = []
    for entry in registry.get(collection_key, []):
        item = dict(_require_mapping(entry, f"{collection_key} entry"))
        _require_no_fields(
            item,
            PROHIBITED_RESOURCE_FIELDS,
            str(item.get(id_key, collection_key)),
        )

        definition = _load_structured_definition(
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
        "schema_version": f"{collection_key}.legacy_projection.v0.2",
        "status": registry.get("status"),
        "owner": registry.get("owner"),
        collection_key: projected,
        "_projection": {
            "authoritative": False,
            "persistent": False,
            "may_expand_authority": False,
        },
    }
