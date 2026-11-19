# 2026-11-19 — discovery loop round 75: L25's planted door, with plants that turn over

**Shipped:** `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §93 (registration), a row in
`HYPOTHESIS_FORM_AND_RESEARCH_MAP_V0.1.md`, the status line, and code in `strategy_ladder`: the `blend` option of
`simulate_held` (L25: half the way to the target, each side scaled back to the target's total, coins that left the
tier sold whole), `TierSweep.monthly_rows(..., blend=)`, the planted door's functions and the `cost-null`,
`cost-door` and `cost-door-reading` commands, with tests.

**Why.** §92 proposed L25 for the 2026-12-17 restructure and left open how to plant an effect it could be measured
on (§92.6): an effect redrawn every week is half lost by construction to a book that carries half its position.

**Choices that are not obvious.**

- *Two plants, the rule on the persistent one.* `week` (next week's return) and `month` (2ρ × the next four weeks'
  return score, about ρ per week); the noise is AR(1) φ 0.8, drawn per coin as a whole series so call order never
  changes it. The question to Thomas rests on `month`; `week` is reported beside it.
- *The question's own null.* L22 is read against the recorded ranks 31–100 null; L25 against the same null
  variables built as L25 books — the null §92.4 names for the real re-run, not an AR null that would trade more and
  lower the bar.
- *A rule on the grid,* not at one size: two neighbouring sizes 20 points apart, or the 80% bracket one step lower,
  so a floor or a ceiling at 0.05 cannot decide it.
- *Six sizes and a seed rule fixed before measuring:* the run is two logics × two plants; if the first chunk shows the
  run would pass 23:00 UTC, seeds drop to 50.
