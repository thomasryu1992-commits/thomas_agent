# The information sources are shallow, and Binance's public archive is deep (proposal)

- **Why:** the system score (2026-10-03) put research diversity at 4/10 and real edge at 2.5/10. When we asked
  what would raise them, the bottleneck turned out to be not the number of hypotheses but how deep the
  information-source history is.
  - The only family with signal, OI, reads a daily series even at 1h and 4h. At 1d it cannot be minted at all,
    because a daily series of 1,020 days covers about half of the 2,000-bar replay.
  - Positioning and order-book depth have zero lineages behind the same depth gates.
  - S1, decided 2026-10-01, tilts minting toward 1d, which is exactly where OI is missing.
- **What was measured:**
  - `data.binance.vision` serves 5-minute open interest and three long/short ratios: BTC from 2020-09, the four
    alts from 2021-09 to 2021-12. It also serves funding rates and banded book depth.
  - The archive's hourly OI matches the Coinalyze-seeded `oi_store` once a one-hour label shift is applied:
    hourly-change correlation 0.94, median level gap 0.09%.
  - Accumulating at today's pace instead reaches the 1,000-day depth in 2029.
- **What this PR adds:** `docs/proposals/CRYPTO_ARCHIVE_BACKFILL_V0.1.md` (DRAFT, R1–R4) and `STATUS.md`
  regenerated. No code. The recommendation:
  - measure and backfill OI now, which changes no feature because `oi_store` feeds nothing;
  - switch the OI feature source at the first cohort verdict together with S1;
  - pin the positioning gate explicitly before any positioning backfill, because its coverage measurement is
    the switch that mints the family;
  - fetch the archive outside the runtime, so that runtime egress is unchanged;
  - leave order-book depth until after D3.
