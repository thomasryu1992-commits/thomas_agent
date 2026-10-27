# G6-buy places limit orders at the book's price and carries an unfilled remainder to the next trading day

- **Why:** on 2026-10-27, scenario B's market orders filled for KODEX 200 and KODEX 국고채3년 and were refused
  (422) for ACE KRX금현물 ×2. Gold ×2 alone was refused again, with a non-JSON body and request id
  `rVTb3cEaKHJZR0o5`. Gold ×1 was accepted.
  - The same instrument split by quantity, which points to a market buy reserving cash at the upper price
    limit (+30%), not to a restriction on the instrument. Toss's docs do not state this.
  - Thomas set the design, including the unfilled case, before any code: "승인: G6-buy 지정가 전환(…) (Thomas
    2026-10-27)". Record: `G6_BUY_CASH_AUTOINVEST_V0.1.md` §2d.
- **The price:** `TossHoldingsFeed.asks` reads `/api/v1/orderbook`. `autoinvest._limit_price` takes the lowest
  ask at which the cumulative volume covers the remaining quantity. That level is on a tick by construction.
  - The day holds if no level covers the quantity (`book_too_thin`), or if the price is more than
    `PRICE_GUARD` = 2% over the last price.
  - The cash and cap checks use the limit price, which is what the order reserves.
  - The scenario cap counts what already filled and what is open.
- **The order:** `buy_kr(symbol, quantity, price=…)` sends `LIMIT`/`DAY` with an integer KRW price. A non-integer
  price is refused before any POST. `clientOrderId` now includes the KST day, because a remainder is re-ordered
  on a later day.
- **Tracking:** every fire, `_track` reads each open order through `TossHoldingsFeed.fill_state`
  (`/api/v1/orders/{id}`).
  - FILLED closes the order.
  - CANCELED, REJECTED and REPLACED close it with what filled, and the remainder waits.
  - Pending, partial, an unknown status and a failed read all keep it open, and no second order goes beside it.
  - When every item is filled the scenario is `executed`.
  - New orders still go at most once per KST trading day, inside the session.
- **Not built:** a cancel. The exchange's close does it (`DAY`). The adapter still has one method and BUY.
- **Also:** a non-JSON refusal keeps the first 200 characters of its body and its Content-Type, and the terminal
  status prints both. #1330's history note had the re-run's reading backwards; it is corrected there and in
  the record.
- **Checked:**
  - Book price across levels; thin book; the 2% guard and the next day; the live case (51.6k covers 2 × 23,765
    at the limit).
  - Close-cancelled remainder re-ordered the next day with a new id; partial or unknown status or a failed
    read places nothing; the cap counts fills; expiry says what filled.
  - Eleven new mutations and the earlier executor and scheduler ones were each caught.
