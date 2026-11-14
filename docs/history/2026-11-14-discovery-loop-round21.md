# 2026-11-14 — discovery loop round 21: the rest of the change shape

**Shipped (registration and tools; nothing run in this commit):** `scripts/research/signal_lab.py` (four more
entries in `CHANGE_OF`, `hold_spot`, the random variables from 56 to 64), `scripts/research/strategy_ladder.py`
(`SWEEP_NULLS` from 300 to 400, `SWEEP_NULLS_ROUND_6`), `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §35.

**Why now.** Round 19 could add three variables and listed five more as next. The rounds go on back to back.

**Choices that are not obvious.**

- *The five were checked against closed readings before being raised.* Round 19 had given the cap as the only
  reason they were out. One is struck: the change of illiquidity is the change of volume (already registered as a
  change) and of the size of moves (the closed volatility cluster). Four are raised, each with its kin written
  down, and with what a lead may not become (momentum under another window, the spot-share book again).
- *The cap is lifted by addition.* The sweep's null goes from 300 to 400 books. The 300 that round 6 read are among
  the 400 by name and are the same random variables; a test pins both, so round 6's reading still reproduces.
- *The random variables grow again with the registry*, and the books that read them pick by name, so their picks
  do not move; the round-five re-check is in the order to show it.
- *A light loader for the spot group was built before the run*, not after a kill: round 18's first run died
  because a one-group map loaded every attached series.
- *Only the four new variables' cells are read.* The lead line has moved with the larger random set and with the
  breadth cells; re-reading old variables against a new line would be reading them twice.
