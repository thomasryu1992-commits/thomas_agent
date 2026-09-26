# The system review's phase-2 decisions are recorded where each one lives

- **What was decided (Thomas 2026-09-26, as recommended):** D1 wider failover (with the reason
  logged and surfaced), D2 deliver non-safety REVISE and reviewer outages under an unverified banner
  (fabricated sources and lineage/authority/secret BLOCKs stay withheld), D3 a pause on new crypto
  research machinery until the first forward-cohort verdict, D4 1h mined on all five symbols — as five
  single-symbol schedules, not pooling (F9's reasons for leaving 1h unpooled still hold), D7
  `deferred/` to `historical/` (last), D8 a 60-day evidence deadline for the non-crypto lanes
  (2026-11-25). D5's breaker values wait on the slippage measurement; D6 stays as it is.
- **Where it is written:** the plan's "결정 (Thomas 2026-09-26)" section, and each affected proposal's
  status line and decision section (`RESEARCH_EPOCH` Q3, `FAMILY_EXHAUSTION` §6, `RISK_BREAKER_UNIT_RESTATEMENT`
  (c), `COST_GATE_RESET_THE_RECORD` §6-2, `GATE0_CANNOT_BE_SATISFIED`'s approval, and #615's third allowance
  criterion recorded as dropped in `EVALUATION_CANNOT_ACT_PER_STRATEGY`). The D3 pause is a rule
  with an end condition, so it is in `CLAUDE.md`. `docs/proposals/STATUS.md` is regenerated.
- **Not in this change:** the code for D1 and D2 — each is its own PR. D4 needs no code: it is a host-side
  schedule split (`scheduler_cli`), so it lands when that is run.
