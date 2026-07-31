"""The switch door — the assistant may stop trading freely, and start it only with a grant.

Thomas runs a conversational assistant (Hermes) beside this runtime and wants it to work as
his secretary over it: report the board, and flip the trading switch both ways. Stopping was
already reachable (the halt door). Starting was refused by construction. This module is the
one door that carries both, and the asymmetry survives — it moved from "impossible" to
"requires Thomas's signature", which is where it belonged.

**Why starting is different, still.** Halting is idempotent and fail-safe: applying KILLED to
an already-KILLED runtime is the same runtime, and the worst outcome of a spurious halt is
that trading stops. ``resume`` re-arms an autonomous path that places real orders, and the
assistant on the other end of this socket is a model that reads untrusted text and can be
talked into calling a tool. So ``enable`` never acts on the request that asks for it. It
creates an APPROVAL_REQUIRED ask and hands back an id; the runtime acts only when that id
comes back APPROVED, and an approval can only become APPROVED on Thomas's verified control
channel (``operator.py`` R9, ``TELEGRAM_PRIVATE_1_TO_1``).

**Nothing here is a new authority.** ``resume`` is already in the policy's
``emergency_controls_allowed`` — an explicit Thomas decision (2026-07-19) resting on host
access being operator authentication. The approval lifecycle, its single-use rule, its TTL and
its channel gate are all ``approval.py``'s, unchanged. What this module adds is the one thing
that did not exist: a path from the assistant to an ask, and from Thomas's answer to the
effect. Scope is ``RUNTIME_GOVERNANCE`` (already APPROVAL_REQUIRED in the policy), so there is
no policy edit anywhere in this change.

**The effect follows the snapshot, never the request.** The domain that gets re-armed is read
out of the approved action snapshot, not out of the frame presenting the approval id. A caller
therefore cannot get an approval for one domain and spend it on another — and more generally,
no field the requester controls decides which check runs or what the check runs against.

**Absorbing the halt door.** ``disable`` carries what ``halt_bridge`` carried (``kill`` and
``pause``), for the same reason and with the same attribution. Two doors for one switch would
mean two places to reason about the asymmetry, and the halt door's own docstring already
argued the asymmetry belongs in one enforced surface rather than two documented ones. Stopping
stays approval-free: an emergency control you must first get signed is not an emergency
control.

Failure directions, each chosen once:

- Unknown/unpermitted verb -> ``VERB_NOT_PERMITTED``, nothing applied.
- Unexpected key in the frame -> ``ARGUMENT_NOT_ACCEPTED``. The door names what it accepts and
  refuses the rest rather than ignoring it, so a field it does not understand can never be
  read as consent to something it does.
- Missing reason -> ``REASON_REQUIRED``. An unattributed switch flip on a money path is not
  worth the convenience of omitting it.
- ``enable`` without an approval -> ``APPROVAL_REQUIRED`` **carrying the id to approve**. This
  is the ordinary path, not an error: it is how the ask is made.
- An approval that is unknown / not APPROVED / expired / already spent / fingerprint-drifted /
  scoped elsewhere / pointed at another domain -> its own reason code, nothing applied.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import approval as approval_mod
from . import control, socket_door, timeutil
from .approval_store import ApprovalStore
from .binding import bind_task_to_core
from .control import ControlStore
from .errors import ControlBlocked
from .filelock import locked
from .intake import build_task
from .permission import (
    TRADING_SWITCH_PERMISSION_SCOPE,
    build_trading_switch_permission_decision,
)
from .store import LedgerStore

from . import _scripts_bridge  # noqa: F401  (side effect: scripts/ on sys.path, once)

from lib.action_fingerprint import compute_action_fingerprint  # noqa: E402

# The actor recorded on every control event this door produces. Deliberately NOT
# `console_cli.LOCAL_ACTOR`: an operator reading the ledger must be able to tell a flip that
# came from SSH from one that came from the assistant, which matters precisely because the
# assistant is the less trusted of the two. Shared with the halt door's constant so the two
# read identically in the ledger across the absorption.
ASSISTANT_ACTOR = "assistant_bridge"

CMD_STATUS = "status"
CMD_ENABLE = "enable"
CMD_DISABLE = "disable"

# The whole permission surface of this module. `control.CMD_RESUME` is deliberately NOT a verb
# a caller can name — `enable` is the only route to it, and it goes through an approval. A test
# asserts the raw control verbs stay unnameable here.
_ALLOWED_COMMANDS: frozenset[str] = frozenset({CMD_STATUS, CMD_ENABLE, CMD_DISABLE})

# Every key this door will act on. An unexpected key is refused rather than ignored: a frame
# carrying something this module does not understand must not be treated as a frame that means
# what the understood subset says.
_ALLOWED_KEYS: frozenset[str] = frozenset(
    {"command", "domain", "reason", "mode", "approval_id"}
)

# Stopping has two shapes and the caller picks; both are already in the policy's
# `emergency_controls_allowed`. Built from control's own constants so a rename there cannot
# silently widen this.
_DISABLE_MODES: dict[str, str] = {"kill": control.CMD_KILL, "pause": control.CMD_PAUSE}
_DEFAULT_DISABLE_MODE = "kill"

# Domains this door switches. `crypto` is the only trading domain that exists; `prediction`
# joins the set when PM1 has a switch to flip, and that is the whole change — the door takes a
# domain argument so a second domain never means a second door.
_ALLOWED_DOMAINS: frozenset[str] = frozenset({"crypto"})
_DEFAULT_DOMAIN = "crypto"

# The prefix the approved action snapshot must carry. The domain is read from here, never from
# the request that presents the approval id.
_SWITCH_TARGET_PREFIX = "trading_switch:"

SOCKET_REL = ".runtime_governance_state/bridge/switch.sock"
SOCKET_ENV = "MVP_SWITCH_BRIDGE_SOCKET"


def socket_path(root: Path | None = None) -> Path:
    """The socket path, overridable per-deployment via ``MVP_SWITCH_BRIDGE_SOCKET``."""
    return socket_door.resolve_socket_path(SOCKET_ENV, SOCKET_REL, root)


def _require_reason(request: dict[str, Any]) -> str:
    reason = request.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ControlBlocked(
            "REASON_REQUIRED", "a switch action must state its reason; it is recorded"
        )
    return reason.strip()


def _require_domain(request: dict[str, Any]) -> str:
    raw = request.get("domain")
    if raw is None:
        return _DEFAULT_DOMAIN
    if not isinstance(raw, str) or not raw.strip():
        raise ControlBlocked("MALFORMED_REQUEST", "'domain' must be a non-empty string when given")
    domain = raw.strip().lower()
    if domain not in _ALLOWED_DOMAINS:
        raise ControlBlocked(
            "DOMAIN_NOT_PERMITTED",
            f"{domain!r} is not a domain this door switches; it carries "
            f"{sorted(_ALLOWED_DOMAINS)} only",
        )
    return domain


def _open_ask(
    domain: str,
    reason: str,
    *,
    approval_store: ApprovalStore,
    now: str,
    repo_root: Path | None,
) -> dict[str, Any]:
    """Create the APPROVAL_REQUIRED ask for re-arming ``domain`` and return it to the caller.

    Anchored to a real bound Task like every other ask (``approval_cli request``), so the
    approval hangs off a Core Binding rather than floating free.
    """
    task = build_task(
        f"자동매매 스위치 재개 검토: {domain}",
        now=now, channel="agent", requester_type="agent", requester_id=ASSISTANT_ACTOR,
        authenticated=True,
    )
    _, bound = bind_task_to_core(task, now=now)
    decision = build_trading_switch_permission_decision(
        bound, domain, now=now, repo_root=repo_root
    )
    request = approval_mod.build_approval_request(decision, now=now)
    approval_store.append_permission_decision(decision)
    approval_store.append([request])
    return {
        "ok": False,
        "reason_code": "APPROVAL_REQUIRED",
        "reason": (
            f"re-arming {domain} needs Thomas's approval on the control channel; "
            f"nothing has been changed"
        ),
        "approval_id": request["approval_id"],
        "expires_at": request["validity"]["expires_at"],
        "approve_with": f"/approve {request['approval_id']}",
        "domain": domain,
        "requested_reason": reason,
    }


def _spend(
    approval_id: str,
    reason: str,
    *,
    approval_store: ApprovalStore,
    control_store: ControlStore,
    ledger: LedgerStore,
    now: str,
    repo_root: Path | None,
) -> dict[str, Any]:
    """Verify an APPROVED grant and spend it once to re-arm the domain it names.

    Mirrors ``consumption.consume_approval``'s checks — status, expiry, bound decision,
    fingerprint — because those are what make a grant a grant. It does NOT copy that module's
    kill-switch precondition: this action runs precisely when execution is not allowed, which
    is the state it exists to leave.
    """
    record = approval_store.get(approval_id)
    if record is None:
        raise ControlBlocked("UNKNOWN_APPROVAL", f"no approval with id {approval_id}")
    status = record.get("status")
    if status == approval_mod.STATUS_CONSUMED:
        raise ControlBlocked(
            "ALREADY_CONSUMED", "approval has already been consumed (one-time use)"
        )
    if status != approval_mod.STATUS_APPROVED:
        raise ControlBlocked(
            "NOT_APPROVED", f"only an APPROVED approval can be spent; this one is {status}"
        )
    if approval_mod.is_expired(record, now=now):
        raise ControlBlocked(
            "APPROVAL_EXPIRED",
            f"approval expired at {record['validity']['expires_at']}; it can no longer be spent",
        )

    decision = approval_store.get_permission_decision(record["permission_decision_id"])
    if decision is None:
        raise ControlBlocked(
            "PERMISSION_DECISION_MISSING",
            f"the decision {record['permission_decision_id']} this approval binds to is not on record",
        )

    snapshot = record["approved_action_snapshot"]
    try:
        recomputed = compute_action_fingerprint(snapshot)
    except ValueError as exc:
        raise ControlBlocked("FINGERPRINT_UNCOMPUTABLE", str(exc)) from exc
    if recomputed != record.get("action_fingerprint"):
        raise ControlBlocked(
            "FINGERPRINT_MISMATCH",
            "the approved action no longer fingerprints to its recorded value; spending is refused",
        )
    if snapshot.get("permission_scope") != TRADING_SWITCH_PERMISSION_SCOPE:
        raise ControlBlocked(
            "SCOPE_NOT_SPENDABLE",
            f"scope {snapshot.get('permission_scope')} is not a trading-switch grant",
        )

    # The domain comes from what Thomas approved, not from what the caller sent. A grant for
    # one domain cannot be presented against another, and the frame gets no say in which.
    target_ref = str(snapshot.get("target_ref", ""))
    if not target_ref.startswith(_SWITCH_TARGET_PREFIX):
        raise ControlBlocked(
            "TARGET_NOT_SWITCH", f"approval target {target_ref!r} is not a trading switch"
        )
    domain = target_ref[len(_SWITCH_TARGET_PREFIX):]
    if domain not in _ALLOWED_DOMAINS:
        raise ControlBlocked(
            "DOMAIN_NOT_PERMITTED",
            f"the approved domain {domain!r} is not one this door switches",
        )

    # Single-use under a cross-process lock, the rule R10 established: the stored status is
    # re-read and the CONSUMED record appended inside one exclusion, so two concurrent spends
    # cannot both pass (the loser gets ALREADY_CONSUMED). The effect is applied inside the same
    # exclusion, so a spend can never be recorded without its effect or the reverse.
    lock = approval_store.path.with_name(approval_store.path.name + ".switch.lock")
    with locked(lock, code="APPROVAL_WRITE_FAILED", label="trading switch spend"):
        fresh = approval_store.get(approval_id)
        if fresh is None or fresh.get("status") != approval_mod.STATUS_APPROVED:
            raise ControlBlocked(
                "ALREADY_CONSUMED", "approval has already been consumed (one-time use)"
            )
        outcome = control.apply_command(
            control_store, control.CMD_RESUME, actor=ASSISTANT_ACTOR, now=now,
            reason=f"{reason} [approval {approval_id}]", ledger=ledger,
        )
        consumed = approval_mod.build_consumed_record(
            fresh, decision, consumed_at=now,
            consumption_ref=f"control:{domain}:{outcome['action']}",
            repo_root=repo_root,
        )
        approval_store.append([consumed])

    return {
        "ok": True,
        "reply": outcome["reply"],
        "mode": outcome["mode"],
        "changed": outcome["changed"],
        "action": outcome["action"],
        "actor": ASSISTANT_ACTOR,
        "domain": domain,
        "approval_id": approval_id,
    }


def apply_switch(
    request: Any,
    *,
    control_store: ControlStore,
    ledger: LedgerStore,
    approval_store: ApprovalStore | None = None,
    now: str | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Validate one request and apply it, or raise a typed ``ControlBlocked``.

    Pure with respect to the transport — an already-decoded object in, a reply out — which is
    what makes the permission surface testable without a listener.
    """
    if not isinstance(request, dict):
        raise ControlBlocked("MALFORMED_REQUEST", "request must be a JSON object")

    unexpected = set(request) - _ALLOWED_KEYS
    if unexpected:
        raise ControlBlocked(
            "ARGUMENT_NOT_ACCEPTED",
            f"this door accepts only {sorted(_ALLOWED_KEYS)}; "
            f"it will not act on {sorted(unexpected)}",
        )

    command = request.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ControlBlocked("MALFORMED_REQUEST", "request needs a non-empty 'command'")
    command = command.strip().lower()
    if command not in _ALLOWED_COMMANDS:
        raise ControlBlocked(
            "VERB_NOT_PERMITTED",
            f"{command!r} is not permitted here; this door carries "
            f"{sorted(_ALLOWED_COMMANDS)} only",
        )

    now = now or timeutil.utc_now_iso()
    approval_store = approval_store or ApprovalStore.default(repo_root)

    if command == CMD_STATUS:
        # Read-only, so no reason is required and no ledger event is written — the same
        # posture the read door takes for `runtime_status`.
        outcome = control.apply_command(
            control_store, control.CMD_STATUS, actor=ASSISTANT_ACTOR, now=now,
        )
        return {
            "ok": True, "reply": outcome["reply"], "mode": outcome["mode"],
            "action": outcome["action"], "domain": _require_domain(request),
        }

    reason = _require_reason(request)

    if command == CMD_DISABLE:
        raw_mode = request.get("mode")
        if raw_mode is None:
            mode = _DEFAULT_DISABLE_MODE
        elif isinstance(raw_mode, str) and raw_mode.strip():
            mode = raw_mode.strip().lower()
        else:
            raise ControlBlocked("MALFORMED_REQUEST", "'mode' must be a non-empty string when given")
        if mode not in _DISABLE_MODES:
            raise ControlBlocked(
                "MODE_NOT_PERMITTED",
                f"{mode!r} is not a stop this door applies; it carries "
                f"{sorted(_DISABLE_MODES)} only",
            )
        domain = _require_domain(request)
        outcome = control.apply_command(
            control_store, _DISABLE_MODES[mode], actor=ASSISTANT_ACTOR, now=now,
            reason=reason, ledger=ledger,
        )
        return {
            "ok": True, "reply": outcome["reply"], "mode": outcome["mode"],
            "changed": outcome["changed"], "action": outcome["action"],
            "actor": ASSISTANT_ACTOR, "domain": domain,
        }

    # CMD_ENABLE. Two shapes: without an approval id it opens the ask; with one it spends it.
    approval_id = request.get("approval_id")
    if approval_id is None:
        return _open_ask(
            _require_domain(request), reason,
            approval_store=approval_store, now=now, repo_root=repo_root,
        )
    if not isinstance(approval_id, str) or not approval_id.strip():
        raise ControlBlocked(
            "MALFORMED_REQUEST", "'approval_id' must be a non-empty string when given"
        )
    return _spend(
        approval_id.strip(), reason,
        approval_store=approval_store, control_store=control_store, ledger=ledger,
        now=now, repo_root=repo_root,
    )


def open_door(
    path: Path,
    *,
    control_store: ControlStore,
    ledger: LedgerStore,
    approval_store: ApprovalStore | None = None,
) -> socket_door.SocketDoor:
    """Listen on ``path`` and serve switch actions from it.

    The framing, the deadline, the size cap and the error envelope come from ``socket_door`` —
    shared with the read and dispatch doors so a malformed frame cannot be answered three
    different ways. What is specific to *this* door is the apply function, and therefore the
    verb set and the approval requirement.
    """
    return socket_door.SocketDoor(
        path,
        lambda request: apply_switch(
            request, control_store=control_store, ledger=ledger,
            approval_store=approval_store,
        ),
    )
