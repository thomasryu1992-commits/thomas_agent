# Cross-cohort siblings: one key, one open cohort (proposal)

- **Why now:** the one-member-per-key rule (family, symbol scope, timeframe) holds within a single cohort freeze
  only. `eligible_members` drops earlier cohorts' members by candidate_id, so a same-key lineage with a different
  rule hash enters the next freeze.
  - 53 of cohort 2's 82 members are siblings of cohort 1 members. 40 of those pairs share the entry structure.
    Their selection dates are a median 0 days apart.
  - 3 siblings re-test a key whose cohort 1 member was already FORWARD_CONTRADICTED.
  - Frozen today, the next cohort would hold 41 lineages, every one a sibling, and no new key.
- **What this PR adds:** `docs/proposals/FORWARD_COHORT_SIBLING_RULE_V0.1.md` (DRAFT, S1–S4) and `STATUS.md`
  regenerated. No code. D3-3 (Thomas 2026-10-01) placed the rule outside the research pause.
- **Recommendation:** A + B.
  - A, from the next freeze: a key held by an open cohort cannot be used until that cohort closes (freeze + 180
    days, N4). Key occupancy reads no outcome, so it also blocks re-testing a refuted key.
  - B, for the already sealed cohort 2: the report marks `sibling_of` at read time, and the hierarchical
    judgement counts a key cluster as one unit.
  - Rejected: excluding only refuted keys (C), because it makes membership depend on forward outcomes.
- **Measured after cohort 2's first walk (2026-10-01 07:15 UTC):**
  - Sibling pairs share forward entries at a median 55%, against 13% for same-context, same-direction pairs from
    different families. Their daily R correlates at a median 0.62 against 0.31 (28 of 53 pairs comparable).
  - All 3 siblings that re-test a refuted key run negative, and one turned FORWARD_CONTRADICTED on its first walk.
  - The recommendation stands.
