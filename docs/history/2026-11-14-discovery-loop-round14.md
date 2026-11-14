# 2026-11-14 — discovery loop round 14: two of the three remaining variants, registered together

**Shipped (registration and tools; nothing run in this commit):** `scripts/research/strategy_ladder.py`
(`DeepFunding` takes a threshold whose sign sets the side, `deep_basket`/`deep_counts`/`deep_run` take the pool;
`hot_counts`, `hot_run`, `GapPools`, `gap_overlap`, `gap_counts`, `gap_run`, `with_spot`; the commands `hot-counts`,
`hot`, `gap-counts`, `gap`), `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §28.

**Why now.** Round 13 ended on one question: run the three unread variants round 12's table left, all kin of
records whose results are known? Thomas, 2026-11-14: "셋 다 돌림".

**Choices that are not obvious.**

- *The third variant is defined before the first is read.* Funding is computed from the perp's premium over spot,
  so a spot-gap tail picks much the same coin-weeks as a funding tail: its low side is H-F1 under another signal
  (closed, never redefined) and its high side is this round's H-F2. Choosing its side and threshold after H-F2's
  result would be a redefinition with the result in hand. So the side, the signal, the threshold and an overlap
  rule are fixed here: if 40% or more of its coin-decisions are L15's picks it is not measured. The limit and the
  decisions it needs are the ones the spot-share search already judged its own overlap by.
- *The price line is split into structure and selection.* H-F1's placebo books (long alts, short beta of Bitcoin)
  lost about 3% a year before costs whatever they held, and H-F1's by-year table shows the hedge losing in 2023 and
  2024. The mirrored book starts with that as a tailwind. Rung 2's entry rule and later-years rule read the book's
  return, so they can be carried by the structure alone; only the two placebo p-values speak to what funding
  selects. The run prints the placebo books' mean net return beside the result for that reason, and a pass without
  carry reads as structure.
- *One threshold, and a stop closes it.* +1.0% a week may leave 2022 too thin for the stop rule. If it does the
  record is "not measurable under the registered definition"; no second threshold is counted.
- *L15 does not move.* The side is a parameter whose default reproduces L15, the placebo seeds are the same
  strings, and round 11's tests pass unmodified. Its outputs (`deep1.json`, `bar-deep.json`) are cited and are not
  re-run. `deep_counts` gains a key (the share of the pool that is picked) and `deep_run` gains one (the placebo
  books' mean net return); neither changes a number already recorded.
- *The delta-neutral form is not this hypothesis.* Buying spot and selling the perp collects the same funding with
  no price risk. That is the funding-basis line, whose forward screen is running; widening it to a per-coin tail
  belongs there.
- *The open-interest variant waits one round.* It reads other data and nothing in these two results can reach its
  definition.

**Results (same day).**

- *H-F2 stopped at its counts and is closed unmeasured.* Funding of +1.0% a week is 2021's: the book would hold a
  position at 5, 5 and 20 decisions in 2022, 2023 and 2024 against the 26 the stop rule asks for. No second
  threshold was counted and no return was read.
- *H-G1 cleared its overlap rule and failed the ladder.* Only 17.1% of its coin-decisions are L15's picks (chance
  4.9%), so it was measured: annual Sharpe -0.135 net, -3.6% of total position a year, p 0.153 against the same
  book on other coins and 0.160 against coins as beaten down. It was not a tail (46% of the pool in 2022) and the
  funding its alts received did not cover their costs. Closed.
- *A guard was added before H-G1's counts were read.* The registered code counted every day with both closes; the
  spot-share search drops days when the two are more than 10% apart, because such a pair is another asset under
  the same name. Without the band one mismatched pair is a pick every week and never one of L15's, which pushes
  the overlap toward "a reading of its own". The unbanded counts were set aside unopened and the count was re-run.
- *The structure showed again.* The placebo books (long alts, short beta of Bitcoin, on coins the signal did not
  pick) netted -8% to -10% of total position a year, as H-F1's did. A book of this shape starts there.

