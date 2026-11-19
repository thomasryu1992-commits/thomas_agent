# 2026-11-19 — discovery loop round 70: the price-delay lead beside the struck books

**Shipped:** `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §88 (registration), a row in
`HYPOTHESIS_FORM_AND_RESEARCH_MAP_V0.1.md`, the status line, and code: `strategy_ladder.HOURLY_LEADS`,
`hourly_overlap_books`, `hourly_lead_overlap` and the `hourly-lead-overlap` command, with a test.

**Why.** Round 69's map lead (price delay, low side, both upper tiers) overlapped the struck volatility cluster on
inputs (0.546 with idiosyncratic volatility on the top tier). §87.4 sends a map lead to the check round first: if
its book re-builds a struck book it closes by rule, before any question to Thomas.

**Choices that are not obvious.**

- *Each tier's own sweep book.* The top tier uses its sweep's L9T over the month's top 30, ranks 31–100 the tier
  sweep's L22; the struck books are built high, the lead on its cell's side, and the size of the median cosine is
  read, so the sign of the comparison does not matter.
- *Which books.* On the top tier the nine struck on every tier; on ranks 31–100 the tier sweep's list together
  with the map's strikes there, less the groups this market does not load (positioning, depth), as §58 did.
- *No return is read*; the weights come from inputs only.
