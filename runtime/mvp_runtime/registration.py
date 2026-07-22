"""Program registry registration — ask, verify, apply (explicit Thomas decision 2026-07-22).

The last link before activation in the programization chain: an ACCEPTED candidate with
its program request may be **registered** in the Program Registry as
``status: candidate, enabled: false`` — an index entry + a definition file, granting
nothing (``unregistered_or_disabled_resource_execution`` stays BLOCK for it; activation is
a separate approval). Registration is a change to committed governance *source*, so the
runtime never performs it: the flow is the C8b verified-never-spent pattern —

1. **Ask** (:func:`request_registration`): a real bound task anchors an APPROVAL_REQUIRED
   PermissionDecision (scope ``TOOL_PROGRAM_GOVERNANCE``) whose fingerprint binds the
   exact definition content + review lineage, turned into a PENDING R9 ask.
2. Thomas answers ``/approve`` / ``/reject`` on the verified control channel.
3. **Apply** (:func:`apply_registration`, via ``scripts/register_program_candidate.py``):
   the APPROVED approval is *verified* against the content hash re-derived from current
   state (never consumed — this scope has no consumption implementation), then the
   definition file + registry entry are written into the **working tree**, self-checked
   through the canonical resolver, and the action is recorded on the programization
   ledger stream. Committing the change is Thomas's PR — no direct main commits.

Thomas authors the definition substance (purpose / inputs / outputs) in an input file;
the runtime pins everything load-bearing: ``status: candidate``, ``enabled: false``,
``implementation_available: false``, effects all false, and the required permission level
taken from the program request's registry evaluation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml

from runtime.read_only_kernel import integrity
from runtime.registry_resolution import (
    RegistryResolutionError,
    canonical_sha256,
    load_resource_definitions,
    resolve_resource_registry,
)

from . import approval as approval_mod
from . import timeutil
from .binding import bind_task_to_core
from .errors import ApprovalBlocked, ProgramizationBlocked
from .events import stamped_event
from .intake import build_task
from .paths import repo_root as _repo_root
from .permission import POLICY_BINDING, build_program_registration_permission_decision
from .program_request import PROGRAM_REGISTRY_REL
from .programization import REVIEW_EVENT_TYPE, ProgramizationStore

REGISTRATION_ACTION_TYPE = "program.registry.registration"
DEFINITION_SCHEMA_VERSION = "program_definition.v0.1"

_INPUT_LISTS = ("inputs", "outputs")


def _require_operator_text(value: str, code: str, what: str) -> str:
    if not (isinstance(value, str) and value.strip()):
        raise ProgramizationBlocked(code, f"registration requires {what}")
    return value.strip()


def _lineage(store: ProgramizationStore, candidate_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The ACCEPTED candidate and its program request — the chain evidence registration
    stands on. Fail-closed when either link is missing."""
    candidate = store.latest_candidates().get(candidate_id)
    if candidate is None:
        raise ProgramizationBlocked("CANDIDATE_NOT_FOUND", f"no candidate {candidate_id!r}")
    if candidate.get("status") != "ACCEPTED":
        raise ProgramizationBlocked(
            "REGISTRATION_REQUIRES_ACCEPTED", "registration needs an ACCEPTED candidate")
    request = None
    for row in store.read_requests():
        if row.get("candidate_id") == candidate_id:
            request = row.get("request")
    if not isinstance(request, dict):
        raise ProgramizationBlocked(
            "REGISTRATION_REQUIRES_REQUEST",
            "registration needs the candidate's program request (create it first with `request`)",
        )
    return dict(candidate), dict(request)


def build_program_definition(
    request: Mapping[str, Any],
    definition_input: Mapping[str, Any],
) -> dict[str, Any]:
    """The exact definition content a registration would commit.

    Thomas supplies the substance (``purpose``, ``inputs``, ``outputs``); everything
    load-bearing is pinned by the runtime: candidate status, disabled runtime, no
    implementation, no effects, and the permission level the program request recorded.
    Fail-closed on missing substance or secret-bearing content."""
    if not isinstance(definition_input, Mapping):
        raise ProgramizationBlocked("DEFINITION_INPUT_INVALID", "definition input must be a mapping")
    purpose = definition_input.get("purpose")
    if not (isinstance(purpose, str) and purpose.strip()):
        raise ProgramizationBlocked("DEFINITION_INPUT_INVALID", "definition input requires a purpose")
    lists: dict[str, list[str]] = {}
    for key in _INPUT_LISTS:
        value = definition_input.get(key)
        items = [x for x in value if isinstance(x, str) and x.strip()] if isinstance(value, list) else []
        if not items:
            raise ProgramizationBlocked(
                "DEFINITION_INPUT_INVALID", f"definition input requires a non-empty string list {key!r}")
        lists[key] = items

    resource = request.get("resource", {})
    definition = {
        "schema_version": DEFINITION_SCHEMA_VERSION,
        "program_id": str(resource.get("program_id")),
        "version": str(resource.get("program_version")),
        "status": "candidate",
        "owner": "Thomas",
        "purpose": purpose.strip(),
        "deterministic": True,
        "required_permission_level": str(resource.get("required_permission_level")),
        "inputs": lists["inputs"],
        "outputs": lists["outputs"],
        "effects": {"external_action": False, "filesystem_write": False, "network_access": False},
        "runtime": {"implementation_available": False, "enabled": False},
    }
    try:
        integrity.scan_for_secret_bearing_keys(definition)
    except integrity.IntegrityError as exc:
        raise ProgramizationBlocked("SECRET_IN_DEFINITION", str(exc)) from exc
    return definition


def definition_content_sha256(definition: Mapping[str, Any]) -> str:
    """The material identity of one registration — the canonical hash the registry entry
    records (bare hex) is the same hash the approval binds (prefixed)."""
    return canonical_sha256(dict(definition))


def definition_rel_path(program_id: str) -> str:
    return f"programs/definitions/{program_id.replace('.', '_').upper()}_PROGRAM.yaml"


def _registry_has(registry: Mapping[str, Any], program_id: str, version: str) -> bool:
    return any(
        e.get("program_id") == program_id and e.get("version") == version
        for e in registry.get("programs", []) if isinstance(e, Mapping)
    )


def request_registration(
    store: ProgramizationStore,
    candidate_id: str,
    definition_input: Mapping[str, Any],
    *,
    now: str | None = None,
    ttl_minutes: int | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Build the records that ASK Thomas for this registration. Performs nothing.

    Mirrors the C8b promotion ask: a real bound task, the APPROVAL_REQUIRED decision
    fingerprinting the exact definition content + lineage, and the PENDING approval
    request. The caller persists the decision + request to the approval store and audits
    the ask (the script does). Refuses when the program is already registered."""
    now = now or timeutil.utc_now_iso()
    root = repo_root if repo_root is not None else _repo_root()
    candidate, request = _lineage(store, candidate_id)
    definition = build_program_definition(request, definition_input)
    program_id, version = definition["program_id"], definition["version"]

    try:
        registry = yaml.safe_load((root / PROGRAM_REGISTRY_REL).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ProgramizationBlocked("REGISTRY_UNRESOLVABLE", f"program registry unreadable: {exc}") from exc
    if _registry_has(registry, program_id, version):
        raise ProgramizationBlocked(
            "ALREADY_REGISTERED", f"{program_id}@{version} is already in the Program Registry")

    content = definition_content_sha256(definition)
    task = build_task(
        f"프로그램 등재 검토: {program_id}@{version} (candidate {candidate_id})",
        now=now, channel="manual", requester_type="real_thomas", requester_id="Thomas",
        authenticated=True, repo_root=root,
    )
    binding, bound = bind_task_to_core(task, repo_root=root, now=now)
    permission_decision = build_program_registration_permission_decision(
        bound, program_id=program_id, program_version=version,
        definition_sha256=f"sha256:{content}",
        candidate_id=candidate_id,
        program_request_id=str(request.get("program_request_id")),
        now=now, repo_root=root,
    )
    approval_request = approval_mod.build_approval_request(
        permission_decision, now=now, ttl_minutes=ttl_minutes, repo_root=root,
    )
    return {
        "candidate": candidate,
        "program_request": request,
        "definition": definition,
        "task": task,
        "binding": binding,
        "bound_task": bound,
        "permission_decision": permission_decision,
        "approval_request": approval_request,
        "content_sha256": f"sha256:{content}",
    }


def verify_registration_approval(
    approval: Mapping[str, Any] | None,
    *,
    definition: Mapping[str, Any],
    now: str | None = None,
) -> dict[str, Any]:
    """Verify an approval authorizes EXACTLY this registration, or fail closed.

    The C8b verification posture: exists, APPROVED, unexpired, snapshots this action
    type, and the content hash matches the definition re-derived from CURRENT state — a
    candidate/request/input that changed since Thomas approved mints a different hash and
    is refused. The approval is verified, never consumed."""
    now = now or timeutil.utc_now_iso()
    if approval is None:
        raise ApprovalBlocked("APPROVAL_MISSING", "no approval record with that id")
    status = approval.get("status")
    if status != "APPROVED":
        raise ApprovalBlocked("APPROVAL_NOT_APPROVED", f"approval status is {status}, not APPROVED")
    expires_at = (approval.get("validity") or {}).get("expires_at")
    if not isinstance(expires_at, str) or timeutil.parse_iso(expires_at) <= timeutil.parse_iso(now):
        raise ApprovalBlocked("APPROVAL_EXPIRED", "the approval's validity window has passed")
    snapshot = approval.get("approved_action_snapshot") or {}
    if snapshot.get("action_type") != REGISTRATION_ACTION_TYPE:
        raise ApprovalBlocked(
            "APPROVAL_WRONG_ACTION", f"approval snapshots {snapshot.get('action_type')!r}, not a registration")
    expected = f"sha256:{definition_content_sha256(definition)}"
    if snapshot.get("content_sha256") != expected:
        raise ApprovalBlocked(
            "APPROVAL_CONTENT_MISMATCH",
            "the approval binds a different registration (definition content changed)",
        )
    return dict(approval)


def apply_registration(
    definition: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Write the definition file + registry entry into the working tree. Fail-closed.

    Create-only: an existing definition path or an already-registered id+version refuses.
    The updated registry is self-checked through the canonical resolver (definition hash,
    status/version/runtime mirroring) before it is written — on a failed self-check the
    just-created definition file is removed and the registry is left untouched. The entry
    is ``status: candidate, enabled: false, runtime_implementation_available: false``:
    registration grants nothing; committing the change is Thomas's PR."""
    root = repo_root if repo_root is not None else _repo_root()
    definition = dict(definition)
    program_id, version = str(definition.get("program_id")), str(definition.get("version"))
    rel_path = definition_rel_path(program_id)
    definition_path = root / rel_path
    registry_path = root / PROGRAM_REGISTRY_REL

    try:
        registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ProgramizationBlocked("REGISTRY_UNRESOLVABLE", f"program registry unreadable: {exc}") from exc
    if _registry_has(registry, program_id, version):
        raise ProgramizationBlocked(
            "ALREADY_REGISTERED", f"{program_id}@{version} is already in the Program Registry")
    if definition_path.exists():
        raise ProgramizationBlocked(
            "DEFINITION_PATH_EXISTS", f"{rel_path} already exists; registration is create-only")

    entry = {
        "program_id": program_id,
        "version": version,
        "status": "candidate",
        "enabled": False,
        "definition_path": rel_path,
        "definition_sha256": definition_content_sha256(definition),
        "runtime_implementation_available": False,
    }
    new_registry = {**registry, "programs": [*registry.get("programs", []), entry]}

    # The resolver reads definition files from disk, so write the definition first, then
    # self-check the WHOLE updated registry; a failed check removes the created file and
    # never touches the registry (create-only rollback of our own artifact).
    definition_path.parent.mkdir(parents=True, exist_ok=True)
    definition_path.write_text(
        yaml.safe_dump(definition, allow_unicode=True, sort_keys=False), encoding="utf-8")
    try:
        definitions = load_resource_definitions(repo_root=root, registry=new_registry, collection_key="programs")
        resolve_resource_registry(
            repo_root=root, registry=new_registry, definitions=definitions,
            governance_policy={"policy_id": POLICY_BINDING["policy_id"]},
            collection_key="programs", id_key="program_id",
        )
    except RegistryResolutionError as exc:
        definition_path.unlink(missing_ok=True)
        raise ProgramizationBlocked(
            "REGISTRATION_SELF_CHECK_FAILED", f"updated registry fails resolution: {exc}") from exc

    registry_path.write_text(
        yaml.safe_dump(new_registry, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {"entry": entry, "definition_path": rel_path}


def build_registration_event(
    entry: Mapping[str, Any],
    *,
    candidate_id: str,
    approval_id: str,
    registered_by: str,
    reason: str,
    now: str,
) -> dict[str, Any]:
    """The tamper-evident standalone ledger event for one applied registration."""
    return stamped_event(
        REVIEW_EVENT_TYPE,
        action="program_registered",
        candidate_id=candidate_id,
        program_id=str(entry.get("program_id")),
        program_version=str(entry.get("version")),
        registry_status="candidate",
        registry_enabled=False,
        definition_sha256=str(entry.get("definition_sha256")),
        approval_id=approval_id,
        reviewed_by=registered_by, reason=reason, created_at=now,
    )
