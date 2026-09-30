# What a live leg's result says moves to its own module (crypto refactor plan PR-13)

- **What was there:** `live_leg` (1,931 lines) is the module that executes live orders: the entry,
  the protective bracket, the close, the settle. It also held the vocabulary its results are written
  in and the functions that read a result without touching the venue.
- **What moved:** 61 definitions, identical text, to `crypto/live_leg_results.py` (execution layer,
  553 lines): the entry and exit outcomes, the reason codes, the close reasons and exit sources, the
  protection states, the two status sets and `_BRACKET_LEGS`; and nine functions that call no
  adapter, store or clock (`realized_pnl_usdt`, `exit_fill_from_history`, `_history_start_ms`,
  `bracket_error_detail`, `legs_left_resting`, `_placed_id`, `_record_naked_outcome`,
  `_persist_failure_reason`, `leg_status_line`). `live_leg` (now 1,432 lines) re-exports all 61 as
  the same objects.
- **What did not move:** the eighteen definitions that send, read the venue, write a store or build
  a request. `execute_live_entry`, `place_bracket_leg`, `cancel_bracket_legs`, `execute_live_exit`,
  `settle_venue_closed_position`, `read_bracket_legs` and their helpers are unchanged, definition
  for definition. So are the four request builders (`build_bracket_intent`, `_naked_close_identity`,
  `_exit_terms`, `claim_exposure`) and the bracket-confirm knobs: they shape what is sent, and they
  stay with the code that sends it. The egress roster, `EGRESS_MODULES` and `LIVE_ORDER_MODULES` are
  not edited.
- **Why this cut:** the plan's step is "the leg's result classification, made pure". The functions
  above were already free of the adapter; they were only filed with the code that is not. The new
  module imports no module that sends an order, so a reader of results (the board, the P&L ledger,
  a watch) can take a status name without importing the leg. A test pins that.
- **Evidence that nothing changed:**
  - every one of the 79 top-level definitions of the old file is in exactly one of the two files
    with an identical syntax tree;
  - **adapter and bracket scenarios.** A digest drives `execute_live_entry`, `execute_live_exit` and
    `settle_venue_closed_position` through 76 scenarios on the test suite's scripted adapter and
    stores: a bracket that rests, one refused, not found, found late, partly filled; a naked close
    that works, fails, is refused, cannot be priced; entries not filled, rejected, timed out; every
    missing store; closes confirmed and not; a settle from a filled leg, from `FINISHED`, from the
    fill history, and with each store failing. It logs all 329 adapter calls with their arguments
    (94 of them submits), the result, what each store was handed and the order the doubles were
    touched in. Every scenario hashes the same before and after;
  - **the order path's requests.** The record comparison (`scripts/ops/crypto_record_capture.py`)
    covers 6,694 lane tests and 10,210 files, 355 of them the request log of the order path's pure
    seams. Five files differ, all in the scheduler's measured `duration_ms` and the event hash over
    it;
  - **mutation.** 35 mutants in the moved code, one rule each. 28 were killed by the existing tests.
    Seven survived: rules of the readers nothing had pinned (a resting leg that spoke, the closing
    side on a naked outcome, an unknown direction, the minute before the open, a malformed history
    row beside a good one, a close made of two orders, the status line). They survive on the old
    file too, so the move did not cause them; seven tests now pin them, and all 35 are killed;
  - the patch-reach census finds no patch on `live_leg` read by a moved function, before or after;
  - the full suite passes.
- **Not done, and not the author's to do:** the plan asks for one signed testnet cycle on a change to
  the send path. It needs the testnet key pair and the testnet opt-in, so an operator runs it:
  `docker exec -u 10001 thomas-scheduler python -m scripts.run_signed_testnet_cycle --run …` on the
  image that carries this change.
- **Independent review** of the refactor commit: no behaviour finding. It listed comments in the
  moved text that said "this module" or "below" and now pointed nowhere; those are reworded.
- **Behaviour change:** none. The change ships with the next candidate.
