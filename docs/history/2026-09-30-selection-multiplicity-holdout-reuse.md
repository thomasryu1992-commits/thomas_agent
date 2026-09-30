# Two structural weaknesses in strategy selection, measured and designed around (proposal)

- **What was measured** (read-only, live store, 2026-09-30):
  - **The LIVE door's holdout path** is a single-test bar. The 4h pooled context has 1,756 attempts,
    so its corrected bar (`selection_adjusted_z`) is z 4.19. 0 of the 8 holdout-CONFIRMED lineages
    clear it (max 4.06). That correction is used only in the sort key, never in a door.
  - **Fusion parenting reads the holdout** (`holdout_permits_parenting`). Crossover children's
    holdout advantage decays with their distance from the parent's selection: < 7 days 94% positive,
    7–30 days 71%, 30–90 days 33%. That is the signature of holdout reuse.
  - **In forward rows, selected entries do not beat same-direction coin flips.** Seeded entries are
    below them in both directions.
- **What was delivered:** `docs/proposals/SELECTION_MULTIPLICITY_AND_HOLDOUT_REUSE_V0.1.md`
  (DRAFT), with options for each weakness and four decisions (D1–D4):
  - Recommended for the LIVE door: FORWARD_CONFIRMED required, with the forward bar corrected by
    `observed_lineages`.
  - Recommended for parenting: mark reused holdouts now, and a validation slice at the epoch
    boundary.
- **Deliberately not done:** no code. Centring keeps its holdout check, because the 2026-08-05 hazard
  it fixed is measured and it shows no leak.
