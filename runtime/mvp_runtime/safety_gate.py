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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from runtime.read_only_kernel import integrity
from runtime.read_only_kernel.integrity import IntegrityError

from .authority import rank_of
from .errors import SafetyGateBlocked
from .paths import repo_root as _repo_root

# Capability flags this gate governs. These are the two OFF-by-default safety flags
# named in CLAUDE.md; enabling either requires an activation record that lists it.
MODEL_INVOCATION = "model_invocation"
NETWORK_ACCESS = "network_access"
_KNOWN_FLAGS = frozenset({MODEL_INVOCATION, NETWORK_ACCESS})

# Local (gitignored) activation record — never committed, per-machine, like the Core pointer.
ACTIVATION_REL = ".runtime_governance_state/safety_flag_activation.json"

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


def _load_record(root: Path) -> dict[str, Any]:
    path = root / ACTIVATION_REL
    if not path.is_file():
        raise SafetyGateBlocked(
            "ACTIVATION_MISSING",
            f"no safety-flag activation record at {ACTIVATION_REL}; network-capable "
            "providers are disabled (fail-closed)",
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
    approval evidence and writes the returned object to ``ACTIVATION_REL`` as a
    deliberate, local governance step.
    """
    bad = [f for f in flags if f not in _KNOWN_FLAGS]
    if bad:
        raise SafetyGateBlocked("UNKNOWN_FLAG", f"not a governed safety flag: {bad}")
    if rank_of(authority_level) is None:
        raise SafetyGateBlocked("ACTIVATION_MALFORMED", "authority_level is not a known P0..P6 level")
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

    Verifies the local activation record: present, integrity-consistent, unexpired,
    references a real evidence file, and explicitly enables every ``required_flag`` for
    ``provider_id``. Returns an :class:`Authorization` on success; raises
    :class:`SafetyGateBlocked` (a fail-closed BLOCK) otherwise.
    """
    root = root if root is not None else _repo_root()
    record = _load_record(root)
    activation_sha256 = _verify_integrity(record)

    activated_at, expires_at = record["activated_at"], record["expires_at"]
    if now < str(activated_at):
        raise SafetyGateBlocked("ACTIVATION_NOT_YET_ACTIVE", "activation record is not active yet")
    if now >= str(expires_at):
        raise SafetyGateBlocked("ACTIVATION_EXPIRED", "safety-flag activation has expired (fail-closed)")

    if record["provider_id"] != provider_id:
        raise SafetyGateBlocked(
            "PROVIDER_NOT_AUTHORIZED",
            f"activation authorizes provider {record['provider_id']!r}, not {provider_id!r}",
        )
    enabled = set(record["flags"])
    missing = [f for f in required_flags if f not in enabled]
    if missing:
        raise SafetyGateBlocked("FLAG_NOT_ENABLED", f"activation does not enable required flags: {missing}")

    evidence = (root / str(record["evidence_ref"])).resolve()
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
