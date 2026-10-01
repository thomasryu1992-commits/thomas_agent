# The strategy pipeline scores 4/10, and the cohort report can now put each timeframe beside its coin-flip twins

- **What was asked:** score how this runtime builds crypto strategies, then write the steps that raise
  the score in order and start them.
- **The score:** 4 of 10. The machinery that rejects strategies scores 9; the strategies it is
  handed score 2 to 3. No forward member is confirmed, and the members do not beat their coin-flip
  twins: on 2026-10-01 the members net −0.295R per trade at 4h against the twins' −0.183R, and
  +0.388R at 1d against +0.629R.
- **A recommendation withdrawn before it was written down:** the first answer put "fix the stop width
  against the cost" first. `BUILD_HISTORY.md` 2026-08-04 already measured that: a wider `stop_atr`
  halves the cost per R and leaves gross where it was, so it shrinks a loss and cannot make a profit.
  The proposal says so in §2 so the idea is not raised again.
- **What was built:** `scripts/forward_cohort.py report --arms`, a per-timeframe table of the
  members' and the twins' mean net R per trade.
  - The rows are the ones the judge prices, with members resolved as `report --detail` resolves them
    and twins as `forward_cohort_null.null_report` judges them, from the active null arm only. A test
    seals a superseded twin's row and checks it is not counted, and that the same row under the
    active id is.
  - Display only. The board's `arm_comparison` and the judge are untouched. This is the precedent
    Thomas set on 2026-09-30 for read-only report columns (gap analysis Q1).
- **A number corrected along the way:** the twins' figure first quoted in conversation (−0.098R) read
  the whole null outcome store, which still holds the superseded v1 arm's rows. The active arm reads
  −0.062R pooled.
- **What was proposed:** `docs/proposals/CRYPTO_STRATEGY_EDGE_ORDER_V0.1.md` (DRAFT). Each step is
  sorted by what gates it.
  - Tilting mint share to 1d waits for the first cohort verdict, when D3 lifts.
  - Ranking by the twin and fewer families wait for an epoch boundary.
  - Maker entry is Thomas's to reopen.
  - S1–S3 are the decisions.
- **Deliberately not done:** no factory, judge, board, ranking or constant changed.
