# G6-sell checks Toss's sellable quantity, not the holding, right before a sell

- **Why:** Toss's public spec 1.2.28 (read 2026-11-11, `openapi.tossinvest.com/openapi-docs/latest/openapi.json`)
  has `GET /api/v1/sellable-quantity`.
  - It gives the shares of one symbol that can be sold now, net of shares already in a pending sell. Domestic
    answers are whole shares.
  - The executor checked the holding instead (`domestic_quantities`). Shares sitting in a sell Thomas placed in the
    app passed that check. The order would then be refused, which stops the scenario.
  - Thomas: "바꾸기 진행". Record: `G6_SELL_REBALANCE_V0.1.md` §4a.
- **Change:**
  - `TossHoldingsFeed.free_shares(symbol)` reads the endpoint through the same GET-only `_get`, with the account
    header. An answer without a readable number raises, so the day holds and nothing is guessed.
  - `autosell._checks` reads it per item. Fewer than the scenario still sells holds the whole scenario for the day.
    The hold reason is still `holding_short`; its notice now says "판매 가능 수량".
  - `domestic_quantities` is gone. It had no other caller.
- **The name:** the feed's test forbids order words in its method names (`test_the_feed_has_no_order_capability`),
  so the read is `free_shares`, not `sellable_quantity`. G6-buy's cash read was renamed for the same reason
  (`buying_power_krw` → `cash_krw`).
- **Also read off the spec, recorded in the packet:**
  - The order book's bid side is `bids` (required, highest first), as the code assumed.
  - An order is refused while the same symbol has a pending order the other way (`opposite-pending-order-exists`).
  - Which code a sell over the sellable quantity draws is not in the spec's examples. It stays unverified until the
    first real sell.
- **Checked:**
  - The feed's request (path, symbol, account header) and four malformed answers.
  - A pending app sell that the holding alone would have passed now holds the day.
  - The failing read holds and never half-orders.
  - The mutations in the PR were each caught.
