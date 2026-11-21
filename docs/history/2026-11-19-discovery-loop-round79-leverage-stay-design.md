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
  also reaches the ranks 101+ map, that sweep's 19 open slots (cap 60 from 1/601 ≤ 0.10/n), and that tier's axis
  check. §97.5 corrects the axis room: the code has no cap, and the upper family's rule (0.10/n ≥ 1/7,201) gives
  720, so 366 tests are left. §97.1 first said "354 of 360, one variable", reading an observation in §77 as a cap.
- *Candidates dropped on paper, with reasons.* Distance to the 90-day high (kin of momentum, which is struck on the
  lower tiers), downside beta (the closed volatility cluster), funding volatility (no "who pays" line), the
  funding–premium gap (mostly the exchange's own formula), the volume–return correlation (a per-coin statement, not
  a cross-sectional rank), and growth in trade count (volume growth over trade size).
  - No daily-bar candidate survived, and the form was not loosened to fill the ranks 101+ slots.
- *One variable is raised: leverage stay (`oi_turnover_30`).* It is open interest × close ÷ perp quote volume over
  the same 30 days the other positioning variables read.
  - It is the per-coin quantity §38's market axis used, now ranked across coins. That axis is not re-checked.
  - It reaches all three tiers' maps and one slot each in the ranks 31–100 and 101+ sweeps. §97 first said the
    positioning files do not cover ranks 101+. That was wrong: round 45 (§63) fetched and sealed that tier's own
    files, and §97.5 corrects it.
- *The overlap door's kin are built per tier from `signal_lab.no_book` in code, not by hand (§97.5).* Each tier's
  list is every name struck there, the nine struck everywhere, the two closed hourly variables (upper tiers),
  illiquidity and volume growth.
  - On ranks 31–100 that adds funding level, which a leverage level is most likely to repeat. §97 first listed
    three of that tier's nine struck names.
  - The 0.8 rule is read per tier, not only on the top tier as in §40. The 0.5 weighted-overlap rule applies to
    every struck book of the tier.
- *Prior, sized honestly.* Overlap door 55%, map lead 4%, sweep survival 3%. The two hourly variables, given
  3–5%, produced nothing.

**Next.** Round 80 registers R1's three inputs-only doors (coverage, overlap, persistence). If R1 does not pass
them, it is closed and the next row is the dated one (report and map by 2026-11-26).
