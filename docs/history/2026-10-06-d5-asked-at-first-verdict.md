# D5 is asked at the first forward-cohort verdict, not "after the slippage measurement"

- **Decision (Thomas, 2026-10-06):** scorecard Q6 (c) (`docs/proposals/SYSTEM_SCORECARD_V0.1.md`).
- **The circle it removes:** D5 (2026-09-26) set the loss-breaker values "after the slippage measurement"
  and held the stage below LIVE_AUTONOMOUS until then. The only instrument that adds slippage samples at
  this stage is a probe, and a probe requires LIVE_AUTONOMOUS (`execution_stage._REQUIRED_STAGE`); there
  is no canary rung. The gate could never open (stop n=12, entry n=3 since 2026-09-28).
- **Now:** D5 is asked at the first cohort verdict, 2027-03-22, and crypto live stays below
  LIVE_AUTONOMOUS until then. Nothing is lost meanwhile — the LIVE door takes FORWARD_CONFIRMED only, and
  none exists. SIGNED_TESTNET is not affected.
- **Said in advance:** PAPER adds no fills, so the sample on 2027-03-22 will be today's. The real choice
  that day is a provisional value on n=12 (re-set once live fills accrue) or keeping live down.
- **What changed:** RISK_BREAKER and SYSTEM_REVIEW status lines and decision sections, the REMAINING_WORK
  §F8 sentence that called the old gate "a concrete blocker", the scorecard's Q6. No code, no value.
