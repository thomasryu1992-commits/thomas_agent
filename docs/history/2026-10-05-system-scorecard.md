# System scorecard: 6/10 — the guards score 9, what they guard scores 4–5

- **What was asked:** after the Phase 7.14 audit, Thomas asked for a score of the whole system, in the
  shape of the 10-01 crypto strategy score (`CRYPTO_STRATEGY_EDGE_ORDER_V0.1.md`) but across every area.
- **What it found** (`docs/proposals/SYSTEM_SCORECARD_V0.1.md`, measured on the host read-only):
  safety and governance 9, core reliability 8 (the 09-26 lock-timeout and watchdog items are closed),
  analysis/content lanes 7, development process 6, product value 5, ops and secrets 5, crypto edge 4
  (unchanged; 0 of 197 cohort members confirmed). The two lowest non-crypto scores are not code:
  the daily core backup still carries `.env` in plaintext because #1024 waits on the age public key,
  and 13 proposals wait on a Thomas decision.
- **Two findings worth keeping:** OpenRouter, the chain's first member, failed over on 151 of 160
  content and research runs in the week the failover record covers; the runtime and Hermes share one
  OpenRouter key. And every Windows-only red since 09-21 (7 of 639 runs) is one of three wall-clock
  assertions, none a product defect.
- **What it does not do:** no code, policy, schedule or env change. Q1–Q6 wait on Thomas; Q5 (when the research epoch boundary falls) and Q6 (the D5 breaker decision waits on live slippage samples that only a LIVE_AUTONOMOUS probe can take) cannot resolve by waiting.
