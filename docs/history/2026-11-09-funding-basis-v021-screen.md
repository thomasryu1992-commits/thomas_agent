# 2026-11-09 — The v0.2.1 funding-basis screen: coverage before exclusion

**What.** `scripts/research/funding_basis_v021_screen.py` screens B-POS over the point-in-time top-30 universe.
- The window is 2026-12-01..2027-03-31 (`FUNDING_BASIS_V0.2` §4, §7, §8).
- The rule is `STRATEGY_SCREEN_GATE_V0.1`: keep when the window's mean daily portfolio return is above zero.
- The model, universe and traded-days holding rule are `funding_basis_v02` unchanged. The verdict and shown-only
  numbers come from `funding_basis_screen.summarise`, factored out of the v0.1 screen so the two cannot drift.

**Why coverage is checked before exclusion.** v0.2's missing-data rule excludes a symbol-month whose traded days
lack bars, and blocks above 5%. That rule assumes the data is complete. In a forward run it is not:
- A month not fetched yet looks exactly like a permanent gap. Applied directly, an unfetched March would exclude
  every member and block.
- Worse, a series that ends in February would read as a delisting, close at its last bar, and be judged.

So the klines decide whether a perp was alive. A member whose derivative bars stop short of its last traded day
is reported `NOT_YET`, never excluded or closed.

**What blocks and what waits.** A ranking month whose klines hold fewer than 30 perps means the fetch missed
some. Waiting would never resolve it, so the run blocks and names the month. A ranking month absent from the spot
archive is just not fetched yet: `NOT_YET`. Fewer than 30 *members* is normal and only shown. The top 30 are
ranked first and those without a spot pair then drop; 40 of v0.2's 81 months had fewer, and 2025–2026 months had
20–29. The first draft blocked on fewer than 30 members. A count on the real cache showed it would have blocked
the official screen on a universe exactly as pre-registered.

**Why only B-POS.** Only B-POS cleared the entry floor in the official v0.2.1 run. Screening B-ALL "for display"
would produce a reading nobody pre-registered a use for.

**Why not in the crypto script roster.** Like the v0.1 screen, it reaches the lane only through modules that are
already listed.
