# Positioning rows: measured when they are known, declared in the point-in-time contract

- **Why:** E3 (#1281) left `positioning` UNVERIFIED. Its rows store no fetch time, and the collector's docstring
  said a row labelled T is the forming aggregate of [T, T+period) whose "ratio is still moving". If that were
  true, aligning rows on their label would be a lookahead of one period on any frame finer than the period.
- **Measurement** (2026-10-21 09:47–10:38 UTC; 162 public polls, no key, about 3 per 2 minutes):
  - Covered BTCUSDT on `topLongShortPositionRatio`, `topLongShortAccountRatio` and
    `globalLongShortAccountRatio`, periods 5m and 1h, limit 3.
  - **1h:** the row labelled 10:00 first appeared between +94 s and +213 s depending on the series. It then
    kept its value through 19–20 polls inside its hour. The 09:00 row was already present at 09:47 and also
    never moved.
  - **5m:** rows appeared between +7 s and +293 s after their label. None of the 40 rows observed ever changed
    value.
  - No errors; the IP weight was negligible.
- **Reading:** a row labelled T is a reading at T, published a few minutes later and never revised. The
  collector's "still moving" premise does not hold. The docstring now says so, and the code is unchanged.
- **Contract:**
  - `AVAILABLE_AT["positioning"]` is label + 10 minutes, the measured maximum with margin. `UNVERIFIED` is now
    empty, and the `MINTING_DECIDED` pin is gone.
  - The fixture carries all three series hourly, and the test now asserts every column fills, positioning
    included.
  - The contract runs on 4h, 1h and 15m frames. A leak shorter than one bar is invisible on that bar's frame
    (its own close already knows it): a positioning alignment one hour early passes on 4h and fails on 1h and
    15m.
- **Left for the positioning-minting decision:**
  - The collector drops row T until T+period, so live holds a row one period after the venue publishes it.
  - Replay aligns on the label, so on a 15m frame with 1h rows replay can read a row live does not yet hold.
    That is not a lookahead in the world, but it is a replay/live difference.
  - The options are to key replay on label + period (replay follows live) or to keep rows once published
    (live follows the venue). Either is a decision for when `positioning_store.MINTING_DECIDED` is opened.
