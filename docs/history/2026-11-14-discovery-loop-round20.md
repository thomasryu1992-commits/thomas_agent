# 2026-11-14 — discovery loop round 20: one new scenario axis, market breadth

**Shipped (registration and tools; nothing run in this commit):** `scripts/research/signal_lab.py` (the axis
`breadth` in `scenario_labels`, an `AXES` tuple the cells and both checks list from, `breadth` in `RESTING_AXES`),
`docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §34.

**Why now.** The last of the monthly checks on the data in hand (§3.2, "a new axis, one a month"), after round 19.

**Choices that are not obvious.**

- *The axis was not picked.* It is the first entry of §3.2's own list that had not been read.
- *Born at rest.* The five earlier axes were put to rest because none beat its shuffled-spell placebos. A new axis
  gets the same rule from its first day: it is mapped and shown, and no cell of it can be a lead until its own
  check says it separates more than its placebos' 95th percentile.
- *The check reads the variables that need bars and funding only.* Loading every attached series at once no
  longer fits the sandbox (round 18). The registration says which variables are in, and that the other five axes'
  rows in the same output are not a re-judgement of them.
- *Adding an axis adds cells to every later map*, the random variables' included, so the lead line can move. The
  round-five re-check guards the registered books.
