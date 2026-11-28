# 2026-11-18 — discovery loop round 58: the spot taker-buy share at 7, 14 and 90 days, and the cap of 60 moved to where a family is read

**Shipped:** `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §76 (registration), a row in
`HYPOTHESIS_FORM_AND_RESEARCH_MAP_V0.1.md`, the status line, and code: three registered variables
(`spot_taker_share_7/14/90`, `signal_lab._spot_taker_share_over`), 120 random variables with a third earlier-line set
(`CONTROLS_BEFORE_WINDOWS`), `signal_lab.AXIS_FAMILY_OUT`, `strategy_ladder.FAMILY_MAX`, `search_prune.AFTER_L24`.

**Why.** §73.4 found no record and no strike for these windows; §75.5 listed the doors with room.

**Choices that are not obvious.**

- *No overlap gate.* §42 and §43 added other windows without one. Whether a window re-builds the 30-day book is asked
  only if something survives, by book weights, with the measure fixed in §76.5 before any number is seen.
- *The registry passes 60, so the cap moves.* Tests used to keep the registry at or under 60, because 600 null books
  can pass a book that beat every null only in a family of 60 or fewer. Now `tier_family` and `lower_sweep_family`
  refuse a family over `FAMILY_MAX`, and the two upper tiers' per-variable axis check leaves the three windows out
  by name (744 tests would pass what 7,200 placebos reach). The top-30 sweep is not run for them: one slot, three
  windows, and §75.5 put that door under "no room".
- *L24's material is frozen.* `search_prune.material` read the registry live, so three new variables would have
  changed the material of a closed logic. They are struck there by name; the material stays 47 · 38 · 40.
- *The refactor's door is the record.* The 30-day reading now goes through a window parameter; the maps reprint the
  recorded four spot-flow variables' cells, which must equal round 51's files, and the three earlier control lines
  must equal theirs, before any new cell is read.

**Result (03:48Z, §76.6).** All three windows cleared coverage on both tiers (0.837 – 0.840 and 0.956). Both map doors
held (480 recorded cells, three earlier lines). No new cell met either map rule; on the upper tiers 27 of 72 28-day
cells show |t| > 2, all positive and on the side each variable's all-weeks cell leans, none at the line. No new book
survives: 31 – 100's family of 43 keeps its two recorded survivors (smallest new p 0.082, the 90-day), 101+'s family of
41 has none. The three windows are closed. Next: the lower tier's axis check gains them (needs `--names` in
`lower-axes`).
