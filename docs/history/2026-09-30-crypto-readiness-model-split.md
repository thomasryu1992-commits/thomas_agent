# The readiness board's judgement moves to its own module (crypto refactor plan PR-07)

- **What was there:** `live_readiness` (2,108 lines) held three things: collecting the facts (its own
  switches and a signed account read on the trading process, the recorded state elsewhere), judging
  them (the v2 readiness model: eleven three-valued components and their AND), and rendering the board.
- **What moved:** the judgement, whole and unchanged, to `crypto/readiness_model.py` (report layer,
  593 lines). That is 36 definitions: the model's constants, the component functions,
  `readiness_state`, `readiness_data`, `minority_may_enter`, `contradicts_recorded_gate` and
  `env_out_of_scope`. It reads the report mapping and nothing else: its only imports are four control
  constants and one `live_pnl` reason code. `live_readiness` (now 1,542 lines) imports it and
  re-exports every public name as the same object.
- **Why this cut:** the block had no reader of its own, so it moved without touching a call site.
  The three names tests patch on `live_readiness` (`read_account`, `build_readiness`,
  `resolve_execution_stage`) are all outside it.
- **Evidence that nothing changed:**
  - the board's report, data, state and text hash the same before and after, at three fixed clocks,
    on an empty state directory and on the production state mounted read-only (Python 3.12 image,
    no live environment, no network);
  - the full suite passes;
  - two new tests pin the re-export identity, and that the model judges a report the same once the
    board's readers refuse, the environment is empty and the state directory is gone.
- **Behaviour change:** none. The change ships with the next candidate.
