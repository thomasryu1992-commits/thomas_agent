# The quiet-window rule says why it left a deposit open

- **Why:** Thomas deposited 20,000 KRW on 2026-10-27. The deposit landed in a window after the market
  close, and `quiet_window.v1` did not close it. He closed it himself with `--resolve`.
  - Nothing recorded which condition failed. The residual line, `--flows`, the scheduler events and the
    Claude door all said only that the residual was open.
  - Thomas: "승인: quiet_window.v1이 닫지 않은 이유(조건 이름만, 금액 없음)를 원장과 --flows에 남기는 수정
    (Thomas 2026-10-28)". The record is `HOLDINGS_STATUS_READ_AND_AUTO_FLOWS_V0.1.md` §3.2.
- **What changed** (`holdings/cash_flows.py`):
  - `quiet_window_verdict` returns the kind, or the first condition that failed, by name, from a closed list:
    `orders_truncated`, `exchange_pair`, `identity_unstable`, `settlement`, `fill`, `krw_not_open`, `usd_moved`,
    `not_round`.
  - `quiet_window_kind` is now a thin wrapper around it, so the rule decides exactly as before. Only the reason
    is new.
  - A KRW residual line that the rule left open carries `quiet_window_declined`. A USD residual line, and a
    line the rule closed, carry nothing.
  - The validator refuses a name outside the list before the line reaches the chain.
- **`--flows`** prints `rule: <name>` after such a line, and never the amount.
  - A line written before this change gets its reason derived from its stored window, marked `(derived)`.
  - The stored amounts are rounded, so three cases look like a pass and show `undetermined`: a decline under
    one won, a USD change under a cent, and a window recorded before the rule was deployed.
- **A gap this makes visible:** the USD pocket opens above 0.01 USD, but the rule wants the USD residual under
  0.005. A change between the two opens no USD exception and still keeps the KRW window open. Before this
  change that failure was silent; now it reads `usd_moved`.
- **Untouched:**
  - the rule's decision itself;
  - `reconcile_window` and the reconciliation fingerprint and digest, so no new semantics epoch is needed;
  - the Claude door and the Telegram notice (direction and count only).
- **Checked:**
  - each of six declines is named on the line and in the view;
  - a rule-closed residual, a USD-only residual and a re-closed residual carry no reason;
  - an older line is derived (`not_round`, `undetermined`);
  - the board prints the reason without the amount;
  - an unknown name is refused;
  - the sub-cent USD case.
  - 12 mutations were each caught.
