# H6a: the cash-flow history reads, read-only, and a probe that prints counts

- **What this PR changes:**
  - `holdings/binance_wallet.py`: `FLOW_SOURCES` and `flow_history`. Nine history reads go over six
    signed GET paths. Each one is checked against Binance's own connector source:
    - crypto deposit and withdraw;
    - fiat deposit and withdraw (`/fiat/orders`), card and bank buy and sell (`/fiat/payments`);
    - Pay;
    - spot↔USD-M universal transfers.

    The allowlist grows by those paths. A window longer than the venue documents is refused before a
    socket opens. The rows stay in-process.
  - `holdings/toss_account.py`: `closed_orders` reads `GET /api/v1/orders?status=CLOSED`, paging by
    cursor, and reports whether it ran out of pages. The feed still has no order-writing method.
  - `scripts/probe_cash_flow_sources.py`: one read per source. It prints PASS with a row count, or the
    reason code with the venue's numeric code. For Toss it also reports which execution fields are
    present. It never prints a row.
  - Appendices B and C record the widening with its date. Tests:
    `tests/test_mvp_runtime_holdings_cash_flow_sources.py`, plus two pinning tests that now name the
    new reads.
- **Why:** the first step of H6 (D-H6-3, D-H6-10). Before a ledger exists, establish which official
  records the read-only credentials can actually see. A source that does not open is either required
  (an H6c blocker) or declared unused. It is never fixed by widening a key.
- **Found while checking:** `/sapi/v1/fiat/payments` (buying crypto with a card or a bank) is also
  an external inflow. The design's list of five had missed it.
- **What it does not do:** no ledger, no units, nothing stored, nothing on the board. Whether Toss
  `cashBuyingPower` moves with settlement is the D-H6-7 gate, which this PR does not open.
- **First probe (2026-10-08 16:4xZ, after the `candidate-1192` deploy):** every source returned PASS. On
  Binance that was the nine history reads on the read-only key (last 7 days); on Toss, the closed-order
  list (last 30 days). Every read returned 0 rows, so on live data the error codes are confirmed but
  the row shapes are not: the parsers rest on the documented shapes until the first real flow. No
  Toss order closed in those 30 days, so the execution fields and the D-H6-7 `cashBuyingPower` gate
  are still unobserved.
