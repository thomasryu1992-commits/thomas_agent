# Task Registry v0.1 (F1 — coordination)

**Status:** Active runtime capability
**Owner:** Thomas
**Authority:** None. This document describes an implementation; the canonical Governance
Policy (`governance/GOVERNANCE_POLICY.yaml`) owns the rules it obeys.
**Design context:** `docs/proposals/CONVERSATIONAL_ORCHESTRATION_FRONT_V0.1.md` Part ②
(decision D1). The other two parts of that proposal — the conversational front desk and
standing grants — are **not** implemented and remain separate Thomas decisions.

The runtime could always *run* a request and *audit* it. What it could not do was answer
**"what am I working on, what did I already do, and how did it go"** without reading a raw
JSONL ledger. `trace_id` existed; a list of tasks with a status did not. This is that list,
and deliberately nothing more.

## What it changes in governance: nothing

Zero new contracts, schemas of a new *concept*, registries, gates, permission scopes, or
safety flags. Recording what was asked and how it ended is `INTERNAL_READ`-tier bookkeeping
over the runtime's own local state. The one new schema (`task_registry_entry.v0.1`) is the
record format for that bookkeeping, not a new authority: the task itself stays the
authority of `task.v0.3` and the run's evidence stays in the durable ledger.

That is also why it could ship first. The proposal stages F1 ahead of F2/F3 precisely
because this part needs no gate widening to be useful.

## The entry

`schemas/task_registry_entry.v0.1.schema.json` (closed, `additionalProperties: false`).
Stored at `.runtime_governance_state/task_registry.jsonl` — per-machine, gitignored, exactly
like `schedules.jsonl`, whose `ScheduleStore` this deliberately mirrors: append-only JSONL,
latest row per id wins, and every read-modify-write under one cross-process sidecar lock
(the operator loop and a `docker exec` CLI share the file in the shipped deployment).

Coordination state only — `request_text` (verbatim), `origin`, `requester_id`, `flags`,
`status`, the three timestamps, `task_id`/`trace_id`, `result_ref`, `last_reason_code`. It
restates none of the run's content. **One concept, one authority.**

### Forward-only lifecycle

```
QUEUED ──► RUNNING ──► DELIVERED | FAILED | BLOCKED     (terminal)
   └─────► CANCELLED                                    (terminal)
```

Anything else is `TRANSITION_INVALID`. Two edges are refused on purpose:

- **`QUEUED → DELIVERED`** — a delivered result that never ran is a bookkeeping lie.
- **`RUNNING → CANCELLED`** — stopping in-flight execution is the kill switch's authority.
  Duplicating it here would give the operator two different answers to "how do I stop this".

A terminal status is final; a re-run is a new submission with its own entry.

### `DELIVERED` means it reached Thomas

Not "was produced". A run that COMPLETEs leaves its entry **open** and the sender closes it:
`DELIVERED` after a successful send, `FAILED`/`SEND_FAILED` if delivery is what broke.
Telegram send failures are an observed failure mode here (QA wave 6 hardened the batch loop
around them), and `/result` exists precisely so an undelivered analysis can be fetched later
— which only works if the registry does not already claim it was handed over.

### Stranded `RUNNING`, stated rather than hidden

A process killed mid-run leaves its entry at RUNNING with no terminal — the same blind spot
`scheduler.py` names for a fire that never wrote its outcome. The next startup is the only
vantage point that can see it, so `reconcile_stale_running` supplies the missing terminal
there (`FAILED`/`RUN_ABANDONED`), wired into the operator loop's startup. Deliberately
**not** a timeout: a long model call is not a dead process, and guessing from elapsed time
would abandon healthy runs.

## The verbs

Behind R4's identity gate, sharing the console tokenizer (`control.command_verb`) with the
`control`/`approval`/`feedback`/`memory` verb families so the channels cannot drift.

| Verb | Effect | Runtime mode |
|---|---|---|
| `/tasks` | open entries, RUNNING first, with elapsed time | any |
| `/history [n]` | last n finished entries (default 10, cap 50) | any |
| `/result <id>` | re-send a delivered entry's deliverable | any |
| `/cancel <id>` | cancel a QUEUED entry | **ACTIVE only** |

Ids accept any unambiguous prefix; an ambiguous one is refused rather than resolved to an
arbitrary match.

**The read verbs write no ledger event.** That is the `/status` precedent and a correctness
point, not a saving: a read that appends to the log it reads races its own tail. `/cancel`
writes one — it changed something.

**`/cancel` is kill-switch bound**, checked first and refused mode-aware
(`RUNTIME_PAUSED`/`RUNTIME_KILLED`) — the same door R8 `tool_write`, R6
`scheduler_execution` and R10 consume go through. The read verbs answer in any mode:
`kill_allows` covers read-only status, and an operator facing a PAUSED runtime most needs
to see what it was doing.

### `/result` stores nothing of its own

It re-renders from the run's records in the durable ledger — agent output, the search hits
its citations point at, and whether an independent reviewer passed it — through the
pipeline's own `render_response`. Keeping the text in the registry would make a second copy
of a delivered analysis that could drift from the audited one.

When the ledger cannot rebuild it (pruned, another machine, a malformed row), the answer is
`RESULT_UNAVAILABLE` — an honest refusal, never a half-rendered analysis.

## Honesty rules this implementation follows

- **No fake progress.** The pipeline is a sequence of stages, not a measurable fraction, so
  `/tasks` reports **elapsed time**, which the registry actually knows. No percentage.
- **Recording is best-effort; reporting is not.** A registry failure on the run path
  degrades to "unrecorded" and never costs Thomas the analysis (the working-memory and
  programization seam precedent). But asked what is running, an unreadable registry
  **refuses** — an empty list would read as "nothing is running", the one answer that must
  not be given when the truth is unknown.
- **Malformed rows are typed refusals**, not tracebacks — the `SCHEDULE_RECORD_INVALID`
  lesson, and optional fields never become the string `"None"` (the coercion that once made
  a schedule silently never fire).

## What is recorded

| Origin | Recorded | Note |
|---|---|---|
| `TELEGRAM` | yes | every request the operator channel runs |
| `SCHEDULER` | yes | `analysis_task` fires only |
| `CLI` | not yet | the one-shot intake CLI is unwired |
| `FRONTDESK` | reserved | for the proposal's Part ①, which does not exist |

The scheduler's maintenance and crypto kinds are not task-shaped and keep their own
scheduler events. Stated here rather than left to be discovered: `/history` is complete for
what the deployed service runs unattended, not for every code path that can call the
pipeline.

## Deliberately excluded

- **Parallel execution.** Execution stays single-process sequential. The registry gives
  *visibility*, not concurrency. A worker pool would need concurrent budget/gate semantics
  and is a separate Thomas decision.
- **Deferred submission (a real queue).** `QUEUED` is modelled and `/cancel` operates on it,
  but the operator channel still runs a request inline. Enqueue-then-drain is the next
  increment; the lifecycle was built to accept it without a schema change.
- **Cancelling a running task.** The kill switch owns that.
