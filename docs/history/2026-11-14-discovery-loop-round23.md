# 2026-11-14 — discovery loop round 23: the three remaining shapes, settled in one registration

**Shipped (registration and tools; nothing run in this commit):** `scripts/research/signal_lab.py` (`_position`,
`POSITION_OF`, `_residual_momentum`, nine variables, 84 random variables, 6,000 by-variable placebo axes),
`scripts/research/strategy_ladder.py` (`SWEEP_NULLS` 500), `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §37,
three rows in the research map for the tool rounds 18 to 22.

**Why now.** The change shape is finished; §3.1 names three more. Settling them together shows how short the list
really is before any code is run.

**Choices that are not obvious.**

- *"The residual after the market's part" is empty on a map of ranks.* Taking one number off every coin on a day
  moves no rank, so those cells would be the level's cells again. Only a per-coin loading changes ranks, and the
  registry holds one: beta. So the shape yields one variable, beta-adjusted momentum.
- *"Other lookbacks" leaves no survivor.* Every registered variable has a closed or forward-registered reading
  whose closure names its window, except trade size, whose ranks persist at 0.98 from week to week: other windows
  of it would be the same lead read three more times.
- *"Position against the coin's own history" is real*, because it is centred coin by coin. The history is fixed
  at the six earlier lookbacks. Eight levels carry it after the same strikes as before.
- *Two more registered constants had to grow, by addition.* The sweep's null (400 to 500) and the by-variable
  axis check's placebos (5,000 to 6,000): a test already pinned that 5,000 could not correct for 516 tests. In
  both, the earlier draws are the same draws.
- *The tool rounds get rows in the research map*, so a later session does not read these cells again.
- *This closes §3.1's list.* The registration says no shape is raised again under another history length or
  window, whatever the result.
