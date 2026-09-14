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
| `steps[]` | 1–10 steps: `id` (`^[a-z][a-z0-9_-]{0,31}$`), `capability` (one of `analysis`, `research`, `translation`, `content` — exactly the dispatch door's kinds, pinned by a test), `request`, `reason`, `depends_on`, `input_refs` (⊆ `depends_on`), optional `naver_keywords`, `max_attempts` (1–3), `options` (`independent_validation`, `revise`, `important`), `requires_approval` (P07: the step runs only after Thomas's grant for it is spent — a plan can **ask** for an approval and cannot carry one) |
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
  workflow is at least `VALIDATED` — validation precedes the accepting transaction. A gated root
  step waits from acceptance (`VALIDATED → WAITING_APPROVAL`, P07), and the two waiting states
  move between each other — a refused ask blocks the step (`→ WAITING_REPLAN`), a retry or a new
  plan version asks again (`→ WAITING_APPROVAL`). Forward-only is a claim about the terminal
  states, which have no outgoing edge.
- **Step:** `PENDING → READY → RUNNING → SUCCEEDED`; on failure `RETRY_WAIT` (attempts remain)
  or `FAILED`; `WAITING_APPROVAL`; `NEEDS_RECONCILIATION` (effect class `external` only);
  `BLOCKED` (budget, or a dependency that will never succeed — `DEPENDENCY_FAILED`, transitive);
  cancel is `CANCEL_REQUESTED → CANCELLED` for a running step and `CANCELLED` at once for a
  waiting one. **Two kinds of done (P06):** `SUCCEEDED` and `CANCELLED` are terminal; `FAILED` and
  `BLOCKED` are *settled* — the automatic policy is exhausted and only a decision re-opens them
  (`FAILED → READY`, `BLOCKED → PENDING|READY` through `retry_step`; a cancel ends either). A
  gated step (P07) goes `PENDING|FAILED|BLOCKED → WAITING_APPROVAL` where an ungated one would go
  `READY`; `WAITING_APPROVAL → READY` only when its bound grant is spent, `→ BLOCKED` under
  `APPROVAL_REJECTED | APPROVAL_EXPIRED | APPROVAL_REUSED | APPROVAL_STALE` when it cannot be.
- **Attempt:** `RUNNING → SUCCEEDED | FAILED | EXPIRED | CANCELLED`. A retry is a **new** attempt;
  a completed attempt is never re-opened. `MAX_ATTEMPTS_PER_STEP` (3) is the hard cap across
  automatic retries and decisions.

The workflow's status is recomputed from its steps after every change
(`workflow.workflow_status_for`): active work keeps it `RUNNING` (`CANCELLING` while a cancel is
being honoured) — a `PENDING` step is not active work, it waits on a dependency, so a gated root
with dependents behind it is `WAITING_APPROVAL`, not `RUNNING`; a step waiting for an approval
with nothing else runnable makes it `WAITING_APPROVAL`; all `SUCCEEDED` is
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

`.runtime_governance_state/workflow/workflow.db`, SQLite in WAL mode, schema version 3 (P07
added `requires_approval`, `approval_id`, `approval_plan_version` to `steps`; P08 added the
`delivery_cursors` and `reported_usage` tables; an older file is migrated in place when opened,
under the same write lock — columns by `ALTER TABLE`, tables by `CREATE IF NOT EXISTS`).

| table | writer | holds |
|---|---|---|
| `workflows` | manager | id, principal, goal, status, plan version, row version, budget caps, cancel reason |
| `plan_versions` | manager | the plan as submitted, its hash, a reason per version |
| `steps` | manager | key, position, capability, effect class, request, options, input refs, status, attempts opened, current attempt, result ref, row version, gate + the bound approval id and the plan version it was asked for (P07) |
| `dependencies` | manager | `step_id → depends_on` |
| `attempts` | manager | number, status, opened/deadline/closed, trace id, registry entry id, result ref, reason |
| `requests` | manager | `(principal, request_id) → fingerprint, workflow_id` — the idempotency key |
| `events` | manager | the append-only cursor of every transition, validated against `workflow_event.v0.1` before insert; coordination, not audit |
| `budget_reservations` | manager | per-attempt reservation, `cost_status` (`unconfirmed` / `observed` / `estimated`), confirmed usage |
| `deliveries` | operator (P08) | per-channel delivery state of pushed events: `PENDING` (recorded before the send) → `CONFIRMED` / `UNCERTAIN`; `SKIPPED` marks a backlog the first pass adopted |
| `delivery_cursors` | operator (P08) | where the push has read up to per channel — advanced past events it does not push |
| `reported_usage` | manager (P08, through the door) | Hermes's own usage per workflow as it reported it: tokens in/out, estimated cost, `cost_status`, source, `as_of`; latest report wins; never summed into the reservations |

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

### Gated steps — `requires_approval` (P07; A13)

A gated step becomes `WAITING_APPROVAL` where an ungated one would become `READY` (at
acceptance for a root step, when its dependencies deliver otherwise, on `retry_step`, and on a
new plan version that changes it). The manager (`WorkflowManager.approvals`, each tick before
it claims) binds it to the **existing** ask machinery — nothing new is minted, scoped or gated:

- **The ask.** A Core-bound task, `permission.build_workflow_step_permission_decision`
  (APPROVAL_REQUIRED, scope `RUNTIME_GOVERNANCE` like the switch asks, risk ORANGE, target
  `workflow_step:<workflow_id>:<step_key>`), `approval.build_approval_request`. What Thomas
  signs is `normalized_parameters = {workflow_id, step_key, plan_version, capability,
  request_sha256}` — the step at this plan version with this exact request. The store records
  the bound id and version on the step (`bind_approval`); the operator announces the ask on the
  control channel like a switch ask and never mirrors it (policy 1.5.0 mirrors switch-door asks
  only). The same action fingerprints to the same approval id, as every ask does: a re-ask after
  a refusal is a new PENDING record under it, and the trail keeps the decision.
- **The spend.** An APPROVED record is spent through the shared single-use ladder
  (`validate_spendable_approval`, `spend_lock`, `build_consumed_record`, consumption ref
  `workflow:<id>:<step>:v<n>`), and only after the snapshot is compared with the step *as it is
  now* — target, plan version, request hash. Then `approve_step` makes the step `READY` and the
  next tick claims it. **The manager never writes APPROVED**; only Thomas's verified decision
  does, and the only path that runs a gated step is the consumption of its bound grant.
- **Refusals.** Rejected → `APPROVAL_REJECTED`; expired (ask or grant) → `APPROVAL_EXPIRED`;
  already consumed → `APPROVAL_REUSED`; missing, re-pointed, another version or a changed
  request → `APPROVAL_STALE`. Each blocks the step (`refuse_step_approval`), blocks its
  dependents, and leaves the workflow `WAITING_REPLAN` — a retry, or a new plan version that
  changes the step, asks again (an unchanged step keeps the refusal through unrelated updates); a
  plan can carry no approval field at all (`PLAN_INVALID` at the closed schema). A halted runtime
  (kill/pause) spends nothing and the grant waits (`approvals_deferred`).
- **The binding is rechecked where it is written** (review, 2026-09-14). The manager re-reads the
  step just before spending and spends nothing if it changed; `approve_step` then checks, in its own
  transaction, that the step is still bound to that grant at that plan version (`APPROVAL_NOT_BOUND`
  otherwise — the grant is spent and the step waits to be asked again, the safe direction); a refusal
  decided on an ask the step is no longer bound to changes nothing. A failure minting one step's ask
  — typed or not — is logged and backed off and never stops the tick's claims for other workflows.
- **The dispatch bridge mounts the Core read-only** (`docker-compose.yml`): minting binds a Task to
  the active Core, and without the mounts every ask in the container failed `BINDING_FAILED`.

### Plan versions — `propose_update(workflow_id, expected_version, plan, reason, now)` (P07)

A whole new plan (`workflow_plan.v0.1`, validated like a submission) for a workflow that has not
ended, at the version the caller read (`VERSION_CONFLICT` otherwise). **May change:** steps not
yet started (request, options, dependencies, inputs, gate, keywords, attempt cap), the goal, the
budget (never below what is already reserved), and the step set — a new step is inserted
`PENDING`/`READY`/`WAITING_APPROVAL` by its dependencies and gate, a dropped unstarted step is
`CANCELLED` under `PLAN_UPDATED`. **May not change (`PLAN_CONFLICT`):** the capability, request,
dependencies, inputs or keywords of a step that is running or delivered, nor drop such a step;
a cancelled step's key cannot be reused; **a gate cannot be removed** — `requires_approval` goes
false→true, never back, so a rejected or unanswered step cannot be run by a version that drops the
flag. Refused as well: a workflow being cancelled (`WORKFLOW_CANCELLING`, like `retry_step`), a
version that adds more waiting steps than the server's `MAX_OPEN_STEPS` leaves room for
(`CAPACITY_EXHAUSTED`, like `submit`), and — at the door — any version while the runtime is halted.
Dependencies are rebuilt from the new plan and every unstarted step is put where they now say, in
plan order: a READY or asked step given a dependency not yet delivered goes back to `PENDING` (and
loses its binding); a step waiting on a dead dependency (`FAILED`, `BLOCKED`, `CANCELLED`,
`NEEDS_RECONCILIATION`) is `BLOCKED (DEPENDENCY_FAILED)`; a step so blocked whose dead dependency was
dropped or released waits again. For a gated step, **any change to what it would run** — request,
capability, options, keywords, inputs, dependencies — clears its binding and it is asked again,
whether it was still waiting or already released by a spent grant. A budget-blocked step goes back
to `PENDING` (the claim re-checks the budget) and its dependents with it. `plan_versions` gains a row
with the reason; nothing delivered is re-run. Door command `workflow.propose_update`, shim tool
`propose_workflow_update`.

### Inputs — what a step reads (P07; A15)

`input_refs` (⊆ `depends_on`) names the dependency steps whose results feed a step. At claim
the store resolves them to result references (`{step_key: "ledger:<trace>"}`), the manager sends
them on the attempt frame as `workflow_inputs`, the worker validates the shape (a closed key set:
1–10 step keys, `ledger:` references) and records them on the run's `source_ref` after the
reason, and `workflow.status` shows each step's `input_refs` and their live resolution `inputs`
— so the link from a step to what it was given is one fact, readable at every end. What a run
*does* with a prior result is the pipeline's evidence model and is not in this increment: the
reference travels and is recorded, it is not yet rendered into the specialist's prompt.

### Deliveries — the operator's push (P08; V0.2 Q23, A19)

`operator.push_workflow_events` runs once per operator pass, after the approval announcer,
with the same posture: best-effort, one stderr line on failure, never a reason the loop stops
reading `/approve`. It is the **one** pusher and the only writer of `deliveries` and
`delivery_cursors`; the store is opened only if the manager has created it
(`WorkflowStore.exists`), so an operator on a host without the manager creates nothing.

- **What is pushed:** a workflow's arrival at `COMPLETED`, `FAILED`, `BLOCKED`, `CANCELLED`
  or `WAITING_REPLAN` (`wf.PUSHED_WORKFLOW_STATUSES`) — one message to the registered control
  chat (`workflow_console.render_push`: the goal, the reason, the settled steps, and for
  `WAITING_REPLAN` where the decision is made — the Hermes window, never a control-bot
  command). Step and attempt events are Hermes's to narrate; `CANCELLING` is not a cancel; and
  `WAITING_APPROVAL` is not pushed here because the ask itself is that push
  (`announce_pending_approvals`) — one event, one pusher.
- **Delivery record:** `PENDING` is written before the send and `CONFIRMED` or `UNCERTAIN`
  after it. A crash between the two leaves `PENDING`, which the next pass retries **once** — the
  row turns `UNCERTAIN` before the retry leaves, so a process that dies during the retry does not
  retry again. `UNCERTAIN` is kept and never re-sent — the channel may have delivered it. An event
  that already has a delivery row is never pushed again, so a pass killed after a send but before
  it moved the cursor re-sends nothing. This is at-least-once for one retry; nothing promises
  exactly-once to the outside (V0.2 §4.3).
- **Arrivals only:** a same-status workflow event (a new plan version on a workflow that is already
  waiting) is not pushed. Assistant-authored text in the message (the goal, a cancel reason) is one
  line, capped, with no token that begins like a bot command.
- **Cursor:** the first pass adopts the current tail and sends nothing (a deploy must not
  open with every ending since the store was created; the adopted tail is a `SKIPPED` row).
  Each pass reads a page of events past the cursor, pushes at most `MAX_PUSHES_PER_PASS`, and
  advances the cursor past every event it read — including the ones it does not push — but
  never past an event the cap left unsent. A restarted operator resumes from the stored
  cursor; an unchanged store is silence.

### Reported usage — `record_reported_usage(workflow_id, principal, usage, now)` (P08; V0.2 Q24, A12)

The second budget layer. The **enforced** layer is the reservations above: Thomas-side calls,
in model calls and tokens, reserved before the frame is sent and never refunded on an
unconfirmed outcome. The **reported** layer is Hermes's own model usage — its `state.db`
`session_model_usage`, uid 10000's alone — which this side cannot read and cannot enforce.
Decision (Q24, taken here): Hermes reads its own snapshot and sends it through the door —
the shim tool `report_workflow_usage(workflow_id, session_id | since)` sums the rows for one
session (exact) or every session since a timestamp (an upper bound, named as such by its
`source`) and sends `workflow.report_usage` with `{input_tokens, output_tokens,
estimated_cost_usd, cost_status, source, as_of}`. `cost_status` is `observed`, `estimated` or
`unmeasured`, from the rows. The store keeps one row per workflow (latest wins), the view shows
it as `budget.reported` and the console as a separate line marked *강제 아님*; the enforced
counters never include it, and no decision reads it. A malformed report is refused unrecorded
(`REPORTED_USAGE_INVALID`).

### Narration — Hermes polls (P08; V0.2 Q23)

Hermes narrates by polling `workflow.events`; the shim tool `workflow_changes` keeps its cursor
in a file beside the shim (`THOMAS_WORKFLOW_CURSOR_FILE`), adopts the tail on its first call,
answers *변화 없음* when nothing moved, and advances only after a page was rendered. The cron
template's fourth job (워크플로 서술, every 30 minutes) calls it once and stays silent
(`[SILENT]`) on no change. Narration guarantees nothing about delivery and says so; the ending
or the waited-for decision reached Thomas through the operator's push above.

### Scheduled workflows — the `workflow_plan` kind (P09; A20)

A schedule of kind `workflow_plan` (maintenance lane) carries a `workflow_plan.v0.1` object as
its request, validated at registration by `workflow.validate_plan`. Its fire does one thing:
`WorkflowStore.submit(principal="scheduler", request_id="<schedule_id>:<schedule_run_id>", plan)` —
**unless this schedule's previous occurrence has not ended**, in which case the occurrence is dropped
with that reason (`workflow:skipped:previous occurrence wf_… still <status>`): one open workflow per
schedule, so a manager that is off or slower than the cadence never comes back to a burst of stale
occurrences and one schedule never fills the server's open-step ceiling. The occurrence's own
`schedule_run_id` is in the request id, so the store's request table makes it
at-most-once per occurrence: a duplicate tick claims nothing (`claim_due`), a re-fire of the
same occurrence replays the accepted workflow, a clock jump fires once on the grid and a restart
never catches up (`next_occurrence`). The manager runs the workflow like any other; without a
workflow store on the runtime the fire fails by name (`WORKFLOW_UNAVAILABLE`). This is a second
process submitting to `workflows`/`requests` — serialised by SQLite's writer lock like the door's
own submit; the manager remains the only writer of step and attempt transitions.

Who may register one is the scheduler's question, not this store's: `schedule_delegation`
(P09) applies a change inside the policy's delegated scope, records a proposal outside it, and
refuses every financial kind — dormant until policy 1.6.0 names the scope
(`docs/runtime-contracts/POLICY_1_6_0_DRAFT.md`).

### Backup, cutover, rollback (P10; A22, A24, A25)

The live file is never tarred: `harness_backup.sh` has the bridge write a backup-API copy into
`workflow/snapshots/<stamp>/` and archives that; `workflow_cli verify --snapshot` reads a copy on
its own and `workflow_cli drain` says what is in flight on both paths. The entry-point cutover is
the door's `--v2-intake closed` (new single dispatches refused as `V2_INTAKE_CLOSED`; v3, reads
and pre-close replays untouched), then drain, then the client switch; rollback keeps the store and
a compatible manager finishes it. Procedure and rehearsals: `docs/RUNBOOK_WORKFLOW_CUTOVER.md`.

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
plan versions and approval-bound steps (P07), the operator's push with `deliveries`, the
reported budget layer and the polling narration (P08) are in. Still ahead: a prior step's
result rendered into the next step's prompt as evidence (a pipeline decision; today the
reference is carried and recorded, not read), a retention policy for old request ids (A06),
and the audit-write-failure injection (A09).
