# 2026-11-17 — discovery loop round 51: the spot taker-buy share's shapes

**Shipped (registration and tool; nothing read in this commit):** `scripts/research/signal_lab.py` (the two shapes
join `OVERLAP_CANDIDATES` beside the same shapes of the perp's taker-buy share and the spot share),
`scripts/research/strategy_ladder.py` (`pick_names` and `--names`: the three sweep commands build only the named
books), tests, `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §69.

**Why.** Section 67 named the level's change and position as the next list before the level's result was seen;
round 50 closed the other spot flow lead and left these.

**Choices that are not obvious.**

- *One slot on the top tier.* The top tier's reading rounds p to four places, so its smallest p (1/601) reads 0.0017.
  The family's first threshold is 0.10 ÷ books: open at 58, shut at 59, where the recorded lone survivor would stop
  surviving. The first shape, in section 3.1's order, that passes the overlap takes the slot, fixed before the
  overlap runs. The other is never built there; `--names` makes that a property of the command, not a promise.
- *The reading function is not changed in a round that would gain from it.* Whether the rounding or the test's
  pinned 60 is the cap, or the null books grow by name, is left as the next list's row.
- *A shape's lead passes through a weight overlap before anything else.* Round 50 showed a value overlap under 0.8
  can still be the same book at 0.5 in weights.

## Result (2026-11-17 02:41Z)

Both shapes passed the overlap (0.32 and 0.23) and were registered (59 variables, 114 controls). The position shape's
31–100 coverage printed 0.8 at three places; the counts now print the denominator, and the exact share is 0.8003, so
both were read there. No map lead on any tier, no sweep survivor; the recorded survivors stand as the registration
computed. The two shapes are closed as read, nothing (§69.7).

Next: the design `docs/proposals/SEARCH_THEN_PRUNE_V0.1.md` (DRAFT), from Thomas's word the same day — a combination
logic (L8) whose components are chosen on the years before each scored year, filtered first by running the same
search end to end on the control variables 200 times. The real search waits for its five questions.
