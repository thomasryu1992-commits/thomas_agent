# 2026-11-17 — discovery loop round 56: the per-variable axis check on the four variables added after §47

**Shipped:** `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §74 (registration), a row in
`HYPOTHESIS_FORM_AND_RESEARCH_MAP_V0.1.md`, the status line. No code change: `signal_lab axes-by-variable --only
spot_flow` already runs exactly the four.

**Why.** Round 55 (§73.3) found the one row with neither a record nor a prohibition: the per-variable axis check
(§16, §47) ran on 55 variables, and the spot flow level, its two shapes and the spot trade size came later.

**Choices that are not obvious.**

- *Two doors, because the new run re-creates no old test.* §47's door was old tests reappearing inside the new runs;
  here the run holds only new tests. So the recorded 642-test family is re-read from its files (done before
  registering), and the recorded `spot` group is re-run and must match its counts to the letter, on the same lake
  snapshot as the measured run (the round-53 snapshot caveat, avoided by reading `.err` first).
- *A pass wakes no axis.* §47.5, written before measuring, says so; §61.1 later called this check the waking door.
  The registration follows §47.5 (round 55's reading).
- *What a pass buys is fixed per variable before measuring.* The spot trade size is recorded only (its clue was closed
  as a rebuild of the perp trade-size book, §68.5). For the other three, §47.5 (route to the scenario-switch book) and
  §44 (no other logic for what failed the sweep) disagree, so a pass is a question to Thomas with the default "not
  raised". A pass whose kin already split the same way in the 642 is not counted as separate evidence (§51.6).
- *An old test that enters the BH list only because new tests are small is named, not counted as a clue.*

**Result (2026-11-17 17:26Z, §74.5).** Both doors held: the recorded spot group's 36 count rows came back equal on
the same lake snapshot (`20261117T171701Z`) as the measured run. The 48 new tests joined the recorded 642: no test
survives Benjamini–Hochberg in the family of 690; two new tests are under 0.05 (chance 2.4), the smallest 0.0332 at
rank 29 against a threshold of 0.0042. The four variables' axis check is closed.
