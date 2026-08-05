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
from . import bridge_idempotency, control, socket_door, timeutil
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

# Re-exported so this door reads like the others. THE definition lives in `socket_door` beside
# the transport every door shares — see there for why an assistant action must never be
# attributed to `local_console`.
ASSISTANT_ACTOR = socket_door.ASSISTANT_ACTOR

CMD_STATUS = "status"
CMD_ENABLE = "enable"
CMD_DISABLE = "disable"

# The whole permission surface of this module. `control.CMD_RESUME` is deliberately NOT a verb
# a caller can name — `enable` is the only route to it, and it goes through an approval. A test
# asserts the raw control verbs stay unnameable here.
_ALLOWED_COMMANDS: frozenset[str] = frozenset({CMD_STATUS, CMD_ENABLE, CMD_DISABLE})

# Every key this door will act on. An unexpected key is refused rather than ignored: a frame
# carrying something this module does not understand must not be treated as a frame that means
# what the understood subset says. `request_id` is optional and carries no authority — see
# `_ENABLE_DOOR` for which verb actually uses it and why the other two do not.
_ALLOWED_KEYS: frozenset[str] = frozenset(
    {"command", "domain", "reason", "mode", "approval_id", bridge_idempotency.REQUEST_ID_KEY}
)

# `enable` is the only verb here with a duplicate worth preventing, and it has two of them. The
# ask shape mints an APPROVAL_REQUIRED record, so a retried frame puts a SECOND pending approval
# in front of Thomas for one intent — the failure mode where he answers one and the other sits
# there spendable. The spend shape is already single-use through the approval itself, but a
# retry gets `ALREADY_CONSUMED`, which reads like a refusal for something that in fact worked.
#
# `status` and `disable` are deliberately NOT wired to this. `status` has no effect. `disable`
# is idempotent by construction (KILLED applied to a KILLED runtime is the same runtime) and is
# the emergency stop: putting a file lock on the path a halt takes buys nothing and adds a way
# for a stop to block. An emergency control you must first acquire a lock for is a worse trade
# than the duplicate it would prevent.
_ENABLE_DOOR = "switch.enable"

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

# Deliberately not 1. This door carries the stop AND the start, and the start is the slow one
# (approval store, Core binding, fingerprint). A ceiling of one would let an in-flight `enable`
# refuse the `disable` behind it — the same failure that made this door its own service in the
# first place, reproduced inside it. See `socket_door.DEFAULT_MAX_CONCURRENT_REQUESTS`.
MAX_CONCURRENT_REQUESTS = 4


def socket_path(root: Path | None = None) -> Path:
    """The socket path, overridable per-deployment via ``MVP_SWITCH_BRIDGE_SOCKET``."""
    return socket_door.resolve_socket_path(SOCKET_ENV, SOCKET_REL, root)


def _echo_request_id(request: dict[str, Any], reply: dict[str, Any]) -> dict[str, Any]:
    """Say the id back on the two verbs that do not dedup on it.

    This door's rule is that a key it does not act on is refused rather than ignored, because a
    caller that sent it believed it would be used. ``request_id`` is now a key the door *does*
    understand, and `status` and `disable` deliberately do nothing with it (see `_ENABLE_DOOR`)
    — which lands in exactly the gap that rule exists to close. Echoing it is the honest
    middle: the caller can see the field arrived, and can see from `replayed` being absent that
    this was a fresh application rather than a repeat.
    """
    request_id = request.get(bridge_idempotency.REQUEST_ID_KEY)
    if request_id is not None:
        reply[bridge_idempotency.REQUEST_ID_KEY] = request_id
    return reply


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

    # **The effect below is global; the grant above is not.** `control.CMD_RESUME` clears the
    # one kill switch this runtime has — `ControlState` has no `domain` field and every
    # consumer (crypto's `live_route`, `paper`, `cycle`, the scheduler) reads that same state.
    # The approval, meanwhile, is minted per domain: `target_ref` is `trading_switch:<domain>`,
    # the domain is inside the action fingerprint, and `permission.py` writes the constraint
    # "Approval is single-use and authorizes this domain's switch only."
    #
    # Today those agree, for one reason only: there is exactly one domain. The module comment
    # above `_ALLOWED_DOMAINS` says `prediction` joins the set "and that is the whole change" —
    # and on this line, it is not. Adding a second domain would make a `crypto` grant re-arm
    # `prediction` too, which is the constraint text saying one thing while the effect does
    # another, in the direction that starts something nobody approved.
    #
    # So the mismatch is refused rather than documented. This cannot fire while the set has one
    # member; it fires the moment someone widens it without giving control state a domain, which
    # is exactly when it should. A test pins that by widening the set.
    if len(_ALLOWED_DOMAINS) > 1:
        raise ControlBlocked(
            "DOMAIN_EFFECT_MISMATCH",
            f"this door switches {sorted(_ALLOWED_DOMAINS)} but the runtime has one global "
            f"kill switch, so re-arming {domain!r} would re-arm the others on a grant that "
            f"names only {domain!r}. Give control state a domain before widening this door",
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
        return _echo_request_id(request, {
            "ok": True, "reply": outcome["reply"], "mode": outcome["mode"],
            "action": outcome["action"], "domain": _require_domain(request),
        })

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
        return _echo_request_id(request, {
            "ok": True, "reply": outcome["reply"], "mode": outcome["mode"],
            "changed": outcome["changed"], "action": outcome["action"],
            "actor": ASSISTANT_ACTOR, "domain": domain,
        })

    # CMD_ENABLE. Two shapes: without an approval id it opens the ask; with one it spends it.
    # Both are validated before anything is claimed, so a frame that was going to be refused
    # never spends its request_id.
    approval_id = request.get("approval_id")
    if approval_id is not None and (not isinstance(approval_id, str) or not approval_id.strip()):
        raise ControlBlocked(
            "MALFORMED_REQUEST", "'approval_id' must be a non-empty string when given"
        )

    request_id = bridge_idempotency.request_id_of(request)
    fingerprint = bridge_idempotency.fingerprint(request) if request_id is not None else ""
    if request_id is not None:
        prior = bridge_idempotency.claim(
            ledger, door=_ENABLE_DOOR, request_id=request_id,
            request_fingerprint=fingerprint, now=now,
        )
        if prior is not None:
            return bridge_idempotency.replay_reply(prior)

    try:
        if approval_id is None:
            reply = _open_ask(
                _require_domain(request), reason,
                approval_store=approval_store, now=now, repo_root=repo_root,
            )
            # The ask IS the effect here, and its identity is what a retry needs back: the id
            # to approve, when it lapses, and how. Without these the replay would be correct
            # and useless — "you already asked" with no way to answer.
            outcome = {
                key: reply.get(key)
                for key in ("approval_id", "expires_at", "approve_with", "domain")
            }
        else:
            reply = _spend(
                approval_id.strip(), reason,
                approval_store=approval_store, control_store=control_store, ledger=ledger,
                now=now, repo_root=repo_root,
            )
            outcome = {key: reply.get(key) for key in ("action", "mode", "domain", "approval_id")}
    except BaseException:
        # Nothing was applied, so the id names nothing. Note what this does NOT undo: a spend
        # that raised after `control.apply_command` cannot reach here — that call and the
        # CONSUMED record share one lock inside `_spend`, and an exception between them is the
        # window that lock exists to make impossible.
        if request_id is not None:
            bridge_idempotency.release(
                ledger, door=_ENABLE_DOOR, request_id=request_id,
                request_fingerprint=fingerprint, now=now,
            )
        raise

    if request_id is not None:
        bridge_idempotency.complete(
            ledger, door=_ENABLE_DOOR, request_id=request_id,
            request_fingerprint=fingerprint, outcome=outcome, now=now,
        )
    # On the fresh application too, so every reply from this door carries the id it was given
    # and `replayed` is the only thing that distinguishes the first from the second.
    return _echo_request_id(request, reply)


def open_door(
    path: Path,
    *,
    control_store: ControlStore,
    ledger: LedgerStore,
    approval_store: ApprovalStore | None = None,
) -> socket_door.SocketDoor:
    """Listen on ``path`` and serve switch actions from it.

    The framing, the deadline, the size cap, the peer check, the concurrency ceiling and the
    error envelope come from ``socket_door`` — shared with the read and dispatch doors so a
    malformed frame cannot be answered three different ways. What is specific to *this* door is
    the apply function, and therefore the verb set and the approval requirement.
    """
    return socket_door.SocketDoor(
        path,
        lambda request: apply_switch(
            request, control_store=control_store, ledger=ledger,
            approval_store=approval_store,
        ),
        max_concurrent_requests=MAX_CONCURRENT_REQUESTS,
    )
