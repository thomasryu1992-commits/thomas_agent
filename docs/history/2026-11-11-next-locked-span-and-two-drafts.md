# 2026-11-11 — a reserved span for the next search, and two drafts that wait on Thomas

`scripts/research/basket_search.py` (`RESERVED_FROM`, `check_search_span`), `metrics_basket_search.py`, and three
proposals: `NEXT_LOCKED_SPAN_V0.1`, `SCREEN_STAGE2_V0.1`, `STRESSED_COIN_COST_V0.1`. Thomas asked for all four
follow-ups of the M04 work to be prepared for review ("전부 진행하고 검토해보자"); the fourth is its own PR.

**Why the boundary is code and not a sentence.** The locked span 2025-01..2026-09 was spent on M04. A promise in a
document that later days stay unread cannot be checked; a search tool that refuses a span reaching 2026-10-01 can.
The check sits before any archive is read, and a test calls the tool with a span edited to reach further. It does
not sit in `cut()`: the forward screens read those days by their own registrations and use the same function.

**Why the stage-2 draft says it reopens a decision.** `STRATEGY_SCREEN_GATE` Q6 (2026-11-09) put the confirm rule
after a screen passes. The draft asks to move it before the first verdict because five strategies now wait on a
screen that half of all null strategies pass; it carries no computed family-wise figure because the strategies
overlap. It is a DRAFT and Q6 stands until Thomas answers.

**Why the cost draft fetches nothing.** `bookDepth` exists in the public archive (listings probed for PIPPIN and BTC
only; two files read in memory and not kept: one `bookDepth` day and one `aggTrades` day of about 100 MB), but it is a new file kind and the tool's client name carries the project. Both are
Thomas's to answer first. The draft fixes what may be printed: depth and impact at stated sizes, never a
cost-adjusted return of the spent span.
