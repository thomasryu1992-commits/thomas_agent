# Wallet activation: the first complete portfolio NAV (record)

- **What happened (2026-10-08):**
  - #1187 gave scheduler-maint the read-only pair.
  - The valuation preflight passed twice, read-only and one-shot, from the scheduler and from
    scheduler-maint. It found no unpriced asset, no invalid row, no truncated Earn list, Earn read, and
    no warnings.
  - `.env` was backed up, and `MVP_BINANCE_WALLET=binance_wallet` was appended at 08:18Z. Only
    scheduler-maint was recreated.
- **First fire (08:33:54Z):**
  - All five required checks PASS. The source skew was 1,161 s against a 1,800 s limit; the futures
    file was one write short after the deploy restart, and a normal fire sees about 250 s.
  - `portfolio_nav_complete` is true, and the NAV is shown as COMPLETE (scope v1).
  - The peak file restarted under scope v1. The drawdown is `initialized`, and verdicts resume from
    the next fire.
  - No class was withheld under the single-holding rule.
- **Still open:** the allocation band verdict stays withheld. The wallet's `other` class (8 altcoins) is
  unclassified by decision (Thomas 2026-10-07), and where those assets belong is Thomas's call. The
  skew margin is the second watch item.
- **Rollback:** remove the `MVP_BINANCE_WALLET` line from `.env`, then recreate scheduler-maint with
  the compose command. The board returns to INCOMPLETE (coverage).
