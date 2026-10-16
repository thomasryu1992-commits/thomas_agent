# C7 PR-A: the holdings ledger checkpoint — detect a mixed restore, never enforce

**What changed** (`runtime/mvp_runtime/holdings/cash_flows.py`, two test files).

PR-A of `HOLDINGS_LEDGER_CHECKPOINT_V0.1.md` §10, built to the PR-A readiness note §3.2–§3.6 after PR-D
(#1216, merged as `52b744e9`). D1 (Thomas 2026-10-11) set the order PR-D → A → B → C and the condition that PR-A
detects only. Built inside Thomas's 8-hour autonomous grant (2026-10-16). Merge and deploy are each a
separate approval.

- **The checkpoint.** The state keeps `ledger_checkpoint = {lines, sha256, at}`: the ledger's length and
  last hash as of the last write. Because the ledger is a chain, line N's hash proves lines 1…N.
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
  - The readiness detail says `checkpoint: present|absent`.

**Two open decisions, held to their stand-ins rather than decided.**
- **R-2 (the detection's reason code).** The marker carries `HOLDINGS_CASH_FLOW_LEDGER_TAMPERED`, the
  §6 family code PR-D's merged code already falls back to. `reason` names the detector. No new reason
  code is added.
- **R-3 (telling a lost checkpoint from one never written).** Option (가) is kept: they are not told
  apart, and PR-A writes no ledger line of its own.
  - A state without a checkpoint is not judged; it is PR-B's history check (D2: UNCHECKED).
  - The first write records the observed end.
  - **Known limit until PR-B:** a state restored from before PR-A, or a lost state file, reads
    consistency PASS. The readiness detail then says `checkpoint: absent`, so that PASS is never a
    verified restore. Option (나) (`checkpoint_introduced`) would close this at the price of a new
    ledger event.

**Tests.**
- `test_defect_c7_4` and `test_defect_c7_6` flip and are renamed.
  - C7-4 is detected inside and past the one-day overlap. Inside it, D1 counts a shorter ledger as a
    restore even though the re-read finds the flow.
  - C7-6 is detected.
  - Both assert that the flow is still lost and the writes go on.
- `test_defect_c7_8` stays a defect for PR-B. It now strips the checkpoint to model a pre-PR-A state, and
  asserts `checkpoint: absent`. `test_defect_c7_11` is unchanged.
- New: `tests/test_mvp_runtime_holdings_c7_checkpoint.py`, 11 cases. It covers:
  - recording and moving the checkpoint, and freezing it with a stable alert key while writes go on;
  - no self-clearing;
  - the told-key detector, including a resolved exception that is no loss;
  - malformed checkpoints, and the absent case;
  - the read order and its race;
  - the checkpoint and the marker surviving every state-only writer.
- **Fault injection** (scratch, outside the suite): 16 of 17 faults made a test fail. The one survivor
  runs detection after the writer's own patch instead of before. A standing marker returns early, so
  the order changes no outcome: an equivalent mutant.

**What this does not change.** No ledger format or event, no refusal, no cutover, no epoch, no H6b or
H6c state. No door reads the new key. The board and the snapshot show the same readiness fields as
before. No real ledger, state file or exchange was touched.
