# The factory's seeded generator moves to its own module (crypto refactor plan PR-10)

- **What was there:** after PR-09, `factory` (2,647 lines) held the generator, the ablation lattice,
  fusion, the hypothesis trials and `run_factory`.
- **What moved:** the generator, whole, to `crypto/generator.py` (strategy layer, 939 lines). That is
  23 definitions with identical text: the knobs (`DEFAULT_BATCH_SIZE`, `_MUTATION_SCALE`,
  `_EXIT_PROBE_SLOTS`, `_MAX_ATTEMPTS_PER_SPEC`), the probe slot's stop ceiling
  (`liquidation_admissible_stop_atr`, `cohort_probe_stop_ceiling`), `mutate_params` and its two
  folding rules, the elite centres, `build_spec_dict`, `context_rotation_phase`, `_rotation_offset`
  and `generate_batch`. `factory` (now 1,763 lines) re-exports all 23 as the same objects.
- **What stayed, and why:** `context_rotation_index`. It is the one function in that stretch the
  generator does not call: `run_factory` counts the cursor off the store and hands it in. It also
  reads `is_trial`, which belongs to the trials section, so moving it would have pulled the trial
  vocabulary into the generator or made the generator import `factory`.
- **What else changed, and why:**
  - three tunables name `crypto/generator.py` as their owner;
  - `generator` joins `RESEARCH` in the layer test, and `breaker_watch` and `live_route` name it in
    what they reach, as with `backtest` and `template_space`: code they already reached inside
    `factory`, no new import edge;
  - four patches in six tests sat on `factory` names that `generate_batch` reads (`validate_strategy`
    twice, `templates_for_timeframe` and `mutate_params` once each). They sit on `generator` now. The
    patch-reach census shows the same 20 hits as before the move, none missed;
  - `factory` drops the imports only the generator read (`math`, `random`, `features`, `indicators`
    and three names).
- **Evidence that nothing changed**, base and head run on one frozen copy of the inputs:
  - the generator itself: centres and cursors read off the 3,665 stored candidate rows, the probe
    ceiling off archived candles, then 672 drawn specs and 24 refusals over four timeframes, three
    contexts, six fires each with and without centres, hash the same;
  - the library, the validator verdicts, `run_factory` and the replay of the 2,958 stored rules hash
    the same, as in PR-08 and PR-09;
  - the full suite passes.
- **Behaviour change:** none. No draw, centre, rotation or knob changed, and the module adds no
  capability. The change ships with the next candidate.
- **Where `factory` stands:** 5,477 lines at the start of the plan, 1,763 now, in four modules. What
  is left in it is the rotation cursor, ablation, fusion, the trials and `run_factory`, which is the
  record-writing part the plan said would stay.
