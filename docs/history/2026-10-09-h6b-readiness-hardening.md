# H6b readiness-hardening: the signal names the unit shadow, exceptions block it, and the shadow is counted in verified dates

- **What this PR changes:**
  - `holdings/cash_flows.py`:
    - **Exceptions.** A new readiness check, `post_cutover_exceptions`, counts events after the cutover
      that nothing explains yet: a Binance event held for any reason, and a Toss residual. One is
      enough to FAIL it.
    - **Shadow observation.** It needs seven days since the cutover and a verified fire on seven KST
      dates. One fire a date is enough, so a passing outage cannot block it forever.
    - **Verified fire.** `mark_fire_verified` records `first_verified_fire_at` and the date. It is called
      only after a fire ends cleanly. `collect_binance` no longer stamps it when the read starts.
    - A cutover migration clears the dates.
  - `holdings/store.py`:
    - A fire is verified when every Binance history read and the Toss pass, the summary and the
      readiness pass all ended.
    - It returns `exception_alert`, which carries counts only, each time the count grows.
  - `scheduler.py` delivers the exception message and records it only after delivery, as it does the
    readiness message.
  - `holdings/board.py`:
    - The signal is `H6b_shadow_ready`, not `H6c_ready`.
    - A `shadow days / dates` line is added.
    - `Action required` reads `exception review (N)` while exceptions block the signal.
  - Tests: 16 new and 2 changed tests (51 in all) in `tests/test_mvp_runtime_holdings_cash_flows.py`, and the scheduler readiness test in `tests/test_mvp_runtime_holdings.py` takes the new keys and name; through the store
    and the scheduler too.
    - Eight mutations were each caught: the two exception counts, the date rule, the verified-fire
      guard, the Binance error check, the told mark, the told comparison, and the pre-cutover guard.
- **Why:** Thomas's review of #1195 (2026-10-09) found four gaps:
  - The signal was named for H6c while it opens H6b-shadow.
  - A held or unresolved event did not block it.
  - The shadow check was elapsed time, not a shadow that worked.
  - `first_verified_fire_at` was stamped before the reads it was meant to vouch for.

  Thomas chose to tell exceptions by Telegram as they grow and to leave the resolution tool to H6d,
  counting Binance holds and Toss residuals alike.
- **What it does not do:**
  - It touches no unit, NAV per unit, drawdown or allocation.
  - It resolves no exception.
  - Nothing turns on by itself.
