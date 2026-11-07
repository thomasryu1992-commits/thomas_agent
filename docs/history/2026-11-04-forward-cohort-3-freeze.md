# The third forward cohort was frozen six days late: 48 lineages and their twins, closing 2027-05-03

- **What happened:** `FORWARD_COHORT_EXPANSION_V0.1.md` N2 sets a hand freeze every 28 days. After cohort 2
  (2026-10-01), the next round was due 2026-10-29. It did not run, and nothing recorded the miss. Nothing
  schedules these dates. A strategy review on 2026-11-04 found only two cohorts in `forward_cohort report`.
- **Dry run (2026-11-04):** `freeze` without `--apply` listed 48 members over 11 contexts, with a largest
  context of K = 13.
  - INVALID evidence cannot enter. `eligible_members` passes each row through `assert_observation_entry_bar`,
    which refuses `evidence_invalid`.
  - The five `oi_squeeze_*` members were selected on 2026-10-21, after `OI_FIX_EFFECTIVE_AT`
    (2026-10-20T13:59:48Z).
  - The oldest 1h selecting row is 2026-10-03, well inside the ~200-day walk limit (`MAX_WALK_BARS`).
- **Applied by Thomas, in his terminal:**
  - `freeze --apply` → `FROZEN fwd_cohort_55437022b9ba4af1cf45 at 2026-11-04T03:44:33Z`, 48 members.
  - `freeze-nulls --apply` → 48 twins, rate rule `parent backtest closed_count / (bars_replayed x
    symbols_replayed)`.
- **Its close:** freeze + 180 days, 2027-05-03T03:44:33Z. Under `RESEARCH_EPOCH_V0.1.md` (boundary at each
  cohort's close), that is also an epoch boundary.
- **What the delay cost:** no verdict date moves, because each member's clock starts at its selecting row,
  not at the freeze. Lineages that became eligible between 10-29 and 11-04 join this cohort instead of the
  next one.
- **Next round:** 28 days from this freeze, 2026-12-02. Cohort 4 then closes 180 days after its own freeze.
- **This PR changes:** this entry only. No code, schedule or state changed in the repository. The freeze
  itself is per-machine state.
