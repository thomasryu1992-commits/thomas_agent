# Total asset allocation: the 5/25 bands (Q2) and the BTC risk cap (Q4) are no longer P2 limit candidates

- **What this PR changes:** `docs/proposals/TOTAL_ASSET_ALLOCATION_V0.1.md` (status line, a dated addendum under Q4) and
  `docs/proposals/MULTI_ASSET_EXPANSION_V0.1.md` (status line, §5 P2 row, P2 decision section). `STATUS.md` is regenerated.
  No code, no schema, no constant.
- **Why:** At the P2 decision Thomas answered "no weight limit" for the Binance margin. The record read that answer narrowly and
  kept Q2 and Q4 open as further P2 limit candidates. After P2 shipped, Thomas extended the answer: "비중 한도는 Q2·Q4도 빼는 걸로"
  (2026-10-07). The system builds no asset-class weight limit. P2 is the account-wide drawdown alert alone (-20% from the peak).
- **What stays:**
  - The Q2 bands remain Thomas's own manual rebalancing rule.
  - The Q4 cap remains the reason BTC is 2.5% (Q6).
  - The Q9 drift display (display only, §11.1) is unchanged, and there the bands are a display reference, not an alert.
- **Reopening:** putting a weight limit into the system needs a new decision.
