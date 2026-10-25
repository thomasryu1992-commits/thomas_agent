# The funnel's family table (FAMILY_EXHAUSTION B, decided 2026-09-24)

- **Why:** Thomas chose B on 2026-09-24: no SEARCH_EXHAUSTED state, only a regular measurement, with retirement
  kept as a `RETIRED_FAMILIES` change. Its one build item, a family section in the funnel report, was never
  built.
- **What:** `strategy_funnel.family_exhaustion` lists, per strategy family:
  - rows minted in the last 30 days and how many were holdout-CONFIRMED;
  - the last CONFIRMED mint and the number of CONFIRMED lineages;
  - forward cohort members and their maturity.

  `scripts.strategy_funnel` prints it as `FAMILIES`, and `--json` carries it as `families`. It is not shown on
  the board.
- **Counting:**
  - CONFIRMED is recomputed under today's rule and leaves out INVALID evidence; BIASED rows are marked.
  - The 2026-09-24 signal that motivated the table was the OI families' CONFIRMED rows. Since the OI lookahead
    fix, those rows are INVALID.
  - A re-score is not a mint. It is dated by the candidate it re-scored.
- **Checked:** synthetic stores, and three mutations each caught by a test: counting INVALID confirmations,
  counting re-scores as mints, and dating a re-score by itself.
