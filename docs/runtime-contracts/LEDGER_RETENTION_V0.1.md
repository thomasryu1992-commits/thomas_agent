# Ledger Retention v0.1

**Status:** Active runtime capability
**Owner:** Thomas
**Authority:** None. This document describes an implementation; the canonical Governance
Policy (`governance/GOVERNANCE_POLICY.yaml`) owns the rules it obeys — in particular
`audit_concealment`, which is a BLOCK-tier prohibited action.

The record ledger reached **56MB** on the live host and only grows. It is read on every
dashboard build and every `/result`, and an append-only file with no story ends up either
unreadable or quietly truncated by whoever runs out of disk first.

## The policy, in one sentence

**Rotation moves rows out of the active file into an archive file beside it. Nothing is
ever destroyed.**

An archived row is still on disk, still byte-identical, still readable. That is what makes
this safe to automate: a job that can only move bytes cannot conceal anything. "Retention"
in this repo does not mean deletion, and no code path here deletes a ledger row.

## What may be rotated, and what may never be

| Ledger | Rotatable | |
|---|---|---|
| `records.jsonl` | ✅ | the 56MB one |
| `blocks.jsonl` | ✅ | |
| `scheduler_events.jsonl` | ✅ | |
| `memory_events.jsonl` | ✅ | |
| `programization_events.jsonl` | ✅ | |
| `feedback_events.jsonl` | ✅ | |
| **`audit_events.jsonl`** | ❌ | **the hash chain** |
| **`control_events.jsonl`** | ❌ | **the kill switch's recovery source** |

The two exclusions are **not one blanket rule**. They are different failures:

**`audit_events.jsonl`** — front truncation *is* detected (the first event must be a
genesis with a null previous hash), so rotating the front would make an honest ledger
verify as **tampered**. And in the other direction, a **prefix** of a valid chain is itself
valid — which is exactly why tail truncation is the chain's documented blind spot. A tool
that routinely rewrites this file is a tool that makes the one signal of tampering
ordinary. Excluded outright rather than made careful.

**`control_events.jsonl`** — `ControlStore.load()` consults it when the state file is
missing, precisely so that *deleting* the state file cannot silently resume a KILLED
runtime. Rotating it is a way to lose the last KILLED event, which turns a safety stop into
an unauthenticated resume. It is small besides: nothing to gain, a kill switch to lose.

Asking to rotate either is a typed refusal (`LEDGER_PROTECTED_FROM_ROTATION`) carrying that
file's own reason, not a generic "no".

## How it behaves

- **Streams.** Rows move as raw lines — never parsed, never re-serialized — so an archived
  row is byte-identical to what was written, and memory is one line regardless of a 56MB
  ledger. (The dashboard learned this the hard way: it OOM-killed loading this same file.)
- **Holds the writer's lock.** Rotation takes the same per-file lock appends take
  (`LedgerStore.file_lock`), so it can never interleave with a write. A reader sees the
  pre- or post-rotation file, never a torn middle.
- **Archive first, replace second.** A crash between the two leaves rows in *both* places —
  duplicated, never lost — and readers only read the active file, so the duplication is
  invisible and the next rotation cleans it up. The opposite order could lose evidence.
- **Never overwrites an archive.** Two rotations in the same second get distinct files.
- **`keep_rows` must be a positive int.** `0` would empty the active file — the one call
  that looks like deletion — and is refused (`LEDGER_INVALID_KEEP`).
- **Recorded.** A rotation that moved anything appends a `ledger_retention.v0` event to the
  block ledger. It does **not** enter the audit chain: moving bytes between files is
  bookkeeping about the store, not a governed action on a task (the `/status` precedent — a
  maintenance read/move does not enter the trail it maintains).
- **One failure does not stop the rest.** The point is to bound growth; a single unreadable
  ledger should not leave the others unbounded. Every failure is reported, never swallowed.

## Using it

```bash
python -m runtime.mvp_runtime.ledger_cli status          # sizes, row counts, what is protected
python -m runtime.mvp_runtime.ledger_cli rotate           # archive all but the newest 2000 rows
python -m runtime.mvp_runtime.ledger_cli rotate --keep 500 --file records.jsonl
```

`status` is read-only and answers in any runtime mode — an operator asking what the store
costs is usually asking because something is already wrong.

## Scheduled rotation

Explicit Thomas decision, 2026-07-26: run it unattended. The `ledger_rotate` schedule kind
does exactly what the CLI does, on a cadence:

```bash
python -m runtime.mvp_runtime.scheduler_cli add --kind ledger_rotate \
    --interval-seconds 86400 --request 2000
```

`--request` optionally carries the row limit; anything unparseable falls back to the module
default rather than guessing a *smaller* one, because guessing small archives more than was
asked — the lossier direction.

It is safe unattended for one reason and it is worth naming: **rotation archives and can
delete nothing.** The scheduled path adds no capability — it calls the same `rotate_all`,
so the two protected ledgers are refused by the retention module itself rather than by this
caller remembering to skip them. It is **kill-switch bound** like every scheduled execution
(`kill_blocks: scheduler_execution`), and a fire without a ledger skips rather than
crashing. Failures ride in the fire's status: a ledger that could not be rotated is one
that keeps growing, and the operator should see which one.

## Deliberately not done here

- **Archive compression or pruning.** Archives only accumulate. That is deliberate for now:
  the first version of a retention mechanism should not also be the first version of a
  deletion mechanism. When disk actually becomes the constraint, compressing archives is
  the next step — and *removing* one remains a separate, explicit Thomas decision.
- **Anything at all for the two protected ledgers.** They grow. If the audit chain ever
  becomes the constraint, the answer is an external anchor + archival of *verified*
  segments, not truncation, and it needs its own design.
