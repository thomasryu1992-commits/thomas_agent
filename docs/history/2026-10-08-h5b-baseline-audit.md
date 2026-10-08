# H5b: every drawdown baseline change leaves a chained line

- **What this PR changes:**
  - `holdings/baseline_log.py` (new): the append-only, hash-chained `holdings_baselines.jsonl` in the
    holdings state directory. It records two events:
    - `reset_requested`: reason, requester, the peak it forgets.
    - `baseline_set`: the cause (bootstrap, first_complete, after_reset, scope_change), the new
      baseline, the previous peak, the scope and the classification `mapping_version`.
  - `holdings/combined.py`:
    - When the log begins, the standing peak is recorded once (bootstrap), never reset.
    - Every new peak is logged before the peak file is written.
    - `reset_peak` needs a reason and logs the request before removing anything.
    - The block carries `baseline_id`.
  - `holdings/store.py` reads the classification once per fire and passes its version.
  - `scripts/holdings_board.py`: `--reset-peak --reason TEXT [--by NAME]`.
  - Tests: `tests/test_mvp_runtime_holdings_baseline_log.py`.
- **Why:** after a deposit or withdrawal the peak is reset by hand, and until now that left only a
  changed file. Thomas asked that a baseline change be explainable later: what it was, what replaced
  it, why, and under which scope and classification.
- **Shaped this way because:**
  - **The line goes before the change.** A broken chain or a failed append stops the combined block, so
    a baseline never moves unrecorded.
  - **Amounts are whole KRW.** The kernel's canonical hashing refuses floats.
  - **Local only.** The lines carry totals, so no door reads the log. Only the id leaves.
- **What it does not do:** it does not reset today's baseline. It adds no cash-flow accounting; that is H6.
