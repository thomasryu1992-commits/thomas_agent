from __future__ import annotations

import ast
import configparser
import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from jsonschema import Draft202012Validator, FormatChecker

REGISTRY_REL = "05_REGISTRIES/I0_5_READ_ONLY_RUNTIME_COMPONENTS_REVIEW_ONLY.yaml"
WORKFLOW_REL = ".github/workflows/thomas-agent-runtime-validation.yml"
GATE_EVIDENCE_REL = "generated/release_gate/RELEASE_GATE_EVIDENCE.yaml"
I0_4_LOCK_REL = "generated/legacy/i0_4_consolidation/I0_4_CONTRACT_SET_LOCK.yaml"
REVIEW_CORE_REL = "THOMAS_CORE/REVIEW_CORE_RELEASE.yaml"
CURRENT_CORE_REL = "THOMAS_CORE/CURRENT_CORE_RELEASE.yaml"
TOOL_REGISTRY_REL = "05_REGISTRIES/TOOL_REGISTRY.yaml"
PROGRAM_REGISTRY_REL = "05_REGISTRIES/PROGRAM_REGISTRY.yaml"
DEFAULT_CI_EVIDENCE_REL = "generated/deferred/runtime_entry/i0_5_1_runtime_promotion/GITHUB_CI_EVIDENCE.yaml"

REQUIRED_GATE_CHECKS = {
    "I0.4 Consolidated Contract Set": "i0.4_consolidated_contract_set",
    "I0.5 Read-only Runtime Kernel": "i0.5_read-only_runtime_kernel",
    "I0.5.1 Runtime Promotion Readiness": "i0.5.1_runtime_promotion_readiness",
    "Contract Schema Parity": "contract_schema_parity",
    "Security Hardening": "security_hardening",
    "Core Release Reproducibility": "core_release_reproducibility",
}

EXPECTED_WORKFLOW_NAME = "Thomas Agent Runtime Validation"
EXPECTED_JOB_NAMES = {
    "ubuntu": "Full validation (ubuntu-latest)",
    "windows": "Full validation (windows-latest)",
}


class ReadinessError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReadinessError(f"{path.as_posix()}: expected YAML object")
    return value


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        deepcopy(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_value(value: Any) -> str:
    return sha256_bytes(canonical_bytes(value))


def short_id(prefix: str, value: Any, length: int = 20) -> str:
    return f"{prefix}_{hashlib.sha256(canonical_bytes(value)).hexdigest()[:length]}"


def safe_relative_path(root: Path, rel: str, *, must_exist: bool = False) -> Path:
    if not isinstance(rel, str) or not rel.strip():
        raise ReadinessError("relative path must be a non-empty string")
    ref = Path(rel)
    if ref.is_absolute() or any(part in {"", ".."} for part in ref.parts):
        raise ReadinessError(f"unsafe relative path: {rel}")
    root = root.resolve(strict=True)
    candidate = (root / ref).resolve(strict=must_exist)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ReadinessError(f"path escapes repository: {rel}") from exc
    return candidate


def extract_string_constant(path: Path, constant_name: str) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
    matches: list[str] = []
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        names: list[str] = []
        value_node: ast.expr | None = None
        if isinstance(node, ast.Assign):
            value_node = node.value
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.append(target.id)
        else:
            value_node = node.value
            if isinstance(node.target, ast.Name):
                names.append(node.target.id)
        if constant_name not in names or value_node is None:
            continue
        if not isinstance(value_node, ast.Constant) or not isinstance(value_node.value, str):
            raise ReadinessError(f"{path}: {constant_name} must be a literal string")
        matches.append(value_node.value)
    if not matches:
        imported_from: list[Path] = []
        for node in tree.body:
            if not isinstance(node, ast.ImportFrom) or node.level != 1 or not node.module:
                continue
            if any(
                alias.name == constant_name
                and (alias.asname is None or alias.asname == constant_name)
                for alias in node.names
            ):
                imported_from.append(path.parent / f"{node.module}.py")
        if len(imported_from) == 1 and imported_from[0].is_file():
            return extract_string_constant(imported_from[0], constant_name)
    if len(matches) != 1:
        raise ReadinessError(f"{path}: expected exactly one literal {constant_name}, found {len(matches)}")
    return matches[0]


def _component_boundary_match(item: dict[str, Any]) -> bool:
    component_id = item.get("component_id")
    common = item.get("status") == "candidate" and item.get("runtime_authoritative") is False
    if component_id == "thomas.read_only_runtime_kernel":
        return (
            common
            and item.get("component_type") == "orchestration_kernel"
            and item.get("run_mode") == ["DEVELOPMENT_REPLAY"]
            and all(
                item.get(name) is False
                for name in [
                    "network_allowed",
                    "filesystem_write_allowed",
                    "model_invocation_allowed",
                    "tool_execution_allowed",
                    "program_execution_allowed",
                    "external_action_allowed",
                    "runtime_mutation_allowed",
                ]
            )
        )
    if component_id == "kernel.contract_inspection.readonly":
        return (
            common
            and item.get("component_type") == "deterministic_in_process_worker"
            and all(
                item.get(name) == 0
                for name in [
                    "model_calls",
                    "tool_calls",
                    "program_calls",
                    "network_calls",
                    "filesystem_writes",
                    "external_actions",
                ]
            )
        )
    return False


def _registry_boundary_match(registry: dict[str, Any]) -> bool:
    effects = registry.get("review_only_effects")
    components = registry.get("components")
    return (
        registry.get("status") == "CANDIDATE_DEVELOPMENT_REPLAY_ONLY"
        and registry.get("runtime_source_of_truth") is False
        and registry.get("runtime_authoritative_mode_enabled") is False
        and isinstance(effects, dict)
        and bool(effects)
        and all(value is False for value in effects.values())
        and isinstance(components, list)
        and len(components) == 2
        and [item.get("component_id") for item in components]
        == ["thomas.read_only_runtime_kernel", "kernel.contract_inspection.readonly"]
        and all(isinstance(item, dict) and _component_boundary_match(item) for item in components)
    )


def build_component_attestation(repo_root: Path, *, created_at: str | None = None) -> dict[str, Any]:
    root = repo_root.resolve(strict=True)
    created_at = created_at or utc_now()
    registry_path = root / REGISTRY_REL
    registry = load_yaml(registry_path)
    registry_boundary_match = _registry_boundary_match(registry)
    components: list[dict[str, Any]] = []

    for item in registry.get("components", []):
        if not isinstance(item, dict):
            raise ReadinessError("component registry entries must be objects")
        implementation_ref = item.get("implementation_ref")
        identity = item.get("implementation_identity")
        if not isinstance(implementation_ref, str) or not isinstance(identity, dict):
            raise ReadinessError("component requires implementation_ref and implementation_identity")
        implementation_path = safe_relative_path(root, implementation_ref, must_exist=True)
        if not implementation_path.is_file():
            raise ReadinessError(f"implementation file missing: {implementation_ref}")

        id_constant = identity.get("id_constant")
        version_constant = identity.get("version_constant")
        if not isinstance(id_constant, str) or not isinstance(version_constant, str):
            raise ReadinessError("implementation_identity constants must be strings")
        implementation_id = extract_string_constant(implementation_path, id_constant)
        implementation_version = extract_string_constant(implementation_path, version_constant)
        id_match = implementation_id == item.get("component_id")
        version_match = implementation_version == item.get("version")
        boundary_match = _component_boundary_match(item)
        result = "PASS" if id_match and version_match and boundary_match else "BLOCK"
        components.append(
            {
                "component_id": item.get("component_id"),
                "registry_version": item.get("version"),
                "implementation_ref": implementation_ref,
                "implementation_sha256": sha256_file(implementation_path),
                "implementation_id_constant": id_constant,
                "implementation_version_constant": version_constant,
                "implementation_id": implementation_id,
                "implementation_version": implementation_version,
                "id_match": id_match,
                "version_match": version_match,
                "boundary_match": boundary_match,
                "implementation_exists": True,
                "result": result,
            }
        )

    pass_count = sum(item["result"] == "PASS" for item in components)
    block_count = len(components) - pass_count
    seed = {
        "registry_sha256": sha256_file(registry_path),
        "components": components,
        "created_at": created_at,
    }
    attestation_id = short_id("rcatt", seed)
    payload = {
        "schema_version": "runtime_component_attestation_fingerprint_payload.v0.1",
        "attestation_id": attestation_id,
        "registry_sha256": seed["registry_sha256"],
        "components": components,
        "created_at": created_at,
    }
    return {
        "schema_version": "runtime_component_attestation.v0.1",
        "attestation_id": attestation_id,
        "phase": "I0.5.1",
        "registry": {
            "ref": REGISTRY_REL,
            "sha256": seed["registry_sha256"],
            "schema_version": registry.get("schema_version"),
            "document_version": registry.get("document_version"),
        },
        "components": components,
        "summary": {
            "result": "PASS" if registry_boundary_match and block_count == 0 and len(components) == 2 else "BLOCK",
            "registry_boundary_match": registry_boundary_match,
            "component_count": len(components),
            "pass_count": pass_count,
            "block_count": block_count,
            "runtime_authoritative": False,
        },
        "runtime_effect": {
            "grants_runtime_permission": False,
            "grants_runtime_activation": False,
            "changes_registry": False,
            "changes_implementation": False,
            "executes_component": False,
        },
        "integrity": {
            "hash_schema": "runtime_component_attestation_fingerprint_payload.v0.1",
            "attestation_fingerprint_payload": payload,
            "attestation_sha256": sha256_value(payload),
        },
        "created_at": created_at,
    }


def _git_directory(root: Path) -> Path:
    marker = root / ".git"
    if marker.is_dir():
        return marker.resolve()
    if marker.is_file():
        text = marker.read_text(encoding="utf-8").strip()
        if not text.startswith("gitdir:"):
            raise ReadinessError(".git file has invalid format")
        value = text.split(":", 1)[1].strip()
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = (root / candidate).resolve()
        return candidate
    raise ReadinessError("repository has no .git metadata")


def _git_common_directory(git_dir: Path) -> Path:
    common = git_dir / "commondir"
    if common.is_file():
        value = common.read_text(encoding="utf-8").strip()
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = (git_dir / candidate).resolve()
        return candidate
    return git_dir


def git_head_sha(root: Path) -> str:
    git_dir = _git_directory(root)
    common_dir = _git_common_directory(git_dir)
    head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    if re.fullmatch(r"[0-9a-f]{40}", head):
        return head
    if not head.startswith("ref: "):
        raise ReadinessError("Git HEAD has invalid format")
    ref = head[5:].strip()
    for base in [git_dir, common_dir]:
        ref_path = base / ref
        if ref_path.is_file():
            value = ref_path.read_text(encoding="utf-8").strip()
            if re.fullmatch(r"[0-9a-f]{40}", value):
                return value
        packed = base / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line.startswith("#") or line.startswith("^") or not line.strip():
                    continue
                sha, name = line.split(" ", 1)
                if name == ref and re.fullmatch(r"[0-9a-f]{40}", sha):
                    return sha
    raise ReadinessError(f"Git ref cannot be resolved: {ref}")


def _normalize_github_repo_url(url: str) -> str | None:
    url = url.strip()
    if url.startswith("git@github.com:"):
        value = url[len("git@github.com:") :]
    elif url.startswith("ssh://git@github.com/"):
        value = url[len("ssh://git@github.com/") :]
    else:
        parsed = urlparse(url)
        if parsed.hostname not in {"github.com", "www.github.com"}:
            return None
        value = parsed.path.lstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        return value
    return None


def git_origin_repository(root: Path) -> str:
    git_dir = _git_directory(root)
    config_path = git_dir / "config"
    if not config_path.is_file():
        config_path = _git_common_directory(git_dir) / "config"
    parser = configparser.ConfigParser()
    parser.read(config_path, encoding="utf-8")
    section = 'remote "origin"'
    if not parser.has_option(section, "url"):
        raise ReadinessError("Git origin URL is missing")
    repo = _normalize_github_repo_url(parser.get(section, "url"))
    if not repo:
        raise ReadinessError("Git origin is not a supported github.com repository URL")
    return repo


def build_github_ci_evidence(
    *,
    repository_full_name: str,
    workflow_name: str,
    workflow_path: str,
    workflow_sha256: str,
    run_id: int,
    run_attempt: int,
    event: str,
    head_sha: str,
    html_url: str,
    created_at: str,
    completed_at: str,
    ubuntu_job_id: int,
    ubuntu_job_name: str,
    ubuntu_completed_at: str,
    windows_job_id: int,
    windows_job_name: str,
    windows_completed_at: str,
    collected_at: str,
) -> dict[str, Any]:
    payload = {
        "schema_version": "github_ci_evidence_fingerprint_payload.v0.1",
        "repository_full_name": repository_full_name,
        "workflow": {
            "name": workflow_name,
            "path": workflow_path,
            "sha256": workflow_sha256,
        },
        "run": {
            "run_id": run_id,
            "run_attempt": run_attempt,
            "event": event,
            "head_sha": head_sha,
            "status": "completed",
            "conclusion": "success",
            "html_url": html_url,
            "created_at": created_at,
            "completed_at": completed_at,
        },
        "jobs": {
            "ubuntu": {
                "job_id": ubuntu_job_id,
                "name": ubuntu_job_name,
                "runner_os": "ubuntu-latest",
                "status": "completed",
                "conclusion": "success",
                "completed_at": ubuntu_completed_at,
            },
            "windows": {
                "job_id": windows_job_id,
                "name": windows_job_name,
                "runner_os": "windows-latest",
                "status": "completed",
                "conclusion": "success",
                "completed_at": windows_completed_at,
            },
        },
        "source": {
            "source_type": "GITHUB_API_VIA_GH",
            "collected_by": "scripts/collect_github_ci_evidence.py",
            "live_api_verified": True,
            "collected_at": collected_at,
        },
    }
    evidence_id = short_id("gcie", payload)
    return {
        "schema_version": "github_ci_evidence.v0.1",
        "evidence_id": evidence_id,
        **{key: deepcopy(value) for key, value in payload.items() if key != "schema_version"},
        "integrity": {
            "hash_schema": "github_ci_evidence_fingerprint_payload.v0.1",
            "evidence_fingerprint_payload": payload,
            "evidence_sha256": sha256_value(payload),
        },
    }


def validate_github_ci_evidence_semantics(record: dict[str, Any]) -> None:
    if record.get("schema_version") != "github_ci_evidence.v0.1":
        raise ReadinessError("GitHub CI evidence schema_version must be github_ci_evidence.v0.1")
    integrity = record.get("integrity", {})
    payload = integrity.get("evidence_fingerprint_payload")
    if integrity.get("hash_schema") != "github_ci_evidence_fingerprint_payload.v0.1":
        raise ReadinessError("GitHub CI evidence hash_schema mismatch")
    if not isinstance(payload, dict):
        raise ReadinessError("GitHub CI evidence fingerprint payload is missing")
    expected_payload = {
        "schema_version": "github_ci_evidence_fingerprint_payload.v0.1",
        "repository_full_name": record.get("repository_full_name"),
        "workflow": record.get("workflow"),
        "run": record.get("run"),
        "jobs": record.get("jobs"),
        "source": record.get("source"),
    }
    if payload != expected_payload:
        raise ReadinessError("GitHub CI evidence fingerprint payload does not match record fields")
    if integrity.get("evidence_sha256") != sha256_value(payload):
        raise ReadinessError("GitHub CI evidence fingerprint mismatch")
    if record.get("evidence_id") != short_id("gcie", payload):
        raise ReadinessError("GitHub CI evidence ID mismatch")

    workflow = record.get("workflow", {})
    run = record.get("run", {})
    jobs = record.get("jobs", {})
    source = record.get("source", {})
    if workflow.get("name") != EXPECTED_WORKFLOW_NAME or workflow.get("path") != WORKFLOW_REL:
        raise ReadinessError("GitHub CI evidence references an unexpected workflow")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        raise ReadinessError("GitHub Actions run must be completed with success")
    if run.get("event") not in {"pull_request", "push"}:
        raise ReadinessError("GitHub Actions event must be pull_request or push")
    if not re.fullmatch(r"[0-9a-f]{40}", str(run.get("head_sha", ""))):
        raise ReadinessError("GitHub Actions head_sha is invalid")
    if source.get("source_type") != "GITHUB_API_VIA_GH" or source.get("live_api_verified") is not True:
        raise ReadinessError("GitHub CI evidence must come from a live GitHub API collection")
    if source.get("collected_by") != "scripts/collect_github_ci_evidence.py":
        raise ReadinessError("GitHub CI evidence collector identity mismatch")

    seen_job_ids: set[int] = set()
    for key, expected_name in EXPECTED_JOB_NAMES.items():
        item = jobs.get(key)
        if not isinstance(item, dict):
            raise ReadinessError(f"GitHub CI evidence is missing {key} job")
        if item.get("name") != expected_name or item.get("runner_os") != f"{key}-latest":
            raise ReadinessError(f"GitHub CI {key} job identity mismatch")
        if item.get("status") != "completed" or item.get("conclusion") != "success":
            raise ReadinessError(f"GitHub CI {key} job must be completed with success")
        job_id = item.get("job_id")
        if not isinstance(job_id, int) or job_id <= 0 or job_id in seen_job_ids:
            raise ReadinessError("GitHub CI job IDs must be positive and unique")
        seen_job_ids.add(job_id)


def verify_github_ci_evidence(root: Path, evidence_rel: str | None) -> dict[str, Any]:
    result = {
        "present": False,
        "verified": False,
        "head_matches": False,
        "workflow_matches": False,
        "repository_matches": False,
        "ubuntu_pass": False,
        "windows_pass": False,
        "evidence_ref": evidence_rel,
        "evidence_sha256": None,
        "reasons": [],
    }
    if not evidence_rel:
        result["reasons"].append("GITHUB_CI_EVIDENCE_MISSING")
        return result
    try:
        path = safe_relative_path(root, evidence_rel, must_exist=True)
    except Exception as exc:
        result["reasons"].append(f"GITHUB_CI_EVIDENCE_PATH_INVALID:{exc}")
        return result
    if not path.is_file():
        result["reasons"].append("GITHUB_CI_EVIDENCE_MISSING")
        return result
    result["present"] = True
    result["evidence_sha256"] = sha256_file(path)
    try:
        record = load_yaml(path)
        _validate_yaml_against_schema(root, path, "schemas/github_ci_evidence.v0.1.schema.json", "GitHub CI evidence")
        validate_github_ci_evidence_semantics(record)
        local_head = git_head_sha(root)
        local_repo = git_origin_repository(root)
        workflow_path = root / WORKFLOW_REL
        result["head_matches"] = record["run"]["head_sha"] == local_head
        result["workflow_matches"] = (
            workflow_path.is_file()
            and record["workflow"]["path"] == WORKFLOW_REL
            and record["workflow"]["sha256"] == sha256_file(workflow_path)
        )
        result["repository_matches"] = record["repository_full_name"] == local_repo
        result["ubuntu_pass"] = record["jobs"]["ubuntu"]["conclusion"] == "success"
        result["windows_pass"] = record["jobs"]["windows"]["conclusion"] == "success"
        for condition, code in [
            (result["head_matches"], "GITHUB_CI_HEAD_MISMATCH"),
            (result["workflow_matches"], "GITHUB_CI_WORKFLOW_MISMATCH"),
            (result["repository_matches"], "GITHUB_CI_REPOSITORY_MISMATCH"),
            (result["ubuntu_pass"], "GITHUB_CI_UBUNTU_NOT_PASS"),
            (result["windows_pass"], "GITHUB_CI_WINDOWS_NOT_PASS"),
        ]:
            if not condition:
                result["reasons"].append(code)
        result["verified"] = not result["reasons"]
    except Exception as exc:
        result["reasons"].append(f"GITHUB_CI_EVIDENCE_INVALID:{exc}")
    return result


def _gate_evidence_status(root: Path) -> dict[str, Any]:
    result = {
        "present": False,
        "passed": False,
        "current": False,
        "required_checks_pass": False,
        "missing_required_checks": [],
        "reasons": [],
    }
    path = root / GATE_EVIDENCE_REL
    if not path.is_file():
        result["reasons"].append("RELEASE_GATE_EVIDENCE_MISSING")
        return result
    result["present"] = True
    try:
        evidence = load_yaml(path)
        if evidence.get("schema_version") != "thomas_release_gate_evidence.v0.1":
            result["reasons"].append("RELEASE_GATE_EVIDENCE_SCHEMA_INVALID")
        result["passed"] = evidence.get("result") == "PASS"
        if not result["passed"]:
            result["reasons"].append("RELEASE_GATE_EVIDENCE_NOT_PASS")

        from lib.release_gate_evidence import repository_source_fingerprint

        actual, entries = repository_source_fingerprint(root)
        result["current"] = (
            evidence.get("repository_source_fingerprint") == actual
            and evidence.get("source_file_count") == len(entries)
        )
        if not result["current"]:
            result["reasons"].append("RELEASE_GATE_EVIDENCE_STALE")

        checks = evidence.get("checks")
        if not isinstance(checks, list):
            checks = []
            result["reasons"].append("RELEASE_GATE_CHECKS_INVALID")
        by_label: dict[str, dict[str, Any]] = {}
        by_id: dict[str, dict[str, Any]] = {}
        duplicate = False
        for item in checks:
            if not isinstance(item, dict):
                duplicate = True
                continue
            label = item.get("label")
            check_id = item.get("check_id")
            if not isinstance(label, str) or not isinstance(check_id, str):
                duplicate = True
                continue
            if label in by_label or check_id in by_id:
                duplicate = True
            by_label[label] = item
            by_id[check_id] = item
        if duplicate:
            result["reasons"].append("RELEASE_GATE_CHECKS_DUPLICATE_OR_INVALID")

        missing: list[str] = []
        for label, check_id in REQUIRED_GATE_CHECKS.items():
            by_label_item = by_label.get(label)
            by_id_item = by_id.get(check_id)
            if (
                by_label_item is None
                or by_id_item is None
                or by_label_item is not by_id_item
                or by_label_item.get("result") != "PASS"
            ):
                missing.append(check_id)
        result["missing_required_checks"] = missing
        result["required_checks_pass"] = not missing and not duplicate
        if missing:
            result["reasons"].append("RELEASE_GATE_REQUIRED_CHECKS_MISSING_OR_NOT_PASS")
    except Exception as exc:
        result["reasons"].append(f"RELEASE_GATE_EVIDENCE_INVALID:{exc}")
    return result


def _registry_disabled(path: Path, list_key: str, expected_status: str) -> bool:
    record = load_yaml(path)
    return record.get("status") == expected_status and all(
        isinstance(item, dict) and item.get("enabled") is False
        for item in record.get(list_key, [])
    )


def _validate_yaml_against_schema(root: Path, record_path: Path, schema_rel: str, label: str) -> None:
    schema_path = safe_relative_path(root, schema_rel, must_exist=True)
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    record = load_yaml(record_path)
    issues = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(record),
        key=lambda item: list(item.absolute_path),
    )
    if issues:
        rendered = "; ".join(
            f"{'.'.join(str(part) for part in item.absolute_path) or '$'}: {item.message}"
            for item in issues[:5]
        )
        raise ReadinessError(f"{label} schema validation failed: {rendered}")


def _require_tracked_paths(root: Path, paths: list[Path]) -> None:
    from lib.git_provenance import require_file_tracked_at_head

    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve(strict=True)
        if resolved in seen:
            continue
        seen.add(resolved)
        require_file_tracked_at_head(root, resolved)


def verify_current_core_release(root: Path) -> dict[str, Any]:
    result = {
        "present": False,
        "verified": False,
        "committed": False,
        "evidence_refs": [],
        "reasons": [],
    }
    current_path = root / CURRENT_CORE_REL
    if not current_path.is_file():
        result["reasons"].append("CURRENT_CORE_RELEASE_MISSING")
        return result
    result["present"] = True
    result["evidence_refs"].append(CURRENT_CORE_REL)
    try:
        from lib.core_release_verifier import verify_activation_record, verify_current_pointer

        pointer = verify_current_pointer(root, current_path)
        if pointer.get("runtime_activation_status") != "approved_via_activation_registry":
            raise ReadinessError("Current Core is not an active approved activation")
        activation_path = safe_relative_path(root, pointer.get("activation_path", ""), must_exist=True)
        activation, manifest, approval, manifest_path, approval_path = verify_activation_record(root, activation_path)
        if pointer.get("activation_id") != activation.get("activation_id"):
            raise ReadinessError("Current Core activation lineage mismatch")

        _validate_yaml_against_schema(root, current_path, "schemas/current_core_release.v0.2.schema.json", "Current Core pointer")
        _validate_yaml_against_schema(root, activation_path, "schemas/core_activation.v0.1.schema.json", "Core Activation")
        _validate_yaml_against_schema(root, approval_path, "schemas/thomas_core_release_approval.v0.3.schema.json", "Core Approval")
        _validate_yaml_against_schema(root, manifest_path, "schemas/thomas_core_release_manifest.v0.3.schema.json", "Core Release Manifest")

        release_dir = manifest_path.parent
        tracked_paths = [current_path, activation_path, manifest_path, approval_path]
        for item in manifest.get("files", []):
            if isinstance(item, dict) and isinstance(item.get("snapshot_path"), str):
                tracked_paths.append((release_dir / item["snapshot_path"]).resolve(strict=True))
        toolchain = manifest.get("build", {}).get("validation_toolchain", {})
        for item in toolchain.get("validator_files", []):
            if isinstance(item, dict) and isinstance(item.get("snapshot_path"), str):
                tracked_paths.append((release_dir / item["snapshot_path"]).resolve(strict=True))
        for key in ["dependency_lock", "release_gate_evidence"]:
            item = toolchain.get(key)
            if isinstance(item, dict) and isinstance(item.get("snapshot_path"), str):
                tracked_paths.append((release_dir / item["snapshot_path"]).resolve(strict=True))
        _require_tracked_paths(root, tracked_paths)
        result["committed"] = True
        result["verified"] = True
        result["evidence_refs"].extend(
            [
                activation_path.relative_to(root).as_posix(),
                manifest_path.relative_to(root).as_posix(),
                approval_path.relative_to(root).as_posix(),
            ]
        )
    except Exception as exc:
        result["reasons"].append(f"CURRENT_CORE_RELEASE_INVALID:{exc}")
    return result


def build_runtime_promotion_readiness(
    repo_root: Path,
    *,
    created_at: str | None = None,
    github_ci_evidence_ref: str | None = None,
) -> dict[str, Any]:
    root = repo_root.resolve(strict=True)
    created_at = created_at or utc_now()
    attestation = build_component_attestation(root, created_at=created_at)
    registry = load_yaml(root / REGISTRY_REL)
    gate = _gate_evidence_status(root)
    ci = verify_github_ci_evidence(root, github_ci_evidence_ref)
    current_core = verify_current_core_release(root)
    workflow_present = (root / WORKFLOW_REL).is_file()
    i0_4_lock_present = (root / I0_4_LOCK_REL).is_file()
    review_core_present = (root / REVIEW_CORE_REL).is_file()
    tool_safe = _registry_disabled(root / TOOL_REGISTRY_REL, "tools", "active_registry_no_active_tools")
    program_safe = _registry_disabled(root / PROGRAM_REGISTRY_REL, "programs", "active_registry_no_active_programs")

    requirements = {
        "component_attestation_pass": attestation["summary"]["result"] == "PASS",
        "i0_4_contract_lock_present": i0_4_lock_present,
        "release_gate_evidence_present": gate["present"],
        "release_gate_evidence_pass": gate["passed"],
        "release_gate_fingerprint_current": gate["current"],
        "release_gate_required_checks_pass": gate["required_checks_pass"],
        "github_workflow_present": workflow_present,
        "github_ci_evidence_present": ci["present"],
        "github_ci_evidence_verified": ci["verified"],
        "github_ci_head_matches": ci["head_matches"],
        "github_ci_workflow_matches": ci["workflow_matches"],
        "github_ci_repository_matches": ci["repository_matches"],
        "github_ci_ubuntu_pass": ci["ubuntu_pass"],
        "github_ci_windows_pass": ci["windows_pass"],
        "github_ci_evidence_ref": github_ci_evidence_ref,
        "review_core_release_present": review_core_present,
        "current_core_release_present": current_core["present"],
        "current_core_release_verified": current_core["verified"],
        "current_core_release_committed": current_core["committed"],
        "tool_registry_no_enabled_tools": tool_safe,
        "program_registry_no_enabled_programs": program_safe,
        "runtime_registry_source_of_truth": bool(registry.get("runtime_source_of_truth")),
        "runtime_authoritative_mode_enabled": bool(registry.get("runtime_authoritative_mode_enabled")),
    }

    checks: list[dict[str, Any]] = []

    def add(check_id: str, result: str, notes: str, *refs: str) -> None:
        checks.append({"check_id": check_id, "result": result, "evidence_refs": list(refs), "notes": notes})

    add(
        "component_attestation",
        "PASS" if requirements["component_attestation_pass"] else "BLOCK",
        "Registry component IDs and versions must match literal implementation constants.",
        f"in_memory:{attestation['attestation_id']}",
    )
    add("i0_4_contract_lock", "PASS" if i0_4_lock_present else "BLOCK", "I0.4 contract lock must be present.", I0_4_LOCK_REL)
    add("release_gate_evidence", "PASS" if gate["passed"] else "BLOCK", "Repository Release Gate evidence must report PASS.", GATE_EVIDENCE_REL)
    add("release_gate_fingerprint", "PASS" if gate["current"] else "BLOCK", "Gate evidence fingerprint must match current Gate-owned source.", GATE_EVIDENCE_REL)
    add(
        "release_gate_required_checks",
        "PASS" if gate["required_checks_pass"] else "BLOCK",
        "Gate evidence must contain the required I0.4, I0.5, I0.5.1, parity, security, and reproducibility checks exactly once and PASS.",
        GATE_EVIDENCE_REL,
    )
    add("github_workflow", "PASS" if workflow_present else "BLOCK", "Cross-platform GitHub Actions validation workflow must exist.", WORKFLOW_REL)
    add(
        "github_ci_evidence",
        "PASS" if ci["verified"] else ("BLOCK" if ci["present"] else "UNVERIFIED"),
        "GitHub CI evidence must be collected from the live GitHub API and bind the local HEAD, origin repository, workflow hash, and successful Ubuntu/Windows jobs.",
        github_ci_evidence_ref or "github_actions:unverified",
    )
    add("review_core_release", "PASS" if review_core_present else "BLOCK", "A review-ready Core Release record must exist.", REVIEW_CORE_REL)
    add("current_core_release", "PASS" if current_core["present"] else "BLOCK", "Activation review requires an explicit verified Current Core Release pointer.", CURRENT_CORE_REL)
    add(
        "current_core_verification",
        "PASS" if current_core["verified"] and current_core["committed"] else ("BLOCK" if current_core["present"] else "UNVERIFIED"),
        "Activation review requires Current Core to pass the existing Manifest, Approval, Activation, Revocation, hash, and committed-at-HEAD verification chain.",
        *(current_core["evidence_refs"] or [CURRENT_CORE_REL]),
    )
    add("tool_registry", "PASS" if tool_safe else "BLOCK", "I0.5.1 requires no enabled Tools.", TOOL_REGISTRY_REL)
    add("program_registry", "PASS" if program_safe else "BLOCK", "I0.5.1 requires no enabled Programs.", PROGRAM_REGISTRY_REL)
    add(
        "runtime_registry_mode",
        "PASS" if not requirements["runtime_registry_source_of_truth"] and not requirements["runtime_authoritative_mode_enabled"] else "BLOCK",
        "I0.5.1 remains candidate-only and must not become a Runtime source of truth.",
        REGISTRY_REL,
    )

    design_blocking_reasons = _expected_design_blocking_reasons(requirements)
    activation_blocking_reasons = _expected_activation_blocking_reasons(requirements)
    ready_for_design = not design_blocking_reasons
    ready_for_activation_review = not activation_blocking_reasons
    seed = {
        "attestation_sha256": attestation["integrity"]["attestation_sha256"],
        "requirements": requirements,
        "design_blocking_reasons": design_blocking_reasons,
        "activation_blocking_reasons": activation_blocking_reasons,
        "created_at": created_at,
    }
    readiness_id = short_id("rpr", seed)
    payload = {
        "schema_version": "runtime_promotion_readiness_fingerprint_payload.v0.1",
        "readiness_id": readiness_id,
        "component_attestation_sha256": attestation["integrity"]["attestation_sha256"],
        "requirements": requirements,
        "checks": checks,
        "design_blocking_reasons": design_blocking_reasons,
        "activation_blocking_reasons": activation_blocking_reasons,
        "created_at": created_at,
    }
    return {
        "schema_version": "runtime_promotion_readiness.v0.1",
        "readiness_id": readiness_id,
        "phase": "I0.5.1_REV3",
        "status": "REVIEW_ONLY_NOT_RUNTIME_ACTIVE",
        "component_attestation": {
            "attestation_id": attestation["attestation_id"],
            "attestation_sha256": attestation["integrity"]["attestation_sha256"],
            "result": attestation["summary"]["result"],
        },
        "requirements": requirements,
        "checks": checks,
        "summary": {
            "result": "READY_FOR_THOMAS_DESIGN_DECISION" if ready_for_design else "BLOCKED_NOT_READY",
            "blocking_reasons": design_blocking_reasons,
            "design_readiness": {
                "result": "READY_FOR_THOMAS_DESIGN_DECISION" if ready_for_design else "BLOCKED_NOT_READY",
                "blocking_reasons": design_blocking_reasons,
                "ready_for_runtime_authoritative_design": ready_for_design,
            },
            "activation_readiness": {
                "result": "READY_FOR_RUNTIME_ACTIVATION_REVIEW" if ready_for_activation_review else "BLOCKED_NOT_READY",
                "blocking_reasons": activation_blocking_reasons,
                "ready_for_runtime_activation_review": ready_for_activation_review,
            },
            "ready_for_runtime_authoritative_design": ready_for_design,
            "ready_for_runtime_activation_review": ready_for_activation_review,
            "ready_for_runtime_activation": False,
            "ready_for_external_execution": False,
            "ready_for_financial_execution": False,
        },
        "runtime_effect": {
            "grants_runtime_permission": False,
            "grants_runtime_activation": False,
            "grants_core_activation": False,
            "grants_tool_enablement": False,
            "grants_program_enablement": False,
            "grants_executor_enablement": False,
            "grants_external_execution": False,
            "grants_financial_execution": False,
            "consumes_approval": False,
            "mutates_runtime": False,
        },
        "integrity": {
            "hash_schema": "runtime_promotion_readiness_fingerprint_payload.v0.1",
            "readiness_fingerprint_payload": payload,
            "readiness_sha256": sha256_value(payload),
        },
        "created_at": created_at,
    }


def validate_component_attestation_semantics(record: dict[str, Any]) -> None:
    components = record.get("components", [])
    if len(components) != 2:
        raise ReadinessError("component attestation must contain exactly two components")
    pass_count = sum(
        item.get("result") == "PASS"
        and item.get("id_match") is True
        and item.get("version_match") is True
        and item.get("boundary_match") is True
        and item.get("implementation_exists") is True
        for item in components
    )
    block_count = len(components) - pass_count
    summary = record.get("summary", {})
    registry_boundary_match = summary.get("registry_boundary_match") is True
    expected_result = "PASS" if registry_boundary_match and block_count == 0 else "BLOCK"
    if summary.get("result") != expected_result:
        raise ReadinessError("component attestation summary result does not match component checks")
    if summary.get("pass_count") != pass_count or summary.get("block_count") != block_count:
        raise ReadinessError("component attestation summary counts do not match component checks")
    integrity = record.get("integrity", {})
    if integrity.get("attestation_sha256") != sha256_value(integrity.get("attestation_fingerprint_payload")):
        raise ReadinessError("component attestation fingerprint mismatch")


def _expected_design_blocking_reasons(requirements: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if not requirements.get("component_attestation_pass"):
        reasons.append("COMPONENT_ATTESTATION_BLOCKED")
    if not requirements.get("i0_4_contract_lock_present"):
        reasons.append("I0_4_CONTRACT_LOCK_MISSING")
    if not requirements.get("release_gate_evidence_present"):
        reasons.append("RELEASE_GATE_EVIDENCE_MISSING")
    else:
        if not requirements.get("release_gate_evidence_pass"):
            reasons.append("RELEASE_GATE_EVIDENCE_NOT_PASS")
        if not requirements.get("release_gate_fingerprint_current"):
            reasons.append("RELEASE_GATE_EVIDENCE_STALE")
        if not requirements.get("release_gate_required_checks_pass"):
            reasons.append("RELEASE_GATE_REQUIRED_CHECKS_NOT_VERIFIED")
    if not requirements.get("github_workflow_present"):
        reasons.append("GITHUB_VALIDATION_WORKFLOW_MISSING")
    if not requirements.get("github_ci_evidence_present"):
        reasons.append("GITHUB_CI_EVIDENCE_MISSING")
    elif not requirements.get("github_ci_evidence_verified"):
        reasons.append("GITHUB_CI_EVIDENCE_NOT_VERIFIED")
    if not requirements.get("review_core_release_present"):
        reasons.append("REVIEW_CORE_RELEASE_MISSING")
    if not requirements.get("tool_registry_no_enabled_tools"):
        reasons.append("TOOL_REGISTRY_NOT_READ_ONLY_SAFE")
    if not requirements.get("program_registry_no_enabled_programs"):
        reasons.append("PROGRAM_REGISTRY_NOT_READ_ONLY_SAFE")
    if requirements.get("runtime_registry_source_of_truth") or requirements.get("runtime_authoritative_mode_enabled"):
        reasons.append("RUNTIME_MODE_ALREADY_ENABLED_UNDER_REVIEW_ONLY_POLICY")
    return reasons


def _expected_activation_blocking_reasons(requirements: dict[str, Any]) -> list[str]:
    reasons = list(_expected_design_blocking_reasons(requirements))
    if not requirements.get("current_core_release_present"):
        reasons.append("CURRENT_CORE_RELEASE_MISSING")
    else:
        if not requirements.get("current_core_release_verified"):
            reasons.append("CURRENT_CORE_RELEASE_NOT_VERIFIED")
        if not requirements.get("current_core_release_committed"):
            reasons.append("CURRENT_CORE_RELEASE_NOT_COMMITTED_AT_HEAD")
    return reasons


def _expected_blocking_reasons(requirements: dict[str, Any]) -> list[str]:
    """Compatibility alias: top-level blockers are Design Readiness blockers."""
    return _expected_design_blocking_reasons(requirements)

def validate_runtime_promotion_readiness_semantics(record: dict[str, Any]) -> None:
    requirements = record.get("requirements", {})
    summary = record.get("summary", {})
    design = summary.get("design_readiness", {})
    activation = summary.get("activation_readiness", {})

    design_blockers = _expected_design_blocking_reasons(requirements)
    activation_blockers = _expected_activation_blocking_reasons(requirements)
    if summary.get("blocking_reasons") != design_blockers:
        raise ReadinessError("top-level blocking_reasons must mirror Design Readiness blockers")
    if design.get("blocking_reasons") != design_blockers:
        raise ReadinessError("Design Readiness blockers do not match requirements")
    if activation.get("blocking_reasons") != activation_blockers:
        raise ReadinessError("Activation Readiness blockers do not match requirements")

    design_ready = len(design_blockers) == 0
    activation_review_ready = len(activation_blockers) == 0
    design_result = "READY_FOR_THOMAS_DESIGN_DECISION" if design_ready else "BLOCKED_NOT_READY"
    activation_result = "READY_FOR_RUNTIME_ACTIVATION_REVIEW" if activation_review_ready else "BLOCKED_NOT_READY"

    if summary.get("result") != design_result or design.get("result") != design_result:
        raise ReadinessError("Design Readiness result does not match Design blockers")
    if activation.get("result") != activation_result:
        raise ReadinessError("Activation Readiness result does not match Activation blockers")
    if summary.get("ready_for_runtime_authoritative_design") is not design_ready:
        raise ReadinessError("ready_for_runtime_authoritative_design does not match Design Readiness")
    if design.get("ready_for_runtime_authoritative_design") is not design_ready:
        raise ReadinessError("nested Design Readiness flag does not match Design blockers")
    if summary.get("ready_for_runtime_activation_review") is not activation_review_ready:
        raise ReadinessError("ready_for_runtime_activation_review does not match Activation Readiness")
    if activation.get("ready_for_runtime_activation_review") is not activation_review_ready:
        raise ReadinessError("nested Activation Readiness flag does not match Activation blockers")
    if activation_review_ready and not design_ready:
        raise ReadinessError("Activation Readiness cannot be ready while Design Readiness is blocked")

    if (record.get("component_attestation", {}).get("result") == "PASS") is not bool(requirements.get("component_attestation_pass")):
        raise ReadinessError("component attestation result does not match requirements")
    if requirements.get("github_ci_evidence_verified"):
        required_ci = [
            "github_ci_evidence_present",
            "github_ci_head_matches",
            "github_ci_workflow_matches",
            "github_ci_repository_matches",
            "github_ci_ubuntu_pass",
            "github_ci_windows_pass",
        ]
        if not all(requirements.get(name) is True for name in required_ci):
            raise ReadinessError("verified GitHub CI evidence requires every bound CI check to pass")
        if not requirements.get("github_ci_evidence_ref"):
            raise ReadinessError("verified GitHub CI evidence requires an evidence reference")
    if requirements.get("current_core_release_verified") and not (
        requirements.get("current_core_release_present") and requirements.get("current_core_release_committed")
    ):
        raise ReadinessError("verified Current Core requires a present and committed Current Core pointer")
    if any(
        summary.get(name) is not False
        for name in ["ready_for_runtime_activation", "ready_for_external_execution", "ready_for_financial_execution"]
    ):
        raise ReadinessError("review-only readiness cannot claim activation or external/financial readiness")
    if any(value is not False for value in record.get("runtime_effect", {}).values()):
        raise ReadinessError("runtime promotion readiness must have no Runtime effects")
    integrity = record.get("integrity", {})
    if integrity.get("readiness_sha256") != sha256_value(integrity.get("readiness_fingerprint_payload")):
        raise ReadinessError("runtime promotion readiness fingerprint mismatch")
