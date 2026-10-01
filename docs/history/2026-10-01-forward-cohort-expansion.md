# Widening the forward test means a second cohort, not a lower bar: 82 lineages are eligible and have no clock

- **The proposal:** `docs/proposals/FORWARD_COHORT_EXPANSION_V0.1.md` (DRAFT). Asked by Thomas on
  2026-10-01 after the question "doesn't validating more strategies on paper raise the chance one goes
  live?"
- **What "expand" turned out to mean:** cohort 1 already holds every lineage that clears the OBSERVATION
  bar. A dry-run freeze on 2026-10-01 found 82 more that became eligible after 2026-09-23 and have no
  forward clock: 27 1d, 22 4h, 33 1h, over 17 contexts. The 1h growth is the output of review C3.
  The existing `freeze` and `freeze-nulls` commands cover it; no code is needed.
- **What it does not buy:** speed. Each member's clock starts at its selecting row, so the freeze date
  moves no verdict date. It buys breadth, twin samples for the false-confirmation rate, and visibility.
- **The one time lever in it:** re-asking Decision 2 B of `FORWARD_COHORT_OFF_POOL_V0.1.md`, which would
  let a cohort confirmation reach the LIVE door without a second pool clock of about three to four months.
  The proposal ties that re-ask to a fixed close of cohort 1 (N4), so it is not re-read whenever
  someone looks.
- **Decisions asked:** N1, whether a second freeze is outside review D3. N2, the freeze cadence. N3, summing
  K across cohorts per context. N4, a fixed close for cohort 1.
- **This PR changes:** the proposal, this entry, and `STATUS.md`. No code, and nothing is frozen.
- **A correction made in conversation:** RESEARCH_EPOCH Q4 was cited as the place that decides when the
  next cohort is frozen. It is not. Q4 asks whether the judgement fingerprint goes on the freeze record,
  and `forward_cohort.build_cohort_record` already stamps it. Nothing decided a cadence until this
  proposal's N2.
