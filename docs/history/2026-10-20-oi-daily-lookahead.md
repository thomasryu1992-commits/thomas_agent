# Daily OI was aligned on its label, not when it was known: 1h/4h replay read the day's close early

- **Found** while checking whether the OI feature switch (archive backfill step 3) could be applied.
  - The vendor labels an OI row with its period's start and carries the period's close. This is
    measured on the hourly series (store t = archive t+1h, correlation 0.94) and inferred for daily.
  - `features._asof_align` matches events on the bar's open, and nothing shifted the label in between.
  - Every 1h/4h bar of day D read day D's closing OI: up to ~23 h early in mint, walk-forward and holdout replays,
    and in the daily cohort walk for bars of completed days.
  - Live read D-1, because it drops the forming day. 1d had no lookahead.
  - The convention dates from 2026-07-25 (`f279f985`), so every 4h-OI result the research documents call the only
    signal was produced under it.
- **Fix:** daily OI events are keyed by label + `OI_READING_KNOWN_AFTER_HOURS` (24) before alignment. That is the
  `_htf_columns` rule: key by the instant an event is known, and match on the bar's open. Replay and live now read
  the same value on every bar; a test simulates live with the parser's own forming-day rule. The parser's label
  is unchanged, because `oi_store` and the archive import depend on it.
- **Flag (Thomas 2026-10-20: flag, do not re-judge):** `candidate_ranking.oi_lookahead_affected` is derived at read
  time, like `holdout_reused`. A spec whose entry reads an OI change or z-score, on a timeframe other than 1d,
  created before `OI_FIX_EFFECTIVE_AT`:
  - has its CONFIRMED holdout read as UNDERPOWERED, so it is not ROBUST;
  - cannot arm LIVE on a FORWARD_CONFIRMED record.
  `OI_FIX_EFFECTIVE_AT` is None until the deploy's restart instant is recorded, so until then every affected lineage
  is flagged. Parenting, centring and ranking expectancy are not covered; that is a separate decision.
- **Checked:**
  - Mutation checks: reverting the shift, removing either cap, flagging 1d, or ignoring the cutoff each fails a test.
  - Full suite: 11445 passed. Two pipeline tests fail identically on `main`.
- **Next:** a read-only paired measurement (legacy vs fixed alignment, same holdout window). Its criteria are in
  `CRYPTO_ARCHIVE_BACKFILL_V0.1.md`, written before it runs.
