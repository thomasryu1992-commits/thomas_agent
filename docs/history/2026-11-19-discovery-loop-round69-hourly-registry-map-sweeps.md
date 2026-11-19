# 2026-11-19 — discovery loop round 69: the two hourly variables enter the registry, the map and the sweeps

**Shipped:** `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §87 (registration), a row in
`HYPOTHESIS_FORM_AND_RESEARCH_MAP_V0.1.md`, the status line, and code: the registry's `hourly` group,
`signal_lab.attach_hourly` / `hold_hourly`, 124 random variables with `chance_before_hourly`, the lower tier's
`no_book` entries, one axis-family exclusion, `strategy_ladder.SWEEP_ALONE["hourly"]`, `search_prune.AFTER_L24`.

**Why.** Both hourly variables passed round 68's input-only doors on both upper tiers.

**Choices that are not obvious.**

- *Loaded or blocked.* Every map and sweep opens the group through `hold_hourly`, which builds the values at every
  weekly rebalance day for the month's top 100 and returns nothing when no value was built, so a missing attachment
  cannot read as "too few decisions".
- *Below rank 100 they do not exist.* The hourly bars on disk cover the month's top 100 only; the lower tier strikes
  both by name (`no_book`), which refuses the whole group there.
- *One axis slot, not two.* §85.2 counted room from the recorded axis family (690 tests); the code's guard counts the
  family's possible size (variables × 2 tiers × 6 axes ≤ 720), which leaves room for one. The price delay takes it
  (§85.4's order) and the volume concentration is excluded by name.
- *The whole registry is not mapped at once.* That was already refused because of the spot-flow group; the hourly
  group is mapped alone too.
