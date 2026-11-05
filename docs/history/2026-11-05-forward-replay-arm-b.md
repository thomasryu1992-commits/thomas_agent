# 2026-11-05 — Historical forward replay, part 2a: arm B (market regime, the generator's gate, the near-high template)

**What.** `forward_replay --arm B` replays the procedure with three additions. All three are wired inside the
replay process by `arm_b()`, which restores everything on exit. No factory file changes.
- `mkt_regime`: the BTC daily `btc_only` label (CRYPTO_REGIME_STRATEGY_SET_V0.1 §1, three-day confirm). It is
  aligned with the HTF rule, `features._asof_align` keyed on the daily close.
- A gate on exactly half of each draw. The odd-numbered specs get one regime, chosen from the spec's identity.
- `near_high_long` added to the 1d rotation.

**Why the gate is by spec number and not a hash.** The first smoke drew one gate in seven. The decision says
half of each batch is gated, so the parity of the spec's own number makes that exact.

**What the factory still decides.** The train-only ablation lattice can drop a gate that does not earn its place,
as it can drop any condition. The smoke fire showed exactly that.

**Tests.**
- The daily label equals `regime_strategies_v02.labels_from(raw_btc_only)` on the same closes.
- A bar on the day the label changes still sees the previous day's label. This catches an own-day leak mutant.
- The gate parity, the vocabulary, the 1d-only template, and that `arm_b()` restores every binding it touched.
- Seven arm-B mutants are all caught.
