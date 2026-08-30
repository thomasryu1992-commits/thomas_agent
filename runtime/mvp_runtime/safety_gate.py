"""Safety-Flag Gate — the enforced chokepoint for model/network capabilities.

Since 2026-08-10 (Thomas), **every gated capability opens on its environment opt-in
alone**: the operator names the capability in the process environment, the capable
implementation is built only behind that opt-in (never before — the construction-order
property below is unchanged), and every egress re-check re-reads the environment. The
per-machine grant records and their 30-day renewal are retired. The path here was
incremental and each step is still documented at its call site: live trading left
grants on 2026-07-28 (an expiry that traps an open position), the candle archive on
2026-08-04 and the Naver lane on 2026-08-09 (a renewal gap is a silent hole in a
rolling collection) — and on 2026-08-10 Thomas retired the renewal for the rest on the
gap-vs-benefit ledger those three had already priced.

Stated so a future reader restoring grants knows what they are restoring: a grant was
a second factor (a record only the operator could mint, integrity-hashed, expiring,
carrying scope and authority level, whose deletion was a live mid-flight revocation),
and none of that exists in the env-only world. Revocation is now: unset the variable
and restart the process — a running process's environment does not change under it,
which `assert_authorization` below is honest about.

The grant machinery itself (`authorize`, `build_activation_record`, `select_gated`,
`select_gated_optional`, `select_gated_chain`, and `scripts/activate_safety_flag.py`)
was removed on 2026-08-30 — this is that reviewed change. It had zero runtime callers
since 2026-08-10; the containment test keeps sweeping for the retired names so a
reintroduction is a named decision, never a drift. Leftover
`.runtime_governance_state/safety_flag_activations/*.json` files stay inert exactly as
before: nothing reads them.

Nothing here performs a network call or stores a secret.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence, TypeVar

from runtime.read_only_kernel import integrity
from runtime.read_only_kernel.integrity import IntegrityError

from .errors import SafetyGateBlocked

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

# The self-hash field name of the retired activation records. Still read in ONE place:
# `assert_authorization`'s record-path branch below re-hashes a record a hand-built
# Authorization may still point at (the documented test seam keeps that branch reachable).
_HASH_FIELD = "content_sha256"


@dataclass(frozen=True)
class Authorization:
    """A granted authorization to use a network-capable capability.

    Produced by :func:`env_only_authorization` (the one production path since the grant
    machinery's removal — module docstring). Frozen so it cannot be mutated after the
    grant. ``activation_path`` survives from the grant era: a hand-built test
    authorization may still point it at a record file, and
    :func:`assert_authorization`'s record branch then re-reads that file at egress —
    the historical live-revocation contract, kept because the field is part of this
    class's shape and the branch is the honest behavior for anything that sets it.
    """

    flags: tuple[str, ...]
    provider_id: str
    activation_sha256: str
    expires_at: str
    evidence_ref: str
    activation_path: str | None = None
    # Set ONLY by `env_only_authorization` — `(env_var, opt_in_value)` for a capability gated
    # by the environment opt-in alone (live trading first, 2026-07-28; every capability since
    # 2026-08-10). `assert_authorization` keys its re-check on this field.
    #
    # It is a field rather than a marker inside `evidence_ref`, and that is load-bearing:
    # `evidence_ref` on a grant-backed authorization is copied verbatim out of the operator's
    # activation record, so keying on its text let a RECORD choose which check ran. A record
    # carrying `evidence_ref: "env_only:MVP_LIVE_TRADING=real"` passed the path validation
    # (no drive, not absolute, no `..`, and a file of that name is legal on Linux) and then took
    # the env branch — skipping the grant re-read, so deleting the grant file stopped revoking
    # it. That defeated the one property `assert_authorization` below promised about grants.
    # The grant producer (`authorize`) is gone now; the field stays record-proof by
    # construction — only `env_only_authorization` sets it.
    env_gate: tuple[str, str] | None = None


def assert_authorization(
    authorization: Any,
    *,
    required_flags: Sequence[str],
    provider_id: str,
    now: str,
) -> None:
    """Egress-time re-check: the socket-opening path calls this immediately before it
    would open a network connection. Fails closed unless it holds a genuine, unexpired
    :class:`Authorization` covering the required flags for this provider. An
    authorization that still carries an ``activation_path`` (the grant-era shape; only a
    hand-built one can, since the grant producer was removed) is additionally re-checked
    against that record on disk — the historical live-revocation contract, honored for
    whatever still sets the field.

    For an authorization from :func:`select_env_gated` there is no record, so the re-check
    re-reads the environment variable instead. It is the weaker of the two and the code below
    says why."""
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
    if authorization.env_gate is not None:
        # The env-only gate (live trading 2026-07-28; every capability 2026-08-10): there is no
        # grant record to re-read, so the egress re-check re-reads the ENV. Be honest about how
        # much weaker that is — a running process's environment does not change under it, so
        # unlike deleting a grant file this is NOT a mid-flight revocation, and stopping a live
        # scheduler means restarting it (see the halt note the readiness board prints). Kept
        # anyway, because the alternative is an egress path with no re-check at all and this
        # still fails closed for every caller that builds its authorization once and uses it
        # later.
        #
        # Keyed on `env_gate`, which only `env_only_authorization` sets. Not on
        # `activation_path is None` — that is the documented hand-built-test seam, and reusing it
        # would make a production authorization indistinguishable from a test one. And not on the
        # `evidence_ref` text either: that string is copied verbatim from the operator's grant
        # record, so keying on it let a RECORD select which check ran (see `env_gate`'s comment).
        env_var, expected = authorization.env_gate
        if os.environ.get(env_var, "").strip().lower() != expected.strip().lower():
            raise SafetyGateBlocked(
                "ENV_OPT_IN_WITHDRAWN",
                f"{env_var} no longer opts in to {expected!r} — the environment IS the gate for "
                "this capability (fail-closed)",
            )
    elif authorization.activation_path is not None:
        record_path = Path(authorization.activation_path)
        if not record_path.is_file():
            raise SafetyGateBlocked(
                "ACTIVATION_REVOKED",
                "the activation record backing this authorization is gone — "
                "deleting a grant revokes it (fail-closed)",
            )
        current: str | None
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
            payload = {k: v for k, v in record.items() if k != _HASH_FIELD} if isinstance(record, dict) else None
            current = integrity.sha256_record(payload) if payload is not None else None
        except (OSError, ValueError, IntegrityError):
            current = None
        if current != authorization.activation_sha256:
            raise SafetyGateBlocked(
                "ACTIVATION_CHANGED",
                "the activation record no longer matches the granted authorization "
                "(replaced, edited, or corrupt) — re-authorize against the current record",
            )


# --- the environment-only gate --------------------------------------------------------------
#
# Thomas decision, 2026-07-28 (live trading): gated by its environment opt-in ALONE, with no
# per-machine grant — and, since Thomas 2026-08-10, the same is true of EVERY gated capability.
# The grant-backed selectors themselves were removed 2026-08-30 (module docstring).
#
# The separate-function shape predates that and is kept: while both worlds existed, a
# `require_grant=False` keyword on the grant selector would have put "no grant needed" one token
# away from the model provider, the search tool, the operator channel and the workspace writer;
# a separate function can only be reached by a caller that names it. It is also what keeps the
# containment test's AST sweep meaningful — env-only call sites are enumerable BY NAME.
#
# Why live trading left grants first: the grant was TTL-capped at 30 days and this system is
# meant to run unattended for months. The sharper reason is that a grant expiring while a
# position is OPEN blocks the CLOSE path too — `evaluate_live_close_guard` exempts a reduceOnly
# close from the loss breaker, the daily count, the exposure cap, the promotion gate and both
# kill switches, and then requires the gate. A halt that traps an open position is what those
# exemptions exist to prevent. The candle archive (2026-08-04) and the Naver lane (2026-08-09)
# followed on the renewal-gap argument, and 2026-08-10 retired the renewal for the rest.
#
# What is given up, stated so a future reader restoring the grant knows what they are restoring:
# a second factor, an expiry, and an audited per-machine record of scope and authority level.
#
# What is NOT given up: revocation. For live trading, `console_cli kill` is file-based, instant,
# checked by the order guard, and deliberately exempted by the close path — it stops new entries
# without trapping a position, which is precisely what grant expiry could not do. For everything
# else, revocation is the environment: unset the variable and restart the process.
ENV_ONLY_EVIDENCE_PREFIX = "env_only:"


def env_only_authorization(
    *, flags: Sequence[str], provider_id: str, env_var: str, opt_in_value: str
) -> Authorization:
    """An :class:`Authorization` backed by the environment opt-in instead of a grant record.

    Deliberately a real ``Authorization`` rather than a bypass: the capable implementations keep
    their constructor and their egress re-check unchanged, so this exception cannot also quietly
    remove the "re-verify immediately before opening a socket" property.

    ``env_gate`` carries the setting :func:`assert_authorization` re-reads at egress instead of a
    file that does not exist. ``evidence_ref`` spells the same thing for a human reading a log
    and is **not** load-bearing — it used to be, and a grant record could spell the same prefix
    into its own evidence path to skip its file re-check. ``expires_at`` is far-future **by
    design** — removing the expiry is the decision; encoding a fake one would make the board and
    the audit trail claim a bound that nothing enforces.
    """
    return Authorization(
        flags=tuple(flags),
        provider_id=provider_id,
        activation_sha256="",
        expires_at="9999-12-31T23:59:59Z",
        evidence_ref=f"{ENV_ONLY_EVIDENCE_PREFIX}{env_var}={opt_in_value}",
        activation_path=None,
        env_gate=(env_var, opt_in_value),
    )


def select_env_gated(
    *,
    env_var: str,
    opt_in_value: str,
    flags: Sequence[str],
    provider_id: str,
    default_factory: Callable[[], T],
    gated_factory: Callable[[Authorization], T],
) -> T:
    """The gate chokepoint every capability selects through, environment opt-in only.

    Construction order is the safety property: the capable implementation is built only after the opt-in is
    confirmed and receives its ``Authorization`` as an argument, so "never construct the capable
    thing before the gate opens" stays structural here too. The environment IS the gate.
    """
    # Both sides normalised, and `assert_authorization`'s re-check normalises the same way. Only
    # the env value was, which is right for every caller today (all five pass
    # `REAL_LIVE_TRADING = "real"`) and silently wrong for one that passes "REAL": the gate would
    # never open and nothing would say why.
    choice = os.environ.get(env_var, "").strip().lower()
    if choice != opt_in_value.strip().lower():
        return default_factory()
    return gated_factory(
        env_only_authorization(
            flags=flags, provider_id=provider_id, env_var=env_var, opt_in_value=opt_in_value
        )
    )


def select_env_gated_optional(
    *,
    env_var: str,
    flags: Sequence[str],
    provider_id: str,
    gated_factory: Callable[[Authorization], T],
) -> tuple[T | None, str | None]:
    """The chokepoint for a capability that DEGRADES rather than fails closed, with the
    environment as the only gate: ``(impl, None)`` when ``env_var``'s
    comma-separated list names ``provider_id``, ``(None, reason_code)`` otherwise.

    The member must be NAMED; the variable merely being set is not an opt-in. That keeps
    the per-provider scope the per-provider grants used to carry: a list that spells the
    light tier's id still cannot open the heavy tier's gate. Use rule unchanged from the retired grant era: ONLY where the fallback is itself already
    authorized and inert-or-narrower; a capability with no safe fallback fails closed."""
    choice = os.environ.get(env_var, "").strip().lower()
    names = [part.strip() for part in choice.split(",") if part.strip()]
    if provider_id not in names:
        return None, "ENV_OPT_IN_MISSING"
    return gated_factory(
        env_only_authorization(
            flags=flags, provider_id=provider_id, env_var=env_var, opt_in_value=choice
        )
    ), None


def select_env_gated_chain(
    *,
    env_var: str,
    factories: dict[str, Callable[[Authorization], T]],
    flags: Sequence[str],
    default_factory: Callable[[], T],
) -> list[T]:
    """The chain-aware chokepoint (comma-separated failover order), environment gate only.

    The single-value semantics match :func:`select_env_gated` (unset or a single
    unrecognized value -> the inert default, never any capable path), and the plural rule
    is unchanged: **a chain never silently shrinks.** The moment the operator writes a
    comma they are being explicit, so ANY unknown name or ANY duplicate fails the WHOLE
    selection closed at startup — never at 3am when the primary goes down and the chain
    quietly has one link.

    Every member's :class:`Authorization` carries the FULL normalised chain string as its
    ``env_gate`` expectation, so EDITING the chain — not only emptying it — trips every
    member's egress re-check: the composition is the opt-in, and a member the operator
    removed from the list cannot keep serving out of a long-lived process."""
    choice = os.environ.get(env_var, "").strip().lower()
    if not choice:
        return [default_factory()]
    names = [part.strip() for part in choice.split(",") if part.strip()]
    if len(names) == 1 and names[0] not in factories:
        # Single unrecognized opt-in falls back to inert, exactly like select_env_gated.
        return [default_factory()]
    unknown = [name for name in names if name not in factories]
    if unknown:
        raise SafetyGateBlocked(
            "UNKNOWN_PROVIDER",
            f"failover chain names unknown provider(s) {unknown}; "
            f"known: {sorted(factories)} (fail-closed — a chain never silently shrinks)",
        )
    if len(set(names)) != len(names):
        raise SafetyGateBlocked(
            "DUPLICATE_PROVIDER", f"failover chain names a provider twice: {names}"
        )
    return [
        factories[name](
            env_only_authorization(
                flags=flags, provider_id=name, env_var=env_var, opt_in_value=choice
            )
        )
        for name in names
    ]
