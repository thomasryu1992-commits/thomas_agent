# Proposals carry one closed lifecycle state, and the open decisions are a generated page

- **The defect:** 16 of 37 proposals said DRAFT or "awaiting a Thomas decision", several of them
  decided and built — `FORWARD_EVIDENCE_CONFIRMATION_V0.1.md` said "not implemented, awaiting Thomas"
  while `crypto/forward_confirmation.py` quoted Thomas's 2026-08-11 approval. No list anywhere said
  what actually waited on a decision (system review D3, `docs/proposals/SYSTEM_REVIEW_IMPROVEMENT_PLAN_V0.1.md`).
- **What changed:** `tests/test_design_record_lifecycle.py` already pinned a closed status vocabulary
  for `docs/runtime-contracts/`; its section (4) extends that owner to `docs/proposals/` with a
  decision-shaped vocabulary (`DRAFT`, `PARTIALLY DECIDED`, `DECIDED`, `IMPLEMENTED`, `SUPERSEDED`,
  `RECORD`), one dated `**상태:**` line per header. `scripts/build_proposal_status.py` holds the
  vocabulary, parser and renderer so the test and the page cannot read a line differently, and writes
  `docs/proposals/STATUS.md` under a freshness test. Every header was re-checked against code, tests and
  records: 12 waiting on Thomas, 2 decided and not fully built, 21 implemented, 1 superseded, 1 record.
- **Deliberately not done:** no hand-written one-page status — a hand-maintained summary is the stale
  header again, one file up. `REMAINING_WORK.md`'s §F/§G stay where they are (a large edit to a file
  concurrent sessions touch daily).
- **Found while re-checking, recorded in the headers, not acted on:** Gate 0's removal (#473, a
  real-money door widening) has no quoted Thomas approval; #615 dropped its third allowance criterion
  without a record; `COST_GATE_RESET` §4.1 was built as reporting where it proposed a gate. Two of
  #980's own corrections were wrong and are fixed (`FORWARD_EVIDENCE_CONFIRMATION` §5-3 is recorded;
  `EQUITY_PERP_LANE` S2(a) finished 2026-08-09).
