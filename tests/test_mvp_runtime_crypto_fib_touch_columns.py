"""The retracement columns: a bar coming back to 0.382, 0.5 or 0.618 of the structure leg (evidence only).

Thomas 2026-11-26: "예, 구조 구간에 꼴 가로". ``docs/history/2026-11-26-fib-touch-columns.md`` carries the
definition. These tests pin what the two columns DO, each by building bars and reading the row: where the
leg starts and ends, which bar carries a level and which does not, that a frame which starts later says the
same thing or nothing, and that nothing the factory makes can read them. All bars are synthetic. No level in
the hand-built bars sits exactly on a ratio: the touch compares a quotient, and a low placed on a level
could land a hair either side of it."""

from __future__ import annotations

import functools
import math
import random
import re
from datetime import datetime, timedelta, timezone

import pytest

from runtime.mvp_runtime.crypto import features, generator, proposer, template_space
from runtime.mvp_runtime.crypto.strategy import StrategySpec

COLUMNS = features.FIB_COLUMNS
LEVELS = features.FIB_RETRACE_LEVELS
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

Bar = tuple[float, float, float, float]            # open, high, low, close


def _candles(bars: list[Bar]) -> list[dict]:
    out = []
    for i, (opened, high, low, close) in enumerate(bars):
        start = T0 + timedelta(hours=i)
        out.append({"open_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "open": opened, "high": high, "low": low,
                    "close": close, "volume": 1.0,
                    "close_time": (start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")})
    return out


def _rows(bars: list[Bar]) -> list[dict]:
    return features.build_feature_rows({"candles": _candles(bars)})


def _mirror(bars: list[Bar]) -> list[Bar]:
    """The same bars upside down around 100: an up leg becomes a down leg."""
    return [(200.0 - opened, 200.0 - low, 200.0 - high, 200.0 - close) for opened, high, low, close in bars]


def _scene(tail: tuple[Bar, ...] = (), *, tie: bool = False) -> tuple[list[Bar], dict[str, int]]:
    """The state is first known as UP, TURNS down at ``down`` and turns up again at ``up``. The up leg's
    origin is 90.0, the lowest low between the two turns, and its end is the turn bar's high, 110.0: a leg
    of 20, so a low of 102.36 is the 0.382 level, 100.0 the 0.5 and 97.64 the 0.618.

    With ``tie`` the 90.0 low comes twice, with a bar between them whose high (111.0) tops the turn bar's.
    The origin is the LATER of the two, so that high is before the leg and the end stays 110.0."""
    bars: list[Bar] = []
    at: dict[str, int] = {}

    def add(opened: float, high: float, low: float, close: float, name: str | None = None) -> None:
        if name:
            at[name] = len(bars)
        bars.append((opened, high, low, close))

    for i in range(16):                                    # quiet, creeping up: no swing in here
        add(100.0 + 0.001 * i, 101.0 + 0.001 * i, 99.0 + 0.001 * i, 100.0 + 0.001 * i)
    add(100.0, 100.4, 97.0, 99.0)                          # a swing low
    add(99.0, 100.6, 98.6, 100.2); add(100.2, 101.4, 99.6, 101.0)
    add(101.0, 104.0, 100.8, 102.0)                        # a swing high at 104.0
    add(102.0, 102.6, 100.6, 101.2); add(101.2, 102.4, 100.5, 101.0)
    add(101.0, 105.5, 100.9, 105.0, "first")               # closes above 104.0 while the state is already UP
    add(105.0, 106.5, 104.6, 106.0); add(106.0, 108.0, 105.5, 106.2)     # a swing high at 108.0
    add(106.2, 106.8, 104.8, 105.2); add(105.2, 106.0, 104.0, 104.4)     # a swing low at 104.0
    add(104.4, 105.0, 103.5, 104.6); add(104.6, 105.2, 103.8, 104.9)
    add(104.9, 105.0, 96.0, 97.0, "down")                  # closes below 104.0: the state turns DOWN
    add(97.0, 98.0, 92.0, 93.0)
    add(93.0, 94.0, 90.0, 91.0, "origin")                  # the lowest low of the down span
    if tie:
        add(91.0, 111.0, 90.5, 92.0)                       # a spike that closes back down: no break
        add(92.0, 93.0, 90.0, 91.5, "origin")              # the same low again, later
    add(91.0, 95.0, 90.5, 94.5); add(94.5, 99.0, 94.0, 98.5)             # a swing high at 99.0
    add(98.5, 98.8, 96.0, 96.5); add(96.5, 97.5, 95.5, 97.0)
    add(97.0, 110.0, 96.8, 109.0, "up")                    # closes above 99.0: the state turns UP
    for bar in tail:
        add(*bar)
    return bars, at


def _touches(rows: list[dict], start: int) -> list[float]:
    return [row["fib_touch_ratio"] for row in rows[start:]]


# --- the leg and the bar that carries a level -------------------------------------------------

def test_the_scene_is_what_its_docstring_says():
    bars, at = _scene()
    rows = _rows(bars)
    assert rows[at["first"]]["structure_break"] == "BOS_UP"          # the state was known before this bar
    assert rows[at["down"]]["structure_break"] == "CHOCH_DOWN" and rows[at["up"]]["structure_break"] == "CHOCH_UP"
    turns = [t for t, row in enumerate(rows) if str(row["structure_break"]).startswith("CHOCH")]
    assert turns == [at["down"], at["up"]]                           # exactly two turns


def test_the_leg_runs_from_the_low_between_the_two_turns_to_the_turn_bars_high():
    bars, at = _scene()
    rows = _rows(bars)
    up = rows[at["up"]]
    assert up["structure_leg_size_atr"] == pytest.approx((110.0 - 90.0) / up["atr"])
    assert up["fib_touch_ratio"] == 0.0


def test_nothing_is_said_before_the_state_has_turned_twice():
    """One turn names where the next leg's origin is searched from. Until there are two, there is no leg."""
    bars, at = _scene()
    rows = _rows(bars)
    for t in range(at["up"]):
        assert rows[t]["fib_touch_ratio"] is None and rows[t]["structure_leg_size_atr"] is None, t
    assert rows[at["down"]]["structure_state"] == "DOWN"             # the state itself was known all along


def test_the_turn_bar_carries_no_level_though_its_own_low_is_deep():
    bars, at = _scene()
    turn = bars[at["up"]]
    assert (turn[1] - turn[2]) / (turn[1] - 90.0) > 0.618            # its range spans more than the deepest level
    assert _rows(bars)[at["up"]]["fib_touch_ratio"] == 0.0


def test_a_bar_carries_the_level_it_reaches_for_the_first_time():
    tail: tuple[Bar, ...] = (
        (109.0, 109.5, 104.0, 108.0),       # depth 0.30: nothing yet
        (108.0, 108.5, 101.0, 103.0),       # depth 0.45: past 0.382
        (103.0, 104.0, 100.5, 102.0),       # depth 0.475: deeper, no new level
        (102.0, 103.0, 99.5, 101.0),        # depth 0.525: past 0.5
        (101.0, 102.0, 99.8, 101.5),        # shallower than before: nothing
        (101.5, 102.0, 97.0, 98.0),         # depth 0.65: past 0.618
        (98.0, 99.0, 95.0, 96.0),           # deeper still: every level is spent
    )
    bars, at = _scene(tail)
    assert _touches(_rows(bars), at["up"] + 1) == [0.0, 0.382, 0.0, 0.5, 0.0, 0.618, 0.0]


def test_a_level_is_reached_as_soon_as_the_low_is_past_it_with_no_tolerance():
    """No width was decided, so there is none: a low a little short of the level is no touch and a low a
    little past it is one. "A little" here is 0.007 of the leg either way, far outside float error and
    well inside any tolerance someone might add."""
    short, past = 110.0 - 0.375 * 20.0, 110.0 - 0.389 * 20.0          # the 0.382 level is 102.36
    bars, at = _scene(((109.0, 109.5, short, 108.0),))
    assert _touches(_rows(bars), at["up"] + 1) == [0.0]
    bars, at = _scene(((109.0, 109.5, past, 108.0),))
    assert _touches(_rows(bars), at["up"] + 1) == [0.382]


def test_a_bar_that_passes_several_levels_carries_the_deepest():
    bars, at = _scene(((109.0, 109.5, 99.0, 100.0),))                 # depth 0.55 in one bar
    assert _touches(_rows(bars), at["up"] + 1) == [0.5]


def test_a_bar_that_goes_beyond_the_origin_counts_as_the_deepest_level():
    bars, at = _scene(((109.0, 109.5, 88.0, 105.0),))                 # a low below 90.0 (depth 1.1), closing back up
    rows = _rows(bars)
    assert rows[-1]["structure_break"] == "NONE"
    assert _touches(rows, at["up"] + 1) == [0.618]


def test_a_bar_that_closes_through_the_structure_the_other_way_is_a_turn_and_carries_nothing():
    """The same low, but the bar CLOSES down there: it breaks the live swing low, the state turns, and a
    turn bar carries no level. The leg it ends is gone; the bar belongs to the next one."""
    bars, at = _scene(((109.0, 109.5, 88.0, 89.0),))
    rows = _rows(bars)
    assert rows[-1]["structure_break"] == "CHOCH_DOWN"
    assert rows[-1]["fib_touch_ratio"] == 0.0 and rows[-1]["structure_leg_size_atr"] < 0


def test_a_bar_wholly_above_the_legs_end_is_no_retracement():
    bars, at = _scene(((111.0, 113.0, 110.5, 112.0), (112.0, 112.5, 111.0, 111.5)))
    rows = _rows(bars)
    assert _touches(rows, at["up"] + 1) == [0.0, 0.0]
    assert rows[at["up"] + 1]["structure_leg_size_atr"] == pytest.approx((113.0 - 90.0) / rows[at["up"] + 1]["atr"])


def test_a_bar_that_retraces_and_makes_a_new_end_is_read_retracement_first():
    """The order inside a bar is not known, so it is a rule: the low is measured against the end as it
    stood BEFORE this bar (110.0: depth 0.45), and only then does the end move to 113.0. Read the other
    way round the same low would be 0.52 of the longer leg and carry 0.5."""
    bars, at = _scene(((109.0, 113.0, 101.0, 112.0),))
    rows = _rows(bars)
    assert _touches(rows, at["up"] + 1) == [0.382]
    assert rows[at["up"] + 1]["structure_leg_size_atr"] == pytest.approx((113.0 - 90.0) / rows[at["up"] + 1]["atr"])


def test_a_new_end_starts_the_record_again():
    tail: tuple[Bar, ...] = (
        (109.0, 109.5, 101.0, 103.0),       # 0.45 of 110 - 90: past 0.382
        (103.0, 114.0, 102.5, 113.0),       # shallower than 0.45, then a new end at 114.0
        (113.0, 113.5, 104.0, 105.0),       # 0.417 of 114 - 90: 0.382 again, for the new end
    )
    bars, at = _scene(tail)
    assert _touches(_rows(bars), at["up"] + 1) == [0.382, 0.0, 0.382]


def test_a_down_leg_mirrors_an_up_leg():
    tail: tuple[Bar, ...] = ((109.0, 109.5, 101.0, 103.0), (103.0, 104.0, 97.0, 98.0))
    bars, at = _scene(tail)
    up, down = _rows(bars), _rows(_mirror(bars))
    assert down[at["up"]]["structure_break"] == "CHOCH_DOWN"
    assert _touches(up, at["up"]) == [0.0, 0.382, 0.618] and _touches(down, at["up"]) == [0.0, -0.382, -0.618]
    for t in range(at["up"], len(bars)):
        assert down[t]["structure_leg_size_atr"] == pytest.approx(-up[t]["structure_leg_size_atr"])
        assert up[t]["structure_leg_size_atr"] > 0


def test_a_tie_for_the_origin_takes_the_later_bar():
    """Between the two 90.0 lows a bar spikes to 111.0, above the turn bar's high. With the later low as
    the origin that spike is before the leg, and the end is the turn bar's 110.0."""
    bars, at = _scene(tie=True)
    rows = _rows(bars)
    assert max(bar[1] for bar in bars[at["down"]:at["up"]]) == 111.0
    assert rows[at["up"]]["structure_break"] == "CHOCH_UP"
    assert rows[at["up"]]["structure_leg_size_atr"] == pytest.approx((110.0 - 90.0) / rows[at["up"]]["atr"])


# --- the two cut rules ------------------------------------------------------------------------

def test_a_frame_that_holds_one_turn_says_nothing_where_the_whole_history_has_a_leg():
    """The frame starts after the scene's first break, so the DOWN turn is the bar it first learns a state
    on. That is not a turn for the frame: it cannot know where the state before it began. So at the UP turn
    it has one turn, no leg, and says nothing, where the whole history reports the leg and its touches."""
    tail: tuple[Bar, ...] = ((109.0, 109.5, 101.0, 103.0),)
    bars, at = _scene(tail)
    full = _rows(bars)
    assert full[-1]["fib_touch_ratio"] == 0.382 and full[-1]["structure_leg_size_atr"] is not None
    begin = at["first"] - 4
    frame = _rows(bars[begin:])
    assert frame[at["down"] - begin]["structure_state"] == "DOWN"       # it does know the state
    assert frame[at["down"] - begin - 1]["structure_state"] is None     # and first learned it on that bar
    assert frame[at["up"] - begin]["structure_break"] == "CHOCH_UP"     # and sees the turn up
    assert [frame[-1][col] for col in COLUMNS] == [None, None]


def test_the_bar_a_frame_first_learns_a_state_on_is_not_a_turn():
    """In the whole scene the state is first known before ``first``, and ``down`` is the first TURN. Were
    the first sight counted as a turn, a leg would appear at ``down`` with an origin searched from there."""
    bars, at = _scene()
    rows = _rows(bars)
    first_known = next(t for t, row in enumerate(rows) if row["structure_state"] is not None)
    assert first_known < at["down"]
    assert all(rows[t]["fib_touch_ratio"] is None for t in range(at["down"], at["up"]))


def _ohlc_walk(seed: int, n: int) -> list[dict]:
    """A random walk whose bars open away from the previous close."""
    rng = random.Random(seed)
    bars: list[Bar] = []
    price = 100.0
    for _ in range(n):
        opened = price * math.exp(rng.gauss(0.0, 0.004))
        price = opened * math.exp(rng.gauss(0.0, 0.01))
        bars.append((opened, max(opened, price) * (1 + abs(rng.gauss(0.0, 0.003))),
                     min(opened, price) * (1 - abs(rng.gauss(0.0, 0.003))), price))
    return _candles(bars)


def _legs(candles: list[dict]) -> list[dict]:
    return [{col: row[col] for col in COLUMNS} for row in features.build_feature_rows({"candles": candles})]


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_the_value_at_a_bar_never_depends_on_later_bars(seed):
    candles = _ohlc_walk(seed, 400)
    full = _legs(candles)
    assert sum(1 for row in full if row["fib_touch_ratio"]) > 10
    for t in range(3, 400, 23):
        assert _legs(candles[:t + 1])[t] == full[t], t


@functools.lru_cache(maxsize=None)
def _walk_and_full(seed: int) -> tuple[tuple[dict, ...], tuple[dict, ...]]:
    candles = _ohlc_walk(seed, 700)
    return tuple(candles), tuple(_legs(candles))


@pytest.mark.parametrize("window", [20, 60, 80, 120, 200])
def test_a_frame_that_starts_later_says_the_same_or_nothing(window):
    compared = empty = 0
    for seed in (11, 12, 13):
        candles, full = _walk_and_full(seed)
        for t in range(300, 700, 7):
            last = _legs(list(candles[t - window + 1:t + 1]))[-1]
            for col in COLUMNS:
                if last[col] is None:
                    empty += 1
                    continue
                compared += 1
                assert last[col] == pytest.approx(full[t][col]), (seed, t, col)
    if window >= 200:
        assert compared > 20 * empty        # a frame of 200 bars has nearly always seen two turns
    if window >= 120:
        assert compared > 5 * empty         # the live cycle's 120 bars usually have


def test_a_hole_in_the_candles_forgets_the_turns():
    bars, at = _scene(((109.0, 109.5, 104.0, 108.0), (108.0, 108.5, 101.0, 103.0), (103.0, 104.0, 102.0, 103.5)))
    opens, highs, lows, closes = (list(column) for column in zip(*bars))
    atr = [row["atr"] for row in _rows(bars)]
    whole = features._structure_columns(opens, highs, lows, closes, atr)
    assert whole["fib_touch_ratio"][-2] == 0.382 and whole["fib_touch_ratio"][-1] == 0.0
    hole = at["up"] + 1
    highs[hole] = None
    out = features._structure_columns(opens, highs, lows, closes, atr)
    assert all(out[col][t] is None for col in COLUMNS for t in range(hole, len(bars)))


def test_the_columns_are_the_same_at_any_price_scale():
    def scaled(factor: float) -> list[dict]:
        return _legs([{**c, **{k: c[k] * factor for k in ("open", "high", "low", "close")}} for c in _ohlc_walk(31, 300)])
    small, large = scaled(0.005), scaled(500.0)
    for a, b in zip(small, large):
        assert a["fib_touch_ratio"] == b["fib_touch_ratio"]
        assert a["structure_leg_size_atr"] == (pytest.approx(b["structure_leg_size_atr"], rel=1e-9)
                                               if b["structure_leg_size_atr"] is not None else None)


def test_the_touch_takes_only_the_three_ratios_and_agrees_with_the_legs_side():
    assert LEVELS == (0.382, 0.5, 0.618)
    rows = _legs(_ohlc_walk(41, 1500))
    allowed = {0.0, None} | {sign * level for level in LEVELS for sign in (1, -1)}
    assert {row["fib_touch_ratio"] for row in rows} == allowed        # the walk reaches every one of them
    for row in rows:
        if row["fib_touch_ratio"] and row["structure_leg_size_atr"] is not None:
            assert (row["fib_touch_ratio"] > 0) == (row["structure_leg_size_atr"] > 0)
        if row["structure_leg_size_atr"] is not None:
            assert row["structure_leg_size_atr"] != 0.0               # a state, not an event: a leg has a size


# --- evidence only ----------------------------------------------------------------------------

def test_the_two_columns_ride_on_the_row_and_are_evidence_only():
    assert COLUMNS == ("structure_leg_size_atr", "fib_touch_ratio")
    assert set(COLUMNS) <= set(features.STRUCTURE_EVIDENCE_COLUMNS)
    assert features.STRUCTURE_MINTABLE_NUMERIC_COLUMNS == ("structure_break_atr", "fvg_touch_size_atr",
                                                           "order_block_touch_size_atr")
    row = features.build_feature_rows({"candles": _ohlc_walk(51, 80)})[-1]
    for column in COLUMNS:
        assert column in row and f"{features.PREVIOUS_BAR_PREFIX}{column}" in row, column


def test_nothing_the_factory_makes_can_read_a_retracement_column():
    numeric, categorical = template_space.known_features("binance_futures")
    for column in COLUMNS:
        assert column not in template_space.NUMERIC_FEATURES and column not in template_space.CATEGORICAL_FEATURES
        assert column not in numeric and column not in categorical, column
    template = template_space.TEMPLATES[0]
    raw = generator.build_spec_dict(template, dict(template.base_params), strategy_id="strategy_test",
                                    generation_id="generation_test")
    for condition in (
        {"feature": "fib_touch_ratio", "comparison": ">=", "value": 0.5},
        {"feature": "structure_leg_size_atr", "comparison": ">=", "value": 3.0},
        {"feature": "close", "comparison": ">", "value_from": "structure_leg_size_atr"},
    ):
        spec = StrategySpec.from_dict({**raw, "entry_rules": {"operator": "AND", "conditions": [condition]}})
        verdict = template_space.validate_strategy(spec)
        assert not verdict["approved_for_backtest"] and "BLOCK_UNKNOWN_FEATURE" in verdict["block_reasons"], condition


def test_the_proposal_prompt_does_not_offer_a_retracement_column():
    prompt = proposer.build_proposal_prompt(existing_families=[])
    named = set(re.findall(r"[a-z0-9_]+", prompt))
    assert "order_block_touch_size_atr" in named         # the prompt does list the structure vocabulary
    assert not (set(COLUMNS) & named)


def test_no_template_reads_a_retracement_column():
    for template in template_space.TEMPLATES:
        assert not (template_space.template_features(template) & set(COLUMNS)), template.family
