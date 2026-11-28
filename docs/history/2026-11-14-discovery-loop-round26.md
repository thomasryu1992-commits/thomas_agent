# 2026-11-14 — discovery loop round 26: the three items with no record

**Shipped (registration and tools; nothing run in this commit):** `scripts/research/signal_lab.py` (`_spread`,
`_premium`, `read_premium`, `hold_premium`, `OVERLAP_CANDIDATES`, `overlap` and the `overlap` command; the axis
`funding_spread` in `LATE_AXES`), `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §40.

**Why.** Round 25's item-by-item pass over §3 found three things the "on disk but unused" list names and no round
had touched: a spread estimated from highs and lows, the dispersion of funding across coins, and the premium index
as a map variable.

**Choices that are not obvious.**

- *A candidate variable is ranked beside its kin before it is mapped, and that check reads no return.* Earlier rounds
  decided "this is that closed reading again" by judgement. Here it is a number fixed beforehand: a median rank
  correlation of 0.8 or more with a registered kin on the top tier, either sign, and the candidate is not mapped.
  A test makes `forward_return` raise and the check still runs.
- *The candidates are not in the registry.* The axis check reads the registry, so its older rows must equal the last
  run's. A candidate under the threshold joins the registry in a later commit, with the random variables kept in
  proportion.
- *The premium files run to 2026-11.* `read_premium` keeps nothing after the span's last day; a test writes five
  later days and finds none.
- *Funding dispersion is an axis, not a variable.* It is one number a week for the whole market. It stands
  fourteenth, so it takes the 400-placebo path: with 200 the smallest p it could show is above its level.
- *Nothing compared is not a pass.* If a candidate has no decision with eight coins ranked beside a kin, the check
  says so and maps nothing.

**Result, first half (2026-11-14 16:36Z).** The funding-dispersion axis is not woken (353 of 400 placebos at or
above). Both candidate variables stand under the threshold beside their kin on the top tier: the spread estimate at
0.13 (its nearest is volatility), the premium at 0.72 (30-day funding). As registered, both join the registry
(45 variables, 88 random variables) and are mapped once.

- *The prior on the spread was wrong.* It was expected to order coins as the daily range does; the rank correlation
  with the range is 0.05. The reason is not known. The overlap output now also prints the share of values at zero,
  since an estimate floored at zero may tie many coins; the rule's numbers must not move.

**Result, second half (2026-11-14 16:55Z).** No lead among the two: the spread's largest |t| is 2.67 against a line
of 4.30, the premium's 3.50 (28 days) against 4.96.

- *More than half of the spread's values are zero* (52% and 54% by tier), because the estimate is floored at zero.
  So the first half's sentence "the overlap already answered that the spread is not the volatility cluster read
  again" was more than a rank correlation of 0.13 can carry, and the result says so. The floor and the 0.8 are not
  changed and nothing is re-ranked.
- *The "what is left" table now goes item by item*: the rows of §3, the bullets under them, the sentences of §7 and
  the cache's folders. Two things come out of it. The two new levels are owed their shapes (change and position),
  since no written exclusion covers them. And §7's past-data test of the hypothesis channel was never run; the
  screen is a pure function, so it looks runnable on the research side, but it is the factory's layer and needs its
  own registration first.
