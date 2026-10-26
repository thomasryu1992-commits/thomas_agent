# The monthly band notice (PR-D of RB1–RB6)

- **Why:** TOTAL_ASSET_ALLOCATION §6 checks the bands monthly. RB5 (Thomas 2026-10-26,
  `REBALANCING_PROPOSAL_V0.1.md`) put the reminder on Telegram, a count only, from the month's first business
  day.
- **What:** `store.band_notice` runs in the holdings fire. From the month's first weekday (KST) on, it returns
  one of two messages until the month is marked told:
  - "밴드 이탈 N개", with "--rebalance" when N > 0, or "리밸런싱할 것이 없습니다" when N is 0;
  - "판정할 수 없습니다" when the NAV is incomplete.

  The scheduler delivers it and records the month only after delivery (`mark_band_told`, the local
  `holdings_band_notice.json`), like every holdings notice. An undelivered notice is offered again the next
  fire.
- **The verdict is the calculator's:** `rebalance.plan` with the engine margin left out (RB5). The count and
  `--rebalance` therefore agree. The board's own `outside_band` counts the engine group too, and is not used.
- **What leaves:** a count, never a class or an amount. `outside_band` is already in the outward snapshot, so
  this is no new disclosure.
- **Approximation:** weekdays stand in for business days. A public holiday is not known here, so the notice
  then arrives on that holiday.
- **First deploy:** the month has no told record yet. So the first fire after deploy (in October, after its
  first weekday) sends October's notice once; November's comes on 11-02.
- **Tests moved:** `test_mvp_runtime_holdings.py` stubs the notice out (an autouse fixture). Its tests count
  the other notices and end-of-status lines, and NOW (10-07) would make the monthly one due on every fire. The
  notice has its own file, and one test there runs the real refresh with a spy.
- **Checked:**
  - the first weekday after a Sunday 1st;
  - two bands outside, with the engine margin ignored;
  - zero outside;
  - an incomplete NAV;
  - told once a month, asked again the next month;
  - delivery before marking.
  - Six mutations were each caught: weekend counted, told month ignored, engine counted, incomplete judged,
    marked before delivery, refresh dropping the notice.
