# The signed testnet door, run end to end against a venue that fails at each step

- **Why:** the residual safety review (PR-B) asked whether every way a signed testnet cycle can fail stays
  short of COMPLETE evidence. `testnet_evidence.cycle_findings` is the one judge, and its parametrized test
  already breaks each half of a stored row. What was not pinned end to end is that `run_signed_testnet_cycle`
  records each failure truthfully when the venue misbehaves at that step. Before this PR the end-to-end tests
  covered only the happy path, a refused stop, a position left open, an interruption after the entry, refusals,
  and unknown outcomes.
- **What this PR adds:** tests only, no runtime change.
  - `_FaultyVenue`: the dry-run testnet adapter, failing at one named step.
  - 17 failures:
    - entry: rejected, unknown, quantity mismatch;
    - protective legs: SL or TP rejected, SL or TP unknown, SL invisible;
    - cancel: wrong endpoint, rejected, unknown;
    - exit: rejected, unknown;
    - after the exit: the exit filled but the position is still open, quantity mismatch, side mismatch, and
      final reconciliation unreadable.
  - In every case no cycle is complete, and `assert_complete_cycle` refuses every row that was recorded, so none
    can back the stage climb.
  - A control with no fault completes, which proves each case fails because of its fault.
  - Evidence persistence failure surfaces as an error, and nothing is reported complete.
- **Mutation check:** dropping any of four findings fails the matrix: the withdrawal, the venue position view,
  the exit reconcile, and the resting status.
