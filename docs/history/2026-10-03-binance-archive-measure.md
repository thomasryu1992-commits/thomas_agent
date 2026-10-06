# Step 1 of the archive backfill: fetch and measure Binance's public archive, outside the runtime

- **The decision it implements:** `CRYPTO_ARCHIVE_BACKFILL_V0.1.md`, R1 (measure now) and R3 (fetch outside the
  runtime), Thomas 2026-10-03.
- **What this PR adds:** `scripts/binance_archive.py`. It uses the stdlib plus `scripts/lib/utctime`, imports nothing from `runtime`, signs
  nothing, and writes nothing under `.runtime_governance_state/`. Two commands:
  - `fetch` downloads the daily `metrics` files (5-minute OI and the three long/short ratios) and, with
    `--funding`, the monthly `fundingRate` files.
    - Each zip is kept only when it matches the archive's own `.CHECKSUM` sha256, and is written through a
      `.part` name.
    - A day the archive does not serve is recorded in `missing.json` and is not asked for again.
    - A verified file already on disk is skipped, so a fetch resumes where it stopped.
  - `measure` reports, per symbol:
    - first and last day held, and the missing days between them;
    - the span against the 1,000-day (<1d) and 2,000-bar (1d) replay depths;
    - hourly OI against `open_interest_1h.jsonl` at three alignments. The store is read without a lock or a
      write.
- **The one-hour label:**
  - The archive's on-the-hour reading closes the hour before it, and the store labels an hour by its start.
    `hourly_open_interest` shifts the archive back by one hour. Without the shift, a feature would read one
    hour into the future.
  - Smoke run on 2026-09-19..21 against the live store: hourly-change correlation 0.931 (ETH) and 0.973
    (BTC) at the applied shift, against about 0.1 one hour either side.
- **Tests:** `tests/test_scripts_binance_archive.py`, nine tests, no socket opened:
  - no `runtime` import and no `hmac`;
  - a kept file is not fetched twice;
  - a checksum mismatch keeps nothing;
  - a 404 day is remembered;
  - funding is fetched monthly;
  - the label shift;
  - coverage gaps and replay depths;
  - `compare` finds the applied alignment;
  - `measure` leaves the store unchanged.
- **Not in this PR:** writing into `oi_store` (step 2), the positioning gate pin (R2), and the feature switch
  (step 3, at the first verdict).
