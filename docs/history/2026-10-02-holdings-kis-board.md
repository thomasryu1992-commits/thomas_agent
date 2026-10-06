# The holdings lane reads a KIS account behind its own gate: aggregate out, positions only on the terminal

- **What this delivers:** P1-a of `docs/proposals/MULTI_ASSET_EXPANSION_V0.1.md`, the read-only
  multi-account board's first account. It adds `runtime/mvp_runtime/holdings/` (`kis_account.py`, the
  feed; `board.py`, two renders), `scripts/holdings_board.py`, and `tests/test_mvp_runtime_holdings.py`.
  It is gated by `MVP_KIS_ACCOUNT=kis` (provider id `kis_account`). Credentials (`KIS_APP_KEY`,
  `KIS_APP_SECRET`) and the account (`KIS_ACCOUNT_NO`, `KIS_ACCOUNT_PRODUCT_CODE`) are read at call
  time, and `KIS_SERVER` is `real` (the default) or `demo`.
- **Why it is not behind `select_account_feed`, as the proposal's §2 had assumed:** `MVP_ACCOUNT_FEED`
  is the live money path's account. `live_route.py` and `account_store.py` read it, and the daily-loss
  breaker meters it. So this capability has its own gate and its own package, and a test pins that
  nothing in `runtime/` outside `holdings/` imports it. That makes D4's "no door reads it" an
  import-graph property. The proposal's §2 now carries the correction.
- **The external-send boundary, as code (appendix A, Thomas 2026-10-02):** `board.aggregate_view` has
  an exact key set: totals by asset class, unrealized P&L, a count, weights, and freshness. It carries
  no symbol, name or per-symbol number, because a per-symbol value over its quantity is a price, and
  art. 5(3) of the KIS terms bars giving quotes to a third party. The script defaults to the
  aggregate, and `--full` prints positions for the account holder's terminal.
- **Secrets and load:** one token per process, held in memory and never on disk. KIS sends a KakaoTalk
  notice per issuance, so each one-shot run costs one notice. A token rejection is one attempt and
  never a retry, because issuance is limited to about once a minute. Pagination is capped at five
  pages. No key, secret, token or full account number appears in any render or error, and each of
  those is tested.
- **What it deliberately does not do:**
  - It deploys nothing. No compose service carries the KIS variables, and wiring one is P1-b, a change
    to the secret-ownership matrix.
  - It persists no record, so no closed schema is owed.
  - It does not read foreign-currency cash. Which field holds it in KRW, and whether it overlaps the
    domestic deposit, is unverified, so every total is "known parts" and says so.
- **Unverified:** the response field names come from KIS's own sample column maps, not from a
  response seen here. Parsing degrades to `None` plus a warning, never zero. The first live run,
  by Thomas with the keys only Thomas holds, is the verification step.
