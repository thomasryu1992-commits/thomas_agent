# Three follow-ups from verifying the tilt and the standing fixes

- **The 4h record was wrong.** The pooled 4h fire picks templates by its snapshot `symbol`, which is BTCUSDT, so its
  library has 40 families, not 42.
  - It had the same shortfall-tail defect as BTCUSDT 1h, and #1300 fixed both. GEN-1164's S005–S006 match the fix
    rule exactly.
  - `MINT_REALLOCATION_PRE_WINDOW_V0.1.md` gains a correction section and asks for Q4a: the 4h pre-registered
    share moves from 35.7% to 37.5%.
- **`tilted=` on the factory status line.** The fire result has carried `tilted_topup_count` since #1295. The
  scheduler line forwarded only `topup=`, so the first tilt check inferred the tilt from slot order. The line now
  carries both.
- **Holdout readers must read evidence standing.**
  - `tests/test_evidence_standing_readers.py` fails when a module calls `holdout_status` without
    `evidence_invalid`/`evidence_standing`, unless it is exempt with a reason. The two exemptions are `robustness`,
    the scorer, and `measure_oi_lookahead`, which must see the leak.
  - The sweep found one more unaware reader, `pooled_mint_check`. It now leaves INVALID confirmations out and
    counts them.
- **Checked:**
  - Each change has a behaviour test.
  - Fixing `tilted=` at 0 fails its test.
  - The old `pooled_mint_check` fails both its behaviour test and the roster.
