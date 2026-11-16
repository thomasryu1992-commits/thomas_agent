# 2026-11-16 — discovery loop round 43: a scenario effect, read by the scenario cells and by the axis check

**Shipped (registration and tool; nothing run in this commit):** `scripts/research/strategy_ladder.py`
(`AXIS_POWER_*` constants, `axis_carrier`, `axis_planted_size`, `split_share`, `_split_share_full`, `axis_placebos`,
`axis_test`, `axis_cell_rule`, `axis_power`, `axis_power_reading`, command `axis-power --tier`), tests,
`docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §61.

**Why.** Section 60.4 named the scenario cells' sensitivity as round 43. That measurement runs as registered, but
every map axis rests (`signal_lab.RESTING_AXES`), and a cell on a resting axis is never a lead. A scenario cell is
read only after the per-variable axis check wakes its axis, and that check's power was never measured (sections 16
and 47). So the same planted values are read by both doors, side by side.

**Choices that are not obvious.**

- *Round 39's planting and noise, reused.* `planted` and `_door_noise("fixed")` are called as they are, so the sizes
  speak the same units as sections 56 and 60.
- *The effect sits in each axis's smallest state.* It is chosen by a count, not by sort order. The `flip` shape puts
  minus the size in the other states, so the all-weeks cell sees almost nothing.
- *The cell side is the whole lead rule with only the resting clause lifted*, read on either state, with |t| alone
  printed beside it. In the flip shape the all-weeks sign follows the bigger state, so reading one state alone
  could fail the sign clause at every size.
- *One separation function for the real axis and the placebos.* The sums-only path is used only where it is exact,
  and a test pins it to `signal_lab.separation`. Ranks 31–100 have a 7-day frame on 212 of the 260 decisions, so
  the placebos are cut once per tier to those decisions; a series with values elsewhere, or a placebo left with a
  thin state, goes through the slow path (1.1 s per test against 15 ms), and tests pin both to the same p.
- *The false-alarm gate is an upper bound on p < 0.05 at size 0, per axis.* A wake at size 0 needs all 7,200
  placebos beaten, so a gate on wakes would pass anything.
- *The expected brackets are written before the run* (section 61.5), so the run's order is read against a stated
  expectation rather than discovered afterwards.

## Result (2026-11-16 11:18Z)

- All three gates passed: the 7-day line recomputed to 4.30 on both runs, and at size 0 the count of p < 0.05
  per axis was 3–6.
- An effect in one state only (`one`): in eleven of twelve tier-axis pairs the scenario cells reach 80% at a
  smaller size than the axis check. On ranks 31–100 the cells see it at δ 0.08 (trend 0.10) and the axis wakes
  at 0.10–0.13; on ranks 1–30 at 0.13 (trend 0.16) against 0.16–0.20. Between those sizes a scenario cell cannot
  be a lead. Whether the all-weeks cell already shows the same effect there was not counted (section 61.8); it is a
  gap for round 44's table. Breadth on ranks 1–30 shares a grid step.
- An effect that flips sign (`flip`): the axis wakes first in nine pairs and shares a step in three.
- The cell side came out where section 61.5 expected it. The axis side landed on the expected grid step for four
  axes; heat and trend came out one step larger (section 61.8). Nothing moves: resting axes, lines, placebos, family and closed readings stay as they are.
- Next is round 44: the table re-laid with the three measured doors side by side.
