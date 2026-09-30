# The factory's template space moves to its own module (crypto refactor plan PR-09)

- **What was there:** after PR-08, `factory` (4,471 lines) still held the template space, the seeded
  generator, the ablation lattice, fusion, the hypothesis trials and `run_factory`.
- **What moved:** the template space, whole, to `crypto/template_space.py` (strategy layer, 1,895
  lines). That is 91 definitions in one contiguous block: the validator's bounds (`STOP_ATR_RANGE`,
  `MAX_ENTRY_CONDITIONS`, `MAX_FUSION_ENTRY_CONDITIONS` and the rest), the feature vocabulary,
  `ParamSpec`, `StrategyTemplate`, the exit parameter spaces, the entry builders, `TEMPLATES`, the
  family sets, the feed-reach checks, `templates_for_timeframe`, `known_features` and
  `validate_strategy`. The text is identical line for line. `factory` (now 2,647 lines) re-exports
  all 91 as the same objects.
- **Where the cut is:** the block reads nothing else in `factory`. Its only lane imports are
  `features`, `market_data`, `robustness.MIN_HOLDOUT_TRADES`, `strategy` and
  `backtest.holdout_split_index`. The generator's own knobs above it (`DEFAULT_BATCH_SIZE`,
  `_MUTATION_SCALE`, `_EXIT_PROBE_SLOTS`, the probe's liquidation ceiling, `_MAX_ATTEMPTS_PER_SPEC`)
  stay in `factory` with the generator that reads them.
- **What else changed, and why:**
  - seven tunables name `crypto/template_space.py` as their owner;
  - `template_space` joins `RESEARCH` in the layer test, and `breaker_watch` and `live_route` name it
    in what they reach. As with `backtest`, it is code they already reached inside `factory`, and no
    new import edge;
  - one test widened `factory.NUMERIC_FEATURES` to show that an unclassified feature is mintable
    nowhere. `known_features` reads the vocabulary in `template_space` now, so the test patches it
    there, and the classification test it calls reads the same binding. The patch-reach census shows
    the same 20 hits as before the move, none missed;
  - `factory` drops the imports only the space read (`dataclasses`, `ToolBlocked`);
  - the identity test's scan counts annotated assignments, which the space has and the replay did not.
- **Evidence that nothing changed**, base and head run on one frozen copy of the inputs:
  - the library, the tables, and what `templates_for_timeframe` offers in 156 contexts (venue x
    timeframe x symbol x positioning) hash the same;
  - `validate_strategy` on the 2,958 distinct rules in a copy of the production candidates store, and
    on every fifth rule broken nine ways (8,277 verdicts, 4,276 of them refusals across the block
    reasons), hashes the same;
  - `run_factory` on synthetic candles and the replay of those 2,958 rules over archived candles
    (48,738 trades) hash the same, as in PR-08;
  - the full suite passes.
- **Behaviour change:** none. No template, bound, vocabulary entry or verdict changed, and the module
  adds no capability. The change ships with the next candidate.
- **Next:** the generator (`mutate_params` to `generate_batch`) is the remaining section the plan
  names. It reads `is_trial` from the trials section, so that name moves first or with it.
