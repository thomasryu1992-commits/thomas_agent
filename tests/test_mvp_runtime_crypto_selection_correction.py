"""A1 — the ranking charges a candidate for the siblings it was selected out of.

`rank_candidates` takes the maximum over a store that only grows, and every statistic beneath
it judges one candidate against a fixed standard. Nothing knew the store had 979 members, so
nothing could say that the top of it is what a null model predicts: measured 2026-08-03, 39 of
959 candidates clear an uncorrected t of 1.96 — 4.1% against the 2.5% pure noise produces — and
the highest expectancy in the store (+2.713R) came from a candidate with ONE closed trade.

These pin the three halves of the fix: the attempt count is a property of the bars (not of the
family, and not of the row), the threshold rises with it, and a candidate that cannot produce a
t is never given the benefit of the doubt.
"""

from __future__ import annotations

import math

import pytest

from runtime.mvp_runtime.crypto.pool import (
    attempt_context_key,
    pooled_context_keys,
    attempts_by_context,
    candidate_quality,
    rank_candidates,
    search_context_key,
)
from runtime.mvp_runtime.crypto.robustness import (
    CONFIDENCE_Z,
    SELECTION_BELOW,
    SELECTION_CLEARS_CORRECTED,
    SELECTION_CLEARS_UNCORRECTED,
    SELECTION_UNMEASURED,
    expectancy_t,
    selection_adjusted_z,
    selection_rank,
)


def _candidate(cid, *, symbol="BTCUSDT", timeframe="1h", family="breakout",
               expectancy=0.2, stdev=None, closed=100):
    evidence = {
        "robustness": {"verdict": "PROVISIONAL", "robustness_score": 0.5,
                       "trades_per_parameter": 12.0, "holdout_status": "UNCONFIRMED"},
        "closed_count": closed, "win_count": int(closed * 0.4),
        "avg_win_R": 2.0, "avg_loss_R": 1.0, "expectancy": expectancy,
    }
    if stdev is not None:
        evidence["stdev_r"] = stdev
    return {
        "candidate_id": cid,
        "champion_score": 0.5,
        "strategy_spec": {"strategy_family": family, "symbol_scope": [symbol],
                          "timeframe": timeframe},
        "backtest_evidence": evidence,
    }


# --- what counts as one attempt -----------------------------------------------

def test_the_context_is_the_bars_not_the_family():
    """Two families on one symbol and timeframe were tried against the same data. Counting
    per family would divide the burden by the choice that creates it."""
    a = _candidate("c1", family="breakout")
    b = _candidate("c2", family="mean_reversion")
    assert search_context_key(a["strategy_spec"]) == search_context_key(b["strategy_spec"])
    assert attempts_by_context([a, b]) == {(("BTCUSDT",), "1h"): 2}


def test_a_different_market_or_timeframe_is_a_different_question():
    records = [_candidate("c1"), _candidate("c2", symbol="ETHUSDT"),
               _candidate("c3", timeframe="4h")]
    assert sorted(attempts_by_context(records).values()) == [1, 1, 1]


def test_re_appends_of_one_candidate_count_once():
    """The store is append-only and re-appends a lineage. Counting rows would raise the bar
    for candidates that never moved."""
    again = _candidate("c1", expectancy=0.3)
    assert attempts_by_context([_candidate("c1"), again]) == {(("BTCUSDT",), "1h"): 1}


# --- the threshold ------------------------------------------------------------

def test_one_attempt_needs_no_correction():
    assert selection_adjusted_z(1) == CONFIDENCE_Z
    assert selection_adjusted_z(0) == CONFIDENCE_Z


def test_the_bar_rises_with_the_number_of_tries():
    z = [selection_adjusted_z(n) for n in (1, 10, 100, 1000)]
    assert z == sorted(z)
    assert z[0] == pytest.approx(1.96, abs=0.01)
    assert z[-1] == pytest.approx(4.06, abs=0.02)


def test_the_bar_grows_slowly_enough_that_a_real_edge_does_not_care():
    """Ten times the attempts costs about half a standard error. An edge that is real clears
    it; one that is the maximum of a noise population does not."""
    assert selection_adjusted_z(1000) - selection_adjusted_z(100) < 0.7


# --- the t-statistic ----------------------------------------------------------

def test_expectancy_t_is_the_mean_over_its_own_standard_error():
    assert expectancy_t(0.2, 1.4, 196) == pytest.approx(0.2 / (1.4 / math.sqrt(196)))


@pytest.mark.parametrize("expectancy,stdev,closed", [
    (0.2, None, 100),      # evidence predating the field
    (0.2, 0.0, 100),       # no observed variation is not evidence of none
    (0.2, 1.4, 1),         # a single trade has no spread
    (None, 1.4, 100),      # nothing to divide
    (0.2, "wide", 100),    # unusable shape
])
def test_an_unmeasurable_t_is_none_never_a_substitute(expectancy, stdev, closed):
    assert expectancy_t(expectancy, stdev, closed) is None


def test_the_t_is_not_reconstructed_from_the_payoff_legs():
    """`win_count`/`avg_win_R`/`avg_loss_R` could approximate a spread, and the approximation
    is biased low — it drops the variation within wins and within losses, which inflates every
    t built on it. A candidate carrying them and no `stdev_r` stays unmeasured."""
    q = candidate_quality(_candidate("c1", stdev=None), attempts=50)
    assert q["expectancy_t"] is None
    assert q["selection_rank"] == SELECTION_UNMEASURED


# --- the tiers ----------------------------------------------------------------

def test_an_edge_that_clears_its_siblings_outranks_one_that_only_clears_zero():
    strong = selection_rank(4.5, attempts=100)
    ordinary = selection_rank(2.5, attempts=100)
    assert (strong, ordinary) == (SELECTION_CLEARS_CORRECTED, SELECTION_CLEARS_UNCORRECTED)
    assert strong < ordinary


def test_the_same_t_changes_tier_when_the_store_grows():
    """The property nothing in this runtime could express before: believability is not a
    property of one candidate alone."""
    assert selection_rank(3.0, attempts=2) == SELECTION_CLEARS_CORRECTED
    assert selection_rank(3.0, attempts=500) == SELECTION_CLEARS_UNCORRECTED


def test_below_zero_is_below_whatever_the_attempt_count():
    assert selection_rank(0.4, attempts=1) == SELECTION_BELOW


def test_an_unknown_attempt_count_is_not_the_cheaper_answer():
    """A caller without the store must not hand every candidate the uncorrected bar."""
    assert selection_rank(9.9, attempts=None) == SELECTION_UNMEASURED


# --- the ordering -------------------------------------------------------------

def test_the_tier_orders_within_a_verdict_tier():
    """Same verdict, same cost basis, same depth: the one whose edge survives selection first."""
    survives = _candidate("cand_survives", expectancy=0.9, stdev=1.4, closed=400)
    selected = _candidate("cand_selected", expectancy=0.2, stdev=1.4, closed=400)
    ranked = [r["candidate_id"] for r in rank_candidates([selected, survives])]
    assert ranked.index("cand_survives") < ranked.index("cand_selected")


def test_a_store_with_no_recorded_spreads_ranks_exactly_as_it_did_before():
    """Every candidate on this machine when the tier landed. A uniform tier is a no-op
    tiebreak, so the tier cannot reshuffle a store it has nothing to say about."""
    records = [_candidate(f"cand_{i}", expectancy=0.1 * i, stdev=None) for i in range(5)]
    assert {q["selection_rank"] for q in (candidate_quality(r) for r in records)} == {
        SELECTION_UNMEASURED}
    ranked = [r["candidate_id"] for r in rank_candidates(records)]
    assert ranked == [r["candidate_id"] for r in rank_candidates(list(reversed(records)))]


def test_the_quality_view_reports_the_bar_it_applied():
    """An operator reading the promotion surface has to be able to see WHY a good-looking
    edge ranks below a smaller one on a less-searched context."""
    q = candidate_quality(_candidate("c1", expectancy=0.3, stdev=1.4, closed=400), attempts=62)
    assert q["attempts_in_context"] == 62
    assert q["selection_adjusted_z"] == pytest.approx(3.35, abs=0.01)
    assert q["expectancy_t"] == pytest.approx(0.3 / (1.4 / math.sqrt(400)), abs=1e-4)


def test_rank_candidates_counts_attempts_over_the_population_it_sorts():
    """The tier an operator sees must be the tier the ordering used — so the count comes from
    the same list, not from a store read a second time.

    ``expectancy`` puts t at 2.5: above the uncorrected 1.96 and below the ~3.34 that 60
    siblings demand. A t high enough to clear both bars would tie the two tiers and prove
    nothing, which is the arithmetic this test is actually about."""
    ev = 2.5 * (1.4 / math.sqrt(400))
    crowded = [_candidate(f"btc_{i}", expectancy=ev, stdev=1.4, closed=400) for i in range(60)]
    lonely = _candidate("eth_1", symbol="ETHUSDT", expectancy=ev, stdev=1.4, closed=400)
    ranked = [r["candidate_id"] for r in rank_candidates(crowded + [lonely])]
    # Identical evidence; the only difference is how many siblings it was chosen out of.
    assert ranked[0] == "eth_1"


# --- the attempt follows the bars it was scored against (F9 topup omission) ------

COHORT = ("BNBUSDT", "BTCUSDT", "DOGEUSDT", "ETHUSDT", "SOLUSDT")


def _pooled(cid, *, timeframe="4h", cohort=COHORT, **kw):
    row = _candidate(cid, timeframe=timeframe, **kw)
    row["strategy_spec"]["symbol_scope"] = list(cohort)
    row["backtest_evidence"]["symbols_replayed"] = len(cohort)
    return row


def _mislabelled(cid, *, symbol="BTCUSDT", timeframe="4h", symbols=5, in_holdout=False, **kw):
    """The F9 topup shape: single-symbol scope, cohort-scored evidence (fixed by #712)."""
    row = _candidate(cid, symbol=symbol, timeframe=timeframe, **kw)
    if in_holdout:
        row["backtest_evidence"]["holdout"] = {"symbols": symbols, "closed_count": 30}
    else:
        row["backtest_evidence"]["symbols_replayed"] = symbols
    return row


def test_a_scope_mismatched_row_charges_the_cohort_it_was_scored_on():
    """An attempt charges the bars it was scored against. A topup row minted single-scope but
    scored pooled was never an attempt against the single-symbol bars — leaving it there
    understated the pooled context's burden (the anti-conservative direction the audit
    measured: bar 3.49 where the true count says 3.64) and inflated the single context's."""
    rows = [
        _pooled("pooled_1"),
        _mislabelled("mis_1"),
        _mislabelled("mis_2", in_holdout=True),
        _candidate("single_1", timeframe="4h"),
    ]
    counts = attempts_by_context(rows)
    pooled_key = (COHORT, "4h")
    assert counts[pooled_key] == 3
    assert counts[(("BTCUSDT",), "4h")] == 1


def test_the_mismatched_row_is_judged_against_the_key_it_charges():
    """Counting and lookup must move together: a row judged against a key it does not charge
    would face a bar computed without its own attempt in it."""
    rows = [_pooled("pooled_1"), _mislabelled("mis_1"), _candidate("single_1", timeframe="4h")]
    pooled_keys = pooled_context_keys(rows)
    assert attempt_context_key(rows[1], pooled_keys=pooled_keys) == (COHORT, "4h")
    assert attempt_context_key(rows[2], pooled_keys=pooled_keys) == (("BTCUSDT",), "4h")


def test_reattribution_needs_an_unambiguous_cohort():
    """Two same-size cohorts on one timeframe make the move unprovable, and an unprovable
    move stays home: the stored key is the pre-existing behavior, and today it carries the
    LARGER count, so falling back can only raise the bar, never lower it."""
    other = ("ADAUSDT", "BTCUSDT", "LINKUSDT", "XRPUSDT", "LTCUSDT")
    rows = [_pooled("pooled_1"), _pooled("pooled_2", cohort=other), _mislabelled("mis_1")]
    pooled_keys = pooled_context_keys(rows)
    assert ("4h", 5) not in pooled_keys
    assert attempt_context_key(rows[2], pooled_keys=pooled_keys) == (("BTCUSDT",), "4h")


def test_a_symbol_outside_the_cohort_stays_home():
    """Membership is part of the proof: a single-scope row whose symbol the cohort does not
    contain cannot have been one of its draws."""
    rows = [_pooled("pooled_1"), _mislabelled("mis_1", symbol="XRPUSDT")]
    pooled_keys = pooled_context_keys(rows)
    assert attempt_context_key(rows[1], pooled_keys=pooled_keys) == (("XRPUSDT",), "4h")


def test_a_store_with_no_cohort_counts_exactly_as_before():
    """Pre-F9 stores (and every single-symbol row in any store) are untouched: the
    evidence-aware key is the stored key whenever nothing pooled exists to move to."""
    rows = [_candidate(f"c_{i}") for i in range(4)] + [_mislabelled("mis_1")]
    counts = attempts_by_context(rows)
    assert counts[(("BTCUSDT",), "1h")] == 4
    assert counts[(("BTCUSDT",), "4h")] == 1  # the mismatched row stayed on its stored key
