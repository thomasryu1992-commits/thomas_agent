"""The 5-3 OBSERVATION entry bar and family cap (Thomas 2026-08-11).

The bar selects for MEASURABILITY, not winners: a slot buys forward evidence, so an
entrant must be honestly scored (CURRENT basis, FULL window), estimable (>=50 closes —
where the store's own inflation table converges), positive at today's rates, and
disconfirmable (holdout thin or better; the vintage shapes that cannot state their own
dispersion go back through the factory, not into a slot). Restatements of occupying
lineages are not entrants — the grandfathering that keeps the re-arm path usable, since
all five pre-bar occupants are 350d/undispersed vintage by measurement."""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import pool
from runtime.mvp_runtime.crypto.cost import (
    DEFAULT_FUNDING_BPS_PER_INTERVAL,
    DEFAULT_MAKER_FEE_BPS,
    DEFAULT_SLIPPAGE_BPS,
    DEFAULT_TAKER_FEE_BPS,
    FUNDING_SOURCE_VENUE,
)
from runtime.mvp_runtime.errors import ToolError

_MISSING = object()


def _entrant(
    cid,
    *,
    family="trend_pullback",
    timeframe="1d",
    closed=60,
    net_r=12.0,
    holdout_block=_MISSING,
    stored_holdout="INSUFFICIENT",
    bars_replayed=_MISSING,
):
    """A row that clears every term of the bar unless a knob says otherwise.

    The default holdout block is THIN (10 closes) — the exact shape observation exists
    to feed. ``holdout_block=None`` removes the block so the stored label decides."""
    evidence = {
        "robustness": {"verdict": "PROVISIONAL", "holdout_status": stored_holdout},
        "closed_count": closed,
        "win_count": int(closed * 0.45),
        "avg_win_R": 2.0,
        "avg_loss_R": 1.0,
        "expectancy": round(net_r / closed, 8) if closed else 0.0,
        "cost_summary": {
            "cost_model": {
                "taker_fee_bps": DEFAULT_TAKER_FEE_BPS,
                "maker_fee_bps": DEFAULT_MAKER_FEE_BPS,
                "slippage_bps": DEFAULT_SLIPPAGE_BPS,
                "funding_bps_per_interval": DEFAULT_FUNDING_BPS_PER_INTERVAL,
                "funding_source": FUNDING_SOURCE_VENUE,
            },
            "total_net_r": net_r,
            "total_fee_cost_r": 2.0,
            "total_maker_fee_cost_r": 0.5,
        },
        "bars_replayed": (pool.expected_replayed_bars(timeframe)
                          if bars_replayed is _MISSING else bars_replayed),
    }
    if holdout_block is _MISSING:
        evidence["holdout"] = {"closed_count": 10}
    elif holdout_block is not None:
        evidence["holdout"] = holdout_block
    return {
        "candidate_id": cid,
        "strategy_id": "S001",
        "generation_id": "GEN-001",
        "strategy_rule_hash": f"hash-{cid}",
        "strategy_spec": {
            "strategy_family": family, "symbol_scope": ["BTCUSDT"], "timeframe": timeframe,
        },
        "champion_score": 0.8,
        "backtest_evidence": evidence,
    }


def _occupying(cid, family="trend_pullback"):
    return {"candidate_id": cid, "status": "PAPER_ACTIVE",
            "strategy_spec": {"strategy_family": family}}


# --- the bar --------------------------------------------------------------------------

def test_a_clean_thin_entrant_clears_the_bar():
    pool.assert_observation_entry_bar([_entrant("cand_ok")])


def test_a_confirmed_entrant_is_beyond_the_bar():
    row = _entrant("cand_confirmed", holdout_block=None, stored_holdout="CONFIRMED")
    pool.assert_observation_entry_bar([row])


def test_a_vintage_holdout_is_a_remint_not_a_slot():
    row = _entrant("cand_vintage", holdout_block={"closed_count": 30})
    with pytest.raises(ToolError) as exc:
        pool.assert_observation_entry_bar([row])
    assert exc.value.reason_code == "CANDIDATE_BELOW_OBSERVATION_ENTRY_BAR"
    assert "vintage" in str(exc.value)


def test_contradicted_and_blockless_rows_are_refused():
    contradicted = _entrant("cand_contra", holdout_block=None, stored_holdout="CONTRADICTED")
    unconfirmed = _entrant("cand_uncf", holdout_block=None, stored_holdout="UNCONFIRMED")
    for row in (contradicted, unconfirmed):
        with pytest.raises(ToolError):
            pool.assert_observation_entry_bar([row])


def test_a_refusal_names_every_failing_term_at_once():
    """A bar that reports one term per run is a bar an operator meets four times."""
    row = _entrant("cand_multi", closed=30, net_r=-3.0,
                   bars_replayed=pool.expected_replayed_bars("1d") // 2,
                   holdout_block={"closed_count": 30})
    with pytest.raises(ToolError) as exc:
        pool.assert_observation_entry_bar([row])
    message = str(exc.value)
    assert "depth=" in message
    assert "closed=30<" in message
    assert "expectancy_at_current=" in message
    assert "vintage" in message


def test_a_restated_occupant_is_not_an_entrant():
    """The grandfathering that keeps the re-arm path usable: all five pre-bar occupants
    are 350d/undispersed vintage, and restating them is not an entry."""
    row = _entrant("cand_incumbent", bars_replayed=pool.expected_replayed_bars("1d") // 2,
                   holdout_block={"closed_count": 30})
    pool.assert_observation_entry_bar(
        [row], occupying_candidate_ids=frozenset({"cand_incumbent"}),
    )


# --- the family cap -------------------------------------------------------------------

def test_a_third_family_member_is_refused():
    with pytest.raises(ToolError) as exc:
        pool.assert_family_cap(
            [_entrant("cand_third")],
            occupying_entries=[_occupying("cand_a"), _occupying("cand_b")],
        )
    assert exc.value.reason_code == "CANDIDATE_FAMILY_CAP_EXCEEDED"


def test_another_family_passes_beside_a_full_one():
    pool.assert_family_cap(
        [_entrant("cand_other", family="xs_momentum_short")],
        occupying_entries=[_occupying("cand_a"), _occupying("cand_b")],
    )


def test_two_entrants_of_one_family_fill_an_empty_pool_and_three_do_not():
    a, b, c = (_entrant(f"cand_{n}") for n in "abc")
    pool.assert_family_cap([a, b], occupying_entries=[])
    with pytest.raises(ToolError):
        pool.assert_family_cap([a, b, c], occupying_entries=[])


def test_a_restated_member_counts_once_as_base_never_as_entrant():
    """Restating an occupant beside one entrant of its family is 1 base + 1 entrant = 2,
    not 2 + 1 — double-counting the restatement would refuse a legal batch."""
    restated = _entrant("cand_a")
    pool.assert_family_cap(
        [restated, _entrant("cand_new")],
        occupying_entries=[_occupying("cand_a")],
    )


def test_an_overfull_incumbent_family_blocks_nothing_it_is_not_adding_to():
    """The door must never refuse the promotion that is moving the pool AWAY from a
    concentration."""
    pool.assert_family_cap(
        [_entrant("cand_other", family="xs_momentum_short")],
        occupying_entries=[_occupying(f"cand_{n}") for n in "abc"],
    )


# --- an underpowered tail is admitted; a contradicted one is not (Thomas 2026-08-29) --------

def _tail(expectancy, closed=95, stdev=1.2):
    """A modern holdout block: enough closes to be judged, with a spread and period data."""
    per = [round(expectancy * closed / 10, 8)] * 10
    return {
        "closed_count": closed, "expectancy": expectancy,
        "total_R": round(expectancy * closed, 8), "stdev_r": stdev,
        "period_r": per, "period_trades": [closed // 10] * 10,
    }


def test_a_tail_that_leans_with_the_edge_but_cannot_resolve_it_is_admitted():
    """The bar's own argument for admitting a THIN tail, applied to the case that carries
    MORE evidence rather than less.

    A slot's product is forward evidence, so the tail too small to judge is let in. A tail
    at +0.10R over 95 trades against a 1.2 spread cannot clear its interval either — it
    needs roughly 553 trades to — but it is not silent: it leans the way the edge does.
    Refusing it while admitting a 10-trade block was the inversion this fixes."""
    from runtime.mvp_runtime.crypto.robustness import HOLDOUT_UNDERPOWERED, holdout_status

    block = _tail(0.10)
    assert holdout_status(block) == HOLDOUT_UNDERPOWERED
    assert pool._observation_holdout_term(_entrant("c1", holdout_block=block)) is None
    pool.assert_observation_entry_bar([_entrant("c1", holdout_block=block)])


def test_a_tail_that_measured_against_the_edge_is_still_refused():
    """The half that must not move with it: UNDERPOWERED buys entry for "unresolved", never
    for "disconfirmed". Same sample size, same spread — only the sign differs."""
    from runtime.mvp_runtime.crypto.robustness import HOLDOUT_CONTRADICTED, holdout_status

    block = _tail(-0.10)
    assert holdout_status(block) == HOLDOUT_CONTRADICTED
    with pytest.raises(ToolError) as exc:
        pool.assert_observation_entry_bar([_entrant("c2", holdout_block=block)])
    assert exc.value.reason_code == "CANDIDATE_BELOW_OBSERVATION_ENTRY_BAR"
    assert "CONTRADICTED" in exc.value.reason


def test_admitting_an_underpowered_tail_does_not_loosen_the_other_three_terms():
    """The bar has four terms and only the holdout one moved. A row whose tail is admitted
    still has to be FULL depth, clear `OBSERVATION_MIN_BACKTEST_CLOSED`, and be positive at
    CURRENT costs — so this cannot become a door for rows that fail elsewhere."""
    block = _tail(0.10)
    thin_sample = _entrant("c3", holdout_block=block,
                           closed=pool.OBSERVATION_MIN_BACKTEST_CLOSED - 1, net_r=8.0)
    with pytest.raises(ToolError):
        pool.assert_observation_entry_bar([thin_sample])

    losing = _entrant("c4", holdout_block=block, net_r=-5.0)
    with pytest.raises(ToolError):
        pool.assert_observation_entry_bar([losing])
