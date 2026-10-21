# Research sandbox, step 3 follow-up: the lake carries the Coinalyze daily rows, and the sandbox feed serves them

- **What** (`RESEARCH_SANDBOX_V0.1.md` §4.1, §4.3):
  - `scripts/ops/export_research_lake.py`: `ALLOWED` gains `coinalyze_open_interest_1d.jsonl` and
    `coinalyze_liquidations_1d.jsonl`, the files `coinalyze_daily_store` (#1276) writes. Its refresh marks file
    stays out: it is bookkeeping, not data.
  - `scripts/research/lake.py`: `LakeLiquidationFeed` now has a `feed_id` (`research_lake`) and reads the lake
    snapshot with the store's own `read_rows`. It returns the vendor feed's row shapes and keeps the last `days`,
    as `CoinalyzeLiquidationFeed` does.
- **Why it raises where it does:** no snapshot, no rows for the symbol, and an hourly request all raise
  `RESEARCH_LAKE_NO_COINALYZE`. `attach_feeds` then marks the leg degraded and the OI features are None. An
  hourly request is refused rather than answered from daily rows, because the hourly series is `oi_store`'s and
  a daily row in its place would be a stand-in.
- **Why `feed_id` matters:** `attach_feeds` and `oi_store` skip a feed whose id is missing or `"none"`. Without
  one, the step-2 feed was never asked, so the OI legs read as absent rather than degraded.
- **Checked:** tests for the vendor shapes and trim, unknown symbol, hourly refusal, no snapshot, and
  `attach_feeds` putting the lake rows on the frame while writing nothing to the run's state. The allowlist
  test now names the two files through `coinalyze_daily_store`'s constants.
