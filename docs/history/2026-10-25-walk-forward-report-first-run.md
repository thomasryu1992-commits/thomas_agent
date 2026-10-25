# Walk-forward report: first run, and it now reads evidence standing

- **Why:** The 2026-10-06 decision gated PR-2 on the §4 report of `WALK_FORWARD_TEMPORAL_STABILITY_V0.1.md`, and
  the report had never been run. Run as written on 2026-10-25, it showed sharp discrimination: CONFIRMED p50 0.70
  vs CONTRADICTED 0.45, an AUC of 0.89 on 14 vs 1,874.
- **What it was:** 12 of the 14 CONFIRMED rows carry `oi_lookahead` (INVALID). A lookahead leaks the same future into
  the scored region and the holdout, so it lifts both the metric and its answer key. Without those rows, two
  CONFIRMED remain, both BIASED, with p50 0.40 (AUC 0.39).
- **What changed:**
  - `walk_forward_stability_report` leaves INVALID evidence out of the discrimination split and counts it.
  - The occupancy and migration sections are unchanged, since they describe the store, not a verdict.
  - Each status line shows how many of its rows are BIASED.
- **Proposal:** the result is recorded in §4, plus W1, which asks to close the proposal without PR-2 under the
  10-06 rule.
- **Checked:**
  - A synthetic store with two lookahead CONFIRMED rows prints "2 with INVALID evidence left out", and CONFIRMED
    holds only the valid row.
  - Removing the exclusion fails the test.
