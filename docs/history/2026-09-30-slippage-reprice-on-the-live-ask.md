# A LIVE ask says what each candidate nets at other slippage rates, as a range where the record cannot say more

- **The gap:** review C2 designed a "net at 3/10/23.5 bps" column for the LIVE request, and
  `2026-09-28-slippage-instrument-archives.md` left it as a separate change. `REMAINING_WORK.md` §F8
  showed why it matters: the store's median edge sat 1.3 bps above the modelled rate. A LIVE ask did
  not say so.
- **The decision (Thomas 2026-09-30, gap analysis Q2):** the column belongs to the slippage
  measurement, which review D3 exempts. It is shown and decides nothing.
- **What was found while building it:** §F8's formula, `net − slippage × (r/3 − 1)`, is exact only
  for a record scored at one slippage rate.
  - Since the stop split of 2026-08-11, `cost.apply_cost_model` charges the stop leg its own rate.
    `cost_summary.total_slippage_cost_r` is the sum over both, and the stop share is not recorded.
  - On a record scored at 3.0 and 1.4, the formula scales the stop share by the entry ratio. That is
    the optimistic answer: it is what the record would net if no trade had ended on its stop.
  - On the host on 2026-09-30, 1,948 candidates carry one rate and about 1,600 carry two. Every
    candidate that can ask for LIVE is in the second group, because the promotion door refuses a
    record with no stop rate.
- **The change:**
  - `candidate_ranking.net_at_slippage` and `slippage_breakeven_bps`: pure functions over a
    candidate's own `cost_summary`. They return `(lowest, highest)`.
    - One rate: the two are equal, and equal to §F8's formula.
    - Two rates: the ends are the two books the total cannot tell apart, no stop exit at all and
      every trade ending on its stop.
    - No slippage total, no closed trade, no named rate, or an unreadable stop rate: None.
  - `promotion._live_context_notes` gains a fourth note built from them. It rides the LIVE ask's
    `risk_reason` like the other three: never signed, absent at the OBSERVATION tier.
  - `STRESS_SLIPPAGE_BPS = (3.0, 10.0, 23.5)` is indexed in `crypto/tunables.py`.
- **How it was checked:** not by the algebra. The tests charge the same trades again through
  `apply_cost_model` at the new rate, and run a real `backtest.backtest_spec` at 10 and 23.5 bps. The
  one-rate figure lands within 0.0001R of the re-run. The two-rate range contains it, and each end is
  reached by the book that defines it.
- **Read against the host store (2026-09-30, read-only):** the median two-rate candidate (stop 1.4)
  breaks even somewhere between 3.8 and 5.3 bps and nets −0.049 to −0.028R per trade at 10 bps.
- **Deliberately not done:**
  - The range would close if the replay (`crypto/backtest.py`) recorded the stop share, as it
    records `total_maker_fee_cost_r` for fees. That changes what a mint writes, so it is a separate decision.
  - No board column, no gate, no change to a constant. The re-price is of the trades the backtest
    took: a replay at a higher rate would also refuse entries at the entry-cost door.
  - The rates are fixed figures. Observed percentiles replace them when the live sample reaches
    twenty fills a leg.
