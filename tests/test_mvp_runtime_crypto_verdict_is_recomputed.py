"""The promotion shortlist's verdict is the current rule's answer, not a stored label.

Verdicts are written once, at mint time, under whatever rule was current then. The rule
changed when ROBUST became gated on out-of-sample survival — and `candidate_quality` went on
reading `robustness["verdict"]` back, so candidates minted before that kept a stored ROBUST
beside a holdout reading UNCONFIRMED: a pair `classify_verdict` can no longer produce.

Measured on a live store: 12 of 269 candidates. `rank_candidates` orders by verdict tier
FIRST, so all 12 sorted above the 13 PROVISIONAL+CONFIRMED lineages that had actually survived
unseen bars — the shortlist inverted on exactly the property the holdout rule was added to
enforce, in the surface an operator reads before putting real money behind a strategy.
"""

from __future__ import annotations

from runtime.mvp_runtime.crypto.pool import candidate_quality, rank_candidates
from runtime.mvp_runtime.crypto.robustness import ROBUST, PROVISIONAL


def _candidate(cid: str, *, verdict: str, score: float, tpp: float, holdout: str | None,
               closed: int = 40, wins: int = 20) -> dict:
    robustness = {"verdict": verdict, "robustness_score": score, "trades_per_parameter": tpp}
    if holdout is not None:
        robustness["holdout_status"] = holdout
    return {
        "candidate_id": cid,
        "champion_score": score,
        "backtest_evidence": {
            "robustness": robustness,
            "closed_count": closed,
            "win_count": wins,
            "avg_win_R": 2.0,
            "avg_loss_R": 1.0,
            "expectancy": 0.5,
        },
    }


def test_a_stored_robust_with_an_unconfirmed_holdout_is_recomputed_down():
    """The exact stale pair the live store held 12 of."""
    stale = _candidate("cand_stale", verdict=ROBUST, score=0.88, tpp=8.5, holdout=None)
    assert candidate_quality(stale)["verdict"] == PROVISIONAL


def test_a_genuinely_robust_candidate_keeps_its_tier():
    fresh = _candidate("cand_fresh", verdict=ROBUST, score=0.88, tpp=8.5, holdout="CONFIRMED")
    assert candidate_quality(fresh)["verdict"] == ROBUST


def test_confirmed_out_of_sample_now_outranks_the_stale_label():
    """The property the whole change exists for: the shortlist stops being inverted.

    Before, `cand_stale` sorted first on its stored ROBUST tier despite never having faced
    unseen bars, ahead of a lineage that had faced them and survived.
    """
    stale = _candidate("cand_stale", verdict=ROBUST, score=0.88, tpp=8.5, holdout=None)
    confirmed = _candidate("cand_confirmed", verdict=PROVISIONAL, score=0.70, tpp=8.5,
                           holdout="CONFIRMED")
    ranked = [r["candidate_id"] for r in rank_candidates([stale, confirmed])]
    assert ranked.index("cand_confirmed") < ranked.index("cand_stale")


def test_a_record_without_the_components_keeps_its_stored_verdict():
    """Recomputing from absent inputs would invent a rating rather than correct one."""
    legacy = {
        "candidate_id": "cand_legacy",
        "backtest_evidence": {"robustness": {"verdict": ROBUST}, "closed_count": 10,
                              "win_count": 5, "expectancy": 0.1},
    }
    assert candidate_quality(legacy)["verdict"] == ROBUST


def test_the_critical_ratio_veto_survives_recomputation():
    """Below the critical trades-per-parameter every other component is noise, holdout or not."""
    thin = _candidate("cand_thin", verdict=ROBUST, score=0.95, tpp=2.0, holdout="CONFIRMED")
    assert candidate_quality(thin)["verdict"] == "FRAGILE"


# --- the holdout STATUS is a stored label too ---------------------------------
#
# The recomputation above fixed one label and fed itself the other one stale. A stored
# CONFIRMED was minted under whatever confirmation rule was current then, and the rule has
# since changed twice — so the pair the tests above pin (stored ROBUST, current holdout) has a
# twin: stored CONFIRMED, current block. Measured on the live store when this was found: 236
# stored CONFIRMED labels under a rule that confirms 1 of them.

def _with_block(cid: str, block: dict | None, *, stored_holdout: str = "CONFIRMED") -> dict:
    record = _candidate(cid, verdict=ROBUST, score=0.88, tpp=8.5, holdout=stored_holdout)
    if block is not None:
        record["backtest_evidence"]["holdout"] = block
    return record


def test_a_stored_confirmed_does_not_survive_a_block_that_cannot_confirm():
    """A tail of 3 profitable trades WAS a confirmation. The label outlived the rule."""
    old_rule = _with_block("cand_old", {"closed_count": 3, "total_R": 1.5, "expectancy": 0.5})
    assert candidate_quality(old_rule)["holdout_status"] == "INSUFFICIENT"
    assert candidate_quality(old_rule)["verdict"] == PROVISIONAL


def test_a_stored_confirmed_does_not_survive_a_block_with_no_recorded_spread():
    """The 847 on this machine: deep tails, real profit, no interval to judge them by."""
    unpriceable = _with_block("cand_legacy", {"closed_count": 400, "total_R": 90.0,
                                              "expectancy": 0.225})
    assert candidate_quality(unpriceable)["holdout_status"] == "INSUFFICIENT"
    assert candidate_quality(unpriceable)["verdict"] == PROVISIONAL


def test_a_block_that_clears_the_current_rule_is_confirmed_whatever_the_label_says():
    """It recomputes in both directions, or it is a demotion rather than a rule."""
    understated = _with_block("cand_good", {"closed_count": 400, "total_R": 90.0,
                                            "expectancy": 0.225, "stdev_r": 1.4,
                                            "period_r": [6.0, 7.5, 10.5, 9.0, 12.0,
                                                         8.0, 9.5, 7.0, 11.0, 9.5],
                                            "period_trades": [40] * 10},
                              stored_holdout="CONTRADICTED")
    assert candidate_quality(understated)["holdout_status"] == "CONFIRMED"
    assert candidate_quality(understated)["verdict"] == ROBUST


def test_a_record_with_no_block_at_all_keeps_its_stored_status():
    """Candidates predating the holdout entirely. Their stored label is UNCONFIRMED, so the
    fallback grants nothing — but it must not invent an INSUFFICIENT either."""
    pre_holdout = _candidate("cand_pre", verdict=ROBUST, score=0.88, tpp=8.5, holdout=None)
    assert candidate_quality(pre_holdout)["holdout_status"] == "UNCONFIRMED"
    empty_block = _with_block("cand_empty", {}, stored_holdout="UNCONFIRMED")
    assert candidate_quality(empty_block)["holdout_status"] == "UNCONFIRMED"
