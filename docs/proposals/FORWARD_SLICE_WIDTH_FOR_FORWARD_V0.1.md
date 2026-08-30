# The forward test gets its own slice width — decided: 14 days (option B)

**Decision: Thomas 2026-08-30.** Implemented as `forward_confirmation.FORWARD_SLICE_WIDTH_DAYS`
with `forward_slice_width_days = min(holdout width, 14d)`; the holdout's own width derivation
(and the 2026-08-21 decision inside it) is untouched.

## The problem

`judge_forward`'s slice step inherited the HOLDOUT's width — replay span x HOLDOUT_FRACTION /
HOLDOUT_PERIODS = 30 days at 15m/1h/4h/1d. That width was derived where bars are plentiful and
width costs nothing. Forward evidence accrues in real calendar: with `MIN_HOLDOUT_PERIODS = 8`
active slices, 8 x 30d demanded ~210 days of span, putting the first possible LIVE arming ~7
months after a lineage's mint — while the per-strategy forward book (#807) had just made the
TRADE floors reachable in weeks.

## What the slice test protects, and what the width does not

Trades are not independent draws (within-slice variance inflation measured at 10-15x); the
block-level t-interval is the honest unit, and requiring 8 distinct periods is a real
regime-diversity guard. But the WIDTH of a block only has to be wide enough that adjacent
block means are not positively correlated — that is the whole statistical requirement.

## Measurement (2026-08-30)

Extension of the 2026-08-06 factory table (which stopped at ~17d) down to 7d. Method
identical: lag-1 autocorrelation of the block-mean gross-R series minus the -1/(K-1)
small-sample iid bias. 12 BTC-scoped specs per timeframe, replayed with the production doors:
4h — 1,000 days, 5,629 trades; 1h — 250 days, 3,872 trades.

| width | 4h excess | 1h excess |
|-------|-----------|-----------|
| 7d    | +0.036    | +0.191    |
| 10d   | -0.039    | +0.188    |
| 14d   | **-0.047**| **-0.131**|
| 17d   | +0.157    | +0.048    |
| 30d   | -0.114    | +0.346 (K=9) |

Honest reading: SE ~ 1/sqrt(K) = 0.13-0.26, so no cell is individually significant — this is
"nothing measurable argues against 14d", not a proof of independence. 14d is the only width
non-positive in BOTH windows; 7d is positive at 1h and is not adoptable on today's data.

## Options put to Thomas

| | rule | span needed | first arming* | trade-off |
|---|------|-------------|---------------|-----------|
| A | keep 30d x 8 | ~211d | 2027-02-27 | most conservative |
| **B (chosen)** | **forward width 14d x 8** | ~99d | **2026-11-07** | most defensible narrow width; keeps 8 periods |
| C | 30d x 5 | ~121d | 2026-11-29 | keeps measured 30d blocks; only 5 periods (t-crit self-tightens to 2.78) |
| D | 7d x 8 | ~50d | 2026-09-15 | rejected — 1h measured +0.19 |

*for the leading lineage (S004-GEN-706, 9/25 forward trades at decision time), assuming the
interval tests pass.

## What does not change

The trade floors (25; 10 at 1d), the trade-level z-interval, the block-level t-interval,
`MIN_HOLDOUT_PERIODS = 8`, the operator arming ask, risk limits, canaries, and the lifecycle
ladder. `assert_live_tier_confirmed`'s `observed_lineages` remains informational — with one
forward clock per lineage (#807), the multiple-testing burden on a FIRST confirmation stays
with the operator reading the attempt count, and this decision deliberately does not hide
that behind a correction nobody tuned.

## Reopens when

The block-autocorrelation table is re-measured at the factory's full scale (the 2026-08-06
methodology, 263k trades) and disagrees at 14d; or a first arming's evidence visibly reads as
one hot regime spread across eight adjacent thin slices.
