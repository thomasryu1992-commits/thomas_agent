# The quiet-window rule v3: a small inflow where nothing traded is deposit interest

- **Why:** on 2026-11-03, `--flows` showed two open Toss exceptions. Both were small KRW inflows that v2 left open
  as `too_small`: windows `2026-10-29T17:34:01Z..18:34:11Z` and `2026-11-03T04:33:53Z..05:34:16Z`.
  - Thomas said they were interest ("이자 같은거").
  - Toss has no cash-ledger API, so a residual's meaning can only come from its window's shape. Without a rule,
    every interest credit needed his hand.
  - He approved the INC1–INC4 defaults ("기본값대로 승인", Thomas 2026-11-03). The record is
    `HOLDINGS_STATUS_READ_AND_AUTO_FLOWS_V0.1.md` §3.2b.
- **What changed** (`holdings/cash_flows.py`):
  - **INC1:** v2's `too_small` branch now has one exception. If the KRW residual is an inflow and nothing filled or
    settled in the window (`_traded`), the rule closes it as `income`. Every earlier v2 condition still applies
    first: an untruncated, stable order read, no exchange pair, no open order at either end, KRW open, USD
    untouched.
  - **INC2:** a small outflow, or a small inflow beside a fill or a settlement, still declines as `too_small`. Either
    could be a fee or a settlement remainder.
  - The rule id is `rule:quiet_window.v3`. `RULE_IDS` keeps v1's and v2's lines the rule's, for re-closing,
    telling and the flows view.
  - An income line records the whole inflow as `rule_amount_krw` and `"0"` as `rule_remainder_krw`, on the LOCAL
    ledger only.
  - **INC4:** `rule_flows_untold` counts `income` beside deposits and withdrawals. The Telegram notice
    (`holdings/store.py`) says "입금 a건·출금 b건·이자 c건" and names the rule version from the constant. It carries
    counts only.
- **Not changed:**
  - **INC3:** the two exceptions open before the deploy stay for Thomas to close by hand. The rule judges a window
    once, when it is recorded.
  - An income-closed window has no fill or settlement, so it is never a problem window. The trade evidence before
    it stands.
  - `income` is not a unit flow (`unit_shadow.UNIT_KINDS`). It counts as the account's own return.
  - `reconcile_window` and the digest are unchanged, so no new epoch is needed.
- **Checked:**
  - A +812 inflow in a quiet window closes as income: amount 812, remainder 0, the notice count `income: 1`, no
    exception left.
  - The same inflow beside a fill, or beside a settlement, stays open as `too_small`. Removing the `_traded` check
    fails both cases.
  - A −812 outflow stays open.
  - An income window keeps the earlier buy's evidence (`buy_explained` 1, `problem_windows` 0) and adds no unit flow.
  - v1 and v2 resolutions are still the rule's.
