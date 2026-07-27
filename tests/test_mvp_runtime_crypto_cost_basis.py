"""The promotion evidence names the cost basis it was measured on.

The first version of this file asserted the opposite and was wrong: it claimed the listing's
R excluded costs. It does not. `factory.backtest_spec` runs every closed trade through
`cost.apply_cost_model` and states that `result_R` — and therefore `expectancy` and
`champion_score` — is the NET R after fees and slippage. The error came from reading
`robustness.py`'s "the cost model was not ported" as a statement about R; it is a statement
about the scorer's cost-ROBUSTNESS term, which measures whether an edge survives ACROSS cost
assumptions rather than whether costs were charged.

What is actually worth telling the operator is the rate. The ported default charges 2.5 bps
taker; this account was charged 0.1291 USDT over roughly 258 USDT of fills on 2026-07-26 —
5.0 bps, Binance USD-M standard. The evidence is measured against a venue half as expensive
as the one the orders reach, and the promotion gate is a person reading these numbers.
"""

from __future__ import annotations

from runtime.mvp_runtime.crypto.pool import (
    EDGE_COST_BASIS_NET,
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
    assert candidate_quality(_RECORD)["cost_basis"] == EDGE_COST_BASIS_NET


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
    assert "NET of costs" in out
    assert "HALF what this" in out, "the rate gap is the actionable half of the note"
    note_at = out.index("NET of costs")
    row_at = out.index("cand_x")
    assert note_at < row_at, "the basis must be stated above the candidate rows"
