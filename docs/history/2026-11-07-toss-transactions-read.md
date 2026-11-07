# The Toss transaction list joins the feed, first as a probe, as the H6 settlement-day source

- **Why:** the `holdings_status` v1.3 settlement diagnosis (#1393, 2026-11-07) showed two reasons H6 cannot become
  ready.
  - **Epoch 0.** Toss semantics is still in epoch 0. The pre-H6d counters frozen at the import judge it, and they
    hold zero clean settlement days. So `toss_cash_semantics` cannot PASS, whatever new windows gather. The way out
    is Thomas's `--semantics-epoch`, not code.
  - **No settlement records.** Over 14 days the windows recorded no settlement at all: 24 windows a day, 0
    settling, 1 sell explained. A probe of the closed-order list found `execution.settlementDate: null` on every
    filled order (5 of 5), including fills from 2026-10-27 that settled long ago. Spec v1.2.28 shows a date there.
    The live answer does not.
  - Thomas chose option A, the transaction list (`GET /api/v1/transactions`, spec v1.2.28): "A: 거래내역 API".
    It was absent from v1.2.24, which the ledger design was written against. Record:
    `PORTFOLIO_CASH_FLOW_LEDGER_V0.1.md` §10.
- **What changed (this PR, no judgement moves):**
  - `TossHoldingsFeed.transactions(...)` is a GET with the account header and cursor paging like `closed_orders`.
    It takes an optional type filter, and `to` may be omitted so unsettled trades come back. Unknown types pass
    through.
  - `scripts/probe_cash_flow_sources.py` also reads it. It prints counts by type and settled state, the shape of
    `settlementDate`, and, for sells only, each one's settlement date, settled flag, asset class and currency.
    It never prints a symbol, ISIN, quantity, price or amount.
- **Shaped this way on purpose:**
  - **Probe before judgement.** The order list's spec example carried a settlement date and the live answer did
    not. The transaction list's real shape is confirmed before the settlement day moves onto it (the next PR,
    which changes the reconciliation digest).
  - **The epoch waits for that PR.** The 0→1 transition is exempt from the digest-must-differ rule once. Starting
    epoch 1 before the settlement change would let that change move the judgement inside the epoch, and the buys
    and sells gathered would have to be gathered again in epoch 2.
  - **No order matching.** Transactions carry no order id, and a domestic unsettled row can become several settled
    rows. The settlement day will be read as "a settled sell with this `settlementDate` in this currency", never
    tied to an order.
