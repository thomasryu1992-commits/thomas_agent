# 2026-11-14 — discovery loop round 32: which leg earns the lead's return

**Shipped (registration and tools; nothing run in this commit):** `scripts/research/strategy_ladder.py`
(`simulate_held` takes `leg_of` and then adds each day's net split by leg; `lead_attribution` and the `lead-legs`
command; `lead_check` prints the tie shares per decision), `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §46.

**Why.** Round 31 expected the coins tied at zero to carry little of the book. They carry 21% at the median
decision and 95% at the largest, ordered by name. Before a hypothesis form can say anything about spreads, the
book's own return has to be split between the coins the variable ordered, the coins the alphabet ordered, and the
hedge.

**Choices that are not obvious.**

- *This step was added after a result, and the registration says so.* It is not a variant: the book, the
  accounting and the span are the swept book's, and a leg table is what earlier hypothesis results already carry.
- *The split lives in the accounting, off by default.* `simulate_held` gains an optional `leg_of`; without it the
  rows are identical, which a test asserts and the round 5 and round 1 re-checks confirm on real data. A coin's leg
  is fixed at the decision its position is taken; a trade is charged to the coin's new leg, or to the leg it
  leaves.
- *Two gates before the rule.* The legs must sum to the book's net every day, and the run must reproduce the
  sweep's Sharpe and return. Otherwise nothing is read.
- *One rule.* The untied coins must earn at least half of the book's net, or the lead is closed and named for the
  leg that earned it. The hedge coin is one position (its rank weight and the hedge together) and is kept whole.
- *The form's door is quoted, not softened*: lines 1 to 3 must be filled or the idea does not go on the ladder
  without asking Thomas, and no payer is invented.

**Result (2026-11-14 20:12Z).** Both gates pass (the legs sum to the book to 1e-16; the run reproduces 1.667 and
34.4%). The untied coins earn 61% of the five-year net, so the rule does not close the lead.

- *By year the picture is weaker than the total.* The variable's own leg earns in four years and loses 32.1% of
  total position in 2024; that year's +15.9% for the book is the name-ordered coins (+33.5%) and the hedge
  (+14.4%). So the 2024 pass in round 31's later-years check was not the variable's.
- *The name-ordered leg flips sign from year to year* at the same order of magnitude as the variable's leg.
- *The form's door is where this stops for now.* No check has shown who pays, and what the variable measures is
  not known. The form's rule says such an idea goes on the ladder only by asking Thomas.
