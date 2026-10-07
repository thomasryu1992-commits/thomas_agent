# The hierarchical verdict is code; building it found five gaps in the rule, decided before any reading

- **What:** `scripts/selection_evidence.py verdict` issues the judgement H2 fixed for each epoch
  boundary: stage 1 (BH q = 0.10 over timeframe × economic family), the swap calibration gate,
  stage 2 (Holm inside a passing family), and H1's S1 line. Read-only. It refuses before a cohort's
  close (`SELECTION_VERDICT_NOT_DUE`) and while the walker has not reached it
  (`SELECTION_VERDICT_WALK_BEHIND`). It counts only trades settled by the close
  (`forward_cohort.member_twin_pairs(settled_by=…)`), so any later run reproduces the reading.
- **The five gaps (Thomas 2026-10-07, "권고대로"):**
  - Holm α 0.10, over every lineage of the family.
  - The ten-day floor applies to lineages too.
  - Every frozen cohort's pairs count, up to the close.
  - The calibration centres each family before swapping.
  - A rejection only PASSES when the selection is ahead; behind is BEHIND.
- **Why centring:** swapping pairs that carry a real edge δ creates a ±δ spread the data never had.
  The day-clustered test then rejects the swaps too often, so the gate withheld the families that
  lead.
  - On synthetic pairs, the uncentred rate was 0.11–0.30 with a 1R edge against 0.04–0.21 with none.
    Centred, the rate equals the no-edge rate seed for seed.
  - Live on 2026-10-07, 1h read 0.140 uncentred, which is over q and so withheld; centred it read
    0.020. `families` shares the function, so its calibration column moved too. Nothing else in its
    output did, and `report --pairs` is byte-identical.
- **Why the walker check:** the daily walk lands about 07:16Z and cohort 1 closes at 07:03:48Z. A
  reading in between would miss the last bars, and a later run would not reproduce it.
- **Unchanged:** no door, board, ranking, constant, schedule or mint share reads or changed. S1's
  execution waits for the first verdict.
