# What G6-buy bought is classified in the class it was bought for

- **Why:** a draft (§2e) needs a complete allocation, and a newly bought ETF that no entry places leaves it
  incomplete. The next draft would then wait for Thomas to run `--classify`. Thomas approved the fix on
  2026-10-27: "자동 매수로 산 후보 종목은 후보의 자산군으로 자동 분류 … 분류 권한에 관한 결정: 승인할게".
  The record is `G6_BUY_CASH_AUTOINVEST_V0.1.md` §2f.
- **What:** `autoinvest.classify_bought` runs in each holdings fire, before the entries are read, so the same
  fire's allocation places the instrument. It enters every instrument with an `accepted` journal row that no
  entry places.
  - The class is the one the instrument was approved for (`approve` now keeps it on the item and in the
    journal). An older row takes its class from the current candidates.
  - A bond names its currency: the US Treasury pick is USD and any other bond is KRW.
  - Entries carry `"source": "g6_buy"` (`classification.apply(..., source=)`).
- **What it never does:** change or remove an existing entry, guess a class it cannot tell, or enter an order
  that was not accepted. The mapping version moves once, as with Thomas's own `--classify`. The H5b baseline
  only records it.
- **Also fixed (seen live on 2026-10-27, nothing approved):**
  - `--autoinvest-approve` with no spec asked for `y` when there was no draft.
  - A key typed in a Korean input mode reached `input()` outside UTF-8 and raised.
  - Now it asks only when there is an unapproved draft, and reads the answer as bytes: anything not `y` is a no.
- **Checked:**
  - The entry per kind: an old row, a new row, a US bond, a rejected order, an intent.
  - Existing entries are untouched, and a repeat writes no new version.
  - A refresh places the bought holding in the same fire.
  - The board asks nothing without a draft, and a non-UTF-8 byte is a no.
  - 10 mutations were each caught.
