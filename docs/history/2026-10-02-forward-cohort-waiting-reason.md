# What each unjudged cohort member is waiting on, on the board

- **Why:** the residual safety review (PR-E) asked for forward observability without touching a threshold. The
  maturity shows how far a member has got (EXPLORATORY, MATURE, CONFIRMED, CONTRADICTED), but not what stands
  between it and a verdict.
  - EXPLORATORY covered three different waits: a member no walk had reached, one walked with no trade closed,
    and one a few trades short.
  - MATURE covered two: too few slices, and an interval that spans zero.
- **What changed (display only):**
  - `forward_cohort.waiting_on(line, walked=)` names the wait from the judge's own numbers and the walker's
    book. The values are `NOT_YET_WALKED`, `NO_SIGNAL`, `TRADE_FLOOR`, `SLICE_FLOOR`, `NO_SPREAD`,
    `CONFIDENCE_BOUND` and `UNKNOWN` (book unreadable, or no slice width). It is None once a member is
    CONFIRMED or CONTRADICTED.
  - `cohort_report` carries it per member, and `board_summary` counts it.
  - The board adds a line, `대기 …`, under the leaders.
  - `scripts/forward_cohort report` appends it after the status.
  - Maturity, status, the judge, the LIVE door and K are untouched. D3-1 allows measurement and display.
- **On the live store, 2026-10-02:** `대기 신호 없음 78 · 거래 하한 108 · 신뢰 하한 5`, plus 6 contradicted. 78
  of the 186 EXPLORATORY members have not closed a single trade.
