"""The order-block columns: the last opposite-close bar before a break, as a zone.

Thomas 2026-11-26: "예, 가까운 봉으로". ``docs/history/2026-11-26-order-block-columns.md`` carries the
definition. These tests pin what the four columns DO, each by building bars and reading the row: which
bar becomes the block, when there is no zone, when a zone is touched and when it is gone, that a
frame which starts later says the same thing or nothing, and that the factory can read exactly one of
them (the touched block's size, admitted with the `order_block_return_*` pair the same day, "예,
선례대로"; the pair itself is pinned in ``test_mvp_runtime_crypto_structure_families``). The nine columns that were there before are pinned in ``test_mvp_runtime_crypto_structure_columns``;
here only that an unreadable open leaves them alone. All bars are synthetic."""

from __future__ import annotations

import functools
import math
import random
import re
from datetime import datetime, timedelta, timezone

import pytest

from runtime.mvp_runtime.crypto import features, generator, proposer, template_space
from runtime.mvp_runtime.crypto.strategy import StrategySpec

K = features.STRUCTURE_SWING_BARS
LIFE = features.FVG_BAND_LIFE_BARS
COLUMNS = features.ORDER_BLOCK_COLUMNS
MINTABLE = "order_block_touch_size_atr"                        # the one column a family reads
EVIDENCE = tuple(column for column in COLUMNS if column != MINTABLE)
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

Bar = tuple[float, float, float, float]            # open, high, low, close


def _candles(bars: list[Bar]) -> list[dict]:
    """Hourly candles from (open, high, low, close): the open is the bar's own, not the previous close."""
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
    """The same bars upside down around 100: an up break becomes a down break."""
    return [(200.0 - opened, 200.0 - low, 200.0 - high, 200.0 - close) for opened, high, low, close in bars]


DOWN_BAR: Bar = (101.5, 101.8, 100.9, 101.0)        # closes below its open: range 100.9 .. 101.8
SWING_UP: Bar = (101.0, 104.0, 100.8, 102.0)        # the swing high at 104.0, closing above its open
BREAK: Bar = (101.0, 105.6, 100.95, 105.0)          # closes above 104.0; its own low is inside DOWN_BAR's range


def _scene(*, lead: tuple[Bar, ...] = (DOWN_BAR,), tail: tuple[Bar, ...] = (),
           swing: Bar = SWING_UP) -> tuple[list[Bar], dict[str, int]]:
    """A swing low and a swing high at 104.0, then quiet bars for longer than the band life (so the touch
    column is reporting), then ``lead``, then a bar that closes above 104.0, then ``tail``.

    The eight warm-up bars close DOWN and sit before the swing bar; every bar from the swing bar to
    ``lead`` closes at or above its open (``swing`` can be swapped for one that does not). So the only
    opposite bars a search between the swing and the break can find are the ones a test puts in ``lead``."""
    bars: list[Bar] = []
    at: dict[str, int] = {}
    for i in range(8):
        bars.append((100.2 + 0.001 * i, 101.0 + 0.001 * i, 99.0 + 0.001 * i, 100.0 + 0.001 * i))
    bars += [(99.0, 100.4, 97.0, 99.5), (99.5, 100.6, 98.6, 100.2), (100.2, 101.4, 99.6, 101.0)]   # the swing low at 97.0
    at["swing"] = len(bars)
    bars.append(swing)
    bars.append((102.0, 102.6, 100.6, 102.1))
    for i in range(LIFE + 6):                       # highs and lows creep up: no swing forms in here
        bars.append((100.5 + 0.001 * i, 101.5 + 0.001 * i, 99.5 + 0.001 * i, 100.5005 + 0.001 * i))
    bars += list(lead)
    at["break"] = len(bars)
    bars.append(BREAK)
    bars += list(tail)
    return bars, at


def _above(n: int) -> tuple[Bar, ...]:
    """Bars that stay well above every zone in the scene, creeping up so that no swing forms."""
    return tuple((105.0 + 0.001 * i, 106.0 + 0.001 * i, 104.0 + 0.001 * i, 105.0005 + 0.001 * i) for i in range(n))


def _dip(low: float) -> Bar:
    return (105.0, 105.2, low, 104.5)


# --- which bar is the block, and when there is none -----------------------------------------

def test_the_block_is_the_last_bar_that_closed_against_the_break_and_its_range_is_the_zone():
    bars, at = _scene(tail=(*_above(2), _dip(101.81), _dip(101.8), _dip(100.0)))
    rows = _rows(bars)
    born = rows[at["break"]]
    assert rows[at["break"]]["structure_break_atr"] > 0                        # the break the zone hangs on
    assert born["order_block_size_atr"] == pytest.approx((101.8 - 100.9) / born["atr"])
    assert rows[at["break"] - 1]["order_block_size_atr"] == 0.0                 # no break, no zone born
    assert [rows[at["break"] + k]["order_block_touch"] for k in (1, 2, 3)] == ["NONE"] * 3   # 101.81 is not 101.8
    touch = rows[at["break"] + 4]
    assert touch["order_block_touch"] == "UP_BLOCK" and touch["order_block_touch_age_bars"] == 4.0
    assert touch["order_block_touch_size_atr"] == pytest.approx(born["order_block_size_atr"])
    gone = rows[at["break"] + 5]                                                # a touched zone is gone
    assert (gone["order_block_touch"], gone["order_block_touch_size_atr"], gone["order_block_touch_age_bars"]) == ("NONE", 0.0, 0.0)


def test_the_break_bar_never_touches_the_zone_it_made():
    """The break bar has usually just travelled through the zone (here its low is inside it). The zone is
    born when that bar closes; a touch is a LATER bar coming back."""
    bars, at = _scene(tail=(_dip(101.0),))
    rows = _rows(bars)
    assert BREAK[2] <= DOWN_BAR[1]
    assert rows[at["break"]]["order_block_touch"] == "NONE"
    assert rows[at["break"] + 1]["order_block_touch"] == "UP_BLOCK" and rows[at["break"] + 1]["order_block_touch_age_bars"] == 1.0


def test_the_nearest_opposite_bar_wins_over_an_earlier_one():
    earlier: Bar = (100.4, 100.6, 100.0, 100.1)                                 # also closes down: range 100.0 .. 100.6
    bars, at = _scene(lead=(earlier, DOWN_BAR), tail=(_dip(101.5),))
    assert _rows(bars)[at["break"] + 1]["order_block_touch"] == "UP_BLOCK"      # 101.5 reaches 101.8, not 100.6
    bars, at = _scene(lead=(earlier,), tail=(_dip(101.5), _dip(100.6)))
    rows = _rows(bars)
    assert rows[at["break"] + 1]["order_block_touch"] == "NONE" and rows[at["break"] + 2]["order_block_touch"] == "UP_BLOCK"


def test_a_bar_that_closes_where_it_opened_is_not_an_opposite_bar():
    earlier: Bar = (100.4, 100.6, 100.0, 100.1)
    flat: Bar = (101.0, 101.8, 100.9, 101.0)                                    # DOWN_BAR's range, close == open
    bars, at = _scene(lead=(earlier, flat), tail=(_dip(101.5), _dip(100.6)))
    rows = _rows(bars)
    assert rows[at["break"]]["order_block_size_atr"] == pytest.approx((100.6 - 100.0) / rows[at["break"]]["atr"])
    assert rows[at["break"] + 1]["order_block_touch"] == "NONE" and rows[at["break"] + 2]["order_block_touch"] == "UP_BLOCK"


def test_the_search_reaches_back_to_the_swing_bar_and_no_further():
    """With no opposite bar between the swing and the break there is no zone, though the warm-up bars
    before the swing all closed down. The swing bar itself is inside the search."""
    bars, at = _scene(lead=(), tail=(_dip(99.0),))
    rows = _rows(bars)
    assert rows[at["break"]]["structure_break_atr"] > 0 and rows[at["break"]]["order_block_size_atr"] == 0.0
    assert rows[at["break"] + 1]["order_block_touch"] == "NONE"
    assert all(opened > close for opened, _h, _l, close in bars[:8])            # they did close down
    swing_down: Bar = (103.0, 104.0, 100.8, 102.0)                              # same range, closes below its open
    bars, at = _scene(lead=(), swing=swing_down, tail=(_dip(104.0),))
    rows = _rows(bars)
    assert rows[at["break"]]["order_block_size_atr"] == pytest.approx((104.0 - 100.8) / rows[at["break"]]["atr"])
    assert rows[at["break"] + 1]["order_block_touch"] == "UP_BLOCK"


def test_a_zone_price_never_left_is_no_zone():
    """The opposite bar's wick tops the break bar's close: the close is still inside its range."""
    wick: Bar = (103.0, 105.5, 102.0, 102.5)
    bars, at = _scene(lead=(wick,), tail=(_dip(101.0),))
    rows = _rows(bars)
    assert rows[at["break"]]["structure_break_atr"] > 0 and rows[at["break"]]["order_block_size_atr"] == 0.0
    assert rows[at["break"] + 1]["order_block_touch"] == "NONE"
    inside: Bar = (103.0, 104.9, 102.0, 102.5)                                  # a hair below the close: a zone
    bars, at = _scene(lead=(inside,), tail=(_dip(104.9),))
    assert _rows(bars)[at["break"] + 1]["order_block_touch"] == "UP_BLOCK"


def test_a_bar_that_reaches_several_zones_reports_the_latest_and_clears_them_all():
    tail: tuple[Bar, ...] = (
        (105.0, 106.0, 104.2, 105.8), (105.8, 108.0, 105.5, 106.5),             # a second swing high at 108.0
        (106.5, 107.0, 105.2, 105.6),                                           # closes down: range 105.2 .. 107.0
        (105.6, 106.8, 105.3, 106.6),
        (106.6, 109.5, 106.4, 109.0),                                           # closes above 108.0: the second zone
        (109.0, 109.4, 107.5, 108.0),
        (108.0, 108.2, 101.0, 102.0),                                           # reaches both zones
        (102.0, 103.0, 100.5, 101.0),                                           # nothing left to reach
    )
    bars, at = _scene(tail=tail)
    rows = _rows(bars)
    second = at["break"] + 5
    assert rows[second]["order_block_size_atr"] == pytest.approx((107.0 - 105.2) / rows[second]["atr"])
    assert rows[second + 1]["order_block_touch"] == "NONE"
    both = rows[second + 2]
    assert both["order_block_touch"] == "UP_BLOCK" and both["order_block_touch_age_bars"] == 2.0
    assert both["order_block_touch_size_atr"] == pytest.approx(rows[second]["order_block_size_atr"])
    assert rows[second + 3]["order_block_touch"] == "NONE"


def test_an_untouched_zone_is_dropped_after_the_band_life():
    bars, at = _scene(tail=(*_above(LIFE - 1), _dip(101.0)))                    # the dip is LIFE bars after the break
    assert _rows(bars)[-1]["order_block_touch"] == "UP_BLOCK" and len(bars) - 1 - at["break"] == LIFE
    bars, at = _scene(tail=(*_above(LIFE), _dip(101.0)))                        # one bar later
    assert _rows(bars)[-1]["order_block_touch"] == "NONE"


def test_a_down_break_mirrors_an_up_break():
    bars, at = _scene(tail=(*_above(2), _dip(101.0)))
    up, down = _rows(bars), _rows(_mirror(bars))
    assert down[at["break"]]["structure_break_atr"] < 0
    assert down[at["break"]]["order_block_size_atr"] == pytest.approx(-up[at["break"]]["order_block_size_atr"])
    assert up[at["break"] + 3]["order_block_touch"] == "UP_BLOCK" and down[at["break"] + 3]["order_block_touch"] == "DOWN_BLOCK"
    assert down[at["break"] + 3]["order_block_touch_size_atr"] == pytest.approx(-up[at["break"] + 3]["order_block_touch_size_atr"])
    assert down[at["break"] + 3]["order_block_touch_age_bars"] == 3.0


def _lists(bars: list[Bar]) -> tuple[list, list, list, list, list]:
    opens, highs, lows, closes = (list(column) for column in zip(*bars))
    atr = [row["atr"] for row in _rows(bars)]
    return opens, highs, lows, closes, atr


def test_an_open_that_cannot_be_read_stops_the_search_and_leaves_every_other_column_alone():
    """The search walks back from the bar before the break. A bar whose open is not a number may have been
    the last opposite bar, so the search ends there with no zone. An unreadable open the search never
    reaches changes nothing. Every structure column that does not read the open is the same either way."""
    up_bar: Bar = (101.0, 101.6, 100.95, 101.4)                                 # sits between DOWN_BAR and the break
    bars, at = _scene(lead=(DOWN_BAR, up_bar), tail=(_dip(101.0),))
    opens, highs, lows, closes, atr = _lists(bars)
    whole = features._structure_columns(opens, highs, lows, closes, atr)
    assert whole["order_block_size_atr"][at["break"]] > 0 and whole["order_block_touch"][at["break"] + 1] == "UP_BLOCK"

    hidden = list(opens)
    hidden[at["break"] - 1] = None                                              # the bar the search meets first
    out = features._structure_columns(hidden, highs, lows, closes, atr)
    assert out["order_block_size_atr"][at["break"]] == 0.0 and out["order_block_touch"][at["break"] + 1] == "NONE"

    far = list(opens)
    far[at["break"] - 3] = None                                                 # behind DOWN_BAR: never reached
    assert features._structure_columns(far, highs, lows, closes, atr) == whole

    others = [column for column in features.STRUCTURE_COLUMNS if column not in COLUMNS]
    assert len(others) >= 9 and "structure_break_atr" in others       # only the order-block columns read the open
    for column in others:
        assert out[column] == whole[column], column


# --- the two cut rules ------------------------------------------------------------------------

def _ohlc_walk(seed: int, n: int) -> list[dict]:
    """A random walk whose bars open AWAY from the previous close, so "closed below its open" and "closed
    below the previous close" are different bars (about one in eight here). On bars that open at the
    previous close the two are the same and the definition would go unchecked."""
    rng = random.Random(seed)
    bars: list[Bar] = []
    price = 100.0
    for _ in range(n):
        opened = price * math.exp(rng.gauss(0.0, 0.004))
        price = opened * math.exp(rng.gauss(0.0, 0.01))
        bars.append((opened, max(opened, price) * (1 + abs(rng.gauss(0.0, 0.003))),
                     min(opened, price) * (1 - abs(rng.gauss(0.0, 0.003))), price))
    return _candles(bars)


def _block(candles: list[dict]) -> list[dict]:
    return [{col: row[col] for col in COLUMNS} for row in features.build_feature_rows({"candles": candles})]


def test_the_walk_does_tell_a_down_close_from_a_down_bar():
    candles = _ohlc_walk(1, 400)
    differ = sum(1 for before, bar in zip(candles, candles[1:])
                 if (bar["close"] < bar["open"]) != (bar["close"] < before["close"]))
    assert differ > 20


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_the_value_at_a_bar_never_depends_on_later_bars(seed):
    candles = _ohlc_walk(seed, 400)
    full = _block(candles)
    assert sum(1 for row in full if row["order_block_touch"] in ("UP_BLOCK", "DOWN_BLOCK")) > 10
    for t in range(3, 400, 23):
        assert _block(candles[:t + 1])[t] == full[t], t


@functools.lru_cache(maxsize=None)
def _walk_and_full(seed: int) -> tuple[tuple[dict, ...], tuple[dict, ...]]:
    candles = _ohlc_walk(seed, 700)
    return tuple(candles), tuple(_block(candles))


@pytest.mark.parametrize("window", [5, 20, 49, 50, 60, 80, 120, 200])
def test_a_frame_that_starts_later_says_the_same_or_nothing(window):
    compared = empty = 0
    for seed in (11, 12, 13):
        candles, full = _walk_and_full(seed)
        for t in range(300, 700, 7):
            last = _block(list(candles[t - window + 1:t + 1]))[-1]
            if window <= LIFE:              # too short to have waited a band life: the touch says nothing
                assert last["order_block_touch"] is None, (seed, t)
            for col in COLUMNS:
                if last[col] is None:
                    empty += 1
                    continue
                compared += 1
                expected = full[t][col]
                assert last[col] == (pytest.approx(expected) if isinstance(expected, float) else expected), (seed, t, col)
    if window >= 120:
        assert compared > 20 * empty        # the live cycle's 120 bars do carry these columns


def test_a_frame_that_cannot_know_a_zone_says_nothing_rather_than_none():
    """Why the touch waits for more than a gap's touch does. The whole history has a zone, born at a break
    of a swing high that sits BEFORE this frame's first bar, and a bar touches it. The frame is longer
    than the band life, so a wait counted from its first bar (the gap's rule) would report "NONE" here:
    a value, and the wrong one. The frame has confirmed no swing of its own, so it reports nothing."""
    bars, at = _scene(tail=(*_above(2), _dip(101.0)))
    touch = at["break"] + 3
    full = _rows(bars)
    assert full[touch]["order_block_touch"] == "UP_BLOCK" and full[touch]["fvg_touch"] is not None
    begin = at["swing"] + 4                                                     # the swing is outside the frame
    frame = _rows(bars[begin:])
    assert len(frame) - 1 >= LIFE + 2 and frame[-1]["fvg_touch"] is not None    # the gap's wait is over
    assert [frame[-1][col] for col in COLUMNS if col != "order_block_size_atr"] == [None, None, None]


def test_the_touch_starts_reporting_one_band_life_after_both_sides_are_confirmed():
    """The break's size column is filled from the bar the frame has confirmed a swing on both sides (in
    the scene that is in the warm-up, before the named swing). From that bar the frame knows every
    break, so every zone alive LIFE bars later was born in view."""
    bars, at = _scene()
    rows = _rows(bars)
    both = next(t for t, row in enumerate(rows) if row["structure_break_atr"] is not None)
    assert K <= both < at["swing"] + K
    assert rows[both + LIFE - 1]["order_block_touch"] is None
    assert rows[both + LIFE]["order_block_touch"] == "NONE"
    assert rows[both + LIFE - 1]["order_block_size_atr"] == 0.0                 # the size does not wait


def test_a_hole_in_the_candles_makes_the_touch_wait_again():
    bars, at = _scene(tail=_above(4))
    opens, highs, lows, closes, atr = _lists(bars)
    assert features._structure_columns(opens, highs, lows, closes, atr)["order_block_touch"][-1] == "NONE"
    hole = at["break"] - 20
    highs[hole] = None
    out = features._structure_columns(opens, highs, lows, closes, atr)
    assert all(out[col][hole] is None for col in COLUMNS)
    assert all(value is None for value in out["order_block_touch"][hole:])      # fewer than LIFE bars since the hole


def test_the_columns_are_the_same_at_any_price_scale():
    def scaled(factor: float) -> list[dict]:
        return _block([{**c, **{k: c[k] * factor for k in ("open", "high", "low", "close")}} for c in _ohlc_walk(31, 300)])
    small, large = scaled(0.005), scaled(500.0)
    for a, b in zip(small, large):
        for col in COLUMNS:
            assert a[col] == (pytest.approx(b[col], rel=1e-9, abs=1e-9) if isinstance(b[col], float) else b[col]), col


def test_the_label_stays_inside_its_closed_vocabulary_and_the_signs_agree():
    rows = _block(_ohlc_walk(41, 800))
    assert {r["order_block_touch"] for r in rows} == features.ORDER_BLOCK_TOUCH_VALUES | {None}
    for row in rows:
        if row["order_block_touch"] == "UP_BLOCK":
            assert row["order_block_touch_size_atr"] > 0 and row["order_block_touch_age_bars"] >= 1.0
        elif row["order_block_touch"] == "DOWN_BLOCK":
            assert row["order_block_touch_size_atr"] < 0 and row["order_block_touch_age_bars"] >= 1.0
        elif row["order_block_touch"] == "NONE":
            assert row["order_block_touch_size_atr"] == 0.0 and row["order_block_touch_age_bars"] == 0.0


# --- one column is mintable, three are evidence only -------------------------------------------

def test_the_four_columns_ride_on_the_row_and_exactly_one_is_mintable():
    assert COLUMNS == ("order_block_size_atr", "order_block_touch", "order_block_touch_size_atr",
                       "order_block_touch_age_bars")
    assert set(EVIDENCE) <= set(features.STRUCTURE_EVIDENCE_COLUMNS) and len(EVIDENCE) == 3
    assert MINTABLE not in features.STRUCTURE_EVIDENCE_COLUMNS
    assert MINTABLE in features.STRUCTURE_MINTABLE_NUMERIC_COLUMNS
    assert not (set(EVIDENCE) & set(features.STRUCTURE_MINTABLE_NUMERIC_COLUMNS))
    assert set(features.STRUCTURE_MINTABLE_LABELS) == {"structure_break"}
    row = features.build_feature_rows({"candles": _ohlc_walk(51, 80)})[-1]
    for column in COLUMNS:
        assert column in row and f"{features.PREVIOUS_BAR_PREFIX}{column}" in row, column


def test_the_factory_can_read_the_touched_blocks_size_and_none_of_the_other_three():
    """The three are absent from both mintable vocabularies, so the validator every generated spec,
    fusion child and proposed trial passes through refuses a spec naming one, as a feature and as a
    ``value_from``. The fourth is admitted, and a spec naming it passes."""
    numeric, categorical = template_space.known_features("binance_futures")
    assert MINTABLE in template_space.NUMERIC_FEATURES and MINTABLE in numeric
    for column in EVIDENCE:
        assert column not in template_space.NUMERIC_FEATURES, column
        assert column not in template_space.CATEGORICAL_FEATURES, column
        assert column not in numeric and column not in categorical, column
    template = template_space.TEMPLATES[0]
    raw = generator.build_spec_dict(template, dict(template.base_params), strategy_id="strategy_test",
                                    generation_id="generation_test")
    for condition in (
        {"feature": "order_block_size_atr", "comparison": ">", "value": 0.5},
        {"feature": "order_block_touch", "comparison": "==", "value": "UP_BLOCK"},
        {"feature": "order_block_touch_age_bars", "comparison": ">=", "value": 3.0},
        {"feature": "close", "comparison": ">", "value_from": "order_block_size_atr"},
    ):
        spec = StrategySpec.from_dict({**raw, "entry_rules": {"operator": "AND", "conditions": [condition]}})
        verdict = template_space.validate_strategy(spec)
        assert not verdict["approved_for_backtest"] and "BLOCK_UNKNOWN_FEATURE" in verdict["block_reasons"], condition
    admitted = StrategySpec.from_dict({**raw, "entry_rules": {"operator": "AND", "conditions": [
        {"feature": MINTABLE, "comparison": ">=", "value": 0.3}]}})
    assert "BLOCK_UNKNOWN_FEATURE" not in template_space.validate_strategy(admitted)["block_reasons"]


def test_the_proposal_prompt_offers_exactly_one_order_block_column():
    prompt = proposer.build_proposal_prompt(existing_families=[])
    named = set(re.findall(r"[a-z0-9_]+", prompt))
    assert set(COLUMNS) & named == {MINTABLE}


def test_only_the_order_block_pair_reads_an_order_block_column_and_only_the_one():
    readers = set()
    for template in template_space.TEMPLATES:
        raw = generator.build_spec_dict(template, dict(template.base_params), strategy_id="strategy_test",
                                        generation_id="generation_test")
        named = {c.get("feature") for c in raw["entry_rules"]["conditions"]} | {
            c.get("value_from") for c in raw["entry_rules"]["conditions"]}
        assert not (named & set(EVIDENCE)), template.family
        if MINTABLE in named:
            readers.add(template.family)
    assert readers == {"order_block_return_long", "order_block_return_short"}
