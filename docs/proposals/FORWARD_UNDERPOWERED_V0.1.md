# The forward judge gets the holdout's UNDERPOWERED split — decided: option A

**Status:** IMPLEMENTED 2026-09-24 — option A (the sign split), built in the same PR as this record (#964).

**Decision: Thomas 2026-09-24 — option A (the sign split), implemented in the same PR as this
record.** It changes a label and the displays that read it; no door that arms money changes.

## The problem

`forward_confirmation.judge_forward` reads a record at its trade floor as `FORWARD_CONTRADICTED`
whenever the trade-level interval fails to clear zero:

```python
if mean - CONFIDENCE_Z * spread / math.sqrt(len(nets)) <= 0:
    return verdict(FORWARD_CONTRADICTED, mean_net_r=round(mean, 6))
```

That is one label for two opposite facts — the record measured *against* the edge, or it leaned
*with* the edge and the sample could not resolve the lean from zero. It is the defect #783 fixed for
the holdout on 2026-08-29 (`robustness.HOLDOUT_UNDERPOWERED`, where 140 of 442 CONTRADICTED blocks
had a positive expectancy), left standing in the forward judge that mirrors it.

The first scheduled cohort walk (2026-09-24 07:15Z) shows it. `cand_6ca57482afb44b0c9f24`
(`trend_pullback+volatility_expansion_long`, 1d) reached its 10-trade floor at **+0.198R** mean and
reads CONTRADICTED. Its own interval is **[−0.62, +1.01]R** (sd 1.31): it says nothing about the
sign. The board reads it as 반박 and drops it from the leaders an operator may promote from
(option A), beside four 4h shorts that really did measure negative.

## Measurement (2026-09-24, live store, read-only)

| population | judged | CONTRADICTED | of which mean > 0 |
|---|---|---|---|
| forward cohort members | 115 | 5 | 1 (1d, n=10, +0.198R) |
| cohort null arm (coin-flip twins) | 115 | 7 | 1 (4h, n=34, +0.255R) |
| pool forward book | 18 | 0 | 0 |

The five member intervals, from the judge's own spread: 4h `macd_cross_down` −0.55R n=39
[−0.93, −0.17]; 4h `taker_flow_short` −0.44R n=31 [−0.80, −0.09]; 4h `breakdown_short` −0.23R
n=40 [−0.61, +0.14]; 4h `funding_momentum_short` −0.22R n=27 [−0.68, +0.24]; 1d
`trend_pullback+volatility_expansion_long` +0.20R n=10 [−0.62, +1.01].

Small today because few records have reached their floor — 4 of 115 members before this walk. The
share grows as the cohort matures: the holdout's version of the same test was under-powered about
6x against a realistic edge (#783's arithmetic: a 0.10R edge at spread 1.2 needs 553 trades to
clear; the forward floors are 25, and 10 at 1d).

## Design

1. **`FORWARD_UNDERPOWERED = "FORWARD_UNDERPOWERED"`** in `forward_confirmation`. In `judge_forward`,
   the failed trade-level interval splits on the sign of the mean, exactly as `holdout_status` does:
   `mean > 0` → UNDERPOWERED, else CONTRADICTED. The sign of a number the verdict already carries —
   no new threshold. Like the holdout, the split returns before the slice test (a record that fails
   the trade-level interval cannot pass the wider slice interval).
2. **The LIVE door is untouched.** `assert_live_tier_confirmed` passes only `FORWARD_CONFIRMED` (or a
   CONFIRMED holdout) and refuses everything else by name, so UNDERPOWERED is refused exactly as
   CONTRADICTED is; its refusal text names the new status. A test pins that UNDERPOWERED cannot arm.
3. **The cohort displays.** `forward_cohort.maturity_of` maps UNDERPOWERED to `MATURE` (at the floor,
   not confirmed — what the maturity already means), so the board's 반박 count means "measured
   against" again, and an UNDERPOWERED member can be a leader, ranked by its lower bound like any
   other. The leader line keeps showing the lower bound, so a lineage like the one above ranks low
   on its own numbers, not by label.
4. **The null arm.** `forward_cohort_null.arm_counts` counts CONTRADICTED through `maturity_of`, so it
   follows automatically for members and twins alike; the comparison stays like-for-like. Adding an
   `underpowered` column there is optional and suggested, so the arm comparison shows where the
   moved records went.

Nothing stores the forward status: the judge is recomputed on every read (the gate, the board, the
report), so there is no migration and no vintage of old labels.

## Options

| | rule | CONTRADICTED means | today's effect |
|---|---|---|---|
| **A (recommended)** | sign split, as the holdout: `mean ≤ 0` CONTRADICTED, `mean > 0` UNDERPOWERED | at the floor and leaning against | 1 member and 1 twin move |
| B | symmetric interval: CONTRADICTED only when the UPPER bound is below zero; everything straddling zero UNDERPOWERED, either sign | significantly negative | 3 of 5 members move: the +0.20R one and two of the four shorts (`breakdown_short`, `funding_momentum_short`, whose intervals reach above zero) |
| C | leave it | "not significant" | the label keeps reading "disproven" |

A is recommended because it is the rule already decided for the same test on the holdout, so the
two judges keep one meaning per word. B is the statistically cleaner "contradicted", but it would
make the word rare, and it would diverge from the holdout's rule without a decision to change both.

## What does not change

`FORWARD_CONFIRMED` and everything it takes (the trade floors, the trade-level z interval, the
slice test, `MIN_HOLDOUT_PERIODS`, the 14-day width, #945's selection cutoff); the LIVE door; the
OBSERVATION entry bar (it does not read forward status); the cohort's membership, walker and stores; the
null arm's records. Nothing retires, demotes or refuses a lineage on either label today, and this
adds no such consequence.

## Decision — **A (Thomas 2026-09-24)**

A, B or C. Under A or B the build is one PR: the constant and the split, `maturity_of`, the
optional null-arm column, tests (a positive-mean floor record reads UNDERPOWERED and cannot arm; a
non-positive one still reads CONTRADICTED), a `BUILD_HISTORY` entry, and a deploy.

## Reopens when

Anything starts acting on CONTRADICTED (a retirement, a demotion, an automatic cohort exit): then
the choice between A and B decides what gets removed, and should be made again with that in view.
