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
over the runtime's own local state. The one new schema (`task_registry_entry.v0.1`, bumped to
`v0.2` on 2026-07-27 — see below) is the record format for that bookkeeping, not a new
authority: the task itself stays the authority of `task.v0.3` and the run's evidence stays in
the durable ledger.

That is also why it could ship first. The proposal stages F1 ahead of F2/F3 precisely
because this part needs no gate widening to be useful.

## The entry

`schemas/task_registry_entry.v0.2.schema.json` (closed, `additionalProperties: false`).

**v0.2 (2026-07-27)** adds one field, `request_kind`: the architecture §8.5 routing kind a
request was submitted with, or `null` for the default analysis routing. A string beside the
boolean `flags` rather than inside them — flags answer yes/no about one run option, this names
which capability set (and therefore which Role) the request needs. It exists because the queue
is **durable and unattended**: a request queued as a translation must still be a translation
when the drain picks it up minutes later, and a kind that survived only in the operator's
message would silently become an analysis. Additive and backward-compatible: v0.1 rows already
on disk carry no such key and are read as `null`, which *is* the routing they were queued with.
An unroutable kind is refused at **submission**, not at drain — a bad kind accepted into the
queue would surface later as a blocked run with nobody watching.
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

## The queue (increment 2)

The registry is not only the view — it is **the queue**. A request over the control channel
is *enqueued* and the operator loop's drain runs it between polls:

```
poll → handle messages (a task request returns "접수했습니다" immediately)
     → drain at most ONE queued task → send its result
     → poll again
```

**Why one per pass.** Returning to the poll between tasks is the entire point. A drain that
emptied the backlog in one go would hold the loop exactly as long as the inline execution it
replaced, and `/tasks`, `/cancel` or `/kill` issued mid-backlog would not land until it
finished. Execution stays single-process sequential — this buys **visibility**, not
concurrency.

**The claim is atomic.** `claim_next_queued` finds and claims inside one lock acquisition
(the scheduler's `claim_due` pattern), so two drains cannot both take the same entry. The
claim precedes the execution, so an occurrence is **at-most-once**: a crash after the claim
leaves the entry RUNNING for startup reconciliation, which is the direction that never runs
a task twice.

**Strict FIFO, tie-broken on arrival.** Timestamps are the repo's canonical second-resolution
UTC form, so a burst of requests shares one `submitted_at`. The tie breaks on the entry's
position in the append-only file — the one record of arrival that exists. Breaking it on the
hashed id (the first implementation) ran a burst in an order unrelated to the order Thomas
asked, which is not a queue.

**Kill-switch is re-read before every claim**, not once per batch (the scheduler's per-fire
precedent): one task can hold the drain for minutes, and a kill issued during it must stop
the tasks behind it. Queued entries stay queued — a kill **pauses** the drain, it does not
drop the backlog. New requests are still refused at the door while not ACTIVE, so nothing
accumulates to burst on resume.

**`QUEUE_DEPTH_LIMIT` (20)** bounds the backlog. The runtime executes one task at a time, so
a deeper queue does not get through faster — it only converts "the operator typed a lot"
into a long unattended burst. `QUEUE_FULL` says so while he can still choose what matters.

**Enqueue is not best-effort.** Everywhere else the registry is bookkeeping and a failure
degrades to "unrecorded". Here it *is* the execution path, so a failed enqueue **refuses**:
silently dropping a request Thomas believes is running is the one outcome worse than telling
him no.

**Pending work suppresses the long poll.** Holding the channel open for 25s waiting on a
message while a queued task sits unstarted would be the one wait nobody asked for.

**No registry ⇒ inline.** The registry is the queue, so without one there is nothing to
queue into and the request runs inline. That is one rule, not a mode switch.

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

## The live status line

Between the queue receipt and the deliverable there used to be nothing. On a validated run
that is two model calls of silence, in which a working runtime and a wedged one look exactly
alike from the channel.

The drain now sends **one** short line when a run actually starts — a different fact from
"queued", which is why it is its own message — and then **edits that message in place** as
each stage begins:

```
[treg_6d30347] 이 사업 아이디어를 분석해줘: 구독형 세차
상태: 분석 중
```

- **One message, however long the run.** Six stages as six notifications would be worse than
  the silence. Editing is wired to this line only: an analysis Thomas has already read must
  not change under him, for the same reason the ledger is append-only.
- **Stages that ran, not stages that were planned.** The pipeline reports from inside each
  branch, so a run that skipped the independent review never claims one.
- **One vocabulary.** The stage names are the pipeline's own step names (`pipeline.STEP_*`),
  the same strings the programization observer records — a rename moves both consumers.
- **Nothing durable, nothing audited.** The line is a `CONTROL_CHANNEL_RESPONSE` (ALLOW) on
  the already-verified channel — the ack precedent — and the durable account of the run stays
  the registry entry plus its ledger trail. Nothing was added to `task_registry_entry` for it:
  a message id that does not survive a restart does not belong in a durable record.
- **Every failure direction is "the run wins."** A line that cannot be sent, cannot be
  edited, or comes back with no message id to edit costs the display and nothing else; the
  first failed edit switches the line off for that run rather than retrying five more times
  into a channel that just refused.

## Honesty rules this implementation follows

- **No fake progress.** The pipeline is a sequence of stages, not a measurable fraction, so
  `/tasks` reports **elapsed time**, which the registry actually knows, and the status line
  reports the **stage name**. No percentage anywhere — a run has no denominator (a model call
  takes as long as it takes), and a bar that reaches 80% and sits there is a lie told slowly.
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
| `FRONTDESK` | yes | conversational submissions (F2), with the kind the front desk read |

The scheduler's maintenance and crypto kinds are not task-shaped and keep their own
scheduler events. Stated here rather than left to be discovered: `/history` is complete for
what the deployed service runs unattended, not for every code path that can call the
pipeline.

## Deliberately excluded

- **Parallel execution.** Execution stays single-process sequential. The registry gives
  *visibility*, not concurrency. A worker pool would need concurrent budget/gate semantics
  and is a separate Thomas decision.
- **Priority ordering.** The queue is strict FIFO. `!중요` raises the *task's* priority
  (which under the "auto" policy adds the reviewer), but it does not jump the queue — no
  ordering is displayed that the runtime does not actually implement.
- **Queueing scheduled work.** The scheduler runs its own tick loop and records its
  `analysis_task` fires inline; it does not feed the operator queue.
- **Cancelling a running task.** The kill switch owns that.
