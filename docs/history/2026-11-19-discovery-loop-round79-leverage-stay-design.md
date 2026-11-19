# 2026-11-19 — discovery loop round 79: a new variable for the remaining doors (design only)

**Shipped:** `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §97, a row in
`HYPOTHESIS_FORM_AND_RESEARCH_MAP_V0.1.md`, and the status line. No value was computed, coverage shares included;
only file names and recorded family sizes were counted.

**Why.** Thomas's §96.7 line keeps the loop running on the remaining doors (the map and the ranks 31–100 sweep).
Those doors only move when the registry changes, and it has not changed since §87. Redrawing the map now would
reproduce it. The weekly report was written this morning (§91).

**Choices that are not obvious.**

- *A correction to §91 and §95.3.* Both said a new variable reaches only the upper-tier map and the 15 open slots
  of the ranks 31–100 sweep. That holds for hourly variables. A variable built from daily bars, funding or premium
  also reaches the ranks 101+ map, that sweep's 19 open slots (cap 60 from 1/601 ≤ 0.10/n), and one variable in
  that tier's axis check (354 of 360, §77).
- *Candidates dropped on paper, with reasons.* Distance to the 90-day high (kin of momentum, which is struck on the
  lower tiers), downside beta (the closed volatility cluster), funding volatility (no "who pays" line), the
  funding–premium gap (mostly the exchange's own formula), the volume–return correlation (a per-coin statement, not
  a cross-sectional rank), and growth in trade count (volume growth over trade size).
  - Nothing reaches the ranks 101+ slots, so they stay empty. The form was not loosened to fill them.
- *One variable is raised: leverage stay (`oi_turnover_30`).* It is open interest × close ÷ perp quote volume over
  the same 30 days the other positioning variables read.
  - It is the per-coin quantity §38's market axis used, now ranked across coins. That axis is not re-checked.
  - It reaches the upper-tier map and one slot of the ranks 31–100 sweep. Positioning files do not cover ranks 101+
    (§57).
- *The overlap door's kin are fixed before any value exists.* They are every struck name, the two closed hourly
  variables, the nearest positioning variables (OI growth and long-account share, both struck on ranks 31–100,
  and taker level), illiquidity and volume growth.
  - The 0.5 weighted-overlap rule with struck books applies.
- *Prior, sized honestly.* Overlap door 55%, map lead 4%, sweep survival 3%. The two hourly variables, given
  3–5%, produced nothing.

**Next.** Round 80 registers R1's three inputs-only doors (coverage, overlap, persistence). If R1 does not pass
them, it is closed and the next row is the dated one (report and map by 2026-11-26).
