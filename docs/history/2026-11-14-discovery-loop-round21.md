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

**Results (same day).**

- *No lead among the four.* Their largest |t| is 3.60 against a line of 4.30; every one of their largest cells is
  on a resting axis. Of their 256 shown cells 31 are over 2 where the random variables' rate gives about 21: a
  small excess, most of it the change of momentum and of taker share on ranks 31-100, both negative there.
- *The outputs list leads that are not this round's.* The volatility cluster and the trade-size cell were seen in
  earlier rounds; the registration said only the new variables' cells would be read, and the record says so.
- *All four group runs fitted the sandbox*, the spot group through the loader built beforehand, and the
  round-five re-check was unchanged with 64 random variables.
- *The change shape is finished*: every registered level that could carry it now does. Three shapes remain.
