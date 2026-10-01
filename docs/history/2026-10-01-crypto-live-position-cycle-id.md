# A live position names the cycle that opened it (crypto refactor plan J-5.1)

- **What changed (behaviour, approved 2026-10-01):** a live position opened by the autonomous path now
  carries the `cycle_id` of the cycle that opened it, instead of `None`. `build_live_position` has had
  the field all along, but only the signed testnet cycle filled it. The cycle computed its id at the
  end, after the live leg had sent and booked.
- **How:**
  - `cycle.cycle_id_for(symbol, timeframe, now)` computes the id exactly as before, and the cycle now
    calls it before the live leg.
  - The id travels as a keyword argument: `live_route.run_live_leg` → `_run_gated_live_leg` →
    `live_leg.execute_live_entry` → `build_live_position`.
  - The cycle record carries the same value it always did.
- **What it does not touch:** the id is not in `INTENT_BOUND_FIELDS`, so no sealed order changes, and
  it is not in the `position_id` seed, so no position id changes. A caller that does not pass it
  (probe, the existing tests) books `None` as before. No code reads the field from a live position,
  so a rollback to the previous candidate reads such a position unchanged.
- **Pinned:** `tests/test_mvp_runtime_crypto_live_position_cycle_id.py`. A mutation pass killed all
  six mutants: each link of the chain dropping the id, the formula changing, and the record
  recomputing it differently. Like every live entry, it shows in production only when a live
  position opens.
