# The cohort expansion is decided: a second freeze now, every 28 days after, K summed across cohorts, each cohort closed at 180 days

- **The decision (Thomas 2026-10-01):** N1–N4 of `docs/proposals/FORWARD_COHORT_EXPANSION_V0.1.md`, each as
  recommended.
  - N1: a second freeze is outside review D3. It uses the existing commands, every member is already minted,
    and cohort evidence stays a screen (Decision 2 A).
  - N2: an operator freezes a new cohort every 28 days, by hand. A round with no eligible lineage is skipped.
    The walk replays from each member's selecting row but fetches at most 4,999 bars, so on 1h a freeze must
    come before a selecting row is about 200 days old.
  - N3: when Decision 2 B is re-asked, K per context is the sum over every frozen cohort in that context.
  - N4: each cohort is judged once and closed at freeze + 180 days. For cohort 1 that is 2027-03-22.
    Decision 2 B is re-asked against that close and the twins' confirmation rate, not against a
    mid-course reading.
- **What this PR changes:** the proposal's status line (DRAFT to DECIDED), a decision block in §5, and
  `STATUS.md` regenerated. No code.
- **Left to do:** run the second freeze, an operator command (`docker exec thomas-scheduler python -m
  scripts.forward_cohort freeze --apply`, then `freeze-nulls --apply`). The 28-day cadence and the closes
  live in no schedule and no code, so this record and the proposal are where they are kept.
