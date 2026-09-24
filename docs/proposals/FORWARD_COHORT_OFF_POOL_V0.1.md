# Forward evidence off the pool: a frozen cohort — decided: Phase 1, option A

**Decision: Thomas 2026-09-23.** Build Phase 1 (Decision 1: yes), and cohort evidence is a
screen only (Decision 2: option A). No family cap in Phase 1, as suggested below. Implemented in
#948 (`crypto/forward_cohort.py`, `scripts/forward_cohort.py`); the scheduled fire and the board
section follow. Phase 1 changes no door; option A keeps the 2026-08-30 multiple-testing stance
(`FORWARD_SLICE_WIDTH_FOR_FORWARD_V0.1.md`) intact, because no cohort evidence reaches the
LIVE door.

## The problem

`forward_book` (#807, Thomas 2026-08-29) made forward evidence per-lineage instead of
per-context. It did not make it independent of the pool: `run_forward_book_update` advances
only OCCUPYING pool entries, and the scheduled cycle that calls it fans out over the contexts
the pool trades plus any open position (`scheduler.py`, `crypto_pipeline`). The number of
forward clocks is therefore the number of occupied slots.

Measured 2026-09-23 on this host:

| | value |
|---|---|
| occupying pool entries (`OCCUPYING_STATUSES`) | 15 (14 PAPER_ACTIVE, 1 WARNING) over 13 contexts |
| lineages with forward rows, ever | 18 |
| forward rows | 141 |
| lineages at the trade floor (25; 10 at 1d) | **0** — best is 21 |
| store rows that clear the OBSERVATION entry bar + cost basis + depth | 285 |
| ... collapsed to one per (family, symbol scope, timeframe) | **120** over 15 contexts (4h 51, 1d 48, 1h 21; 62 families) |

So 120 lineages clear the bar a slot would ask of them, and 15 get a clock. Two cohort
contexts (BNBUSDT 1h and 4h) are outside the pool's 13, and 4 members sit wholly in them.

**What a cohort buys, and what it does not.** It widens the sample; it does not speed any one
clock. Estimated from each lineage's own backtest trade rate x days since its selecting row
(not counted from forward rows):

| tf | lineages | median days to the trade floor (est.) | median trades since its selecting row (est.) | estimated at floor now |
|----|----|----|----|----|
| 4h | 51 | 128 | 6.1 | 4 |
| 1h | 21 | 115 | 5.6 | 0 |
| 1d | 48 | 179 | 2.3 | 1 |

The trade floor binds before the 8 x 14d slice rule (~112 days) at the median. A cohort frozen
today yields its first judgeable lineages in roughly four months, not sooner, but it yields ten
times as many as the pool can.

## A defect found on the way — fixed separately (#945)

`scripts/seed_forward_book.py` seeds a lineage from the **earliest** `created_at_utc` across all
store rows sharing its rule hash (`_first_seen_by_hash`). Its rationale — "a re-score
re-measures an unchanged spec, so the original mint is the honest start of its out-of-sample
span" — holds for **spec freezing** and fails for **selection freezing**. A `mvp_rescore` row's
holdout is the most recent 30% of a snapshot taken at re-score time, so for a lineage promoted
on its re-score row, every bar between the original mint and the re-score was inside the
holdout that admitted it. Seeding from the original mint counts those bars again as forward
evidence.

Measured on the live book: **31 of 141 forward rows (22%) opened before the candidate row that
selected their lineage.** All 31 belong to lineages promoted on 2026-08-24 re-score rows. The
two lineages with the best forward records carry the most of them: `cand_c80d741fa05422d8be68`
(8 of 17 rows, mean +0.524R) and `cand_0ab780971198521358ff` (6 of 13, mean +0.599R). Neither
has reached the trade floor, so no arming has read this yet.

**Fixed in #945, not here:** the seed start is the `created_at_utc` of the row the pool entry
names by `candidate_id`; and because the rows already written stay (the store is append-only
and sealed), the judge's input (`forward_outcomes_for`) keeps only rows opened at or after the
record's own `created_at_utc` (`forward_confirmation.selection_cutoff`). Against the live store
that is 141 rows → 110, the 31 above. Phase 1 below reuses that same cutoff.

## Phase 1 — the cohort as observation (no door)

**1. A frozen cohort record.** `forward_cohort.v1`: `cohort_id`, `frozen_at_utc`, the
eligibility rule version, and the members — `candidate_id`, `strategy_rule_hash`, the
selecting row's `created_at_utc`, the context — plus `cohort_size` in total and per context,
sealed with `record_sha256`. Membership never changes after freeze. A lineage that becomes
eligible later waits for the next cohort; that is what keeps K a number fixed before any
outcome is seen, rather than one counted at judgement time.

**2. Mechanical eligibility, reusing the door's own functions.** `PROMOTABLE_DERIVATION_TYPES`,
the promotable cost-basis and depth ranks, `assert_observation_entry_bar`; one member per
`promotion_backlog._lineage_key` in `rank_candidates` order; lineages already occupying the pool
excluded (they have their own clock). Nothing is chosen by hand, and nothing reads post-selection
outcomes.

**3. The clock starts at the selecting row** — `selection_cutoff`, the boundary #945 gave the
judge — for every member, re-score or not.

**4. Its own store, its own provenance.** `forward_cohort_outcomes.jsonl` and
`forward_cohort_positions.json`, provenance `mvp_forward_cohort`. `forward_outcomes.jsonl` is
read by `assert_live_tier_confirmed` through `promotion.py`, and its reader's contract is
"exactly one legitimate writer" because it feeds the door that arms real money. A cohort
writer there would be a second writer into arming evidence. Nothing is ever copied across. If
a member is later promoted, how the money store is seeded follows Decision 2: under A the
cohort period was the selection data, so the pool clock starts at promotion and the seeder must
not walk back into it; under B the seeder re-walks from the selecting row, as it does since #945.

**5. An independent walker, not a cycle step.** The cycle visits the pool's contexts, and
`run_forward_book_update` advances pool entries only; the cohort's members are neither, and two
of its contexts are not in the fan-out at all. A daily scheduled job, per context: one snapshot fetch
(`collect_market_data` + `attach_mining_legs` + `build_replay_frame`, the seeder's path), then
`replay_entry_bar` for each member from its persisted `last_seen_candle`. **Not**
`walk_seed_span` on each run: it drops a position still open at the boundary, so a daily walk
would discard every trade held across a run. The first run seeds from the clock start with the
same per-bar transition. The cycle and the money path are untouched. Cost: 15 fetches and 120
spec replays per day.

**6. Two parity traps the walker must close.**

- *Admission evidence.* A candidate row does not carry `regime_evidence` or
  `distribution_reference`; both doors fail OPEN without them. The synthesized entry is built
  with `strategy_artifact.admission_evidence`, the function the promotion door uses — the
  2026-09-02 lesson (S004-GEN-690 probed at 36 opens, installed at 11).
- *Position identity.* `position_id` hashes `(strategy_id, entry price, opened_at)`, and
  candidate rows share template ids (`S007` names many lineages). Two sibling members firing on
  the same bar at the same price would mint one `settlement_id`, and the append-side dedup would
  silently drop the second lineage's row. The walker keys identity on `candidate_id`.

**7. A null arm, optional but cheap.** *Built 2026-09-24 (Thomas: choice B), as a companion
record rather than inside the cohort: `crypto/forward_cohort_null.py`, one coin-flip twin per
member of each frozen cohort, on its parent's clock, walked after the members into stores of its
own. The judge's rate over the twins, beside the members', per timeframe, is on the daily board
(`null 대조`) and in `scripts/strategy_funnel` (NULL ARM), from 2026-09-24.* `null_control` already builds mint-anchored random-entry
controls through the same exits. Freezing N of them into the cohort measures the forward
judge's false-confirmation rate directly — the number the 2026-08-30 decision said nobody had
tuned a correction against.

**8. Output: display only.** Per member, the current `judge_forward` numbers (n, mean net R,
active slices, status) and the per-context K, on the daily board beside `promotable_backlog`.
It reads and reports; it refuses and decides nothing.

## What does not change

`forward_book`, its live stream and its store; `judge_forward` (its input is #945's, this
proposal does not touch it); the trade floors (25; 10 at
1d), the trade-level z interval, the block-level t interval, `MIN_HOLDOUT_PERIODS = 8`, the 14d
forward slice width; the OBSERVATION entry bar, family and size caps; the pool; the arming ask;
the execution stage.

## Decision 1 — build Phase 1? **Decided: yes (Thomas 2026-09-23).**

Recommended: yes. It is observation-only, and every option below needs it.

**Open within it:** does `OBSERVATION_FAMILY_CAP = 2` apply to cohort membership? Its stated
reason is multiple-testing debt from correlated siblings, not slot scarcity, so it is a K
question. The collapse to one member per (family, scope, timeframe) already removes re-mints;
the cap would further hold a family to two contexts. Suggested: no cap in Phase 1 (display
only), revisit under Phase 2.

## Decision 2 — what may cohort evidence do? (Phase 2) **Decided: A (Thomas 2026-09-23).**

Today one lineage's forward confirmation is judged alone, with `observed_lineages`
informational — Thomas 2026-08-30, deliberately, with 18 clocks. At 120 it is a different
question. Critical values at the current one-sided 2.5% level, 8 slices (df 7):

| | rule | trade-level z | slice t (df 7) | block Sharpe needed | trade-off |
|---|---|---|---|---|---|
| **A** | **screen only** — cohort evidence never reaches the LIVE door; it orders promotion into the pool, and the pool clock starts fresh at promotion (the seeder needs a promotion-time start for these lineages) | 1.96 | 2.36 | 0.84 | no new correction (selection and confirmation on disjoint data); costs a second ~4-month clock after promotion |
| B | direct door, Bonferroni **per context**, K stamped at freeze (~8 per context) | 2.73 | 3.86 | 1.36 | mirrors `robustness.SELECTION_CONTEXT`; family-wise error across the 15 contexts is not controlled |
| C | direct door, Bonferroni over the whole cohort (K = 120) | 3.53 | 6.27 | 2.22 | effectively nothing passes — gate tightening by another route |
| D | direct door, uncorrected | 1.96 | 2.36 | 0.84 | at the trade-level test alone, a pure-noise cohort of 120 clears ~3 by chance; the slice test lowers that by an unmeasured amount |

`judge_forward` is read at ask time, so under B or D the door is re-asked whenever an operator
looks — optional stopping, which no fixed-sample interval survives. The cheap guard is a fixed
cohort close (`frozen_at + N days`, judged once); under A it costs nothing, because the cohort
opens no door.

Recommended: **A now**, and revisit B once a cohort has closed with a null arm (item 7), so the
correction is tuned against a measured false-confirmation rate rather than assumed.

## Reopens when

The null arm measures a false-confirmation rate that makes A's second clock unnecessary (B) or
D's risk acceptable; or the eligible population changes by an order of magnitude; or the pool
itself stops being the unit that arming reads.
