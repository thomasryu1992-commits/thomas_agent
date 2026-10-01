# One consecutive-loss rule for the pool breaker and the live allowance (crypto refactor plan §K-1)

- **What changed (behaviour unchanged):** `guards._consecutive_losses` (the pool-wide breaker) and
  `live_allowance._consecutive_losses` (the per-lineage live allowance) each had their own copy of
  the streak loop. The allowance's docstring said "deliberately the same shape", and nothing held
  them to it. The rule now lives once, as `outcome_math.consecutive_losses(rows, r_of=, skip=)`:
  newest first, a row below zero adds one, the first that is not ends the streak, and a skipped
  row does neither.
- **What stays each counter's own,** passed as arguments:
  - **Probes.** The breaker skips them; the allowance counts every live row.
  - **The R it reads.** The breaker reads `pnl_r` (net R), and a missing or `None` value raises.
    The allowance reads the raw `result_R`, and a missing or malformed value ends the streak.
- **Not done:** making the allowance count by the breaker's rule. §K-1 measured that today's data
  gives the same answer, but it is a behaviour change that tightens the allowance once live rows
  carry net R, so it needs its own decision.
- **Evidence:**
  - Old and new functions for both counters were run on 914,871 generated row lists (845 row
    kinds, exhaustive to length 2 plus 200,000 random lists up to length 8). Results and exception
    types are identical.
  - Two characterization tests pin the shared rule and each counter's reading of a missing R.
  - Three mutants of the shared rule (skip ignored, zero counted as a loss, oldest first) are
    each killed through the wrappers.
