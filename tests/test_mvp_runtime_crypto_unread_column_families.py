"""The three pairs added 2026-08-08 to reach columns the rotation never conditioned on.

`htf_reversal_*` reads `htf_rsi`, `taker_flow_fade_*` reads `taker_flow_imbalance` — both on
the row since their legs were wired and read by no minted family — and `funding_momentum_*`
reads `funding_zscore` at the sign the library holds no family at.

What is asserted here is the pair of properties that make a widening safe rather than merely
wide. **The gate**: a family whose column cannot be supplied where it is minted is permanently
no-entry, so each pair must inherit the existing gate for its feed rather than acquire one of
its own. **The absence**: a missing column must leave the condition indeterminate, never match
on a fabricated value — the `liquidation_spike_ratio` hazard this repo carries once and must
not carry twice.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import factory
from runtime.mvp_runtime.crypto.strategy import StrategySpec, evaluate_spec

NEW_FAMILIES = frozenset({
    "htf_reversal_long", "htf_reversal_short",
    "funding_momentum_long", "funding_momentum_short",
    "taker_flow_fade_long", "taker_flow_fade_short",
})

# The column each pair exists to reach, and the one whose absence must silence it.
REACHED_COLUMN = {
    "htf_reversal_long": "htf_rsi",
    "htf_reversal_short": "htf_rsi",
    "funding_momentum_long": "funding_zscore",
    "funding_momentum_short": "funding_zscore",
    "taker_flow_fade_long": "taker_flow_imbalance",
    "taker_flow_fade_short": "taker_flow_imbalance",
}


def _template(family):
    return next(t for t in factory.TEMPLATES if t.family == family)


def _spec(family, **overrides):
    template = _template(family)
    params = {**template.base_params, **overrides}
    return StrategySpec.from_dict(factory.build_spec_dict(
        template, params, strategy_id="S999", generation_id="GEN-999",
    ))


def _row(**columns):
    """A feature row carrying only what a condition names. Every other column is absent,
    which is the state the evaluator must read as indeterminate rather than as false."""
    return dict(columns)


# --- the families exist and are mintable --------------------------------------

def test_every_new_family_is_in_the_rotation_at_1h():
    families = {t.family for t in factory.templates_for_timeframe("1h", symbol="BTCUSDT")}
    assert NEW_FAMILIES <= families


@pytest.mark.parametrize("family", sorted(NEW_FAMILIES))
def test_every_new_family_reads_only_mintable_classified_columns(family):
    """The venue gate is what stands between a family and a column the venue cannot serve.
    It works by containment, so a template whose features are not all classified would be
    dropped everywhere and read as a broken family rather than as an unclassified column."""
    numeric, categorical = factory.known_features(factory.market_data.BINANCE_FUTURES)
    mintable = numeric | frozenset(categorical)
    assert factory.template_features(_template(family)) <= mintable
    assert REACHED_COLUMN[family] in factory.NUMERIC_FEATURES


@pytest.mark.parametrize("family", sorted(NEW_FAMILIES))
def test_every_new_family_passes_its_own_validator(family):
    assert factory.validate_strategy(_spec(family))["approved_for_backtest"] is True


# --- each pair inherits the gate its feed already had -------------------------

def test_htf_reversal_inherits_the_higher_timeframe_gate():
    """1d has no collected step above it. Minting there would produce a spec whose filter is
    indeterminate on every bar — no-entry forever, which is noise wearing diversity's coat."""
    assert {"htf_reversal_long", "htf_reversal_short"} <= factory.HTF_FAMILIES
    families_1d = {t.family for t in factory.templates_for_timeframe("1d", symbol="BTCUSDT")}
    assert not ({"htf_reversal_long", "htf_reversal_short"} & families_1d)


def test_funding_momentum_inherits_the_funding_feed_gate():
    """The funding fetch does not reach the 1d replay window (`_funding_feed_reaches`), so a
    funding family minted at 1d is scored over a window its own feed cannot answer."""
    assert {"funding_momentum_long", "funding_momentum_short"} <= factory.FUNDING_FAMILIES
    families_1d = {t.family for t in factory.templates_for_timeframe("1d", symbol="BTCUSDT")}
    assert not ({"funding_momentum_long", "funding_momentum_short"} & families_1d)
    assert {"funding_momentum_long", "funding_momentum_short"} <= {
        t.family for t in factory.templates_for_timeframe("1h", symbol="BTCUSDT")
    }


def test_taker_flow_fade_needs_no_ladder_gate():
    """Its legs ride the same klines call as the OHLCV at every rung, so unlike the two pairs
    above it survives at the top of the ladder — asserted so a future gate cannot be added to
    it silently."""
    families_1d = {t.family for t in factory.templates_for_timeframe("1d", symbol="BTCUSDT")}
    assert {"taker_flow_fade_long", "taker_flow_fade_short"} <= families_1d


# --- absence is indeterminate, never a fabricated match -----------------------

@pytest.mark.parametrize("family", sorted(NEW_FAMILIES))
def test_a_new_family_is_silent_when_its_column_is_absent(family):
    """The property that makes minting these safe wherever the gates let them through: with
    the reached column missing the spec does not trade, rather than trading on a default."""
    supplying = {
        "htf_reversal_long": _row(htf_rsi=10.0, rsi=90.0),
        "htf_reversal_short": _row(htf_rsi=90.0, rsi=10.0),
        "funding_momentum_long": _row(funding_zscore=3.0, close=110.0, ma20=100.0),
        "funding_momentum_short": _row(funding_zscore=-3.0, close=90.0, ma20=100.0),
        "taker_flow_fade_long": _row(taker_flow_imbalance=-0.9, rsi=10.0),
        "taker_flow_fade_short": _row(taker_flow_imbalance=0.9, rsi=90.0),
    }[family]
    spec = _spec(family)
    assert evaluate_spec(spec, supplying).matched is True, "the condition never fires at all"

    without = {k: v for k, v in supplying.items() if k != REACHED_COLUMN[family]}
    assert evaluate_spec(spec, without).matched is False, "matched on an absent column"
    none_valued = {**supplying, REACHED_COLUMN[family]: None}
    assert evaluate_spec(spec, none_valued).matched is False, "matched on a None column"


# --- the shapes themselves ----------------------------------------------------

@pytest.mark.parametrize("htf_rsi_edge", [10.0, 15.0, 20.0, 25.0, 30.0])
@pytest.mark.parametrize("rsi_edge", [2.0, 8.0, 14.0, 20.0])
def test_htf_reversal_legs_are_disjoint_at_every_draw(htf_rsi_edge, rsi_edge):
    """Both legs are mirrored around 50 through one edge each, so no bar can satisfy the long
    and the short at the same time however the search moves the bounds — the `xs_rank_edge`
    property, which is what keeps a mirrored pair from being one family counted twice."""
    overrides = {"htf_rsi_edge": htf_rsi_edge, "rsi_edge": rsi_edge}
    long_spec = _spec("htf_reversal_long", **overrides)
    short_spec = _spec("htf_reversal_short", **overrides)
    for htf_rsi in (0.0, 20.0, 35.0, 50.0, 65.0, 80.0, 100.0):
        for rsi in (0.0, 20.0, 35.0, 50.0, 65.0, 80.0, 100.0):
            row = _row(htf_rsi=htf_rsi, rsi=rsi)
            both = (evaluate_spec(long_spec, row).matched
                    and evaluate_spec(short_spec, row).matched)
            assert not both, f"both legs fired at htf_rsi={htf_rsi} rsi={rsi}"


def test_funding_momentum_takes_the_opposite_side_of_funding_fade():
    """The reason the pair is worth a rotation slot: on the bar the fade family buys, the
    momentum family sells. If a future edit made them agree, one of them is redundant."""
    crowded_shorts = _row(funding_zscore=-3.0, rsi=20.0, close=90.0, ma20=100.0)
    assert evaluate_spec(_spec("funding_fade_long"), crowded_shorts).matched is True
    assert evaluate_spec(_spec("funding_momentum_short"), crowded_shorts).matched is True
    assert evaluate_spec(_spec("funding_momentum_long"), crowded_shorts).matched is False


def test_taker_flow_fade_takes_the_opposite_side_of_taker_absorption():
    """Absorption buys heavy BUY aggression into a washed-out RSI; the fade buys heavy SELL
    aggression into the same RSI. Opposite premises on one variable, which is what makes
    minting both informative rather than duplicative."""
    capitulation = _row(taker_flow_imbalance=-0.9, taker_flow_zscore=-3.0, rsi=20.0)
    assert evaluate_spec(_spec("taker_flow_fade_long"), capitulation).matched is True
    assert evaluate_spec(_spec("taker_absorption_long"), capitulation).matched is False
