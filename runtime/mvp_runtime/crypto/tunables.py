"""The numbers that decide money, and where each one came from — G1's first slice.

``REMAINING_WORK.md`` §G1: **656 upper-case constants across the crypto package and no module
owns them** (602 when it was written four days earlier — the population grows). CLAUDE.md
enforces *"One concept = One authority = One source of truth"* hard for contracts, schemas and
registries, and **not at all for the numbers that decide money.**

**This index does not move a single value, and that is the design.** §G1 says so directly —
*"Do not do this as one refactor. It is the live money path, and a sweep that moves 602 values is
a sweep nobody can review"* — and there is a second reason it would be wrong: in this package the
rationale lives in a dense comment beside the constant, and that comment is the good part. Moving
values into a central module would separate every number from its own argument. So each entry
here **imports** the constant from its owner, which means the recorded value cannot drift from the
real one, and carries the *one thing the comment beside it does not say*: where the number came
from, and what would reopen it.

**Provenance is the axis, because the defect §G1 names is a stale premise rather than a wrong
number.** `MAX_DAYS_TO_LIFECYCLE_WINDOW = 14` was not incorrect when written; its premise (15m is
the workhorse) was killed by a cost re-measurement somewhere else, and the board read *0
promotable against 900 candidates* until someone went looking. A number's value is checkable by a
test; whether the reason for it still holds is not, and the only defence is that the reason is
written where a reader will meet it.

Reading the index the first time already answers a question nobody had asked: **the four numbers
that stop the money are the ones nobody here has ever decided.** `DAILY_MAX_LOSS_R`,
`WEEKLY_MAX_LOSS_R`, `MAX_CONSECUTIVE_LOSSES` and `MAX_DRAWDOWN_PCT` are `INHERITED` — carried
from the predecessor system's `config/settings.py` — while the cost model, the ladder and the
promotion door have each been re-measured against this runtime's own record more than once.

Nothing imports this module to make a decision, and nothing should: it is an index, not an
authority over the values. What gives it teeth is
``tests/test_mvp_runtime_crypto_tunables.py``, which fails when a **new** decision-shaped
constant appears in a swept module without an entry here — so the population cannot keep growing
unowned the way it has been.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import (
    account,
    candle_archive,
    cost,
    dashboard,
    digest,
    factory,
    features,
    feedback,
    guards,
    live_allowance,
    live_order,
    live_position,
    live_budget,
    live_promotion,
    live_sizing,
    market_data,
    null_control,
    paper,
    pool,
    probe,
    proposer,
    robustness,
    strategy,
)

# --- provenance: how this number came to be what it is ----------------------------------------
#
# Ordered weakest to strongest as *evidence about this runtime*. The distinction that matters is
# the first one: a value can be perfectly reasonable and still never have been examined here.
INHERITED = "inherited_from_source"        # came from the predecessor system; never re-decided here
OPERATOR = "operator_decision"             # Thomas decided it, and the record says when
MEASURED = "measured_here"                 # derived from this runtime's own store or account
DERIVED = "derived_from_another"           # a function of another constant; moves when that moves
VENUE = "venue_published"                  # the venue's own published or measured rate

PROVENANCE = frozenset({INHERITED, OPERATOR, MEASURED, DERIVED, VENUE})


@dataclass(frozen=True)
class Tunable:
    """One decision-bearing constant, read from its owner rather than copied.

    ``value`` is the live object, so this index can be wrong about a *premise* but never about a
    *number*. ``premise`` is one line; the full argument stays in the comment at ``owner`` and is
    deliberately not duplicated here — two spellings of one rationale is how the two drift, which
    is the failure this package warns about everywhere else.
    """

    name: str
    value: Any
    owner: str
    provenance: str
    premise: str
    reopens_when: str


TUNABLES: tuple[Tunable, ...] = (
    # --- cost premise: every net R downstream is denominated in these ------------------------
    Tunable("DEFAULT_TAKER_FEE_BPS", cost.DEFAULT_TAKER_FEE_BPS, "crypto/cost.py", VENUE,
            "measured on this account 2026-07-26; the source's 2.5 was half the real rate",
            "a VIP tier or BNB discount — both move it down, the safe direction"),
    Tunable("DEFAULT_MAKER_FEE_BPS", cost.DEFAULT_MAKER_FEE_BPS, "crypto/cost.py", VENUE,
            "Binance's published standard maker rate; NOT measured — no maker fill has happened",
            "the first live maker fill, which should replace it with a measurement"),
    Tunable("DEFAULT_SLIPPAGE_BPS", cost.DEFAULT_SLIPPAGE_BPS, "crypto/cost.py", INHERITED,
            "carried from the source system unmeasured; a canary is one order, not a sample",
            "enough live fills to measure realized slippage against intended price"),
    Tunable("DEFAULT_STOP_SLIPPAGE_BPS", cost.DEFAULT_STOP_SLIPPAGE_BPS, "crypto/cost.py", DERIVED,
            "the stop leg split out at the general rate; first two live stops read 23.5 and 0.0 "
            "bps against it (2026-08-06, §C) — a seam, deliberately not yet a re-decision",
            "the sample `live_pnl.stop_slippage_observations` accumulates reaching a size worth "
            "averaging; raising it is the fail-closed direction"),
    Tunable("DEFAULT_FUNDING_BPS_PER_INTERVAL", cost.DEFAULT_FUNDING_BPS_PER_INTERVAL,
            "crypto/cost.py", VENUE,
            "Binance USD-M's base rate; a FALLBACK only — real history is charged when present",
            "a venue whose base rate differs, which `cost_model_for` now refuses rather than guesses"),
    Tunable("MAX_ENTRY_COST_R", cost.MAX_ENTRY_COST_R, "crypto/cost.py", MEASURED,
            "friction at a quarter of an R needs a top-quartile edge merely to break even (n=102)",
            "the store's positive-expectancy distribution moving; re-measure before touching"),

    # --- the four numbers that stop the money, and the two nobody has re-decided --------------
    Tunable("DAILY_MAX_LOSS_R", guards.DAILY_MAX_LOSS_R, "crypto/guards.py", INHERITED,
            "source `config/settings.py`; the unconfigured runtime judges on exactly this",
            "this runtime's own realized loss distribution — which now exists and has not been used"),
    Tunable("WEEKLY_MAX_LOSS_R", guards.WEEKLY_MAX_LOSS_R, "crypto/guards.py", INHERITED,
            "source `config/settings.py`; tripped at a recorded -5.53R whose costs were ~7.8R",
            "the same; the cost work of 2026-07-30 changed what an R in this breaker means"),
    Tunable("MAX_CONSECUTIVE_LOSSES", guards.MAX_CONSECUTIVE_LOSSES, "crypto/guards.py", INHERITED,
            "source `config/settings.py`; three in a row halts",
            "a measured run-length distribution; three is a plausible number nobody checked here"),
    # #615 §5 — the per-lineage allowance. DERIVED because both are the pool-wide breaker's own
    # numbers narrowed to one lineage's share; when those move, these should be reconsidered
    # together. The proposal states their weakness rather than hiding it (§8-A): the live sample
    # is 3 outcomes, so there is nothing to calibrate against and will not be for months.
    #
    # An allowance does not need a calibrated threshold the way a verdict does — it encodes how
    # much we were willing to risk before pausing to look. What would falsify the choice is the
    # opposite failure: lineages leaving the live tier before they can produce any evidence at
    # all. If that happens, the honest reading is "there is nothing worth proving live right
    # now", not "the numbers are wrong".
    Tunable("MAX_CONSECUTIVE_LIVE_LOSSES", live_allowance.MAX_CONSECUTIVE_LIVE_LOSSES,
            "crypto/live_allowance.py", DERIVED,
            "`guards.MAX_CONSECUTIVE_LOSSES` (3) narrowed to one lineage; deliberately tighter",
            "a per-lineage run-length distribution, which needs live outcomes this pool has not "
            "produced — 3 live settlements exist in total as of 2026-08-09"),
    Tunable("MAX_CUMULATIVE_LIVE_LOSS_R", live_allowance.MAX_CUMULATIVE_LIVE_LOSS_R,
            "crypto/live_allowance.py", DERIVED,
            "`guards.WEEKLY_MAX_LOSS_R` (-5.0) narrowed to one lineage's share of the portfolio",
            "the same measurement, and the -1.108R live stop-out of 2026-08-04 first: if realized "
            "R runs past planned R systematically, every R-denominated limit here is optimistic"),
    Tunable("MAX_DRAWDOWN_PCT", guards.MAX_DRAWDOWN_PCT, "crypto/guards.py", INHERITED,
            "source `config/settings.py`",
            "the same"),
    Tunable("RISK_PER_TRADE", guards.RISK_PER_TRADE, "crypto/guards.py", INHERITED,
            "source `config/settings.py`; the paper-side fraction",
            "`live_sizing.RISK_PER_TRADE_FRACTION` is the live analogue and agrees at 0.01"),

    # --- how far an operator may WIDEN a breaker. One-sided: tightening is unbounded ----------
    Tunable("MAX_RISK_PER_TRADE", guards.MAX_RISK_PER_TRADE, "crypto/guards.py", DERIVED,
            "2x the default, matched to `factory.MAX_RISK_PER_TRADE_R`",
            "either of the two it is matched to moving"),
    Tunable("MIN_DAILY_MAX_LOSS_R", guards.MIN_DAILY_MAX_LOSS_R, "crypto/guards.py", DERIVED,
            "3x the default", "the default moving"),
    Tunable("MIN_WEEKLY_MAX_LOSS_R", guards.MIN_WEEKLY_MAX_LOSS_R, "crypto/guards.py", DERIVED,
            "3x the default", "the default moving"),
    Tunable("MAX_MAX_CONSECUTIVE_LOSSES", guards.MAX_MAX_CONSECUTIVE_LOSSES, "crypto/guards.py",
            OPERATOR, "the loosest streak a registered config may set", "a Thomas approval"),
    Tunable("MIN_MAX_DRAWDOWN_PCT", guards.MIN_MAX_DRAWDOWN_PCT, "crypto/guards.py", OPERATOR,
            "the deepest drawdown a registered config may allow", "a Thomas approval"),

    # --- live money caps: the smallest numbers in the package and the most load-bearing -------
    Tunable("DEFAULT_ABSOLUTE_MAX_NOTIONAL_USDT", live_order.DEFAULT_ABSOLUTE_MAX_NOTIONAL_USDT,
            "crypto/live_order.py", INHERITED,
            "source value; the ceiling a configured cap can never exceed, whatever is typed",
            "a Thomas decision to raise the live boundary at all"),
    Tunable("MAX_LIVE_CONCURRENT_POSITIONS", live_position.MAX_LIVE_CONCURRENT_POSITIONS,
            "crypto/live_position.py", DERIVED,
            "what fits inside the approved boundary (60 USDT/order, 120 open)",
            "the approved boundary moving — this is a consequence of it, not a separate choice"),
    Tunable("MAX_LIVE_POSITIONS_PER_SYMBOL", live_position.MAX_LIVE_POSITIONS_PER_SYMBOL,
            "crypto/live_position.py", VENUE,
            "the venue nets per symbol in one-way mode, so a second book cannot exist",
            "hedge mode, which would make this a choice rather than a fact"),
    Tunable("RISK_PER_TRADE_FRACTION", live_sizing.RISK_PER_TRADE_FRACTION,
            "crypto/live_sizing.py", INHERITED,
            "fraction of usable equity per trade; can only ever make an order smaller than the caps",
            "the same evidence that would move `guards.RISK_PER_TRADE`"),
    Tunable("MAX_CONSECUTIVE_BRACKET_FAILURES", live_order.MAX_CONSECUTIVE_BRACKET_FAILURES,
            "crypto/live_order.py", MEASURED,
            "the incident's own number: two refusals in a row is the path broken, not a venue moment",
            "a bracket failure mode that is not the confirm race #460 fixed"),
    Tunable("DEFAULT_MIN_CLEAN_CANARY_ORDERS", live_promotion.DEFAULT_MIN_CLEAN_CANARY_ORDERS,
            "crypto/live_promotion.py", INHERITED,
            "the source's default: three clean canaries before an autonomous live entry",
            "a canary failure this runtime has not seen"),

    # --- the promotion door and the ladder ---------------------------------------------------
    Tunable("MAX_ROUTABLE_STRATEGIES", pool.MAX_ROUTABLE_STRATEGIES, "crypto/pool.py", OPERATOR,
            "pool sizing cap; the router picks one strategy per context so a surplus dilutes",
            "the attribution-rate argument in `pool.py` changing shape"),
    Tunable("MAX_ROUTABLE_PER_CONTEXT", pool.MAX_ROUTABLE_PER_CONTEXT, "crypto/pool.py", OPERATOR,
            "one per context, which is what makes promotion split trades rather than add them",
            "a router that could run more than one strategy per context"),
    Tunable("PROMOTION_BACKLOG_ALERT_THRESHOLD", pool.PROMOTION_BACKLOG_ALERT_THRESHOLD,
            "crypto/pool.py", OPERATOR,
            "how many promotable lineages may wait before the board says so; speaks, refuses nothing",
            "the board being ignored, or the door stopping being manual"),
    Tunable("OBSERVATION_MIN_BACKTEST_CLOSED", pool.OBSERVATION_MIN_BACKTEST_CLOSED,
            "crypto/pool.py", MEASURED,
            "where the store's own expectancy-vs-sample table stops being inflated "
            "(<20 closes +0.114R, 50-199 -0.003R); below it a positive pick is the lottery",
            "the inflation table re-measured on a store with a different mint geometry"),
    Tunable("OBSERVATION_FAMILY_CAP", pool.OBSERVATION_FAMILY_CAP,
            "crypto/pool.py", OPERATOR,
            "Thomas 5-3 (2026-08-11): a third sibling's forward record is correlated with the "
            "first two, not independent — F1 has no correlation control to price it",
            "correlation control landing (F1), which would price diversity instead of capping it"),
    Tunable("LIFECYCLE_MIN_WINDOW_TRADES", pool.LIFECYCLE_MIN_WINDOW_TRADES, "crypto/pool.py",
            DERIVED, "`lifecycle.DEFAULT_WINDOWS[0]`, restated; a test pins the two equal",
            "the ladder's smallest window moving"),
    Tunable("MAX_DAYS_TO_LIFECYCLE_WINDOW", pool.MAX_DAYS_TO_LIFECYCLE_WINDOW, "crypto/pool.py",
            OPERATOR,
            "the slowest horizon the operator actually promoted at (five lineages, 2026-07-31)",
            "another promotion round at a different horizon — decide again, do not re-derive"),
    Tunable("LIVE_CANDIDATE_MIN_SAMPLE", feedback.LIVE_CANDIDATE_MIN_SAMPLE, "crypto/feedback.py",
            DERIVED, "the ladder's own lowest rung, borrowed as Gate 0's floor",
            "`lifecycle`'s smallest window moving"),

    # --- what the scorer will call believable ------------------------------------------------
    Tunable("MIN_HOLDOUT_TRADES", robustness.MIN_HOLDOUT_TRADES, "crypto/robustness.py", MEASURED,
            "the old bar (3 trades, total_R > 0) was a coin flip roughly half the time",
            "a re-measurement of what sample size separates signal from a coin here"),
    Tunable("CONFIDENCE_Z", robustness.CONFIDENCE_Z, "crypto/robustness.py", INHERITED,
            "the conventional 95% interval; the same one `dashboard.sample_verdict` draws",
            "nothing routine — it is a convention, and changing it changes every verdict at once"),
    Tunable("SELECTION_ALPHA", robustness.SELECTION_ALPHA, "crypto/robustness.py", INHERITED,
            "one in twenty, the alpha the selection correction is built on",
            "the same; the correction's shape is what was measured, not this number"),
    Tunable("ROBUST_SCORE_THRESHOLD", robustness.ROBUST_SCORE_THRESHOLD, "crypto/robustness.py",
            INHERITED, "source threshold for the top tier",
            "this runtime's own score distribution, which nothing has been fitted against"),
    Tunable("FRAGILE_SCORE_THRESHOLD", robustness.FRAGILE_SCORE_THRESHOLD, "crypto/robustness.py",
            INHERITED, "source threshold for the bottom tier", "the same"),
    Tunable("HEALTHY_TRADES_PER_PARAMETER", robustness.HEALTHY_TRADES_PER_PARAMETER,
            "crypto/robustness.py", INHERITED,
            "~10 observations per fitted parameter to mean anything", "a measured overfit curve"),
    Tunable("CRITICAL_TRADES_PER_PARAMETER", robustness.CRITICAL_TRADES_PER_PARAMETER,
            "crypto/robustness.py", INHERITED, "under ~5 it is noise, and the veto is absolute",
            "the same"),
    Tunable("MAX_FREE_PARAMETERS", robustness.MAX_FREE_PARAMETERS, "crypto/robustness.py", DERIVED,
            "the validator caps entry conditions at 8, so 11 is the worst case",
            "`factory.MAX_ENTRY_CONDITIONS` moving"),
    Tunable("MIN_HOLDOUT_PERIODS", robustness.MIN_HOLDOUT_PERIODS, "crypto/robustness.py", MEASURED,
            "the unit of independence is the market period, not the trade (+0.459 cross-context)",
            "a re-measurement of block correlation over a different market regime"),

    # --- the space the factory may mint into -------------------------------------------------
    Tunable("STOP_ATR_RANGE", factory.STOP_ATR_RANGE, "crypto/factory.py", INHERITED,
            "validator bounds, source S3 verbatim: outside is rejected, never clamped",
            "a measured stop distribution; the mint-time floor moved separately in #420"),
    Tunable("TARGET_ATR_RANGE", factory.TARGET_ATR_RANGE, "crypto/factory.py", INHERITED,
            "validator bounds, source S3 verbatim", "the same"),
    Tunable("MAX_RISK_PER_TRADE_R", factory.MAX_RISK_PER_TRADE_R, "crypto/factory.py", INHERITED,
            "validator bound, source S3 verbatim", "the same"),
    Tunable("MIN_REWARD_RISK", factory.MIN_REWARD_RISK, "crypto/factory.py", INHERITED,
            "validator bound, source S3 verbatim: a spec must at least pay 1:1", "the same"),
    Tunable("MAX_ENTRY_CONDITIONS", factory.MAX_ENTRY_CONDITIONS, "crypto/factory.py", INHERITED,
            "validator bound, source S3 verbatim; answers rule LEGALITY and nothing else",
            "nothing routine — judgeability is a separate bound, added deliberately as one"),
    Tunable("MAX_FUSION_ENTRY_CONDITIONS", factory.MAX_FUSION_ENTRY_CONDITIONS,
            "crypto/factory.py", MEASURED,
            "0 of 22 mints at 8 conditions produced a judgeable holdout; the band yields nothing",
            "a mint at 8 conditions reaching `MIN_HOLDOUT_TRADES` — needs a longer replay window"),
    Tunable("HOLDOUT_FRACTION", factory.HOLDOUT_FRACTION, "crypto/factory.py", INHERITED,
            "the most recent 30% is withheld from scoring entirely",
            "evidence that the split size is what limits holdout depth rather than signal rate"),
    Tunable("ELITE_EVIDENCE_MIN_TRADES", factory.ELITE_EVIDENCE_MIN_TRADES, "crypto/factory.py",
            OPERATOR, "a search centre moved on three trades is not a lesson",
            "a measured relationship between centre-evidence depth and child quality"),
    Tunable("FACTORY_DEPTH_DAYS", market_data.FACTORY_DEPTH_DAYS, "crypto/market_data.py", MEASURED,
            "calendar depth, not a bar count: a flat window makes fast timeframes single-regime",
            "F4 — the window is our constant rather than the venue's limit, and it moved once already"),

    # --- the paper book's own caps -----------------------------------------------------------
    Tunable("MAX_CONCURRENT_POSITIONS", paper.MAX_CONCURRENT_POSITIONS, "crypto/paper.py", DERIVED,
            "derived from `guards.DAILY_MAX_LOSS_R` at the pool's standard risk",
            "the daily loss breaker moving — which is itself INHERITED and never re-decided"),
    Tunable("MAX_POSITIONS_PER_SYMBOL", paper.MAX_POSITIONS_PER_SYMBOL, "crypto/paper.py", DERIVED,
            "the per-symbol share of the concurrency cap above", "the cap above moving"),
    Tunable("DEFAULT_MAX_HOLD_BARS", paper.DEFAULT_MAX_HOLD_BARS, "crypto/paper.py", INHERITED,
            "the time exit a spec gets when it declares none",
            "the measured median hold, which is 5-27% of this on every timeframe"),
    Tunable("MIN_VOL_SIZE_MULTIPLIER", paper.MIN_VOL_SIZE_MULTIPLIER, "crypto/paper.py", OPERATOR,
            "a floor on volatility down-scaling; below it the venue refuses the entry anyway",
            "the venue's minimum quantity or notional changing"),
    Tunable("MIN_REGIME_TRADES_TO_EXCLUDE", paper.MIN_REGIME_TRADES_TO_EXCLUDE, "crypto/paper.py",
            OPERATOR,
            "a per-regime sign read off three trades is the overfitting hazard wired into routing",
            "a measured relationship between per-regime sample size and forward sign"),

    # --- what counts as enough evidence to score at all --------------------------------------
    Tunable("MIN_FACTORY_BARS", market_data.MIN_FACTORY_BARS, "crypto/market_data.py", VENUE,
            "what the venue can answer: ~2.1k daily bars on the shortest routed history (SOLUSDT)",
            "a venue with deeper daily history, or a routed universe that drops the shortest one"),
    Tunable("MIN_BARS_FOR_HOLDOUT", factory.MIN_BARS_FOR_HOLDOUT, "crypto/factory.py", INHERITED,
            "the bar floor under `HOLDOUT_FRACTION`; below it there is no tail worth withholding",
            "F4's replay-window work, which is what decides how much tail there is to split"),
    Tunable("MIN_TRADES_PER_WINDOW", factory.MIN_TRADES_PER_WINDOW, "crypto/factory.py", INHERITED,
            "a walk-forward slice needs this many closed trades before its sign counts",
            "the same evidence that moved `MIN_HOLDOUT_TRADES` off 3 — this one is still 3"),
    Tunable("MAX_HOLDING_BARS_RANGE", factory.MAX_HOLDING_BARS_RANGE, "crypto/factory.py",
            INHERITED, "validator bounds, source S3 verbatim",
            "the measured median hold, which sits at 5-27% of what a spec declares"),
    Tunable("MIN_SAMPLE_SIZE", feedback.MIN_SAMPLE_SIZE, "crypto/feedback.py", INHERITED,
            "source default for the performance report; NOT what Gate 0 uses",
            "nothing on its own — `LIVE_CANDIDATE_MIN_SAMPLE` is the one that gates money"),

    # === second slice, 2026-08-08 — the ten modules the first one left ==========================
    #
    # The first slice took the money path and named its boundary in the coverage test so the rest
    # was a decision rather than an oversight. This finishes it: every crypto module is swept now.
    # The count is the surprise — 29 decision-shaped constants had no owner across those ten, not
    # the hundreds §G1's headline implies, because the headline counts every upper-case assignment
    # and most of them are mechanics. Twelve are decisions and are indexed here; seventeen are
    # estimator warm-up, page budgets and display thresholds, and are named in the test's
    # `MECHANICS` with a reason apiece.

    # --- what the regime label and the digest inherited, and nobody re-measured ---------------
    Tunable("ADX_TREND_THRESHOLD", features.ADX_TREND_THRESHOLD, "crypto/features.py", INHERITED,
            "source `entry_policy.adx_trend_threshold`; the cutoff every regime label turns on",
            "§F6 already reads this runtime's regime/outcome record for a different question"),
    Tunable("TREND_THRESHOLD_R", digest.TREND_THRESHOLD_R, "crypto/digest.py", INHERITED,
            "source `performance_digest`'s ±0.1R band, ported whole with the bucketing",
            "a digest verdict that disagrees with what the ledger says over the same window"),
    Tunable("DEFAULT_MIN_BUCKET_SAMPLE", digest.DEFAULT_MIN_BUCKET_SAMPLE, "crypto/digest.py",
            INHERITED, "the same port's floor for calling a bucket judgeable at all",
            "the same evidence that moved `MIN_HOLDOUT_TRADES` off 3"),

    # --- floors this runtime measured for itself ----------------------------------------------
    Tunable("MIN_MEANINGFUL_SAMPLE", dashboard.MIN_MEANINGFUL_SAMPLE, "crypto/dashboard.py",
            MEASURED,
            "the board graduated here while +0.08R over 60 trades sat 0.4 standard errors from 0",
            "nothing: it is a floor now, not the promoter — `MIN_INTERVAL_SAMPLE` and the interval are"),
    Tunable("MIN_POST_MINT_DAYS", null_control.MIN_POST_MINT_DAYS, "crypto/null_control.py",
            MEASURED, "a month is one market period, and the period is the unit of independence",
            "the store accumulating — on the day it landed nothing cleared it, which was honest"),
    Tunable("MIN_POST_MINT_TRADES", null_control.MIN_POST_MINT_TRADES, "crypto/null_control.py",
            DERIVED, "the same independence argument on the other axis: a long window can be thin",
            "moves with `MIN_POST_MINT_DAYS`; neither alone makes a window judgeable"),

    # --- decided here, with the argument in the code beside each ------------------------------
    Tunable("MAX_PROPOSALS_PER_RUN", proposer.MAX_PROPOSALS_PER_RUN, "crypto/proposer.py",
            OPERATOR, "an unbounded list spends the whole allowance on quantity and none on thought",
            "a review rate that empties the backlog faster than the cap fills it"),
    Tunable("MAX_UNREVIEWED_BACKLOG", proposer.MAX_UNREVIEWED_BACKLOG, "crypto/proposer.py",
            OPERATOR, "M4b's gate: it reverses the 2026-07-24 manual-CLI-only decision safely",
            "installing families into `factory.TEMPLATES` faster than the proposer accepts them"),
    Tunable("BACKLOG_WINDOW_DAYS", proposer.BACKLOG_WINDOW_DAYS, "crypto/proposer.py", OPERATOR,
            "how far back the backlog counts, so the tap reopens without anyone installing anything",
            "#607 already corrected what this measures — it is the rotation horizon, not 30 days of review"),
    Tunable("MAX_CONDITION_LAG", strategy.MAX_CONDITION_LAG, "crypto/strategy.py", OPERATOR,
            "one bar, because that is what a crossover needs and each depth copies the vocabulary",
            "a hypothesis that needs persistence over n bars — which wants a rolling derivative instead"),
    Tunable("MIN_CROSS_SECTION_MEMBERS", features.MIN_CROSS_SECTION_MEMBERS, "crypto/features.py",
            OPERATOR, "below it `xs_rank_pct` has fewer distinct values than a coin flip has sides",
            "a cohort that is reliably deeper — the floor is about determinacy, not about universe size"),

    # --- a window that is really another window -----------------------------------------------
    Tunable("FEE_MEASUREMENT_DAYS", account.FEE_MEASUREMENT_DAYS, "crypto/account.py", DERIVED,
            "matches the longest window `PNL_WINDOW_DAYS` already reports; one query, same span",
            "a fee tier that changes inside the window, which is what the bound exists to exclude"),

    # === third slice, 2026-08-09 — found by profiling, not by reading ===========================
    #
    # Profiling the scheduler put `candle_archive` at 64% of everything it does (p50 125s, ~49
    # minutes a day), and all three constants that govern it were invisible to the coverage
    # test's name pattern — as was the number that bounds live order size. A pattern that misses
    # a ceiling and a pace misses the two shapes an operator most often reaches for.

    Tunable("HARD_CEILING_USDT", live_budget.HARD_CEILING_USDT, "crypto/live_budget.py", OPERATOR,
            "the largest per-order cap a registered budget may declare; 200 at bring-up, 500 since",
            "Thomas raised it 2026-08-08 after 4 clean canaries and 2 round trips; the same evidence"),
    Tunable("ARCHIVE_REQUEST_INTERVAL_SECONDS", candle_archive.ARCHIVE_REQUEST_INTERVAL_SECONDS,
            "crypto/candle_archive.py", MEASURED,
            "the venue's own wall, measured: 352 reads at speed answered ~70 and rate-limited 282",
            "a venue that answers faster — nothing else; the number is a demonstrated limit"),
    Tunable("ARCHIVE_BOOKS_PER_PASS", candle_archive.ARCHIVE_BOOKS_PER_PASS,
            "crypto/candle_archive.py", OPERATOR,
            "archive latency traded against tick latency: `run_due` is sequential and the live "
            "leg's `_settle_or_protect` runs on the same tick",
            "the scheduler ceasing to run due schedules sequentially, which is what forces the trade"),
    Tunable("VENUE_CANDLE_CEILING", candle_archive.VENUE_CANDLE_CEILING,
            "crypto/candle_archive.py", VENUE,
            "Hyperliquid serves at most this many candles per request and nothing behind them",
            "a venue that pages backwards — which is the whole reason this archive exists"),

    # --- the stop-slippage probe (proposal §5, Thomas 2026-08-11) -----------------------------
    # The probe exists to measure DEFAULT_STOP_SLIPPAGE_BPS's sample; its own numbers ride in
    # the batch approval's content hash, so changing any of them mints a different approval.
    Tunable("PROBE_STOP_BPS", probe.PROBE_STOP_BPS, "crypto/probe.py", OPERATOR,
            "§5-2: the factory's representative stop width, fixed rather than ATR-linked so "
            "every probe buys the same measurement",
            "the factory's representative stop width moving, or a re-approved probe design — "
            "either way a new batch approval, never an edit"),
    Tunable("WORST_CASE_LOSS_FRACTION", probe.WORST_CASE_LOSS_FRACTION, "crypto/probe.py", DERIVED,
            "§3's arithmetic: stop 40 + measured-worst slippage 23.5 (§C, 2026-08-06) + ~10 "
            "round-trip fees = 73.5 bps, carried as 0.75% so the budget cap errs upward",
            "any term moving — a worse measured slippage RAISES it, the fail-closed direction "
            "for a budget cap"),
    Tunable("DECISION_SAMPLE_FLOOR", probe.DECISION_SAMPLE_FLOOR, "crypto/probe.py", OPERATOR,
            "§5-5: at sample N >= 10 the DEFAULT_STOP_SLIPPAGE_BPS re-decision opens (this "
            "floor is that constant's own reopens_when, quantified)",
            "the re-decision actually opening — the floor retires with the interim constant "
            "it exists to replace"),
)


def by_provenance(provenance: str) -> tuple[Tunable, ...]:
    """Every indexed constant with this provenance. ``INHERITED`` is the interesting one."""
    return tuple(t for t in TUNABLES if t.provenance == provenance)


def names() -> frozenset[str]:
    return frozenset(t.name for t in TUNABLES)
