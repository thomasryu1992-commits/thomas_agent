# Pre-window replay: pooled twin draws for the confirmation run

- **Why:** `MINT_REALLOCATION_PRE_WINDOW_V0.1.md` was decided as B on 2026-10-23. A second twin seed had shrunk
  the pre-window LEADs to the boundary, so before tilting the topup, the same replay runs again with each twin
  pooled over eight seeded draws (§5).
- **What:** `pre_window_replay --twin-seeds K`. The twin's trades are pooled over the draws `seed|1`..`seed|K`.
  K = 1 keeps the twin's own seed and is the decided rule, byte for byte. Every report stamps `twin_seeds`.
- **Checked:**
  - A test shows K = 1 equals the default.
  - With K = 3, the twin is exactly the union of three single-seed replays and the member is unchanged.
  - K < 1 is refused.
  - Making the seed list ignore K fails the test.
- **Also:** corrects the #1292 history note. The sandbox runner tests run in CI too; #1291's CI failed them on
  the clock at 07:42.
