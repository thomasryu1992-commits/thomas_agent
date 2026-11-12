# The allocation's review triggers get a tracker (2026-11-11)

**Decision:** Thomas 2026-11-11, "기본값대로", recorded in `docs/proposals/TOTAL_ASSET_ALLOCATION_V0.1.md`
("결정 (Thomas 2026-11-11) — 재검토 조건").

## Why

The rebalancing review found that none of the allocation's review triggers was tracked. Q1 re-asks the growth mix after
the first -15% drawdown is held through, but the only drawdown notice is P2's -20%, on the raw total that deposits and
withdrawals move. Q7 waits for an "annual review" that had no date. Q3's conditions are mostly outside events.

## What changed

- **Annual review:** every October from 2027, the month Q1-Q10 were decided. `store.band_notice` appends one line to
  that month's band notice (both the judged and the refused text). No new schedule; the notice's own once-a-month,
  after-delivery rule carries it. The line names questions (Q7, Q3), never a class or an amount.
- **Q1's -15%:** not built here. It is HQ7 of the H6c packet, because the right drawdown is the unit one, whose
  authority H6c decides. Built with H6c if Thomas answers A.
- **Q3's financial-income condition:** not built. Thomas checks the year's total at the annual review; automating it
  needs a yearly aggregate of interest, dividends and taxed ETF sale gains, which would be LOCAL_ONLY.

## Checks

- `tests/test_mvp_runtime_holdings_band_notice.py`: October 2027 and 2028 carry the line; November 2026, September and
  November 2027, and October 2026 do not; a refused October still carries it.
- Mutations (no first-year floor, any month, either branch dropping the line) each turn a test red.
