# `forward_cohort report --pairs`: each member against its own twin, per family × context

- **Why:** THROUGHPUT P1-3 (`docs/proposals/RESEARCH_FORWARD_THROUGHPUT_ANALYSIS_V0.1.md` §11–12, option D,
  decided 2026-10-06 via scorecard Q3). The arm means (`report --arms`) compare two pooled populations,
  so a timeframe where the market drifted reads as an edge for both; a member and its own coin-flip twin
  share bars, direction and exits, so the drift cancels inside the pair.
- **What it computes (`scripts/forward_cohort.py`):** per pair, the member's mean net R per trade minus
  its twin's, over the rows the judge prices on each side; averaged over the pairs of one timeframe ×
  family × symbol scope, with one `(all)` line per timeframe first. The 95% interval is clustered by
  settlement day: the mean is a sum of per-day contributions and the days are the units treated as
  independent (§11 measured per-trade intervals 1.1–3× too narrow). Under two days there is no interval.
  A pair with no priced trade on either side is left out.
- **Display only, like `--arms`:** computed in the script and nowhere else; no verdict, board, ranking or
  door reads it, and `arm_comparison` stays the designed comparison. Whether a pair statistic ever
  judges anything is an epoch-boundary question (RESEARCH_EPOCH Q3 B).
- **First reading, on a copy of the host's stores (2026-10-06, direction only):** 1d −0.719R
  [−1.291, −0.147] over 26 pairs and 31 days, 6 of 26 members ahead of their twin; 1h +0.252R
  [−0.323, +0.826] over 32; 4h −0.103R [−0.377, +0.171] over 50. Family × context cells hold one or two
  pairs (85 cells for 108 pairs), which is why the timeframe lines lead.
