# The LIVE door reads the forward stream only, at the bar its observation cohort sets

- **Decision:** Thomas 2026-09-30, `SELECTION_MULTIPLICITY_AND_HOLDOUT_REUSE_V0.1.md` D1 C and D2.
- **Why:**
  - A holdout CONFIRMED used to arm LIVE on its own. It was a single-test bar (z 1.96 plus the
    period test) in a context of about 1,756 attempts (4h pooled). At the Bonferroni bar that count
    sets (4.19), none of the store's 8 CONFIRMED holdouts cleared.
  - The holdout is also partly spent by the search that breeds from it (D3; `holdout_reused`, #1043).
  - The lineage's own forward stream is the only evidence neither the search nor the breeding
    touched.
- **What changed:**
  - `forward_confirmation.assert_live_tier_confirmed` no longer passes a holdout CONFIRMED. The
    holdout status stays in the refusal as context.
  - It judges forward at `robustness.selection_adjusted_z(observed_lineages)` in place of 1.96, for
    example 2.94 over the pool's 15 lineages. The first of N forward records to clear is the best of
    N tries.
  - `judge_forward` takes that bar as `z`. Its default is 1.96, so the cohort, the null arms and the
    board judge as before.
  - A higher bar only moves a record from CONFIRMED toward UNDERPOWERED.
  - The refusal no longer points at the `--allow-unconfirmed-holdout` escape, which the LIVE tier
    retired in PR1c.
- **Tests:**
  - A confirmed holdout without forward evidence is refused.
  - A forward record between the two bars (t ≈ 2.33) is FORWARD_CONFIRMED to the plain judge,
    refused at a cohort of 15, and admitted at a cohort of 1.
  - The promotion and artifact fixtures that armed LIVE on a stored holdout label now write each
    seeded lineage a real forward record.
- **Effect today:** none; nothing is armed and the stage is PAPER. The earliest LIVE arming is
  months out: forward floors of 25/10 trades plus 8 slices of 14 days.
