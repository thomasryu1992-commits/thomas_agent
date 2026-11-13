# 2026-11-13 — discovery loop round 8: two conditions added to rung 2, and the first hypothesis with a reason

**Shipped (registration and tools; nothing measured in this commit):** `scripts/research/strategy_ladder.py`
(`later_years`, `beside_nulls`, the listing book `L12` with `ListingPools`, `listing_counts`, `listing_run`, and a
per-coin cost in `simulate_held`), `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §22.

**Why the ladder changes.** Three books that passed on 2020-2024 failed on 2025-2026. The sweep showed that 7.7% of
random-variable books clear the entry rule under the same logic, and the transfer check saw no carry from the
earlier years' Sharpe to the next year's. Rung 2 judged a book by one number over the years it was chosen on. It
now also asks for the three later years, each decided on the years before it, and for a p beside null books of the
same logic. Both are additions: the five-year rule stays, because dropping it could pass a book that fails today,
and loosening is not this loop's to do.

**Why the second condition is weak alone.** Under the tail-out logic 32% of random books meet it. It is kept because
it costs nothing and reads a fitted book honestly; the weight is on the p.

**Why candidates now need a reason.** Ranking variables on the map and building the best into books produced passes
that were not separable from noise. A candidate now enters the ladder only with the hypothesis form filled before
any measurement: who pays, why it lasts, how much, what would refute it, the nearest thing already tried, and a
prior. One hypothesis and one definition a round.

**The first one: decay after listing (H-L1).** Short every perp in its second to ninth week after listing, equal
weights, the same dollars long in Bitcoin, stressed slippage on every leg but Bitcoin's. Its null is the same book
on placebo listings — a seasoned coin drawn once for each real listing and held for the same weeks — because a book
short alts and long Bitcoin earns in any year alts lag, whatever the listings do. The direction is not prior to the
data (the map's `listing_age` already points this way inside the top thirty), and the registration says so; the
prior for passing every condition is 10%.

**A data gap found before measuring.** Funding is on disk for 330 symbols, mostly ones that reached the top thirty.
The held accounting treats a coin with no funding as paying none, and holding only coins that have it would select
on later success. So the monthly funding files of the 158 remaining perps listed through 2024 are fetched first,
and a counts-only pass with fixed stop rules runs before any return is computed.
