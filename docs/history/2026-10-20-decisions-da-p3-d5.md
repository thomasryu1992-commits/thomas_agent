# Three decisions after the research pause was lifted: US Treasury share, P3's date, D5's date

- **What (Thomas 2026-10-20, from a four-question round):**
  - **D-A:** a quarter of the bond 37 (9.25) goes to 7–10y US Treasuries, unhedged. `TARGET_PCT` is unchanged
    because the board judges bonds as one class.
  - **P3:** the "research pause ends (2027-03-22)" condition is removed, together with the D3 lift. Every other P3
    condition stays, FORWARD_CONFIRMED ≥ 1 among them.
  - **D5:** the date becomes a condition. (a)(b)(d) are asked when the first FORWARD_CONFIRMED lineage exists, and there
    is no LIVE_AUTONOMOUS before then.
- **Why:** D-A follows the D-C step 1 measurement (#1262), where the quarter-unhedged variant had the smallest drawdown
  increase. P3 and D5 carried the date only because of the pause that was lifted the same day.
- **What this approves:** nothing operational. No order path, no stage climb, no gate reopening, and no constant
  change. `CLAUDE.md`'s D3 bullet is updated so it no longer says P3 and D5 keep the date.
- **Not edited:** dated records that state the 2027-03-22 gates as they stood when written. The target architecture
  map gets a status-line correction only.
