# The strategy edge order is decided: tilt mint toward 1d at the first verdict, judge against the twin at the epoch boundary, keep entry MARKET

- **The decision (Thomas 2026-10-01):** S1, S2 and S3 of
  `docs/proposals/CRYPTO_STRATEGY_EDGE_ORDER_V0.1.md`, each as recommended.
  - S1: at the first forward-cohort verdict, the factory schedules shift from 4h toward 1d. This is
    recorded as a loss-reducing choice, not a claim of edge: holdout cost per trade is about a third of
    4h's on 1d, and gross is not shown to be better.
  - S2: ranking against the coin-flip twin is decided at the research-epoch boundary, together with
    `REMAINING_WORK.md` §L E1. Until then `forward_cohort report --arms` is read-only and nothing
    judges by it.
  - S3: maker (limit) entry stays closed for now. Entry remains MARKET, as deferred on 2026-07-28.
- **What this PR changes:** the proposal's status line (DRAFT to DECIDED), a decision block in §4, and
  `STATUS.md` regenerated. No code, no schedule, no constant.
- **What waits, and on what:** S1's schedule change waits on the first cohort verdict, which also lifts
  review D3. `schedules.jsonl` is per-machine state, so that change gets its own `docs/history/` entry
  when it is made. S2 waits on the epoch boundary. Step 0 of the order (`report --arms`) already merged
  as #1088.
