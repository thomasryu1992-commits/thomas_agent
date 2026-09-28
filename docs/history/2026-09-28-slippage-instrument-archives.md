# The live slippage instrument read the rotated ledger again, and the C2 sample was measured

- **The defect:** `scripts/measure_live_slippage.py` joined live outcomes to their entry and bracket
  legs through `runtime_ledger/records.jsonl` alone. Rotation had moved every live row (positions
  opened 2026-08-04..21) into `archive/`. The script therefore reported "no exit leg has both an
  intended and a realized price yet" and "entries with no recorded intent: 0" over a sample on disk.
- **The fix:**
  - The script reads the archives through `LedgerStore.iter_records_with_archive`, in one pass for
    both legs.
  - A probe's stop is taken from its own outcome's `stop_price`, because probe positions never
    reach `live_opened`. The submitted bracket still wins where both exist.
  - Stops are reported by source (strategy / probe), with the mean beside the median. The mean is
    what a per-trade constant must match (review C2).
  - Stops are compared against `DEFAULT_STOP_SLIPPAGE_BPS` (1.4), not the entry constant (3.0).
  - Two tests pin the archive read and the probe fallback.
- **The measurement (2026-09-28):** read in a one-off container as the service uid. The lock file is
  opened; nothing is written.
  - Stops: n=12, mean 3.06 bps against 1.4 modelled. Strategy stops n=3 (23.47, 0.00, 2.25) and
    probe stops n=9 (mean 1.22).
  - Entries: n=3, all BTCUSDT, all better than intended (−4.67, −4.99, −2.60).
  - Recorded in `REMAINING_WORK.md` §F8.
- **Deliberately not done:**
  - No constant moves: three entry fills are not a distribution.
  - The sample cannot grow at PAPER. Canaries and probe batches are real orders and Thomas's to
    place.
  - The "net at 3/10/23.5 bps" board columns that review C2 also names are a separate change.
