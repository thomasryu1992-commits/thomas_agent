# One key, one open cohort: later freezes skip keys an open cohort holds, and sealed siblings are marked

- **The decision it implements:** `FORWARD_COHORT_SIBLING_RULE_V0.1.md` S1–S4, Thomas 2026-10-01, as recommended.
- **What changed in `crypto/forward_cohort.py`** (and `tunables`, which now registers `COHORT_LIFETIME_DAYS` as OPERATOR):
  - New constant `COHORT_LIFETIME_DAYS = 180`. This is N4's close, the first time it appears in code.
  - New `open_cohort_lineages(cohorts, now=)`. It returns the (family, symbol scope, timeframe) keys of every
    cohort frozen less than 180 days ago. A freeze time that does not parse keeps its keys held.
  - `eligible_members(..., exclude_lineages=)` skips those keys, and `freeze_cohort` passes them. A freeze offered
    only siblings is therefore empty (`FORWARD_COHORT_EMPTY`), and under N2 that round is skipped.
  - The cohort record stamps `observation_entry_bar.v2`, with
    `"one_member_per": "... across every open cohort"` and `cohort_lifetime_days`. v1 records stay sealed and are
    not reinterpreted.
  - `sibling_of(cohorts)` marks a member of a later cohort that shares a key with an earlier cohort's member.
    `cohort_report` carries it per member, and `board_summary` counts it.
- **Where the marks show:**
  - the board line reads `형제 N`, and is silent at zero;
  - `scripts/forward_cohort report` appends `sibling of <id>`.

  Display only: no judgement, door or K reads it (D3-1).
- **What it does to the live store:** nothing on deploy. Cohort 1 and cohort 2 are sealed, and cohort 2's 53
  siblings keep walking and are now marked. The rule first acts at the 10-29 freeze. Frozen on 10-01, that freeze
  would have held 41 lineages, all siblings; under the rule it would hold none.
- **Not done here:** a hierarchical judgement that counts a key cluster as one unit. No such judgement exists
  yet. The mark is what it will read.
