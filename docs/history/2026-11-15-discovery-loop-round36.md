# 2026-11-15 — discovery loop round 36: the sweep on ranks 31–100

**Shipped (registration and tools; nothing run in this commit):** `scripts/research/strategy_ladder.py`
(`TierSweep`, `tier_sweep_names`, `tier_sweep_counts`, `tier_sweep`, `tier_sweep_null`, `tier_sweep_reading`,
`tier_sweep_gate`; commands `tier-gate`, `tier-counts`, `tier-sweep`), tests,
`docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §52.

**Why.** The sweep (every registered variable built into one book and laid beside null books) had only ever read
the top 30, and the record gave no reason for not doing it on ranks 31–100. Round 35's last pass over the table
named that as the one gap without a reason. On the top 30 the sweep had surfaced a book the map did not flag, so
the map having read this tier does not stand in for it.

**Choices that are not obvious.**

- *The book is H-R2's, not the top tier's sweep logic.* H-R2 is the one book read on this tier: rank weights over
  every ranked member, weekly, held, 5 + 21 bps on every coin. The top tier's sweep logic was picked for the top 30.
- *A gate on a closed record.* H-R2's own book through the new code over H-R2's own 60 months must give
  `mid1.json` to the digit. `MidTier` itself is untouched.
- *Seventeen registered variables get no book here, by name*, each because a record forbids it: the closed
  volatility cluster and its kin, H-M1's two variables, H-R2's variable and its other windows, the top tier's
  lead (D10: never read on another tier) and its runner-up, and depth (no files). 38 remain.
- *One class of null: a value fixed per coin.* Cost sets the null's mean on this tier, so the null that trades
  least is the high bar for every book; mixing faster nulls in would make a slow book's p look smaller than it is.
- *Each book on its own span*, starting the month after its first decision, with the null books cut to the same
  months and their side picked on those months. A book under 24 months is named and not built.
- *A lead needs both*: surviving the correction and clearing the entry rule. A survivor under the entry rule is
  recorded and nothing is built from it. The sweep is read once: no other hold, cost, weighting or null.
- *The tier is read as it is*, with the dated contracts and the coin without funding that round 35 recorded;
  their share is counted before any return and again if a lead appears.

## Gate and counts (no new return read)

- *The gate passed*: H-R2's book through the new code equals `mid1.json` on all eight keys.
- *36 books are built.* Two of the 38 have under 24 months of their own (the top-trader share's change and
  position). 29 books run 48 months from 2021-01; the seven positioning books run 27–36 months.
- *No book trades less than the null* (ratios 1.08 to 8.10). Two books rank under 80% of what a null book ranks;
  three positioning books have months inside their span with no book (their Sharpe is pressed toward zero).
- *Ties*: the four spread levels stand at one value in over half their cells and the listing age in a quarter; if
  one of them is a lead, round 32's untied-leg rule applies as registered.

## Result: one lead, not a candidate

Six sandbox runs at `7b44b1a2`, one each; files under `~/research/discovery/tier1/`.

- *One book survives the correction and clears the entry rule*: 30-day funding, long the low side, on ranks
  31–100 over 2021-01..2024-12. Annual Sharpe 1.000 after costs (on the entry line as rounded), +16.95% a year on
  total position (price +14.3, funding +9.6, cost −6.9), none of 600 null books at or above (the highest is
  0.988). Years: +39.2, +5.8, +26.2, −3.3. Its side was picked on a mean rank correlation of −0.0071.
- *The other 35 do not survive*: three are under 0.05 where chance gives 1.8, and none is a survivor under the
  entry rule. The median book returns −5.2% a year (the prior said about −9%).
- *Kin already on file*: 7-day funding on the same side scored 0.637 with all of it in 2021; the top tier's
  30-day funding book scored 0.009 in its own sweep.
- *What it is*: a lead by the registered rule. It is not on the ladder. The checks before the form (transfer,
  legs, concentration, closed kin) are the next round, with their closing rules fixed before any reading. Nothing
  printed yet shows which coins or which months earned it.
