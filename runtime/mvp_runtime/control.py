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

from . import timeutil
from .errors import ControlBlocked
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
COMMANDS = frozenset({CMD_STATUS, CMD_PAUSE, CMD_KILL, CMD_RESUME, CMD_STOP})
_ALIASES = {"stop_task": CMD_STOP}


@dataclass(frozen=True)
class ControlState:
    """The runtime's operator-control state. Immutable; a transition builds a new one."""

    mode: str = ACTIVE
    updated_by: str = "system"
    updated_at: str = ""
    reason: str = "default active state (no operator stop in effect)"
    stop_requested_task_ids: tuple[str, ...] = ()

    @classmethod
    def active_default(cls, *, now: str | None = None) -> "ControlState":
        return cls(mode=ACTIVE, updated_by="system", updated_at=now or "", reason="default active state (no operator stop in effect)")

    @property
    def execution_allowed(self) -> bool:
        """Only ACTIVE lets the runtime start a task; PAUSED and KILLED both refuse."""
        return self.mode == ACTIVE

    def as_record(self) -> dict[str, Any]:
        return {
            "record_type": RECORD_TYPE,
            "mode": self.mode,
            "updated_by": self.updated_by,
            "updated_at": self.updated_at,
            "reason": self.reason,
            "stop_requested_task_ids": list(self.stop_requested_task_ids),
        }


def status_lines(state: ControlState) -> str:
    """A short human-readable status report (the read-only `status` command output)."""
    lines = [
        f"mode: {state.mode}",
        f"execution: {'allowed' if state.execution_allowed else 'BLOCKED'}",
        f"updated_by: {state.updated_by}",
        f"updated_at: {state.updated_at or 'n/a'}",
        f"reason: {state.reason}",
    ]
    if state.stop_requested_task_ids:
        lines.append("stop_requested_task_ids: " + ", ".join(state.stop_requested_task_ids))
    return "\n".join(lines)


class ControlStore:
    """Load/persist the local control-state file. Single-writer, per-machine, gitignored."""

    def __init__(self, root: Path):
        self._path = Path(root) / CONTROL_STATE_REL

    @classmethod
    def default(cls) -> "ControlStore":
        return cls(_repo_root())

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> ControlState:
        """Return the current control state. Fail-closed on uncertainty.

        Missing file -> ACTIVE (no stop order in effect). Present but unreadable, non-object,
        or carrying an unknown mode -> KILLED (a corrupt safety state must not silently permit
        execution); the returned state names the corruption so `status` can report it and an
        operator can recover."""
        if not self._path.is_file():
            return ControlState.active_default()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return self._corrupt_killed("control state file is unreadable")
        if not isinstance(data, dict) or data.get("mode") not in _MODES:
            return self._corrupt_killed("control state file is malformed or has an unknown mode")
        raw_ids = data.get("stop_requested_task_ids", [])
        ids = tuple(str(x) for x in raw_ids) if isinstance(raw_ids, list) else ()
        return ControlState(
            mode=str(data["mode"]),
            updated_by=str(data.get("updated_by", "unknown")),
            updated_at=str(data.get("updated_at", "")),
            reason=str(data.get("reason", "")),
            stop_requested_task_ids=ids,
        )

    @staticmethod
    def _corrupt_killed(detail: str) -> ControlState:
        return ControlState(
            mode=KILLED,
            updated_by="system",
            updated_at="",
            reason=f"fail-closed: {detail}; manual recovery required (resume to clear)",
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
    event: dict[str, Any] = {
        "record_type": CONTROL_EVENT_TYPE,
        "action": action,
        "resulting_mode": state.mode,
        "actor": state.updated_by,
        "reason": state.reason,
        "created_at": now,
    }
    if task_id is not None:
        event["task_id"] = task_id
    event["integrity"] = {"event_sha256": integrity.sha256_record(dict(event))}
    return event


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
    verb = head.lstrip("/").strip().lower()
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
) -> dict[str, Any]:
    """Apply a console command and return ``{reply, mode, changed, action}``.

    ``status`` is read-only (no state change, no ledger event). ``pause``/``kill``/``resume``
    transition the state; ``stop`` records a stop request for ``arg`` (a task id). Every state
    change is persisted and, when a ``ledger`` is given, recorded as a durable control event.
    ``resume`` clears any pause/kill — callers must only invoke it for the authenticated
    operator (`resume_requires_thomas_authentication`)."""
    if command not in COMMANDS:
        raise ControlBlocked("UNKNOWN_COMMAND", f"unknown control command: {command!r}")
    stamp = now or timeutil.utc_now_iso()
    current = store.load()

    if command == CMD_STATUS:
        return {"reply": status_lines(current), "mode": current.mode, "changed": False, "action": CMD_STATUS}

    if command == CMD_STOP:
        if not (isinstance(arg, str) and arg.strip()):
            raise ControlBlocked("MISSING_TASK_ID", "stop requires a task id: /stop <task_id>")
        task_id = arg.strip()
        # The MVP runs each task synchronously to completion within one call, so there is no
        # long-running in-flight task to interrupt. The stop request is still recorded (durably,
        # for audit) and will apply once R6 introduces persistent/long-running tasks.
        pending = tuple(dict.fromkeys((*current.stop_requested_task_ids, task_id)))
        new_state = ControlState(
            mode=current.mode, updated_by=actor, updated_at=stamp,
            reason=reason or f"stop requested for task {task_id}", stop_requested_task_ids=pending,
        )
        store.save(new_state)
        if ledger is not None:
            ledger.append_control(_control_event(CMD_STOP, new_state, now=stamp, task_id=task_id))
        return {
            "reply": (
                f"Recorded stop request for task {task_id}. Note: the MVP runs tasks synchronously, "
                f"so there is no long-running task to interrupt; the request is logged for audit."
            ),
            "mode": new_state.mode, "changed": True, "action": CMD_STOP,
        }

    if command == CMD_PAUSE:
        new_state = ControlState(mode=PAUSED, updated_by=actor, updated_at=stamp,
                                 reason=reason or "paused by operator", stop_requested_task_ids=current.stop_requested_task_ids)
        verb_reply = "Paused. New task requests are refused until /resume."
    elif command == CMD_KILL:
        new_state = ControlState(mode=KILLED, updated_by=actor, updated_at=stamp,
                                 reason=reason or "killed by operator", stop_requested_task_ids=current.stop_requested_task_ids)
        verb_reply = "KILLED. All new/pending execution is blocked; only status and audit reads remain. /resume to clear."
    else:  # CMD_RESUME
        new_state = ControlState(mode=ACTIVE, updated_by=actor, updated_at=stamp,
                                 reason=reason or "resumed by operator")
        verb_reply = "Resumed. The runtime is ACTIVE and will accept task requests again."

    store.save(new_state)
    if ledger is not None:
        ledger.append_control(_control_event(command, new_state, now=stamp))
    return {"reply": verb_reply, "mode": new_state.mode, "changed": True, "action": command}
