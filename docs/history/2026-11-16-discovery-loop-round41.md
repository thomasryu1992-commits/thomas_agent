# 2026-11-16 — discovery loop round 41: checking round 40's two map leads below rank 100

**Shipped (registration and tool; nothing run in this commit):** `scripts/research/strategy_ladder.py`
(`LOWER_LEADS`, `LOWER_OVERLAP_CLOSE`, `lower_lead_overlap`, command `lower-overlap`), a test,
`docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §58.

**Why.** Round 40's map found two leads on ranks 101+ (residual momentum at 7 days, listing age at 28 days). Section
57.8 registered a check round before any form.

**Choices that are not obvious.**

- *The overlap comes first and reads no return.* Each lead's book is laid beside every book the record strikes on
  this tier that has values there (fourteen), plus H-R1's closed book. The list was fixed before the result. It is
  not three books picked after seeing which leads came out. |median cosine| ≥ 0.5 closes the lead. Only the size
  is read, because a struck book has no registered side.
- *Residual momentum closes even if it passes the overlap.* Its cell's own book (weekly, low side, this tier's L22)
  was already read in round 40's sweep and missed the entry rule. Building another book for it would be a variant
  chosen after the result.
- *Listing age is not closed by the weekly book.* That book turns over about 60% a week because the tier's members
  change. It is therefore not the 28-day cell's book (section 57.11). The lead goes to the form, and the form's own
  rule decides.
- *The size line closes nothing.* The form's rule for a size under the floor is to say so and set the prior to
  match; it is not a closing rule. An earlier draft treated it as one, and that draft was dropped.
- *Round 42 is fixed now.* If nothing passes, it measures the sweep's sensitivity, which section 57.7 records as
  never measured. Steps 1–4 of section 4's order are recorded as done, and step 5 is not asked.

## Result (2026-11-16 06:17Z)

- Both cells reproduced to the digit.
- **Residual momentum is closed.** Its book's weights sit at median cosine −0.950 with momentum_30's book on this
  tier, a reading H-R2's record forbids there. It is also −0.506 with momentum_90.
- **Listing age passed the overlap**: the largest overlap is −0.272, with the intraday range. The form could not
  fill line 1 (who pays) or line 3 (size) from the record. The only recorded payer is H-L1's, which was measured at
  7–63 days and closed. So by the form's default the lead is not raised, and Thomas is asked (D12; default no).
- Round 42 measures the sweep's sensitivity. That registration does not depend on D12.
