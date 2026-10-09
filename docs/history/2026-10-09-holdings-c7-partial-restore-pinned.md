# A restore that mixes points in time loses flows and still passes — pinned, with a design

- **What this PR adds (no runtime change):**
  - `tests/test_mvp_runtime_holdings_c7_partial_restore.py` covers C7-1 … C7-10, synthetic only.
    - The cases the code handles stay asserted as they must stay:
      - a normal pair;
      - a stop between the append and the save;
      - a state behind a longer ledger;
      - a ledger restored within the one-day re-read;
      - both files restored from one point;
      - a cursor ahead of the newest event on a quiet day;
      - a broken chain refused with both files unchanged;
      - no duplicate after any re-read.
    - Three cases are pinned as they behave today, in tests named `test_defect_…`:
      - **a ledger restored past the re-read loses the flow, its exception goes, and the readiness
        says PASS**;
      - a ledger from another history with the same length passes;
      - a state with no checkpoint reads as consistent.
  - `docs/proposals/HOLDINGS_LEDGER_CHECKPOINT_V0.1.md` (DRAFT) holds three things:
    - the fix: the state records the ledger's line count and that line's hash; an ancestor passes, and
      a shorter or different ledger is refused with `LEDGER_TAMPERED`;
    - the migration of states that have no checkpoint: `UNCHECKED` until the operator adopts;
    - the backup and restore rules.
- **Why:** found in the H6d pre-deploy verification (2026-10-09) and re-checked after the deploy. The
  read cursors live only in the state file. A ledger put back without its state starts the next read
  after the flows it lost.
- **Shaped this way because:**
  - The cursor cannot be the detector. A fire with no rows still moves it to `now`, so "cursor ahead
    of the last event" is a normal quiet day (C7-7).
  - The repo has no `xfail`. The defects are therefore asserted as they are, and the tests' docstring
    says they flip when the design lands.
- **Not changed:** the runtime, the state file format and the production state. Building the fix waits
  on Thomas's decisions D1–D4 in the proposal.
