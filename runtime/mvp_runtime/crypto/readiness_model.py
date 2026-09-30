"""The readiness state (model v2): whether a live entry can open, as eleven three-valued components.

Pure judgement over the report ``live_readiness.build_readiness`` assembles: it reads that mapping and
nothing else (no environment, no clock, no file, no venue). ``live_readiness`` collects the facts (its
own switches and account read on the trading process, the trading process's records everywhere else)
and renders the board; this module says what those facts add up to.

It was the middle of ``live_readiness`` until crypto refactor plan PR-07, moved whole and unchanged.
``live_readiness`` re-exports every public name as the same object, so its importers and the board's
output are what they were.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..control import ACTIVE, HALT_SOFT, KILLED, PAUSED
from .live_pnl import LIVE_PNL_VENUE_FIGURE_MISSING

# === the readiness state (model v2, crypto PR5a) ===================================================
#
# `ready` answers "can THIS process trade", and was read as "live trading is on" (the 2026-09-13
# review). `live_entry_possible` then answered "can a real position open" from four facts and missed
# the kill, the disarm and every breaker: a KILLED runtime drops every later fire, so its last record
# stayed OPEN and fresh — and the answer True — for two hours; a disarmed one keeps firing and records
# HELD, which the recorded gate reads as open, so the answer stayed True for as long as it ran (FO-10).
#
# v2 names every fact an entry needs as a component, and each is three-valued:
#
# * ``False`` — an entry door refuses on it. Only where a door would, so False is never a guess —
#   with two judgements that are coarser, and say so:
#   - the last fire's per-context refusals. Data the door refuses, an account the leg could not read,
#     and a leg refused whole before any venue action (BLOCKED) each read False when half or more of
#     the last fire's contexts were refused on it — the pipeline's stall rule — though a minority of
#     contexts may still enter;
#   - `trading_cycle_recent`: no enabled pipeline schedule, or no fire within three of its intervals.
#     No door refuses on that; nothing runs to enter. It errs toward refusal, the safe direction.
# * ``None`` — this process cannot observe it: no cycle on record yet, an unreadable ledger, a last
#   fire too old or undatable to speak for the next one, an account snapshot too old to speak for now.
#   Never to be read as possible.
# * ``True`` — observed, and admitting.
#
# `live_entry_possible` is their three-valued AND: any False is False, else any None is None, and only
# all True is True. `blocking` and `unknown` name each False and each None with its reason.
#
# Whose word a fact is depends on where the board runs. A process carrying the live-trading
# environment — the trading process itself — answers from its own switches and its own account read.
# Every other process (the operator console, the assistant's read door) answers from what the trading
# process recorded: its cycle records and its account snapshot, dated, and unknown once too old. The
# switches it reads are the ones the leg read at the last fire, so an env change on the trading
# process shows here at the next fire, one interval late.
#
# "Possible" means the autonomous leg may open a position at its next cycle. Entries happen only
# inside a cycle, so a trading process that has not fired within three of its schedule's intervals
# opens nothing — False, NO_RECENT_CYCLE, under `trading_cycle_recent` alone — while what that old
# fire saw is unknown, and so is its gate once the record is two hours old. A scheduler that stopped
# reads True for up to three intervals: the heartbeat stall alarm is the instrument for that, not
# this board (review of #906).
#
# Per-order capacity is not readiness, and is not here: open exposure against its cap, the concurrent-
# position caps, a symbol already in flight, a stop-loss cooldown. The guard decides those for the
# order at hand; `guard_dry_run_status` is this process's judgement of a representative one.

READINESS_MODEL = "readiness_state.v2"

# In the order a reader fixes them: what is installed, what the machine is allowed, what the operator
# switched, what is armed, what the venue backs, what risk admits, whether the pipeline still fires,
# and what its last fire saw.
READINESS_COMPONENTS = (
    "live_capability_installed",
    "execution_stage",
    "live_gate_open",
    "runtime_control",
    "armed_strategy_count",
    "venue_ready",
    "risk_ready",
    "account_ready",
    "trading_cycle_recent",
    "market_data_ready",
    "reconciliation_ready",
)

SOURCE_THIS_PROCESS = "this_process"
SOURCE_RECORDED = "recorded"
NOT_REPORTED = "NOT_REPORTED"

# The reasons the stall rule decides (`_majority`). A component refused on one is False for most of the
# last fire's contexts, and a minority may still enter (`minority_may_enter`, review of #907).
# Private: `vocabulary.ACCOUNT_UNREADABLE` is the leg's own code, with another value.
_MAJORITY = "MAJORITY_"
_LEG_BLOCKED = "LEG_BLOCKED"
_ACCOUNT_UNREADABLE = "ACCOUNT_UNREADABLE"

# The rows the entry door refuses on, read from state every process mounts, and what each is called
# when it refuses. The pre-order snapshot row is judged apart (`_risk_component`): it also fails on a
# record that no longer proves past orders, which the entry path does not refuse.
_RISK_ROWS = (
    ("registered_budget", "BUDGET_INVALID"),
    ("risk_limits_record", "RISK_LIMITS_UNUSABLE"),
    ("bracket_breaker", "BRACKET_BREAKER"),
    ("api_breaker", "API_BREAKER"),
    ("entry_marks", "ENTRY_MARKS_UNREADABLE"),
)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _component(ok: bool | None, reason: str, **extra: Any) -> dict[str, Any]:
    return {"ok": ok, "reason": reason, **extra}


def _rows(status: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The board's check rows; anything else where they belong reads as none (review of #906)."""
    checks = status.get("checks")
    return [c for c in checks if isinstance(c, Mapping)] if isinstance(checks, (list, tuple)) else []


def _check_ok(status: Mapping[str, Any], name: str) -> bool | None:
    """The named row's verdict, or None when the board carries no such row."""
    for check in _rows(status):
        if check.get("check") == name:
            return bool(check.get("ok"))
    return None


def _capability_component(status: Mapping[str, Any]) -> dict[str, Any]:
    implemented = status.get("order_path_implemented")
    wired = status.get("autonomous_routing_wired")
    if implemented is None or wired is None:
        return _component(None, NOT_REPORTED)
    if not implemented:
        return _component(False, "ORDER_PATH_NOT_IMPLEMENTED")
    if not wired:
        return _component(False, "ROUTING_NOT_WIRED")
    return _component(True, "INSTALLED")


def _stage_component(status: Mapping[str, Any]) -> dict[str, Any]:
    stage = _mapping(status.get("execution_stage"))
    if "admits_entry" not in stage:
        return _component(None, NOT_REPORTED)
    name = str(stage.get("stage"))
    if stage.get("admits_entry"):
        return _component(True, f"STAGE_{name}", stage=name)
    # An unbound record reads READ_ONLY; its own reason says why, which the rung name would not.
    reason = str(stage["reason_code"]) if stage.get("valid") is False and stage.get("reason_code") \
        else f"STAGE_{name}"
    return _component(False, reason, stage=name)


def _gate_component(status: Mapping[str, Any], inputs: Mapping[str, Any], *,
                    opted: bool) -> dict[str, Any]:
    """The opt-in, the confirmation phrase and the manual kill switch — the gate and the two switches
    the guard refuses every autonomous entry on."""
    if opted:
        if _check_ok(status, "confirmation_phrase") is not True:
            return _component(False, "CONFIRMATION_MISSING", source=SOURCE_THIS_PROCESS)
        if _check_ok(status, "manual_kill_switch") is not True:
            return _component(False, "MANUAL_KILL_ENGAGED", source=SOURCE_THIS_PROCESS)
        return _component(True, "OPEN", source=SOURCE_THIS_PROCESS)
    # This process's env rows describe this container (#382); the trading process's record decides.
    recorded = _mapping(status.get("recorded_gate"))
    if not recorded:
        return _component(None, NOT_REPORTED, source=SOURCE_RECORDED)
    if not recorded.get("known"):
        return _component(None, "RECORD_UNREADABLE" if recorded.get("error") else "NO_RECORD",
                          source=SOURCE_RECORDED)
    if recorded.get("stale"):
        return _component(None, "RECORD_STALE", source=SOURCE_RECORDED)
    if not recorded.get("open"):
        return _component(False, "GATE_DISABLED", source=SOURCE_RECORDED)
    switches = _mapping(_mapping(inputs.get("recorded_fire")).get("gate"))
    if not switches:
        # A record written before the leg stamped its switches: the opt-in is known, the rest is not.
        return _component(None, "SWITCHES_NOT_RECORDED", source=SOURCE_RECORDED)
    if not switches.get("confirmation_present"):
        return _component(False, "CONFIRMATION_MISSING", source=SOURCE_RECORDED)
    if switches.get("manual_kill_switch"):
        return _component(False, "MANUAL_KILL_ENGAGED", source=SOURCE_RECORDED)
    return _component(True, "OPEN", source=SOURCE_RECORDED)


def _control_component(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """The runtime control state every entry reads (`ControlState.trading_allowed`): ACTIVE, armed, and
    no halt placed."""
    control = _mapping(inputs.get("runtime_control"))
    if not control:
        return _component(None, NOT_REPORTED)
    if control.get("fail_closed"):
        return _component(False, "CONTROL_FAIL_CLOSED")
    mode = control.get("mode")
    if mode == KILLED:
        return _component(False, "RUNTIME_KILLED")
    if mode == PAUSED:
        return _component(False, "RUNTIME_PAUSED")
    if mode != ACTIVE:
        # No mode: the state could not be read, and the leg's own read would refuse (BLOCKED).
        return _component(False, "CONTROL_UNREADABLE")
    # A named halt before the arm, as `ControlState.trading_allowed` refuses on either (PR6). Any
    # level but SOFT reads HARD, the way `control.halt_level_of` reads the file.
    level = control.get("halt_level")
    if level is not None:
        return _component(False, "SOFT_HALT" if level == HALT_SOFT else "HARD_HALT")
    if not control.get("trading_armed"):
        return _component(False, "TRADING_DISARMED")
    return _component(True, "ACTIVE_ARMED")


def _armed_component(status: Mapping[str, Any], inputs: Mapping[str, Any]) -> dict[str, Any]:
    """How many armed strategies the gate would accept. An unreadable pool is refused by the entry
    door, so it is False, not unknown."""
    armed = _mapping(status.get("live_armed_strategies"))
    if not armed:
        return _component(None, NOT_REPORTED, count=None, armed=None)
    if not armed.get("known"):
        return _component(False, "POOL_UNREADABLE", count=None, armed=None)
    raw = _count(armed.get("armed"))
    tradable = _count(inputs.get("tradable_armed"))
    if tradable is None:
        return _component(None, NOT_REPORTED, count=None, armed=raw)
    if tradable > 0:
        return _component(True, "ARMED", count=tradable, armed=raw)
    return _component(False, "ARMED_CANNOT_TRADE" if raw else "NONE_ARMED", count=0, armed=raw)


def _venue_component(status: Mapping[str, Any]) -> dict[str, Any]:
    """The venue contract (PR4b): a usable PASS. The door judges each entry's own symbol, so a PASS
    that covers some of the budget's symbols admits those (review of #906): True, naming the rest in
    ``uncovered``, where the check row — "every budget symbol covered" — fails. False only when the
    PASS is not usable or covers none of them."""
    row = _check_ok(status, "venue_contract")
    if row is None:
        return _component(None, NOT_REPORTED)
    if row:
        return _component(True, "USABLE")
    contract = _mapping(status.get("venue_contract"))
    if contract.get("error"):
        return _component(False, "CONTRACT_UNREADABLE")
    if not contract.get("usable"):
        return _component(False, str(contract.get("refusal") or "CONTRACT_NOT_USABLE"))
    budget_symbols, uncovered = contract.get("budget_symbols"), contract.get("uncovered")
    if (isinstance(budget_symbols, list) and isinstance(uncovered, list)
            and 0 < len(uncovered) < len(budget_symbols)):
        return _component(True, "USABLE_PARTIAL", uncovered=[str(s) for s in uncovered])
    return _component(False, "BUDGET_SYMBOL_UNCOVERED")


def _risk_component(status: Mapping[str, Any], inputs: Mapping[str, Any], *,
                    opted: bool) -> dict[str, Any]:
    """Every breaker and record the entry door refuses on, the C4 loss breakers, today's realized loss
    and the daily order cap. Several can refuse at once, and each is named."""
    refused: list[str] = []
    unknown: list[str] = []
    for row, code in _RISK_ROWS:
        ok = _check_ok(status, row)
        if ok is None:
            unknown.append(NOT_REPORTED)
        elif not ok:
            refused.append(code)
    # The entry path appends to the pre-order snapshot record and refuses when it cannot: a damaged
    # line. A row that parses but fails its seal fails the check row — the record no longer proves why
    # past orders left — and refuses no entry (review of #906).
    ok = _check_ok(status, "pre_order_snapshots")
    if ok is None:
        unknown.append(NOT_REPORTED)
    elif not ok and _mapping(status.get("pre_order_snapshots")).get("appendable") is not True:
        refused.append("PRE_ORDER_SNAPSHOTS_UNREADABLE")
    c4 = _mapping(inputs.get("c4"))
    if not c4:
        unknown.append(NOT_REPORTED)
    elif c4.get("allow") is None:
        unknown.append("C4_UNREADABLE")
    elif not c4.get("allow"):
        refused.append("C4_BREAKER")
    account = _mapping(inputs.get("account"))
    if opted or account.get("source") == SOURCE_THIS_PROCESS:
        # Measured here, from this process's own account read — the row. The trading process always,
        # and any process holding the account feed (review of #906).
        ok = _check_ok(status, "daily_loss_breaker")
        if ok is None:
            unknown.append(NOT_REPORTED)
        elif not ok:
            refused.append("DAILY_LOSS")
    else:
        breached = account.get("daily_loss_breached")
        if breached is None:
            unknown.append("DAILY_LOSS_UNMEASURED")
        elif breached:
            refused.append("DAILY_LOSS_FIGURE_MISSING"
                           if account.get("loss_error") == LIVE_PNL_VENUE_FIGURE_MISSING
                           else "DAILY_LOSS_BREACHED")
    cap = _mapping(inputs.get("daily_order_cap"))
    submitted, limit = _count(cap.get("submitted_today")), _count(cap.get("cap"))
    if not cap:
        unknown.append(NOT_REPORTED)
    elif cap.get("error"):
        refused.append("ORDER_COUNTER_UNREADABLE")
    elif submitted is not None and limit is not None and limit > 0 and submitted >= limit:
        # A cap of zero is a budget that backs nothing, which BUDGET_INVALID already names.
        refused.append("DAILY_ORDER_CAP")
    if refused:
        return _component(False, "+".join(dict.fromkeys(refused)))
    if unknown:
        return _component(None, "+".join(dict.fromkeys(unknown)))
    return _component(True, "CLEAR")


def _fire_unknown(fire: Mapping[str, Any]) -> str | None:
    """Why the last fire cannot be read, or None when it can."""
    if not fire:
        return NOT_REPORTED
    if not fire.get("known") or not _count(fire.get("contexts")):
        return "RECORD_UNREADABLE" if fire.get("error") else "NO_RECORD"
    return None


def _fire_unseen(fire: Mapping[str, Any]) -> str | None:
    """Why the last fire cannot speak for the next one, or None when it can: none readable, or one too
    old or undatable — which `trading_cycle_recent` judges, and names as the refusal."""
    missing = _fire_unknown(fire)
    if missing is not None:
        return missing
    if fire.get("recent") is None:
        return "RECORD_UNDATED"
    if not fire.get("recent"):
        return "NO_RECENT_CYCLE"
    return None


def _majority(fire: Mapping[str, Any], count: Any) -> bool:
    """The pipeline's stall rule (`pool_cycle_is_stalled`, Thomas 2026-08-22), for every refusal the
    last fire recorded per context: half or more of its contexts. One flaky context of thirteen does
    not make a component False; the other twelve still enter."""
    contexts, count = _count(fire.get("contexts")) or 0, _count(count) or 0
    return contexts > 0 and count > 0 and 2 * count >= contexts


def _account_component(inputs: Mapping[str, Any], *, opted: bool) -> dict[str, Any]:
    account = _mapping(inputs.get("account"))
    if not account:
        return _component(None, NOT_REPORTED)
    if opted and not account.get("configured"):
        # The leg reads the account before it enters and refuses without it.
        return _component(False, "ACCOUNT_FEED_NOT_CONFIGURED", source=SOURCE_THIS_PROCESS)
    if account.get("source") == SOURCE_THIS_PROCESS:
        if account.get("readable"):
            return _component(True, "READ", source=SOURCE_THIS_PROCESS)
        return _component(False, _ACCOUNT_UNREADABLE, source=SOURCE_THIS_PROCESS)
    # The trading process's own reads at its last fire decide before its snapshot does: the store keeps
    # the previous snapshot when a read fails, so a fresh snapshot is no evidence the account reads now
    # (review of #906).
    fire = _mapping(inputs.get("recorded_fire"))
    if _fire_unseen(fire) is None and _majority(fire, fire.get("account_unreadable")):
        return _component(False, _ACCOUNT_UNREADABLE, source=SOURCE_RECORDED)
    if not account.get("recorded"):
        return _component(None, "SNAPSHOT_MISSING", source=SOURCE_RECORDED)
    if account.get("stale"):
        return _component(None, "SNAPSHOT_STALE", source=SOURCE_RECORDED)
    return _component(True, "SNAPSHOT_FRESH", source=SOURCE_RECORDED)


def _cycle_component(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Whether the trading process is still firing: entries happen only inside a cycle (review of
    #906). False when no pipeline schedule is enabled, or when the last fire is older than three of the
    schedule's intervals — a scheduler that stopped, or a schedule turned off. No door refuses on it:
    nothing runs to enter, and False is the safe direction (the model's second coarser judgement)."""
    window = _mapping(inputs.get("cycle_window"))
    if not window:
        return _component(None, NOT_REPORTED)
    if window.get("scheduled") is False:
        return _component(False, "PIPELINE_DISABLED")
    fire = _mapping(inputs.get("recorded_fire"))
    unseen = _fire_unseen(fire)
    if unseen == "NO_RECENT_CYCLE":
        return _component(False, unseen)
    if unseen is not None:
        return _component(None, unseen)
    return _component(True, "RECENT")


# Counted per context under the first reason its data is refused, in this order.
_DATA_REFUSALS = (("SYNTHETIC", "synthetic"), ("DEGRADED", "degraded"), ("DATA_HEALTH", "data_health"),
                  ("OPTIONAL_DATA", "optional_data"))


def _market_data_component(status: Mapping[str, Any], inputs: Mapping[str, Any], *,
                           opted: bool) -> dict[str, Any]:
    """Whether the last fire's contexts had data the entry door trades on, by the stall rule
    (`_majority`) — though a minority may still enter. The reason names the commonest refusal."""
    if opted and _check_ok(status, "market_data_visibility") is False:
        return _component(False, "MARKET_DATA_NOT_CONFIGURED")
    fire = _mapping(inputs.get("recorded_fire"))
    unseen = _fire_unseen(fire)
    if unseen is not None:
        return _component(None, unseen)
    refused = {kind: _count(fire.get(key)) or 0 for kind, key in _DATA_REFUSALS}
    if _majority(fire, sum(refused.values())):
        commonest = max(refused, key=lambda kind: refused[kind])
        return _component(False, f"{_MAJORITY}{commonest}")
    return _component(True, "FRESH")


def _reconciliation_component(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Whether the leg can reconcile the book with the venue. The book itself, read now as the leg reads
    it first: a record it cannot read or attribute refuses the whole leg. Then the last fire: an
    INCIDENT or a halt there halts the next pass too, until the book and the venue agree; and legs
    refused whole before any venue action (BLOCKED), by the stall rule, name the commonest refusal —
    they settled, protected and entered nothing (review of #906)."""
    if _mapping(inputs.get("position_book")).get("readable") is False:
        return _component(False, "POSITION_BOOK_UNREADABLE")
    fire = _mapping(inputs.get("recorded_fire"))
    unseen = _fire_unseen(fire)
    if unseen is not None:
        return _component(None, unseen)
    if fire.get("incident"):
        return _component(False, "INCIDENT")
    if _majority(fire, fire.get("blocked")):
        code = fire.get("blocked_code")
        return _component(False, f"{_LEG_BLOCKED}_{code}" if isinstance(code, str) and code else _LEG_BLOCKED)
    return _component(True, "CLEAR")


def _stall_rule_refusal(name: str, component: Mapping[str, Any]) -> bool:
    """Whether this refusal is one of the stall rule's (`_majority`): False for most of the last fire's
    contexts rather than for every entry. The account read counts only as the trading process recorded
    it; this process's own failed read refuses its every entry."""
    reason = component.get("reason")
    if not isinstance(reason, str):
        return False
    if name == "market_data_ready":
        return reason.startswith(_MAJORITY)
    if name == "reconciliation_ready":
        return reason == _LEG_BLOCKED or reason.startswith(_LEG_BLOCKED + "_")
    if name == "account_ready":
        return reason == _ACCOUNT_UNREADABLE and component.get("source") == SOURCE_RECORDED
    return False


def minority_may_enter(state: Mapping[str, Any]) -> bool:
    """True when the answer is False by the stall rule alone: most of the last fire's contexts were
    refused, and the rest may still open a position at the next cycle. Any other refusal refuses every
    entry, and then this is False (review of #907: "no autonomous entry can open now" overclaimed)."""
    components = _mapping(state.get("components"))
    refused = [(name, c) for name, c in components.items() if isinstance(c, Mapping) and c.get("ok") is False]
    return bool(refused) and all(_stall_rule_refusal(name, c) for name, c in refused)


def readiness_state(status: Mapping[str, Any]) -> dict[str, Any]:
    """The readiness state (model v2): each component with its value and reason, and
    `live_entry_possible` as their three-valued AND. Pure over a board, and never raises: a fact the
    board does not carry is ``None`` (``NOT_REPORTED``), never a guess."""
    inputs = _mapping(status.get("readiness_inputs"))
    # A process that carries the live-trading environment is the trading process: its own switches
    # and its own account read decide. Everywhere else the trading process's record does.
    opted = _check_ok(status, "live_trading_opt_in") is True
    components = {
        "live_capability_installed": _capability_component(status),
        "execution_stage": _stage_component(status),
        "live_gate_open": _gate_component(status, inputs, opted=opted),
        "runtime_control": _control_component(inputs),
        "armed_strategy_count": _armed_component(status, inputs),
        "venue_ready": _venue_component(status),
        "risk_ready": _risk_component(status, inputs, opted=opted),
        "account_ready": _account_component(inputs, opted=opted),
        "trading_cycle_recent": _cycle_component(inputs),
        "market_data_ready": _market_data_component(status, inputs, opted=opted),
        "reconciliation_ready": _reconciliation_component(inputs),
    }
    blocking = [f"{name}:{c['reason']}" for name, c in components.items() if c["ok"] is False]
    unknown = [f"{name}:{c['reason']}" for name, c in components.items() if c["ok"] is None]
    possible: bool | None = False if blocking else (None if unknown else True)
    return {
        "model": READINESS_MODEL,
        "live_entry_possible": possible,
        "blocking": blocking,
        "unknown": unknown,
        "components": components,
    }


def readiness_data(status: Mapping[str, Any]) -> dict[str, Any]:
    """The board's structured view — the facts a reader must keep apart, as fields a JSON
    consumer reads instead of parsing the rendered rows (sequence 2, P02).

    One word cannot carry what this board says. These fields keep the meanings apart:

    * ``infrastructure_ready`` — every check row ok, i.e. "can THIS process place a live
      order". Read through the assistant door it describes that door's container, which
      carries no ``MVP_LIVE_*``; ``env_scope`` and ``env_out_of_scope`` say so.
    * ``live_armed_strategies`` — how many occupying strategies are ``live_tier=LIVE``.
      Zero armed beside ``ready: true`` is this host's normal state (2026-09-13 review) and
      the one a summariser turns into "live trading is on".
    * ``recorded_gate`` — the trading process's own last word about its gate, dated, with
      the ``stale`` verdict the text banner uses.
    * ``live_entry_possible`` — whether the autonomous leg may open a real position at its next
      cycle: the three-valued AND of the readiness state's components (model v2, PR5a — see
      `readiness_state`). ``False`` names what refuses in ``readiness.blocking``; ``None`` names
      what this process cannot see in ``readiness.unknown``, and is never a guess in either
      direction. Until PR5a this was four facts — armed, the recorded gate, the stage, the
      contract — and read True under a kill or a disarm (FO-10).
    * ``readiness`` — the components themselves, each ``{ok: true|false|null, reason}``.
    """
    armed = status.get("live_armed_strategies") or {}
    gate = status.get("recorded_gate") or {}
    state = readiness_state(status)
    guard = status.get("guard_dry_run") or {}
    return {
        "as_of": status.get("created_at"),
        "infrastructure_ready": bool(status.get("ready")),
        "env_scope": "this_process",
        "env_out_of_scope": env_out_of_scope(status),
        "checks": [
            {"check": check.get("check"), "ok": bool(check.get("ok"))} for check in _rows(status)
        ],
        "live_armed_strategies": {
            "known": bool(armed.get("known")),
            "armed": armed.get("armed"),
            "occupying": armed.get("occupying"),
            "error": armed.get("error"),
        },
        "recorded_gate": {
            "known": bool(gate.get("known")),
            "open": gate.get("open"),
            "status": gate.get("status"),
            "recorded_at": gate.get("recorded_at"),
            "age_seconds": gate.get("age_seconds"),
            "stale": bool(gate.get("stale")),
        },
        "live_entry_possible": state["live_entry_possible"],
        "readiness": {key: state[key] for key in ("model", "blocking", "unknown", "components")},
        # The stage record's own answer: which rung, whether it binds, whether it admits a new
        # entry, and that the doors read it (crypto PR1a/PR1b).
        "execution_stage": status.get("execution_stage"),
        # The venue contract sentinel's last decided answer (PR4a); `usable` is what the mainnet doors
        # require of every entry (PR4b), for the entry's own symbol among `symbols`.
        "venue_contract": {
            key: (status.get("venue_contract") or {}).get(key)
            for key in ("error", "recorded", "status", "verified_at", "age_seconds", "stale",
                        "version_current", "usable", "failed_checks", "symbols")
        },
        "guard_dry_run_status": guard.get("status"),
        "submitted_today": status.get("submitted_today"),
    }


def _opted_in(status: Mapping[str, Any]) -> bool:
    return _check_ok(status, "live_trading_opt_in") is True


def contradicts_recorded_gate(status: Mapping[str, Any]) -> bool:
    """True when this board's env rows say "off" and the trading process says otherwise.

    The single state this board must never render as a plain "live trading off": the reader is
    on a console that cannot see the money path, and the process that can was trading when it
    last wrote. A stale or unknown record does NOT qualify — an old record is not evidence about
    now, and inventing a contradiction from one would trade this false negative for a false
    positive.
    """
    recorded = status.get("recorded_gate") or {}
    return (
        not _opted_in(status)
        and bool(recorded.get("known"))
        and bool(recorded.get("open"))
        and not recorded.get("stale")
    )


def env_out_of_scope(status: Mapping[str, Any]) -> bool:
    """True when this board's env rows describe only this container (crypto PR5b, FC-10).

    A process without the live-trading environment cannot see the trading process's, so its env
    rows are a fact about itself. The one exception is the trading process's own fresh record of a
    CLOSED gate: then "live trading off" is true of the system as well, and the rows say it.

    Until PR5b only :func:`contradicts_recorded_gate` qualified — a fresh record of an OPEN gate —
    on the argument that absence of evidence is no licence to soften a row. That was right while
    these rows were the board's conclusion. They are not any more: whether a live entry can open is
    the readiness state's answer (PR5a), at the top of the board and in its last line. What was
    left was the harm: a console whose last record had gone stale — the state a kill leaves behind,
    since a killed runtime writes no more cycles — printed "MVP_LIVE_TRADING is not 'real' (live
    trading off)" off its own empty environment. Right by accident after a kill, for the wrong
    reason, in the one sentence a summariser lifts (the 2026-08-10 misreport). ``ready`` and
    ``checks`` are untouched: this decides what the board SAYS, never what it permits.
    """
    recorded = status.get("recorded_gate") or {}
    closed_now = bool(recorded.get("known")) and not recorded.get("stale") and not recorded.get("open")
    return not _opted_in(status) and not closed_now
