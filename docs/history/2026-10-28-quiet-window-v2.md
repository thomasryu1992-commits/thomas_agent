# The quiet-window rule v2: a deposit beside a buy is still a deposit

- **Why:** v1 closed a Toss residual only in a window with no fill and no settlement, and only when the residual
  was a whole multiple of 1,000 KRW.
  - Thomas's 2026-10-27 deposit failed on `not_round`. A buy filled that morning, and an adjustment seems to
    have landed beside the deposit after the close (guessed, not confirmed).
  - With G6 buying on trading days, a deposit and a buy on one day will be common.
  - Thomas: "승인: 입금 자동 정리 규칙 v2(Q1 체결 구간 포함, Q2 1만 원 단위, Q3 끝전 허용 체결액
    0.1%+100원·상한 1,000원, Q4 미체결 주문 구간 제외, Q5 나머지 조건 유지·금액은 원장만) 설계·구현
    (Thomas 2026-10-28)". The record is `HOLDINGS_STATUS_READ_AND_AUTO_FLOWS_V0.1.md` §3.2a.
- **What changed** (`holdings/cash_flows.py`):
  - The verdict now takes `open_orders` (the counts at the window's two cash points) and `day_fills_krw`.
  - A fill or settlement no longer excludes a window: the fills are already out of the residual.
  - "Round" means this: the nearest 10,000 KRW is not zero (`too_small` otherwise), and the remainder is within
    0.1% of the KRW filled on the window's KST day(s) plus 100 KRW, at most 1,000 KRW (`not_round` otherwise).
  - An open order at either cash point declines the window (`open_order`). So does a count that could not be
    read; the first window after the deploy has none for its start.
  - The resolution line carries `rule_amount_krw`, `rule_remainder_krw` and `rule_tolerance_krw`. They are on the
    LOCAL ledger only, never on `--flows`, Telegram or the door.
  - The rule id is now `rule:quiet_window.v2`. `RULE_IDS` keeps v1's lines the rule's for re-closing, telling
    and the flows view.
  - A line written before the decline field existed is derived by v1, the rule then in force.
- **The feed** (`TossHoldingsFeed.open_orders`): a GET for `status=OPEN`, which Toss returns whole and unpaged.
  `collect_toss` keeps only the count, on the state's cash point. The no-order-capability test now names two
  list reads.
- **Not changed:**
  - A fill window closed as a deposit is still no trade evidence (`problem_windows`), as when Thomas closes it.
    `toss_cash_semantics` evidence starts again after it, though not as MIXED.
  - `reconcile_window`, the reconciliation fingerprint and the digest are unchanged, so there is no new epoch.
  - The Telegram notice gives direction and count only, and now names v2.
- **Fixtures moved:** the MIXED fixtures in `h6d_toss` and `h6d_resolution` used a round +50,000 beside a buy,
  which v2 now closes. They use +53,000, a remainder beyond the tolerance, so they still test MIXED and Thomas's
  close.
- **Checked:**
  - v2 closes a deposit beside a buy, and the remainder is kept on the ledger only.
  - The tolerance edges hold at 200/201 and at 1,000/1,001 (the cap). The day's fills from an earlier window
    count; USD and other days do not.
  - A settlement window closes.
  - An open order at the start or the end, or an unread count, declines the window.
  - The seven declines are named.
  - v1 lines stay the rule's, and a rule-closed fill window is no trade evidence.
  - The feed reads `status=OPEN`.
  - 16 mutations were each caught.
