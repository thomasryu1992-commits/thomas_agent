# Point-in-time contract test (E3), and the daily liquidation lookahead it found

- **Why:** the daily OI lookahead ran from 2026-07-25 to 2026-10-20 and passed every test, because those tests
  ordered rows by their labels. Coinalyze labels a daily row with its period's START and carries its close
  (OI) or its total (liquidations). A label-ordered check cannot see that. E3 of
  `CRYPTO_ARCHIVE_BACKFILL_V0.1.md`, "결정 (Thomas 2026-10-21)", asks for a test that would have caught it.
- **The test** (`tests/test_mvp_runtime_crypto_point_in_time.py`):
  - `AVAILABLE_AT` declares when each snapshot series' rows are known in the world. These are the vendor's
    semantics, stated independently of the constants in `features`:
    - candles, HTF, reference and peer candles: at their `close_time`;
    - mark, index and premium: at the close of their grid bar;
    - funding: at the settlement;
    - Coinalyze daily OI and liquidations: label + 24 h.
  - The keys `build_feature_rows` reads are recorded by running it on a dict that logs every lookup. Every
    recorded key must be declared, metadata, or `UNVERIFIED`.
  - For 20 bars, the row built from the full snapshot must equal the row built from the snapshot cut at
    that bar's close, column by column. The fixture fills every column but positioning, and a test pins
    that, so a None-equals-None pass is impossible.
  - Setting either daily vendor constant back to 0 makes the test report leaks in its own columns, so the
    test is shown to catch both bugs.
- **Found while writing it: the daily liquidations had the same lookahead.**
  - `features` aligned them on the label, so a 1h/4h bar of day D read day D's total in replay.
  - Fix: `LIQUIDATION_READING_KNOWN_AFTER_HOURS = 24` via the shared `_known_after`, the OI rule.
    `_oi_known_at` still reads its constant at call time, because `measure_oi_lookahead` switches it.
  - Live is unchanged, because it never holds the forming day. A 1d bar now reads D-1, as with OI.
  - The source-parity fixture is 1d with daily liquidations, so its four liquidation columns join
    `SOURCE_DEVIATIONS` with this decision named.
  - Store impact at lake `20261021T041701Z`: one sub-daily spec (1h) and one 1d spec read a liquidation
    feature at entry; neither is CONFIRMED.
  - Flagging the 1h spec as INVALID evidence is a one-line reason in `candidate_ranking.evidence_standing`
    (#1280, E1), plus the effective instant after this deploys. That follows #1280.
- **Positioning is `UNVERIFIED`:** its rows store no fetch time, so whether a label is a period start or a
  point reading is unmeasured. No spec can read positioning while `positioning_store.MINTING_DECIDED` is
  False, and the test pins that. Opening minting fails the test until the availability is measured and
  declared.
