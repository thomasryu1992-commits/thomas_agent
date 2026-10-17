# C7 PR-A: the holdings ledger checkpoint — detect a mixed restore, never enforce

**What changed** (`runtime/mvp_runtime/holdings/cash_flows.py`, two test files).

PR-A of `HOLDINGS_LEDGER_CHECKPOINT_V0.1.md` §10, built to the PR-A readiness note §3.2–§3.6 after PR-D
(#1216, merged as `52b744e9`). D1 (Thomas 2026-10-11) set the order PR-D → A → B → C and the condition that PR-A
detects only.

**Approval basis, stated as it stands.**
- Built and opened on 2026-10-16 inside Thomas's 8-hour autonomous grant. That grant did not lift D1's
  condition that each PR's implementation is approved separately, and no approval preceded the work.
- On 2026-10-17 Thomas ratified keeping it, decided R-2 and R-3, and approved the corrections below.
  The ratification is not recorded as a prior approval.
- Merge is a separate approval. **Deploy: not before PR-B** (Thomas 2026-10-17), and Option A's hold
  applies regardless.

- **The checkpoint.** The state keeps `ledger_checkpoint = {lines, sha256, at, basis, observed_at}`: the
  ledger's length and last hash as of the last write, and where the checkpoint began. Because the ledger is a chain, line N's hash proves lines 1…N.
  `_commit` moves the checkpoint to the new end after every write.
- **Two detectors, under both locks** (inside `_commit`'s `build`):
  - **Checkpoint.** The ledger is shorter than N (`checkpoint_ledger_shorter`), or holds another line at
    N (`checkpoint_ledger_replaced`), or the checkpoint is not well formed (`checkpoint_malformed`).
  - **Told keys.** An exception the state says was told has no line left in the ledger
    (`told_exception_missing`). Told keys are compared against every exception line, open or resolved,
    so a told exception closed since is not a loss.
- **Detect only.**
  - A detection writes the marker PR-D already reads: `ledger_checkpoint_mismatch` =
    `{reason_code, reason, since, checkpoint}`.
  - The checkpoint stays frozen, so PR-D's alert key does not change every fire.
  - The writes go on. Nothing is refused, repaired or re-read.
  - The marker stands until an operator adopts a ledger (PR-C). It does not clear itself even if the
    original ledger comes back.
- **Readiness.**
  - `ledger_state_consistency` FAILs on a marker or a live detection.
  - Readiness now reads the state **before** the ledger. A write between the two reads can then only
    lengthen the ledger past the checkpoint, never produce a false FAIL. A test runs that race.
  - The readiness detail reports `checkpoint: absent|observed` and `checkpoint_history: UNVERIFIED`.

**R-2 and R-3, as Thomas decided them (2026-10-17).**
- **R-2: a detection-only reason code.**
  - The marker carries `HOLDINGS_CASH_FLOW_LEDGER_MISMATCH_DETECTED`, never `LEDGER_TAMPERED`, which
    means a write was refused and nothing written.
  - `reason` names the detector.
  - PR-D's BLOCKED message for the marker category now says what is true: a mismatch was detected, the
    writes go on, and readiness FAILs. The first version said the writes were refused. The other
    categories keep their text (C7 §6: code, time and next action, no amount or account).
- **R-3: (가-수정), basis recorded.**
  - PR-A still writes no ledger line.
  - Every checkpoint records `basis: observed` and `observed_at`, the moment this code first saw the
    ledger. Both survive every move: moving verifies the lines added since `observed_at`, never the
    history before it. So a checkpoint first recorded over a restored or lost state does not become
    "verified" by being moved, which closes the gap the first version had: there, one write turned
    `absent` into a present checkpoint.
  - `absent` and `observed` are both **UNVERIFIED** history. PR-A never reports anything else, and
    whatever basis a state file claims, it reads `observed`: the state cannot vouch for itself. Only a
    later step that verifies the history (PR-B, an operator adoption) may write another basis.

**Recorded, not decided: where this meets D2 and the readiness note.**
- The readiness note says `ledger_state_consistency` keeps its meaning. D2 puts "no checkpoint, state
  missing → UNCHECKED" in a separate key, `history_completeness`, which PR-B builds.
- So with the checkpoint `absent` or `observed`, `ledger_state_consistency` can still be PASS, and
  `ready` (all checks PASS) can still become true. The detail beside it says
  `checkpoint_history: UNVERIFIED`.
- Gating `ready` on the basis would change the readiness contract that D2 and the note fixed. It is
  left to Thomas, not inferred here. Until PR-B, the decided "no deploy before PR-B" is what keeps this
  from reaching operation.

**Tests.**
- `test_defect_c7_4` and `test_defect_c7_6` flip and are renamed.
  - C7-4 is detected inside and past the one-day overlap. Inside it, D1 counts a shorter ledger as a
    restore even though the re-read finds the flow.
  - C7-6 is detected.
  - Both assert that the flow is still lost and the writes go on.
- `test_defect_c7_8` stays a defect for PR-B. It now strips the checkpoint to model a pre-PR-A state, and
  asserts `checkpoint: absent` with history UNVERIFIED. `test_defect_c7_11` is unchanged.
- New: `tests/test_mvp_runtime_holdings_c7_checkpoint.py`, 14 cases. It covers:
  - recording and moving the checkpoint, and freezing it with a stable alert key while writes go on;
  - no self-clearing;
  - the told-key detector, including a resolved exception that is no loss;
  - malformed checkpoints, and the absent case;
  - the read order and its race;
  - the checkpoint and the marker surviving every state-only writer;
  - the R-2 and R-3 decisions: the code, the alert text, `observed` surviving moves, and a state file
    unable to claim a verified basis.
- **Fault injection** (scratch, outside the suite): 21 of 21 faults made a test fail. Six of them are
  on the decisions: the code reverted, `observed_at` reset, a move promoting the basis, the state
  vouching for its basis, observed read as verified, and the alert claiming a refusal. The first
  version's one equivalent mutant (detection after the writer's patch) is no longer listed.

**What this does not change.** No ledger format or event, no refusal, no cutover, no epoch, no H6b or
H6c state. No door reads the new keys. The board and the snapshot show the same readiness fields as
before: `checkpoint` and `checkpoint_history` are in `readiness()`'s detail, not in the stored snapshot. No real ledger, state file or exchange was touched.
