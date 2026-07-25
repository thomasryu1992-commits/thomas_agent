"""F1 Task Registry — the coordination ledger for everything the runtime is asked to do.

The runtime could always *run* a request and *audit* it; what it could never do was answer
"what am I working on, what did I already do, and how did it go" without reading a raw
JSONL ledger. ``trace_id`` existed, but a list of tasks with a status did not. This module
is that list, and nothing more.

**Coordination state only.** The task itself stays the authority of ``task.v0.3`` and the
run's records stay in the durable ledger (``store.py``); an entry references them by
``task_id``/``trace_id`` and restates none of their content. That is why ``/result`` can
re-render a delivered analysis without the registry storing a single line of it — the
ledger already has the agent output, and one authority per fact is the repo's rule.

**Zero new governance.** Recording what was asked and how it ended is ``INTERNAL_READ``-tier
bookkeeping over the runtime's own local state: no new contract, registry, gate, permission
scope, or safety flag. The store is per-machine and gitignored exactly like
``schedules.jsonl``, whose ``ScheduleStore`` this deliberately mirrors (append-only JSONL,
latest-wins per id, every read-modify-write under one cross-process sidecar lock, because
the operator loop and a ``docker exec`` CLI share the file in the shipped deployment).

**It is also the queue.** A request submitted over the control channel is *enqueued* here
and executed by the operator loop's drain between polls, one at a time. That is what makes
the channel answer while work is pending: the loop is no longer held for the length of a
model call, so ``/tasks``, ``/cancel`` and ``/kill`` still land mid-backlog. Execution stays
single-process sequential — this buys **visibility**, not concurrency.

**Forward-only lifecycle**, following the programization review precedent::

    QUEUED ──► RUNNING ──► DELIVERED | FAILED | BLOCKED      (terminal)
      └──────► CANCELLED                                     (terminal)

A backwards or skipped edge is a typed refusal, never a silent overwrite: the whole value
of this file is that its statuses can be believed. ``CANCELLED`` is reachable only from
``QUEUED`` — cancelling a *running* task is the kill switch's job, and this module does not
duplicate that authority.

**Known limit — stranded RUNNING.** A process killed mid-run leaves its entry at RUNNING
with no terminal, the same blind spot ``scheduler.py`` names for a fire that never got to
write its outcome. The next startup is the only vantage point that can see it, so
:func:`reconcile_stale_running` supplies the missing terminal there. Until it runs, a
stranded entry says RUNNING; it never silently becomes DELIVERED.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Mapping

from runtime.read_only_kernel import integrity
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from . import jsonl, schema_cache, timeutil
from .errors import PersistenceError, TaskRegistryBlocked
from .filelock import locked
from .paths import repo_root as _repo_root

REGISTRY_REL = ".runtime_governance_state/task_registry.jsonl"
SCHEMA_VERSION = "task_registry_entry.v0.1"

# Lifecycle. Terminal statuses are final — the registry is a record of what happened, so a
# terminal entry is never re-opened (a re-run is a new submission with its own entry).
QUEUED = "QUEUED"
RUNNING = "RUNNING"
DELIVERED = "DELIVERED"
FAILED = "FAILED"
BLOCKED = "BLOCKED"
CANCELLED = "CANCELLED"
TERMINAL_STATUSES = frozenset({DELIVERED, FAILED, BLOCKED, CANCELLED})
OPEN_STATUSES = (QUEUED, RUNNING)

# The only legal edges. Anything else is TRANSITION_INVALID — including QUEUED -> DELIVERED
# (a delivered result that never ran is a bookkeeping lie) and RUNNING -> CANCELLED (the
# kill switch owns in-flight execution; see the module docstring).
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    QUEUED: frozenset({RUNNING, CANCELLED}),
    RUNNING: frozenset({DELIVERED, FAILED, BLOCKED}),
    DELIVERED: frozenset(),
    FAILED: frozenset(),
    BLOCKED: frozenset(),
    CANCELLED: frozenset(),
}

ORIGINS = frozenset({"TELEGRAM", "CLI", "SCHEDULER", "FRONTDESK"})
_FLAG_NAMES = ("important", "independent_validation", "revise", "write_output")

# The terminal supplied by the next startup for an entry whose process died mid-run.
ABANDONED_REASON_CODE = "RUN_ABANDONED"

# How many requests may wait at once. The runtime executes one task at a time, so a deeper
# queue does not get through faster — it only turns "the operator typed a lot" into a long
# unattended burst he stops being able to predict. Refusing at the limit says so while he
# can still choose what matters.
QUEUE_DEPTH_LIMIT = 20


@dataclass(frozen=True)
class RegistryEntry:
    """One coordination entry. Immutable; a transition produces an updated copy."""

    registry_entry_id: str
    request_text: str
    origin: str
    requester_id: str
    flags: dict[str, bool]
    status: str
    submitted_at: str
    started_at: str | None = None
    finished_at: str | None = None
    task_id: str | None = None
    trace_id: str | None = None
    result_ref: str | None = None
    last_reason_code: str | None = None

    def as_record(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "registry_entry_id": self.registry_entry_id,
            "request_text": self.request_text,
            "origin": self.origin,
            "requester_id": self.requester_id,
            "flags": {name: bool(self.flags.get(name, False)) for name in _FLAG_NAMES},
            "status": self.status,
            "submitted_at": self.submitted_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "task_id": self.task_id,
            "trace_id": self.trace_id,
            "result_ref": self.result_ref,
            "last_reason_code": self.last_reason_code,
        }

    @classmethod
    def from_record(cls, r: Mapping[str, Any]) -> "RegistryEntry":
        """Rebuild an entry from its stored row, or fail closed with a typed error.

        A hand-edited or half-written row must not escape as a raw KeyError past the
        console's ``except MvpRuntimeError`` and kill the loop with a traceback — the
        ``SCHEDULE_RECORD_INVALID`` lesson, applied here before it can happen."""
        try:
            entry_id = str(r["registry_entry_id"])
            status = str(r["status"])
            flags = r.get("flags") or {}
            entry = cls(
                registry_entry_id=entry_id,
                request_text=str(r["request_text"]),
                origin=str(r["origin"]),
                requester_id=str(r["requester_id"]),
                flags={name: bool(flags.get(name, False)) for name in _FLAG_NAMES},
                status=status,
                submitted_at=str(r["submitted_at"]),
                started_at=_opt_str(r.get("started_at")),
                finished_at=_opt_str(r.get("finished_at")),
                task_id=_opt_str(r.get("task_id")),
                trace_id=_opt_str(r.get("trace_id")),
                result_ref=_opt_str(r.get("result_ref")),
                last_reason_code=_opt_str(r.get("last_reason_code")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise TaskRegistryBlocked(
                "REGISTRY_RECORD_INVALID",
                f"a stored registry entry is missing or has a malformed required field: {exc}",
            ) from exc
        if status not in _ALLOWED_TRANSITIONS:
            raise TaskRegistryBlocked(
                "REGISTRY_RECORD_INVALID",
                f"registry entry {entry_id} has unknown status {status!r}",
            )
        return entry

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


def _opt_str(value: Any) -> str | None:
    """Optional string field: absent/null stays None, anything else is stringified.

    Deliberately never turns None into the string ``"None"`` — that exact coercion is what
    made a scheduler row sort above every timestamp and silently never fire."""
    return None if value is None else str(value)


def _validate(record: Mapping[str, Any]) -> None:
    """Validate a record against its closed schema before it is persisted (fail-closed).

    Resolved against the *repo* root, never the store's root: schemas are committed source
    that ships with the code, while the store's root is per-machine state (a test tmpdir, a
    mounted volume). Conflating the two would make validation silently depend on where the
    state happens to live."""
    try:
        schema_cache.validate_against_schema(
            record, _repo_root() / "schemas" / f"{SCHEMA_VERSION}.schema.json", "task_registry_entry"
        )
    except RuntimeSchemaError as exc:
        raise TaskRegistryBlocked("REGISTRY_RECORD_INVALID", str(exc)) from exc


def build_entry(
    *,
    request_text: str,
    origin: str,
    requester_id: str,
    now: str,
    flags: Mapping[str, bool] | None = None,
    status: str = QUEUED,
) -> RegistryEntry:
    """Validate inputs and build a new entry with a deterministic id. Fail-closed.

    The id seed includes ``now`` and the requester so two identical requests from the same
    operator at different moments are different entries — the registry is a log of
    submissions, not a set of distinct texts."""
    text = request_text.strip() if isinstance(request_text, str) else ""
    if not text:
        raise TaskRegistryBlocked("EMPTY_REQUEST", "a registry entry requires a non-empty request text")
    if origin not in ORIGINS:
        raise TaskRegistryBlocked("UNKNOWN_ORIGIN", f"origin must be one of {sorted(ORIGINS)}")
    if not (isinstance(requester_id, str) and requester_id.strip()):
        raise TaskRegistryBlocked("MISSING_REQUESTER", "a registry entry requires a requester identity")
    if status not in (QUEUED, RUNNING):
        raise TaskRegistryBlocked(
            "INVALID_INITIAL_STATUS", f"a new entry starts at {QUEUED} or {RUNNING}, not {status!r}"
        )
    supplied = dict(flags or {})
    unknown = sorted(set(supplied) - set(_FLAG_NAMES))
    if unknown:
        # A silently-dropped flag would make history explain a run by options it never had.
        raise TaskRegistryBlocked("UNKNOWN_FLAG", f"unknown submission flags: {unknown}")
    entry_id = integrity.short_id(
        "treg",
        {"request": text, "origin": origin, "requester_id": requester_id.strip(), "submitted_at": now},
    )
    return RegistryEntry(
        registry_entry_id=entry_id,
        request_text=text,
        origin=origin,
        requester_id=requester_id.strip(),
        flags={name: bool(supplied.get(name, False)) for name in _FLAG_NAMES},
        status=status,
        submitted_at=now,
        started_at=now if status == RUNNING else None,
    )


def _assert_transition(current: str, target: str, entry_id: str) -> None:
    if target not in _ALLOWED_TRANSITIONS.get(current, frozenset()):
        raise TaskRegistryBlocked(
            "TRANSITION_INVALID",
            f"registry entry {entry_id} is {current}; {current} -> {target} is not a legal "
            "transition (the lifecycle is forward-only)",
        )


class TaskRegistryStore:
    """Local append-only JSONL store of coordination entries; latest row per id wins.

    Append-only rather than rewrite-in-place: the sequence of rows for one id *is* the
    entry's history, so "when did this start running" is answerable without a second log.
    Every read-modify-write holds the sidecar lock — the operator loop and a ``docker exec``
    CLI reading the same volume must not interleave a transition with a read.
    """

    def __init__(self, root: Path):
        self._root = Path(root)
        self._path = self._root / REGISTRY_REL

    @classmethod
    def default(cls, root: Path | None = None) -> "TaskRegistryStore":
        return cls((root if root is not None else _repo_root()))

    @property
    def path(self) -> Path:
        return self._path

    def _lock(self):
        return locked(self._path.with_name(".task_registry.lock"),
                      code="REGISTRY_WRITE_FAILED", label="the task registry")

    def _read_rows(self) -> list[dict[str, Any]]:
        return jsonl.read_objects(self._path, read_code="REGISTRY_UNREADABLE", label="the task registry")

    def _append(self, entries: Iterable[RegistryEntry]) -> None:
        records = []
        for entry in entries:
            record = entry.as_record()
            _validate(record)
            records.append(record)
        jsonl.append_lines(self._path, records,
                           write_code="REGISTRY_WRITE_FAILED", label="the task registry")

    def latest(self) -> list[RegistryEntry]:
        """The current state of every entry, most recently submitted first.

        Read under the appender's own lock, like the ledger's readers: a drain may be
        appending a claim while a ``/tasks`` listing scans the stream, and an unlocked read
        could see a torn final line and report the store corrupt when nothing is wrong."""
        with self._lock():
            return list(reversed(self._latest_locked()))

    def _latest_locked(self) -> list[RegistryEntry]:
        """The current state of every entry, oldest submission first; caller holds the lock.

        Ties break on **arrival order** — the position of an entry's first row in the
        append-only file — not on ``registry_entry_id``. Timestamps here are the repo's
        canonical second-resolution UTC form, so two requests sent in the same second carry
        the same ``submitted_at``; breaking that tie on a hashed id would have run them in
        an order unrelated to the order Thomas asked, which is not a queue. The file's own
        append order is the one record of arrival that exists.
        """
        latest: dict[str, dict[str, Any]] = {}
        arrival: dict[str, int] = {}
        for index, row in enumerate(self._read_rows()):
            if isinstance(row, dict) and isinstance(row.get("registry_entry_id"), str):
                entry_id = row["registry_entry_id"]
                latest[entry_id] = row
                arrival.setdefault(entry_id, index)
        entries = [RegistryEntry.from_record(row) for row in latest.values()]
        entries.sort(key=lambda e: (e.submitted_at, arrival[e.registry_entry_id]))
        return entries

    def queued_count(self) -> int:
        """How many entries are waiting. Cheap enough to ask before every poll."""
        return sum(1 for entry in self.latest() if entry.status == QUEUED)

    def claim_next_queued(self, *, now: str) -> RegistryEntry | None:
        """Atomically claim the oldest QUEUED entry, or None if the queue is empty.

        The whole find-and-claim runs inside ONE lock acquisition, so two drains cannot
        both see the same entry as QUEUED: the second finds it already RUNNING and moves
        on. This is the ``claim_due`` pattern from the scheduler, and for the same reason —
        the claim must precede the execution, so an occurrence is at-most-once. A crash
        after the claim leaves the entry RUNNING for :func:`reconcile_stale_running` to
        close honestly, which is the direction that never runs a task twice.

        Strict FIFO (see ``_latest_locked`` on why arrival order, not the id, breaks a
        same-second tie): the queue comes back in the order Thomas asked, and no priority
        ordering is claimed that the runtime does not actually implement.
        """
        with self._lock():
            for entry in self._latest_locked():
                if entry.status != QUEUED:
                    continue
                claimed = replace(entry, status=RUNNING, started_at=now)
                self._append([claimed])
                return claimed
            return None

    def find(self, entry_id: str) -> RegistryEntry | None:
        """The current state of one entry, or None. Accepts a unique id prefix, so the
        operator can type the first few characters off a ``/tasks`` listing; an ambiguous
        prefix is refused rather than resolved to an arbitrary match."""
        if not (isinstance(entry_id, str) and entry_id.strip()):
            return None
        needle = entry_id.strip()
        entries = self.latest()
        exact = [e for e in entries if e.registry_entry_id == needle]
        if exact:
            return exact[0]
        matches = [e for e in entries if e.registry_entry_id.startswith(needle)]
        if len(matches) > 1:
            raise TaskRegistryBlocked(
                "AMBIGUOUS_ENTRY_ID",
                f"'{needle}' matches {len(matches)} entries; use more characters of the id",
            )
        return matches[0] if matches else None

    def submit(self, entry: RegistryEntry) -> RegistryEntry:
        """Record a new submission."""
        with self._lock():
            self._append([entry])
        return entry

    def transition(
        self,
        entry_id: str,
        target: str,
        *,
        now: str,
        task_id: str | None = None,
        trace_id: str | None = None,
        result_ref: str | None = None,
        reason_code: str | None = None,
    ) -> RegistryEntry:
        """Move one entry forward and append its new state. Fail-closed.

        The whole read-check-append runs under the store lock, so two processes cannot both
        see QUEUED and both claim it: the loser's ``_assert_transition`` sees the winner's
        RUNNING and refuses. Identity fields (``task_id``/``trace_id``) are only ever
        filled in, never overwritten with None — a later transition that does not know them
        must not erase what an earlier one recorded."""
        with self._lock():
            current = self._current_locked(entry_id)
            _assert_transition(current.status, target, current.registry_entry_id)
            updated = replace(
                current,
                status=target,
                started_at=now if target == RUNNING else current.started_at,
                finished_at=now if target in TERMINAL_STATUSES else current.finished_at,
                task_id=task_id if task_id is not None else current.task_id,
                trace_id=trace_id if trace_id is not None else current.trace_id,
                result_ref=result_ref if result_ref is not None else current.result_ref,
                last_reason_code=reason_code,
            )
            self._append([updated])
        return updated

    def _current_locked(self, entry_id: str) -> RegistryEntry:
        """Re-read one entry's CURRENT state; caller must hold the lock."""
        latest: dict[str, Any] | None = None
        for row in self._read_rows():
            if isinstance(row, dict) and row.get("registry_entry_id") == entry_id:
                latest = row
        if latest is None:
            raise TaskRegistryBlocked("ENTRY_NOT_FOUND", f"no registry entry {entry_id}")
        return RegistryEntry.from_record(latest)


def reconcile_stale_running(store: TaskRegistryStore, *, now: str) -> list[RegistryEntry]:
    """Supply the terminal that a process killed mid-run never wrote. Call at startup.

    A RUNNING entry can only be genuinely in flight while the process that claimed it is
    alive; the MVP runs tasks one at a time in a single process, so any entry still RUNNING
    when a fresh process starts belongs to a dead one. Marking it FAILED
    (``RUN_ABANDONED``) is the honest terminal — the run did not deliver, and the registry
    must not keep telling the operator that something is running when nothing is.

    Deliberately startup-only, and deliberately not a timeout: a long model call is not a
    dead process, and guessing from elapsed time would abandon healthy runs.
    """
    reconciled: list[RegistryEntry] = []
    for entry in store.latest():
        if entry.status != RUNNING:
            continue
        reconciled.append(store.transition(
            entry.registry_entry_id, FAILED, now=now, reason_code=ABANDONED_REASON_CODE,
        ))
    return reconciled


def enqueue(
    store: TaskRegistryStore,
    *,
    request_text: str,
    origin: str,
    requester_id: str,
    now: str,
    flags: Mapping[str, bool] | None = None,
) -> tuple[RegistryEntry, int]:
    """Queue a request for later execution. Returns ``(entry, queue_position)``.

    **Not best-effort, unlike :func:`record_submission`.** When the registry is the
    execution path rather than a view onto it, a swallowed failure would silently drop a
    request Thomas believes was accepted — so every failure here raises and becomes a
    refusal he can see. That difference is the whole distinction between bookkeeping and
    the queue.

    ``QUEUE_DEPTH_LIMIT`` bounds the backlog: the runtime executes one task at a time, so
    an unbounded queue only converts "the operator typed a lot" into a long unattended
    burst. Refusing at the limit tells him now, when he can still decide what matters.
    """
    depth = store.queued_count()
    if depth >= QUEUE_DEPTH_LIMIT:
        raise TaskRegistryBlocked(
            "QUEUE_FULL",
            f"{depth}건이 이미 대기 중입니다 (최대 {QUEUE_DEPTH_LIMIT}) — "
            "먼저 처리되기를 기다리거나 /cancel 로 정리해 주세요.",
        )
    entry = build_entry(
        request_text=request_text, origin=origin, requester_id=requester_id,
        now=now, flags=flags, status=QUEUED,
    )
    store.submit(entry)
    return entry, depth + 1


def record_submission(
    store: TaskRegistryStore | None,
    *,
    request_text: str,
    origin: str,
    requester_id: str,
    now: str,
    flags: Mapping[str, bool] | None = None,
) -> RegistryEntry | None:
    """Open a RUNNING entry for a request that is about to be executed inline.

    The coordination seam is **best-effort by design**: a failure to record bookkeeping must
    never cost the operator their analysis, so a broken/absent registry returns None and the
    run proceeds unrecorded (the working-memory and programization precedent). The durable
    audit ledger — which is *not* best-effort — remains the record of truth for the run
    itself; what is lost here is the convenience view, not evidence.
    """
    if store is None:
        return None
    try:
        entry = build_entry(
            request_text=request_text, origin=origin, requester_id=requester_id,
            now=now, flags=flags, status=RUNNING,
        )
        return store.submit(entry)
    except (TaskRegistryBlocked, PersistenceError):
        return None


def close_entry(
    store: TaskRegistryStore | None,
    entry: RegistryEntry | None,
    *,
    status: str,
    now: str,
    task_id: str | None = None,
    trace_id: str | None = None,
    result_ref: str | None = None,
    reason_code: str | None = None,
) -> None:
    """Move an inline run's entry to its terminal status. Best-effort, like the opening."""
    if store is None or entry is None:
        return
    try:
        store.transition(
            entry.registry_entry_id, status, now=now, task_id=task_id, trace_id=trace_id,
            result_ref=result_ref, reason_code=reason_code,
        )
    except (TaskRegistryBlocked, PersistenceError):
        return
