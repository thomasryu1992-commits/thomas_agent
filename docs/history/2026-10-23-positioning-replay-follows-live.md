# Positioning: replay reads a row from when the runtime holds it (label + period)

- **Why:** the measurement in `2026-10-21-positioning-availability.md` showed that a row labelled T is a reading
  at T, published a few minutes later. The collector still keeps row T only once its period has closed
  (`market_data._parse_positioning_page`), so live first holds it at T + period. Replay keyed on the label,
  so on a frame finer than the period (15m against hourly rows) it read a row up to one period before live
  could.
  - The options were replay follows live (key at label + period) or live follows the venue (keep rows once
    published).
  - **Thomas 2026-10-21 chose replay follows live.** The reasons:
    - It is the OI and liquidation principle: replay sees no more than live holds.
    - It leaves the live collection path and the append-only store untouched.
    - The venue's "never revised" behaviour rests on 50 minutes of polls, and the drop rule is what
      protects the store if a row is ever revised.
    - The cost is at most one period of freshness on a slow ratio that the store already refreshes only
      hourly.
- **What:**
  - `features._positioning_columns` keys each row at label + its period.
  - The period comes from the row's own `period` field through `_period_seconds`, the interval grammar. A test
    holds it equal to `market_data.POSITIONING_PERIOD_SECONDS` for every key.
  - An unknown or missing period aligns nowhere.
- **Effect today:** none on evidence. No spec reads positioning while `positioning_store.MINTING_DECIDED` is False,
  and the factory mints no 15m. On 1h and 4h frames, live already read the same row. The shift makes replay and
  live agree on every frame before minting opens.
- **Checked:**
  - A test walks a 15m grid across six hourly rows. At every bar, the row features read equals the newest row
    the collector's own parser keeps at that bar's open.
  - Reverting to label alignment fails it, and the two existing alignment tests now state the held-reading rule.
  - The point-in-time contract (world availability, label + 10 min) still passes; this rule is stricter.
