# 2026-11-18 — discovery loop round 65: what the two top-30 nulls' gap is made of

**Shipped:** `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §83 (registration), a row in
`HYPOTHESIS_FORM_AND_RESEARCH_MAP_V0.1.md`, the status line, and code: `strategy_ladder.sweep(..., legs=)`,
`book_legs`, `sweep --only null --legs`, `null_legs_reading` and `null-legs-reading`.

**Why.** §82.5 found the top-30 null held four weeks stands about 0.3 annual Sharpe above the same 600 books held
weekly, and the null files carry no cost or price share, so why the four-week door (max 1.557) was higher was unknown.
§82.5 registered the split as the next row.

**Choices that are not obvious.**

- *An exact split, not an ordered one.* A book's annual Sharpe is (price + funding − cost) / σ × √12 on the complete
  months, so the three terms add up to it and a gap between means splits with no "cost first" choice. The yearly
  shares on total position sit beside it; when the two units name different legs, or the return and the Sharpe move
  apart, the reading is "σ", not a leg.
- *Complete months only.* `measure`'s `funding_pct` and `cost_pct` sum every simulated day while its Sharpe reads
  complete months; the legs use the Sharpe's days. The row's own `funding_pct` is left as it was.
- *The recorded nulls must build again first.* The reading compares five recorded fields of all 600 books in both
  files and prints no leg unless every one matches; a moved lake is recorded, not rebuilt around.
- *Judged per half.* The weekly-redrawn and fixed halves trade differently by construction, so the pooled figure is
  printed but not judged. Each null's own tail (at or above its p95) is read unpaired.
- *Null books only.* `--legs` refuses any real variable and `transfer` under every accounting. "Price" is a bundle
  (side picked on the whole span, drift of held weights, net exposure) that this round does not split.

**Result (§83.5).** Every door passed: all 600 recorded books in both nulls built again field for field, and every
book's terms added up. The weekly-redrawn half reads "σ" (in Sharpe terms cost is 71% of the 0.37 gap, in yearly
return price is 52% of 6.7 points); the fixed half reads "price" (82% and 67%); each null's own tail reads "price".
The four-week books earn 3.5 – 4.7 points a year more price and swing more (monthly σ 11.4% against 7.6%); funding is
under 5% of every gap. No verdict moves; the loop goes to the dated rows.
