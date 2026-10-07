# S1 runs only if 1d selection is not worse than its twins; the hierarchical judgement rule is fixed before the boundary

- **The decision (Thomas 2026-10-07):** "응 권고대로", answering H1 and H2 of
  `docs/proposals/SELECTION_EVIDENCE_V0.1.md` (#1165).
- **H1:** at the first cohort verdict, S1 (tilting the factory toward 1d) runs only if the 1d `(all)`
  line of `forward_cohort report --pairs` holds 38 or more pairs and its 95% interval's upper bound
  is at or above zero. 38 is the majority of the two cohorts' 75 1d members. A held or waiting S1 is
  asked once more at cohort 2's close (2027-03-30), and the line is not re-read in between. Why: on
  2026-10-07 the 1d pairs read −0.711R [−1.282, −0.140], so tilting the mint toward 1d could add
  strategies that do worse than a coin flip on the same bars. S1's cost reasoning stands.
- **H2:** the hierarchical judgement rule of §4.2 is the rule for cohort 1's close (2027-03-22).
  It covers the member-minus-twin unit, BH at q = 0.10 over timeframe × economic family, a
  ten-settlement-day floor, the swap calibration, Holm inside a passing family, and one reading at the
  close. The family grouping is frozen as `selection_evidence.ECONOMIC_FAMILY_MARKERS`. This is S2's
  twin-based judgement, written before the boundary so that the boundary reads it rather than
  inventing it.
- **What changed:** the proposal's status and decision section, the S1 pointer and status line in
  `CRYPTO_STRATEGY_EDGE_ORDER_V0.1.md`, the P1-4 pointer in
  `RESEARCH_FORWARD_THROUGHPUT_ANALYSIS_V0.1.md`, and `STATUS.md`. No code, judgement, door, board,
  constant, schedule or mint share changed.
- **What remains:** the code that issues the hierarchical verdict, built before 2027-03-22, and S1's
  conditional execution at the first verdict.
