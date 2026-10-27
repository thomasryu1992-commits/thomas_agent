# G6-buy drafts its own buy scenario; Thomas confirms it on his terminal

- **Why:** Thomas asked not to type scenarios by hand. He decided Q1-Q5 at their defaults: "승인: G6 매수
  시나리오 자동 작성(Q1~Q5 기본값: …) 설계·구현 (Thomas 2026-10-27)". The record is `G6_BUY_CASH_AUTOINVEST_V0.1.md` §2e.
  The decision that orders follow only a scenario Thomas confirmed (decision 2) did not move.
- **What drafts:** `holdings/autoinvest_proposal.py`, after the executor's look in each holdings fire.
  - The budget is the Toss KRW cash, at most the daily cap, less 3%. That 3% is room for the executor's 2% price
    guard and the fee. The scenario cap is the cash itself.
  - Whole shares go one at a time to the slot with the largest KRW gap to target. A slot at or over target gets
    nothing, and leftover cash stays cash.
  - Each class buys its first candidate. Bonds are two slots: Korean 3y (¾ of the bond target) and US 7-10y
    (¼, D-A). US bonds held are the bond positions Thomas's entries expose to USD.
  - A class whose first candidate is overseas has no slot. Today that is global equity, since there is no USD path.
  - It drafts once per KST trading day, when the budget is at least 50,000 and no scenario is pending, unknown or
    holding an open order. It also needs the fire's allocation to be complete.
- **What Thomas does:** Telegram carries "매수 시나리오 준비 — N종목 M주". `holdings_board --autoinvest-approve`
  with no spec prints the draft. On `y` at an interactive terminal it goes through the same `autoinvest.approve`
  as a typed spec. A draft is approved at most once, and never after 4 days.
- **Found on the way:** `autoinvest.approve` replaced the scenario even when one of its orders was still open at
  the exchange. That order's fill would then go untracked. It now refuses, for a typed spec too.
- **Also:** the automatic candidate file records which bond pick is the US quarter (`us_bonds`). A file from
  before this is screened once more (`bond_split`) to record it, so one extra candidate notice is sent.
- **Not built:**
  - Auto-classifying a newly bought ETF. A purchase Thomas's entries do not place leaves the allocation
    incomplete, and the next draft waits until it is classified.
  - Ordering without Thomas's `y`.
- **Checked:**
  - The share-by-share fill was worked by hand, and so were the ¾·¼ split, the fee and the cap.
  - Each reason to wait, the board's `y`-only approval, and the scheduler's told-after-delivery.
  - 19 mutations were each caught.
