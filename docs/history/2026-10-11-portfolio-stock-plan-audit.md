# An outside portfolio + Toss stock-trading plan is audited against the record; Q1, Q2, Q4, Q5 decided, Q3 waits on a C7 packet

- **What this PR adds:** `docs/proposals/PORTFOLIO_AND_STOCK_TRADING_PLAN_AUDIT_V0.1.md`. It audits a pasted plan with 15 parts and
  6 requested documents against the record, and adds one line or section to each document that owns a decision.
  - `HOLDINGS_LEDGER_CHECKPOINT_V0.1.md` §12.1: the C7 D1–D4 decision packet. For each item it gives the original question,
    the recommendation, the alternatives, the impact, the basis against ledger loss, the rollback impact, and the checks before
    implementation.
  - `PORTFOLIO_CASH_FLOW_LEDGER_V0.1.md` §7: C7 becomes an H6c precondition.
  - `MULTI_ASSET_EXPANSION_V0.1.md`: the P3 row records Q1 and Q2. Appendix B points to the Toss order-scope table.
  - `EXECUTION_STAGE_V0.1.md`: the canary rung was asked for a fourth time and not approved.
- **Decisions (Thomas 2026-10-11):**
  - Q1, no: P3's gate (2027-03-22 and one crypto FORWARD_CONFIRMED) stays.
  - Q2, no: `stocks/` stays rejected and stocks come in through the three seams. Stock strategy, hours, settlement, order
    policy and risk checks are validated on their own. No crypto approval or execution authority carries over to stocks.
  - Q3, conditional: C7 as an H6c precondition is agreed. D1–D4 wait on the packet.
  - Q4, yes: the fourth canary request is on record.
  - Q5, no: none of the six new documents.
- **Why the audit is shaped this way:**
  - The plan's flow-adjusted performance and investment drawdown already exist as H6 unit accounting (D-H6-4, decided, not
    built).
  - C7 already had a design, pinned defects and D1–D4.
  - The stock half of the plan met four recorded decisions. Writing six documents would have duplicated owners or decided
    against the record.
- **Facts it rests on:**
  - The Toss public spec `openapi.json` v1.2.24, read 2026-10-11. No endpoint was called. It shows:
    - the order create, modify and cancel APIs and conditional orders;
    - `scopes: {}`, so the read key is the order key;
    - no sandbox server;
    - one valid token per client.
  - The synthetic raw-versus-unit drawdown table (§A.2) is the design formula, computed separately, not repository output.
- **What it deliberately does not do:**
  - No code, schema, policy, constant, schedule or stage change.
  - No C7 implementation.
  - No LOCAL_ONLY read.
  - No merge or deploy.
