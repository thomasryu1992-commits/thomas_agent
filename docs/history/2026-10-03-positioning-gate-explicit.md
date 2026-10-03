# The positioning families mint on a decision, not on coverage alone

- **The decision it implements:** `CRYPTO_ARCHIVE_BACKFILL_V0.1.md` R2, Thomas 2026-10-03. Pin the positioning
  gate to an explicit decision before backfilling, and mint only after 2027-03-22.
- **The defect it closes before it can bite:** the scheduler's factory fire passed
  `positioning_store.coverage_summary(...)["eligible"]` straight to the factory as `positioning_eligible`. The
  day the store covered the replay window, the positioning families would mint on the next fire, with nobody
  deciding. That was harmless while the store could only grow a day at a time (2029). A backfill can fill it in
  an afternoon, and minting a new family is research that review D3 holds until 2027-03-22.
- **What changed:**
  - `positioning_store.MINTING_DECIDED = False`.
  - `positioning_store.mint_eligible(root, symbols=)` is coverage AND the flag. While the flag is down it does
    not read the store, so neither a full store nor a damaged one can open the gate.
  - `scheduler`'s factory fire calls `mint_eligible`.
  - The board's positioning line reads `커버 충족·생성 닫힘(결정 전)` when the store is covered but the decision
    is not made.
  - The comments in `template_space` and `data_review` that said "the data decides" are updated.
- **What did not change:** coverage measurement and display, the positioning columns on feature rows, and every
  other family. With today's store (95 days) nothing behaves differently; the gate was already closed by
  coverage.
- **Tests:**
  - closed by default however full the store is;
  - an undecided gate does not read the store;
  - a decided gate still needs coverage;
  - a factory fire over a covered store hands the factory `positioning_eligible=False`;
  - the board line.
- **Mutation check:** reverting the scheduler to coverage alone fails one test, and ignoring the flag fails three.
