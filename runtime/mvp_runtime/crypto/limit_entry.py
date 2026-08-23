"""LP4 preconditions (2) and (3): a limit ENTRY can now be scored — it still cannot be placed.

Thomas's 2026-07-28 decision stands: the live entry is a MARKET order, and the maker leg this
repo ships is the take-profit exit only. What blocked even *asking* whether a limit entry is
worth its ~6 bps was that nothing could score one honestly: ``paper.build_entry_plan`` enters
at the row's close, so nothing rests, and OHLCV alone cannot tell a touch from a fill. This
module is that missing instrument, built while the question stays closed — precondition (4)
(an aligned family earning a confirmed candidate) is what re-opens it, and as of 2026-08-10
the aligned families hold 140 candidates with holdout 124 INSUFFICIENT / 16 CONTRADICTED /
0 CONFIRMED (`docs/runtime-contracts/LP4_ORDER_ADAPTER_DESIGN_V0.1.md`).

**Zero callers, by design, and the property is pinned** — no runtime module may import this
one until the door opens (`test_nothing_in_the_runtime_imports_the_limit_entry_model_yet`).
Wiring it into the factory's scoring or into a live leg is the deliberate act condition (4)
gates, not a refactor. The precedent is `cost.VenueCostDeclaration.funding_multiplier`:
measured, recorded, and deliberately not applied until its semantic is settled.

The two semantics this module owns, stated once:

- **(2) A touch is not a fill.** A resting limit fills here only when the bar trades
  THROUGH the price by at least one tick — ``low <= limit - tick`` for a resting buy,
  ``high >= limit + tick`` for a resting sell. A bar whose extreme merely equals the limit
  proves the price printed, not that the queue reached this order; scoring a touch as a fill
  is the exact optimism the selection-bias refusal was written against (the non-fills
  correlate with the winners). The fill price is the LIMIT, never better: a gap through the
  level credits no improvement, so the model's error runs toward understating the edge.
- **(3) The wait is denominated in BARS.** An unfilled order is cancelled after
  ``timeout_bars`` bars of the traded timeframe — the one unit the backtest and a future
  live leg can both count without drift. There is deliberately NO default: the number is a
  decision nobody has made, and a default here is where an unowned constant would hide.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..errors import ToolError

LONG = "LONG"
SHORT = "SHORT"

# One refusal, fail-closed: an input this model cannot price is never a quiet non-fill,
# because "the order would not have filled" is a RESULT, and a malformed bar might have been
# the fill. Same posture as `cost.round_trip_cost_r` returning inf: unknown must not read as
# anything in particular.
LIMIT_FILL_UNPRICEABLE = "LIMIT_FILL_UNPRICEABLE"

# Why an order ended unfilled. Two answers on purpose: a rule-decided cancel and a replay
# window that simply ran out are different qualities of evidence, and a selection-bias
# measurement that pools them would count "the tail ended" as "the market never came back".
UNFILLED_CANCEL_TIMEOUT = "cancel_timeout"
UNFILLED_BARS_EXHAUSTED = "bars_exhausted"


@dataclass(frozen=True)
class LimitFillResult:
    """What became of one resting entry order. Pure data; the caller owns what it means.

    ``filled`` — the bar traded through the limit within the window.
    ``fill_bar_index`` — 0-based index into the bars the order rested across; None unfilled.
    ``entry_price`` — the LIMIT price exactly (pessimistic: improvement is never credited).
    ``bars_scanned`` — how many bars the order actually rested; on a fill, the fill bar is
    the last one counted.
    ``unfilled_reason`` — ``cancel_timeout`` or ``bars_exhausted``; None when filled.
    """

    filled: bool
    fill_bar_index: int | None
    entry_price: float | None
    bars_scanned: int
    unfilled_reason: str | None


def _price(bar: Mapping[str, Any], field: str, index: int) -> float:
    value = bar.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ToolError(
            LIMIT_FILL_UNPRICEABLE,
            f"bar {index} has no usable {field!r}; a bar this model cannot read might have "
            f"been the fill, so the order cannot be scored (fail-closed)",
        )
    return float(value)


def limit_entry_fill(
    direction: str,
    limit_price: float,
    bars: Sequence[Mapping[str, Any]],
    *,
    timeout_bars: int,
    tick_size: float,
) -> LimitFillResult:
    """Whether a resting limit entry fills, on which bar, and at what price. Pure.

    ``bars`` are the bars the order RESTS across, oldest first — the first element is the
    bar after the signal bar, because the signal is computed on a close and nothing can rest
    inside the bar that produced it. The contract is positional on purpose: this function
    takes no timestamps and cannot re-derive the caller's alignment, so handing it the
    signal bar itself is the caller's bug, not a case it can detect.

    ``tick_size`` has no default for the same reason ``timeout_bars`` has none: it is the
    venue's number for one symbol, and a plausible-looking module constant here would be
    Binance-shaped and silently wrong per symbol. The future caller reads it from the venue
    filter it already fetches for order quantization.

    A LONG entry rests BELOW the market (a buy limit), so it fills when a bar's low trades
    through it; a SHORT rests above, on the high. Equality at exactly one tick through
    counts as filled — "at least one tick" includes the tick itself.
    """
    if direction not in (LONG, SHORT):
        raise ToolError(LIMIT_FILL_UNPRICEABLE, f"unknown direction {direction!r}")
    if isinstance(limit_price, bool) or not isinstance(limit_price, (int, float)) or not limit_price > 0:
        raise ToolError(LIMIT_FILL_UNPRICEABLE, f"limit price {limit_price!r} is not a positive number")
    if isinstance(tick_size, bool) or not isinstance(tick_size, (int, float)) or not tick_size > 0:
        raise ToolError(
            LIMIT_FILL_UNPRICEABLE,
            f"tick size {tick_size!r} is not a positive number; one tick is the whole "
            f"touch-versus-through distinction, so it cannot be guessed",
        )
    if isinstance(timeout_bars, bool) or not isinstance(timeout_bars, int) or timeout_bars < 1:
        raise ToolError(
            LIMIT_FILL_UNPRICEABLE,
            f"timeout_bars {timeout_bars!r} is not a positive bar count; the bar-based "
            f"cancel is precondition (3) and an order that never expires is not scoreable",
        )

    threshold = float(limit_price) - float(tick_size) if direction == LONG \
        else float(limit_price) + float(tick_size)

    scanned = 0
    for index, bar in enumerate(bars):
        if scanned >= timeout_bars:
            break
        if not isinstance(bar, Mapping):
            raise ToolError(LIMIT_FILL_UNPRICEABLE, f"bar {index} is not a mapping")
        scanned += 1
        if direction == LONG:
            traded_through = _price(bar, "low", index) <= threshold
        else:
            traded_through = _price(bar, "high", index) >= threshold
        if traded_through:
            return LimitFillResult(
                filled=True,
                fill_bar_index=index,
                entry_price=float(limit_price),
                bars_scanned=scanned,
                unfilled_reason=None,
            )

    reason = UNFILLED_CANCEL_TIMEOUT if scanned >= timeout_bars else UNFILLED_BARS_EXHAUSTED
    return LimitFillResult(
        filled=False,
        fill_bar_index=None,
        entry_price=None,
        bars_scanned=scanned,
        unfilled_reason=reason,
    )
