# 2026-11-18 — discovery loop round 57: the per-variable axis check on ranks 101 and after, with the map as its door

**Shipped:** `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §75 (registration), a row in
`HYPOTHESIS_FORM_AND_RESEARCH_MAP_V0.1.md`, the status line, and code: `signal_lab.ic_series` /
`axis_by_variable` take `members=` (tiers given per decision day, the shape `grid` has taken since round 40),
`signal_lab.weekly_cells` / `weekly_cells_differ`, and `strategy_ladder lower-axes --only <group> --check <map>`.

**Why.** §73.4 listed the lower tier's axis check as a cost row (the tool read `TIERS` only) and §73.6 put it after
round 56. Its own family, never mixed with the upper tiers' 690.

**Choices that are not obvious.**

- *56 variables, not §73.4's 59.* The family is the variables the lower-tier map actually read, taken by name from the
  eight recorded map files. The three depth variables were never read there (the tier's depth files are not on disk,
  D11's grounds). The run refuses when its variable set differs from the recorded map's.
- *Struck variables are read.* §47.1 read every registered variable on the upper tiers, struck ones included, and the
  lower map shows struck variables with their reason. A pass on one is recorded only. Because Benjamini – Hochberg
  sets its bar over the whole family, a non-struck pass is also re-read among the non-struck tests alone, and the
  question to Thomas says which case it is.
- *The door is the map, inside every run.* No axis check was ever recorded on this tier, so round 56's door (re-run a
  recorded group to the letter) does not exist here. Each run first rebuilds every one-week cell of its group's
  recorded map from the weekly series it will measure: the decisions per scenario key, and the mean and t of the
  whole span. A single difference ends the run blocked with no counts. That covers the tier's members, each group's
  loader and values, and the top-30 axis labels as computed on that market. Door and measurement then share one lake
  snapshot by construction, so round 56's start window is dropped.
- *The changed function is checked on the upper tiers first.* Every recorded axis check went through `ic_series` and
  `axis_by_variable`; before the lower runs, `signal_lab axes-by-variable --only spot` must give `byvar4/spot.json`'s
  36 count rows again.
- *A broken chain re-runs whole when code changes.* No counts file is opened until all eight exit 0. If a door fails
  and the fix is code, all eight run again on the new commit.

**Result (03:03Z, §75.5).** Door 1 held (36 rows equal) and all eight runs rebuilt their recorded map cells
(1,120 one-week cells) on one snapshot. In the family of 336 no test survives Benjamini – Hochberg; 14 are under 0.05
against 16.8 by chance; the smallest p is 67/7,200 (spot share × funding level). The 240 non-struck tests alone give
the same: none. The lower tier's axis check is closed. With it the per-variable axis check is done on the map's six axes for the
variables each tier's map read; the depth three were tested on ranks 1 – 30 only (no depth files below), and the
thirteen off-map axes were left out by §47.2. The lower tier's own family still has room for the spot taker share's
other windows (354 tests, first bar 0.000282); the two upper tiers' family does not (744 > 720).
