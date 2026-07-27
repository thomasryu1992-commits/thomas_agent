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
    EDGE_COST_BASIS_UNRECORDED,
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


def test_the_quality_view_names_the_rates_the_candidate_was_scored_under():
    """The rates, not just "costed" — the store holds candidates scored under two models."""
    record = {**_RECORD, "backtest_evidence": {
        **_RECORD["backtest_evidence"],
        "cost_summary": {"cost_model": {"taker_fee_bps": 2.5, "slippage_bps": 3.0}},
    }}
    basis = candidate_quality(record)["cost_basis"]
    assert basis.startswith(EDGE_COST_BASIS_NET)
    assert "2.5" in basis and "3.0" in basis


def test_a_candidate_without_a_recorded_cost_model_says_so():
    """Reporting the current default would claim it paid a rate it never faced."""
    assert candidate_quality(_RECORD)["cost_basis"] == EDGE_COST_BASIS_UNRECORDED


def test_two_candidates_scored_under_different_models_do_not_share_a_basis():
    """The property the mixed-store warning depends on."""
    def at(taker):
        return candidate_quality({**_RECORD, "backtest_evidence": {
            **_RECORD["backtest_evidence"],
            "cost_summary": {"cost_model": {"taker_fee_bps": taker, "slippage_bps": 3.0}},
        }})["cost_basis"]
    assert at(2.5) != at(5.0)


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
    assert "5.0" in out, "the current taker rate must be stated"
    note_at = out.index("NET of costs")
    row_at = out.index("cand_x")
    assert note_at < row_at, "the basis must be stated above the candidate rows"


def test_a_mixed_basis_store_is_flagged_not_silently_ranked(monkeypatch, capsys):
    """The consequence of moving the default: the store now holds both, and ranking is blind.

    `backtest_evidence` is durable, so raising the taker default does not re-score anything —
    it splits the store. `rank_candidates` orders by verdict tier and edge quality with no
    notion that one row paid half the fee the other did, so the listing is the only place that
    can say so.
    """
    from scripts import promote_strategy_candidates as prom

    def at(cid, taker):
        return {**_RECORD, "candidate_id": cid, "backtest_evidence": {
            **_RECORD["backtest_evidence"],
            "cost_summary": {"cost_model": {"taker_fee_bps": taker, "slippage_bps": 3.0}},
        }}

    monkeypatch.setattr(prom.pool_store, "read_candidates",
                        lambda root: [at("cand_old", 2.5), at("cand_new", 5.0)])
    prom.main(["--list"])

    out = capsys.readouterr().out
    assert "MIXED BASES" in out
    assert "not scored alike" in out.lower()
    assert "2.5" in out and "5.0" in out


def test_a_single_basis_store_is_not_warned_about(monkeypatch, capsys):
    """The warning has to stay rare enough to be read when it appears.

    Via monkeypatch, not a bare attribute assignment: the first version of this test set
    `pool_store.read_candidates` directly and never restored it, so every later test in the
    run saw a two-row candidate store. Twenty-four failures, none of them in the code under
    test.
    """
    from scripts import promote_strategy_candidates as prom

    record = {**_RECORD, "backtest_evidence": {
        **_RECORD["backtest_evidence"],
        "cost_summary": {"cost_model": {"taker_fee_bps": 5.0, "slippage_bps": 3.0}},
    }}
    monkeypatch.setattr(prom.pool_store, "read_candidates", lambda root: [record])
    prom.main(["--list"])
    assert "MIXED BASES" not in capsys.readouterr().out
