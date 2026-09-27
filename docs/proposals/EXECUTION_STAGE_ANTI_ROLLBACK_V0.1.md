# The execution stage gets an anti-rollback ledger

**Status:** PROPOSAL 2026-09-27 — four decisions for Thomas (§6). Nothing is built.

The stage record (`crypto/execution_stage.json`) can be put back. The module says so itself
(`execution_stage.py:42`): "one who kept a copy of an earlier witnessed record can put it back,
undoing a demotion." This proposal replaces the single overwritable file with an append-only,
hash-chained ledger whose tip *is* the stage, plus an anchor that sits outside what a backup restore
brings back. It changes what a stage read verifies. It does not change the ladder, the transitions, the
witness rules, or which doors read the stage.

## 1. What can go wrong today

A stage read (`resolve_execution_stage`) checks the file's self-hash, schema, venue, transition shape,
effective time, witness and policy identity. The witness is the CONSUMED approval whose
`consumption_ref` names the record's `stage_id`. None of these checks is about **time order**. A record
that was valid yesterday is still valid today, because its approval is still CONSUMED and still names
it. So:

| # | Vector | What comes back | Detected today? |
|---|---|---|---|
| R1 | An old copy of `execution_stage.json` is put back (operator copy, a script, a partial restore) | The pre-demotion stage | No: the old record's witness still checks out |
| R2 | The whole `.runtime_governance_state/` is restored from the daily tar (`backup-governance-state.sh core`, 7 kept; Thomas 2026-08-29) | Stage, approval ledger and control ledger, all consistently older | No, and nothing *inside* the directory can detect it: every witness in there agrees with every other |
| R3 | The service uid deliberately forges the directory (ledger, approvals, anchor) | Anything | Out of scope, as today: that uid can place orders directly (`execution_stage.py:43-44`) |

R1 and R2 are the real ones. Neither is an attack. Both are ordinary operations that silently undo
decision 8 ("a demotion is immediate and must never be lost"). R2 matters more: a restore is exactly
what someone does after an incident, and an incident is often why the machine was demoted.

The control-ledger event (`execution_stage_transition.v0`) already records every transition. It is
not a defence. It is written **after** the record, and a failure to write it is only a warning
(`register_execution_stage._ledger_event`). It lives in the same restored directory. Nothing reads it
on a stage read.

## 2. Design

### 2.1 The ledger is the record

A new file, `crypto/execution_stage_ledger.jsonl`, is append-only and written only under `stage_lock`.
Each row looks like this:

```
{schema_version: "execution_stage_ledger.v0.1", seq: n, prev_row_sha256: <row n-1> | null,
 record: <the stage record exactly as today, record_sha256 included>, appended_at, row_sha256}
```

The **tip's `record` is the stage.** `read_registered_stage` reads the tip instead of
`execution_stage.json`. Everything after that stays as it is today, applied to the tip record: schema,
venue, `_transition_consistent`, effective time, `_witness_reason`, policy identity.

Two more checks run on the chain:

- **Chain integrity (every row, every read):**
  - each `row_sha256` is true;
  - `seq` runs 0..n without gaps;
  - `prev_row_sha256` links each row to the one before;
  - each row's `record.previous_stage` equals the prior row's `record.stage`;
  - no two approved (non-DEMOTE) rows name the same `approval_id`.

  Transitions are rare, so the chain is a few dozen rows at most and reading all of it is cheap. The
  whole chain is checked, not just the tip, because a tip check alone cannot tell a truncated chain
  from a complete one.
- **Anchor (§2.2).**

Any failure reads **READ_ONLY with its reason** (decision 9), never an exception.

**Why the ledger replaces the file instead of sitting beside it (D2).** With both, every write has two
steps. A crash between them leaves "file ≠ tip". That reads READ_ONLY, which is safe, but getting back
needs a repair that rewrites the file from the tip: a stage write with no approval. A repair like that
can be shown never to exceed the tip, but it is simpler not to have one. With one authoritative file,
the question "did the transition happen" has one answer: is the row there.

`execution_stage.json` is kept as a **mirror written after the row**, for humans and old tooling. No
decision reads it. A test pins that no stage consumer opens it. Once a release has passed with no
readers left, the mirror can be removed.

### 2.2 The anchor: what a restore does not bring back

The anchor is a small self-hashed file naming the ledger row the machine last reached:

```
{schema_version: "execution_stage_anchor.v0.1", venue, seq, row_sha256, written_at, anchor_sha256}
```

The read rule is: **the row the anchor names must be on the chain.** Concretely,
`anchor.seq <= tip.seq` and `rows[anchor.seq].row_sha256 == anchor.row_sha256`, with
`tip.seq - anchor.seq <= 1`. The last condition leaves room for one crash between the row write and
the anchor write (§2.3).

| State after the event | Read |
|---|---|
| Anchor names a row on the chain, tip at most one ahead | Normal |
| Chain is shorter than the anchor, or holds a different row at `anchor.seq` (restored ledger, anchor survived) | READ_ONLY `EXECUTION_STAGE_ROLLED_BACK` |
| Anchor file is gone (restored by `rm -rf` then untar, which dropped it) | READ_ONLY `EXECUTION_STAGE_ANCHOR_MISSING` |
| Anchor fails its self-hash | READ_ONLY `EXECUTION_STAGE_ANCHOR_TAMPERED` |
| Tip is two or more rows past the anchor | READ_ONLY `EXECUTION_STAGE_ANCHOR_BEHIND` |

Both restore shapes fail closed. Untarring over the live directory keeps the newer anchor, so it reads
ROLLED_BACK. Wiping the directory first loses the anchor, so it reads ANCHOR_MISSING. Only one thing
defeats the anchor: **the anchor itself being restored together with the ledger.** So the anchor
must never be in the backup (D1).

The anchor does not make the machine trust the backup less. The approval ledger and everything else
in the tar are restored as before. Only the stage refuses to come back above READ_ONLY on its own.

### 2.3 Write order: the machine never reads higher than what was witnessed

Everything below happens under `stage_lock`, as the file write does today.

**DEMOTE** (no approval; decision 8):

1. Append the row.
2. Write the anchor.
3. Write the mirror.

| Crash after | Read | Direction |
|---|---|---|
| Nothing written | the old stage | the demotion did not happen; the door reports failure, as today |
| 1 | the demoted stage (tip is one past the anchor) | ✓ at the target |
| 2 | the demoted stage | ✓ |

If step 2 fails, the demotion **has taken effect** but is not yet restore-proof: a restore to the
pre-demotion ledger would still carry the old anchor row. The door reports
`EXECUTION_STAGE_ANCHOR_WRITE_FAILED` beside WRITTEN, and every later `plan_transition` refuses
`EXECUTION_STAGE_ANCHOR_SYNC_REQUIRED` until the anchor matches the tip. `--sync-anchor` fixes that and
needs no approval. It writes only the anchor, set to the currently **verified** tip. It cannot change
the tip, so it cannot change the stage. All it does is make rollback detection stricter. DEMOTE to
READ_ONLY keeps its guarantee: it works from any state, including an unreadable ledger. In that case
it starts a **new chain** (seq 0, `prev_row_sha256: null`, `evidence.replaced_reason_code`) and writes
a fresh anchor.

**CLIMB / REBIND / BOOTSTRAP** (approved):

1. Spend the approval (as today, under `spend_lock`).
2. Append the row.
3. Write the anchor.
4. Write the mirror.

| Crash after | Read | Direction |
|---|---|---|
| 1 | the old stage; the approval is spent | as today: `EXECUTION_STAGE_WRITE_FAILED_AFTER_SPEND`, ask again |
| 2 | the new stage (witnessed, tip one past the anchor) | ✓ the witnessed stage |
| 3 | the new stage | ✓ |

The row is built and fully validated **before** the spend, as `record_from_approved` does today, so a
row that could not be written leaves the grant APPROVED.

### 2.4 The only way back up is BOOTSTRAP

ROLLED_BACK, ANCHOR_MISSING, ANCHOR_TAMPERED, ANCHOR_BEHIND and every LEDGER_* reason are **not
rebindable**. They read READ_ONLY. From READ_ONLY the ladder already allows exactly one thing: a
Thomas-approved BOOTSTRAP at SHADOW or PAPER, carrying an attestation. If the chain and anchor
verify (for example, the tip is a DEMOTE to READ_ONLY), the BOOTSTRAP row is appended. Otherwise it
starts a new chain and names what it replaced: `evidence.replaced_reason_code`, plus the old tip's
`seq`/`row_sha256` when the old tip is readable. The old file is kept beside it as
`execution_stage_ledger.replaced-<stamp>.jsonl`, never deleted. Either way it writes a fresh anchor.
DEMOTE to READ_ONLY over an unreadable ledger (§2.3) replaces the file the same way.

So after any rollback, the machine is back at PAPER at best. Every rung above PAPER has to be climbed
again: one approval per rung, and LIVE_AUTONOMOUS needs a COMPLETE signed testnet cycle. No new
recovery door is added.

**The cost, stated up front:** a *legitimate* disaster recovery from backup will also read READ_ONLY
and need one BOOTSTRAP approval before the machine climbs again. That is the intended trade: after a
restore, nobody can tell a stage that should be back from one that should not. Closing is never
affected (§3).

### 2.5 Reason codes

- **Read side** (all read READ_ONLY):
  - `EXECUTION_STAGE_LEDGER_MISSING`
  - `EXECUTION_STAGE_LEDGER_UNREADABLE` (includes a torn tail)
  - `EXECUTION_STAGE_LEDGER_BROKEN` (a self-hash, link, seq or stage-continuity failure)
  - `EXECUTION_STAGE_ROLLED_BACK`
  - `EXECUTION_STAGE_ANCHOR_MISSING`
  - `EXECUTION_STAGE_ANCHOR_TAMPERED`
  - `EXECUTION_STAGE_ANCHOR_BEHIND`
- **Door side:**
  - `EXECUTION_STAGE_ANCHOR_WRITE_FAILED` (a warning beside WRITTEN)
  - `EXECUTION_STAGE_ANCHOR_SYNC_REQUIRED` (a plan refusal)

The existing `EXECUTION_STAGE_RECORD_*` codes keep their meaning, applied to the tip record.

New schemas: `execution_stage_ledger.v0.1.schema.json` and `execution_stage_anchor.v0.1.schema.json`.
`execution_stage.v0.1` (the record inside a row) is unchanged.

## 3. What does not change

- **The stage still gates new exposure only.** Closing, settlement, the protection re-check, the time
  exit and reconciliation never read it. A READ_ONLY caused by a ledger or anchor fault cannot trap an
  open position, at any stage.
- **The `resolve_execution_stage` contract ("never raises") stands.** The module's own objection
  applies here: `_live_entry_evidence` refused a registry read on the live path because "a damaged
  file would make the machine silently read READ_ONLY on every cycle". A damaged ledger *will* do
  exactly that. Here it is the intended direction: a stage whose history cannot be verified is not a
  stage. The difference from that case is that the fault is **named** (a reason code on the readiness
  board and in every live-route record), not silent.
- **The ladder, the four transitions, the witness rules and the policy binding are unchanged.**
- **The effect at today's stage (PAPER) is nil.** All four `allows()` callers need a rung above PAPER:
  - `live_order.py:614` (autonomous and probe);
  - `promotion.py:253` (LIVE arming);
  - `live_readiness.py:1003`;
  - `testnet_execution.py:387` (SIGNED_TESTNET).

  At PAPER, a READ_ONLY read changes what the readiness board prints and nothing else. This is what
  makes the rollout window in §5 free.

## 4. Scope note: the control store has the same hole

`trading_armed` and `halt_level` (HARD/SOFT, PR6) live in the same restored directory. An R2 restore
could re-arm the machine or lift a HARD halt taken after the backup. That is the same class of problem
and is not covered here. The anchor is designed so it can cover more than one record later: one anchor
per venue could name the stage tip **and** a control-state tip. That is a separate proposal once this
one is decided.

## 5. Rollout

1. **One PR (code, schemas, tests, docs).**
   - Tests:
     - crash injection at every step of §2.3, for DEMOTE and for an approved climb (the read is never
       above what was witnessed);
     - R1 (copy an old mirror or an old ledger back);
     - R2 (snapshot the state dir, demote, restore it with and without the anchor) → READ_ONLY;
     - chain breaks (edited row, missing row, reordered rows, a reused approval);
     - `--sync-anchor` cannot move the tip;
     - DEMOTE to READ_ONLY over an unreadable ledger;
     - the never-raises contract;
     - a test pinning that no consumer reads `execution_stage.json`.
   - The full `pytest` plus the release gate, as always. The gate does not run the runtime suite.
2. **Deploy (candidate tag).** The machine then reads READ_ONLY `EXECUTION_STAGE_LEDGER_MISSING`
   (§3: no effect at PAPER).
3. **Genesis by the existing BOOTSTRAP (D3).**
   - `--request --to PAPER --attest ...` in module form, as uid 10001:
     `docker exec thomas-scheduler python -m scripts.register_execution_stage --request ...`
     (never a host CLI as root).
   - Thomas approves on the verified channel.
   - `--confirm` writes row 0 and the anchor.
4. **Backup exclude (D1).** Add the anchor's path to `backup-governance-state.sh`'s `--exclude` list
   (a host change, recorded in a host_state PR). Make `backup-watch.sh` fail loudly if an archive ever
   contains it.

## 6. Decisions for Thomas

- **D1 — where the anchor lives.**
  - (a) *Recommended:* `crypto/execution_stage_anchor.json` inside the state directory, excluded
    from the tar. No compose change. Both restore shapes still fail closed (§2.2).
  - (b) A separate host directory outside `.runtime_governance_state/`, with a new bind mount into
    `scheduler`. It survives a wipe-and-untar restore too, so it reads the more precise ROLLED_BACK
    instead of ANCHOR_MISSING. The cost is a compose change and one more path to keep out of `MEMBERS`.

  Either way, the backup must never carry the anchor. That is the whole mechanism.
- **D2 — the ledger replaces the file** (recommended, §2.1). The alternative keeps the file
  authoritative and requires file == tip, which needs a no-approval repair path.
- **D3 — genesis.**
  - (a) *Recommended:* the existing BOOTSTRAP at PAPER (one approval, no new write path).
  - (b) A one-time `--migrate` that copies the current verified record into row 0 without
    approval. That copies a record which is itself the thing R1/R2 can forge.
- **D4 — accept the recovery cost:** after any restore, the stage comes back only through BOOTSTRAP
  (§2.4).
