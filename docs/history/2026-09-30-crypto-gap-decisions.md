# The crypto gap analysis is decided and built: both report changes landed, and pause/kill stay as they are

- **The decision (Thomas 2026-09-30):** Q1, Q2 and Q3 of
  `docs/proposals/CRYPTO_SYSTEM_IMPROVEMENT_GAP_ANALYSIS_V0.1.md`, each as recommended.
  - Q1: read-only columns on the forward cohort report are outside review D3's pause. The condition
    stands: no verdict, board or ranking reads them.
  - Q2: net R at fixed slippage rates and the breakeven rate belong to the slippage measurement
    (review C2), which D3 already exempts. They are shown on a LIVE ask and decide nothing.
  - Q3: `/pause` and `/kill` keep stopping position management. `docs/DEPLOYMENT.md` already says so;
    nothing changes.
- **What this PR changes:** the proposal's status line (DRAFT to IMPLEMENTED), a decision block in §4,
  the record of what merged in §7, and `STATUS.md` regenerated. No code.
- **One correction recorded with it (§7):** the proposal said a candidate can be re-priced at another
  slippage rate exactly, citing `REMAINING_WORK.md` §F8. That formula predates the stop-slippage split
  of 2026-08-11. `cost_summary.total_slippage_cost_r` is now a sum over two rates and the stop share is
  not recorded. Of 3,546 candidates on the host on 2026-09-30, 1,593 carry two different rates, and
  every candidate that can ask for LIVE is one of them. PR-E therefore reports a range for those.
- **Built while this PR was open:** PR-D merged as #1067 and PR-E as #1069, so the status goes straight
  to IMPLEMENTED. Nothing of this proposal's own is left to build.
- **Still waiting, by earlier decisions:** the six deferred sections. Each reopens on its own
  condition: the first cohort verdict, the slippage measurement, a research-epoch boundary.
