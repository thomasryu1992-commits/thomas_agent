# The 2026-08-04 cost economics were quoted as current; re-measured, 1h no longer pays

- **Why:** an outside review read `promotion_backlog.py`'s comment ("1h +0.0241R — 1h pays now") and
  `REMAINING_WORK.md` §F as today's economics. It concluded the 1h cost problem was solved. Those
  were 2026-08-04 snapshots over 474 candidates.
- **Re-measured 2026-09-28** by §F's method: the median per candidate of `backtest_evidence`, over
  the 1,272 lineages on the current basis, which now carries funding. Results: **1h −0.0451R, 4h
  +0.0436R, 1d +0.1274R**, and no 15m candidate carries the current basis.
  - 1h friction barely moved (0.1230 → 0.1099R).
  - Its gross edge fell (+0.1495 → +0.0520R), so friction is now twice the edge.
  - 31% of 1h lineages are net positive, against 69% at 4h and 83% at 1d.
- **What changed:**
  - A dated re-measurement table in §F, plus a superseded note on §F's arrival summary.
  - A dated paragraph under the constant's comment in `promotion_backlog.py`.
  - A dated correction under `EQUITY_PERP_LANE_V0.1.md` §8b's quote.
  - The 2026-08-04 text is kept as it was: it is the reasoning of that day.
- **Deliberately not done:** no code or constant change (`BACKLOG_HORIZON` is anchored on an operator
  decision, not on which timeframe pays). The `dashboard.py` and docstring notes about 474
  candidates are left alone. They record the day the board could not say why its queue was zero,
  which stays true as history.
