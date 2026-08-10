"""LP4 preconditions (2)/(3): the limit-entry fill model — semantics, refusals, inertness.

The model exists so a limit entry can be SCORED before anyone re-opens whether it may be
PLACED. Three properties carry the whole design and each gets its own section here:

- a touch is not a fill (the selection-bias refusal, made mechanical);
- the wait is counted in bars, and a rule-decided cancel is distinguishable from a replay
  window that merely ran out;
- nothing in the runtime imports the model yet — precondition (4) gates the wiring, and
  that inertness is pinned as a test rather than trusted as a convention.
"""

from __future__ import annotations

import pathlib

import pytest

from runtime.mvp_runtime.crypto.limit_entry import (
    LIMIT_FILL_UNPRICEABLE,
    UNFILLED_BARS_EXHAUSTED,
    UNFILLED_CANCEL_TIMEOUT,
    limit_entry_fill,
)
from runtime.mvp_runtime.errors import ToolError

TICK = 0.1


def _bar(low: float, high: float) -> dict:
    return {"low": low, "high": high, "open": (low + high) / 2, "close": (low + high) / 2}


def _fill(direction="LONG", limit=100.0, bars=(), timeout_bars=3, tick=TICK):
    return limit_entry_fill(direction, limit, list(bars), timeout_bars=timeout_bars, tick_size=tick)


# --- (2) a touch is not a fill ------------------------------------------------------------


def test_a_bar_that_only_touches_the_limit_never_fills():
    """The bar's low printing the limit price proves the price traded, not that the queue
    reached this order — scoring it as a fill is the optimism the refusal names."""
    result = _fill(bars=[_bar(low=100.0, high=101.0)] * 3)
    assert result.filled is False


def test_trading_through_by_less_than_a_tick_is_still_a_touch():
    result = _fill(bars=[_bar(low=100.0 - TICK * 0.4, high=101.0)] * 3)
    assert result.filled is False


def test_trading_through_by_exactly_one_tick_fills_at_the_limit():
    """"At least one tick" includes the tick itself — and the fill price is the LIMIT, not
    the better price the bar reached: improvement is never credited."""
    result = _fill(bars=[_bar(low=100.0 - TICK, high=101.0)])
    assert result.filled is True
    assert result.fill_bar_index == 0
    assert result.entry_price == 100.0


def test_a_gap_through_the_level_credits_no_improvement():
    """The bar opened far below the buy limit; a real resting order fills at its own price
    on the sweep, and the pessimistic model claims nothing better."""
    result = _fill(bars=[_bar(low=90.0, high=95.0)])
    assert result.filled is True and result.entry_price == 100.0


def test_the_first_qualifying_bar_is_the_fill():
    bars = [_bar(low=100.5, high=101.0), _bar(low=99.8, high=100.6), _bar(low=95.0, high=100.0)]
    result = _fill(bars=bars)
    assert result.fill_bar_index == 1
    assert result.bars_scanned == 2


def test_a_short_rests_above_and_fills_on_the_high():
    bars = [_bar(low=99.0, high=100.0), _bar(low=99.0, high=100.0 + TICK)]
    result = _fill(direction="SHORT", bars=bars)
    assert result.filled is True and result.fill_bar_index == 1 and result.entry_price == 100.0


def test_a_short_touch_on_the_high_is_not_a_fill():
    result = _fill(direction="SHORT", bars=[_bar(low=99.0, high=100.0)] * 3)
    assert result.filled is False


# --- (3) the wait is denominated in bars --------------------------------------------------


def test_the_order_is_cancelled_after_timeout_bars_even_if_the_market_comes_back():
    """The qualifying bar arrives one bar after the cancel: the order is already gone. This
    is the bar-based timeout doing exactly what wall-clock cannot — backtest and a future
    live leg count the same unit."""
    bars = [_bar(low=100.5, high=101.0)] * 3 + [_bar(low=90.0, high=101.0)]
    result = _fill(bars=bars, timeout_bars=3)
    assert result.filled is False
    assert result.unfilled_reason == UNFILLED_CANCEL_TIMEOUT
    assert result.bars_scanned == 3


def test_a_window_that_ends_early_is_not_a_clean_cancel():
    """Two bars of data against a three-bar timeout: the rule never got to decide, and a
    selection-bias measurement must not count 'the tail ended' as 'the market never came
    back'."""
    result = _fill(bars=[_bar(low=100.5, high=101.0)] * 2, timeout_bars=3)
    assert result.filled is False
    assert result.unfilled_reason == UNFILLED_BARS_EXHAUSTED
    assert result.bars_scanned == 2


def test_a_fill_on_the_last_allowed_bar_still_fills():
    bars = [_bar(low=100.5, high=101.0)] * 2 + [_bar(low=99.0, high=101.0)]
    result = _fill(bars=bars, timeout_bars=3)
    assert result.filled is True and result.fill_bar_index == 2 and result.bars_scanned == 3


# --- refusals: unpriceable never reads as unfilled ----------------------------------------


@pytest.mark.parametrize("kwargs", [
    {"direction": "HOLD"},
    {"limit": 0.0},
    {"limit": -100.0},
    {"tick": 0.0},
    {"tick": -0.1},
    {"timeout_bars": 0},
    {"timeout_bars": -1},
    {"timeout_bars": True},
])
def test_an_unpriceable_input_refuses_by_name(kwargs):
    with pytest.raises(ToolError) as excinfo:
        _fill(bars=[_bar(low=99.0, high=101.0)], **kwargs)
    assert excinfo.value.reason_code == LIMIT_FILL_UNPRICEABLE


def test_a_malformed_bar_refuses_rather_than_reading_as_no_fill():
    """The bar this model cannot read might have been the fill. 'Would not have filled' is a
    RESULT, and a quiet skip would manufacture it from garbage."""
    with pytest.raises(ToolError) as excinfo:
        _fill(bars=[_bar(low=100.5, high=101.0), {"low": None, "high": 101.0}], timeout_bars=3)
    assert excinfo.value.reason_code == LIMIT_FILL_UNPRICEABLE

    with pytest.raises(ToolError):
        _fill(bars=[_bar(low=100.5, high=101.0), "not-a-bar"], timeout_bars=3)


def test_a_malformed_bar_beyond_the_timeout_is_never_read():
    """The cancel fires first: bars past the timeout are outside the order's life and their
    contents are none of this model's business."""
    result = _fill(bars=[_bar(low=100.5, high=101.0)] * 2 + [{"low": None}], timeout_bars=2)
    assert result.filled is False and result.unfilled_reason == UNFILLED_CANCEL_TIMEOUT


# --- the inertness is a property, not a convention ----------------------------------------


def test_nothing_in_the_runtime_imports_the_limit_entry_model_yet():
    """Precondition (4) gates the wiring: the aligned families (`mean_reversion_*`,
    `funding_fade_*`) have never produced a confirmed candidate, so a limit entry may be
    scored in principle and placed nowhere. The day a runtime module imports this one, that
    must be a deliberate re-opening of Thomas's 2026-07-28 decision — this test is where the
    act announces itself, exactly like the seam test on `DEFAULT_STOP_SLIPPAGE_BPS`."""
    runtime_root = pathlib.Path(__file__).resolve().parent.parent / "runtime"
    offenders = []
    for path in runtime_root.rglob("*.py"):
        if path.name == "limit_entry.py":
            continue
        if "limit_entry" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(runtime_root)))
    assert offenders == [], (
        "the limit-entry fill model has runtime importers before condition (4) opened the "
        f"door: {offenders}"
    )
