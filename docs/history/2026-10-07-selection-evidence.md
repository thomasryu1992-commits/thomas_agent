# The collected data says selection does not yet beat a coin flip; the hierarchical judgement is drafted and dry-run

- **What was asked (Thomas 2026-10-07):** raise the strategy-selection score's weak points in order and
  verify them on the data already collected, not on data still to come.
- **What this PR adds:**
  - `scripts/selection_evidence.py` (READ): `null-control` folds the newest `crypto_null_control`
    fire per cell into specs (trade-weighted over legs) and lineages (`promotion_backlog._lineage_key`).
    `families` pools the cohort's member-minus-twin pairs per timeframe × economic family with the
    day-clustered interval. It then dry-runs stage 1 of the hierarchical judgement (BH q = 0.10 over
    families with ten or more settlement days) and calibrates it by swapping member and twin inside
    random pairs.
  - `scripts/forward_cohort.py`: `member_twin_pairs` split out of `pair_differences`, which now uses
    it. `report --pairs` prints the same thing.
  - `docs/proposals/SELECTION_EVIDENCE_V0.1.md` (DRAFT, H1 and H2 for Thomas), with pointers in
    `CRYPTO_STRATEGY_EDGE_ORDER_V0.1.md` (S1) and `RESEARCH_FORWARD_THROUGHPUT_ANALYSIS_V0.1.md`
    (P1-4).
- **What it measured (2026-10-07):**
  - Post-mint against 12 coin flips, by lineage: 1h −0.031R with 18 of 40 ahead, 4h −0.044R with
    28 of 75. By spec it reads worse (−0.131R and −0.072R) only because the weak families hold the
    most specs. 1d cannot be measured this way: one spec reaches the trade floor.
  - Pairs: 1d −0.711R [−1.282, −0.140] over 26 pairs. 1h and 4h are not distinguishable from zero.
  - Stage 1 passes no family at any timeframe. The swap calibration's false-pass rate is 0.000,
    0.055 and 0.000, all under q.
  - OI leads on every record (1h pairs +1.082R [+0.112, +2.053]), but on few lineages, most of
    them DOGE parameter variants.
- **Why direction only for null control:** the record keeps per-spec aggregates, and every spec in a
  cell shares one post-mint market. The unit of independence is the market period, so a count of
  lineages ahead is not given a p-value.
- **What it does not do:** no judgement, door, board, ranking, constant, schedule, mint share or
  template changed. The OI feature switch stays at the first cohort verdict (archive backfill R1). It
  builds no pre-sample replay. The 1h/4h windows before each mint's 1,000-day mining span are unseen
  data, but replaying them needs archive klines and a local collector; that is a separate measurement
  proposal.
