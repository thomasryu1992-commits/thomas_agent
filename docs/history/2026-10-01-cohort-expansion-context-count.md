# The cohort expansion proposal counted contexts in two units; the walk still fetches 15

- **What was wrong:** `docs/proposals/FORWARD_COHORT_EXPANSION_V0.1.md` §6 said a second cohort would grow
  the walk's contexts from 15 to 17. Its §1 table put cohort 1 at 15 contexts and cohort 2 at 17.
- **Why:** the freeze record's `context_sizes` keys on symbol scope × timeframe, where the five-symbol
  cross-section scope is a context of its own. Both cohorts have 17 of those. The walk fetches per symbol
  × timeframe, which is 15 (5 symbols × 3 timeframes), and the second cohort did not change that. The 17
  is the unit K is counted in, not the unit fetched.
- **Measured:** the first walk after the second freeze (2026-10-01 07:15 UTC) logged `contexts=15
  walked=15 members=197`.
- **Also corrected:** §6 said the walk's duration is not on its event. It is, as `duration_ms`: 65 s on
  2026-09-29, 63 s on 09-30, and 72 s on 10-01, the run that first replayed cohort 2 from its selecting
  rows.
- **What this PR changes:** §1's table row, §6, and this entry. No decision moves, and N3's K table was
  already in the freeze record's unit.
