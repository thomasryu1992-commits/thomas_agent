# Phase 7.14 alignment plan: audited, kept as decided, three safety pins added

- **What was asked:** an outside "Phase 7.14 Roadmap Alignment & Safety Refactor" plan — the third
  in the series after 09-28 and 09-30 — asked for a `LIVE_CANARY` rung, a code constant disabling
  signed testnet and live submission above the env opt-in, metadata-only key handling in the
  adapters, a ResearchSignal v2 layer and a 13-id lineage validator.
- **Decision (Thomas, 2026-10-05):** all four questions as recommended
  (`docs/proposals/PHASE_7_14_ALIGNMENT_AUDIT_V0.1.md` §5). The ladder stays; the witnessed stage
  ledger stays the one authority above the env opt-in (the host reads PAPER, so the plan's "no
  testnet or live order now" already holds); adapters keep reading keys, since removing that turns
  off account visibility and the close path; ResearchSignal and the id chain wait on D3.
- **What changed:** three tests that pin existing behaviour and grant nothing. A paper cycle with
  every trading opt-in set opens no socket and builds no venue adapter. A stage record carrying every
  good result the machine can produce binds nothing without a Thomas-spent approval (with a control
  that binds). A non-required `Safety invariants lane` workflow runs the safety test files alone
  from `tests/safety_lane.args`, and `tests/test_safety_lane.py` finds the invariant tests by
  definition so a moved test cannot silently leave the lane.
- **What it does not do:** no code, schema, policy or required-check change.
