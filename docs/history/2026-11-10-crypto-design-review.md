# 2026-11-10 — Crypto design review: what the first verdict date can open, and a record corrected

**What.** Thomas asked for the design problems of the crypto futures strategy system and ways past the blocked
places, then said to work through the options in order. `docs/proposals/CRYPTO_DESIGN_REVIEW_V0.1.md` is the record:
what was read from the research lake that day, three findings sorted by kind, the order of work, and three questions.
No code, judgement rule or schedule changes here.

**Why the findings are sorted into three kinds.** Outside plans kept re-asking decided items as if they were defects
(the canary rung, five times). So the review separates real mismatches, consequences of decided trade-offs (stated,
never re-asked) and questions nobody has asked yet. A list of what it does not propose closes the document.

**Why "2027-03-22 is unlikely to open anything" is written down.** Several gates hang on the first
FORWARD_CONFIRMED: D5, LIVE_AUTONOMOUS, P3's crypto condition. The trade floor is not what binds; most members reach
it. The bar is. Under the family rule decided on 2026-10-23, no family's lower bound is above zero today, and the
best-sampled family's half-width only falls to about 0.26R by the boundary. Saying so before the date keeps the
boundary from reading as a surprise.

**The correction.** The held-out replay verdict said non-price information "only accumulates forward". The research
cache holds Binance's daily `metrics` files with positioning ratios back to 2020-09 (BTC) and 2021-12 (five more).
The verdict stands; the premise that the past cannot be measured does not. The runtime store's own start (2026-06-30)
and `MINTING_DECIDED = False` are untouched.

**Why the factory question defaults to "leave it".** The active contexts' holdout bar is already z 4.2–4.35, and
doubling the attempt count adds 0.15. Stopping the factory buys almost nothing, and the monthly cohorts and null
controls keep accruing while it runs. A change of direction waits for a measurement that argues for one.
