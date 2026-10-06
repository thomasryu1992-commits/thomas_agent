# The halt replies say a running task finishes; the streak latch and three stale comments say what is true

- **Why:** the build queue the scorecard Q3 decisions left (`docs/proposals/SYSTEM_SCORECARD_V0.1.md`),
  the small half of it.
- **CONTROL K1:** `/kill` and `/pause` now say "A task already running is not interrupted; it runs to its
  end", in the reply (`control.apply_command`), `docs/DEPLOYMENT.md` and `OPERATOR_CONTROL_CHANNEL_V0.1.md`.
  The mid-run peek makes the halt real at once, but aborting a run is K4, which was not bought.
- **EVALUATION §8 D (i):** `guards.run_risk_guard` says why the consecutive-loss breaker alone latches —
  a streak ends only on a strategy win, which the tripped breaker refuses — and that a person clears it.
- **THROUGHPUT P0-2:** three comments that contradicted the runtime: the LIVE door's `observed_lineages`
  raises z (#1047); `CONFIDENCE_Z` is the per-candidate floor and `selection_adjusted_z` charges attempts;
  the template count is gone from `search_context_key`. No report prints the stored ROBUST label, so that
  half needed no code.
- **COST §6-3, measured:** the 22 entries the cost gate refused (11 days, 21 on 1h) all hit their stop
  (gross −1R; 12 on the first bar; stops 22–62 bps). No ground to relax `MAX_ENTRY_COST_R`.
- **What it does not do:** no behaviour change except the two reply strings; no deploy (the bot's text
  changes on the next one).
