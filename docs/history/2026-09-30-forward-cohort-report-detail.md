# The forward cohort report can show how each member's trades were made up, without anything reading it

- **The gap:** `scripts/forward_cohort.py report` printed a member's row count, mean net R, active
  slices and status. The outside improvement plan asked for twelve figures per member. The rows
  already carried most of the inputs: each cohort row records `fee_cost_r` and `slippage_cost_r`, and
  the candidate's evidence records its backtest and holdout expectancy. Nothing summed them.
- **The decision (Thomas 2026-09-30, gap analysis Q1):** read-only columns on this report are outside
  review D3's pause, on the condition that no verdict, board or ranking reads them.
- **The change:** `report --detail` prints a second table per cohort.
  - Win rate, profit factor, maximum drawdown in R, average win and average loss.
  - Mean fee cost and mean slippage cost per trade, from the fields the rows record.
  - The forward mean minus the recorded backtest expectancy, and minus the holdout expectancy.
  - Every figure is on the net R the judge prices (`outcome_math.net_result_r`) and over the rows
    `forward_cohort.priced_nets` keeps, so the win rate and the mean share one cost basis. A test
    pins that the two row sets are the same.
  - A figure that cannot be computed prints `-`, never 0: no rows, no losing trade, a row without
    the cost field, a record without the expectancy.
- **Where it lives, and why:** in the script. `forward_cohort.cohort_report` is read by the board
  (`board_summary`) and by the scheduled walk's verdict stamps, so a key added there would reach
  both. This PR changes no file under `runtime/`, and `report` without the flag prints what it
  printed before (a test compares the two outputs line by line).
- **Read against the live cohort (2026-09-30, one-off container, state mounted read-only):** 115
  members. The member with the most rows (n=47, 4h `breakdown_short`) wins 32% of its trades at an
  average +1.35R against an average loss of 1.01R, and sits 0.36R per trade below its backtest.
- **Deliberately not done:**
  - No regime column. Forward rows carry no regime label; that is REMAINING_WORK §L E2, held until the
    first cohort verdict.
  - Nothing on the board and nothing in the judge.
