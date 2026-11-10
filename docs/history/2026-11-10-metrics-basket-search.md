# 2026-11-10 — Metrics basket search: the third attempt clears the development floor; the locked span is still unread

**What.** After the price-characteristic search used its ten attempts without a pass, Thomas asked what else could
be searched. The cache turned out to hold the exchange `metrics` files for the whole top-30 universe (99.8% of
52,950 member-days), which an earlier assessment had called incomplete. `docs/proposals/METRICS_BASKET_SEARCH_V0.1.md`
runs the same protocol on five features built from them, with a budget of five.

Added:
- `scripts/research/metrics_basket_search.py` (`list`, `build`, `explore`, `confirm`).
- `tests/test_research_metrics_basket_search.py`.
- `basket_search.scores`, `weigher` and `measure` take another protocol's feature list; their default is unchanged.

**Why open interest is read in contracts.** The quote-value column is contracts times price, so ranking its growth
would rank a trailing return, the family this protocol keeps out because its 2025–26 outcome is already known. A
test changes the value column and expects the same daily values.

**Why two development spans.** The top-trader and taker columns are empty for most of 2022. A candidate reading
them would have months without a basket counted as zero, so it is developed from 2023-02; one that reads only open
interest and the all-account share gets 2022 as well.

**Why the budget is five.** Three years, not five, means a zero-edge rule clears the floor about 4% of the time per
try (8% on the short span). Ten tries would make a false development pass about as likely as not on the short span.

**What happened.** The protocol commit (`e8f85b8e`) was pushed before the daily values were built or anything was
run. M01 (fade the crowded long side) scored 0.341, M02 (fade open-interest growth) 0.745, and M03 (both ranks
together) 1.547. The first candidate over the floor is final, so the search stopped with two attempts unused and
`FINAL` names M03.

**Why this is not yet a result.** Three tries on this span produce a chance pass about 12% of the time, the two
parts missed on their own, and 2022 contributes nothing. The verdict is one official run on 2025-01..2026-09 from
the merged commit; a zero-edge rule passes that about 9% of the time.

**Correction before the confirm run (same day).** A count of the inputs, dates and counts only, showed the
baskets were never drawn from all thirty. The cache holds a coin's `metrics` files for the months it was in the
universe plus four days before, and `oi_growth` needs the value thirty days back, so a coin new to the universe
is unranked for most of its first month. What was measured is close to "coins in the top thirty this month and
last": about 22 ranked, usually four a side (the first record said six). The filter reads nothing after the
decision day (0 of 3,557 and 2,036 ranked pairs lean on a later month's membership), and the locked span has the
same shape as the development span. Four development weeks in early 2022 had no basket because the exchange's
all-account column is empty from 2021-12-31 to 2022-01-19. Thomas's go-ahead rested on the uncorrected text, so
the confirm run was held and the choice is asked as Q3. Nothing was rebuilt or fetched.
