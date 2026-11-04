# 2026-11-04 — Cross-section spot holdout: v0.2's LEAD rechecked on Binance spot 2017-08 → 2019-12

**What.** `binance_archive.py --market spot` lists and fetches spot monthly 1d klines. `cross_section_basket.py
--test spot-holdout` runs v0.2's S-MOM30 (top 30, 6 a side, weekly) on spot USDT pairs from 2017-08-01 to
2019-12-31. It uses spot's 10 bps taker fee and no funding. Pre-registration:
`docs/proposals/CROSS_SECTION_SPOT_HOLDOUT_V0.1.md`.

**Why.** v0.2's LEAD was chosen after v0.1's result, and its forward confirmation would take one to two years at a
weekly sd of about 979 bps. Thomas rejected waiting that long. The spot archive before 2020 is the one span of
Binance history no measurement here has read: a grep found no spot archive or 2017–2018 data in the repository.
Its window does not overlap the futures window by a day.

**The short leg** is computed from price, as Thomas put it: a short could not be held on spot then, but its flow
can be measured. The test asks whether the signal existed then, not whether that trade was possible.

**Judgement.** One test, so no Holm family. CONFIRMED needs all of:
- weekly mean > 0;
- one-sided p ≤ 0.05;
- beating the random basket;
- 2018 and 2019 both positive.

If anything later reads this span again, that test carries this p value in its family.

**Which spot pairs count as coins.** Dollar, fiat and leveraged-token bases are no members (`SPOT_NOT_COIN_BASES`,
`spot_kinds`). The rule was fixed in code before the spot archive was fetched.

**Tests.** 9 added. Six mutants are caught: fee ignored, stablecoins kept, the year condition ignored, a two-sided
p, the window ignored, and an over-greedy UP suffix.
