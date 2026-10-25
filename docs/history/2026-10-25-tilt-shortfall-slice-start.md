# The plain shortfall turns where it starts in its slice

- **Why:** Checking the tilt after its first fires (2026-10-25) showed the plain shortfall always keeps the
  head of slice `2k+1`. It used to keep the whole slice, so the tail was rarely dropped. With the tilt taking
  half the slots, the tail is now dropped on every fire.
  - In a library that is a multiple of eight families, the batch walks one parity of slices and the
    shortfall the other, so "the next pass" never reaches the tail.
  - BTCUSDT 1h has forty families, so ten of them would never be minted there again. The 42-family contexts
    (the other 1h symbols, pooled 4h) were unaffected.
- **What:**
  - `generate_batch(slice_start=)` starts a draw part-way into its slice.
  - `factory.shortfall_slice_start` sets that start for the plain shortfall, turning it once per *visit* to
    the slice. A slice comes back every `total // gcd(2·count, total)` fires.
  - A fire that keeps its whole slice keeps start 0 and draws exactly what it drew before.
- **Not the fire count:** turning the start with the fire count fails for 32- and 48-family libraries. There
  the visit period is even, so every visit lands on the same start.
- **Also:** `MINT_REALLOCATION_PRE_WINDOW_V0.1.md` gains Q4, which asks to move the mechanical check from
  11-22 to one rotation (11-14). It pre-registers the share at 36% for 1h and 35.7% for 4h, and makes the
  fusion count report-only.
- **Checked:**
  - The real generator, walked over two BTCUSDT 1h cycles, reaches all forty families. Keeping the head
    loses some, and the test asserts it does.
  - The offset walk leaves no family unreachable for any library of 26–60 families, keeping 1, 2 or 3 of
    the slice.
  - `run_factory` passes the turned start, on a cursor where it is not the head.
  - Mutations caught: dropping the wiring, a fire-count rule (fails at 32 and 48), and head-only.
