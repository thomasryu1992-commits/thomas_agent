"""LP5.3 — the venue's own trading rules for one symbol. Read-only; nothing here trades.

LP5.2 sized orders against a :class:`~.live_sizing.SymbolFilters` it could not obtain, so
every call refused: *"LP5.3 supplies real filters from a venue read; until then every call
refuses, which is the honest state."* This is that read.

**The numbers must come from the venue, never from memory.** Lot step, minimum quantity,
minimum notional and price tick differ per symbol and change over time, and an unrounded
size or stop price is rejected outright. So this module parses them out of the venue's
``exchangeInfo`` payload and **refuses when it cannot** — no defaults, no fallbacks, no
"reasonable" constants. A refusal costs one skipped entry; an invented lot step costs a
rejected order at best and a wrong-sized real position at worst.

**Both lot filters apply, and the stricter wins.** A MARKET order is bound by
``MARKET_LOT_SIZE`` *in addition to* ``LOT_SIZE``, and the two can differ. LP5 entries and
closes are MARKET orders, so the effective step/minimum is the larger of the pair and the
effective maximum the smaller — computed here rather than discovered as a venue rejection.

**Degrade, never block.** A failed or absent read returns ``(None, reason_code)``; sizing
then refuses and the entry simply does not happen. That is the market-data posture
(``MARKET_DATA_DEGRADED``) applied to the one input whose absence must not be papered over.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..errors import ToolBlocked, ToolError
from .live_sizing import SymbolFilters

FILTERS_VERSION = "live_filters.v0.1"

# Reason codes — each says exactly why no filters are available, so a refused entry is
# actionable rather than a bare "no".
FILTERS_UNAVAILABLE = "LIVE_FILTERS_UNAVAILABLE"        # collector cannot read exchangeInfo
FILTERS_DEGRADED = "LIVE_FILTERS_DEGRADED"              # the read failed at run time
FILTERS_SYMBOL_UNKNOWN = "LIVE_FILTERS_SYMBOL_UNKNOWN"  # venue lists no such symbol
FILTERS_SYMBOL_NOT_TRADING = "LIVE_FILTERS_SYMBOL_NOT_TRADING"
FILTERS_INCOMPLETE = "LIVE_FILTERS_INCOMPLETE"          # a required filter is missing

# Venue filter names, from the exchangeInfo contract.
_LOT_SIZE = "LOT_SIZE"
_MARKET_LOT_SIZE = "MARKET_LOT_SIZE"
_MIN_NOTIONAL = "MIN_NOTIONAL"
_PRICE_FILTER = "PRICE_FILTER"

_TRADING = "TRADING"


def _f(value: Any) -> float:
    """Venue numbers arrive as strings. An unparseable one is 0.0, and 0.0 is 'unknown',
    which :meth:`SymbolFilters.valid` treats as unusable — never as unrestricted."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _filters_by_type(row: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    found: dict[str, Mapping[str, Any]] = {}
    for entry in row.get("filters") or []:
        if isinstance(entry, Mapping):
            name = entry.get("filterType")
            if isinstance(name, str):
                found[name] = entry
    return found


def parse_symbol_filters(
    payload: Mapping[str, Any], symbol: str
) -> tuple[SymbolFilters | None, str | None]:
    """One symbol's trading rules from an ``exchangeInfo`` payload. Pure — no network.

    Returns ``(filters, reason_code)``: exactly one is ever set. The parsing is separate
    from the read precisely so these venue semantics can be tested exhaustively against
    recorded payloads with no socket and no grant.

    Refuses on: an unknown symbol, a symbol not in ``TRADING`` status (a delisted or
    halted symbol must not be sized, let alone ordered), and any missing or non-positive
    required increment.
    """
    if not isinstance(payload, Mapping):
        return None, FILTERS_INCOMPLETE
    rows = payload.get("symbols")
    if not isinstance(rows, list):
        return None, FILTERS_INCOMPLETE

    row = next(
        (r for r in rows if isinstance(r, Mapping) and str(r.get("symbol") or "") == symbol),
        None,
    )
    if row is None:
        return None, FILTERS_SYMBOL_UNKNOWN
    if str(row.get("status") or "") != _TRADING:
        return None, FILTERS_SYMBOL_NOT_TRADING

    found = _filters_by_type(row)
    lot = found.get(_LOT_SIZE)
    market_lot = found.get(_MARKET_LOT_SIZE)
    notional = found.get(_MIN_NOTIONAL)
    price = found.get(_PRICE_FILTER)
    if lot is None or notional is None or price is None:
        return None, FILTERS_INCOMPLETE

    # The stricter of the two lot filters binds a MARKET order: the LARGER step and
    # minimum, the SMALLER maximum. MARKET_LOT_SIZE is optional, so its absence simply
    # leaves LOT_SIZE binding.
    step = _f(lot.get("stepSize"))
    min_qty = _f(lot.get("minQty"))
    max_qty = _f(lot.get("maxQty"))
    if market_lot is not None:
        step = max(step, _f(market_lot.get("stepSize")))
        min_qty = max(min_qty, _f(market_lot.get("minQty")))
        market_max = _f(market_lot.get("maxQty"))
        if market_max > 0:
            max_qty = min(max_qty, market_max) if max_qty > 0 else market_max

    filters = SymbolFilters(
        step_size=step,
        min_qty=min_qty,
        min_notional=_f(notional.get("notional")),
        tick_size=_f(price.get("tickSize")),
        max_qty=max_qty,
    )
    if not filters.valid():
        # A zero increment is "unknown", not "unrestricted" — see SymbolFilters.valid.
        return None, FILTERS_INCOMPLETE
    if filters.tick_size <= 0:
        # The bracket's stop and target prices need the tick; without it LP5.3 could only
        # send an unrounded price, which the venue rejects.
        return None, FILTERS_INCOMPLETE
    return filters, None


def read_symbol_filters(
    collector: Any, symbol: str, *, timeout_seconds: int = 10
) -> tuple[SymbolFilters | None, str | None]:
    """This symbol's venue filters via ``collector.exchange_info``, or a reason code.

    Degrades rather than raising, on every path:

    - a collector with no ``exchange_info`` (the mock, or any network-free collector)
      yields ``FILTERS_UNAVAILABLE`` — deliberately, so a mock run refuses to size rather
      than sizing on fabricated numbers;
    - a transport or parse failure yields ``FILTERS_DEGRADED``;
    - a payload that does not describe this symbol yields the parse reason.

    Every one of those ends the same way downstream: no filters, so ``size_live_order``
    refuses, so no entry. The distinction exists for the operator reading the record.
    """
    reader = getattr(collector, "exchange_info", None)
    if not callable(reader):
        return None, FILTERS_UNAVAILABLE
    try:
        payload = reader(timeout_seconds=timeout_seconds)
    except (ToolError, ToolBlocked):
        return None, FILTERS_DEGRADED
    return parse_symbol_filters(payload, symbol)


__all__ = [
    "FILTERS_DEGRADED",
    "FILTERS_INCOMPLETE",
    "FILTERS_SYMBOL_NOT_TRADING",
    "FILTERS_SYMBOL_UNKNOWN",
    "FILTERS_UNAVAILABLE",
    "FILTERS_VERSION",
    "parse_symbol_filters",
    "read_symbol_filters",
]
