# Step 2 of the archive backfill: archive OI into `oi_store`, as the service user

- **The decision it implements:** `CRYPTO_ARCHIVE_BACKFILL_V0.1.md` R1 (backfill OI now) and R3 (fetch outside
  the runtime), Thomas 2026-10-03. The positioning gate it depends on was pinned in #1120.
- **What this PR adds:**
  - `scripts/binance_archive.py export-oi`. On the host, it writes the fetched hourly OI as `oi_store`-shaped
    JSONL on stdout, already relabelled to the hour's start. The host never writes the store.
  - `scripts/import_archive_oi.py`. Inside the container, as uid 10001, it reads that JSONL on stdin.
    - A row is refused, never repaired, unless it has an allowed symbol, an hour label for a closed hour, and a
      finite positive value.
    - Rows are written through `oi_store.append_rows`, which drops any `(symbol, hour)` already held, so a
      vendor row always stands.
    - The default run is a dry-run report. `--confirm` writes, behind the host-root guard, and appends one line
      to `open_interest_1h.archive_imports.jsonl`: input sha256, what was offered, written and refused, per
      symbol. The store's own row shape is unchanged.
  - Rosters: `scripts/import_archive_oi.py` is `RESEARCH_WRITE` in the crypto script roster, and is guarded at
    the write point.
- **What it changes when run:** nothing that decides anything. `oi_store` feeds no feature, so only the board's
  OI coverage line moves. Switching the OI feature source is step 3, at the first cohort verdict, with S1.
- **Positioning is not backfilled here, by measurement.**
  - `cycle` reads the whole positioning store every pipeline fan-out (`positioning_store.read_rows_grouped`,
    every 15 minutes).
  - Measured 2026-10-03 at today's size: 40,839 rows in 0.24 s, process RSS 129 MB. At 1,000 days that is
    about nine times the rows on every fan-out, on a host with about 950 MB available.
  - The rows are worth nothing until positioning minting is decided (after 2027-03-22). The storage question
    goes back to Thomas before that backfill: a separate archive file read only by coverage and the factory, a
    windowed read in `cycle`, or waiting.
- **Tests:** `tests/test_scripts_import_archive_oi.py`:
  - each refusal reason;
  - the plan's counts and fingerprint;
  - a held hour keeps the vendor row, and the import is recorded;
  - the dry run writes nothing;
  - `--confirm` through the CLI;
  - the host export and the importer agree on shape and label (the archive's 01:00 reading becomes the
    store's 00:00 hour).
- **Decided (Thomas 2026-10-03):** option (c), wait.
  - Positioning backfill is deferred until minting approaches (after 2027-03-22).
  - When it does, the archive rows go in a separate file that only coverage and the factory read, never the
    `cycle` fan-out's full read.
  - Recorded in the proposal's "추가 결정" section.
