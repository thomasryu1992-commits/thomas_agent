# The lifecycle's "live vs backtest" win-rate drop is paper vs backtest, and now says so

- **What was found:** `lifecycle.compute_strategy_performance` writes `live_vs_backtest_win_rate_drop`,
  and a drop past the threshold demotes to PROBATION as `live_win_rate_dropped_below_backtest`. The
  one caller (`cycle.run_crypto_cycle` → `run_lifecycle`) passes the paper book's outcomes
  (`paper.read_outcomes`). So the field compares the paper win rate with the backtest one. A reader
  looking for a live-vs-backtest gap would take it for one that does not exist. Found by the crypto
  improvement gap analysis (§9, `docs/proposals/CRYPTO_SYSTEM_IMPROVEMENT_GAP_ANALYSIS_V0.1.md`).
- **What was delivered:** the docstring states what the outcomes are.
- **Deliberately not done:** the key and the reason code keep their names. Both are written into
  lifecycle and pool records, and renaming them would split the record history for a label.
