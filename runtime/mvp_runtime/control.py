"""R4 Operator emergency console — pause / stop_task / kill / resume / status.

Governance (`governance/GOVERNANCE_POLICY.yaml`): the `control_channel.local_operator_console`
may `pause` / `stop_task` / `kill` / `status` / `audit` / `recovery`, and the `kill_switch`
block fixes the semantics:

- **kill blocks** new/pending/external execution and tool/program/scheduler writes;
- **kill allows** only read-only status and audit reads;
- **resume requires the authenticated operator** (`resume_requires_thomas_authentication`);
- the agent/runtime can **never** disable or bypass the control (`agent_can_disable_or_bypass:
  false`) — nothing on the run path clears a pause/kill, only an explicit operator command
  (through the verified Telegram control channel or the local host console) does.

The control state is **local, per-machine, gitignored runtime state** — like the Core pointer,
the safety-flag activation, and the operator registration — a single JSON object at
`.runtime_governance_state/operator_control_state.json`.

Fail-closed direction is deliberate for a safety control:
- a **missing** file means ACTIVE (absence of any stop order is "running", not "dead" — a fresh
  deployment must not be bricked by the mere lack of a control file);
- a **present but unreadable/invalid** file means KILLED (uncertainty about a safety state must
  fail to the safe, non-executing state, and demand manual recovery — a corrupt file can never
  silently re-enable execution).

Every state change is recorded to the durable ledger as a tamper-evident control event.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runtime.read_only_kernel import integrity

from . import jsonl, timeutil
from .events import stamped_event
from .audit import AUDIT_GAP_TYPE, verify_audit_chain
from .errors import ControlBlocked, MvpRuntimeError
from .paths import repo_root as _repo_root

CONTROL_STATE_REL = ".runtime_governance_state/operator_control_state.json"
RECORD_TYPE = "operator_control_state.v0"
CONTROL_EVENT_TYPE = "operator_control_event.v0"

# Control modes. ACTIVE is the only mode in which the runtime will start a task.
ACTIVE = "ACTIVE"
PAUSED = "PAUSED"
KILLED = "KILLED"
_MODES = frozenset({ACTIVE, PAUSED, KILLED})

# Console commands (the leading slash is optional on the local CLI).
CMD_STATUS = "status"
CMD_PAUSE = "pause"
CMD_KILL = "kill"
CMD_RESUME = "resume"
CMD_STOP = "stop"
# The remaining two verbs the Governance Policy allows the local operator console
# (`emergency_controls_allowed: [pause, stop_task, kill, status, audit, recovery]`). Both are
# read-only: `audit` reads and verifies the trail, `recovery` diagnoses local state. Neither
# repairs anything — see `recovery_lines` for why repair is not on the table.
CMD_AUDIT = "audit"
CMD_RECOVERY = "recovery"
COMMANDS = frozenset({CMD_STATUS, CMD_PAUSE, CMD_KILL, CMD_RESUME, CMD_STOP, CMD_AUDIT, CMD_RECOVERY})
_ALIASES = {"stop_task": CMD_STOP}

# How many recent events `audit` shows by default. The verification always covers the whole
# chain; this only bounds the excerpt printed back.
AUDIT_TAIL_DEFAULT = 10
AUDIT_TAIL_MAX = 100

# How much of an operator-typed reason is recorded. The reason lands in the local state file
# and in every control event on the durable ledger, so it is bounded — but truncation is
# stated in the reply rather than silent, like every other substitution on this channel.
MAX_REASON_CHARS = 300


@dataclass(frozen=True)
class ControlState:
    """The runtime's operator-control state. Immutable; a transition builds a new one."""

    mode: str = ACTIVE
    updated_by: str = "system"
    updated_at: str = ""
    reason: str = "default active state (no operator stop in effect)"
    stop_requested_task_ids: tuple[str, ...] = ()
    # True when this state was *derived* by failing closed (unreadable file, or a missing
    # file whose last ledger event said the runtime was stopped) rather than read from a
    # written state. `recovery` used to detect that by substring-matching the reason prose,
    # so an operator kill with `--reason "fail-closed test"` printed the wrong guidance.
    fail_closed: bool = False
    # Whether the LIVE order path may OPEN a position. A second dimension rather than a fourth
    # mode, because it is orthogonal to the three: a runtime can be ACTIVE with trading held
    # down, and that state has no spelling in `mode`. Only two consumers read it (`live_route`
    # via `trading_allowed`, and the readiness board, which reports it); every other consumer
    # keeps reading `execution_allowed` and is unaffected.
    #
    # It does NOT gate closing. `live_route._run_gated_live_leg` settles and protects before it
    # reads control state at all, deliberately — "a halt that traps an open position is worse
    # than what the halt prevents" — so a disarmed runtime still exits its positions.
    trading_armed: bool = True

    @classmethod
    def active_default(cls, *, now: str | None = None) -> "ControlState":
        return cls(mode=ACTIVE, updated_by="system", updated_at=now or "", reason="default active state (no operator stop in effect)")

    @property
    def execution_allowed(self) -> bool:
        """Only ACTIVE lets the runtime start a task; PAUSED and KILLED both refuse."""
        return self.mode == ACTIVE

    @property
    def trading_allowed(self) -> bool:
        """A live ENTRY needs both: the runtime running, and trading armed.

        The conjunction lives here rather than at the call site so the two facts cannot be
        read separately and combined differently by a second caller later."""
        return self.execution_allowed and self.trading_armed

    def refusal_reason_code(self) -> str:
        """The ONE reason-code vocabulary for a kill-switch refusal, mode-aware.

        Every execution door refuses the same governance condition
        (``execution_allowed`` False), but half of them said KILL_SWITCH_ACTIVE and the
        other half RUNTIME_KILLED/RUNTIME_PAUSED — an operator grepping the ledger or
        stderr for a paused-run refusal had to know both spellings. One helper, one
        vocabulary, and it keeps the mode distinction (a kill and a pause are different
        operator actions with different resume stories)."""
        return "RUNTIME_KILLED" if self.mode == KILLED else "RUNTIME_PAUSED"

    def as_record(self) -> dict[str, Any]:
        return {
            "record_type": RECORD_TYPE,
            "mode": self.mode,
            "updated_by": self.updated_by,
            "updated_at": self.updated_at,
            "reason": self.reason,
            "stop_requested_task_ids": list(self.stop_requested_task_ids),
            "trading_armed": self.trading_armed,
        }


def status_lines(state: ControlState, *, ledger: Any | None = None) -> str:
    """A short human-readable status report (the read-only `status` command output).

    When a ``ledger`` is given, the reply carries the audit chain's current tip hash.
    That one line is the cheap external anchor for the chain's documented blind spot: a
    PREFIX of a valid chain verifies clean, so on-machine tampering that truncates the
    tail is invisible to `/audit` alone — but every `/status` answered over Telegram
    leaves the tip in an off-machine chat history, and a later tip that does not descend
    from an anchored one is the truncation signal. Reading the tip must never take the
    status answer down with it (`kill_allows: read_only_status`): an unreadable ledger
    reports as exactly that."""
    lines = [
        f"mode: {state.mode}",
        f"execution: {'allowed' if state.execution_allowed else 'BLOCKED'}",
        f"updated_by: {state.updated_by}",
        f"updated_at: {state.updated_at or 'n/a'}",
        f"reason: {state.reason}",
    ]
    # Named "live entries" and not "trading": paper is deliberately NOT gated on this, so an
    # operator reading `disarmed` must not conclude the research loop stopped too.
    lines.append(f"live entries: {'armed' if state.trading_armed else 'DISARMED'}")
    if state.stop_requested_task_ids:
        lines.append("stop_requested_task_ids: " + ", ".join(state.stop_requested_task_ids))
    if ledger is not None:
        try:
            tip = ledger.last_audit_hash()
        except MvpRuntimeError as exc:
            lines.append(f"audit_tip: unavailable ({exc.reason_code})")
        else:
            lines.append(f"audit_tip: {tip or 'none (empty ledger)'}")
    return "\n".join(lines)


def parse_count_arg(arg: Any, *, default: int, maximum: int, usage: str) -> tuple[int, str]:
    """Read an operator-typed count off a read-only console verb. Returns ``(count, note)``,
    where ``note`` is empty when the argument was absent or used exactly as typed.

    A read-only diagnostic is never *denied* over a typo — ``/audit`` is the command an
    operator runs when things are already broken, and it must answer while KILLED — but a
    substituted number is never silent either. ``/audit abc`` used to show the default 10
    events as though ``abc`` had been the request, and ``/audit 500`` showed 100: in both
    cases the operator was looking at a different window than the one they asked for and had
    no way to tell. Answer with the clamped count and say what happened.

    The same shape of typo used to be a typed refusal on ``/history`` and a silent default
    here. One helper for both, so the two read verbs on the same channel cannot drift on what
    a mistyped count means (``command_verb`` is shared for the same reason)."""
    if arg is None or not str(arg).strip():
        return default, ""
    raw = str(arg).strip().split()[0]
    try:
        requested = int(raw)
    except (TypeError, ValueError):
        return default, f"'{raw}'는 숫자가 아니라 기본값 {default}개를 보여드립니다 — {usage}"
    count = max(1, min(requested, maximum))
    if count != requested:
        return count, f"{requested}개는 범위를 벗어나 {count}개로 조정했습니다 (1–{maximum})."
    return count, ""


def with_note(reply: str, note: str) -> str:
    """Append a substitution note to a console reply, when there is one."""
    return f"{reply}\n\n({note})" if note else reply


def _stated_reason(reason: str, arg: Any) -> str:
    """The operator's own words for a pause/kill/resume, from either door.

    The local console passes ``--reason``; over Telegram there are no options, so the text
    after the verb IS the reason (``/kill 시장 급변으로 중단``). That tail used to be parsed and
    then dropped, which recorded 'killed by operator' in the state file and on the ledger and
    lost the one field that answers *why* the runtime was stopped. An explicit ``reason=``
    still wins — it is the caller being deliberate, not a channel artifact."""
    stated = reason.strip() if isinstance(reason, str) else ""
    if not stated and isinstance(arg, str):
        stated = arg.strip()
    return " ".join(stated.split())[:MAX_REASON_CHARS]


def _audit_gap_summary(ledger: Any) -> list[dict[str, Any]]:
    """Recorded audit gaps from the block ledger, newest last; empty on any read problem.

    Deliberately silent on failure: this enriches a diagnosis, and `recovery` already
    reports an unreadable store in its own section — raising here would break the exact
    command an operator runs when things are broken."""
    read = getattr(ledger, "read_blocks", None)
    if read is None:
        return []
    try:
        entries = read()
    except MvpRuntimeError:
        return []
    return [e for e in entries
            if isinstance(e, dict) and e.get("record_type") == AUDIT_GAP_TYPE]


def audit_lines(ledger: Any | None, *, limit: int = AUDIT_TAIL_DEFAULT) -> str:
    """Verify the audit chain and render the verdict plus the most recent events.

    This is the read half of a promise the runtime has been making since R2.6: the ledger is
    "append-only and hash-chained, therefore tamper-evident". It builds that chain on every
    run, but nothing ever checked it — tamper-evidence you never look at is a description,
    not a property. `audit` is the looking.

    Verification always covers the WHOLE chain; ``limit`` only bounds the excerpt shown.
    A corrupt ledger is reported, not raised: the operator reaching for `audit` is often
    already in trouble, and "it broke while telling you it broke" helps nobody.
    """
    if ledger is None:
        return "audit: no ledger available in this context."
    try:
        events = ledger.read_audit_events()
    except MvpRuntimeError as exc:
        return (
            f"audit: LEDGER UNREADABLE ({exc.reason_code})\n"
            f"  {exc.reason}\n"
            "  The trail cannot be verified. Run `recovery` for a full state diagnosis."
        )

    report = verify_audit_chain(events)
    lines = [
        f"audit: chain {'INTACT' if report['intact'] else 'BROKEN'} over {report['checked']} event(s)",
    ]
    if not report["intact"]:
        lines.append(f"  first break at index {report['first_break_index']} — the trail is NOT trustworthy:")
        for item in report["breaks"][:5]:
            lines.append(f"   [{item['index']}] {item['check']}: {item['detail']}")
        if len(report["breaks"]) > 5:
            lines.append(f"   ... and {len(report['breaks']) - 5} more")
        lines.append("  Audit records are append-only and corrections are new events —")
        lines.append("  do NOT edit or delete the ledger to 'fix' this (that is audit concealment).")

    tail = events[-limit:]
    if tail:
        lines.append(f"  last {len(tail)} event(s):")
        for event in tail:
            lines.append("   " + _event_summary(event))
    else:
        lines.append("  (no events recorded yet)")
    return "\n".join(lines)


def _event_summary(event: Any) -> str:
    """One rendered line for an audit event, tolerant of a corrupt-but-parseable record.

    A tampered line can be valid JSON with the wrong *shapes* (``"event": 5``), and the
    naive ``event.get("event", {}).get(...)`` chain raised AttributeError on it — so the
    diagnostic died exactly when it was needed, and (since only ControlBlocked is caught
    upstream) took the operator loop down with it. The rendering of a malformed record is
    a malformed-record line, never an exception."""
    if not isinstance(event, dict):
        return "(unreadable event: not an object)"
    detail = event.get("event")
    detail = detail if isinstance(detail, dict) else {}
    codes = detail.get("reason_codes")
    codes = [str(c) for c in codes[:3]] if isinstance(codes, list) else []
    shape = "" if isinstance(event.get("event"), dict) else "  [MALFORMED EVENT BLOCK]"
    return (
        f"{event.get('created_at')}  {event.get('event_type')}  "
        f"{detail.get('outcome')}  {','.join(codes)}{shape}"
    )


def recovery_lines(state: ControlState, ledger: Any | None) -> str:
    """Diagnose the local runtime state and name the safe operator action for each fault.

    **This diagnoses; it does not repair.** Two reasons, and both are the point:

    - Repairing the audit ledger would BE the thing the governance blocks
      (``audit_concealment``): a damaged trail is evidence, and truncating it to make the
      runtime start again destroys exactly what it exists to preserve.
    - Rollback/recovery proper is modelled only by ``ROLLBACK_RECOVERY_CONTRACT_V0.1`` and
      ``RUNTIME_ENTRY_CRASH_RECOVERY_CONTRACT_V0.1``, both pinned inside DEFERRED_DISABLED
      families (the first requires the deferred ``execution_request.v0.1``, the second is
      ``SYNTHETIC_TEST_ONLY`` over a SQLite store the MVP does not use). Nothing here may
      claim to perform it.

    What it is for: the runtime fails closed on corrupt local state, which is correct but
    leaves the operator with a reason code and no idea what to do. This turns that into a
    precise diagnosis.
    """
    lines = [
        f"recovery: read-only diagnosis (nothing below is modified)",
        "",
        f"control state: {state.mode} — execution {'allowed' if state.execution_allowed else 'BLOCKED'}",
        f"  reason: {state.reason}",
    ]
    if state.fail_closed:
        # The one genuinely stuck state the live runtime has, and its exit is already built.
        lines.append("  -> the control state is unreadable/corrupt, so it reads as KILLED (fail-closed).")
        lines.append("     `resume` (as the authenticated operator) writes a fresh ACTIVE state and clears it.")
    elif not state.execution_allowed:
        lines.append("  -> an operator stop is in effect. `resume` clears it; only Thomas may.")

    if state.fail_closed and state.reason and "no control-state file" in state.reason:
        lines.append("     (the state FILE is missing; the mode above was recovered from the")
        lines.append("      control-event ledger, so a deleted state file did not clear the stop.)")

    lines.append("")
    if ledger is None:
        lines.append("ledger: not available in this context.")
        return "\n".join(lines)

    lines.append("local stores:")
    corrupt = []
    for entry in ledger.health():
        count = "—" if entry["count"] is None else str(entry["count"])
        lines.append(f"  {entry['kind']:17} {entry['status']:7} {count:>6}"
                     + (f"  ({entry['detail']})" if entry["detail"] and entry["status"] != "ABSENT" else ""))
        if entry["status"] == "CORRUPT":
            corrupt.append(entry["kind"])

    gaps = _audit_gap_summary(ledger)
    if gaps:
        lines.append("")
        lines.append(f"KNOWN AUDIT GAPS: {len(gaps)} recorded.")
        lines.append("  Something happened whose audit event could not be written (the record")
        lines.append("  itself is durable; only its trail entry is missing). These are recorded")
        lines.append("  deliberately, so the hole is answerable instead of silent:")
        for gap in gaps[-5:]:
            lines.append(f"   {gap.get('created_at')}  {gap.get('gap_kind')}  "
                         f"{gap.get('reason_code')}  {gap.get('subject_ref')}")
        if len(gaps) > 5:
            lines.append(f"   ... and {len(gaps) - 5} more")

    lines.append("")
    if corrupt:
        lines.append(f"FAULT: {', '.join(corrupt)} unreadable.")
        if "audit_events" in corrupt:
            lines.append("  The audit ledger is the tamper-evident record; every run fails closed until it")
            lines.append("  reads. Do NOT truncate or hand-edit it to get moving — that is audit concealment,")
            lines.append("  and it destroys the evidence of whatever went wrong. Preserve the file, take a")
            lines.append("  copy for investigation, and decide deliberately.")
        other = [k for k in corrupt if k != "audit_events"]
        if other:
            lines.append(f"  {', '.join(other)}: enrichment/diagnostic stores, not the audit of record.")
    else:
        lines.append("No faults found: every present store is readable.")
    return "\n".join(lines)


class ControlStore:
    """Load/persist the local control-state file. Single-writer, per-machine, gitignored."""

    def __init__(self, root: Path):
        self._path = Path(root) / CONTROL_STATE_REL

    @classmethod
    def default(cls, root: Path | None = None) -> "ControlStore":
        return cls((root if root is not None else _repo_root()))

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> ControlState:
        """Return the current control state. Fail-closed on uncertainty.

        Present but unreadable, non-object, or carrying an unknown mode -> KILLED (a corrupt
        safety state must not silently permit execution); the returned state names the
        corruption so `status` can report it and an operator can recover.

        **Missing file** still means ACTIVE for a genuinely fresh deployment — but only
        after consulting the durable control-event ledger. Deleting the state file was
        otherwise an unauthenticated, unaudited resume of a KILLED runtime: corrupting the
        file failed closed to KILLED while *removing* it silently cleared the kill, and a
        volume remount or a stray cleanup script does exactly that. See
        :meth:`_mode_from_ledger`."""
        if not self._path.is_file():
            recovered = self._mode_from_ledger()
            if recovered is None:
                return ControlState.active_default()
            return ControlState(
                mode=recovered, updated_by="system", updated_at="",
                reason=(
                    f"fail-closed: no control-state file, but the control-event ledger's last "
                    f"event is {recovered}; deleting the state file does not clear an operator "
                    "stop (resume, as the authenticated operator, to clear it)"
                ),
                fail_closed=True,
                trading_armed=False,
            )
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return self._corrupt_killed("control state file is unreadable")
        if not isinstance(data, dict) or data.get("mode") not in _MODES:
            return self._corrupt_killed("control state file is malformed or has an unknown mode")
        raw_ids = data.get("stop_requested_task_ids", [])
        ids = tuple(str(x) for x in raw_ids) if isinstance(raw_ids, list) else ()
        # ABSENT means armed; MALFORMED means disarmed. The same split this class already makes
        # one level up, for the same reason: a state file written before this field existed is
        # not a machine that was disarmed, it is a machine that predates the question — reading
        # it as a stop order would halt entries on every deployment that upgrades. A value that
        # is *present and unreadable* is genuine uncertainty about a safety state, and that
        # fails closed, exactly like an unknown `mode` does.
        raw_armed = data.get("trading_armed", True)
        armed = raw_armed if isinstance(raw_armed, bool) else False
        return ControlState(
            mode=str(data["mode"]),
            updated_by=str(data.get("updated_by", "unknown")),
            updated_at=str(data.get("updated_at", "")),
            reason=str(data.get("reason", "")),
            stop_requested_task_ids=ids,
            trading_armed=armed,
        )

    def _mode_from_ledger(self) -> str | None:
        """The mode the durable control-event ledger says was last in effect, or None when
        the ledger genuinely has nothing to say (a fresh deployment).

        This is what makes "missing file = ACTIVE" safe. The ledger is the durable record
        of every transition, on the same volume as the state file, so it answers the one
        question deletion was otherwise able to erase: was a stop in effect? Returns None
        only when the ledger is ABSENT (nothing ever happened here); an unreadable ledger
        returns KILLED, because uncertainty about a safety state is not permission.
        """
        ledger_path = self._path.parent / "runtime_ledger" / "control_events.jsonl"
        if not ledger_path.is_file():
            return None                     # fresh deployment: no history to contradict ACTIVE
        try:
            events = jsonl.read_objects(
                ledger_path, read_code="LEDGER_UNREADABLE", label="the control ledger")
        except MvpRuntimeError:
            return KILLED                   # cannot rule out a stop => do not permit execution
        for event in reversed(events):
            mode = event.get("resulting_mode") if isinstance(event, dict) else None
            if mode in _MODES:
                return None if mode == ACTIVE else mode
        return None

    @staticmethod
    def _corrupt_killed(detail: str) -> ControlState:
        return ControlState(
            mode=KILLED,
            updated_by="system",
            updated_at="",
            reason=f"fail-closed: {detail}; manual recovery required (resume to clear)",
            fail_closed=True,
            trading_armed=False,
        )

    def save(self, state: ControlState) -> None:
        """Atomically persist the state (temp file + replace). Fail-closed on write error."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(json.dumps(state.as_record(), ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
            os.replace(tmp, self._path)
        except (OSError, TypeError, ValueError) as exc:
            raise ControlBlocked("CONTROL_WRITE_FAILED", f"could not persist control state: {exc}") from exc


def _control_event(action: str, state: ControlState, *, now: str, task_id: str | None = None) -> dict[str, Any]:
    """Build a tamper-evident control event for the durable ledger."""
    extra = {"task_id": task_id} if task_id is not None else {}
    return stamped_event(
        CONTROL_EVENT_TYPE, action=action, resulting_mode=state.mode,
        actor=state.updated_by, reason=state.reason, created_at=now, **extra,
    )


def command_verb(head: str, *, slash_seen: bool) -> str:
    """Normalize one control-channel command token: strip the optional leading slash,
    lowercase, and drop a Telegram ``@botname`` suffix. Telegram clients append the bot's
    username to a command picked from the command menu (``/kill@thomas_bot``) — the suffix
    is addressing, not part of the verb, and an unstripped ``kill@...`` would miss the
    verb table. One tokenizer shared by the console and approval parsers, so the two
    channels can never drift on what counts as a verb."""
    verb = head.lstrip("/").strip().lower()
    if slash_seen and "@" in verb:
        verb = verb.split("@", 1)[0]
    return verb


def parse_command(text: Any) -> tuple[str, str | None] | None:
    """Parse an operator console command, or return None if the text is not a command.

    Accepts ``/status``, ``/pause``, ``/kill``, ``/resume``, ``/stop <task_id>`` (leading slash
    optional) and the ``stop_task`` alias. The remainder after the verb is the argument."""
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped:
        return None
    head, _, rest = stripped.partition(" ")
    verb = command_verb(head, slash_seen=stripped.startswith("/"))
    verb = _ALIASES.get(verb, verb)
    if verb not in COMMANDS:
        return None
    arg = rest.strip() or None
    return verb, arg


def apply_command(
    store: ControlStore,
    command: str,
    *,
    actor: str,
    now: str | None = None,
    reason: str = "",
    arg: str | None = None,
    ledger: Any | None = None,
    resume_arms: bool = True,
) -> dict[str, Any]:
    """Apply a console command and return ``{reply, mode, changed, action}``.

    ``status`` is read-only (no state change, no ledger event). ``pause``/``kill``/``resume``
    transition the state. ``stop`` records an auditable stop request for the task id in ``arg``
    and **stops nothing** — nothing on any execution path reads ``stop_requested_task_ids``, so
    its reply says so and names ``/cancel`` and ``/kill``, which do. Every state change is
    persisted and, when a ``ledger`` is given, recorded as a durable control event.
    ``resume`` clears any pause/kill — callers must only invoke it for the authenticated
    operator (`resume_requires_thomas_authentication`).

    For ``pause``/``kill``/``resume``, ``arg`` is the operator's stated reason (the text after
    the verb — the only way to give one over Telegram, which has no ``--reason`` option) and is
    recorded on the state and the ledger event unless an explicit ``reason`` overrides it. For
    ``audit`` it is the event count. Whatever this function does with an argument it says so in
    the reply: a count it had to clamp, a reason it recorded, or a reason it could not.

    ``resume_arms`` applies to ``resume`` alone and defaults to True, which is what every caller
    that existed before it did: the local console and the assistant's approved trading re-arm
    both restore live entries, unchanged. Passing False resumes the runtime and leaves the arm
    where it was — the one path that can start the analysis side without starting the money
    side. It cannot be used to *disarm*: False preserves, it does not clear."""
    if command not in COMMANDS:
        raise ControlBlocked("UNKNOWN_COMMAND", f"unknown control command: {command!r}")
    stamp = now or timeutil.utc_now_iso()
    current = store.load()

    if command == CMD_STATUS:
        return {"reply": status_lines(current, ledger=ledger),
                "mode": current.mode, "changed": False, "action": CMD_STATUS}

    # `audit` and `recovery` are read-only, like `status`, and for the same reason they must
    # keep working while PAUSED/KILLED: `kill_allows: [read_only_status, audit_read]`. They
    # are handled before any state is touched and write no ledger event of their own — a read
    # that appends to the log it is reading would race its own chain tip, and `status` set the
    # precedent that a read is not an event.
    if command == CMD_AUDIT:
        limit, note = parse_count_arg(
            arg, default=AUDIT_TAIL_DEFAULT, maximum=AUDIT_TAIL_MAX, usage="사용법: /audit [개수]",
        )
        return {
            "reply": with_note(audit_lines(ledger, limit=limit), note),
            "mode": current.mode, "changed": False, "action": CMD_AUDIT,
        }

    if command == CMD_RECOVERY:
        return {
            "reply": recovery_lines(current, ledger),
            "mode": current.mode, "changed": False, "action": CMD_RECOVERY,
        }

    if command == CMD_STOP:
        if not (isinstance(arg, str) and arg.strip()):
            raise ControlBlocked("MISSING_TASK_ID", "stop requires a task id: /stop <task_id>")
        # The FIRST token is the id; the rest is the operator's reason — the same split
        # `/approve <id> <reason>` and `/result <id>` already use. Taking the whole remainder
        # recorded `/stop treg_a1 급하게 멈춰줘` as a stop request for the task id
        # "treg_a1 급하게 멈춰줘", which can never match anything, with a confirmation that
        # named it back as though it were an id.
        task_id, _, tail = arg.strip().partition(" ")
        stated_stop = _stated_reason(reason, tail)
        pending = tuple(dict.fromkeys((*current.stop_requested_task_ids, task_id)))
        new_state = ControlState(
            mode=current.mode, updated_by=actor, updated_at=stamp,
            reason=stated_stop or f"stop requested for task {task_id}",
            stop_requested_task_ids=pending,
            # Carried, not defaulted. This branch keeps `mode` deliberately; letting the arm
            # fall back to the dataclass default would make an auditable no-op stop request
            # silently re-arm a disarmed runtime.
            trading_armed=current.trading_armed,
        )
        store.save(new_state)
        if ledger is not None:
            ledger.append_control(_control_event(CMD_STOP, new_state, now=stamp, task_id=task_id))
        return {
            # What this verb does and does NOT do. It records an auditable request and stops
            # nothing: `stop_requested_task_ids` is read by no execution path (grep it — only
            # this module writes and displays it), and the old reply's "will apply once R6
            # introduces long-running tasks" never came true. Meanwhile `/cancel <id>` really
            # does cancel a queued task and `/kill` really does halt execution, so an operator
            # who typed /stop and got a confirmation must be pointed at the verb that works
            # rather than left believing this one did something.
            "reply": (
                f"기록했습니다 (감사 로그): {task_id} 중지 요청.\n"
                "다만 이 명령은 실행을 멈추지 않습니다 — 대기 중인 작업은 `/cancel <id>`, "
                "실행 중인 작업은 `/pause` 또는 `/kill` 을 사용하세요."
                + (f"\n(이유 기록: {stated_stop})" if stated_stop else "")
            ),
            "mode": new_state.mode, "changed": True, "action": CMD_STOP,
        }

    # pause/kill/resume: the operator's own words, from `--reason` on the local console or from
    # the text after the verb over Telegram. Recorded on the state and on the ledger event, and
    # echoed back so the operator can see that it landed (and `/status` will still say it later).
    stated = _stated_reason(reason, arg)
    reason_note = f"\n(이유 기록: {stated})" if stated else ""

    if command == CMD_PAUSE:
        if current.mode == KILLED:
            # A kill is the stronger stop, and `pause` is not the verb for clearing one —
            # only `resume` is, and only for the authenticated operator. Downgrading
            # KILLED to PAUSED here changed a safety state in the permissive direction on
            # a command that never asked to.
            return {
                "reply": ("Runtime is KILLED, which already blocks everything /pause would. "
                          "Left as KILLED — /resume (authenticated operator) is the only way out."
                          # Nothing changed, so there was no state to record a reason on. Say
                          # that rather than let the operator assume their words were logged.
                          + ("\n(상태가 그대로이므로 적어주신 이유는 기록되지 않았습니다.)" if stated else "")),
                "mode": KILLED, "changed": False, "action": CMD_PAUSE,
            }
        new_state = ControlState(mode=PAUSED, updated_by=actor, updated_at=stamp,
                                 reason=stated or "paused by operator", stop_requested_task_ids=current.stop_requested_task_ids,
                                 trading_armed=False)
        verb_reply = "Paused. New task requests are refused until /resume." + reason_note
    elif command == CMD_KILL:
        # Stopping disarms in both dimensions and needs no approval to do it. The asymmetry is
        # the existing one: a stop must be cheap, and a start must not be.
        new_state = ControlState(mode=KILLED, updated_by=actor, updated_at=stamp,
                                 reason=stated or "killed by operator", stop_requested_task_ids=current.stop_requested_task_ids,
                                 trading_armed=False)
        verb_reply = ("KILLED. All new/pending execution is blocked; only status and audit reads "
                      "remain. /resume to clear." + reason_note)
    else:  # CMD_RESUME
        # Pending stop requests survive the resume: they are operator intent about specific
        # tasks, not part of the pause/kill they happened to be recorded during. Dropping
        # them silently (the old default-empty tuple) discarded that intent with no event
        # saying so.
        armed = True if resume_arms else current.trading_armed
        new_state = ControlState(mode=ACTIVE, updated_by=actor, updated_at=stamp,
                                 reason=stated or "resumed by operator",
                                 stop_requested_task_ids=current.stop_requested_task_ids,
                                 trading_armed=armed)
        verb_reply = ("Resumed. The runtime is ACTIVE and will accept task requests again."
                      + ("" if armed else
                         "\nLive entries stay DISARMED - this resume did not re-arm trading. "
                         "Open positions still close; paper is unaffected.")
                      + reason_note)

    store.save(new_state)
    if ledger is not None:
        ledger.append_control(_control_event(command, new_state, now=stamp))
    return {"reply": verb_reply, "mode": new_state.mode, "changed": True, "action": command}
