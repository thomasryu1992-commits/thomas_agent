# Mint tilt: half the 1h/4h shortfall draw goes to the six breakout / volatility-expansion families

- **Why:** `MINT_REALLOCATION_PRE_WINDOW_V0.1.md`, decided B on 2026-10-23.
  - The eight-seed confirmation run passed: 1h `hybrid(fused)` +0.146 and 1h `volatility_expansion` +0.114 are LEAD
    again. 4h `hybrid(fused)` (+0.094, p 0.085) is now NULL.
  - Runs: `20261023T090103Z`, main `5424da04`, lake `20261023T081701Z`, fingerprint `f50dfce1c444`.
- **What:**
  - `factory.MINT_TILT_FAMILIES` holds `breakout`, `breakdown_short`, `bollinger_breakout`,
    `bollinger_breakdown_short`, `volatility_expansion_long` and `volatility_expansion_short`.
  - `MINT_TILT_TIMEFRAMES` is 1h and 4h.
  - `tilt_slots` sends half of the shortfall draw to those families. An odd slot alternates with the context's
    fire count.
  - `generate_batch(families=...)` rotates over only those families, with its own cursor and seed.
  - The other half keeps the next library slice. Fusion keeps first refusal, and the fire's volume is
    unchanged.
  - `tilted_topup_count` is recorded inside `seeded_topup_count`.
- **What it does not do:** it changes no verdict, door, holdout, cohort rule or template list. A tilted row is
  judged like any other.
- **Checked:**
  - Tests cover the half split and the odd alternation, and confirm 1d is untouched.
  - The family filter is tested, including its refusal of an empty set.
  - A 4h fire with fusion declined draws its shortfall 2 plain + 2 tilted, every tilted row from the six families,
    one duplicate guard across draws.
  - A 1d fire tilts nothing.
  - Mutations fail tests: tilt off, filter ignored.
- **Reverting:** empty `MINT_TILT_TIMEFRAMES`. The proposal's §5 sets the 30-day mechanical check and the reversal
  conditions. The 4h evidence weakened in the confirmation, so the PR asks whether to keep 4h.
