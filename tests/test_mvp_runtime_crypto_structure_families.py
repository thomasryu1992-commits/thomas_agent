"""The four price-structure pairs (Thomas 2026-11-25): what they read, where they mint, how they meet.

`structure_follow_bos_*`, `structure_follow_choch_*`, `structure_fade_*` and `fvg_return_*` read three
of the nine structure columns (`features._structure_columns`). Pinned here, each by calling the thing:

- **where:** in the rotation at 1h, 4h and 1d and at no other timeframe, appended after every earlier
  family;
- **what:** exactly three structure columns are mintable and six stay evidence only;
- **zero is not an event:** both size columns are a real 0.0 on a quiet bar, so no draw of any of the
  eight may match a bar on which nothing happened, and none may match on an absent column;
- **the pairs:** long and short of one pair never share a row; follow and fade DO, by decision, and
  fade is the mirror of the two follow families together at the same draw;
- **the path:** each fires on rows built from candles and closes trades in the replay.

The definitions and the reasons live in `docs/history/2026-11-26-structure-families.md`.
"""

from __future__ import annotations

import math
import random
import re
from datetime import datetime, timedelta, timezone

import pytest

from runtime.mvp_runtime.crypto import factory, features, generator, proposer, template_space
from runtime.mvp_runtime.crypto.strategy import StrategySpec, evaluate_spec

FAMILIES = template_space.STRUCTURE_FAMILIES
PAIRS = (
    ("structure_follow_bos_long", "structure_follow_bos_short"),
    ("structure_follow_choch_long", "structure_follow_choch_short"),
    ("structure_fade_long", "structure_fade_short"),
    ("fvg_return_long", "fvg_return_short"),
)
THRESHOLD = {family: ("fvg_atr_min" if family.startswith("fvg_") else "break_atr_min") for family in FAMILIES}
MINTABLE = (*features.STRUCTURE_MINTABLE_NUMERIC_COLUMNS, *features.STRUCTURE_MINTABLE_LABELS)
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _template(family):
    return next(t for t in template_space.TEMPLATES if t.family == family)


def _spec(family, **overrides):
    template = _template(family)
    return StrategySpec.from_dict(generator.build_spec_dict(
        template, {**template.base_params, **overrides}, strategy_id="S999", generation_id="GEN-999",
    ))


def _draws(family):
    """The spec at the floor, the base and the ceiling of its one threshold."""
    space = _template(family).param_space[THRESHOLD[family]]
    return [_spec(family, **{THRESHOLD[family]: value})
            for value in (space.lo, _template(family).base_params[THRESHOLD[family]], space.hi)]


def _walk(seed: int, n: int) -> dict:
    """A random-walk snapshot whose highs and lows never tie, so swings form. Synthetic, not market data."""
    rng = random.Random(seed)
    candles, price = [], 100.0
    for i in range(n):
        opened = price
        price = opened * math.exp(rng.gauss(0.0, 0.01))
        start = T0 + timedelta(hours=i)
        candles.append({
            "open_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "close_time": (start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "open": opened, "close": price, "volume": 10.0,
            "high": max(opened, price) * (1 + abs(rng.gauss(0.0, 0.003))),
            "low": min(opened, price) * (1 - abs(rng.gauss(0.0, 0.003))),
        })
    return {"symbol": "ETHUSDT", "timeframe": "1h", "candles": candles}


@pytest.fixture(scope="module")
def rows():
    return features.build_feature_rows(_walk(20261126, 3000))


def _matches(spec, rows):
    return {i for i, row in enumerate(rows) if evaluate_spec(spec, row).matched}


# --- where they mint --------------------------------------------------------------------------

@pytest.mark.parametrize("timeframe", sorted(template_space.STRUCTURE_TIMEFRAMES))
def test_every_structure_family_is_in_the_rotation_at_the_three_timeframes_thomas_named(timeframe):
    assert template_space.STRUCTURE_TIMEFRAMES == {"1h", "4h", "1d"}
    minted = [t for t in factory.templates_for_timeframe(timeframe, symbol="ETHUSDT")]
    assert FAMILIES <= {t.family for t in minted}
    assert all(t.timeframe == timeframe for t in minted if t.family in FAMILIES)


def test_no_structure_family_is_minted_at_a_timeframe_nobody_decided():
    """15m is authorable and is in the factory's ladder; the decision named three timeframes."""
    fast = {t.family for t in factory.templates_for_timeframe("15m", symbol="ETHUSDT")}
    assert fast, "15m mints nothing at all, so this would pass for the wrong reason"
    assert not (fast & FAMILIES)


def test_the_eight_are_appended_so_every_earlier_family_keeps_its_place():
    families = [t.family for t in template_space.TEMPLATES]
    assert set(families[-8:]) == FAMILIES and len(FAMILIES) == 8
    assert not (set(families[:-8]) & FAMILIES)
    assert not (FAMILIES & template_space.RETIRED_FAMILIES)
    for long_name, short_name in PAIRS:
        assert _template(long_name).direction == "long" and _template(short_name).direction == "short"


def test_the_hold_is_capped_at_1d_like_every_other_trend_space_family():
    cap = template_space.judgeable_holding_bars("1d")
    daily = {t.family: t for t in factory.templates_for_timeframe("1d", symbol="ETHUSDT")}
    for family in FAMILIES:
        assert daily[family].param_space["max_holding_bars"].hi <= cap, family


# --- what they may read -----------------------------------------------------------------------

def test_exactly_three_structure_columns_are_mintable_and_they_are_the_ones_the_families_read():
    vocabulary = set(template_space.NUMERIC_FEATURES) | set(template_space.CATEGORICAL_FEATURES)
    assert set(MINTABLE) == {"structure_break", "structure_break_atr", "fvg_touch_size_atr"}
    assert vocabulary & set(features.STRUCTURE_COLUMNS) == set(MINTABLE)
    assert set(features.STRUCTURE_EVIDENCE_COLUMNS) == set(features.STRUCTURE_COLUMNS) - set(MINTABLE)
    read = set().union(*(template_space.template_features(_template(family)) for family in FAMILIES))
    assert read == set(MINTABLE)
    numeric, categorical = template_space.known_features("binance_futures")
    assert set(MINTABLE) <= numeric | set(categorical)                 # classified, so admitted on the venue
    assert template_space.CATEGORICAL_FEATURES["structure_break"] == features.STRUCTURE_BREAK_VALUES


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_every_draw_of_every_family_passes_the_validator(family):
    for spec in _draws(family):
        verdict = template_space.validate_strategy(spec)
        assert verdict["approved_for_backtest"], (family, verdict["block_reasons"])


def test_the_proposal_prompt_offers_the_mintable_three_and_not_the_other_six():
    prompt = proposer.build_proposal_prompt(existing_families=[])
    named = set(re.findall(r"[a-z0-9_]+", prompt))        # whole names: `fvg_touch` is inside `fvg_touch_size_atr`
    assert set(MINTABLE) <= named
    assert not (set(features.STRUCTURE_EVIDENCE_COLUMNS) & named)


# --- zero is not an event, and absence is not a match -----------------------------------------

QUIET = {"structure_break": "NONE", "structure_break_atr": 0.0, "fvg_touch_size_atr": 0.0}


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_no_draw_matches_a_bar_on_which_nothing_happened(family):
    """Both size columns are 0.0 on a quiet bar. A threshold at zero would match all of them — the
    edge the `liquidation_spike_ratio` note describes — so the floor is strictly above zero and every
    draw is silent on the quiet row. Each condition alone is silent too: the ablation lattice may keep
    a proper subset of a spec's conditions, and a subset must not become every bar."""
    space = _template(family).param_space[THRESHOLD[family]]
    assert space.lo > 0.0
    for spec in _draws(family):
        assert evaluate_spec(spec, QUIET).matched is False
        results = evaluate_spec(spec, QUIET).condition_results
        assert all(result is False for result in results), (family, results)


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_a_family_is_silent_when_a_column_it_reads_is_absent_or_empty(family):
    event = {
        "structure_follow_bos_long": {"structure_break": "BOS_UP", "structure_break_atr": 0.8},
        "structure_follow_bos_short": {"structure_break": "BOS_DOWN", "structure_break_atr": -0.8},
        "structure_follow_choch_long": {"structure_break": "CHOCH_UP", "structure_break_atr": 0.8},
        "structure_follow_choch_short": {"structure_break": "CHOCH_DOWN", "structure_break_atr": -0.8},
        "structure_fade_long": {"structure_break": "BOS_DOWN", "structure_break_atr": -0.8},
        "structure_fade_short": {"structure_break": "BOS_UP", "structure_break_atr": 0.8},
        "fvg_return_long": {"fvg_touch_size_atr": 0.8},
        "fvg_return_short": {"fvg_touch_size_atr": -0.8},
    }[family]
    spec = _spec(family)
    assert evaluate_spec(spec, event).matched is True, "the condition never fires at all"
    for column in template_space.template_features(_template(family)):
        assert evaluate_spec(spec, {k: v for k, v in event.items() if k != column}).matched is False, column
        assert evaluate_spec(spec, {**event, column: None}).matched is False, column


# --- the pairs --------------------------------------------------------------------------------

BREAK_ROWS = [
    {"structure_break": label, "structure_break_atr": size, "fvg_touch_size_atr": gap}
    for label in (*sorted(features.STRUCTURE_BREAK_VALUES), None)
    for size in (-2.0, -0.5, -0.05, -0.01, 0.0, 0.01, 0.05, 0.5, 2.0, None)
    for gap in (-2.0, -0.05, 0.0, 0.05, 2.0, None)
]


@pytest.mark.parametrize("long_name,short_name", PAIRS)
def test_the_long_and_the_short_of_one_pair_never_share_a_row(long_name, short_name):
    """Across the whole threshold range, with the two legs drawn independently — the search mutates
    each family on its own, so disjointness must not lean on the legs sharing a draw."""
    for long_spec in _draws(long_name):
        for short_spec in _draws(short_name):
            for row in BREAK_ROWS:
                both = evaluate_spec(long_spec, row).matched and evaluate_spec(short_spec, row).matched
                assert not both, (long_name, short_name, row)


def test_follow_and_fade_take_opposite_sides_of_the_same_break_by_decision():
    """Thomas 2026-11-26: both readings of one event are minted and they overlap. On the bar the
    follow family buys, the fade family sells. If a later edit made them disjoint, the two would be
    judging different events and the comparison the decision asked for would be gone."""
    up = {"structure_break": "BOS_UP", "structure_break_atr": 0.8}
    assert evaluate_spec(_spec("structure_follow_bos_long"), up).matched is True
    assert evaluate_spec(_spec("structure_fade_short"), up).matched is True
    assert evaluate_spec(_spec("structure_follow_choch_long"), up).matched is False
    assert evaluate_spec(_spec("structure_fade_long"), up).matched is False
    turn = {"structure_break": "CHOCH_DOWN", "structure_break_atr": -0.8}
    assert evaluate_spec(_spec("structure_follow_choch_short"), turn).matched is True
    assert evaluate_spec(_spec("structure_fade_long"), turn).matched is True
    assert evaluate_spec(_spec("structure_follow_bos_short"), turn).matched is False


def test_the_three_break_pairs_share_one_threshold_space_and_base():
    """So that at one draw a difference between follow and fade is not a difference in threshold."""
    spaces = {(_template(f).param_space["break_atr_min"], _template(f).base_params["break_atr_min"])
              for f in FAMILIES if THRESHOLD[f] == "break_atr_min"}
    assert len(spaces) == 1


@pytest.mark.parametrize("value", [0.05, 0.3, 1.0])
def test_fade_is_the_mirror_of_the_two_follow_families_plus_a_frames_first_break(rows, value):
    """On rows built from candles: every bar a follow family takes, fade takes the other way; and what
    fade takes beyond them is only a break that has a size and no label yet (the first of a frame)."""
    for follow_side, fade_side in (("long", "short"), ("short", "long")):
        follow = (_matches(_spec(f"structure_follow_bos_{follow_side}", break_atr_min=value), rows)
                  | _matches(_spec(f"structure_follow_choch_{follow_side}", break_atr_min=value), rows))
        fade = _matches(_spec(f"structure_fade_{fade_side}", break_atr_min=value), rows)
        assert follow and follow <= fade
        assert all(rows[i]["structure_break"] is None for i in fade - follow)
    unlabelled = [i for i, row in enumerate(rows)
                  if row["structure_break"] is None and row["structure_break_atr"] not in (None, 0.0)]
    assert len(unlabelled) <= 1                              # one unbroken run of candles, one such bar


def test_only_the_fade_pair_mints_from_the_fade_exit_space():
    fade_hold = template_space._FADE_EXIT_PARAMS["max_holding_bars"]
    on_fade = {f for f in FAMILIES if _template(f).param_space["max_holding_bars"] is fade_hold}
    assert on_fade == {"structure_fade_long", "structure_fade_short"}


# --- the path, end to end ---------------------------------------------------------------------

@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_every_family_fires_on_rows_built_from_candles_and_closes_trades_in_the_replay(family, rows):
    spec = _spec(family)
    fired = _matches(spec, rows)
    assert fired, f"{family} never matched a row of a 3000-bar walk"
    assert len(fired) < len(rows) / 4, f"{family} matched {len(fired)} of {len(rows)} bars: not an event"
    evidence = factory.backtest_spec(spec, _walk(20261126, 3000))
    assert evidence["closed_count"] > 0, family


def test_two_follow_parents_of_different_breaks_fuse_into_a_child_that_can_never_enter(rows):
    """A BOS parent and a CHoCH parent name different values of one label, so their union is
    unsatisfiable. It parses and validates; `mint_fusions` refuses a child that closed no trades, which
    is the existing net for exactly this. Pinned so the cost is known: such a pair spends a draw."""
    child = factory.fuse_specs(
        _spec("structure_follow_bos_long"), _spec("structure_follow_choch_long"),
        strategy_id="S998", generation_id="GEN-998",
    )
    assert template_space.validate_strategy(child)["approved_for_backtest"]
    assert not _matches(child, rows)
