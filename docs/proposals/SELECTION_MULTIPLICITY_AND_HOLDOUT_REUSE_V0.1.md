# Two structural weaknesses in strategy selection: an uncorrected holdout path to LIVE, and a holdout that leaks into breeding

**Status:** DRAFT 2026-09-30 — §5의 D1–D4 결정 대기. ① LIVE 문의 홀드아웃 경로가 약 1,756회 시도에 대해 보정되지 않았다(보정하면 확정 8건 중 0건 통과). ② 퓨전 부모 선정이 홀드아웃을 읽어 자식의 홀드아웃이 오염된다(부모와의 간격이 짧을수록 우위가 커짐, 94%→33%). 구현 없음.

**What this is:** two findings from a read-only review of the selection chain (factory → robustness →
pool admission → LIVE door), with the measurements behind them and the options for each. Nothing is
built.
- ① re-decides part of the 5-1 rule (Thomas 2026-08-11: "holdout CONFIRMED **or**
  FORWARD_CONFIRMED").
- ② changes what the factory breeds from.

Both are Thomas's.

**Measured 2026-09-30**, read-only against the live store (main of 2026-09-30). The population is
1,417 lineages on the current cost basis, collapsed to their latest row.

## 1. The headline the two findings sit under

The chain's filters work: `0 promotable` of 3,472 lineages is the chain refusing, not failing. What
they filter has no measured entry information yet.
- **Holdout.** Of the 1,204 judgeable current-basis holdouts, **70% are CONTRADICTED**. At zero edge,
  CONTRADICTED and UNDERPOWERED would split roughly by sign, about half each. Only **8 (0.7%) are
  CONFIRMED**, all 4h and all from two OI families (`oi_squeeze_long`, `oi_unwind_short`).
- **Mint-anchored null control.** Across the 15 `crypto_null_control` schedules, a selected entry
  beat its own coin-flip twin through the same exits in 193 of 474 measured specs (41%). The weighted
  edge is −0.08R.
- **Forward, by direction** (cohort rows vs the cohort's null twins, which keep direction):

  | | LONG | SHORT |
  |---|---|---|
  | null twins | +0.379R (n=418) | −0.352R (n=803) |
  | seeded_template | +0.277R (n=108) | **−0.449R** (n=286) |
  | crossover | +0.370R (n=63) | −0.066R (n=12) |
  | rescore / import rows | +0.193R (n=97) | −0.377R (n=78) |

  Seeded entries are below the coin flips in **both** directions. Crossover's forward "lead" is its
  long share (84% LONG in an uptrend). Its LONG rows match the null's LONG rows.
  - *Limits:* one market phase; twin ids do not map to members, so each class is compared with all
    twins of its direction.
  - Read it as "no detectable entry information in this sample", not as proof.

Against that backdrop, the two findings below are about the **evidence** the chain accepts, not its
plumbing.

## 2. ① The holdout path to LIVE is not corrected for how many holdouts were looked at

### What the code does

The LIVE door (`promotion._gate_live_confirmation` → `forward_confirmation.assert_live_tier_confirmed`,
`forward_confirmation.py:316`) passes a lineage whose holdout is CONFIRMED, **or** whose forward
record is FORWARD_CONFIRMED.

Holdout CONFIRMED (`robustness.holdout_status`) is trade-level `mean − 1.96·sd/√n > 0` plus the period
test (≥ 8 of 10 periods, t-interval). That is a **single-test** bar.

The store does count attempts. `candidate_ranking.attempts_by_context` feeds `selection_adjusted_z`
(`robustness.py:196`, a Bonferroni bar that grows like √(2 ln N)). But that bar is used only in the
**sort key** (`selection_rank`, `candidate_ranking.py:790`), never in a door. Every attempt in a
context has its holdout looked at, so the holdout is exactly as multiply-tested as the in-sample score.

### The arithmetic, today

In the 4h pooled context, **1,756 attempts** give a corrected bar of **z = 4.19**.

| lineage | holdout n | holdout expectancy | holdout t | clears 4.19 | in-sample `selection_rank` |
|---|---|---|---|---|---|
| cand_c729a65d35711741c047 | 26 | +0.643 | 2.97 | no | 0 (clears corrected) |
| cand_9ac4de0fd54d1661132b | 55 | +0.383 | 2.68 | no | 0 |
| cand_2322396d18b1d036a909 | 98 | +0.382 | 2.80 | no | 0 |
| cand_a0ce97302e77bae1622f | 36 | +0.641 | 3.73 | no | 0 |
| cand_7c385a24568e4a6ebd74 | 34 | +0.644 | 4.06 | no | 0 |
| cand_7328da2c8c7daa5f2b5f | 52 | +0.411 | 2.40 | no | 1 (uncorrected only) |
| cand_4b1a32f15e739d324abe | 29 | +0.719 | 3.50 | no | 1 |
| cand_34a705b552d7ddb45f8a | 27 | +0.645 | 2.94 | no | 1 |

- **0 of 8 clear the corrected holdout bar**, and 5 of 8 clear the corrected **in-sample** bar.
- Clearing 4.19 is not impossible, it needs more evidence. At +0.64R with sd ≈ 1.1 it takes about
  52 holdout trades, roughly twice what these carry.
- `robustness.py:172-177` records the caveat that applies: Bonferroni over correlated attempts is
  conservative. These attempts correlate heavily (rolling windows, sibling families), so 4.19 is an
  upper bound on the fair bar, not the fair bar.
- The forward path has the same shape at a smaller scale. `assert_live_tier_confirmed` already
  stamps `observed_lineages` (15 in the pool today) into its refusal text, for a first confirmation
  to be read against, but the judge does not use it. A Bonferroni on 15 is z ≈ 2.94.

### Options

| | Rule | Today's 8 | Cost | Weakness |
|---|---|---|---|---|
| **A** | Holdout CONFIRMED **and** in-sample `selection_rank == CLEARS_CORRECTED` | 5 pass | Nil | The in-sample t is the number the search optimised, so it is the weaker discriminator. It is also exposed to ② |
| **B** | Holdout t ≥ `selection_adjusted_z(attempts_in_context)` (same function, applied to the holdout) | 0 pass | The holdout path demands about 2× the trades at today's search volume | Conservative under correlation |
| **C** | Holdout path removed from the LIVE door; LIVE needs FORWARD_CONFIRMED. Holdout CONFIRMED stays what it already is at pool admission (it admits OBSERVATION) | 0 pass | The earliest LIVE arming is months out: pool forward floors are 25/10 trades plus ≥ 8 slices of 14 days (about 112 days) | Slower. The forward path has its own multiplicity (see D2) |

**Recommendation: C, with D2's forward correction.**
- Forward rows are the only evidence neither the search nor ② can have touched.
- The pool forward book is where the 5-1 rule's forward half already lives.
- It is a **tightening**, which `RESEARCH_EPOCH_V0.1.md` Q3 does not hold to an epoch boundary (Q3
  restricts loosening).
- It costs nothing today (0 armed). It is still a live-path rule, so it is Thomas's.

If Thomas wants the holdout path kept, B is the principled variant and A the cheap one.

**What happens to the only lead.** The 8 CONFIRMED are all 4h OI lineages. OI is also the one family
set that separates in `FAMILY_EXHAUSTION_V0.1.md` (30-day CONFIRMED 4/26 vs 0/718 for the rest). No
option discards them: they stay eligible for OBSERVATION, and their test moves forward. Seven OI
lineages already sit in the forward cohort (`oi_squeeze_long` 4, `oi_unwind_short` 3), all
FORWARD_INSUFFICIENT so far.

## 3. ② Holdout results select breeding parents, and the children's holdout inherits the selection

### What the code does

Two factory doors read the holdout:
- **Parenting** (`factory.holdout_permits_parenting`, `factory.py:4410`): a row may parent a fused
  child only if its holdout has ≥ 25 closes and expectancy ≥ 0. It fails closed, and
  `rank_fusion_parents` calls it.
- **Centring** (`factory.holdout_permits_centring`, `factory.py:2446`): a row may centre the next
  draws for its family unless its holdout is judgeable and negative. It fails open.

The holdout is the last 30% of a rolling window that moves one day per day (1,000 days at 1h and 4h,
2,000 bars at 1d). A child minted Δ days after its parent is judged on a holdout that overlaps the
parent's by about `1 − Δ/300` at 1h and 4h. **The child is re-measured on the bars its parent was
selected for passing.**

### The measurement

Current-basis crossover children with a judgeable holdout (n=47), bucketed by Δ from their latest
parent's mint:

| Δ | children | holdout > 0 | mean |
|---|---|---|---|
| < 7 days | 34 | **94%** | +0.345R |
| 7–30 days | 7 | 71% | +0.075R |
| 30–90 days | 6 | 33% | −0.012R |

For comparison, seeded templates show 28% positive and a −0.033R mean (n=877).
- **The advantage decays with overlap**, which is the signature of reuse. Heritable quality would be
  flat in Δ.
- The long buckets are small (7 and 6), so read the gradient, not the endpoints.
- It agrees with §1: crossover's forward LONG rows match coin flips of the same direction.

**The two doors differ in the data, so they get different remedies.**
- The signature is on **parenting**.
- **Centring** acts on seeded draws, and seeded holdouts sit *below* a zero-edge split (28%
  positive). There is no visible centring leak.
- 6 of the 8 CONFIRMED in §2 are seeded, so ② does not explain the CONFIRMED set. It explains the
  crossover class's holdout.

### Why not just delete the holdout from both doors

`holdout_permits_centring` exists because of a measured hazard. On 2026-08-05, **49.2% of the 461
centrable contexts were centred on a row the tail had already refuted** (docstring,
`factory.py:2446`). Removing the holdout from centring brings that back, and the data shows no leak
there to pay for it. Removing it from parenting would let refuted rows breed again. Whatever replaces
the holdout at the parenting door has to keep that protection.

### Options for parenting

| | Remedy | Cost |
|---|---|---|
| **A** | **Validation slice.** Carve the last 15% of the scored window as validation (train 55 / validation 15 / holdout 30). Parenting reads validation, never the holdout. The holdout returns to being unseen by the search. | Training shrinks by about 21%: 4h 4,200 → 3,300 bars, 1d 1,400 → 1,100. More `trades_per_parameter` vetoes (FRAGILE). A replay-path change in `factory` and the evidence schema. |
| **B** | **Mark reuse.** A child whose parents were holdout-selected carries `holdout_reused: true` (parents' ids and mint times are already on the record). Any door that reads holdout CONFIRMED treats a reused holdout as UNDERPOWERED-at-best. The funnel counts them apart. | Gate-side only; the search is unchanged. The children keep breeding on contaminated evidence, but nothing downstream mistakes it for unseen. |
| **C** | **Embargo.** A child's holdout counts only for bars after its parents' mint. | A child cannot confirm for about 300 days. Harsh; it retires the fusion path in practice. |

**Recommendation: B now, A at the next research-epoch boundary.**
- B is small, removes the false-unseen claim wherever a door reads it, and needs no search change.
- A is the proper fix, but it changes what gets minted and how much each spec is trained on, so it
  belongs with the other boundary items (`REMAINING_WORK.md` §L).
- **If ① is decided as C, ②'s LIVE exposure is already zero.** What remains is pool admission
  (`pool_admission._observation_holdout_term` admits thin and UNDERPOWERED, refuses CONTRADICTED)
  and the ranking. B covers both.

Centring stays as it is. Re-measure it if a seeded-vs-Δ test ever shows the gradient that crossover
shows.

## 4. Constraints

- **① is a live-path rule change.** It is a tightening, and nothing is armed, but the door's rule is
  Thomas's (5-1, 2026-08-11). Claude does not arm or run the live path.
- **② B changes a door's reading of evidence, not the search.** It is not new hypothesis, trial or
  display machinery. Whether it falls under review D3's bug-fix exemption is for Thomas to classify
  (D4).
- **② A changes the search** and belongs at an epoch boundary.
- **The Δ measurement is read-only** and reproducible from the store: parents via
  `parent_candidate_ids`, Δ from `created_at_utc`, and the holdout from `backtest_evidence.holdout`.

## 5. Decisions for Thomas

- **D1 — the LIVE door's holdout path (§2).** C (recommended): forward required for LIVE. Or B, the
  corrected holdout bar. Or A, which adds in-sample `selection_rank == 0`.
- **D2 — correct the forward path for multiplicity.** The forward bar becomes
  `selection_adjusted_z(observed_lineages)` instead of 1.96 (15 lineages → 2.94). Recommended with
  C; it is the symmetric half.
- **D3 — parenting (§3).** B now (recommended), then A at the epoch boundary. Or A only at the
  boundary, leaving the contaminated holdout readable until then.
- **D4 — classification.** Is ② B a bug fix under review D3 (buildable now), or held for the first
  cohort verdict?
