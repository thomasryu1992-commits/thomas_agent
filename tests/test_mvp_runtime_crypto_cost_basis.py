"""The promotion evidence names what its R does not include.

Paper settlement models no fee, slippage or funding by design, and the robustness scorer
withholds its cost term for the same reason — both say so in their own docstrings. The
promotion surface said nothing, so a cost-free expectancy arrived looking like a net one in
the one place where the number decides whether real money goes behind a strategy.

That matters more here than in most repositories because the promotion gate is a person:
there is no automated statistical threshold, only Thomas's approval bound to a content hash.
This runtime's own paper record averages +0.092R per trade, which is inside the range a round
trip costs — so "upper bound" is not a pedantic caveat on these numbers, it is the difference
between a positive edge and a negative one.
"""

from __future__ import annotations

from runtime.mvp_runtime.crypto.pool import (
    EDGE_COST_BASIS_EXCLUDED,
    candidate_quality,
)

_RECORD = {
    "candidate_id": "cand_x",
    "strategy_id": "S001",
    "generation_id": "GEN-001",
    "strategy_spec": {"strategy_family": "trend_pullback"},
    "champion_score": 0.8,
    "backtest_evidence": {
        "robustness": {"verdict": "PROVISIONAL", "holdout_status": "CONFIRMED"},
        "closed_count": 40, "win_count": 18,
        "avg_win_R": 2.0, "avg_loss_R": 1.0, "expectancy": 0.35,
    },
}


def test_the_quality_view_names_its_cost_basis():
    assert candidate_quality(_RECORD)["cost_basis"] == EDGE_COST_BASIS_EXCLUDED


def test_the_basis_is_a_field_not_a_printed_sentence():
    """It is a property OF the number, so a consumer can refuse to compare across bases.

    A later cost-adjusted basis becomes a different value here rather than silently changing
    what the old numbers meant.
    """
    q = candidate_quality(_RECORD)
    assert isinstance(q["cost_basis"], str) and q["cost_basis"]
    assert "expectancy" in q and "reward_risk" in q


def test_the_promotion_listing_states_the_basis_before_the_numbers(monkeypatch, capsys):
    """Stated before the rows: a caveat under a screen of figures is one nobody reads."""
    from scripts import promote_strategy_candidates as prom

    monkeypatch.setattr(prom.pool_store, "read_candidates", lambda root: [dict(_RECORD)])
    prom.main(["--list"])

    out = capsys.readouterr().out
    assert "EXCLUDES trading costs" in out
    assert "upper bounds" in out
    note_at = out.index("EXCLUDES trading costs")
    row_at = out.index("cand_x")
    assert note_at < row_at, "the basis must be stated above the candidate rows"
