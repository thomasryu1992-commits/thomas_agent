# A bred row's holdout no longer confirms: `holdout_reused` caps it at UNDERPOWERED

- **Decision:** Thomas 2026-09-30, `SELECTION_MULTIPLICITY_AND_HOLDOUT_REUSE_V0.1.md` D3 B and D4.
  It is a bug fix under review D3 (evidence validity, not new machinery).
- **Why:** a parent may breed only after its holdout passes `factory.holdout_permits_parenting`, and
  the holdout is the tail of a window that rolls one day a day. So a crossover child is re-measured
  on nearly the bars its parent was chosen for passing. Measured 2026-09-30 over 47 current-basis
  children, the share with a positive holdout was:
  - 94% within a week of the parent;
  - 71% at 7–30 days;
  - 33% at 30–90 days.

  In forward rows the same children match coin flips of their direction.
- **What changed:**
  - `candidate_ranking.holdout_reused(record)` is true for any row citing `parent_candidate_ids`. It
    is keyed on the citation rather than the derivation name, so a future bred type is covered.
  - `candidate_quality` caps such a row's CONFIRMED at UNDERPOWERED and reports `holdout_reused`.
    Every door reads that status: ROBUST, the observation entry bar, the promotion backlog, the LIVE
    door, trial graduation and cohort eligibility. So no door reads a reused holdout as CONFIRMED.
  - A reused tail that is CONTRADICTED, UNDERPOWERED or INSUFFICIENT reads as before: the cap only
    withholds.
- **Effect on the live store (read-only):** current-basis holdout CONFIRMED 8 → 6 (the two crossover
  children, `cand_a0ce97302e77bae1622f` and `cand_4b1a32f15e739d324abe`, now read UNDERPOWERED), and
  ROBUST 8 → 6. No occupying pool entry held a CONFIRMED holdout, so the pool is unaffected.
- **Deliberately not done:**
  - Parenting itself still reads the holdout (a reused child can still parent). Taking the holdout
    out of the search is D3 A, the validation slice, held for the epoch boundary (`REMAINING_WORK.md`
    §L E3).
  - No funnel line counts reused rows apart: new display machinery is paused under review D3.
  - No schema change: the flag is derived at read time from fields the record already carries.
