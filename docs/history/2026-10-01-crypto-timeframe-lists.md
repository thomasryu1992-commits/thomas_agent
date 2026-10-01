# The board's timeframe order is derived; the other two lists are pinned (crypto refactor plan §K)

The plan's duplication table listed three timeframe lists beside `market_data.TIMEFRAMES` and
recommended deriving them. Measured one by one:

- **`dashboard._TIMEFRAME_ORDER` is now derived.** It is the authorable timeframes sorted by length
  (`ALLOWED_TIMEFRAMES` ordered by `TIMEFRAMES`). The value is the literal it replaces,
  `{15m: 0, 1h: 1, 4h: 2, 1d: 3}`, and a test pins it. A timeframe made authorable later takes its
  place by length, with no second list to update.
- **`strategy.ALLOWED_TIMEFRAMES` stays explicit.** It is a policy subset of the collectable
  timeframes: 1m and 5m are collected and not authorable. No rule derives it from `TIMEFRAMES`, and
  `test_every_authorable_timeframe_gets_its_full_depth` already pins that it stays a subset.
- **`market_data.POSITIONING_PERIOD_SECONDS` stays.** It is the vendor's period table, wider than
  `TIMEFRAMES` (30m, 2h, 6h, 12h). A new test pins that where the two name the same timeframe,
  seconds equal minutes × 60.
