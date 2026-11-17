# 2026-11-17 — discovery loop round 54: the top-30 sweep family's cap is 60, and the one book that waited for a slot

**Shipped:** `scripts/research/strategy_ladder.py` (`sweep_reading` compares the exact p), a test in
`tests/test_research_strategy_ladder.py`, `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §72 (registration) and
an addition to §71.5.

**Why.** Round 51 left one row: the top-30 sweep reading compared Benjamini–Hochberg thresholds against p rounded to
four places, so 1/601 printed as 0.0017 and the family stopped at 58 books, while the test's inequality (exact p)
allows 60. The other two tiers' readings never rounded.

**Choices that are not obvious.**

- *Fix the compare, not the null.* Recounting the six recorded top-30 readings with exact fractions gives the same
  survivors every time, so the rounding never moved a verdict. A larger null would re-rank the recorded survivor
  under BH even if the first 600 books reproduced, so it is not done.
- *The order stays on the rounded p.* Distinct p values keep their order after rounding while the null has fewer than
  9,999 books, so sorting on the exact p is an equivalent change. A mutation of the sort survives the tests for that
  reason; the compare's mutation is killed.
- *One book, once.* `spot_taker_share_position_30` was left out of the top-30 sweep only for the slot (round 51's
  rule). Round 51 closed the shape's map cells and its other books; this book was never built, so reading it is not a
  re-measure. Nothing else waits: spot trade size's shapes were closed by a sentence in §68.5.
