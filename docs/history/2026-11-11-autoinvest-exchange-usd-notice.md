# The buy notice says how many dollars to exchange (2026-11-11)

**Decision:** Thomas 2026-11-11. He received "글로벌 주식 몫은 달러 환전이 필요합니다. 금액은 Thomas 터미널에서 확인하세요" and
asked for the dollar figure in the notice ("싣고 같이 배포"). Recorded in `docs/proposals/G6_BUY_CASH_AUTOINVEST_V0.1.md`
(§2g Q3).

## Why it was amount-free

G6-buy Q3 (2026-10-27) kept every amount on Thomas's terminal and sent Telegram words only, under H3 (one table leaves
the process). The exchange is the one step Thomas does by hand in the app, so the notice told him to act without the
number he needed, and he had to open the terminal for it.

## What changed

- `autoinvest_proposal._notice_text` adds the dollars to exchange, rounded up to a whole dollar (`_exchange_usd`), on
  both the exchange-only and the drafted notice. Rounding up means an exchange of that size covers the slot.
- Nothing else joins it: no KRW amount, no instrument quantity, no holding. The H3 exception is this one figure.
- With no rate (no dollar figure) the old wording points to the terminal.

## Checks

- `tests/test_mvp_runtime_holdings_autoinvest_usd.py`: the exchange-only notice says "약 $72" for $71.43 and carries no
  KRW; the drafted notice carries exactly one dollar figure; rounding cases; the no-rate fallback.
