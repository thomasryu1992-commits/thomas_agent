# 2026-11-15 — discovery loop round 38: a t for the spread in every map cell

**Shipped (registration and tool; nothing run in this commit):** `scripts/research/signal_lab.py` (`spread_t` in
every cell, the random variables' largest |spread t| in `chance`, `spread_candidates`, `SPREAD_NO_BOOK`, `no_book`),
tests, `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §54.4 and §55.

**Why.** The map's lead rule reads the t of the rank correlation. A rank book earns the mean. On ranks 31–100 the
two disagree in sign in 15 of the 17 strong weekly cells, and the map printed the spread's size without a t or a
line from the random variables, so nothing could say whether that disagreement means anything.

**Choices that are not obvious.**

- *§54.2 said more than the files show, and is corrected first.* The 15 cells are five of the closed volatility
  family, five of the momentum family and five others, all with a negative rank correlation and a positive mean
  spread. A mean's sign without its t does not say which way a book earns.
- *The change is additive.* Old values are untouched, and each group's run must reproduce its latest file to the
  digit before anything new is read. Two groups only have the first map's file, from before axes were added, so
  they are gated on all-weeks cells alone.
- *The second rule is the first rule moved onto the spread*: above the random variables' largest, a persistent
  variable, the same sign as its own all-weeks cell, not on a resting axis. It does not require the rank
  correlation to agree, because the cells this round is about are the ones where it does not. Every cell that meets
  it is listed; the first rule's cap of three once hid a cell for a day.
- *This is a second rule on cells already seen*, and the registration says so: the spreads were printed in every
  run. Two rules on the same cells roughly double the room for a chance lead.
- *No book, no side.* Variables a record forbids on a tier are named in code and give no book whatever the cell
  says, including the two closed today. A lead goes to the form's door; a book the sweeps already read is laid
  beside it and not rebuilt.
- *One line for future sweeps*: a swept book is called a lead only after rung 2(b) on the sides its earlier years
  name. No code while no sweep is pending.

## Result: no cell meets the second rule

Eight sandbox runs at `470d3bed`, one per group; `~/research/signal-lab/run14-<group>.json` with a gate file each.

- *All eight gates passed*: every older value of every compared cell is what it was, and the first rule's counts
  are unchanged.
- *The spread's line from the random variables* is 3.39 at seven days and 3.95 at twenty-eight. The largest real
  value anywhere is 3.36 and 3.38. No cell meets the rule, forbidden variable or not.
- *The fifteen cells behind §54.2* have a spread t between +0.13 and +1.60. The sign disagreement was noise in the
  mean: on ranks 31–100 the ranks lean one way and the mean difference does not stand apart from zero. That fits
  what happened when a strong rank cell there was built into a book (H-R2: about zero before costs).
- *What stays*: the map prints both statistics for every cell and reads both rules for any variable added later.
