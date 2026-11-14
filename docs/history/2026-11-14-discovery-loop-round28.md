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

**Result (2026-11-14 17:38Z).** No lead among the three: the largest |t| is 2.61 against a line of 4.96. The 7- and
14-day readings do not carry their ranks from week to week (persistence about 0 and 0.35), so the lead rule would
have dropped them anyway. The spread is finished, and so is the variable side of round 26's table: 25 variables
added since round 18, no lead.

**The last research item on the table was answered without a run (17:50Z).** §7 says the hypothesis channel is to
be tested on past data "without waiting for the deploy". The channel was deployed on 2026-11-13 and real fires have
screened real proposals since, so where proposals fall is in those fires' refusal reasons. No read prints that
tally, and the doors §7 names do not all exist under those names (there is no refusal called a cost door). A
past-data run of hand-written proposals answers a different question, so none is registered; a read-only tally tool
is proposed apart from this branch.

**The table was read once more before any "nothing left" sentence, and three rows were wrong or missing.** The
premium's other lookbacks had been struck as "read by registered screens, and funding's kin": the kin ground fell
with round 26's own rule (0.72 is under the line), and neither screen's signal is a cross-sectional 30-day premium.
The sweep of round 6 and the per-variable axis check of round 4 were scoped to "the 27 registered variables" and
have not been run on what was added since; nothing written strikes them. All three are owed, in that order.
