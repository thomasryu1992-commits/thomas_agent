# 2026-11-18 — discovery loop round 64: the same top-30 book held weekly, measured against the same planted effects

**Shipped:** `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §82 (registration), a row in
`HYPOTHESIS_FORM_AND_RESEARCH_MAP_V0.1.md`, the status line, and code: `strategy_ladder.sweep(..., accounting=)`,
`sweep --only null --accounting held`, `top-sens --accounting`, `top_sens_false_alarm_range`, `top-sens-reading --null`.

**Why.** §81.5 found the four-week top-30 book catches no planted effect up to ρ 0.10, and §81.3 set the book side as
the next row: the same book with another holding period and its own null.

**Choices that are not obvious.**

- *Only the hold changes.* `sweep` takes the accounting; the weekly book is `simulate_held(every=1)` with the engine's
  costs, as the recorded top-30 sweep. The 31 – 100 and 101+ weekly books paid the listing pool's unit cost, so their
  numbers are not carried over. Another accounting can build null books only; a real-variable sweep under it refuses.
- *The false-alarm range comes from the new null by a rule*, the rule that gives round 63's 3 – 14 on round 63's null.
- *A paired comparison.* Seeds and planted variable are round 63's, so each row's rank correlation and side must equal
  round 63's before any share is read.
- *The two outcomes do not weigh the same.* The planted noise is drawn once per coin, so the planted book barely
  trades; a real variable re-ranks weekly and pays for it. "Not reached" ends the row; "reached" is an upper bound,
  and the next step is the same measurement with noise redrawn weekly, then a question to Thomas, default not raised
  (§44.4 says a variable that failed is not re-measured with another logic).

**Result (11:45Z, §82.5).** All doors held (null shape, ten nulls rebuilt per chunk, 6 false alarms in 3 – 14, the
plant took, the 1,100 rows paired with round 63 exactly). The weekly book's lead share is 11, 54, 79 and 98 of 100 at ρ
0.05, 0.08, 0.10 and 0.13, where the four-week book had 0 up to 0.10. The registered line was 80% at ρ 0.10 or below;
79 is under it, so the row is closed as registered and the loop goes to its dated rows. The line and the share are
not moved after the result. Written beside it: the second clause of the registered "not reached" sentence does not
fit these numbers and is left as written; the figures are an upper bound (the planted book barely trades); and the
weekly null sits far lower (mean 0.008, max 1.368) than the four-week one (0.32, 1.557); paired by name, all 600
null books keep their side and the funding share moves only about 2.3 points, so most of that offset is unexplained.
§82.3 registered nothing about the combined-logic question; this is the first result that bears on it, and it is read
at the 2026-12-11 restructure row. The row that runs next: split the two nulls' difference into price, funding and cost.
