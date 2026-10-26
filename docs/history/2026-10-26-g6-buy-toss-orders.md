# G6-buy: the first Toss order path — a buy scenario Thomas approved, placed once

- **Why:** Thomas put 200,000 KRW into Toss and asked for API buying (2026-10-26). G6 (automatic rebalancing)
  was due to be decided after H6c. Thomas opened its buy side now: "승인: G6-buy … (Thomas 2026-10-26)". He then
  narrowed it to a scenario he confirms first, chose scenario B, and set RB4'' (a whole-share buy may be under
  50,000 KRW). Record: `docs/proposals/G6_BUY_CASH_AUTOINVEST_V0.1.md`.
- **The order half:** `holdings/toss_orders.py`.
  - Its own gate is `MVP_TOSS_ORDERS=buy_only` (scheduler-maint only) with provider `toss_orders`.
    `TossBuyOrders` has one method, `buy_kr`, which sends `POST /api/v1/orders` with `side` as the constant
    `BUY`, a MARKET DAY order, a whole domestic quantity and a `clientOrderId`.
  - The gate is re-read before every POST.
  - It borrows the holdings feed's token, because Toss keeps one token per client. On `token-revoked` it
    reissues once and resends the same id.
  - Outcomes:
    - `OrderRejected` covers every 4xx except `request-in-progress`, plus 429. Nothing was placed.
    - `OrderUnknown` covers a timeout, a dropped connection, a 5xx or `request-in-progress`. The order may
      have been placed.
  - `TossHoldingsFeed` stays a read with no order method. The test that forbids order words on it caught a
    first name for the cash read (`buying_power_krw`), now `cash_krw`.
  - New feed reads: KRW cash, last prices, and the KR market calendar.
- **The executor:** `holdings/autoinvest.py`, a step of its own after a holdings refresh that landed.
  - `--autoinvest-approve` writes a pending scenario. It takes only current candidates' domestic codes, whole
    shares, at most 10 items, and a cap of at most 500,000 KRW. A scenario is valid for 7 days.
  - The fire makes one attempt per KST day, inside the regular session from start + 5 min to end − 30 min,
    taken from Toss's calendar.
  - Before any order it checks:
    - every item is an ACTIVE ETF;
    - every price can be read;
    - the estimate (price × quantity × (1 + the account's KR commission)) is within the scenario cap, the
      day's 500,000 KRW across scenarios (from the journal), and KRW cash.
    - Any miss holds the whole scenario to the next trading day. It never trims what Thomas approved.
  - The journal gets an `intent` line before each POST and its outcome after.
  - A `clientOrderId` already in the journal is never resent: a crash between intent and POST reads as
    unknown.
  - The first rejection or unknown stops the rest. A stopped scenario is not resumed; Thomas approves a new one.
  - Telegram carries counts and state only, told after delivery. Amounts and instruments are in the terminal
    `--autoinvest-status`.
- **RB4'':** `rebalance.plan` now places a domestic whole-share buy of one share or more whatever its amount.
  Amount buys, sells and manual lines keep the 50,000 KRW carry. The carry test was split in two accordingly.
- **Governance, in this PR:**
  - the env-gate call-site list gains `holdings/toss_orders.py`;
  - `_GATE_ENV_VARS` gains `MVP_TOSS_ORDERS`;
  - compose forwards it to scheduler-maint only, and the broker surface becomes five names;
  - CLAUDE.md: Claude never sets it and never runs the two new modes;
  - the hook covers the two modes and the two files;
  - decision lines in TARGET_ARCHITECTURE G6, TOTAL_ASSET_ALLOCATION and REBALANCING §6/RB4;
  - win32 skip ceiling 690 → 696 (six bash-only guard cases).
- **Not built:** sells, a scenario the system writes itself, US ETFs (no exchange endpoint; USD funding
  unconfirmed). P3 is unchanged.
- **Unverified until Thomas's first run:** Toss has no demo server. The request and error shapes are the
  official openapi.json v1.2.24, and the calendar's time format is parsed both as ISO and as HH:MM:SS.
- **Checked:**
  - Adapter tests: no opt-in, no construction and no POST; the exact POST body; the egress re-check;
    whole-share and domestic refusals; rejection vs unknown across nine answers; token reissue with the
    same id.
  - Executor tests: scenario B placed once; RB4''; session edges; a closed day; orders off; short cash then
    the next day; the daily cap; the scenario cap; a rejection stopping the rest; unknown; a crash after
    intent; non-ETF; refusals at approval; expiry; scheduler delivery and the failed-refresh guard; the store
    handing the gated adapter.
  - Sixteen mutations were each caught.
