# 2026-11-08 — A transaction-list sale whose settlement date moves amends the windows that recorded it

**PR:** fix/h6-transaction-settlement-revised. **Closes:** the known gap in
`2026-11-07-h6-settlement-from-transactions.md` (`PORTFOLIO_CASH_FLOW_LEDGER_V0.1.md` §10).

## What

- `cash_flows._transaction_amendments` runs on every Toss fire whose transaction read is whole.
- For each non-cancellation sell in the read, it looks at the windows' recorded `tx:` settlement entries. It writes one
  `settlement_revised` line per window when the row is the same sale but now dated another day. That is the same
  amendment the order list already writes, and its spoiling of the day and the window is unchanged.

## Why it is shaped this way

- **Matching without a new field.** The entry's hash covers `settlementDate`, so a moved date changes the hash. The
  row is hashed again with the recorded day in its place; equal means the same sale. The window line, the
  reconciliation output and `reconciliation_digest` stay as they were, so no new epoch is needed and the windows
  epoch 1 already wrote are covered.
- **Absence proves nothing.** A sale the read no longer returns, a failed read and a truncated read amend nothing.
- **Not covered.** A cancellation row is not matched to the sale it cancels. The spec does not say whether such a
  row repeats the sale's fields, and no cancellation has been observed. A later real sale settling on its expected
  day is still the first live check of the whole transaction path.

## Tests

`tests/test_mvp_runtime_holdings_settlement_transactions.py`, five new tests:

- a moved date withdraws the expected day, one line per recording window;
- a repeat read adds nothing;
- a missing row, a failed read or a truncated read amend nothing;
- a different sale, a cancellation row, a buy, or the same date settled amend nothing;
- the line holds hashes only, and the digest still starts `40748837`.

The two that exercise the amendment fail on the code before this change.
