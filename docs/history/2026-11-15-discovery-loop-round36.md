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
