# The holdings board is wired: scheduler-maint reads KIS hourly, the doors render the aggregate it stored

- **What this delivers:** P1-b of `docs/proposals/MULTI_ASSET_EXPANSION_V0.1.md`. It has four parts:
  - `holdings/store.py`: the snapshot store, on `crypto/account_store.py`'s shape.
  - A new maintenance kind, `holdings_refresh`.
  - The `/holdings` operator verb.
  - A dormant assistant read, `holdings_status`.
  The six KIS variables are forwarded to `scheduler-maint` and to no other service. The secret
  ownership matrix, `docs/DEPLOYMENT.md`'s secret table and the deployment tests all name it.
- **Why scheduler-maint (Thomas 2026-10-02):** a holdings read is maintenance, because a late fire
  costs freshness and never money. The risk lane holds the Binance order key, and putting a second
  order-capable key there (KIS has no read-only scope) would widen the one container that may trade.
  The doors run in `operator` and `read-bridge` and hold no key. They render the file the lane wrote,
  as `/crypto funds` renders `account_store`'s.
- **The boundary is in the file, not in each reader:** the snapshot stores `board.aggregate_view` and
  three stamps, nothing else. No symbol or per-symbol number exists on disk, so every door that
  renders it is inside the external-send boundary (appendix A, art. 5(3) of the KIS terms) by
  construction. The terminal's full board stays a live read (`scripts/holdings_board.py --full`).
- **One token per process:** KIS notifies the account holder on every token issuance, and only
  factory fires fork, so the store keeps the capable feed, and its in-memory token, between fires.
  That comes to about one notice a day at an hourly cadence. The cache drops when the gate stops
  selecting the feed.
- **What changed from P1-a's claims:**
  - "Nothing in `runtime/` imports holdings" is now "the scheduler fire and the console verb, both
    function-locally, and nothing in `crypto/`". The test is exact in both directions and was
    mutation-checked.
  - "Persists no record" is now "persists a state file on `account_store`'s precedent", which is not
    a ledger record, so no closed schema is owed.
- **What it does not do:**
  - The assistant's read is dormant. The policy's closed `assistant_read.verbs` list does not name
    `holdings_status`, so the door refuses it by name. A policy line opens it, and Hermes also needs
    a shim for any new tool name.
  - The schedule is per-machine state, registered by the operator after the deploy.
  - `holdings_refresh` is in `schedule_delegation.FINANCIAL_KINDS`, so the assistant cannot change
    its cadence.
