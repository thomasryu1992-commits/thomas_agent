# The factory's replay backtest moves to its own module (crypto refactor plan PR-08)

- **What was there:** `factory` (5,477 lines) held the template space, the seeded generator, the
  replay backtest, the ablation lattice, fusion, the hypothesis trials and `run_factory`.
- **What moved:** the replay backtest, whole, to `crypto/backtest.py` (strategy layer, 1,062 lines).
  That is 21 definitions: the evidence-window constants (`HOLDOUT_FRACTION`, `MIN_BARS_FOR_HOLDOUT`,
  `HOLDOUT_PERIODS`, `WALK_FORWARD_PERIODS` and the rest), `holdout_split_index`,
  `funding_charges_per_bar`, `_replay`, `unsuppliable_features`, the holdout and prior-window
  evidence, `ReplayFrame`, `build_replay_frame`, `backtest_spec` and `backtest_spec_pooled`. The text
  is identical line for line except one comment that pointed at a function "below". `factory` (now
  4,471 lines) re-exports all 21 as the same objects.
- **Why the replay first:** the plan listed generation first. Measured, the replay is the only
  section that reads nothing else in the file, and the template space reads `holdout_split_index`
  from it. Moving the space first would have put the holdout split and its two constants in the
  template module, with the wrong owner in the tunables index, to be moved again.
- **What else changed, and why:**
  - five tunables name `crypto/backtest.py` as their owner, because the index test parses the owner
    file for the constant;
  - `backtest` joins `RESEARCH` in the layer test, and the two acting modules that reach research
    (`breaker_watch`, `live_route`) now name it. It is the code they already reached inside `factory`,
    and no new import edge;
  - three tests patched a name on `factory` that the replay reads (`MAX_ENTRY_COST_R` twice,
    `build_feature_rows` once). They patch `backtest` now. The patch-reach census found exactly these
    three on the base tree, and reports none missed on the head;
  - `factory` no longer imports the names only the replay read, so a patch of one of them on
    `factory` fails instead of doing nothing.
- **Evidence that nothing changed:**
  - `run_factory` on synthetic candles (three timeframes; single, cohort and fusion fires) and every
    template replayed at its base parameters hash the same before and after;
  - all 2,950 distinct rules in the production candidates store, read-only, replayed over archived
    candles in the runtime image with no network: 48,719 trades, the evidence hashes the same. The
    candles are the equity archive relabelled to each rule's symbols, so this compares the two trees
    on real rules and real price series, not on the rules' own markets;
  - the full suite passes.
- **Behaviour change:** none. No generation, screening or display logic changed, and the module adds
  no capability. The change ships with the next candidate.
- **Next:** `candidate_ranking`, `forward_confirmation` and `judgement_fingerprint` read only replay
  names from `factory`. Measured on the import graph, pointing the three at `backtest` takes `factory`
  out of what `breaker_watch` and `live_route` reach, leaving `backtest` and `robustness`. Not done
  here, so that this change stays a move.
