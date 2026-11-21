# 2026-11-18 — discovery loop round 63: the top-30 sweep's lead rule against planted effects

**Shipped:** `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §81 (registration), a row in
`HYPOTHESIS_FORM_AND_RESEARCH_MAP_V0.1.md`, the status line, and code: `strategy_ladder.top_planted`,
`top_sens_null_check`, `top_sens_run`, `top_sens_reading`, commands `top-sens` and `top-sens-reading`.

**Why.** §79 found the top-30 four-week book, not the filter, to be the binding piece, and §80 set this measurement
next. §73.1's table of measured doors has no row for the top-30 sweep: §60 measured the two weekly sweeps only, and
§71's "single book" always buys the high side, which is not the sweep's rule.

**Choices that are not obvious.**

- *The book is `sweep` itself.* The planted variable goes through the same function the recorded sweep used (side by
  the sign of its own mean weekly rank correlation, L9T held four weeks, five years), so nothing about the book is
  re-implemented.
- *The door is the stored null.* Each chunk rebuilds ten of the 600 stored null books and must match every field the
  sweep printed; the p is read against the stored 600, not a new null.
- *A lead is the tier's own rule: Benjamini-Hochberg's first threshold* (0.10 / 59, which only p = 1/601 meets, so a
  lead beats all 600 null books); the share that also clears the entry rule (§60's form) is counted beside it.
- *Two doors calibrated to this tier.* At size 0 the planted variable is of the null's fixed-per-coin class, whose
  books sit higher than the weekly half: 25 of its 300 have p < 0.05 against the pooled 600, so the false-alarm range
  is 3 – 14 of 100 (binomial), not §60's 1 – 10. And the plant must take: the median rank correlation within 0.8 – 1.1
  of each size and the high side from 0.05 up, or the run is a broken door, not a finding.
- *What is already known sets the prior.* §71's single books on this tier never beat 1.557 at any size up to 0.10.
- *Written before the result: no outcome reopens the combined-logic question.* If the sweep catches map-size effects,
  a combined logic would add only bundles of smaller ones, whose books did not separate from no effect (§79.3); if it
  does not, the book is the gap and the next row is a book with another holding period and its own null.

**Result (06:10Z, §81.5).** All doors held (ten stored nulls rebuilt in every chunk, 5 false alarms in 3 – 14, the
plant took at every size). The top-30 sweep's lead rule caught no planted effect up to ρ 0.10 (0 of 100 seeds at each
size) and 2, 5 and 8 of 100 at 0.13, 0.16 and 0.20; it never reached 80% on the grid, where the 31 – 100 sweep reached
81% at 0.05. At ρ 0.10 the book's median Sharpe is 0.598. The four-week top-30 book is the binding piece under the
sweep's own rule too; the next row registers a top-30 book with another holding period and its own null.
