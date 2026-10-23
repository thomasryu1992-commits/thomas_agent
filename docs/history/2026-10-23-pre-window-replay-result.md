# Pre-window replay: the result (LEAD on 1h and 4h), and the fingerprint the report now stamps

- **Result:**
  - The run was official: sandbox, main `30cd141d`, lake `20261023T041701Z`, run `20261023T045008Z-160577`,
    judgement fingerprint `f50dfce1c444`.
  - Cohort members beat their coin-flip twins on 2020–2023:
    - 1h: +0.083 R/trade [+0.017, +0.148], 27 of 40 ahead.
    - 4h: +0.067 R/trade [+0.018, +0.116], 47 of 63 ahead.
    - Calibration false-pass rates were 0.00 and 0.03, against q = 0.10.
  - LEAD families: 1h `hybrid(fused)` and `volatility_expansion`, and 4h `hybrid(fused)`. Each is positive in
    all four windows. There is no ANTI.
  - The full table is in `PRE_WINDOW_REPLAY_V0.1.md`.
- **Diagnostics** (non-official runs, recorded with their ids):
  - The difference is gross, not cost: gross diff +0.071 (1h) and +0.062 (4h); cost diff about 0.
  - At 1h it decays year by year, from +0.149 to +0.030. At 4h it holds near +0.06 after 2020.
  - A second twin seed keeps the direction but shrinks the LEAD families to +0.105–+0.131. The two hybrid
    families' intervals then just include 0. The LEADs are marginal.
  - Forward cannot resolve an edge this size yet. Its 4h interval is [−0.237, +0.230].
- **Fix:** the report did not stamp the judgement fingerprint or the lake snapshot that §4 asked for. `provenance()`
  now adds both, and a test pins it.
- **What it changes:** nothing in verdicts, holdouts, doors, templates or mint. Per Q2, a mint-reallocation
  proposal for the breakout and volatility-expansion families follows as its own draft.
