# Family net R, printed before the boundary (display only)

- **Why:** `EPOCH_FAMILY_LIVE_ELIGIBILITY_V0.1.md` was decided on 2026-10-23 (Q1 yes, Q2 family-count
  correction). Test 2 asks whether a family's members make money: pooled net R, clustered by settlement day,
  with a lower bound above zero at `selection_adjusted_z(families tested)`. §7 allows printing that number before
  the boundary as a measurement. The point is that the boundary reads a number people have already watched,
  not one computed for the first time on the day.
- **What:** `selection_evidence family-net [--min-days N] [--json]`.
  - `clustered_mean` is the one-sided form of `forward_cohort.clustered_pair_mean` (same variance).
  - `family_net_r` pools each timeframe × economic family's member trades and leaves two kinds of member out:
    INVALID ones (`candidate_ranking.evidence_invalid`) and siblings marked by `forward_cohort.sibling_of`.
    It then corrects z by the number of families with at least `min_days` settlement days.
- **Its own subcommand, not `verdict`:** §7 names `verdict`, but `verdict` refuses before a cohort closes, so it
  cannot show the number in the months before. Wiring the family path into the verdict and into
  `assert_live_tier_confirmed` is still its own approval after the 2027-03-22 boundary. Nothing reads this
  output.
- **Differs from the §5 snapshot:** the §5 table counted every member. With INVALID members and siblings left
  out, 1h `hybrid(fused)` drops from 72 trades to 59 and 4h `breakout` from 168 to 133. Without the exclusions,
  this code reproduces the §5 counts (72 and 168).
- **Reading on 2026-10-23** (sandbox run on lake 20261023T121701Z): 22 families tested, z 3.05, no family NET>0.
- **Checked:**
  - Against a zero twin, the one-sided interval equals the pair interval.
  - A synthetic set shows the INVALID member and the sibling are left out and z is corrected over the families
    above the day floor.
  - The printed header carries `--min-days`.
  - Mutations caught: dropping the sibling exclusion, dropping the INVALID exclusion, and using a fixed z.
