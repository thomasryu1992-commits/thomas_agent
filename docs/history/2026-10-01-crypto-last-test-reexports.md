# The last movable test-only re-exports go (crypto refactor plan PR-16, second follow-up)

- **What changed:** #1093 left eight re-exports that only tests read. In each case the reading test
  file used the defining module's name as a local or a parameter (`cost`, `order_request`,
  `state`), so the module could not be imported there under its own name. Six of them now import
  the names directly, without aliasing a module:
  - `test_mvp_runtime_crypto_factory.py` takes `CostModel` and the three `FUNDING_SOURCE_*` from
    `cost`.
  - `test_mvp_runtime_crypto_live_execution.py` and `…_bracket_overfill.py` take
    `ALGO_TYPE_CONDITIONAL` and `MISMATCH` from `order_request`.
- **Removed re-exports:** those six, from `factory` (its whole `cost` import, which it no longer uses)
  and from `live_execution`. The roster now pins 115 re-exports in 9 modules.
- **Kept:** `paper.STATE_REL` and `live_pnl.STATE_REL`.
  `test_the_crypto_state_root_has_one_definition` reads them to pin that both modules have
  exactly one state root, the one in `state.py`. Dropping them would weaken that pin.
