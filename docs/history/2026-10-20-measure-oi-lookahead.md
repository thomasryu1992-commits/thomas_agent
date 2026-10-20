# A paired, read-only replay to measure what the OI lookahead was worth

- **What:** `scripts/measure_oi_lookahead.py`.
  - **Selection.** Every sub-daily candidate whose entry reads an OI change or z-score.
  - **Replay.** Each frozen spec is replayed twice over the same current snapshot (built by
    `rescore_stale_holdout_candidates._build_snapshot`, the factory's own feeds): once with the legacy alignment
    (`OI_READING_KNOWN_AFTER_HOURS` = 0) and once with the fixed one (24).
  - **Report.** Per candidate: holdout trades, expectancy and status under both. Pooled: per-trade mean, the
    period-level 95% lower bound over period subtotals summed by index, and CONFIRMED counts. Plus the
    pre-registered reading: KEPT, WEAKENED or VANISHED.
- **Why paired on today's window:** the stored holdouts were scored on older windows, so comparing them to a
  replay would mix the lookahead with the window change. L and F differ only in the OI alignment.
- **Why period-summed:** siblings share market periods (six of the eight 1h confirmations were DOGE variants), so
  periods are the unit, which is `robustness`'s own rule.
- **Read-only:** writes no candidate, verdict or state. `--json` prints the report. The script roster lists it as
  READ, and it reads candidates from their owner modules, not through `pool`.
- **Checked:**
  - Tests: the selection; the alignment switch restored even on error; L and F differ on the same window;
    pooling by period index; every branch of the reading rule; a full run writes nothing under the root;
    `--list` collects no data.
- **Not run here:** Claude has no container access. Thomas runs it after #1269 is deployed, and the result goes
  into `CRYPTO_ARCHIVE_BACKFILL_V0.1.md`.
