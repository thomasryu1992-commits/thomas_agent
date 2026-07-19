"""Safety-Flag Gate — the enforced chokepoint for model/network capabilities.

CLAUDE.md requires that turning on real model invocation + network egress needs an
explicit, versioned, audited approval — not a good test result and not a bare
environment variable. This module is where that requirement becomes a *mechanism*
instead of a description.

The gate reads a **local, per-machine activation record** (gitignored, under
``.runtime_governance_state/``, mirroring Core activation) and fails closed unless the
record is present, integrity-consistent (a self-hash the operator computed, so tampering
is detectable), unexpired, references a real approval-evidence file, and explicitly
enables the requested capability flags for the requested provider. A caller that cannot
obtain an :class:`Authorization` here cannot open a network socket: the hosted provider
verifies the same authorization again at egress time (defense in depth), so a stray
env var, a container image, or a CI job cannot silently enable outbound model calls.

Nothing here performs a network call or stores a secret. The activation record is
metadata only (flags, provider id, timestamps, evidence ref, self-hash) — never a key.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Sequence, TypeVar

from runtime.read_only_kernel import integrity
from runtime.read_only_kernel.integrity import IntegrityError

from .authority import rank_of
from .errors import SafetyGateBlocked
from .paths import repo_root as _repo_root
from .timeutil import utc_now_iso as _utc_now_iso

# The capability a gated selection returns (a Provider, SearchTool, OperatorChannel, ...).
T = TypeVar("T")

# Capability flags this gate governs — each OFF by default; enabling one requires an
# activation record that lists it. model_invocation/network_access are the two named in
# CLAUDE.md; filesystem_write (R8) governs leaving a durable artifact on disk. It crosses
# no network, but it is the runtime's first effect outside its own private state, so it
# is gated on the same terms rather than on a bare env var.
MODEL_INVOCATION = "model_invocation"
NETWORK_ACCESS = "network_access"
FILESYSTEM_WRITE = "filesystem_write"
# approval_consumption (R10) governs spending an APPROVED, single-use approval to perform its
# bound action (a SENSITIVE_MEMORY_GOVERNANCE promotion). It crosses no network, but it is the
# first capability that acts on a *governance* decision rather than just recording it, so — like
# filesystem_write — it is gated on the same terms rather than on a bare env var.
APPROVAL_CONSUMPTION = "approval_consumption"
_KNOWN_FLAGS = frozenset({MODEL_INVOCATION, NETWORK_ACCESS, FILESYSTEM_WRITE, APPROVAL_CONSUMPTION})

# Local (gitignored) activation records — never committed, per-machine, like the Core pointer.
# ONE FILE PER PROVIDER. Each grant is scoped, expired, and evidenced on its own, so
# authorizing a second provider cannot widen, refresh, or accidentally re-authorize the
# first, and a corrupt or expired grant fails only its own provider closed rather than all
# of them. A single shared record could not express "the specialist may call Gemini AND the
# validator may call a different vendor" at all — it had one provider_id field.
ACTIVATIONS_DIR_REL = ".runtime_governance_state/safety_flag_activations"

# provider_id becomes a filename, so it is confined like any caller-supplied path segment:
# lowercase alnum start, then alnum/underscore/dot/hyphen. This admits every real provider
# id (google_ai_studio, brave_search, telegram, workspace.writer) and admits no separator,
# no traversal, and no absolute path.
_PROVIDER_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
# The one timestamp form the gate's lexicographic comparisons are correct for.
_TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

ACTIVATION_MARKER = "safety_flag_activation.v0"
_HASH_FIELD = "content_sha256"
_REQUIRED_FIELDS = (
    "activation_marker", "flags", "provider_id", "authority_level",
    "evidence_ref", "activated_at", "expires_at", _HASH_FIELD,
)


@dataclass(frozen=True)
class Authorization:
    """A granted authorization to use a network-capable capability.

    Produced only by :func:`authorize` after a valid activation record is verified.
    Frozen so it cannot be mutated after the grant; carries the activation self-hash
    so the same grant is re-checkable at egress time.
    """

    flags: tuple[str, ...]
    provider_id: str
    activation_sha256: str
    expires_at: str
    evidence_ref: str


def activation_path(root: Path, provider_id: str) -> Path:
    """Where ``provider_id``'s activation record lives, or fail closed.

    The id is a caller-supplied path segment, so it is validated against a strict pattern
    and the resolved path is verified to stay inside the activations directory — a provider
    id can name a grant, never a location.
    """
    if not (isinstance(provider_id, str) and _PROVIDER_ID_PATTERN.match(provider_id)):
        raise SafetyGateBlocked(
            "INVALID_PROVIDER_ID",
            f"provider id {provider_id!r} is not a valid activation name",
        )
    base = (root / ACTIVATIONS_DIR_REL).resolve()
    path = (base / f"{provider_id}.json").resolve()
    if path.parent != base:
        raise SafetyGateBlocked(
            "INVALID_PROVIDER_ID", f"provider id {provider_id!r} resolves outside the activations directory"
        )
    return path


def _load_record(root: Path, provider_id: str) -> dict[str, Any]:
    path = activation_path(root, provider_id)
    if not path.is_file():
        raise SafetyGateBlocked(
            "ACTIVATION_MISSING",
            f"no safety-flag activation record for {provider_id!r} at "
            f"{ACTIVATIONS_DIR_REL}/{provider_id}.json; the capability is disabled (fail-closed)",
        )
    try:
        raw = path.read_text(encoding="utf-8")
        record = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise SafetyGateBlocked("ACTIVATION_MALFORMED", f"activation record is unreadable: {exc}") from exc
    if not isinstance(record, dict):
        raise SafetyGateBlocked("ACTIVATION_MALFORMED", "activation record must be a JSON object")
    return record


def _verify_integrity(record: dict[str, Any]) -> str:
    """Recompute the activation self-hash and compare. Tamper-evident core of the gate."""
    for field in _REQUIRED_FIELDS:
        if field not in record:
            raise SafetyGateBlocked("ACTIVATION_MALFORMED", f"activation record missing field: {field}")
    if record["activation_marker"] != ACTIVATION_MARKER:
        raise SafetyGateBlocked("ACTIVATION_MALFORMED", "unrecognized activation_marker")
    if not isinstance(record["flags"], list) or not all(isinstance(f, str) for f in record["flags"]):
        raise SafetyGateBlocked("ACTIVATION_MALFORMED", "flags must be a list of strings")
    if rank_of(record["authority_level"]) is None:
        raise SafetyGateBlocked("ACTIVATION_MALFORMED", "authority_level is not a known P0..P6 level")

    claimed = record[_HASH_FIELD]
    payload = {k: v for k, v in record.items() if k != _HASH_FIELD}
    try:
        recomputed = integrity.sha256_record(payload)
    except IntegrityError as exc:
        raise SafetyGateBlocked("ACTIVATION_MALFORMED", f"activation record is not fingerprintable: {exc}") from exc
    if not isinstance(claimed, str) or claimed != recomputed:
        raise SafetyGateBlocked(
            "ACTIVATION_TAMPERED",
            "activation record self-hash does not match its content (tampered or hand-edited)",
        )
    return recomputed


def build_activation_record(
    *,
    flags: Sequence[str],
    provider_id: str,
    activated_at: str,
    expires_at: str,
    evidence_ref: str,
    authority_level: str,
) -> dict[str, Any]:
    """Build a valid activation record (with its self-hash) for an operator to write.

    This is the *only* supported way to produce the ``content_sha256``; it does not
    write any file and does not enable anything by itself — the operator records the
    approval evidence and writes the returned object to ``activation_path(root,
    provider_id)`` as a deliberate, local governance step, one grant per provider.
    """
    bad = [f for f in flags if f not in _KNOWN_FLAGS]
    if bad:
        raise SafetyGateBlocked("UNKNOWN_FLAG", f"not a governed safety flag: {bad}")
    if not _PROVIDER_ID_PATTERN.match(provider_id or ""):
        # Refuse to mint a grant whose id could not be stored or looked up.
        raise SafetyGateBlocked("INVALID_PROVIDER_ID", f"provider id {provider_id!r} is not a valid activation name")
    if rank_of(authority_level) is None:
        raise SafetyGateBlocked("ACTIVATION_MALFORMED", "authority_level is not a known P0..P6 level")
    for field_name, value in (("activated_at", activated_at), ("expires_at", expires_at)):
        if not _TIMESTAMP_PATTERN.match(str(value) or ""):
            # The gate compares these lexicographically, which is only a correct time
            # compare for the one fixed second-precision Z format — a grant minted with
            # "+00:00" or sub-second precision could compare wrong in EITHER direction
            # (including never-expires). Refuse to mint what authorize() cannot compare.
            raise SafetyGateBlocked(
                "ACTIVATION_MALFORMED",
                f"{field_name} must be the fixed UTC form YYYY-MM-DDThh:mm:ssZ, got {value!r}",
            )
    record = {
        "activation_marker": ACTIVATION_MARKER,
        "flags": list(flags),
        "provider_id": provider_id,
        "authority_level": authority_level,
        "evidence_ref": evidence_ref,
        "activated_at": activated_at,
        "expires_at": expires_at,
    }
    record[_HASH_FIELD] = integrity.sha256_record(record)
    return record


def authorize(
    required_flags: Sequence[str],
    *,
    provider_id: str,
    now: str,
    root: Path | None = None,
) -> Authorization:
    """Authorize a network-capable capability, or fail closed.

    Verifies **that provider's own** activation record: present, integrity-consistent,
    unexpired, references a real evidence file, and explicitly enables every
    ``required_flag``. Returns an :class:`Authorization` on success; raises
    :class:`SafetyGateBlocked` (a fail-closed BLOCK) otherwise.

    Each provider is authorized independently, so one open grant never speaks for another:
    a run may hold a Gemini grant and no Groq grant, and the Groq call still fails closed.
    """
    root = root if root is not None else _repo_root()
    record = _load_record(root, provider_id)
    activation_sha256 = _verify_integrity(record)

    activated_at, expires_at = record["activated_at"], record["expires_at"]
    # Verify-time re-check of the timestamp format: a self-hash only proves the record was
    # not edited, not that it was minted well-formed. Lexicographic comparison is a correct
    # time compare ONLY for this one fixed format, so anything else fails closed here
    # rather than comparing wrong (possibly in the never-expires direction).
    for field_name, value in (("activated_at", activated_at), ("expires_at", expires_at)):
        if not _TIMESTAMP_PATTERN.match(str(value) or ""):
            raise SafetyGateBlocked(
                "ACTIVATION_MALFORMED",
                f"activation {field_name} is not the fixed UTC form YYYY-MM-DDThh:mm:ssZ",
            )
    if now < str(activated_at):
        raise SafetyGateBlocked("ACTIVATION_NOT_YET_ACTIVE", "activation record is not active yet")
    if now >= str(expires_at):
        raise SafetyGateBlocked("ACTIVATION_EXPIRED", "safety-flag activation has expired (fail-closed)")

    # The filename is only an index; the record's own content is the authority. Copying
    # google_ai_studio.json to groq.json therefore grants nothing — the inner provider_id
    # still says google_ai_studio, and it is covered by the self-hash, so it cannot be
    # edited to agree without breaking integrity.
    if record["provider_id"] != provider_id:
        raise SafetyGateBlocked(
            "PROVIDER_NOT_AUTHORIZED",
            f"activation authorizes provider {record['provider_id']!r}, not {provider_id!r}",
        )
    enabled = set(record["flags"])
    missing = [f for f in required_flags if f not in enabled]
    if missing:
        raise SafetyGateBlocked("FLAG_NOT_ENABLED", f"activation does not enable required flags: {missing}")

    # The evidence ref must stay inside the repository: an absolute or escaping ref would
    # let ANY existing file on the machine (C:\Windows\win.ini) satisfy "references a real
    # approval-evidence file", which is not what the check attests.
    ref = str(record["evidence_ref"])
    ref_parts = PureWindowsPath(ref)
    if ref_parts.is_absolute() or ref_parts.drive or ref.startswith(("/", "\\")) or ".." in ref_parts.parts:
        raise SafetyGateBlocked(
            "EVIDENCE_INVALID", f"evidence_ref {ref!r} must be a repo-relative path without '..'"
        )
    root_real = root.resolve()
    evidence = (root_real / ref).resolve()
    if evidence != root_real and root_real not in evidence.parents:
        raise SafetyGateBlocked(
            "EVIDENCE_INVALID", f"evidence_ref {ref!r} resolves outside the repository"
        )
    if not evidence.is_file():
        raise SafetyGateBlocked(
            "EVIDENCE_MISSING",
            f"activation references approval evidence {record['evidence_ref']!r} that does not exist",
        )

    return Authorization(
        flags=tuple(record["flags"]),
        provider_id=provider_id,
        activation_sha256=activation_sha256,
        expires_at=str(expires_at),
        evidence_ref=str(record["evidence_ref"]),
    )


def assert_authorization(
    authorization: Any,
    *,
    required_flags: Sequence[str],
    provider_id: str,
    now: str,
) -> None:
    """Egress-time re-check: the socket-opening path calls this immediately before it
    would open a network connection. Fails closed unless it holds a genuine, unexpired
    :class:`Authorization` covering the required flags for this provider."""
    if not isinstance(authorization, Authorization):
        raise SafetyGateBlocked(
            "NOT_AUTHORIZED",
            "network egress attempted without a safety-flag authorization (fail-closed)",
        )
    if authorization.provider_id != provider_id:
        raise SafetyGateBlocked("PROVIDER_NOT_AUTHORIZED", "authorization is for a different provider")
    missing = [f for f in required_flags if f not in authorization.flags]
    if missing:
        raise SafetyGateBlocked("FLAG_NOT_ENABLED", f"authorization does not enable required flags: {missing}")
    if now >= authorization.expires_at:
        raise SafetyGateBlocked("ACTIVATION_EXPIRED", "authorization has expired since it was granted")


def select_gated(
    *,
    env_var: str,
    opt_in_value: str,
    flags: Sequence[str],
    provider_id: str,
    default_factory: Callable[[], T],
    gated_factory: Callable[[Authorization], T],
    now: str | None = None,
    root: Path | None = None,
) -> T:
    """The Safety-Flag Gate chokepoint shared by every gated capability.

    Every capability that can reach outside the runtime — the model provider, the search
    tool, the operator channel, the workspace writer — chooses its implementation the same
    way, and that sameness is the safety property, not a coincidence worth deduplicating:

    1. Without the caller's explicit opt-in (``env_var == opt_in_value``), return the inert
       default. It needs no gate because it cannot reach anything.
    2. With the opt-in, ``authorize`` FIRST. Only if it passes does ``gated_factory`` run.

    Step 2 is why this exists. "Never construct the capable thing before the gate opens" was
    previously four authors each remembering to write it in the right order; here it is
    structural — ``gated_factory`` receives the :class:`Authorization` as its argument, so it
    cannot run without one. An env var alone can never open a capability: with no valid
    activation record, ``authorize`` raises :class:`SafetyGateBlocked` and nothing is built.

    The gated object is still expected to re-verify its authorization at the moment it acts
    (defense in depth) — this selects; it does not excuse the egress check.
    """
    choice = os.environ.get(env_var, "").strip().lower()
    if choice != opt_in_value:
        return default_factory()
    # Opted in — the gate must pass before the capable implementation is even constructed.
    authorization = authorize(flags, provider_id=provider_id, now=now or _utc_now_iso(), root=root)
    return gated_factory(authorization)
