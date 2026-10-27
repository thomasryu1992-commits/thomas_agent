# G6-buy keeps what Toss said when it refuses an order

- **Why:** the first live run (2026-10-27, scenario B) placed two orders and both filled. Thomas checked in the
  app: KODEX 200 ×1 and KODEX 국고채3년 ×1. The third order, ACE KRX금현물 ×2, came back 422 and the executor
  stopped, as designed.
  - The journal held only "422": no Toss code, no message, no request id. So the cause could not be told
    apart among the 422 codes (insufficient buying power, stock restriction, prerequisite, …).
  - The leading hypothesis is that a market buy reserves cash at the upper price limit. The two earlier orders
    would then hold about 193k of the 200k. The hypothesis is unconfirmed: Toss's docs do not say which price
    a market buy reserves.
  - Thomas asked for the cause before any fix.
- **What changed:**
  - `toss_orders.error_detail` reads every shape a refusal can take: the documented `{"error": {...}}`
    envelope, a flat `{"code": ...}`, a non-JSON body, an empty one.
  - It keeps `code`, `message` (cut to 200 characters; `ApiError.message` is Toss's user-facing text),
    `request_id` (from the body, else the `X-Request-Id`/`referenceId` header; Toss support asks for it), and
    `body_kind` and `content_type`.
  - `OrderRejected` and `OrderUnknown` carry the detail. The journal's rejected and unknown rows store it as
    `toss`, and `--autoinvest-status` prints it.
  - The Telegram notice is unchanged: a code at most, never the detail.
- **Not changed:** the order type (still MARKET) and every check. The fix waits for the cause.
- **Next:** re-run the remaining gold ×2 as its own scenario. With about 51.6k left, the hypothesis predicts a
  second rejection (2 × upper limit ≈ 61.8k), and a second 422 now carries its code.
  - **Corrected 2026-10-27:** this line first said the opposite, that a fill would point to the hypothesis.
    The same error is in #1330's PR body. See `docs/history/2026-10-27-g6-buy-limit-orders.md`.
- **Checked:**
  - Four body shapes (envelope, flat, HTML with a header request id, empty).
  - The message cut.
  - Journal and terminal carry the detail while the notice does not.
  - Six mutations were each caught.
