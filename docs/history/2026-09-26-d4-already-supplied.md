# D4's schedule split was already in place, so it was not run

- **What was found:** deploying candidate-994 with a runbook that split the 1h cohort schedule into
  five single-symbol schedules, the schedule store showed four of the five already enabled
  (`ETHUSDT 1h`, `BNBUSDT 1h`, `SOLUSDT 1h`, `DOGEUSDT 1h`, created 2026-07-23) and minting daily —
  GEN-958–961 on 2026-09-26 are each one of those symbols at 1h. Only `BTCUSDT 1h` single is disabled,
  and the cohort schedule supplies BTC 1h. `FAMILY_EXHAUSTION_V0.1.md` §6 looked at the cohort row alone
  and concluded the other four had no supply; the plan's C3 and D4 inherited that.
- **Why the split was not run:** adding five schedules would mint four symbols twice a day, and
  disabling the cohort would also stop the cohort-scope 1h trial minting (GEN-964 minted
  `session_liquidation_short` at 5-symbol scope). **Thomas 2026-09-26: keep the current schedules.**
- **What changed:** the correction is written where the wrong sentence was (`FAMILY_EXHAUSTION` header
  and §6, the plan's C3 and D4), and `docs/proposals/STATUS.md` is regenerated. No code or schedule changed.
