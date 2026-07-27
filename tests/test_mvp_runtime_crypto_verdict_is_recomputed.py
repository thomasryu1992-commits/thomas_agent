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
