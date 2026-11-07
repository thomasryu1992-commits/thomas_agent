# The H6 settlement day is read off the Toss transaction list

- **Why:** Toss's closed-order list returns `execution.settlementDate: null` on every filled order, so no window
  ever recorded a settlement and D-H6-7's settlement day could not occur.
  - Thomas chose the transaction list (`PORTFOLIO_CASH_FLOW_LEDGER_V0.1.md` §10, "A: 거래내역 API").
  - The read and its probe came first (#1397). On 2026-11-07 the probe showed the 2026-11-03 sell as
    `settlementDate 2026-11-05`, `settled True`. All nine rows carried the `YYYY-MM-DD` date.
- **What changed (`holdings/cash_flows.py`):**
  - Each Toss fire reads the sells from the transaction list, from the day before the window's first KST day, with
    no upper bound. An unsettled sale shows under its expected date.
  - `reconcile_window(..., transactions=)` records one settlement entry for a non-cancellation `SELL` whose
    `settlementDate` is the window's KST end day, in a pocket's currency. The order list's own settlement path
    stays as it was. The ledger keeps a `tx:` hash, never the row.
  - A window records `transactions_read`. A refused, failed or truncated read, or a feed without the read, is
    `false`. What that blocks:
    - the window's day as settlement evidence (`toss_evidence` spoils the day);
    - the v3 small-inflow income rule, which needs "nothing settled". It declines as `transactions_unread`.
  - What it does not block: buy and sell evidence, the residual reconciliation, and round deposits and
    withdrawals.
  - The settlement diagnosis gains the reason `transactions_unread`. The door's enum follows the list.
  - The reconciliation digest now runs every case over fixed transaction rows, so the behaviour change shows in
    it.
- **Shaped this way on purpose:**
  - **No order matching.** A transaction carries no order id, and a domestic unsettled row can become several
    settled rows. The evidence counts windows on a day, not orders, so identity across reads does not matter.
  - **`settled` is not required.** Whether the flag flips on the day itself is not observed yet. The expected
    date of an unsettled sale is what tells the fire, on the day, that the sale settles then.
  - **Narrow failure.** A read failure withdraws only what depends on the list. Fills, residuals and round
    flows are untouched.
    - A window with a small inflow and an unread list does stay open (`transactions_unread`). That is one Toss
      exception for Thomas to resolve, and it is fail-closed on purpose.
    - The same holds on a real settlement day. Before this, no window ever recorded a settlement, so v3 closed
      every small inflow as income. From now on a small inflow on a settlement day stays open: one exception,
      resolved at Thomas's terminal.
- **Known gap, not built:** no `settlement_revised` amendment for transaction entries.
  - An unsettled sale is recorded under its expected date. If it actually settles on another day, the earlier
    entries stand and that day can count as a settlement day.
  - The entry's hash includes the date, so a later read cannot be matched to withdraw it.
  - Domestic T+2 is deterministic, so the risk is low. The spec warns that US dates can differ.
- **Next:** after this deploys, Thomas starts epoch 1 (`--semantics-epoch`). Epoch 0's frozen counters hold no
  clean settlement day and can never pass. Epoch 1 gathers a buy, a sell and a settlement day again.
