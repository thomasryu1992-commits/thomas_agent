# 2026-11-14 — discovery loop round 28: the spread estimate at the other lookbacks

**Shipped (registration and tools; nothing run in this commit):** `scripts/research/signal_lab.py` (`_spread` takes a
lookback; `SPREAD_LOOKBACKS`; 100 random variables; `PLACEBO_AXES_BY_VARIABLE` 7200),
`scripts/research/strategy_ladder.py` (`SWEEP_NULLS` 600), `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §42.

**Why.** The last variable item on round 26's table. §37.2 struck other lookbacks one variable at a time, each for a
closed reading or because the variable is slow. Neither reason holds for the spread, so its 7-, 14- and 90-day
readings are owed.

**Choices that are not obvious.**

- *The registry passes fifty, so the two capacity numbers go up first.* The sweep's null books and the per-variable
  placebo axes are sized so that a real effect could still pass the correction over every registered variable.
  Neither is run in this round; the tests hold the sizes. The earlier books and axes are the same ones, by seed.
- *No shapes for these.* They are levels, but shapes are made of 30-day levels only. A test names the full set of
  spread and premium variables, so a shape of another window cannot slip in.
