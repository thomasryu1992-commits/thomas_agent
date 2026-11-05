# 2026-11-05 — Regime strategy set v0.2: timing and selection judged apart, four hypotheses on unread spot

**What.** Added `scripts/research/regime_strategies_v02.py` for `docs/proposals/CRYPTO_REGIME_STRATEGY_SET_V0.2.md`
(Thomas: planning Q1–Q5 and §7 Q1–Q3 "기본값대로"). It has two sandbox-only commands:
- `stage0` re-measures the record's §2 diagnosis on the seen futures window.
- `confirm-spot` judges T1 (btc_only timing), S1 (B08), S2 (D10) and S3 (S01R) once on Binance spot 2018-03-05 →
  2019-12-29.

**Why a separate module.** v0.1's stage-1 and stage-2 records name their module's `code_digest`. Editing
`regime_strategies.py` would orphan them. v0.2 imports it as a library and digests all three files it runs on.

**Why S01R is its own strategy and not −S01.** Two reasons:
- The common stop and the exit are asymmetric, so flipping S01's sign is not S01's mirror. S01's middle-band exit
  would close a long bought above the upper band the next day.
- Measured directly, S01R's screening excess is +268 bps (t 2.89), not the +372 (t 4.11) the sign flip suggests.

**Why the trade floor is selection-only.** The timing layer's "trades" are regime changes. Its judging floor is the
BULL and BEAR week counts.

**Tests.** 16 tests. They catch 12 of 14 mutants; the other two compute the same labels as the original.
