# OI lookahead re-score, 4h: the edge vanished; and the measurement script stops hoarding snapshots

- **Result** (pre-registered in `CRYPTO_ARCHIVE_BACKFILL_V0.1.md`, "결정 (Thomas 2026-10-20)", judged on F):
  - The run was official: the research sandbox, main `560032dc`, lake `20261021T041701Z`, run
    `20261021T043019Z-3265090`.
  - 103 4h candidates, none starved.
  - L (legacy alignment): 13 CONFIRMED, pooled mean +0.135R, period lower bound +6.91.
  - F (fixed alignment): 0 CONFIRMED, pooled mean +0.018R, period lower bound −44.83.
  - Reading: **VANISHED**. The full table and the family split are in the proposal.
- **Alignment checked on the frame itself.** On BTCUSDT 4h bars of 2026-10-15:
  - L reads the change labelled 10-15. That is the day's close, known only at 10-16 00:00.
  - F reads the change labelled 10-14.
  - F moves to the 10-15 value at the 10-16 00:00 bar. So F is the point-in-time reading, shifted exactly 24 h.
- **Script fix:** `measure_oi_lookahead` kept every snapshot of a timeframe cached. A 1h snapshot holds 24,000
  bars plus its reference and cross-section legs, so the first official 1h run died at the sandbox's 700M cap
  (exit 137, run `20261021T043506Z`).
  - Fix part 1: the cache now drops a snapshot once no context still ahead needs it. The pooled 4h context
    still reuses the singles.
  - Fix part 2: each context's legs are released before the next snapshot builds.
  - Test: tracks the snapshots that are alive with weak references, at each replay and at each build.
    Removing either part fails it.
  - A preliminary 1h run on the patched branch finished under the cap in 8 minutes. The official 1h run follows
    this merge, and its result goes under the 4h one.
- **Why per timeframe:** `summarize` sums the period subtotals of every block it is handed. A combined
  `--timeframe 4h --timeframe 1h` run would mix the two timeframes' periods, so each reading comes from its own run.
- **1h, official (added after #1278):** VANISHED.
  - Same lake (`20261021T041701Z`), main `217d39c1`, run `20261021T050751Z-3301259`. It matched the preliminary run
    to the digit.
  - L: +0.093R, 4 CONFIRMED. F: −0.053R, 2 CONFIRMED, none of them L's.
