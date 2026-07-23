# Operator Feedback v0.1 (E1)

Status: ACTIVE (implemented 2026-07-23, explicit Thomas decision 2026-07-23)
Owner: Thomas
Implementation: `runtime/mvp_runtime/operator_feedback.py` (+ wiring in `operator.py`,
stream in `store.py`)
Tests: `tests/test_mvp_runtime_operator_feedback.py`

## Why

The ledger records what ran and whether validation PASSED — "was the answer actually
useful" exists only in Thomas's head. Every growth loop this repo has shipped
(programization counter, candidate trials, strategy lifecycle) starts from recorded
outcomes; the analysis pipeline had no outcome signal at all. E1 is the narrowest
capture that closes that gap: one command, one pointer, one append-only stream.
Evaluation (E2) and distillation into proposals (E3) are separate increments with
their own approvals — **nothing here reads feedback back into planning**.

## What it is

`/feedback <good|bad|one-line note>` on the verified control channel records one
`operator_feedback.v0` event on its own ledger stream, bound to the **last delivered
COMPLETED run**.

- **Identity**: parsed after the console and approval command families, behind the
  same R4 identity gate, sharing their tokenizer (`control.command_verb`). Only the
  registered Thomas in the registered private 1:1 chat can leave feedback.
- **Target binding**: the loop (`run_operator_once`) writes a small per-machine
  pointer (`.runtime_governance_state/last_delivered.json`, atomic write — the
  Telegram-offset pattern) only AFTER a COMPLETED reply was actually sent. Feedback
  can never bind to a run Thomas has not seen. No pointer → typed refusal
  (`NO_FEEDBACK_TARGET`); malformed pointer → `FEEDBACK_TARGET_UNREADABLE`
  (fail-closed, never a guess). Explicit task addressing is deferred until the
  one-target default proves insufficient.
- **Verdict**: a recognized leading token (`good`/`bad`/`좋음`/`나쁨`/…) classifies
  the event GOOD/BAD; anything else is recorded whole as an unclassified NOTE — a
  forced classification would be a guess.
- **Record**: `operator_feedback.v0` via `stamped_event` (self-hashed, the
  `control_event`/`memory_event`/`audit_gap` precedent — a standalone operator event
  on its own stream, not the run's audit chain): `trace_id`, `delivered_at`,
  `verdict`, `comment`, `operator_id`, `created_at`. Stream:
  `feedback_events.jsonl` (append-only, per-file cross-process lock, surfaced by
  `store.health()`).
- **Runtime mode**: like `/approve`, answered while PAUSED/KILLED — judging
  already-delivered work is not new execution. Like every `/` command it can never
  fall through to the pipeline.
- **Honest failure**: a feedback whose ledger append fails is REFUSED with the
  persistence reason code — never confirmed. The delivery pointer itself is
  best-effort (the ack precedent): losing it degrades `/feedback` to an honest
  refusal and never costs the delivered reply or the batch.

## What it deliberately is not

- No new Contract / Schema / Registry / Gate. The `.v0` stamped-event precedent
  covers the record; the governance policy is untouched (`/feedback` is not an
  emergency control, so `emergency_controls_allowed` and its drift gate are not in
  play; recording an annotation is ALLOW-tier).
- No analysis, aggregation, or feed-back into prompts/roles/memory — that is E2/E3.
- No feedback on BLOCKED/refused runs (their reason codes are already the recorded
  outcome) and no CLI-side capture (the one-shot CLI prints to the operator's own
  terminal; the corpus this exists for is the deployed Telegram loop).

## Refusal vocabulary

| reason_code | meaning |
| --- | --- |
| `EMPTY_FEEDBACK` | `/feedback` with no payload |
| `NO_FEEDBACK_TARGET` | nothing delivered yet on this machine |
| `FEEDBACK_TARGET_UNREADABLE` | pointer file corrupt — fix or delete it |
| `FEEDBACK_UNAVAILABLE` | no ledger wired on this channel |
| `DELIVERY_POINTER_PERSIST_FAILED` | pointer write failed (loop-internal, swallowed best-effort) |
| `LEDGER_WRITE_FAILED` | the feedback append itself failed — reported, never confirmed |
