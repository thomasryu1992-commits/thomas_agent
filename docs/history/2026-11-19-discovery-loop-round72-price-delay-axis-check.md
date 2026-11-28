# 2026-11-19 — discovery loop round 72: the price delay's per-variable axis check

**Shipped:** `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §90 (registration), a row in
`HYPOTHESIS_FORM_AND_RESEARCH_MAP_V0.1.md` and the status line. No code.

**Why.** §87.1 left the upper tiers' axis check of the price delay as the next row; the axis family has room for one
hourly variable (60 × 2 × 6 = 720).

**Choices that are not obvious.**

- *§74's frame unchanged.* The recorded 690 tests were rebuilt from their files before registering; the spot group is
  re-run first and must match its recorded counts on the same lake snapshot.
- *A pass would not reopen the closed lead.* The price delay's map lead and H-P1 are closed, so a passing test is a
  lead whose route to the form is Thomas's question, default not raised.

**Result (§90.4).** All four doors passed (recorded family, the spot group's 36 rows, one lake snapshot, twelve new
tests only). In the 702-test family nothing passes Benjamini–Hochberg; none of the twelve new tests is under 0.05
(smallest 0.136). The price delay's axis check closes, and with it every row of the two hourly variables.
