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

import pytest

from runtime.mvp_runtime.crypto.cost import (
    DEFAULT_STOP_SLIPPAGE_BPS,
    DEFAULT_FUNDING_BPS_PER_INTERVAL,
    DEFAULT_MAKER_FEE_BPS,
    DEFAULT_SLIPPAGE_BPS,
    DEFAULT_TAKER_FEE_BPS,
    FUNDING_SOURCE_VENUE,
)
from runtime.mvp_runtime.crypto.pool import (
    COST_BASIS_RANK_CONSERVATIVE,
    COST_BASIS_RANK_CURRENT,
    COST_BASIS_RANK_OPTIMISTIC,
    COST_BASIS_RANK_UNRECORDED,
    EDGE_COST_BASIS_NET,
    EDGE_COST_BASIS_UNRECORDED,
    assert_promotable_cost_basis,
    candidate_quality,
    cost_basis_of,
    cost_basis_rank,
    rank_candidates,
)
from runtime.mvp_runtime.errors import ToolError

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
    """The consequence of moving the default: the store now holds both, and says which is which.

    `backtest_evidence` is durable, so raising the taker default does not re-score anything —
    it splits the store. The listing names the split; `rank_candidates` then orders on it, so
    the warning and the ordering agree instead of the second quietly undoing the first.
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


# --- which WAY the basis errs, and what the promotion door does about it ----------
#
# Equality against the current model was the first rule tried and it was too blunt. On the live
# machine 90 of 359 rows are scored at taker 5.0 with no maker leg: their take-profit exit paid
# taker plus adverse slippage where the current model charges maker 2.0 and no slippage, so
# their numbers are too PESSIMISTIC. Refusing those would have made the escape hatch the normal
# door. What the gate actually blocks is evidence scored more CHEAPLY than the venue charges.

def _scored(cid, **model):
    return {**_RECORD, "candidate_id": cid, "backtest_evidence": {
        **_RECORD["backtest_evidence"],
        "cost_summary": {"total_net_r": 14.0, "total_fee_cost_r": 1.0,
                         "total_maker_fee_cost_r": 0.2, "cost_model": model},
    }}


def _current(cid="cand_now"):
    return _scored(cid, taker_fee_bps=DEFAULT_TAKER_FEE_BPS, maker_fee_bps=DEFAULT_MAKER_FEE_BPS,
                   slippage_bps=DEFAULT_SLIPPAGE_BPS,
                   stop_slippage_bps=DEFAULT_STOP_SLIPPAGE_BPS,
                   funding_bps_per_interval=DEFAULT_FUNDING_BPS_PER_INTERVAL,
                   funding_source=FUNDING_SOURCE_VENUE)


def test_the_current_model_is_the_top_tier():
    assert cost_basis_rank(_current()) == COST_BASIS_RANK_CURRENT


def test_a_cheaper_taker_rate_is_optimistic():
    """The 224-row case: half the fee the venue charges, so the edge on file is inflated."""
    assert cost_basis_rank(_scored("c", taker_fee_bps=2.5, slippage_bps=3.0)) == (
        COST_BASIS_RANK_OPTIMISTIC)


def test_a_pre_maker_record_at_the_right_taker_rate_is_conservative_not_optimistic():
    """The 90-row case, and the reason this is a direction test rather than an equality test.

    No maker term means that model had no maker leg: the exit paid the TAKER rate plus adverse
    slippage where today's charges maker 2.0 and none. Its error runs against the candidate.

    Carries a funding term so the case stays about the MAKER axis. The real 90 rows are also
    pre-funding, so today they fall into `test_evidence_with_no_funding_term_is_optimistic`
    below and are refused — one stale axis is enough, and which one it is does not change the
    verdict.
    """
    record = _scored("c", taker_fee_bps=DEFAULT_TAKER_FEE_BPS, slippage_bps=DEFAULT_SLIPPAGE_BPS,
                     stop_slippage_bps=DEFAULT_STOP_SLIPPAGE_BPS,
                     funding_bps_per_interval=DEFAULT_FUNDING_BPS_PER_INTERVAL,
                     funding_source=FUNDING_SOURCE_VENUE)
    assert cost_basis_rank(record) == COST_BASIS_RANK_CONSERVATIVE


# --- the funding axis (2026-07-29) ------------------------------------------------------------
#
# These are perpetuals. A position pays or earns funding every 8 hours, and `_EXIT_PARAMS` lets a
# 1d spec hold 12-48 days — 36 to 144 settlements against a modelled 10 bps of fees. Every
# candidate minted before this axis existed was scored on an instrument with no carry.

def test_evidence_with_no_funding_term_is_optimistic():
    """The whole pre-2026-07-29 store, in one assertion. A missing cost cannot read as a cost of
    zero on an instrument that charges every 8 hours."""
    record = _scored("c", taker_fee_bps=DEFAULT_TAKER_FEE_BPS, maker_fee_bps=DEFAULT_MAKER_FEE_BPS,
                     slippage_bps=DEFAULT_SLIPPAGE_BPS)
    assert cost_basis_rank(record) == COST_BASIS_RANK_OPTIMISTIC


def test_the_basis_string_names_the_omission_rather_than_dropping_the_term():
    """An omitted term reads as one fewer axis; a named one reads as a missing cost. The operator
    listing has to say which, because that is the difference between "older model" and "priced a
    perpetual as though it were free to hold"."""
    record = _scored("c", taker_fee_bps=DEFAULT_TAKER_FEE_BPS, maker_fee_bps=DEFAULT_MAKER_FEE_BPS,
                     slippage_bps=DEFAULT_SLIPPAGE_BPS)
    assert cost_basis_of(record).endswith("+funding_uncharged")
    assert "funding_source" not in cost_basis_of(record)  # the value, not the key name
    assert f"({FUNDING_SOURCE_VENUE})" in cost_basis_of(_current())


def test_a_cheaper_funding_rate_is_optimistic():
    record = _scored("c", taker_fee_bps=DEFAULT_TAKER_FEE_BPS, maker_fee_bps=DEFAULT_MAKER_FEE_BPS,
                     slippage_bps=DEFAULT_SLIPPAGE_BPS,
                     funding_bps_per_interval=DEFAULT_FUNDING_BPS_PER_INTERVAL / 2,
                     funding_source=FUNDING_SOURCE_VENUE)
    assert cost_basis_rank(record) == COST_BASIS_RANK_OPTIMISTIC


def test_a_dearer_funding_rate_is_conservative():
    """Same direction rule as every other axis: an error that runs against the candidate promotes."""
    record = _scored("c", taker_fee_bps=DEFAULT_TAKER_FEE_BPS, maker_fee_bps=DEFAULT_MAKER_FEE_BPS,
                     slippage_bps=DEFAULT_SLIPPAGE_BPS,
                     stop_slippage_bps=DEFAULT_STOP_SLIPPAGE_BPS,
                     funding_bps_per_interval=DEFAULT_FUNDING_BPS_PER_INTERVAL * 2,
                     funding_source=FUNDING_SOURCE_VENUE)
    assert cost_basis_rank(record) == COST_BASIS_RANK_CONSERVATIVE


def test_the_promotion_door_refuses_evidence_with_no_carry():
    with pytest.raises(ToolError) as excinfo:
        assert_promotable_cost_basis([
            _scored("cand_no_carry", taker_fee_bps=DEFAULT_TAKER_FEE_BPS,
                    maker_fee_bps=DEFAULT_MAKER_FEE_BPS, slippage_bps=DEFAULT_SLIPPAGE_BPS)
        ])
    assert excinfo.value.reason_code == "CANDIDATE_COST_BASIS_STALE"
    assert "cand_no_carry" in str(excinfo.value)


def test_a_maker_rate_below_the_current_one_is_optimistic():
    """The shape the eventual maker MEASUREMENT takes. `DEFAULT_MAKER_FEE_BPS` is published,
    not measured; if the real rate lands above it, every candidate scored at the published one
    becomes exactly this case and stops being promotable on its own evidence."""
    record = _scored("c", taker_fee_bps=DEFAULT_TAKER_FEE_BPS,
                     maker_fee_bps=DEFAULT_MAKER_FEE_BPS - 0.5, slippage_bps=DEFAULT_SLIPPAGE_BPS)
    assert cost_basis_rank(record) == COST_BASIS_RANK_OPTIMISTIC


def test_lower_slippage_is_optimistic_too():
    """Slippage is not re-derivable the way a fee rate is, so it can only be refused."""
    record = _scored("c", taker_fee_bps=DEFAULT_TAKER_FEE_BPS, maker_fee_bps=DEFAULT_MAKER_FEE_BPS,
                     slippage_bps=DEFAULT_SLIPPAGE_BPS - 1.0)
    assert cost_basis_rank(record) == COST_BASIS_RANK_OPTIMISTIC


def test_no_recorded_cost_model_is_its_own_tier():
    """Worse than stale: an unrecorded basis cannot even say which way it errs."""
    assert cost_basis_rank(_RECORD) == COST_BASIS_RANK_UNRECORDED


def test_the_promotion_door_refuses_optimistic_evidence():
    with pytest.raises(ToolError) as excinfo:
        assert_promotable_cost_basis([_scored("cand_cheap", taker_fee_bps=2.5, slippage_bps=3.0)])
    assert excinfo.value.reason_code == "CANDIDATE_COST_BASIS_STALE"
    assert "cand_cheap" in str(excinfo.value)
    assert "--allow-stale-cost-basis" in str(excinfo.value), "a refusal must name its escape"


def test_the_promotion_door_refuses_unrecorded_evidence():
    with pytest.raises(ToolError) as excinfo:
        assert_promotable_cost_basis([dict(_RECORD)])
    assert excinfo.value.reason_code == "CANDIDATE_COST_BASIS_STALE"


def test_the_promotion_door_admits_current_and_conservative_evidence():
    """The gate has to let the safe direction through, or it is just an off switch."""
    conservative = _scored("cand_pre_maker", taker_fee_bps=DEFAULT_TAKER_FEE_BPS,
                           slippage_bps=DEFAULT_SLIPPAGE_BPS,
                           stop_slippage_bps=DEFAULT_STOP_SLIPPAGE_BPS,
                           funding_bps_per_interval=DEFAULT_FUNDING_BPS_PER_INTERVAL,
                           funding_source=FUNDING_SOURCE_VENUE)
    assert_promotable_cost_basis([_current(), conservative])  # does not raise


def test_cheaper_evidence_ranks_below_dearer_evidence_whatever_its_score():
    """The gap the printed warning left open. `champion_score`, `edge_quality` and `expectancy`
    are all read off evidence scored under the old model, and the basis tier leads the sort so
    a cheap-venue row cannot top the list an operator promotes from."""
    cheap = {**_scored("cand_cheap", taker_fee_bps=2.5, slippage_bps=3.0), "champion_score": 0.99}
    cheap["backtest_evidence"] = {**cheap["backtest_evidence"], "expectancy": 9.9,
                                  "win_count": 40, "avg_win_R": 9.0, "avg_loss_R": 1.0}
    real = {**_current("cand_real"), "champion_score": 0.10}

    assert [c["candidate_id"] for c in rank_candidates([cheap, real])] == ["cand_real", "cand_cheap"]


# --- the stop axis (Thomas 2026-08-11: a cheaper stop rate reads as OPTIMISTIC) ---------------

def test_a_record_scored_before_the_stop_split_is_optimistic_now():
    """Thomas's direction, in one assertion: the safest treatment of the pre-12bps store is
    not a forced re-price but this rank — absent stop field -> judged on its own general
    slippage (3.0 < 12.0) -> OPTIMISTIC -> refused at the door -> re-mint at the current
    model. This is the taker 2.5 -> 5.0 precedent applied to the stop leg."""
    record = _scored("c", taker_fee_bps=DEFAULT_TAKER_FEE_BPS, maker_fee_bps=DEFAULT_MAKER_FEE_BPS,
                     slippage_bps=DEFAULT_SLIPPAGE_BPS,
                     funding_bps_per_interval=DEFAULT_FUNDING_BPS_PER_INTERVAL,
                     funding_source=FUNDING_SOURCE_VENUE)
    assert cost_basis_rank(record) == COST_BASIS_RANK_OPTIMISTIC


def test_a_dearer_stop_rate_is_conservative_and_the_string_names_it():
    record = _scored("c", taker_fee_bps=DEFAULT_TAKER_FEE_BPS, maker_fee_bps=DEFAULT_MAKER_FEE_BPS,
                     slippage_bps=DEFAULT_SLIPPAGE_BPS, stop_slippage_bps=23.5,
                     funding_bps_per_interval=DEFAULT_FUNDING_BPS_PER_INTERVAL,
                     funding_source=FUNDING_SOURCE_VENUE)
    assert cost_basis_rank(record) == COST_BASIS_RANK_CONSERVATIVE
    assert "+stop_23.5bps" in cost_basis_of(record)


def test_a_legacy_basis_string_is_byte_identical():
    """The maker rule again: a record scored before the split keeps the exact basis string it
    has always reported — the store stays legible as its vintages, and only stamped rows say
    what their stops paid."""
    record = _scored("c", taker_fee_bps=DEFAULT_TAKER_FEE_BPS, maker_fee_bps=DEFAULT_MAKER_FEE_BPS,
                     slippage_bps=DEFAULT_SLIPPAGE_BPS,
                     funding_bps_per_interval=DEFAULT_FUNDING_BPS_PER_INTERVAL,
                     funding_source=FUNDING_SOURCE_VENUE)
    assert "stop_" not in cost_basis_of(record)
