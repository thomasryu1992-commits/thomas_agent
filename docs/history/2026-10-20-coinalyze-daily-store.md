# Research sandbox, step 3: keep the daily Coinalyze rows the runtime already fetches

- **Why:** the factory and the live cycle fetch Coinalyze's daily open interest and liquidations on every fire,
  use them for the frame, and keep nothing. That left the keyed series as the one input a key-less research
  sandbox could never replay (`RESEARCH_SANDBOX_V0.1.md` §3).
- **What:**
  - `crypto/coinalyze_daily_store.py`: `coinalyze_open_interest_1d.jsonl` and `coinalyze_liquidations_1d.jsonl`.
    Both are append-only and deduplicated on `(symbol, timestamp)` before the write. Rows are self-hashed and
    keep the vendor's label (period start) and value (close). A reader must align them by when they are known.
  - `attach_feeds(record_vendor_daily=True)` hands the rows it just fetched to `record()`. **No new request.**
    The throttle is one attempt per symbol per 6 h, and failures are degrade-only.
  - Only two callers opt in: the scheduler's factory (`attach_mining_legs(record_vendor_daily=True)`) and the
    live cycle. Operator and measurement scripts keep the default (off) and stay read-only. A test pins the
    writer set to those two files.
- **Not changed:** features read the live fetch as before; nothing reads the store yet.
- **Checked:**
  - 6 tests: record once then only new rows, the throttle, hash and shape checks on read, never raises, no extra
    vendor asks, mining legs default off, and the writer enumeration.
  - Mutation checks: throttle off, always record, hash check off. Each fails a test.
- **Next:** after #1274 and #1275, the export allowlist and `LakeLiquidationFeed` read these two files. That needs
  a deploy of this change first, so the store starts filling.
