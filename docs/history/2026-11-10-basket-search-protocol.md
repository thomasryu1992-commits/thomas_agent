# 2026-11-10 — Basket search protocol: ten development attempts, none clears the floor, the locked span stays unread

**What.** Thomas asked to keep improving and re-running until something succeeds. Re-tuning on history that has
already been read produces a pass by chance, so the work was given a protocol first
(`docs/proposals/BASKET_SEARCH_PROTOCOL_V0.1.md`): iterate freely on 2020–2024, confirm the first candidate that
clears annual Sharpe 1.0 once on a locked 2025-01..2026-09 span, at most ten attempts.

Added:
- `scripts/research/basket_search.py` (`list`, `explore`, `confirm`).
- `tests/test_research_basket_search.py`.

**Why the cut is in code.** `explore` removes every bar and settlement after the development span before anything
is computed, and a test replaces the later bars with nonsense and expects the same output. A rule that lives only in
a document would not survive ten iterations.

**Why returns and funding cannot be ranked.** `S-REV`, `S-MOM7`, `S-MOM30` and `S-CARRY` were measured through
2026-10, so the locked span is not unseen for them and their passing it would say nothing new. That removes
momentum, the one consistent result so far, from this search; it has its own forward screen.

**Why the first pass would have been final.** Choosing the best of ten adds a second layer of selection. The first
candidate over the floor stops the search.

**What happened.** The protocol commit (`947fa8cb`) was pushed before the first run. Each candidate was committed
before its run and its result was recorded and committed before the next was written. All ten missed: 0.03 to
0.72. The two highest (illiquidity, and illiquidity with low idiosyncratic volatility) fell to about 0.1 when only
the lookback changed from 30 to 90 days, which reads as a few months of 2021–2022 rather than a premium. The search
stopped at the budget; `FINAL` is empty and the locked span was never read.

**Why the default is not to raise the budget.** More draws from the same feature list make a chance pass more
likely, not a real one. If the search continues, the thing to change is the search space.
