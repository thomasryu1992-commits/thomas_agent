# The live leg reads its facts in two named steps (crypto refactor plan PR-14)

- **What changed:** `live_route._run_gated_live_leg` was 437 lines of fact reads, settlement, halts,
  the entry decision, the re-read, the pre-order gate and the send. Its two blocks of fact reads
  now sit in two functions of the same module:
  - `_read_leg_facts` (step 1): the budget and its limits, the execution stage, the control state,
    the account, the book and its reconciliation, and the book's and the ledger's stores, returned
    as one frozen `_LegFacts`;
  - `_read_entry_facts` (step 3): the entry plan, the symbol's filters, the order book, the reference
    quote, the venue's realized loss, the risk snapshot, both breakers, the venue contract and the
    entry marks, returned with the `decision_kwargs` the decision and the gate judge. The clock is
    still read last.
- **What did not change:** every read is at the same point and in the same order. Step 1 still runs
  before anything settles, and step 3 only on a pass that settled nothing and was not halted or held.
  Both helpers write to the record as the inline code did: the stamps (`live_gate`,
  `execution_stage`, `live_reconcile_status`, the breakers, the venue contract) and the reason codes
  of a read that degrades. Step 1 is not pure: it counts the account read on the API breaker and ends
  the protection watch of positions that left the book. Settlement, the halts, `plan_live_entry`, the
  re-read and `verify_live_arm`, the gate and `live_leg.execute_live_entry` stay in
  `_run_gated_live_leg`, so the egress roster's key for the autonomous entry is unchanged. No module
  was added, so the layer map, the egress rosters and the chokepoint pins are as they were.
- **Found while doing it, not changed:** `control.py`, `test_mvp_runtime_assistant_resume_scope.py`
  and `ASSISTANT_RESUME_SCOPE_SPLIT_DESIGN_V0.1.md` say the leg settles and protects "before it reads
  control state at all". The control state is read in step 1, before settlement; only its use waits
  for the entry decision. The safety claim (a disarmed runtime still closes what it holds) holds.
  The wording does not, and is left for its own change.
- **Tests added:** `test_mvp_runtime_crypto_live_route_facts.py` pins four hand-offs nothing pinned,
  because an earlier door refused first or a later one repeated the check: the book and the
  reconciliation the decision is judged on (and the reconciliation's stamp); the protection watch
  ending a gone position's episode; the re-read judging today's loss knowing the account was read;
  and an outcome the executing leg built being written to the ledger step 1 selected.
- **Evidence:**
  - a scratch seam log over the 27 test files that reach the route: 805 tests, 6,261 calls to the
    reads and doors around the leg (the stage, the account, the reconciliation, `_settle_or_protect`,
    every step-3 read, the clock, `plan_live_entry`, the re-read, `verify_live_arm`,
    `narrow_entry_facts`, `gate_live_entry`, `execute_live_entry`), logged in order with their
    arguments and results. Base and head are identical once the wall clock, temp paths and hashes of
    time-stamped records are masked; two base runs differ in exactly those;
  - the record comparison over 6,724 lane tests, request logs included: 7 files differ, all of them
    the scheduler's measured durations and the concurrency test's race winner;
  - the patch-reach census on `live_route`: each of the 235 tests that patch the module reaches the
    same patched names on both commits, with 0 missed;
  - 25 mutants of the new hand-offs (a fact dropped, swapped or replaced on its way to the step
    that reads it). The route's 27 test files killed 19. The other 6 survived the whole crypto lane
    as well (5,531 tests), on lines that were there before this PR. Four tests in
    `test_mvp_runtime_crypto_live_route_facts.py` now kill all 6 and pass on the base commit too;
  - an independent review, and the full suite.
- **Not done:** the plan's evidence for this row includes one signed testnet cycle. It has not run,
  for PR-13 or for this PR: the stage is PAPER and the testnet opt-in is off, so the guard refuses it
  (2026-09-30, as designed). Thomas chose to go ahead without it.
