# 2026-11-16 — hypothesis H-A1: listing age on ranks 101+, held four weeks (Thomas D12 "올림")

**Shipped (registration and tool; nothing run in this commit):** `scripts/research/strategy_ladder.py`
(`TierSweep.monthly_rows(..., every=, weigher=)`, `tier_later(..., every=)`, `tie_mean`, `ha1_twin`, `ha1_days`,
`ics_at`, `ha1_counts`, `ha1_reading`, `ha1_passes`, `ha1_run`, commands `ha1-gate|ha1-counts|ha1`), tests,
`docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §59.

**Why.** Round 41 could not fill the form's payer or size lines from the record, so by default listing age was not
raised. Thomas answered D12 "올림": one registered four-week book, read once.

**Choices that are not obvious.**

- *Sixty months with empty months counted as zero.* That is H-R1's rule on the same tier. The own-span figure is
  printed beside it and nothing reads it.
- *Rung 2(b) reads the side the earlier years name* (`tier_later`, unchanged from round 40), not H-R2's fixed-side
  shortcut. The side was fitted on this span's map cell, and the rung exempts only a book with nothing fitted.
- *The null books pick their side by their own 28-day rank correlation*, as the real side was picked.
- *A no-name twin instead of section 52.5's leg split.* The tied coins here are the oldest, and all of them are on
  the long side. Splitting legs into tied and untied would therefore only split long from short. The twin gives
  tied coins their group's mean weight and must clear the entry rule too.
- *The gate is the same path with weekly holding*, which must reproduce round 40's row for the variable.

## Result (2026-11-16 09:04Z): closed

- The gate reproduced round 40's weekly row to the digit. No count stopped the run (11, 13 and 13 four-week
  decisions with a book in 2022–2024).
- Three of the five conditions failed. Sharpe was 0.633 against 1.0, although the yearly figure was 16.06%. The
  p against 600 null books was 0.0649, and the no-name twin's Sharpe was 0.613. Rungs 2(b) and 3 passed: the later
  years read +35.30, +10.77 and +24.09, and no year was negative.
- Section 59.6 reads this as closed. Listing age on ranks 101+ is not read again under any hold, cost, weight,
  tie rule, tier, side or null. No confirm read was used.
- Next is round 42, the sweep's sensitivity, with its own registration.
