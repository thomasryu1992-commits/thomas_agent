# Toss deposits and withdrawals are closed by rule: `quiet_window.v1`

- **Why:** every Toss deposit became an exception that only Thomas could close (`--resolve`). Toss has no
  cash-ledger endpoint: the official reference was re-read on 2026-10-26, 41 endpoints, none for deposits,
  withdrawals, transfers, dividends or history. Thomas decided F1 A and F2 "no amount" on 2026-10-26
  (`HOLDINGS_STATUS_READ_AND_AUTO_FLOWS_V0.1.md`).
- **The rule** (`cash_flows.quiet_window_kind`): a window with
  - no fill on either side of either pocket,
  - no settlement or exchange pair,
  - an untruncated, stable order read,
  - the KRW pocket open and USD untouched,
  - and a whole, non-zero multiple of 1,000 KRW

  is closed in the same write that records its residual. The `flow_resolved` line has
  `requested_by: rule:quiet_window.v1` and kind `deposit` or `withdrawal` by sign. Anything else stays an
  exception for Thomas.
- **Re-closing:** `--resolve` may now re-close a *rule's* resolution, once. The new line is
  `resolved:<key>:override` with `supersedes`. Thomas's own resolution is still final, and a second override is
  refused (`RESOLVE_DUPLICATE`, and the unique key refuses it too). `--flows` shows who closed each exception.
- **Notice (F2):** "[보유 자산] 토스 입금 N건·출금 M건을 규칙으로 자동 분류" carries the direction and count,
  never an amount. It is told once per flow and marked only after delivery, like the exception notices
  (`rule_flows_untold` / `mark_rule_flows_told`, scheduler `h6 rule flows told`).
- **Evidence:** a quiet window is not "active", so its residual voids no trade evidence. A buy before a deposit
  still counts toward `toss_cash_semantics` (tested).
- **Tests moved:** six existing tests drove the manual path with round quiet changes (1,050,000, 6,000,000, …).
  They now use non-round amounts, so they keep testing the manual path. The rule has its own tests.
- **Checked:**
  - Round deposits and withdrawals close by rule.
  - A non-round amount, a buy in the window, a USD move or a truncated read leaves the exception open.
  - One re-close is allowed and a second is refused.
  - Earlier evidence is kept.
  - Notices are told once; the delivery path is tested, with a failed send not marked told.
  - Six mutations were each caught. The "overrides without end" mutation is caught by the existing resolution
    tests and by the ledger's unique key.
