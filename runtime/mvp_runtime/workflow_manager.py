"""Workflow manager — the loop that turns accepted plans into worker attempts and results.

Sequence 2, P04 (``docs/HERMES_ORCHESTRATOR_ARCHITECTURE_V0.2.md`` Q18, Q22). It runs **inside
the dispatch-bridge process** (``dispatch_bridge_cli --workflow-manager``): no new service, uid,
volume or healthcheck. The bridge is already the admission point (kill switch, kind set,
idempotency) and the worker socket's only legitimate client, so a bridge that does not wait
for the worker is the manager. A loop failure is a bridge failure — v2 and v3 fail together,
which is today's failure shape — and never reaches the scheduler lanes or the operator.

**One tick.** Lapse overdue leases (``expire_overdue``); if the runtime is PAUSED/KILLED, claim
nothing (in-flight attempts run to their end — the worker checked the switch when it started);
otherwise claim as many READY steps as there are free slots and hand each to the worker over
the internal socket, in its own thread, as the v2 dispatch frame the worker already speaks
(``request``/``kind``/``reason``/``naver_keywords``/``client_id``). The attempt frame that
carries ``attempt_id`` for the worker to echo, and the ``WORKFLOW`` registry row, arrive in
P05; until then the worker records these runs as it records every assistant run.

**What a reply does.** ``ok`` → the attempt SUCCEEDED with the run's ``trace_id`` and
``registry_entry_id`` and ``result_ref = ledger:<trace_id>``. A typed refusal or a pipeline
BLOCK → the attempt FAILED under the worker's own reason code; the store retries while the
step has attempts left. A **transport** failure is neither: the frame may or may not have
been executed, so nothing is recorded and the lease decides — the attempt lapses at
``deadline_at`` and an ``effect_class=none`` step opens a new one (Q22). The one transport
failure that is safe to fail at once is a worker socket that is not there at all: nothing was
sent, so the attempt FAILS with ``WORKER_UNAVAILABLE`` and the step retries.

The loop never raises out of a tick: a store or transport error is written to this service's
log and the next tick tries again. The door keeps answering throughout.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any, Callable

from . import socket_door, timeutil, workflow as wf
from .control import ControlStore
from .dispatch_bridge import WORKER_DEADLINE_SECONDS
from .errors import MvpRuntimeError
from .workflow_store import COST_OBSERVED, DEFAULT_ATTEMPT_DEADLINE_SECONDS, ClaimedAttempt, WorkflowStore

DEFAULT_CONCURRENCY = 2          # the worker's own MAX_CONCURRENT_REQUESTS; a third would only queue
DEFAULT_POLL_SECONDS = 2.0
CLIENT_ID_PREFIX = "workflow:"   # attribution on the task record: `assistant_bridge:workflow:<id>`
WORKER_UNAVAILABLE = "WORKER_UNAVAILABLE"

Caller = Callable[..., Any]


class WorkflowManager:
    """The loop. ``call`` and ``door_is_live`` are injectable so a test can drive a tick without
    a socket; ``synchronous=True`` runs attempts inline instead of in threads."""

    def __init__(
        self,
        store: WorkflowStore,
        *,
        control_store: ControlStore,
        worker_socket: Path,
        worker_deadline_seconds: float = WORKER_DEADLINE_SECONDS,
        attempt_deadline_seconds: int = DEFAULT_ATTEMPT_DEADLINE_SECONDS,
        concurrency: int = DEFAULT_CONCURRENCY,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
        call: Caller = socket_door.call_door,
        door_is_live: Callable[[Path], bool] | None = None,
        clock: Callable[[], str] = timeutil.utc_now_iso,
        log: Callable[[str], None] | None = None,
        synchronous: bool = False,
    ):
        self._store = store
        self._control = control_store
        self._worker_socket = Path(worker_socket)
        self._worker_deadline = float(worker_deadline_seconds)
        self._attempt_deadline = int(attempt_deadline_seconds)
        self._concurrency = max(1, int(concurrency))
        self._poll = max(0.05, float(poll_seconds))
        self._call = call
        self._door_is_live = door_is_live or _worker_reachable
        self._clock = clock
        self._log = log or _stderr
        self._synchronous = synchronous
        self._in_flight: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.ticks = 0

    # --- lifecycle ---------------------------------------------------------------------------

    def startup_report(self, *, now: str | None = None) -> dict[str, Any]:
        """What this process inherits: attempts still RUNNING in the store belong to a bridge
        that is gone. Their connections died with it, so they lapse at their leases (P06 adds
        reconciliation against the ledger for the ones that finished in the meantime)."""
        now = now or self._clock()
        self._store.initialize()
        inherited = self._store.running_attempts()
        return {"inherited_running_attempts": len(inherited), "as_of": now,
                "attempt_ids": [a["attempt_id"] for a in inherited]}

    def start(self) -> threading.Thread:
        """Run the loop in a daemon thread beside the door's own server loop."""
        report = self.startup_report()
        self._log(f"WORKFLOW_MANAGER: starting (concurrency={self._concurrency}, poll={self._poll}s, "
                  f"inherited_running_attempts={report['inherited_running_attempts']})")
        self._thread = threading.Thread(target=self.run_forever, name="workflow-manager", daemon=True)
        self._thread.start()
        return self._thread

    def stop(self, *, join_seconds: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(join_seconds)

    def run_forever(self) -> None:
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(self._poll)

    # --- one tick ----------------------------------------------------------------------------

    def in_flight(self) -> int:
        with self._lock:
            done = [k for k, t in self._in_flight.items() if not t.is_alive()]
            for k in done:
                del self._in_flight[k]
            return len(self._in_flight)

    def tick(self, *, now: str | None = None) -> dict[str, Any]:
        """One pass. Returns a small report; never raises."""
        now = now or self._clock()
        self.ticks += 1
        report: dict[str, Any] = {"as_of": now, "expired": 0, "claimed": 0, "skipped": None, "error": None}
        try:
            report["expired"] = len(self._store.expire_overdue(now=now))
            state = self._control.load()
            if not state.execution_allowed:
                report["skipped"] = f"runtime is {state.mode}; claiming nothing"
                return report
            free = self._concurrency - self.in_flight()
            if free <= 0:
                report["skipped"] = "every slot is in flight"
                return report
            claimed = self._store.claim_ready(now=now, limit=free, deadline_seconds=self._attempt_deadline)
            report["claimed"] = len(claimed)
            for attempt in claimed:
                self._launch(attempt)
        except MvpRuntimeError as exc:
            report["error"] = f"{exc.reason_code}: {exc}"
            self._log(f"WORKFLOW_MANAGER[{exc.reason_code}]: {exc}")
        except Exception as exc:  # noqa: BLE001 — the loop must outlive one bad tick
            report["error"] = f"{type(exc).__name__}: {exc}"
            self._log(f"WORKFLOW_MANAGER[{type(exc).__name__}]: {exc}")
        return report

    def _launch(self, attempt: ClaimedAttempt) -> None:
        if self._synchronous:
            self._run_attempt(attempt)
            return
        thread = threading.Thread(target=self._run_attempt, args=(attempt,),
                                  name=f"workflow-attempt-{attempt.attempt_id[-8:]}", daemon=True)
        with self._lock:
            self._in_flight[attempt.attempt_id] = thread
        thread.start()

    # --- one attempt -------------------------------------------------------------------------

    @staticmethod
    def worker_frame(attempt: ClaimedAttempt) -> dict[str, Any]:
        """The attempt frame (P05): the v2 dispatch frame plus the attempt's identity for the
        worker to echo, and the step's assurance options when it asked for any. Optional keys
        travel only when present (the worker's closed key set never sees a null); the
        attribution names the workflow."""
        frame: dict[str, Any] = {"request": attempt.request, "kind": attempt.capability, "reason": attempt.reason}
        if attempt.naver_keywords:
            frame["naver_keywords"] = attempt.naver_keywords
        frame[socket_door.CLIENT_ID_KEY] = f"{CLIENT_ID_PREFIX}{attempt.workflow_id}"
        frame[wf.ATTEMPT_ID_KEY] = attempt.attempt_id
        frame[wf.WORKFLOW_ID_KEY] = attempt.workflow_id
        options = {key: True for key in ("independent_validation", "revise") if attempt.options.get(key)}
        if options:
            frame[wf.WORKFLOW_OPTIONS_KEY] = options
        return frame

    def _run_attempt(self, attempt: ClaimedAttempt) -> None:
        try:
            if not self._door_is_live(self._worker_socket):
                # Nothing was sent: failing now costs nothing and lets the step retry at once.
                self._store.record_result(attempt.attempt_id, now=self._clock(), succeeded=False,
                                          reason_code=WORKER_UNAVAILABLE)
                self._log(f"WORKFLOW_MANAGER[{WORKER_UNAVAILABLE}]: no worker door at {self._worker_socket}; "
                          f"attempt {attempt.attempt_id} failed without dispatch")
                return
            try:
                reply = self._call(self._worker_socket, self.worker_frame(attempt),
                                   deadline_seconds=self._worker_deadline)
            except MvpRuntimeError as exc:
                # Sent, or maybe sent: the frame may be executing. Nothing is recorded; the lease
                # decides (Q22) — the attempt lapses at deadline_at and expire_overdue rules.
                self._log(f"WORKFLOW_MANAGER[{exc.reason_code}]: attempt {attempt.attempt_id} got no answer "
                          f"({exc}); left to its lease {attempt.deadline_at}")
                return
            self._apply_reply(attempt, reply)
        except MvpRuntimeError as exc:
            self._log(f"WORKFLOW_MANAGER[{exc.reason_code}]: attempt {attempt.attempt_id}: {exc}")
        except Exception as exc:  # noqa: BLE001 — an attempt thread must not die silently
            self._log(f"WORKFLOW_MANAGER[{type(exc).__name__}]: attempt {attempt.attempt_id}: {exc}")

    def _apply_reply(self, attempt: ClaimedAttempt, reply: Any) -> None:
        now = self._clock()
        if not isinstance(reply, dict):
            self._store.record_result(attempt.attempt_id, now=now, succeeded=False, reason_code="DOOR_REPLY_MALFORMED")
            return
        echoed = reply.get(wf.ATTEMPT_ID_KEY)
        if echoed is not None and echoed != attempt.attempt_id:
            # A reply that names another attempt is not this attempt's answer. Not applied and
            # not failed: whatever ran, this frame's own fate is what the lease will rule on.
            self._log(f"WORKFLOW_MANAGER[ATTEMPT_ECHO_MISMATCH]: attempt {attempt.attempt_id} got a reply "
                      f"for {echoed!r}; not applied, left to its lease {attempt.deadline_at}")
            return
        trace_id = reply.get("trace_id") if isinstance(reply.get("trace_id"), str) else None
        entry_id = reply.get("registry_entry_id") if isinstance(reply.get("registry_entry_id"), str) else None
        usage = reply.get("usage") if isinstance(reply.get("usage"), dict) else None
        spend: dict[str, Any] = {}
        if usage is not None:
            # The run's recorded spend confirms the reservation (observed, never estimated
            # here). Without it the reservation stays unconfirmed and keeps counting (A12).
            spend = {"model_calls": int(usage.get("model_calls") or 0),
                     "tokens": int(usage.get("tokens_used") or 0), "cost_status": COST_OBSERVED}
        if reply.get("ok"):
            self._store.record_result(
                attempt.attempt_id, now=now, succeeded=True, trace_id=trace_id, registry_entry_id=entry_id,
                result_ref=f"ledger:{trace_id}" if trace_id else None, **spend,
            )
            return
        reason = reply.get("reason_code") if isinstance(reply.get("reason_code"), str) else "DISPATCH_BLOCKED"
        self._store.record_result(attempt.attempt_id, now=now, succeeded=False, trace_id=trace_id,
                                  registry_entry_id=entry_id, reason_code=reason, **spend)


def _worker_reachable(path: Path) -> bool:
    """Is there a worker to send to? An absent socket path is a certain no (nothing can be sent);
    a present one is probed. `door_is_live` answers yes on uncertainty, which is the right bias
    here too: a doubtful door gets the frame, and the lease rules on what happens next."""
    return path.exists() and socket_door.door_is_live(path)


def _stderr(line: str) -> None:
    sys.stderr.write(line + "\n")
    sys.stderr.flush()


__all__ = ["WorkflowManager", "DEFAULT_CONCURRENCY", "DEFAULT_POLL_SECONDS", "CLIENT_ID_PREFIX"]
