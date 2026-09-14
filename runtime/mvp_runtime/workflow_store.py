"""Workflow store — the coordination state of workflows, steps and attempts, in SQLite.

Sequence 2, P03 (``docs/HERMES_ORCHESTRATOR_ARCHITECTURE_V0.2.md`` Q19–Q22, §4.4). This is the
first SQLite in the runtime, and the reason is one sentence: acceptance, idempotency, the first
steps and the budget reservation must land in **one transaction** or not at all, and JSONL under
a file lock cannot give that (``bridge_idempotency`` names its own claim→effect crash window).
Nothing else moves here: the run's evidence stays in the durable ledger's audit chain, the
registry keeps its attempt-level ``WORKFLOW`` rows, approvals stay where they are (§3.2).

**One writer per table.** The manager loop (inside the dispatch-bridge process) writes every
table but ``deliveries``, which the operator's push owns (P08). SQLite serialises writers across
processes; every write here is one short ``BEGIN IMMEDIATE`` transaction, and no transaction is
open across a model call.

**Read rule (Q19).** The file lives under the governance state root and is WAL. A reader opens
it ``mode=ro`` only from the same uid on the same RW mount — a read-only mount or another uid
cannot create the ``-shm`` file and fails. Anything else asks the door.

**What a row can say.** Every event is validated against its closed schema before insert; a
plan is validated (``workflow.validate_plan``) before the transaction that accepts it opens.
Nothing here is reachable as free SQL from a door.

**Fence and lease (Q22).** An attempt is the unit of execution. The step's ``current_attempt_id``
is the fence: a result for any other attempt is recorded as a late result and refused. The
attempt's ``deadline_at`` is the lease: past it, an ``effect_class=none`` step may open a new
attempt (its budget reservation stays counted — an unconfirmed spend is never refunded), while
an ``external`` step goes to NEEDS_RECONCILIATION and waits for a person or a reconciler.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

from . import timeutil, workflow as wf
from .errors import PersistenceError, WorkflowBlocked
from .paths import repo_root as _repo_root

WORKFLOW_DIR_REL = ".runtime_governance_state/workflow"
DB_FILENAME = "workflow.db"
SNAPSHOT_DIR_NAME = "snapshots"
# 1: sequence 2 P03. 2 (P07): steps gain `requires_approval`, `approval_id`, `approval_plan_version`.
SCHEMA_VERSION = 2

# Server-wide ceiling on steps that are accepted but not finished (V0.2 §4.3 starting point).
MAX_OPEN_STEPS = 20
# How long one attempt may run before its lease lapses. The worker socket's own deadline is
# 600 s (`dispatch_bridge.WORKER_DEADLINE_SECONDS`); the lease is a little longer so a result
# that lands at the deadline is not already late.
DEFAULT_ATTEMPT_DEADLINE_SECONDS = 660
BUSY_TIMEOUT_SECONDS = 5.0
# The first open of a file switches it to WAL, which needs the whole file to itself. Several
# handles opening one new file at once — four submit threads, a reader beside a writer — race
# for that instant, and on Windows the loser is told "database is locked" at once rather than
# waiting out the busy timeout (measured in CI, 2026-09-14). A handle serialises its own
# threads with a lock and retries the switch a bounded number of times for every other handle.
INIT_ATTEMPTS = 20
INIT_RETRY_SECONDS = 0.1

_DDL = (
    "CREATE TABLE IF NOT EXISTS schema_migrations ("
    " version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS workflows ("
    " workflow_id TEXT PRIMARY KEY, principal TEXT NOT NULL, goal TEXT NOT NULL,"
    " status TEXT NOT NULL, plan_version INTEGER NOT NULL, row_version INTEGER NOT NULL,"
    " max_model_calls INTEGER NOT NULL, max_tokens INTEGER,"
    " cancel_reason TEXT, last_reason_code TEXT,"
    " created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS plan_versions ("
    " workflow_id TEXT NOT NULL, version INTEGER NOT NULL, plan_hash TEXT NOT NULL,"
    " validated_plan TEXT NOT NULL, reason TEXT, created_at TEXT NOT NULL,"
    " PRIMARY KEY (workflow_id, version))",
    "CREATE TABLE IF NOT EXISTS steps ("
    " step_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, step_key TEXT NOT NULL,"
    " position INTEGER NOT NULL, capability TEXT NOT NULL, effect_class TEXT NOT NULL,"
    " request TEXT NOT NULL, reason TEXT NOT NULL, options TEXT NOT NULL,"
    " input_refs TEXT NOT NULL, naver_keywords TEXT, max_attempts INTEGER NOT NULL,"
    " status TEXT NOT NULL, attempts_opened INTEGER NOT NULL, current_attempt_id TEXT,"
    " result_ref TEXT, last_reason_code TEXT, row_version INTEGER NOT NULL,"
    " updated_at TEXT NOT NULL, requires_approval INTEGER NOT NULL DEFAULT 0,"
    " approval_id TEXT, approval_plan_version INTEGER, UNIQUE (workflow_id, step_key))",
    "CREATE TABLE IF NOT EXISTS dependencies ("
    " step_id TEXT NOT NULL, depends_on TEXT NOT NULL, PRIMARY KEY (step_id, depends_on))",
    "CREATE TABLE IF NOT EXISTS attempts ("
    " attempt_id TEXT PRIMARY KEY, step_id TEXT NOT NULL, workflow_id TEXT NOT NULL,"
    " attempt_number INTEGER NOT NULL, status TEXT NOT NULL, opened_at TEXT NOT NULL,"
    " deadline_at TEXT NOT NULL, closed_at TEXT, trace_id TEXT, registry_entry_id TEXT,"
    " result_ref TEXT, reason_code TEXT, UNIQUE (step_id, attempt_number))",
    "CREATE TABLE IF NOT EXISTS requests ("
    " principal TEXT NOT NULL, request_id TEXT NOT NULL, fingerprint TEXT NOT NULL,"
    " workflow_id TEXT NOT NULL, accepted_at TEXT NOT NULL, PRIMARY KEY (principal, request_id))",
    "CREATE TABLE IF NOT EXISTS events ("
    " cursor INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id TEXT NOT NULL, step_id TEXT,"
    " attempt_id TEXT, entity TEXT NOT NULL, from_status TEXT, to_status TEXT NOT NULL,"
    " reason_code TEXT, detail TEXT, created_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS budget_reservations ("
    " attempt_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, step_id TEXT NOT NULL,"
    " reserved_model_calls INTEGER NOT NULL, cost_status TEXT NOT NULL,"
    " used_model_calls INTEGER, used_tokens INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS deliveries ("
    " channel TEXT NOT NULL, event_cursor INTEGER NOT NULL, status TEXT NOT NULL,"
    " attempted_at TEXT NOT NULL, detail TEXT, PRIMARY KEY (channel, event_cursor))",
    "CREATE INDEX IF NOT EXISTS steps_by_status ON steps (status, workflow_id)",
    "CREATE INDEX IF NOT EXISTS attempts_by_status ON attempts (status, deadline_at)",
    "CREATE INDEX IF NOT EXISTS events_by_workflow ON events (workflow_id, cursor)",
)

COST_UNCONFIRMED = "unconfirmed"
COST_OBSERVED = "observed"
COST_ESTIMATED = "estimated"


@dataclass(frozen=True)
class SubmitOutcome:
    workflow_id: str
    status: str
    accepted_at: str
    replayed: bool


@dataclass(frozen=True)
class ClaimedAttempt:
    """What the manager hands the worker: one attempt frame. ``input_refs`` are the result
    references of the dependency steps the plan named, resolved at claim time."""
    attempt_id: str
    attempt_number: int
    step_id: str
    step_key: str
    workflow_id: str
    capability: str
    effect_class: str
    request: str
    reason: str
    naver_keywords: str | None
    options: dict[str, bool]
    input_refs: dict[str, str | None]
    deadline_at: str


class WorkflowStore:
    """The SQLite repository. Every public method is one transaction; none holds a model call."""

    def __init__(self, root: Path, *, readonly: bool = False):
        self._root = Path(root)
        self._path = self._root / WORKFLOW_DIR_REL / DB_FILENAME
        self._readonly = readonly
        self._initialized = False
        self._init_lock = threading.Lock()

    @classmethod
    def default(cls, root: Path | None = None, *, readonly: bool = False) -> "WorkflowStore":
        return cls(root if root is not None else _repo_root(), readonly=readonly)

    @property
    def path(self) -> Path:
        return self._path

    # --- connections ------------------------------------------------------------------------

    def initialize(self) -> None:
        """Create the file, the tables and WAL mode. Idempotent; a no-op read-only. Serialised
        within this handle and retried against other handles (see INIT_ATTEMPTS)."""
        if self._readonly or self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            last: sqlite3.Error | None = None
            for _ in range(INIT_ATTEMPTS):
                try:
                    self._path.parent.mkdir(parents=True, exist_ok=True)
                    conn = sqlite3.connect(str(self._path), timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None)
                    try:
                        conn.execute("PRAGMA journal_mode=WAL")
                        conn.execute("PRAGMA foreign_keys=ON")
                        for statement in _DDL:
                            conn.execute(statement)
                        self._migrate(conn)
                        conn.execute(
                            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                            (SCHEMA_VERSION, timeutil.utc_now_iso()),
                        )
                    finally:
                        conn.close()
                    self._initialized = True
                    return
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                        raise PersistenceError("WORKFLOW_STORE_UNAVAILABLE",
                                               f"the workflow store cannot be opened: {exc}") from exc
                    last = exc
                    time.sleep(INIT_RETRY_SECONDS)
                except sqlite3.Error as exc:
                    raise PersistenceError("WORKFLOW_STORE_UNAVAILABLE", f"the workflow store cannot be opened: {exc}") from exc
            raise PersistenceError("WORKFLOW_STORE_UNAVAILABLE",
                                   f"the workflow store stayed locked through {INIT_ATTEMPTS} opening attempts: {last}")

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Additive migrations for a file created by an earlier schema version: a column that is
        missing is added with its default. Never drops, never rewrites rows."""
        columns = {row[1] for row in conn.execute("PRAGMA table_info(steps)").fetchall()}
        for name, ddl in (
            ("requires_approval", "ALTER TABLE steps ADD COLUMN requires_approval INTEGER NOT NULL DEFAULT 0"),
            ("approval_id", "ALTER TABLE steps ADD COLUMN approval_id TEXT"),
            ("approval_plan_version", "ALTER TABLE steps ADD COLUMN approval_plan_version INTEGER"),
        ):
            if name not in columns:
                conn.execute(ddl)

    def _connect(self) -> sqlite3.Connection:
        if self._readonly:
            if not self._path.is_file():
                raise PersistenceError("WORKFLOW_STORE_UNAVAILABLE", f"no workflow store at {self._path}")
            uri = f"file:{self._path.as_posix()}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None)
        else:
            if not self._initialized:
                self.initialize()
            conn = sqlite3.connect(str(self._path), timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={int(BUSY_TIMEOUT_SECONDS * 1000)}")
        return conn

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        """One write transaction. ``BEGIN IMMEDIATE`` takes the writer lock up front, so two
        processes that both mean to write serialise here instead of failing at commit."""
        if self._readonly:
            raise WorkflowBlocked("STORE_READ_ONLY", "this handle opened the workflow store read-only")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            raise PersistenceError("WORKFLOW_STORE_WRITE_FAILED", f"the workflow store refused a write: {exc}") from exc
        finally:
            conn.close()

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        except sqlite3.Error as exc:
            raise PersistenceError("WORKFLOW_STORE_UNREADABLE", f"the workflow store cannot be read: {exc}") from exc
        finally:
            conn.close()

    # --- events -----------------------------------------------------------------------------

    @staticmethod
    def _event(conn: sqlite3.Connection, **fields: Any) -> int:
        record = wf.event_record(**fields)
        cur = conn.execute(
            "INSERT INTO events (workflow_id, step_id, attempt_id, entity, from_status, to_status,"
            " reason_code, detail, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (record["workflow_id"], record["step_id"], record["attempt_id"], record["entity"],
             record["from_status"], record["to_status"], record["reason_code"], record["detail"],
             record["created_at"]),
        )
        return int(cur.lastrowid)

    # --- submit -----------------------------------------------------------------------------

    def submit(self, *, principal: str, request_id: str, plan: Any, now: str) -> SubmitOutcome:
        """Accept a plan atomically, or replay / refuse (acceptance A03–A05).

        Validation happens before the transaction opens, so a refused plan leaves no row. Inside
        one transaction: the request mapping, the workflow, its first plan version, every step
        (those without dependencies READY at once), the dependencies and the opening events.
        The same ``(principal, request_id)`` with the same plan replays the accepted workflow;
        with a different plan it is a conflict — the id is a name, never a payload.
        """
        if not (isinstance(principal, str) and principal.strip()):
            raise WorkflowBlocked("MISSING_PRINCIPAL", "a submission needs the door's principal")
        if not (isinstance(request_id, str) and request_id.strip()):
            raise WorkflowBlocked("MISSING_REQUEST_ID", "a workflow submission carries a request_id")
        validated = wf.validate_plan(plan)
        principal = principal.strip()
        request_id = request_id.strip()
        with self._write() as conn:
            prior = conn.execute(
                "SELECT fingerprint, workflow_id, accepted_at FROM requests WHERE principal=? AND request_id=?",
                (principal, request_id),
            ).fetchone()
            if prior is not None:
                if prior["fingerprint"] != validated.plan_hash:
                    raise WorkflowBlocked(
                        "REQUEST_ID_CONFLICT",
                        f"request_id {request_id!r} was already used for a different plan; "
                        "a retry carries the same plan, a new plan carries a new id",
                    )
                row = conn.execute("SELECT status FROM workflows WHERE workflow_id=?", (prior["workflow_id"],)).fetchone()
                return SubmitOutcome(prior["workflow_id"], row["status"], prior["accepted_at"], replayed=True)

            open_steps = conn.execute(
                "SELECT COUNT(*) FROM steps WHERE status IN (?, ?, ?, ?)",
                (wf.S_PENDING, wf.S_READY, wf.S_RETRY_WAIT, wf.S_WAITING_APPROVAL),
            ).fetchone()[0]
            if open_steps + len(validated.steps) > MAX_OPEN_STEPS:
                raise WorkflowBlocked(
                    "CAPACITY_EXHAUSTED",
                    f"{open_steps} step(s) are already waiting; accepting {len(validated.steps)} more "
                    f"would exceed the server ceiling of {MAX_OPEN_STEPS}",
                )

            workflow_id = wf.workflow_id_for(principal, request_id, validated.plan_hash, now)
            conn.execute(
                "INSERT INTO workflows (workflow_id, principal, goal, status, plan_version, row_version,"
                " max_model_calls, max_tokens, cancel_reason, last_reason_code, created_at, updated_at)"
                " VALUES (?,?,?,?,1,1,?,?,NULL,NULL,?,?)",
                (workflow_id, principal, validated.goal, wf.W_VALIDATED,
                 validated.budget.max_model_calls, validated.budget.max_tokens, now, now),
            )
            conn.execute(
                "INSERT INTO plan_versions (workflow_id, version, plan_hash, validated_plan, reason, created_at)"
                " VALUES (?,1,?,?,?,?)",
                (workflow_id, validated.plan_hash, json.dumps(validated.plan, ensure_ascii=False, sort_keys=True),
                 "submitted", now),
            )
            self._event(conn, workflow_id=workflow_id, entity="workflow", to_status=wf.W_RECEIVED, created_at=now)
            self._event(conn, workflow_id=workflow_id, entity="workflow", from_status=wf.W_RECEIVED,
                        to_status=wf.W_VALIDATED, created_at=now)
            for position, key in enumerate(validated.order):
                step = validated.step(key)
                step_id = wf.step_id_for(workflow_id, key)
                if step.depends_on:
                    status = wf.S_PENDING
                else:
                    status = wf.S_WAITING_APPROVAL if step.requires_approval else wf.S_READY
                self._insert_step(conn, workflow_id, key, position, step, status, now)
                self._event(conn, workflow_id=workflow_id, step_id=step_id, entity="step",
                            to_status=wf.S_PENDING, created_at=now)
                if status != wf.S_PENDING:
                    self._event(conn, workflow_id=workflow_id, step_id=step_id, entity="step",
                                from_status=wf.S_PENDING, to_status=status, created_at=now)
            conn.execute(
                "INSERT INTO requests (principal, request_id, fingerprint, workflow_id, accepted_at) VALUES (?,?,?,?,?)",
                (principal, request_id, validated.plan_hash, workflow_id, now),
            )
            # A gated root step waits for Thomas from acceptance: the workflow says so at once.
            # With only READY/PENDING steps the projection keeps VALIDATED until the first claim.
            self._recompute_workflow(conn, workflow_id, now)
            status = conn.execute("SELECT status FROM workflows WHERE workflow_id=?", (workflow_id,)).fetchone()["status"]
        return SubmitOutcome(workflow_id, status, now, replayed=False)

    @staticmethod
    def _insert_step(conn: sqlite3.Connection, workflow_id: str, key: str, position: int,
                     step: wf.PlanStep, status: str, now: str) -> str:
        step_id = wf.step_id_for(workflow_id, key)
        conn.execute(
            "INSERT INTO steps (step_id, workflow_id, step_key, position, capability, effect_class,"
            " request, reason, options, input_refs, naver_keywords, max_attempts, status,"
            " attempts_opened, current_attempt_id, result_ref, last_reason_code, row_version, updated_at,"
            " requires_approval, approval_id, approval_plan_version)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,NULL,NULL,NULL,1,?,?,NULL,NULL)",
            (step_id, workflow_id, key, position, step.capability, step.effect_class,
             step.request, step.reason, json.dumps(step.options, sort_keys=True),
             json.dumps(list(step.input_refs)), step.naver_keywords, step.max_attempts, status, now,
             1 if step.requires_approval else 0),
        )
        for dep in step.depends_on:
            conn.execute("INSERT INTO dependencies (step_id, depends_on) VALUES (?, ?)",
                         (step_id, wf.step_id_for(workflow_id, dep)))
        return step_id

    # --- reads ------------------------------------------------------------------------------

    def get_workflow(self, workflow_id: str) -> dict[str, Any] | None:
        with self._read() as conn:
            row = conn.execute("SELECT * FROM workflows WHERE workflow_id=?", (workflow_id,)).fetchone()
            return dict(row) if row is not None else None

    def find_request(self, principal: str, request_id: str) -> dict[str, Any] | None:
        with self._read() as conn:
            row = conn.execute("SELECT * FROM requests WHERE principal=? AND request_id=?",
                               (principal, request_id)).fetchone()
            return dict(row) if row is not None else None

    def status_view(self, workflow_id: str, *, now: str) -> dict[str, Any]:
        """The structured view a door returns for ``workflow.status``: the workflow, each step
        with its current attempt and result reference, the budget summary, and ``as_of``."""
        with self._read() as conn:
            row = conn.execute("SELECT * FROM workflows WHERE workflow_id=?", (workflow_id,)).fetchone()
            if row is None:
                raise WorkflowBlocked("WORKFLOW_NOT_FOUND", f"no workflow {workflow_id}")
            steps = conn.execute("SELECT * FROM steps WHERE workflow_id=? ORDER BY position", (workflow_id,)).fetchall()
            deps = conn.execute(
                "SELECT d.step_id, s.step_key FROM dependencies d JOIN steps s ON s.step_id = d.depends_on"
                " WHERE d.step_id IN (SELECT step_id FROM steps WHERE workflow_id=?)", (workflow_id,)).fetchall()
            by_step: dict[str, list[str]] = {}
            for d in deps:
                by_step.setdefault(d["step_id"], []).append(d["step_key"])
            budget = self._budget_locked(conn, workflow_id)
            view = {
                "workflow_id": workflow_id, "status": row["status"], "goal": row["goal"],
                "principal": row["principal"], "plan_version": row["plan_version"],
                "row_version": row["row_version"], "created_at": row["created_at"],
                "updated_at": row["updated_at"], "last_reason_code": row["last_reason_code"],
                "cancel_reason": row["cancel_reason"], "as_of": now, "source": "workflow_store",
                "budget": budget,
                "steps": [
                    {
                        "step_id": s["step_id"], "key": s["step_key"], "capability": s["capability"],
                        "effect_class": s["effect_class"], "status": s["status"],
                        "attempts_opened": s["attempts_opened"], "max_attempts": s["max_attempts"],
                        "current_attempt_id": s["current_attempt_id"], "result_ref": s["result_ref"],
                        "last_reason_code": s["last_reason_code"], "row_version": s["row_version"],
                        "depends_on": sorted(by_step.get(s["step_id"], [])),
                        "input_refs": sorted(json.loads(s["input_refs"])),
                        "inputs": self._input_refs_locked(conn, s),          # P07: resolved, live
                        "requires_approval": bool(s["requires_approval"]),
                        "approval_id": s["approval_id"], "approval_plan_version": s["approval_plan_version"],
                    }
                    for s in steps
                ],
            }
        return view

    def list_workflows(self, *, limit: int = 50, statuses: Iterable[str] | None = None) -> list[dict[str, Any]]:
        with self._read() as conn:
            if statuses:
                wanted = tuple(statuses)
                rows = conn.execute(
                    f"SELECT * FROM workflows WHERE status IN ({','.join('?' * len(wanted))})"
                    " ORDER BY created_at DESC, workflow_id LIMIT ?", (*wanted, int(limit))).fetchall()
            else:
                rows = conn.execute("SELECT * FROM workflows ORDER BY created_at DESC, workflow_id LIMIT ?",
                                    (int(limit),)).fetchall()
            return [dict(r) for r in rows]

    def events_after(self, cursor: int, *, limit: int = 100) -> tuple[list[dict[str, Any]], int]:
        """Events with ``cursor > after``, oldest first, and the next cursor to ask from. The
        same call twice returns the same rows: cursors are the table's own rowids."""
        limit = max(1, min(int(limit), 500))
        with self._read() as conn:
            rows = conn.execute("SELECT * FROM events WHERE cursor > ? ORDER BY cursor LIMIT ?",
                                (int(cursor), limit)).fetchall()
        events = [dict(r) for r in rows]
        next_cursor = events[-1]["cursor"] if events else int(cursor)
        return events, next_cursor

    def open_step_count(self) -> int:
        with self._read() as conn:
            return int(conn.execute(
                "SELECT COUNT(*) FROM steps WHERE status IN (?, ?, ?, ?)",
                (wf.S_PENDING, wf.S_READY, wf.S_RETRY_WAIT, wf.S_WAITING_APPROVAL)).fetchone()[0])

    def overdue_attempts(self, *, now: str) -> list[dict[str, Any]]:
        """RUNNING attempts past their lease — the ones the manager reconciles against the
        registry before `expire_overdue` lapses what is left."""
        with self._read() as conn:
            rows = conn.execute("SELECT * FROM attempts WHERE status=? AND deadline_at < ? ORDER BY deadline_at",
                                (wf.A_RUNNING, now)).fetchall()
            return [dict(r) for r in rows]

    def running_attempts(self) -> list[dict[str, Any]]:
        """Every attempt still RUNNING — after a restart, the ones whose connection died with
        the previous process and will lapse at their leases unless reconciled (P06)."""
        with self._read() as conn:
            rows = conn.execute("SELECT * FROM attempts WHERE status=? ORDER BY opened_at", (wf.A_RUNNING,)).fetchall()
            return [dict(r) for r in rows]

    def attempt(self, attempt_id: str) -> dict[str, Any] | None:
        with self._read() as conn:
            row = conn.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            return dict(row) if row is not None else None

    def budget_summary(self, workflow_id: str) -> dict[str, Any]:
        with self._read() as conn:
            return self._budget_locked(conn, workflow_id)

    @staticmethod
    def _budget_locked(conn: sqlite3.Connection, workflow_id: str) -> dict[str, Any]:
        row = conn.execute("SELECT max_model_calls, max_tokens FROM workflows WHERE workflow_id=?",
                           (workflow_id,)).fetchone()
        if row is None:
            raise WorkflowBlocked("WORKFLOW_NOT_FOUND", f"no workflow {workflow_id}")
        agg = conn.execute(
            "SELECT COALESCE(SUM(reserved_model_calls), 0) AS reserved,"
            " COALESCE(SUM(CASE WHEN cost_status <> ? THEN used_model_calls ELSE 0 END), 0) AS confirmed_calls,"
            " COALESCE(SUM(CASE WHEN cost_status <> ? THEN used_tokens ELSE 0 END), 0) AS confirmed_tokens,"
            " COALESCE(SUM(CASE WHEN cost_status = ? THEN reserved_model_calls ELSE 0 END), 0) AS unconfirmed_calls"
            " FROM budget_reservations WHERE workflow_id=?",
            (COST_UNCONFIRMED, COST_UNCONFIRMED, COST_UNCONFIRMED, workflow_id)).fetchone()
        return {
            "max_model_calls": row["max_model_calls"], "max_tokens": row["max_tokens"],
            # What counts against the cap: every reservation ever made. An attempt whose spend
            # was never confirmed keeps its reservation — it may have called the model.
            "reserved_model_calls": int(agg["reserved"]),
            "confirmed_model_calls": int(agg["confirmed_calls"]),
            "confirmed_tokens": int(agg["confirmed_tokens"]),
            "unconfirmed_model_calls": int(agg["unconfirmed_calls"]),
            "remaining_model_calls": int(row["max_model_calls"]) - int(agg["reserved"]),
        }

    # --- claim ------------------------------------------------------------------------------

    def claim_ready(self, *, now: str, limit: int = 1,
                    deadline_seconds: int = DEFAULT_ATTEMPT_DEADLINE_SECONDS) -> list[ClaimedAttempt]:
        """Open attempts for READY steps, oldest workflow first, up to ``limit``.

        One transaction per call: the step goes RUNNING, the attempt is inserted RUNNING with its
        lease, the budget reservation is taken, and the workflow goes RUNNING on its first claim.
        A step whose reservation would exceed the workflow's cap is BLOCKED (``BUDGET_EXHAUSTED``)
        instead — the manager never dispatches what it cannot pay for.
        """
        claimed: list[ClaimedAttempt] = []
        deadline = timeutil.plus_seconds(now, int(deadline_seconds))
        with self._write() as conn:
            rows = conn.execute(
                "SELECT s.* FROM steps s JOIN workflows w ON w.workflow_id = s.workflow_id"
                " WHERE s.status=? AND w.status IN (?, ?)"
                " ORDER BY w.created_at, w.workflow_id, s.position LIMIT ?",
                (wf.S_READY, wf.W_VALIDATED, wf.W_RUNNING, max(1, int(limit)))).fetchall()
            for s in rows:
                step_id, workflow_id = s["step_id"], s["workflow_id"]
                options = json.loads(s["options"])
                calls = 1 + int(bool(options.get("independent_validation"))) + int(bool(options.get("revise")))
                budget = self._budget_locked(conn, workflow_id)
                over_tokens = (budget["max_tokens"] is not None
                               and budget["confirmed_tokens"] >= budget["max_tokens"])
                if budget["remaining_model_calls"] < calls or over_tokens:
                    self._set_step(conn, s, wf.S_BLOCKED, now, reason_code=wf.BUDGET_EXHAUSTED)
                    self._block_dependents(conn, step_id, workflow_id, now)
                    self._recompute_workflow(conn, workflow_id, now, reason_code=wf.BUDGET_EXHAUSTED)
                    continue
                number = int(s["attempts_opened"]) + 1
                attempt_id = wf.attempt_id_for(step_id, number)
                conn.execute(
                    "INSERT INTO attempts (attempt_id, step_id, workflow_id, attempt_number, status, opened_at,"
                    " deadline_at, closed_at, trace_id, registry_entry_id, result_ref, reason_code)"
                    " VALUES (?,?,?,?,?,?,?,NULL,NULL,NULL,NULL,NULL)",
                    (attempt_id, step_id, workflow_id, number, wf.A_RUNNING, now, deadline))
                conn.execute(
                    "INSERT INTO budget_reservations (attempt_id, workflow_id, step_id, reserved_model_calls,"
                    " cost_status, used_model_calls, used_tokens, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,NULL,NULL,?,?)",
                    (attempt_id, workflow_id, step_id, calls, COST_UNCONFIRMED, now, now))
                conn.execute(
                    "UPDATE steps SET status=?, attempts_opened=?, current_attempt_id=?, last_reason_code=NULL,"
                    " row_version=row_version+1, updated_at=? WHERE step_id=?",
                    (wf.S_RUNNING, number, attempt_id, now, step_id))
                self._event(conn, workflow_id=workflow_id, step_id=step_id, attempt_id=attempt_id,
                            entity="attempt", to_status=wf.A_RUNNING, created_at=now,
                            detail=f"attempt {number} of {s['max_attempts']}, lease until {deadline}")
                self._event(conn, workflow_id=workflow_id, step_id=step_id, attempt_id=attempt_id,
                            entity="step", from_status=wf.S_READY, to_status=wf.S_RUNNING, created_at=now)
                w = conn.execute("SELECT status FROM workflows WHERE workflow_id=?", (workflow_id,)).fetchone()
                if w["status"] == wf.W_VALIDATED:
                    self._set_workflow(conn, workflow_id, wf.W_VALIDATED, wf.W_RUNNING, now)
                input_refs = self._input_refs_locked(conn, s)
                claimed.append(ClaimedAttempt(
                    attempt_id=attempt_id, attempt_number=number, step_id=step_id, step_key=s["step_key"],
                    workflow_id=workflow_id, capability=s["capability"], effect_class=s["effect_class"],
                    request=s["request"], reason=s["reason"], naver_keywords=s["naver_keywords"],
                    options={k: bool(v) for k, v in options.items()}, input_refs=input_refs, deadline_at=deadline,
                ))
        return claimed

    @staticmethod
    def _input_refs_locked(conn: sqlite3.Connection, step: sqlite3.Row) -> dict[str, str | None]:
        keys = json.loads(step["input_refs"])
        out: dict[str, str | None] = {}
        for key in keys:
            row = conn.execute("SELECT result_ref FROM steps WHERE workflow_id=? AND step_key=?",
                               (step["workflow_id"], key)).fetchone()
            out[key] = row["result_ref"] if row is not None else None
        return out

    # --- results ----------------------------------------------------------------------------

    def record_result(
        self, attempt_id: str, *, now: str, succeeded: bool,
        trace_id: str | None = None, registry_entry_id: str | None = None, result_ref: str | None = None,
        reason_code: str | None = None, model_calls: int | None = None, tokens: int | None = None,
        cost_status: str = COST_OBSERVED,
    ) -> dict[str, Any]:
        """Apply a worker's result to its attempt — if the attempt is still the step's current
        one and still RUNNING. Otherwise the result is a late one: recorded as an event, never
        applied (acceptance A07). ``model_calls``/``tokens`` confirm the reservation; without
        them the reservation stays unconfirmed and keeps counting (A12).
        """
        fenced: str | None = None
        with self._write() as conn:
            a = conn.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if a is None:
                raise WorkflowBlocked("ATTEMPT_NOT_FOUND", f"no attempt {attempt_id}")
            s = conn.execute("SELECT * FROM steps WHERE step_id=?", (a["step_id"],)).fetchone()
            if a["status"] != wf.A_RUNNING or s["current_attempt_id"] != attempt_id:
                # The late-result event must outlive the refusal, so it is committed by this
                # transaction and the refusal is raised after it — a raise inside would roll the
                # record of the late arrival back with everything else.
                self._event(conn, workflow_id=a["workflow_id"], step_id=a["step_id"], attempt_id=attempt_id,
                            entity="attempt", from_status=a["status"], to_status=a["status"],
                            reason_code="LATE_RESULT", created_at=now,
                            detail=f"result arrived for attempt {attempt_id} ({'success' if succeeded else 'failure'})"
                                   f" after it stopped being the step's current attempt; not applied")
                fenced = (f"attempt {attempt_id} is {a['status']} and the step's current attempt is "
                          f"{s['current_attempt_id']}; the result was recorded as late and not applied")
        if fenced is not None:
            raise WorkflowBlocked("ATTEMPT_FENCED", fenced)
        with self._write() as conn:
            a = conn.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            s = conn.execute("SELECT * FROM steps WHERE step_id=?", (a["step_id"],)).fetchone()
            if a["status"] != wf.A_RUNNING or s["current_attempt_id"] != attempt_id:
                raise WorkflowBlocked("ATTEMPT_FENCED", f"attempt {attempt_id} stopped being current between two reads")
            target = wf.A_SUCCEEDED if succeeded else wf.A_FAILED
            wf.assert_transition("attempt", a["status"], target, attempt_id)
            conn.execute(
                "UPDATE attempts SET status=?, closed_at=?, trace_id=?, registry_entry_id=?, result_ref=?, reason_code=?"
                " WHERE attempt_id=?",
                (target, now, trace_id, registry_entry_id, result_ref, reason_code, attempt_id))
            if model_calls is not None or tokens is not None:
                conn.execute(
                    "UPDATE budget_reservations SET cost_status=?, used_model_calls=?, used_tokens=?, updated_at=?"
                    " WHERE attempt_id=?",
                    (cost_status if cost_status in (COST_OBSERVED, COST_ESTIMATED) else COST_ESTIMATED,
                     int(model_calls or 0), int(tokens or 0), now, attempt_id))
            self._event(conn, workflow_id=a["workflow_id"], step_id=a["step_id"], attempt_id=attempt_id,
                        entity="attempt", from_status=wf.A_RUNNING, to_status=target,
                        reason_code=reason_code, created_at=now)
            workflow_id = a["workflow_id"]
            if succeeded:
                self._set_step(conn, s, wf.S_SUCCEEDED, now, result_ref=result_ref, attempt_id=attempt_id)
                self._release_dependents(conn, workflow_id, now)
            else:
                retriable = (s["effect_class"] == wf.EFFECT_NONE and int(s["attempts_opened"]) < int(s["max_attempts"])
                             and s["status"] == wf.S_RUNNING)
                if retriable:
                    self._set_step(conn, s, wf.S_RETRY_WAIT, now, reason_code=reason_code, attempt_id=attempt_id)
                    s2 = conn.execute("SELECT * FROM steps WHERE step_id=?", (s["step_id"],)).fetchone()
                    self._set_step(conn, s2, wf.S_READY, now, attempt_id=attempt_id)
                else:
                    self._set_step(conn, s, wf.S_FAILED, now, reason_code=reason_code, attempt_id=attempt_id)
                    self._block_dependents(conn, s["step_id"], workflow_id, now)
            self._recompute_workflow(conn, workflow_id, now, reason_code=None if succeeded else reason_code)
            w = conn.execute("SELECT status, row_version FROM workflows WHERE workflow_id=?", (workflow_id,)).fetchone()
            s_after = conn.execute("SELECT status, attempts_opened FROM steps WHERE step_id=?", (s["step_id"],)).fetchone()
        return {"workflow_id": workflow_id, "workflow_status": w["status"], "step_id": s["step_id"],
                "step_status": s_after["status"], "attempt_status": target}

    def expire_overdue(self, *, now: str) -> list[dict[str, Any]]:
        """Lapse every RUNNING attempt past its lease (acceptance A08). ``none``: the step may
        open a new attempt while attempts remain, else it FAILS; ``external``: the step waits in
        NEEDS_RECONCILIATION. The lapsed reservation is never refunded."""
        expired: list[dict[str, Any]] = []
        with self._write() as conn:
            rows = conn.execute("SELECT * FROM attempts WHERE status=? AND deadline_at < ?", (wf.A_RUNNING, now)).fetchall()
            for a in rows:
                conn.execute("UPDATE attempts SET status=?, closed_at=?, reason_code=? WHERE attempt_id=?",
                             (wf.A_EXPIRED, now, "ATTEMPT_EXPIRED", a["attempt_id"]))
                self._event(conn, workflow_id=a["workflow_id"], step_id=a["step_id"], attempt_id=a["attempt_id"],
                            entity="attempt", from_status=wf.A_RUNNING, to_status=wf.A_EXPIRED,
                            reason_code="ATTEMPT_EXPIRED", created_at=now,
                            detail=f"lease {a['deadline_at']} passed; the reservation stays counted")
                s = conn.execute("SELECT * FROM steps WHERE step_id=?", (a["step_id"],)).fetchone()
                if s["current_attempt_id"] != a["attempt_id"] or s["status"] not in (wf.S_RUNNING, wf.S_CANCEL_REQUESTED):
                    continue
                if s["status"] == wf.S_CANCEL_REQUESTED:
                    self._set_step(conn, s, wf.S_CANCELLED, now, reason_code="ATTEMPT_EXPIRED", attempt_id=a["attempt_id"])
                elif s["effect_class"] != wf.EFFECT_NONE:
                    self._set_step(conn, s, wf.S_NEEDS_RECONCILIATION, now, reason_code="ATTEMPT_EXPIRED",
                                   attempt_id=a["attempt_id"])
                elif int(s["attempts_opened"]) < int(s["max_attempts"]):
                    self._set_step(conn, s, wf.S_RETRY_WAIT, now, reason_code="ATTEMPT_EXPIRED", attempt_id=a["attempt_id"])
                    s2 = conn.execute("SELECT * FROM steps WHERE step_id=?", (s["step_id"],)).fetchone()
                    self._set_step(conn, s2, wf.S_READY, now, attempt_id=a["attempt_id"])
                else:
                    self._set_step(conn, s, wf.S_FAILED, now, reason_code="ATTEMPT_EXPIRED", attempt_id=a["attempt_id"])
                    self._block_dependents(conn, s["step_id"], a["workflow_id"], now)
                self._recompute_workflow(conn, a["workflow_id"], now, reason_code="ATTEMPT_EXPIRED")
                expired.append({"attempt_id": a["attempt_id"], "step_id": a["step_id"], "workflow_id": a["workflow_id"],
                                "effect_class": s["effect_class"]})
        return expired

    # --- cancel -----------------------------------------------------------------------------

    def request_cancel(self, workflow_id: str, *, expected_version: int, reason: str, now: str) -> dict[str, Any]:
        """Record a cancel request at the version the caller saw (acceptance A11). Waiting steps
        are CANCELLED at once; a RUNNING step becomes CANCEL_REQUESTED and is CANCELLED only
        when its attempt stops (``confirm_cancelled``) or lapses. The workflow is CANCELLING
        until then — never CANCELLED before the cancellation is real."""
        with self._write() as conn:
            w = conn.execute("SELECT * FROM workflows WHERE workflow_id=?", (workflow_id,)).fetchone()
            if w is None:
                raise WorkflowBlocked("WORKFLOW_NOT_FOUND", f"no workflow {workflow_id}")
            if w["status"] in wf.WORKFLOW_TERMINAL:
                raise WorkflowBlocked("WORKFLOW_TERMINAL", f"workflow {workflow_id} is already {w['status']}")
            if int(w["row_version"]) != int(expected_version):
                raise WorkflowBlocked(
                    "VERSION_CONFLICT",
                    f"workflow {workflow_id} is at version {w['row_version']}, not {expected_version}; re-read and retry",
                )
            steps = conn.execute("SELECT * FROM steps WHERE workflow_id=?", (workflow_id,)).fetchall()
            for s in steps:
                if s["status"] in (wf.S_PENDING, wf.S_READY, wf.S_RETRY_WAIT, wf.S_WAITING_APPROVAL,
                                   wf.S_FAILED, wf.S_BLOCKED, wf.S_NEEDS_RECONCILIATION):
                    self._set_step(conn, s, wf.S_CANCELLED, now, reason_code="CANCEL_REQUESTED")
                elif s["status"] == wf.S_RUNNING:
                    self._set_step(conn, s, wf.S_CANCEL_REQUESTED, now, reason_code="CANCEL_REQUESTED")
            conn.execute("UPDATE workflows SET cancel_reason=? WHERE workflow_id=?", (str(reason)[:2000], workflow_id))
            self._recompute_workflow(conn, workflow_id, now, reason_code="CANCEL_REQUESTED", cancelling=True)
        return self.status_view(workflow_id, now=now)

    def retry_step(self, workflow_id: str, step_key: str, *, expected_version: int, reason: str, now: str) -> dict[str, Any]:
        """A decision re-opens a settled step (V0.2 §4.2 `workflow.retry_step`; acceptance A16).

        Allowed on a FAILED step below the hard attempt cap, a budget-blocked step once the
        budget covers one more attempt, and a NEEDS_RECONCILIATION step a person has looked at.
        Refused on a step blocked by a failed dependency (retry the dependency instead), on a
        step at the cap (`ATTEMPTS_EXHAUSTED`), and on a workflow that has ended. The step goes
        READY (PENDING if a dependency is not yet SUCCEEDED) with one more attempt allowed, its
        dependents blocked by it come back to PENDING, and the workflow runs again. Succeeded
        steps are untouched — nothing already delivered is re-run.
        """
        with self._write() as conn:
            w = conn.execute("SELECT * FROM workflows WHERE workflow_id=?", (workflow_id,)).fetchone()
            if w is None:
                raise WorkflowBlocked("WORKFLOW_NOT_FOUND", f"no workflow {workflow_id}")
            if w["status"] in wf.WORKFLOW_TERMINAL:
                raise WorkflowBlocked("WORKFLOW_TERMINAL", f"workflow {workflow_id} is already {w['status']}")
            if int(w["row_version"]) != int(expected_version):
                raise WorkflowBlocked(
                    "VERSION_CONFLICT",
                    f"workflow {workflow_id} is at version {w['row_version']}, not {expected_version}; re-read and retry",
                )
            s = conn.execute("SELECT * FROM steps WHERE workflow_id=? AND step_key=?", (workflow_id, step_key)).fetchone()
            if s is None:
                raise WorkflowBlocked("STEP_NOT_FOUND", f"workflow {workflow_id} has no step {step_key!r}")
            if s["status"] == wf.S_BLOCKED and s["last_reason_code"] == wf.DEPENDENCY_FAILED:
                raise WorkflowBlocked(
                    "RETRY_NOT_APPLICABLE",
                    f"step {step_key!r} is blocked by a failed dependency; retry that step instead",
                )
            if s["status"] not in (wf.S_FAILED, wf.S_BLOCKED, wf.S_NEEDS_RECONCILIATION):
                raise WorkflowBlocked("RETRY_NOT_APPLICABLE", f"step {step_key!r} is {s['status']}; only a settled step is retried")
            if int(s["attempts_opened"]) >= wf.MAX_ATTEMPTS_PER_STEP:
                raise WorkflowBlocked(
                    "ATTEMPTS_EXHAUSTED",
                    f"step {step_key!r} has opened {s['attempts_opened']} attempts, the cap of {wf.MAX_ATTEMPTS_PER_STEP}",
                )
            options = json.loads(s["options"])
            calls = 1 + int(bool(options.get("independent_validation"))) + int(bool(options.get("revise")))
            if self._budget_locked(conn, workflow_id)["remaining_model_calls"] < calls:
                raise WorkflowBlocked(
                    wf.BUDGET_EXHAUSTED, f"the workflow's budget does not cover one more attempt of {step_key!r}",
                )
            deps = [d["depends_on"] for d in conn.execute("SELECT depends_on FROM dependencies WHERE step_id=?", (s["step_id"],)).fetchall()]
            dep_ok = all(conn.execute("SELECT status FROM steps WHERE step_id=?", (d,)).fetchone()["status"] == wf.S_SUCCEEDED for d in deps)
            if not dep_ok:
                target = wf.S_PENDING
            elif s["requires_approval"]:
                target = wf.S_WAITING_APPROVAL      # a gated step asks again; the old grant is spent or dead
            else:
                target = wf.S_READY
            conn.execute("UPDATE steps SET max_attempts=?, approval_id=NULL, approval_plan_version=NULL WHERE step_id=?",
                         (max(int(s["max_attempts"]), int(s["attempts_opened"]) + 1), s["step_id"]))
            self._set_step(conn, s, target, now, reason_code=None)
            self._event(conn, workflow_id=workflow_id, step_id=s["step_id"], entity="step", from_status=target,
                        to_status=target, reason_code="RETRY_REQUESTED", created_at=now, detail=str(reason)[:2000])
            self._unblock_dependents(conn, s["step_id"], now)
            self._recompute_workflow(conn, workflow_id, now, reason_code=None)
        return self.status_view(workflow_id, now=now)

    def _unblock_dependents(self, conn: sqlite3.Connection, step_id: str, now: str) -> None:
        """Steps blocked because this one failed come back to PENDING, transitively — they will
        wait for it again through the ordinary release."""
        frontier = [step_id]
        seen: set[str] = set()
        while frontier:
            current = frontier.pop()
            for d in conn.execute("SELECT step_id FROM dependencies WHERE depends_on=?", (current,)).fetchall():
                dep_id = d["step_id"]
                if dep_id in seen:
                    continue
                seen.add(dep_id)
                s = conn.execute("SELECT * FROM steps WHERE step_id=?", (dep_id,)).fetchone()
                if s["status"] == wf.S_BLOCKED and s["last_reason_code"] == wf.DEPENDENCY_FAILED:
                    self._set_step(conn, s, wf.S_PENDING, now, reason_code=None)
                frontier.append(dep_id)

    # --- gated steps (P07) ----------------------------------------------------------------------

    def waiting_approval_steps(self) -> list[dict[str, Any]]:
        """Steps waiting for Thomas, with what the manager needs to mint or spend their ask."""
        with self._read() as conn:
            rows = conn.execute(
                "SELECT s.*, w.plan_version AS workflow_plan_version, w.goal AS goal FROM steps s"
                " JOIN workflows w ON w.workflow_id = s.workflow_id WHERE s.status=? ORDER BY w.created_at, s.position",
                (wf.S_WAITING_APPROVAL,)).fetchall()
            return [dict(r) for r in rows]

    def bind_approval(self, step_id: str, *, approval_id: str, plan_version: int, now: str) -> None:
        """Record which ask a waiting step is bound to (the ask itself lives in the approval store)."""
        with self._write() as conn:
            s = conn.execute("SELECT * FROM steps WHERE step_id=?", (step_id,)).fetchone()
            if s is None or s["status"] != wf.S_WAITING_APPROVAL:
                raise WorkflowBlocked("TRANSITION_INVALID", f"step {step_id} is not waiting for an approval")
            conn.execute("UPDATE steps SET approval_id=?, approval_plan_version=?, row_version=row_version+1, updated_at=?"
                         " WHERE step_id=?", (approval_id, int(plan_version), now, step_id))
            self._event(conn, workflow_id=s["workflow_id"], step_id=step_id, entity="step",
                        from_status=wf.S_WAITING_APPROVAL, to_status=wf.S_WAITING_APPROVAL,
                        reason_code="APPROVAL_REQUESTED", created_at=now, detail=approval_id)

    def approve_step(self, step_id: str, *, approval_id: str, now: str) -> dict[str, Any]:
        """The bound grant was spent (by the manager, through the shared single-use ladder): the
        step is READY. The store checks only that the spent id is the bound one."""
        with self._write() as conn:
            s = conn.execute("SELECT * FROM steps WHERE step_id=?", (step_id,)).fetchone()
            if s is None or s["status"] != wf.S_WAITING_APPROVAL:
                raise WorkflowBlocked("TRANSITION_INVALID", f"step {step_id} is not waiting for an approval")
            if s["approval_id"] != approval_id:
                raise WorkflowBlocked("APPROVAL_NOT_BOUND", f"step {step_id} is bound to {s['approval_id']!r}, not {approval_id!r}")
            self._set_step(conn, s, wf.S_READY, now, reason_code=None)
            self._event(conn, workflow_id=s["workflow_id"], step_id=step_id, entity="step",
                        from_status=wf.S_READY, to_status=wf.S_READY, reason_code="APPROVAL_CONSUMED",
                        created_at=now, detail=approval_id)
            self._recompute_workflow(conn, s["workflow_id"], now)
        return self.status_view(s["workflow_id"], now=now)

    def refuse_step_approval(self, step_id: str, *, reason_code: str, detail: str, now: str) -> dict[str, Any]:
        """The ask was rejected, expired, spent elsewhere or no longer describes the step: the
        step is BLOCKED under that reason and the workflow waits for a decision."""
        with self._write() as conn:
            s = conn.execute("SELECT * FROM steps WHERE step_id=?", (step_id,)).fetchone()
            if s is None or s["status"] != wf.S_WAITING_APPROVAL:
                raise WorkflowBlocked("TRANSITION_INVALID", f"step {step_id} is not waiting for an approval")
            self._set_step(conn, s, wf.S_BLOCKED, now, reason_code=reason_code)
            self._event(conn, workflow_id=s["workflow_id"], step_id=step_id, entity="step",
                        from_status=wf.S_BLOCKED, to_status=wf.S_BLOCKED, reason_code=reason_code,
                        created_at=now, detail=detail)
            self._block_dependents(conn, step_id, s["workflow_id"], now)
            self._recompute_workflow(conn, s["workflow_id"], now, reason_code=reason_code)
        return self.status_view(s["workflow_id"], now=now)

    # --- plan versions (P07) -------------------------------------------------------------------

    _IMMUTABLE_ONCE_STARTED = ("capability", "request", "depends_on", "input_refs", "naver_keywords")

    def propose_update(self, workflow_id: str, *, expected_version: int, plan: Any, reason: str, now: str) -> dict[str, Any]:
        """A new plan version for a workflow that has not ended (V0.2 §4.2 `workflow.propose_update`).

        What may change: steps not yet started (their request, options, dependencies, gate,
        attempts), steps added, steps not yet started removed (CANCELLED as PLAN_UPDATED), the
        goal, and the budget — never below what is already reserved. What may not: a step that is
        running or delivered (its capability, request, dependencies and inputs are what its result
        answers; `PLAN_CONFLICT`), and a cancelled step's key. A gated step whose request changed
        loses its bound ask (the grant no longer describes it) and asks again; a budget-blocked step
        the new budget covers is released. Nothing already delivered is re-run.
        """
        validated = wf.validate_plan(plan)
        with self._write() as conn:
            w = conn.execute("SELECT * FROM workflows WHERE workflow_id=?", (workflow_id,)).fetchone()
            if w is None:
                raise WorkflowBlocked("WORKFLOW_NOT_FOUND", f"no workflow {workflow_id}")
            if w["status"] in wf.WORKFLOW_TERMINAL:
                raise WorkflowBlocked("WORKFLOW_TERMINAL", f"workflow {workflow_id} is already {w['status']}")
            if int(w["row_version"]) != int(expected_version):
                raise WorkflowBlocked("VERSION_CONFLICT",
                                      f"workflow {workflow_id} is at version {w['row_version']}, not {expected_version}; re-read and retry")
            current = {s["step_key"]: s for s in conn.execute("SELECT * FROM steps WHERE workflow_id=?", (workflow_id,)).fetchall()}
            new_keys = {s.key for s in validated.steps}
            budget = self._budget_locked(conn, workflow_id)
            if validated.budget.max_model_calls < budget["reserved_model_calls"]:
                raise WorkflowBlocked("PLAN_CONFLICT",
                                      f"the new budget ({validated.budget.max_model_calls} calls) is below what is already reserved "
                                      f"({budget['reserved_model_calls']})")
            started = (wf.S_RUNNING, wf.S_CANCEL_REQUESTED, wf.S_SUCCEEDED)
            for key, s in current.items():
                if s["status"] in started or s["status"] == wf.S_CANCELLED:
                    if key not in new_keys:
                        if s["status"] == wf.S_CANCELLED:
                            continue
                        raise WorkflowBlocked("PLAN_CONFLICT", f"step {key!r} is {s['status']} and cannot be removed")
                    if s["status"] == wf.S_CANCELLED:
                        raise WorkflowBlocked("PLAN_CONFLICT", f"step {key!r} was cancelled; add it back under a new key")
                    new = validated.step(key)
                    stored = {"capability": s["capability"], "request": s["request"],
                              "depends_on": tuple(sorted(self._dep_keys_locked(conn, s["step_id"]))),
                              "input_refs": tuple(sorted(json.loads(s["input_refs"]))), "naver_keywords": s["naver_keywords"]}
                    proposed = {"capability": new.capability, "request": new.request,
                                "depends_on": tuple(sorted(new.depends_on)), "input_refs": tuple(sorted(new.input_refs)),
                                "naver_keywords": new.naver_keywords}
                    changed = [f for f in self._IMMUTABLE_ONCE_STARTED if stored[f] != proposed[f]]
                    if changed:
                        raise WorkflowBlocked("PLAN_CONFLICT",
                                              f"step {key!r} is {s['status']}; {changed} cannot change under a new version")
            version = int(w["plan_version"]) + 1
            conn.execute(
                "INSERT INTO plan_versions (workflow_id, version, plan_hash, validated_plan, reason, created_at) VALUES (?,?,?,?,?,?)",
                (workflow_id, version, validated.plan_hash, json.dumps(validated.plan, ensure_ascii=False, sort_keys=True),
                 str(reason)[:2000], now))
            conn.execute(
                "UPDATE workflows SET goal=?, plan_version=?, max_model_calls=?, max_tokens=?, row_version=row_version+1, updated_at=?"
                " WHERE workflow_id=?",
                (validated.goal, version, validated.budget.max_model_calls, validated.budget.max_tokens, now, workflow_id))
            self._event(conn, workflow_id=workflow_id, entity="workflow", from_status=w["status"], to_status=w["status"],
                        reason_code=wf.PLAN_UPDATED, created_at=now, detail=f"plan v{version}: {str(reason)[:1900]}")
            # steps not started and absent from the new plan are cancelled
            for key, s in current.items():
                if key not in new_keys and s["status"] not in started and s["status"] != wf.S_CANCELLED:
                    self._set_step(conn, s, wf.S_CANCELLED, now, reason_code=wf.PLAN_UPDATED)
            # dependencies are rebuilt for every step (started ones were checked unchanged)
            for s in current.values():
                conn.execute("DELETE FROM dependencies WHERE step_id=?", (s["step_id"],))
            for position, key in enumerate(validated.order):
                step = validated.step(key)
                s = current.get(key)
                if s is None:
                    self._insert_step(conn, workflow_id, key, position, step, wf.S_PENDING, now)
                    self._event(conn, workflow_id=workflow_id, step_id=wf.step_id_for(workflow_id, key), entity="step",
                                to_status=wf.S_PENDING, reason_code=wf.PLAN_UPDATED, created_at=now)
                    continue
                for dep in step.depends_on:
                    conn.execute("INSERT INTO dependencies (step_id, depends_on) VALUES (?, ?)",
                                 (s["step_id"], wf.step_id_for(workflow_id, dep)))
                if s["status"] in started:
                    conn.execute("UPDATE steps SET position=? WHERE step_id=?", (position, s["step_id"]))
                    continue
                material = (s["request"] != step.request or s["capability"] != step.capability
                            or bool(s["requires_approval"]) != step.requires_approval)
                conn.execute(
                    "UPDATE steps SET position=?, capability=?, effect_class=?, request=?, reason=?, options=?, input_refs=?,"
                    " naver_keywords=?, max_attempts=?, requires_approval=?, row_version=row_version+1, updated_at=?"
                    + (", approval_id=NULL, approval_plan_version=NULL" if material else "")
                    + " WHERE step_id=?",
                    (position, step.capability, step.effect_class, step.request, step.reason,
                     json.dumps(step.options, sort_keys=True), json.dumps(list(step.input_refs)), step.naver_keywords,
                     max(step.max_attempts, int(s["attempts_opened"])), 1 if step.requires_approval else 0, now, s["step_id"]))
                fresh = conn.execute("SELECT * FROM steps WHERE step_id=?", (s["step_id"],)).fetchone()
                # a waiting or ready step follows its new gate; a budget-blocked step is released
                # when the new budget covers it
                if fresh["status"] in (wf.S_READY, wf.S_WAITING_APPROVAL) and material:
                    target = wf.S_WAITING_APPROVAL if step.requires_approval else wf.S_READY
                    if target != fresh["status"]:
                        self._set_step(conn, fresh, target, now)
                elif fresh["status"] == wf.S_BLOCKED and fresh["last_reason_code"] == wf.BUDGET_EXHAUSTED:
                    self._set_step(conn, fresh, wf.S_PENDING, now, reason_code=None)
            self._release_dependents(conn, workflow_id, now)
            self._recompute_workflow(conn, workflow_id, now)
        return self.status_view(workflow_id, now=now)

    @staticmethod
    def _dep_keys_locked(conn: sqlite3.Connection, step_id: str) -> list[str]:
        return [r["step_key"] for r in conn.execute(
            "SELECT s.step_key FROM dependencies d JOIN steps s ON s.step_id = d.depends_on WHERE d.step_id=?",
            (step_id,)).fetchall()]

    def confirm_cancelled(self, attempt_id: str, *, now: str) -> dict[str, Any]:
        """The worker stopped at a step boundary: the attempt and its step are CANCELLED now."""
        with self._write() as conn:
            a = conn.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if a is None:
                raise WorkflowBlocked("ATTEMPT_NOT_FOUND", f"no attempt {attempt_id}")
            s = conn.execute("SELECT * FROM steps WHERE step_id=?", (a["step_id"],)).fetchone()
            if a["status"] != wf.A_RUNNING or s["current_attempt_id"] != attempt_id:
                raise WorkflowBlocked("ATTEMPT_FENCED", f"attempt {attempt_id} is not the step's running attempt")
            wf.assert_transition("attempt", a["status"], wf.A_CANCELLED, attempt_id)
            conn.execute("UPDATE attempts SET status=?, closed_at=?, reason_code=? WHERE attempt_id=?",
                         (wf.A_CANCELLED, now, "CANCELLED", attempt_id))
            self._event(conn, workflow_id=a["workflow_id"], step_id=a["step_id"], attempt_id=attempt_id,
                        entity="attempt", from_status=wf.A_RUNNING, to_status=wf.A_CANCELLED,
                        reason_code="CANCELLED", created_at=now)
            self._set_step(conn, s, wf.S_CANCELLED, now, reason_code="CANCELLED", attempt_id=attempt_id)
            self._recompute_workflow(conn, a["workflow_id"], now, reason_code="CANCELLED",
                                     cancelling=self._cancel_requested_locked(conn, a["workflow_id"]))
        return self.status_view(a["workflow_id"], now=now)

    # --- snapshot ---------------------------------------------------------------------------

    def snapshot(self, dest_dir: Path | None = None, *, now: str) -> Path:
        """A consistent copy via the SQLite backup API — never a file copy of a live WAL database
        (V0.2 Q28). Writes ``workflow-<stamp>.db`` and a manifest beside it; returns the copy."""
        dest_dir = Path(dest_dir) if dest_dir is not None else self._path.parent / SNAPSHOT_DIR_NAME
        stamp = now.replace("-", "").replace(":", "").replace("T", "-").rstrip("Z")
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / f"workflow-{stamp}.db"
        try:
            src = self._connect()
            try:
                dst = sqlite3.connect(str(target))
                try:
                    src.backup(dst)
                finally:
                    dst.close()
                max_cursor = src.execute("SELECT COALESCE(MAX(cursor), 0) FROM events").fetchone()[0]
            finally:
                src.close()
        except sqlite3.Error as exc:
            raise PersistenceError("WORKFLOW_SNAPSHOT_FAILED", f"the workflow store could not be snapshotted: {exc}") from exc
        manifest = {"schema_version": SCHEMA_VERSION, "created_at": now, "max_event_cursor": int(max_cursor),
                    "source": self._path.as_posix(), "snapshot": target.name}
        (dest_dir / f"workflow-{stamp}.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        return target

    # --- internals --------------------------------------------------------------------------

    def _set_step(self, conn: sqlite3.Connection, step: sqlite3.Row, target: str, now: str, *,
                  reason_code: str | None = None, result_ref: str | None = None,
                  attempt_id: str | None = None) -> None:
        wf.assert_transition("step", step["status"], target, step["step_id"])
        conn.execute(
            "UPDATE steps SET status=?, result_ref=COALESCE(?, result_ref), last_reason_code=?,"
            " row_version=row_version+1, updated_at=? WHERE step_id=?",
            (target, result_ref, reason_code, now, step["step_id"]))
        self._event(conn, workflow_id=step["workflow_id"], step_id=step["step_id"], attempt_id=attempt_id,
                    entity="step", from_status=step["status"], to_status=target, reason_code=reason_code,
                    created_at=now)

    def _set_workflow(self, conn: sqlite3.Connection, workflow_id: str, current: str, target: str, now: str,
                      *, reason_code: str | None = None) -> None:
        wf.assert_transition("workflow", current, target, workflow_id)
        conn.execute(
            "UPDATE workflows SET status=?, last_reason_code=?, row_version=row_version+1, updated_at=? WHERE workflow_id=?",
            (target, reason_code, now, workflow_id))
        self._event(conn, workflow_id=workflow_id, entity="workflow", from_status=current, to_status=target,
                    reason_code=reason_code, created_at=now)

    def _release_dependents(self, conn: sqlite3.Connection, workflow_id: str, now: str) -> None:
        """Every PENDING step whose dependencies have all SUCCEEDED becomes READY."""
        steps = conn.execute("SELECT * FROM steps WHERE workflow_id=? ORDER BY position", (workflow_id,)).fetchall()
        status_by_id = {s["step_id"]: s["status"] for s in steps}
        deps = {s["step_id"]: [d["depends_on"] for d in conn.execute(
            "SELECT depends_on FROM dependencies WHERE step_id=?", (s["step_id"],)).fetchall()] for s in steps}
        for s in steps:
            if s["status"] == wf.S_PENDING and all(status_by_id.get(d) == wf.S_SUCCEEDED for d in deps[s["step_id"]]):
                self._set_step(conn, s, wf.S_WAITING_APPROVAL if s["requires_approval"] else wf.S_READY, now)

    def _block_dependents(self, conn: sqlite3.Connection, step_id: str, workflow_id: str, now: str) -> None:
        """A step that will never succeed blocks everything downstream of it, transitively, with
        the reason named — a dependent must not sit PENDING forever for a result that is not coming."""
        frontier = [step_id]
        seen: set[str] = set()
        while frontier:
            current = frontier.pop()
            for d in conn.execute("SELECT step_id FROM dependencies WHERE depends_on=?", (current,)).fetchall():
                dep_id = d["step_id"]
                if dep_id in seen:
                    continue
                seen.add(dep_id)
                s = conn.execute("SELECT * FROM steps WHERE step_id=?", (dep_id,)).fetchone()
                if s["status"] in (wf.S_PENDING, wf.S_READY, wf.S_RETRY_WAIT, wf.S_WAITING_APPROVAL):
                    self._set_step(conn, s, wf.S_BLOCKED, now, reason_code=wf.DEPENDENCY_FAILED)
                frontier.append(dep_id)

    @staticmethod
    def _cancel_requested_locked(conn: sqlite3.Connection, workflow_id: str) -> bool:
        row = conn.execute("SELECT status FROM workflows WHERE workflow_id=?", (workflow_id,)).fetchone()
        return row is not None and row["status"] == wf.W_CANCELLING

    def _recompute_workflow(self, conn: sqlite3.Connection, workflow_id: str, now: str, *,
                            reason_code: str | None = None, cancelling: bool | None = None) -> None:
        w = conn.execute("SELECT status FROM workflows WHERE workflow_id=?", (workflow_id,)).fetchone()
        current = w["status"]
        if current in wf.WORKFLOW_TERMINAL:
            return
        is_cancelling = cancelling if cancelling is not None else current == wf.W_CANCELLING
        rows = [dict(r) for r in conn.execute(
            "SELECT status, attempts_opened, last_reason_code FROM steps WHERE workflow_id=?", (workflow_id,)).fetchall()]
        statuses = [r["status"] for r in rows]
        target = wf.workflow_status_for(rows, cancelling=is_cancelling)
        if target == current:
            return
        if current == wf.W_VALIDATED and target == wf.W_RUNNING and not any(s in (wf.S_RUNNING, wf.S_CANCEL_REQUESTED) for s in statuses):
            return   # nothing has been claimed yet; VALIDATED stays until the first attempt opens
        self._set_workflow(conn, workflow_id, current, target, now,
                           reason_code=reason_code if target in wf.WORKFLOW_TERMINAL
                           or target in (wf.W_CANCELLING, wf.W_WAITING_REPLAN) else None)
