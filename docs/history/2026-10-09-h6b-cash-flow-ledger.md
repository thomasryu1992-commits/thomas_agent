# H6b: the shadow cash-flow ledger — every flow recorded once, nothing applied

- **What this PR changes:**
  - `holdings/cash_flows.py` (new). Each holdings fire:
    - reads the nine Binance cash-flow histories and appends what it has not seen to the hash-chained,
      local `holdings_cash_flows.jsonl`;
    - computes the Toss per-pocket residual against closed-order fills and records it, SHADOW_ONLY.
  - **Rules:**
    - `source_event_key` makes every event exactly once.
    - The first read reaches back 7 days (Thomas). Each later read overlaps a day.
    - Pending rows hold the cursor back until they settle. A later failure gets a `flow_reversed` line.
    - Per source, `access` and `schema` are tracked. Schema starts `UNVERIFIED_NO_ROWS`. A row that
      does not fit is a HELD `malformed_source_event`, and its source becomes `MISMATCH`.
    - A look-alike from another source within 10 minutes is HELD as `possible_duplicate`, never merged.
    - Pay and fiat card/bank payments are HELD until their meaning is seen on a real row.
    - The histories share a 20 s read budget inside the maintenance pass.
  - `holdings/chained_log.py` (new): the chain rule, shared with the H5b baseline log. Its hash is
    unchanged, so the existing log still verifies.
  - `holdings/combined.py`: a spot↔futures transfer between the futures file's time and the wallet
    read's time fails coherence for that fire. So does a fire where the transfers could not be read,
    while both sides are counted. The block carries `internal_transfer_in_window`.
  - `holdings/store.py` and `board.py`:
    - The fire runs both passes. A failure costs the fire only its ledger update, plus coherence when
      the transfers are unknown.
    - The board shows counts.
  - Tests: `tests/test_mvp_runtime_holdings_cash_flows.py`.
- **Why:** H6b of `PORTFOLIO_CASH_FLOW_LEDGER_V0.1.md`, in the scope Thomas set (D-H6-5–10). The ledger
  and its reconciliation come first. Units, a NAV per unit and the drawdown switch wait for H6c, after a
  shadow period. Every H6a read returned 0 rows, so nothing about a row's shape is assumed: the schema
  is earned by the first real row.
- **What it deliberately does not do:**
  - No unit, no NAV per unit and no change to the drawdown. `--reset-peak` stays.
  - No alert and no `--resolve`. Every line says `accounting_mode = shadow`.
  - It does not value a flow: everything is `UNVALUED`.
