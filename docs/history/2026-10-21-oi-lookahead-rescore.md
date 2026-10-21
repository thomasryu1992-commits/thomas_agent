# OI-lookahead re-score (E2): true numbers for the search, no confirmation for the doors

- **Why:** E1 (#1280) makes the OI-lookahead rows INVALID, so they stop steering the search. Hiding them
  alone would leave the search blind where OI does work. The re-score measured that the edge vanished
  overall, but not everywhere: BNBUSDT `oi_unwind_short` held up under the fix. E2 of
  `CRYPTO_ARCHIVE_BACKFILL_V0.1.md`, "결정 (Thomas 2026-10-21)", replays each such spec under the fixed
  alignment.
- **What** (`scripts/rescore_stale_holdout_candidates.py --oi-lookahead`):
  - Selects every row whose standing is INVALID by `oi_lookahead`, on 1h and 4h, taking the latest row per
    id and skipping sources already re-scored. 15m is refused: its 96,000-bar frame would only stay
    INVALID anyway.
  - Pooled scopes are replayed pooled (`backtest_spec_pooled`) and hashed the way the factory hashes them.
    The old mode skipped every multi-symbol scope, and the 4h pooled context holds most of the stored
    CONFIRMED.
  - Snapshots are freed once no later context needs them (the #1278 rule).
  - Each new row carries `rescore_reason = OI_LOOKAHEAD_RESCORE_REASON`, which makes it BIASED: it may
    steer the search, but its holdout confirms nothing. A verdict comes only from a lineage minted after
    the fix. Without the reason, a post-fix `created_at_utc` would have passed the OI flag, a laundering
    hole E1 closes.
- **Where it runs:** computed in the research sandbox, appended in production.
  - Run in production, the replay would re-fetch 11 contexts of market data on the IP the order path
    shares, roughly 11 factory fires back to back, in a scheduler container with no memory limit.
  - So `--emit PATH` writes the rows from the sandbox, which has a capped account, the public cache and no
    keys.
  - `--import PATH` validates the file against the live store and appends it with `--apply`. It fetches
    nothing.
- **What `--import` checks** (all or nothing):
  - spec, rule hash, strategy id, generation and lineage equal the source row's frozen values;
  - the source is INVALID by the OI lookahead and not already re-scored;
  - the id derives from the row, and no `record_sha256` is supplied (the store stamps it);
  - `created_at_utc` is after `OI_FIX_EFFECTIVE_AT`;
  - the row's standing is BIASED by the re-score.
  - The evidence numbers themselves cannot be re-checked without re-fetching. A wrong number is bounded by
    what BIASED allows: it steers the search and confirms nothing.
- **Checked:**
  - Tests cover selection, the BIASED standing, the pooled replay and hash, dry run versus apply,
    idempotence, the 15m refusal, the emit/import round trip, six tamper cases each refusing the whole
    file, and a source that is no longer INVALID.
  - Six mutations each fail a test: the reason, the pooled replay, idempotence, the selection, the spec
    check, and the source check.
