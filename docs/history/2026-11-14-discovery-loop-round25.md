# 2026-11-14 — discovery loop round 25: the map's six axes cut finer

**Shipped (registration and tools; nothing run in this commit):** `scripts/research/signal_lab.py` (`_third_split`,
the `*_fine` labels in `scenario_labels`, `finer_gate`, the finer axes in `axis_check` with `FINER_PLACEBOS`),
`docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §39.

**Why this round exists.** Round 24's registration said nothing would be left on the lists. §3.2 has a monthly row,
"cut an axis finer", that no round had run, and Thomas's own words ask for it. This is that row.

**Choices that are not obvious.**

- *Thirds of the same history, not a second reading.* The five median axes are cut where they were already
  measured, so the finer label is a finer cut of the same number: its upper third is always above the median. A test
  holds that.
- *Trend is cut by a spell's age.* §3.2's own example is "the start of a rise against the overheated part". The
  first four weekly decisions of a spell are early. Age is counted over weekly decisions up to the day, so how long
  a spell goes on to last changes no earlier week; a test rewrites the later spell and compares.
- *The gate is in the code.* §3.2 cuts only where every part has 20 decisions in 4 spells. A trend state with a thin
  part goes back whole; an axis with nothing cut is not checked and its counts are printed.
- *400 placebos for these six.* They stand 8th to 13th, and 0.05 / 11 is already under one in 201. The older axes
  keep their 200 and their seeds, so their rows must come out identical to round 22's; the result checks that.
- *A pass would not yet mean the cut added anything.* The placebo is the same spells in another order, which is the
  tool §5 names for finer cuts. It asks whether the finer axis separates, not whether it separates more than its
  parent. The registration says a passing axis meets a within-parent placebo before it is mapped.
