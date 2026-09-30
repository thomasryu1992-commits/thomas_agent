# The cycle's stages get names (crypto refactor plan PR-11)

- **What was there:** `cycle.run_crypto_cycle` was one function of 672 lines: collection and its five
  legs, the pool read, the risk verdict, the paper step, the two observers, the report, the live
  permission, the live leg, the lifecycle and the record.
- **What changed:** six stages are functions in the same file, called in the same order:
  `_collect_context`, `_read_pool`, `_judge_risk`, `_realized_stats`, `_blocked_plan` and
  `_lifecycle_step`. Their bodies are the lines that were in `run_crypto_cycle`, unchanged: every line
  of the old function is in the new file once, except one blank comment separator.
  `run_crypto_cycle` is 398 lines.
- **What stayed inline, on purpose:** `run_paper_update`, the counterfactual and forward-book updates,
  the report, the live allowance with the narrowing of the live-routable set, `run_live_leg`, and the
  record. The order settle, permission, live leg, lifecycle is still read off one function, and
  `run_live_leg` is still called from `run_crypto_cycle`, which is the pair the egress roster names.
- **Why the same file:** tests patch `cycle` in a dozen places (`run_live_leg`, `run_risk_guard`,
  `read_outcomes`), and the stage-order test wraps six more of its names. A function in the same
  module reads the same globals, so every patch still reaches. No layer, import or `__all__` change.
- **How the stages share the reason codes:** each takes the cycle's one list and appends to it, as the
  inline code did, so the record lists the codes in the order the stages ran.
- **Tests, written first and passing on the old function:**
  - two characterization tests pin the full `reason_codes` list of a cycle where several reads fail;
  - `test_lifecycle_still_sees_the_full_history` used to read `run_crypto_cycle`'s source for two call
    spellings, which an extracted stage breaks without any behaviour changing. It now records what
    the guard and the lifecycle are handed in a real cycle: the guard the live history and none of the
    paper store, the lifecycle the paper store whole.
- **Evidence that nothing changed:**
  - record comparison (`scripts/ops/crypto_record_capture.py`, one worktree, the two commits): 6,687
    lane tests, 10,210 files written. Ten differ, all in the scheduler's measured `duration_ms` and
    the event hash over it;
  - a cycle digest on throwaway roots, no live opt-in: eight scenarios of 150 fires on a moving price
    path (real and dry store, a LIVE-tier entry, the venue down, and the paper history, live history,
    limits and pool each spoiled mid-run). 151 opens, 147 settles and 491 lifecycle decisions in the
    plain run. Every summary and every file written hash the same;
  - the patch-reach census on `cycle`: the same 31 tests read the same patched names before and after;
  - the stage-order test from PR-05 passes unchanged, and the full suite passes.
- **Behaviour change:** none intended and none measured. This is the cycle path, so the plan asks for
  one more thing the tests cannot give: watch the first fire after the next candidate deploy.
