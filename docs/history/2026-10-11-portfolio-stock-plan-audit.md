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
  - Q3, conditional: C7 is an H6c precondition. D1–D4 were confirmed the same day, as design direction only:
    - D1 agreed, with five conditions;
    - D2 partly: a separate `history_completeness` and UNCHECKED were agreed, while the Toss proof and the
      cutover stay open (§12.2 options A, B, C);
    - D3 agreed;
    - D4 conditional on five verifications, with no change authority for the backup session.
  - Q4, yes: the fourth canary request is on record.
  - Q5, no: none of the six new documents.
- **Why the audit is shaped this way:**
  - The plan's flow-adjusted performance and investment drawdown already exist as H6 unit accounting (D-H6-4, decided, not
    built).
  - C7 already had a design, pinned defects and D1–D4.
  - The stock half of the plan met four recorded decisions. Writing six documents would have duplicated owners or decided
    against the record.
- **Facts it rests on:**
  - On transfers, the D2 investigation found:
    - Binance's official connector documents a 6-month history, with 7 days as the no-parameter default.
    - The code clamps every transfer read to the last 7 days.
    - Under that clamp, re-collection stops reaching `cutover − 1d` around 2026-10-15 04Z.
    - An older window has never been sent, so the venue side is not verified.
    - The packet's first draft wrote 10-16 and was corrected the same day.
  - Option A's feasibility review (§12.3):
    - One constant does two jobs: the per-request span cap in `flow_history` and the depth clamp in
      `collect_binance`. Changing it alone would also send unverified long spans.
    - A scratch prototype backfill was run against the real feed code on a fake venue: 18 synthetic cases
      passed, and 13 fail when the window is mutated to 8 days.
    - With a 175-day margin, A's practical deadline is about 2027-04-01.
    - The live probe is specified but was not run.
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
