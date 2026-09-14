# Workflow v0.1 (sequence 2, P03 — plan, lifecycles, store)

**Status:** Active runtime capability (model and store; the manager loop, the worker frame and
recovery follow in P04–P06)
**Owner:** Thomas
**Authority:** None. This document describes an implementation; the canonical Governance Policy
(`governance/GOVERNANCE_POLICY.yaml`) owns the rules it obeys, and
[`HERMES_ORCHESTRATOR_ARCHITECTURE_V0.2.md`](../HERMES_ORCHESTRATOR_ARCHITECTURE_V0.2.md) §3–§4
records the decisions it implements (Q18–Q22, Q28).

Hermes proposes a plan; the runtime validates it, stores it, executes it step by step and keeps
the state through restarts. This contract is the plan a caller may submit, the lifecycles a
workflow, a step and an attempt follow, and the store that holds them.

## What it changes in governance: one recorded reversal

The store is the first SQLite in the runtime and a second coordination ledger beside
`task_registry`. That is a deliberate reversal of "reuse first" (`CLAUDE.md`) and of the
2026-09-13 principle that no parallel task ledger is created, recorded as V0.2 Q19/Q20 with the
concept-by-concept authority table (V0.2 §3.2). The reason is one sentence: acceptance,
idempotency, the first steps and the budget reservation must land in **one transaction** or not
at all, and JSONL under a file lock cannot give that (`bridge_idempotency` names its own
claim→effect crash window).

Nothing else moves. The run's evidence stays in the durable ledger's audit chain; the registry
keeps one attempt-level row per attempt (origin `WORKFLOW`, `task_registry_entry.v0.3`);
approvals stay in the approval store; permission and role selection stay with Prime at
execution. No new gate, permission scope or safety flag.

## The plan — `schemas/workflow_plan.v0.1.schema.json` (closed)

| field | meaning |
|---|---|
| `goal` | what the workflow is for; rendered, never executed |
| `steps[]` | 1–10 steps: `id` (`^[a-z][a-z0-9_-]{0,31}$`), `capability` (one of `analysis`, `research`, `translation`, `content` — exactly the dispatch door's kinds, pinned by a test), `request`, `reason`, `depends_on`, `input_refs` (⊆ `depends_on`), optional `naver_keywords`, `max_attempts` (1–3), `options` (`independent_validation`, `revise`, `important`) |
| `budget` | `max_model_calls` (1–100), optional `max_tokens` |

What a plan **cannot** say: an effect class, an actor, a permission, a Role. `effect_class`
belongs to the capability (`workflow.CAPABILITIES`; every first-sequence capability is
`none`), the principal is the door's socket peer, the Role is Prime's choice. An unknown key
is refused by the closed schema; a cycle, an unknown dependency, an input reference that is
not a dependency, a duplicate id and a budget below one attempt of every step are refused by
`workflow.validate_plan` — all before a row exists (acceptance A01).

## Lifecycles (forward-only; every other edge is `TRANSITION_INVALID`)

- **Workflow:** `RECEIVED → VALIDATED → RUNNING → COMPLETED`; in flight `WAITING_APPROVAL`,
  `WAITING_REPLAN`, `CANCELLING`; terminal `COMPLETED | FAILED | BLOCKED | CANCELLED`. A stored
  workflow is at least `VALIDATED` — validation precedes the accepting transaction.
- **Step:** `PENDING → READY → RUNNING → SUCCEEDED`; on failure `RETRY_WAIT` (attempts remain)
  or `FAILED`; `WAITING_APPROVAL`; `NEEDS_RECONCILIATION` (effect class `external` only);
  `BLOCKED` (budget, or a dependency that will never succeed — `DEPENDENCY_FAILED`, transitive);
  cancel is `CANCEL_REQUESTED → CANCELLED` for a running step and `CANCELLED` at once for a
  waiting one. **Two kinds of done (P06):** `SUCCEEDED` and `CANCELLED` are terminal; `FAILED` and
  `BLOCKED` are *settled* — the automatic policy is exhausted and only a decision re-opens them
  (`FAILED → READY`, `BLOCKED → PENDING|READY` through `retry_step`; a cancel ends either).
- **Attempt:** `RUNNING → SUCCEEDED | FAILED | EXPIRED | CANCELLED`. A retry is a **new** attempt;
  a completed attempt is never re-opened. `MAX_ATTEMPTS_PER_STEP` (3) is the hard cap across
  automatic retries and decisions.

The workflow's status is recomputed from its steps after every change
(`workflow.workflow_status_for`): active work keeps it `RUNNING` (`CANCELLING` while a cancel is
being honoured); a step waiting for an approval makes it `WAITING_APPROVAL`; all `SUCCEEDED` is
`COMPLETED`; a cancel with nothing active ends it `CANCELLED`; a settled or reconciling step a
decision could still move — a `FAILED` step below the cap, a budget-blocked step, a
`NEEDS_RECONCILIATION` step — makes it **`WAITING_REPLAN`**, the state `retry_step` and a plan
change (P07) act on; a `FAILED` step at the cap is `FAILED`; otherwise `BLOCKED`.

## Fence, lease, effect class

- **Fence.** The step's `current_attempt_id`. A result for any other attempt — a lapsed one, a
  superseded one — is written as a `LATE_RESULT` event and refused (`ATTEMPT_FENCED`), never
  applied (A07).
- **Lease.** `attempts.deadline_at` (opened + 660 s by default; the worker socket's own deadline
  is 600 s). `expire_overdue` lapses a RUNNING attempt past it: an `effect_class=none` step
  may open a new attempt while attempts remain, else it FAILS; an `external` step goes to
  `NEEDS_RECONCILIATION` and is never re-dispatched on its own (A08). There is no heartbeat
  channel: the connection's lifetime is the liveness signal (V0.2 Q22).
- **Budget.** Each attempt reserves `1 + independent_validation + revise` model calls against
  the workflow's `max_model_calls`; a step whose reservation would cross the cap is `BLOCKED`
  (`BUDGET_EXHAUSTED`) and so is the workflow. A reservation whose spend was never confirmed
  stays counted — a lapsed attempt may have called the model, and an unconfirmed spend is never
  refunded (A12). `max_tokens`, when set, refuses new attempts once confirmed usage reaches it.

## The store — `runtime/mvp_runtime/workflow_store.py`

`.runtime_governance_state/workflow/workflow.db`, SQLite in WAL mode, schema version 1.

| table | writer | holds |
|---|---|---|
| `workflows` | manager | id, principal, goal, status, plan version, row version, budget caps, cancel reason |
| `plan_versions` | manager | the plan as submitted, its hash, a reason per version |
| `steps` | manager | key, position, capability, effect class, request, options, input refs, status, attempts opened, current attempt, result ref, row version |
| `dependencies` | manager | `step_id → depends_on` |
| `attempts` | manager | number, status, opened/deadline/closed, trace id, registry entry id, result ref, reason |
| `requests` | manager | `(principal, request_id) → fingerprint, workflow_id` — the idempotency key |
| `events` | manager | the append-only cursor of every transition, validated against `workflow_event.v0.1` before insert; coordination, not audit |
| `budget_reservations` | manager | per-attempt reservation, `cost_status` (`unconfirmed` / `observed` / `estimated`), confirmed usage |
| `deliveries` | operator (P08) | per-channel delivery state of pushed events |

**One writer per table**, every write one short `BEGIN IMMEDIATE` transaction, no transaction
open across a model call. **Read rule (Q19):** a `mode=ro` handle only from the same uid on the
same RW mount; a read-only mount or another uid cannot create the `-shm` file. **Snapshot
(Q28):** `WorkflowStore.snapshot` uses the SQLite backup API and writes a manifest beside the
copy; a file copy of the live WAL database is not a backup.

### Submit — `submit(principal, request_id, plan, now)`

Validation first (no row on refusal). Then one transaction: the request mapping, the workflow
(`VALIDATED`), plan version 1, every step (`READY` when it has no dependency, `PENDING`
otherwise), the dependencies, the opening events. The same `(principal, request_id)` with the
same plan hash replays the accepted workflow (`replayed: true`); with a different plan it is
`REQUEST_ID_CONFLICT`. A submission that would push the server past `MAX_OPEN_STEPS` (20) is
`CAPACITY_EXHAUSTED`. Nothing answers `accepted` before the commit (A03).

### Retry by decision — `retry_step(workflow_id, step_key, expected_version, reason, now)` (P06)

On a `WAITING_REPLAN` (or `RUNNING`) workflow at the version the caller read: a `FAILED` step
below the cap, a budget-blocked step the budget now covers, or a `NEEDS_RECONCILIATION` step a
person has looked at goes `READY` (`PENDING` while a dependency is not yet `SUCCEEDED`) with one
more attempt allowed; steps blocked by it (`DEPENDENCY_FAILED`) come back to `PENDING`; nothing
already delivered is re-run (acceptance A16). Refused: a step blocked by a failed dependency
(`RETRY_NOT_APPLICABLE` — retry the dependency), a step at the cap (`ATTEMPTS_EXHAUSTED`), a
budget that does not cover one more attempt (`BUDGET_EXHAUSTED`), an ended workflow.

### Recovery — the manager and the registry row (P06; A10, A28)

Every attempt the worker runs opens a registry row of origin `WORKFLOW` carrying the
`attempt_id` (`task_registry_entry.v0.4`). When the reply is lost between the worker and the
manager — a connection drop, a bridge restart — `WorkflowManager.reconcile` reads that row: a
`DELIVERED` row completes the attempt with its trace and `ledger:<trace>` and **no second model
call**; a `FAILED`/`BLOCKED` row fails it under the row's reason; a row still `RUNNING` past the
lease is closed `RUN_ABANDONED` by the manager (`MANAGER_ORIGINS`, its own to close) and
`expire_overdue` rules on the attempt by effect class. A row still `RUNNING` inside the lease is a
run still going and is left alone. Reconciliation runs at manager start and at the head of every
tick.

### Claim, result, expiry, cancel

`claim_ready(now, limit)` opens attempts for READY steps oldest-workflow-first and hands back
the attempt frame (attempt id, step, capability, request, options, the dependency results the
plan named). `record_result(attempt_id, …)` applies a worker's result through the fence and
releases or blocks dependents. `expire_overdue(now)` lapses leases by effect class.
`request_cancel(workflow_id, expected_version, reason, now)` needs the version the caller saw
(`VERSION_CONFLICT` otherwise) and cancels waiting steps at once; a running step is
`CANCEL_REQUESTED` until `confirm_cancelled` or its lease lapses, and the workflow is
`CANCELLING` until then — never `CANCELLED` before the cancellation is real (A11).

## Not in this increment

The manager loop that calls these (P04, inside the dispatch-bridge process), the worker's
attempt frame and the `WORKFLOW` registry rows it writes (P05), recovery after a bridge
restart and the reconciliation of a `NEEDS_RECONCILIATION` step against the ledger (P06),
plan versions beyond the first and approval-bound steps (P07), and the operator's `deliveries`
(P08).
