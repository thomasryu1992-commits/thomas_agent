"""The price-structure columns: swings, the breaks through them, three-bar gaps (evidence only).

``docs/history/2026-11-25-structure-columns.md`` carries the definitions and the three constants.
These tests pin what the columns DO: a swing is known only after it is confirmed, the value at a
bar never depends on later bars, a frame that starts later says the same thing or nothing, a hole
in the candles restarts the run, and nothing the factory makes can read any of them. All bars here
are synthetic."""

from __future__ import annotations

import functools
import math
import random
from datetime import datetime, timedelta, timezone

import pytest

from runtime.mvp_runtime.crypto import features, generator, proposer, template_space
from runtime.mvp_runtime.crypto.strategy import StrategySpec

K = features.STRUCTURE_SWING_BARS
LIFE = features.FVG_BAND_LIFE_BARS
COLUMNS = features.STRUCTURE_COLUMNS
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _candles(bars: list[tuple[float, float, float]]) -> list[dict]:
    """Hourly candles from (high, low, close) triples; the open is the previous close."""
    out, previous = [], bars[0][2]
    for i, (high, low, close) in enumerate(bars):
        start = T0 + timedelta(hours=i)
        out.append({"open_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "open": previous, "high": high, "low": low,
                    "close": close, "volume": 1.0,
                    "close_time": (start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")})
        previous = close
    return out


def _walk(seed: int, n: int, scale: float = 100.0, *, gaps: bool = False) -> list[dict]:
    """A random walk. With ``gaps`` a bar may open away from the previous close, far enough that the
    close lies outside the bar's range — the case where a frame's first true range is not the real one."""
    rng = random.Random(seed)
    bars, price = [], scale
    for _ in range(n):
        opened = price * math.exp(rng.gauss(0.0, 0.01)) if gaps else price
        price = opened * math.exp(rng.gauss(0.0, 0.01))
        bars.append((max(opened, price) * (1 + abs(rng.gauss(0.0, 0.003))),
                     min(opened, price) * (1 - abs(rng.gauss(0.0, 0.003))), price))
    return _candles(bars)


def _structure(candles: list[dict]) -> list[dict]:
    return [{col: row[col] for col in COLUMNS} for row in features.build_feature_rows({"candles": candles})]


def _flat(n: int, level: float = 100.0) -> list[tuple[float, float, float]]:
    """Quiet bars with no swing of their own inside: highs and lows drift up by a hair each bar."""
    return [(level + 1.0 + 0.001 * i, level - 1.0 + 0.001 * i, level + 0.001 * i) for i in range(n)]


# --- the definitions, on bars built by hand -------------------------------------------------

def test_a_swing_is_known_only_once_its_side_bars_have_closed():
    warm = _flat(20)
    peak = len(warm)                                   # index of the swing high
    bars = warm + [(105.0, 99.5, 100.5)] + [(101.5 - 0.1 * i, 99.0 - 0.1 * i, 100.0 - 0.1 * i) for i in range(6)]
    rows = _structure(_candles(bars))
    assert all(rows[t]["swing_high_distance_atr"] is None for t in range(peak + K))
    known = rows[peak + K]["swing_high_distance_atr"]
    assert known is not None and known < 0            # the close sits below the confirmed high


def test_a_tie_is_no_swing():
    warm = _flat(20)
    bars = warm + [(105.0, 99.5, 100.5), (105.0, 99.4, 100.4)] + [(101.0 - 0.1 * i, 99.0 - 0.1 * i, 100.0 - 0.1 * i)
                                                               for i in range(6)]
    assert all(row["swing_high_distance_atr"] is None for row in _structure(_candles(bars)))


def _zigzag() -> tuple[list[tuple[float, float, float]], dict[str, int]]:
    """The warm-up's last bar is a swing high of its own (it tops the two bars on each side). Then: a
    swing low, a bar that closes above that first high while becoming a swing high itself, a close
    above it, a third swing high and a higher swing low, a close above again, and at last a close
    below the latest swing low. Every bar's index of interest is returned."""
    bars = _flat(20)
    at: dict[str, int] = {}

    def add(high: float, low: float, close: float, name: str | None = None) -> None:
        if name:
            at[name] = len(bars)
        bars.append((high, low, close))

    add(100.6, 99.2, 99.6); add(100.2, 97.0, 98.0, "low1"); add(100.4, 98.2, 99.8); add(101.2, 99.4, 101.0)
    add(104.0, 100.6, 102.0, "high1"); add(102.6, 100.8, 101.2); add(102.4, 100.5, 101.0)
    add(105.5, 100.9, 105.0, "break1")                                  # closes above high1 (104.0)
    add(106.5, 104.6, 106.0); add(108.0, 105.5, 106.2, "high2"); add(106.8, 104.8, 105.2)
    add(106.0, 104.0, 104.4, "low2"); add(107.0, 104.5, 106.6); add(109.0, 106.2, 108.6, "break2")   # above high2 (108.0)
    add(109.4, 107.6, 108.0); add(108.4, 106.4, 107.0); add(107.4, 103.0, 103.5, "break3")           # below low2 (104.0)
    return bars, at


def test_the_first_break_only_sets_the_state_then_a_break_with_it_is_bos_and_against_it_choch():
    bars, at = _zigzag()
    rows = _structure(_candles(bars))
    assert rows[at["high1"] - 1]["structure_state"] is None            # both sides known, no break yet
    first = rows[at["high1"]]                                          # closes above the warm-up's swing high
    assert first["structure_break"] is None and first["structure_state"] == "UP"
    assert first["structure_break_atr"] is not None and first["structure_break_atr"] > 0
    for name in ("break1", "break2"):
        assert rows[at[name]]["structure_break"] == "BOS_UP" and rows[at[name]]["structure_state"] == "UP", name
    last = rows[at["break3"]]
    assert last["structure_break"] == "CHOCH_DOWN" and last["structure_state"] == "DOWN"
    assert last["structure_break_atr"] < 0
    quiet = rows[at["break2"] + 1]
    assert quiet["structure_break"] == "NONE" and quiet["structure_break_atr"] == 0.0


def test_a_level_is_broken_once_and_then_gone_until_the_next_swing():
    bars, at = _zigzag()
    rows = _structure(_candles(bars))
    assert rows[at["break1"]]["swing_high_distance_atr"] > 0      # the break bar itself: above the level
    assert rows[at["break1"] + 1]["swing_high_distance_atr"] is None
    assert rows[at["high2"] + K]["swing_high_distance_atr"] is not None


def test_a_three_bar_gap_is_sized_in_atr_and_touched_at_its_near_edge():
    bars = _flat(LIFE + 20)
    gap = len(bars) + 2
    bars += [(101.2, 99.2, 101.0), (104.0, 100.8, 103.8), (106.0, 103.0, 105.5)]      # low 103.0 > high 101.2
    bars += [(106.4, 103.9, 106.0), (106.2, 103.4, 104.0), (105.0, 102.9, 103.2)]     # only the last low reaches 103.0
    rows = _structure(_candles(bars))
    assert rows[gap]["fvg_size_atr"] > 0 and rows[gap - 1]["fvg_size_atr"] == 0.0
    assert rows[gap + 1]["fvg_touch"] == "NONE" and rows[gap + 2]["fvg_touch"] == "NONE"
    touch = rows[gap + 3]
    assert touch["fvg_touch"] == "UP_BAND" and touch["fvg_touch_age_bars"] == 3.0
    assert touch["fvg_touch_size_atr"] == pytest.approx(rows[gap]["fvg_size_atr"])
    assert rows[gap + 3]["fvg_size_atr"] == 0.0


def test_an_untouched_band_is_dropped_after_its_life():
    bars = _flat(LIFE + 20)
    gap = len(bars) + 2
    bars += [(101.2, 99.2, 101.0), (104.0, 100.8, 103.8), (106.0, 103.0, 105.5)]
    bars += [(107.0 + 0.01 * i, 103.5 + 0.01 * i, 106.0 + 0.01 * i) for i in range(LIFE)]     # stays above the band
    bars += [(106.0, 102.0, 102.5)]                                                    # reaches it too late
    rows = _structure(_candles(bars))
    assert rows[gap]["fvg_size_atr"] > 0
    assert rows[-1]["fvg_touch"] == "NONE"
    early = bars[:gap + LIFE] + [(106.0, 102.0, 102.5)]                                # one bar inside the life
    assert _structure(_candles(early))[-1]["fvg_touch"] == "UP_BAND"


# --- the two cut rules, on random bars -------------------------------------------------------

@pytest.mark.parametrize("seed", [1, 2, 3])
def test_the_value_at_a_bar_never_depends_on_later_bars(seed):
    candles = _walk(seed, 400, gaps=True)
    full = _structure(candles)
    for t in range(3, 400, 23):
        assert _structure(candles[:t + 1])[t] == full[t], t


@functools.lru_cache(maxsize=None)
def _walk_and_full(seed: int) -> tuple[tuple[dict, ...], tuple[dict, ...]]:
    candles = _walk(seed, 700, gaps=True)
    return tuple(candles), tuple(_structure(candles))


@pytest.mark.parametrize("window", [5, 8, 14, 15, 20, 120])
def test_a_frame_that_starts_later_says_the_same_or_nothing(window):
    """The short windows are where a late start can lie: the ATR's first value in a frame (bar 14),
    a state set while only one side has a confirmed swing, a band formed before the frame began."""
    compared = empty = 0
    for seed in (11, 12, 13):
        candles, full = _walk_and_full(seed)
        for t in range(300, 700, 5):
            last = _structure(list(candles[t - window + 1:t + 1]))[-1]
            for col in COLUMNS:
                if last[col] is None:
                    empty += 1
                    continue
                compared += 1
                expected = full[t][col]
                assert last[col] == (pytest.approx(expected) if isinstance(expected, float) else expected), (seed, t, col)
    if window >= 120:
        assert compared > 10 * empty        # the live cycle's 120 bars do carry these columns


def test_a_hole_in_the_candles_restarts_the_run():
    candles = _walk(21, 300)
    full = _structure(candles)
    broken = [dict(c) for c in candles]
    broken[150]["high"] = None
    highs = [c["high"] for c in broken]
    lows = [c["low"] for c in broken]
    closes = [c["close"] for c in broken]
    atr = [row["atr"] for row in features.build_feature_rows({"candles": candles})]
    out = features._structure_columns(highs, lows, closes, atr)
    assert all(out[col][150] is None for col in COLUMNS)
    tail = features._structure_columns(highs[151:], lows[151:], closes[151:], atr[151:])
    for col in COLUMNS:
        for offset in range(0, 149, 7):
            value = out[col][151 + offset]
            assert value == tail[col][offset] or (value is None and tail[col][offset] is None), (col, offset)
    assert any(out[col][151 + features.ATR_PERIOD] is None for col in COLUMNS)   # it warms up again
    assert full[149] == {col: out[col][149] for col in COLUMNS}                  # before the hole: untouched


def test_the_columns_are_the_same_at_any_price_scale():
    small, large = _structure(_walk(31, 300, scale=0.5)), _structure(_walk(31, 300, scale=50_000.0))
    for a, b in zip(small, large):
        for col in COLUMNS:
            assert a[col] == (pytest.approx(b[col], rel=1e-9, abs=1e-9) if isinstance(b[col], float) else b[col]), col


def test_the_labels_stay_inside_their_closed_vocabularies():
    rows = _structure(_walk(41, 800))
    assert {r["structure_state"] for r in rows} <= features.STRUCTURE_STATE_VALUES | {None}
    assert {r["structure_break"] for r in rows} <= features.STRUCTURE_BREAK_VALUES | {None}
    assert {r["fvg_touch"] for r in rows} <= features.FVG_TOUCH_VALUES | {None}
    breaks = {r["structure_break"] for r in rows}
    assert {"BOS_UP", "BOS_DOWN", "CHOCH_UP", "CHOCH_DOWN", "NONE"} <= breaks       # the walk exercises all five


# --- evidence only ----------------------------------------------------------------------------

def test_nothing_the_factory_makes_can_read_a_structure_column():
    """They ride on the row and nothing more: absent from both mintable vocabularies, so the validator
    every generated spec, fusion child and proposed trial passes through refuses one that names a
    structure column, as a condition's feature and as its ``value_from``. (An operator-imported spec
    is not validated; that is true of every evidence column and is not what this pins.)"""
    for column in COLUMNS:
        assert column not in template_space.NUMERIC_FEATURES, column
        assert column not in template_space.CATEGORICAL_FEATURES, column
    template = template_space.TEMPLATES[0]
    raw = generator.build_spec_dict(template, dict(template.base_params), strategy_id="strategy_test",
                                    generation_id="generation_test")
    for condition in (
        {"feature": "structure_break_atr", "comparison": ">", "value": 0.5},
        {"feature": "structure_break", "comparison": "==", "value": "BOS_UP"},
        {"feature": "close", "comparison": ">", "value_from": "swing_high_distance_atr"},
    ):
        spec = StrategySpec.from_dict({**raw, "entry_rules": {"operator": "AND", "conditions": [condition]}})
        verdict = template_space.validate_strategy(spec)
        assert not verdict["approved_for_backtest"] and "BLOCK_UNKNOWN_FEATURE" in verdict["block_reasons"], condition


def test_the_proposal_prompt_does_not_offer_a_structure_column():
    """The model proposer is told the vocabulary it may use. It is not told about these."""
    prompt = proposer.build_proposal_prompt(existing_families=[t.family for t in template_space.TEMPLATES])
    assert "rsi" in prompt                       # the prompt does list the vocabulary
    assert not [column for column in COLUMNS if column in prompt]


def test_every_row_carries_the_columns_and_their_previous_bar():
    rows = features.build_feature_rows({"candles": _walk(51, 80)})
    for column in COLUMNS:
        assert column in rows[-1] and f"{features.PREVIOUS_BAR_PREFIX}{column}" in rows[-1], column
    assert rows[-1][f"{features.PREVIOUS_BAR_PREFIX}fvg_size_atr"] == rows[-2]["fvg_size_atr"]
