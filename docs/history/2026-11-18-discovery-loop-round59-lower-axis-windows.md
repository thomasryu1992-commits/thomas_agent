# 2026-11-18 — discovery loop round 59: the lower tier's axis check gains the spot taker-buy share's three windows

**Shipped:** `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §77 (registration), a row in
`HYPOTHESIS_FORM_AND_RESEARCH_MAP_V0.1.md`, the status line, and code: `strategy_ladder lower-axes` takes `--names`.

**Why.** §76.2 left this row: the lower tier's own axis family has room for the three windows (354 tests, first bar
0.000282 above 1/7,201), and §74 is the precedent for adding late variables to a recorded family.

**Choices that are not obvious.**

- *`--names` instead of re-running the group.* Re-running `lower-axes --only spot_flow` would count the recorded four
  again and `axes_family` would refuse the duplicate. With names, the run reads only the windows, and its door is the
  round-58 map's cells of those variables; a name the map did not read refuses.
- *Two doors on the code.* The no-names path is checked on the shortest group (coin character) against its recorded
  counts, and the CLI wiring is pinned by a test that an unknown name stops before anything loads.
- *Windows of one variable count once.* Two windows passing on the same axis with the same top state are one piece
  of evidence, and the 30-day test joins them when it is under 0.05 on that axis.
- *"Next" is fixed before the result.* This is the last row §73.4 and §76.2 left, so the following round re-lays
  the table from the record and carries round 58's two cost rows.

**Result (04:19Z, §77.6).** All doors held (the recorded family from its files, coin character's 12 count rows, the
windows run rebuilding its 60 map cells). In the family of 354 no test survives; 16 are under 0.05 against 17.7 by
chance, 2 of the 18 new (0.9 by chance), the smallest new p 0.0253 at rank 8 against 0.0023. The windows' lower-tier
axis check is closed; on the two upper tiers it stays a cost row (744 > 720).
