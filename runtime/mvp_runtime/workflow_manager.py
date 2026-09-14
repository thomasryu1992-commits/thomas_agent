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
from typing import Any, Callable, Mapping

from . import approval as approval_mod, permission, socket_door, task_registry, timeutil, workflow as wf
from .approval_store import ApprovalStore
from .binding import bind_task_to_core
from .control import ControlStore
from .dispatch_bridge import WORKER_DEADLINE_SECONDS
from .errors import ApprovalBlocked, MvpRuntimeError
from .intake import build_task
from .task_registry import TaskRegistryStore
from .workflow_store import COST_OBSERVED, DEFAULT_ATTEMPT_DEADLINE_SECONDS, ClaimedAttempt, WorkflowStore

DEFAULT_CONCURRENCY = 2          # the worker's own MAX_CONCURRENT_REQUESTS; a third would only queue
DEFAULT_POLL_SECONDS = 2.0
CLIENT_ID_PREFIX = "workflow:"   # attribution on the task record: `assistant_bridge:workflow:<id>`
WORKER_UNAVAILABLE = "WORKER_UNAVAILABLE"
# A gated step whose ask could not be minted (no Core binding, an unreadable store) is retried
# after this many ticks rather than every poll, so one broken precondition is one log line a
# minute and not thirty.
MINT_RETRY_TICKS = 30

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
        registry: TaskRegistryStore | None = None,
        approval_store: ApprovalStore | None = None,
        repo_root: Path | None = None,
    ):
        self._store = store
        self._registry = registry
        self._approval_store = approval_store
        self._repo_root = repo_root
        self._mint_failures: dict[str, int] = {}
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
        that is gone. Their connections died with it. The registry row each one opened says
        what happened meanwhile — a finished run is applied here without a second model call
        (A10, A28); the rest lapse at their leases."""
        now = now or self._clock()
        self._store.initialize()
        inherited = self._store.running_attempts()
        reconciled = self.reconcile(now=now)
        return {"inherited_running_attempts": len(inherited), "as_of": now,
                "attempt_ids": [a["attempt_id"] for a in inherited], **reconciled}

    def reconcile(self, *, now: str | None = None) -> dict[str, Any]:
        """Recover what a lost reply left behind (V0.2 §4.3). For every RUNNING attempt the
        WORKFLOW registry row that ran it is the worker's own record: DELIVERED applies the
        result (trace, registry id, `ledger:<trace>`) with no second model call; FAILED/BLOCKED
        fails the attempt under the row's reason; a row still RUNNING past the lease is closed
        RUN_ABANDONED by the manager — the origin it owns — so /tasks stops saying RUNNING, and
        `expire_overdue` then rules on the attempt by effect class. A row still RUNNING inside
        the lease is a run still going: nothing is touched. No registry, nothing reconciled."""
        report = {"reconciled": 0, "abandoned": 0, "reconcile_errors": 0}
        if self._registry is None:
            return report
        now = now or self._clock()
        for attempt in self._store.running_attempts():
            try:
                entry = self._registry.find_by_attempt(attempt["attempt_id"])
            except MvpRuntimeError as exc:
                report["reconcile_errors"] += 1
                self._log(f"WORKFLOW_MANAGER[{exc.reason_code}]: registry unreadable while reconciling "
                          f"{attempt['attempt_id']}: {exc}")
                continue
            if entry is None:
                continue
            try:
                if entry.status == task_registry.DELIVERED:
                    trace = entry.trace_id
                    self._store.record_result(
                        attempt["attempt_id"], now=now, succeeded=True, trace_id=trace,
                        registry_entry_id=entry.registry_entry_id,
                        result_ref=entry.result_ref or (f"ledger:{trace}" if trace else None),
                    )
                    report["reconciled"] += 1
                    self._log(f"WORKFLOW_MANAGER[RECONCILED]: attempt {attempt['attempt_id']} completed from "
                              f"registry row {entry.registry_entry_id} without a second run")
                elif entry.status in (task_registry.FAILED, task_registry.BLOCKED):
                    self._store.record_result(
                        attempt["attempt_id"], now=now, succeeded=False, trace_id=entry.trace_id,
                        registry_entry_id=entry.registry_entry_id,
                        reason_code=entry.last_reason_code or entry.status,
                    )
                    report["reconciled"] += 1
                elif entry.status == task_registry.RUNNING and attempt["deadline_at"] < now:
                    self._registry.transition(
                        entry.registry_entry_id, task_registry.FAILED, now=now,
                        reason_code=task_registry.ABANDONED_REASON_CODE,
                    )
                    report["abandoned"] += 1
                    self._log(f"WORKFLOW_MANAGER[{task_registry.ABANDONED_REASON_CODE}]: registry row "
                              f"{entry.registry_entry_id} of attempt {attempt['attempt_id']} was still RUNNING "
                              f"past the lease {attempt['deadline_at']}; closed by the manager")
            except MvpRuntimeError as exc:
                # ATTEMPT_FENCED here means the store already moved on (a late row); recorded by
                # the store as a late result, nothing else to do.
                report["reconcile_errors"] += 1
                self._log(f"WORKFLOW_MANAGER[{exc.reason_code}]: reconciling {attempt['attempt_id']}: {exc}")
        return report

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
            report.update(self.reconcile(now=now))
            report["expired"] = len(self._store.expire_overdue(now=now))
            report.update(self.approvals(now=now))
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

    # --- gated steps (P07; V0.2 §1.2, acceptance A13) --------------------------------------------

    def approvals(self, *, now: str | None = None) -> dict[str, Any]:
        """Mint the ask a waiting step has none for; spend the grant Thomas gave; refuse what a
        grant cannot cover. The manager never creates APPROVED: it reads the approval store and
        runs the shared single-use ladder (`validate_spendable_approval`, `spend_lock`,
        `build_consumed_record`) — the same steps the switch door and memory promotion run. A
        step whose bound ask is for an older plan version asks again; a rejected, expired, spent or
        re-pointed grant blocks the step under that reason, and the workflow waits for a decision.
        Without an approval store nothing is minted or spent and gated steps simply wait."""
        report = {"asks_minted": 0, "approvals_spent": 0, "approvals_refused": 0, "approvals_deferred": 0}
        if self._approval_store is None:
            return report
        now = now or self._clock()
        for step in self._store.waiting_approval_steps():
            step_id = step["step_id"]
            try:
                if step["approval_id"] is None or step["approval_plan_version"] != step["workflow_plan_version"]:
                    last = self._mint_failures.get(step_id)
                    if last is not None and self.ticks - last < MINT_RETRY_TICKS:
                        continue
                    self._mint(step, now)
                    self._mint_failures.pop(step_id, None)
                    report["asks_minted"] += 1
                    continue
                record = self._approval_store.get(step["approval_id"])
                if record is None:
                    self._refuse(step, wf.APPROVAL_STALE, f"ask {step['approval_id']} is not in the approval store", now)
                    report["approvals_refused"] += 1
                    continue
                status = record.get("status")
                if status == approval_mod.STATUS_PENDING:
                    if approval_mod.is_expired(record, now=now):
                        self._refuse(step, wf.APPROVAL_EXPIRED, f"ask {step['approval_id']} expired at "
                                     f"{(record.get('validity') or {}).get('expires_at')}", now)
                        report["approvals_refused"] += 1
                    continue
                if status == approval_mod.STATUS_REJECTED:
                    self._refuse(step, wf.APPROVAL_REJECTED, f"ask {step['approval_id']} was rejected", now)
                    report["approvals_refused"] += 1
                elif status == approval_mod.STATUS_CONSUMED:
                    self._refuse(step, wf.APPROVAL_REUSED, f"grant {step['approval_id']} was already spent", now)
                    report["approvals_refused"] += 1
                elif status == approval_mod.STATUS_APPROVED:
                    outcome = self._spend(step, now)
                    if outcome is None:
                        report["approvals_deferred"] += 1       # halted runtime: the grant waits, unspent
                    elif outcome:
                        report["approvals_spent"] += 1
                    else:
                        report["approvals_refused"] += 1
                else:
                    self._refuse(step, wf.APPROVAL_STALE, f"ask {step['approval_id']} is {status!r}", now)
                    report["approvals_refused"] += 1
            except MvpRuntimeError as exc:
                if step["approval_id"] is None:
                    self._mint_failures[step_id] = self.ticks
                self._log(f"WORKFLOW_MANAGER[{exc.reason_code}]: gated step {step['step_key']} of "
                          f"{step['workflow_id']}: {exc}")
        return report

    def _mint(self, step: Mapping[str, Any], now: str) -> None:
        """One ask per gated step per plan version, through the existing machinery: a Core-bound
        task, an APPROVAL_REQUIRED decision naming the step and its request hash, the pending
        approval request. Thomas decides on the control bot; the operator announces it."""
        content = wf.approval_content(workflow_id=step["workflow_id"], step_key=step["step_key"],
                                      plan_version=step["workflow_plan_version"], capability=step["capability"],
                                      request=step["request"])
        task = build_task(
            f"워크플로 단계 승인 검토: {str(step.get('goal') or '')[:80]} / {step['step_key']}",
            now=now, channel="agent", requester_type="agent", requester_id=socket_door.ASSISTANT_ACTOR,
            authenticated=True,
        )
        _, bound = bind_task_to_core(task, now=now, repo_root=self._repo_root)
        decision = permission.build_workflow_step_permission_decision(
            bound, workflow_id=step["workflow_id"], step_key=step["step_key"],
            plan_version=step["workflow_plan_version"], capability=step["capability"],
            request_sha256=content["request_sha256"], request_preview=step["request"], now=now, repo_root=self._repo_root,
        )
        request = approval_mod.build_approval_request(decision, now=now, repo_root=self._repo_root)
        self._approval_store.append_permission_decision(decision)
        self._approval_store.append([request])
        self._store.bind_approval(step["step_id"], approval_id=request["approval_id"],
                                  plan_version=step["workflow_plan_version"], now=now)
        self._log(f"WORKFLOW_MANAGER[APPROVAL_REQUESTED]: step {step['step_key']} of {step['workflow_id']} waits for "
                  f"{request['approval_id']} (plan v{step['workflow_plan_version']})")

    def _spend(self, step: Mapping[str, Any], now: str) -> bool | None:
        """Spend the bound APPROVED grant once, through the shared ladder, and only if it still
        describes this step at this plan version with this request. Returns True when the step
        was released; False when the grant was refused (the step is blocked under the reason);
        None while the runtime is halted (spending a grant is execution — the grant waits)."""
        approval_id = step["approval_id"]
        control_state = self._control.load()
        if not control_state.execution_allowed:
            return None
        try:
            _rec, decision, snapshot = approval_mod.validate_spendable_approval(
                self._approval_store, approval_id, now=now, control_state=control_state,
                expected_scope=permission.TRADING_SWITCH_PERMISSION_SCOPE,
                kill_action="spending a workflow-step grant", refusal_phrase="the step is not run",
                scope_refusal="is not a workflow-step grant",
            )
        except ApprovalBlocked as exc:
            mapped = {"ALREADY_CONSUMED": wf.APPROVAL_REUSED, "APPROVAL_EXPIRED": wf.APPROVAL_EXPIRED,
                      "NOT_APPROVED": wf.APPROVAL_REJECTED}.get(exc.reason_code, wf.APPROVAL_STALE)
            self._refuse(step, mapped, f"grant {approval_id}: {exc.reason_code}", now)
            return False
        expected = wf.approval_content(workflow_id=step["workflow_id"], step_key=step["step_key"],
                                       plan_version=step["workflow_plan_version"], capability=step["capability"],
                                       request=step["request"])
        if (snapshot.get("target_ref") != wf.approval_target_ref(step["workflow_id"], step["step_key"])
                or dict(snapshot.get("normalized_parameters") or {}) != expected):
            self._refuse(step, wf.APPROVAL_STALE, f"grant {approval_id} describes another step, version or request", now)
            return False
        with approval_mod.spend_lock(self._approval_store, approval_id):
            fresh = self._approval_store.get(approval_id)
            consumed = approval_mod.build_consumed_record(
                fresh, decision, consumed_at=now,
                consumption_ref=f"workflow:{step['workflow_id']}:{step['step_key']}:v{step['workflow_plan_version']}",
                repo_root=self._repo_root,
            )
            self._approval_store.append([consumed])
            self._store.approve_step(step["step_id"], approval_id=approval_id, now=now)
        self._log(f"WORKFLOW_MANAGER[APPROVAL_CONSUMED]: step {step['step_key']} of {step['workflow_id']} released by {approval_id}")
        return True

    def _refuse(self, step: Mapping[str, Any], reason_code: str, detail: str, now: str) -> None:
        self._store.refuse_step_approval(step["step_id"], reason_code=reason_code, detail=detail, now=now)
        self._log(f"WORKFLOW_MANAGER[{reason_code}]: step {step['step_key']} of {step['workflow_id']} blocked: {detail}")

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
        worker to echo, the step's assurance options when it asked for any, and (P07) the
        result references of the dependency steps it named in `input_refs`. Optional keys
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
        inputs = {key: ref for key, ref in attempt.input_refs.items() if ref}
        if inputs:
            frame[wf.WORKFLOW_INPUTS_KEY] = inputs            # P07: what this step reads, by result ref
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
