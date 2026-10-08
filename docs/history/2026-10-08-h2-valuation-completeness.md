# H2: the holdings total is a portfolio NAV only when the declared scope is all in it

- **What this PR changes:**
  - `holdings/combined.py`:
    - A declared portfolio scope, `PORTFOLIO_SCOPE` v1: Toss, Binance futures, Binance spot, Binance
      Simple Earn.
    - The single `complete` is split into `source_fetch_complete`, `coverage_complete`,
      `valuation_complete`, `freshness_complete` and `portfolio_nav_complete`. `complete` stays as an alias
      of the last.
    - `sources` gives each declared source and why it is out of the total. `checks` gives PASS, FAIL or
      NOT_EVALUATED for coverage, valuation and freshness (required) and for coherence and reconciliation
      (H4's, not required yet).
    - The peak file carries the scope version. A peak from another scope is never compared against.
  - `holdings/binance_wallet.py`: the snapshot reports `earn_truncated` and `invalid_rows` as fields.
    Before, they were warning text only, beside `unpriced_assets`.
  - `holdings/allocation.py` follows the same verdict.
  - `holdings/board.py` shows `portfolio NAV` only when it is complete, and otherwise what failed.
  - `holdings/store.py` passes the Toss parse-warning count.
  - Tests: `tests/test_mvp_runtime_holdings_nav_completeness.py` (new). The P2 and wallet tests now
    build a complete scope. Docs: the H2 decision and appendix C in `MULTI_ASSET_EXPANSION_V0.1.md`.
- **Why:** the first read-only probe (H1-b, the same day) found non-zero spot assets and flexible Earn
  positions. The board still said `complete: true`, because "complete" meant "every source whose gate is
  on answered", and the wallet's gate is off. That is a statement about the readers, not about the money.
  Thomas decided (D-H2-1…8) that completeness is judged against a scope he declares.
- **Shaped this way because:**
  - **Scope is declared, not discovered.** Whether spot holds anything can only be learned by reading
    it, which is what the gate keeps off. And a reader that exists in the code must not widen the
    portfolio by itself.
  - **Checks that are not built yet are not failures** (D-H2-6). Otherwise the NAV would stay incomplete
    until H4 whatever H2 proved.
  - **No partial sum is produced** (D-H2-7 allows one with a label). The parts are each already on the
    board, labelled. A sum of some of them is the number most easily read as the whole, and the stored
    snapshot reaches Telegram and Hermes.
  - **The peak is scoped.** Comparing a total over a wider scope with a peak over a narrower one reads
    as a gain. The reverse reads as a drop.
- **What it deliberately does not do:**
  - It does not switch the wallet on, and it does not give scheduler-maint the read key. Thomas kept H3
    and H4 as preconditions.
  - So the board is INCOMPLETE from this deploy until the wallet is on. The -20 % drawdown alert and the
    allocation band verdict stay silent meanwhile. Thomas chose that knowingly.
  - It adds no source, no new gate and no alert.
- **Numbers:** the first probe found 12 non-zero spot assets and 11 flexible Earn positions. Locked Earn
  had 0. These are counts only; no amount was recorded.
