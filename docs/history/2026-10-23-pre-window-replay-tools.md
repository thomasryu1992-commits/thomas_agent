# Pre-window replay: the tools (fetch back to 2019, an as-of lake, the replay)

- **Why:** `PRE_WINDOW_REPLAY_V0.1.md`, decided 2026-10-23 (Q1 cohort members only, Q2 an input to
  mint-reallocation proposals only, Q3 public data back to 2019).
  - Forward cohort members cannot be told apart from their coin-flip twins, and forward alone is unlikely to
    answer before 2027-03-22.
  - 1h and 4h lineages never saw the roughly four years before their 1,000-day mining window.
- **What:**
  - `fetch_public --since` extends a held series backward to a date, under the same budget, factory-window
    refusal and closed-bar rule. It never re-asks for a held bar.
  - `lake.as_of(instant)` cuts every lake read at an instant:
    - candles and price series closed before it, through the runtime parser's own `now_ms`;
    - funding settled by it;
    - Coinalyze days closed by it.
  - `scripts/research/pre_window_replay.py`:
    - The population is fixed before the replay: 1h and 4h members whose entry reads only pre-2024 feeds,
      plus their active twins. 1d is reported, not judged.
    - Each calendar year is replayed as a collector at the year's end would have held it.
    - Replay runs through `backtest._replay` with the frame's cost and funding, as `backtest_spec` calls it.
    - Twins are built the forward walker's way.
    - The reading is the decided H2 stage, then LEAD, NULL or ANTI as pre-registered.
- **Checked:**
  - Tests cover the population filter, the window cut, twin determinism and seed dependence, the warm-up
    refusal, the reading rule, the overall label, and the refusal outside the sandbox.
  - The as-of cut is tested on all four series, and `--since` on klines, funding and argument parsing.
  - Mutations each fail a test: no window cut, ignored seed, no window-consistency in LEAD, uncut candles,
    uncut funding, no backward extension.
- **Not yet run.** The fetch and the official measurement follow the merge. Their result goes under the decision
  in the proposal.
