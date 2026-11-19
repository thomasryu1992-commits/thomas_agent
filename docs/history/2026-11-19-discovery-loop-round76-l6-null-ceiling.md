# 2026-11-19 — discovery loop round 76: L6's ceiling on the recorded null books first

**Shipped:** `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §94 (a correction of §93.6 and a registration), a row in
`HYPOTHESIS_FORM_AND_RESEARCH_MAP_V0.1.md`, the status line, and code in `strategy_ladder`: `l6_book`,
`l6_hedge_door`, `l6_chunk`, `l6_reading` and the `l6-null` / `l6-reading` commands, with tests.

**Why.** Round 75 left the 2026-12-17 restructure open, and §93.6 named the next row: L6's ceiling count (the long
side alone, with the market hedged in Bitcoin). §93.6 said the count could be done over the recorded sweep files.

**Choices that are not obvious.**

- *§93.6 is corrected, not followed.* The recorded files hold only whole-book legs, so a real book's L6 ceiling means
  building L6 on real variables. That is a zero-cost L6 sweep with no judgement, which is Thomas's decision, as §92.4
  was.
- *The null comes first.* A two-sided random book earns about nothing before cost, which is what made a 15% ceiling
  meaningful. A one-sided book with a Bitcoin leg carries the tier's alt-versus-Bitcoin drift. So the bar a real book
  would have to clear is fixed from the recorded null books before any question: max(15%, the null's 95th
  percentile). A tier whose 95th percentile reaches 30% is not asked about at all.
- *H-F1's leg, reused.* The leg is Bitcoin sold against each long's weight times its 60-day beta (`bs._beta`). A coin
  with no beta is hedged at 1 and counted; a door stops the run when that share passes 5%.
- *Zero cost, run twice per book.* L22 is split by side and L6 by alts and leg. Two doors prove the split: on the first
  five books, both sides add up to the stressed book's net plus cost, and on every book the alts equal the long side.
