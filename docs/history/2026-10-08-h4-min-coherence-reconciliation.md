# H4-min: snapshot coherence and Toss reconciliation join the NAV's required checks

- **What this PR changes:**
  - `holdings/combined.py`: `REQUIRED_CHECKS` is now all five checks.
    - **coherence:** the included sources' own times (`source_as_of`: the Toss read, the futures
      snapshot file, the wallet read) must lie within `MAX_SNAPSHOT_SKEW_SECONDS` (30 min) of each other.
      A missing or unreadable time fails.
    - **reconciliation:** `toss_reconciliation` compares each Toss market's overview subtotal with the
      sum of its items, within 0.05 % or one minor unit. A missing value, an item in the other currency
      or an uncomputed result fails.
    - New block keys carry times and failure codes, never an amount.
  - `holdings/store.py` passes the Toss read's time and its reconciliation result.
  - `holdings/board.py` names the skew or the failure codes when either check fails.
  - Tests: `tests/test_mvp_runtime_holdings_coherence.py`. The P2, H2 and wallet helpers now pass a
    coherent, reconciled fire.
- **Why:** D-H2-5 reserved these two checks for H4. Thomas asked for the minimum before the wallet is
  switched on. A NAV must not add figures from different moments, and the Toss total must not disagree
  with the holdings it claims to sum.
- **Shaped this way because:**
  - **30 minutes.** The futures file is written every 15 minutes, so a normal fire sees about 15
    minutes of spread. The limit allows one missed write and no more. It is Claude's default, and
    Thomas may change it.
  - **Only Toss is reconciled.** It is the only source that gives both an aggregate and its items. The
    futures margin and the wallet's class totals are not sums of items this lane reads.
- **What it deliberately does not do:** it does not switch the wallet on. It adds no unitized NAV, no
  cash-flow accounting and no instrument master. The board stays INCOMPLETE (coverage) until the wallet
  activation, which is a separate approved step.
