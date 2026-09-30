# The live slippage instrument reports a distribution, and says when the sample is too small for one

- **What was found:** `scripts/measure_live_slippage.py` reported median, mean and worst per leg and
  per stop source. The crypto improvement plan asks for P75/P90/P95 and a split by symbol and order
  type (`docs/proposals/CRYPTO_SYSTEM_IMPROVEMENT_GAP_ANALYSIS_V0.1.md` §4). It is review C2's
  measurement, so D3 exempts it.
- **What was delivered:**
  - `distribution()` returns n, mean, median, worst, and the inclusive P75/P90/P95 once there are
    `MIN_PERCENTILE_N` = 20 fills. Below that each percentile is None, and the rendering says why: at
    n=12 a P95 is the worst fill under another name.
  - Each stop, entry and canary summary line gains a percentile line under it. The existing lines are
    unchanged.
  - Stop and entry fills are listed by symbol.
  - The order type is the leg already reported: entry and canary are MARKET, and a stop is STOP_MARKET.
- **Read 2026-09-30 on the live state:** run in a one-off container as the service user; nothing was
  written. Stops n=12, mean 3.06 bps, as on 09-28, and no percentile is reported. By symbol:
  - ETHUSDT: n=3, mean 8.38, worst 23.47. It carries the pooled mean.
  - BTCUSDT: n=5, mean 0.17.
  - SOLUSDT: n=3, mean 3.57.
  - DOGEUSDT: n=1, 0.00.
  - Entries: n=3, all BTCUSDT.
- **Deliberately not done:**
  - No constant moves.
  - The entry's sealed `market_impact.impact_bps` is not put beside the realized fill yet.
  - The plan's volatility, spread, size and liquidity dimensions wait for a sample worth splitting.
