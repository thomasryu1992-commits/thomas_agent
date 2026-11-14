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
