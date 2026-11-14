# 2026-11-14 — discovery loop round 22: the remaining scenario axes, checked at stricter levels

**Shipped (registration and tools; nothing run in this commit):** `scripts/research/signal_lab.py` (four readers
and a calendar label in `scenario_labels`, `CANDIDATE_AXES`, `AXIS_ORDER`, `axis_level`, the level and p in
`axis_check`'s rows), `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §36.

**Why now.** After breadth, §3.2's list holds six more axes; five can be read from bars alone. The rounds go on.

**Choices that are not obvious.**

- *Checked, not mapped.* Breadth was born at rest with its cells on the map. Doing that for every axis grows the
  map and the random variables' cells with each one and moves the lead line. A candidate axis is labelled and
  checked beside its placebos; only one that passes is moved onto the map, in a later round.
- *The k-th axis is held to 0.05 / k.* Six checks at 5% each wake one by chance about one time in four. The order
  is the order of checking (breadth first), and a test pins that 200 placebos can resolve the strictest level.
- *Five in one round.* They share one run and one rule; registering them together fixes all five definitions
  before any is seen.
- *Leverage is left for a run that loads the positioning files*: it has no label before 2022 and needs another
  loader.
- *The round-five re-check does not see scenario labels*, so the first round's switching book is re-run too and
  compared with the value its code gave before this change.
- *Each label was pinned against lookahead by a case built for it.* A twist that makes the reference coin fall
  after the day cannot show a drawdown reading the future; one that makes it soar from a high does, and the first
  version of the test missed that mutation.
