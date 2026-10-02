# A pass with no confirmed bar no longer spends a position's holding bars

- **The defect:** `trade_plan.advance_holding` advanced `holding_candles` on a `None` timestamp. Its docstring
  called that the pre-existing behaviour, on the grounds that an uncounted bar would stall the exit. A `None` is
  exactly what a degraded collection produces:
  1. `cycle` builds an empty snapshot when the venue cannot be read.
  2. `latest_feature_row` returns `{}`.
  3. The live leg's `candle_ts` is `None`, and the owning context's `_time_exit_or_hold` counted it.

  The pipeline fires every 15 minutes, so an outage on a position's own timeframe aged it one bar per pass. A 4h
  position allowed 12 bars could be timed out after about three hours instead of two days. Paper and the
  counterfactual book share the function. Their time exit also needs a close, so they did not close on such a
  pass, but their counters were inflated the same way.
- **The fix:** `advance_holding` returns `NEW_BAR`, `DUPLICATE_BAR` or `NO_CONFIRMED_BAR`, and on a missing or
  blank timestamp it changes nothing. The next confirmed bar is counted once, never one per missed pass.
- **What the live leg reports:**
  - `live_holding` carries `advanced` and `reason`: `NEW_BAR`, `DUPLICATE_BAR`, `NO_CONFIRMED_BAR` or
    `NOT_TIMED_HERE`.
  - A pass with no bar adds `LIVE_ROUTING_HOLD_NO_CONFIRMED_BAR`.
  - A position whose count had already reached its limit (a close that did not confirm last pass) still has its
    time exit retried. That exit was decided on confirmed bars, and closing reduces risk.
- **What does not change:**
  - Settlement, protection inspection, reconciliation and venue-close settlement run on a degraded pass as before.
  - The entry door still refuses a pass with no bar (`LIVE_ENTRY_BAR_UNKNOWN`). That refusal is now pinned by a
    whole-leg test.
  - Backtest and forward-book candles always carry `close_time`; the forward book already skipped a candle
    without one.
- **Tests:** `tests/test_mvp_runtime_crypto_holding_clock.py`, plus new cases in
  `tests/test_mvp_runtime_crypto_live_route.py`. They cover a new bar, the same bar, no bar, an outage followed by
  a bar, one bar short of the limit, a non-owning context, an already-due exit retried, and a degraded whole-leg
  pass that protects, settles a venue stop fill and enters nothing. Four mutations each fail at least one test:
  - the old None-advance;
  - always reporting `advanced`;
  - dropping the reason code;
  - letting the entry door accept an unknown bar.
