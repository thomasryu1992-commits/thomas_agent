# The 13 proposals waiting on Thomas: dispositions recorded, the queue down to the decisions that are real

- **Decision (Thomas, 2026-10-06):** scorecard Q3 — the §3 dispositions as recommended
  (`docs/proposals/SYSTEM_SCORECARD_V0.1.md`).
- **What changed:** each proposal's status line and a dated "결정" section at its end. Four close with
  nothing to build (CONVERSATIONAL D4, APPROVAL V1–V4, AUTOMATIC_SELECTION Part 2, TEMPLATE_RSI option 0).
  Three move to DECIDED with a named build: CONTROL K1 (the `/kill` reply says a running analysis
  finishes), COST §6-3 (a read-only measurement), THROUGHPUT P0-2/P0-3/P1-2/P1-3 (measurement and
  display, inside D3's exception). The rest keep their open items, now with the gate written down:
  WALK_FORWARD waits on its own pre-registered report, not on Thomas; D5 is one decision across
  RISK_BREAKER and SYSTEM_REVIEW, tracked as scorecard Q6.
- **Checked before writing, not taken from the summary:** K3 is `operator.PEEKABLE_HALT_VERBS`; the
  `/kill` reply names only new/pending work; the probe requires `LIVE_AUTONOMOUS`; the second cohort's
  freeze record carries `judgement_rules.v1` `624cb2108e16` and the first carries none; the approval
  push already prints `/approve <id>`.
- **One deviation:** the §3 table did not name APPROVAL V1; it is recorded as not built, for the same
  dead premise as V2–V3. Thomas can reopen it.
- **What it does not do:** no code, policy or schedule change. Scorecard Q1, Q4–Q6 stay open.
