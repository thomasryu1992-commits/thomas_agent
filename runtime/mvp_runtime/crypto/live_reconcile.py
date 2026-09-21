"""Live position reconciliation: the venue is the truth (crypto PR7d-2).

`reconcile_positions` compares the book this runtime keeps (`live_position`) with what the venue's
account says, symbol by symbol, and names every disagreement: a position the venue no longer holds, one
the book does not know, a side or a quantity that differs. The verdicts it returns (`RECONCILED`,
`DRIFT`, `ACCOUNT_UNREADABLE`) stay in `live_position` as one vocabulary: the book's own entry check
(`entry_allowed`) reads `RECONCILED` and `ACCOUNT_UNREADABLE` on the order path, and `live_route`
reads `DRIFT`.

It lived in `live_position` beside the book, which placed the book in the reconciliation layer and made
every order-path reader of the book import upward. The book is the execution layer's own state; this
comparison is reconciliation. The drift names are imported from here: `live_position` sits below and
cannot re-export them. The records are stamped with the book's kernel version
(`LIVE_POSITION_KERNEL_VERSION`), as before the move, so a change to this comparison bumps it there.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..coerce import as_float as _f
from .account import AccountSnapshot
from .live_position import ACCOUNT_UNREADABLE, DRIFT, LIVE_POSITION_KERNEL_VERSION, RECONCILED, position_symbol


# Drift reasons — each names exactly what disagreed, so an operator reading the record
# does not have to diff two payloads by eye.
DRIFT_MISSING_AT_VENUE = "POSITION_MISSING_AT_VENUE"
DRIFT_UNTRACKED_AT_VENUE = "POSITION_UNTRACKED_AT_VENUE"
DRIFT_SIDE_MISMATCH = "POSITION_SIDE_MISMATCH"
DRIFT_QUANTITY_MISMATCH = "POSITION_QUANTITY_MISMATCH"

# Quantity comparison tolerance. Venue quantities arrive as strings and round-trip through
# float, so an exact == would report drift on a byte-identical position. Relative, with an
# absolute floor for very small sizes; anything larger is real drift (a partial fill).
_QTY_RELATIVE_TOLERANCE = 1e-6
_QTY_ABSOLUTE_TOLERANCE = 1e-9


# --- reconciliation: the venue is the truth ------------------------------------

def _quantities_agree(local: float, venue: float) -> bool:
    return abs(local - venue) <= max(_QTY_ABSOLUTE_TOLERANCE, _QTY_RELATIVE_TOLERANCE * abs(venue))


def reconcile_positions(
    local_positions: list[Mapping[str, Any]],
    snapshot: AccountSnapshot | None,
    *,
    now: str,
) -> dict[str, Any]:
    """Compare the local live book against the venue. Returns a reconciliation record.

    Three outcomes, and the only one that permits a new entry is a clean match:

    - ``RECONCILED`` — every local book matches a venue position on side and quantity, and
      the venue holds nothing the runtime is not tracking.
    - ``DRIFT`` — at least one disagreement. Each is named (missing at venue, untracked at
      venue, side mismatch, quantity mismatch) per symbol.
    - ``ACCOUNT_UNREADABLE`` — ``snapshot`` is None (the account read degraded). **Every**
      symbol is refused for new entries, because with no venue read the runtime cannot
      know what it holds.

    Refusal is scoped to **new entries only**; closes are never gated on reconciliation
    (the standing close-path exemption — a halt must not trap a position open). The caller
    reads ``entry_allowed`` per symbol, or the top-level ``entries_allowed``.

    Pure: it reads no file and opens no socket. The caller supplies both sides.
    """
    local_by_symbol: dict[str, Mapping[str, Any]] = {}
    for position in local_positions:
        local_by_symbol[position_symbol(position)] = position

    if snapshot is None:
        books = {
            symbol: {
                "symbol": symbol,
                "status": ACCOUNT_UNREADABLE,
                "reasons": [ACCOUNT_UNREADABLE],
                "entry_allowed": False,
                "local_quantity": _f(position.get("quantity")),
                "venue_quantity": None,
            }
            for symbol, position in sorted(local_by_symbol.items())
        }
        return {
            "reconcile_version": LIVE_POSITION_KERNEL_VERSION,
            "status": ACCOUNT_UNREADABLE,
            "entries_allowed": False,
            "closes_allowed": True,
            "books": books,
            "reasons": [ACCOUNT_UNREADABLE],
            "created_at": now,
        }

    venue_by_symbol = {p.symbol: p for p in snapshot.positions if p.symbol}
    books: dict[str, dict[str, Any]] = {}

    for symbol in sorted(set(local_by_symbol) | set(venue_by_symbol)):
        local = local_by_symbol.get(symbol)
        venue = venue_by_symbol.get(symbol)
        reasons: list[str] = []

        if local is not None and venue is None:
            # The venue closed it (stop, liquidation, manual close) and the runtime still
            # thinks it is open. Never silently cleared here: clearing a book is a write,
            # and this function performs none.
            reasons.append(DRIFT_MISSING_AT_VENUE)
        elif local is None and venue is not None:
            # Something is open that this runtime did not open, or opened and lost track
            # of. Entering again on that symbol would stack onto an unknown position.
            reasons.append(DRIFT_UNTRACKED_AT_VENUE)
        elif local is not None and venue is not None:
            if str(local.get("direction") or "").upper() != str(venue.side or "").upper():
                reasons.append(DRIFT_SIDE_MISMATCH)
            if not _quantities_agree(_f(local.get("quantity")), _f(venue.quantity)):
                reasons.append(DRIFT_QUANTITY_MISMATCH)

        books[symbol] = {
            "symbol": symbol,
            "status": RECONCILED if not reasons else DRIFT,
            "reasons": reasons,
            "entry_allowed": not reasons,
            "local_quantity": _f(local.get("quantity")) if local is not None else None,
            "venue_quantity": _f(venue.quantity) if venue is not None else None,
        }

    drifted = sorted(s for s, book in books.items() if book["reasons"])
    return {
        "reconcile_version": LIVE_POSITION_KERNEL_VERSION,
        "status": RECONCILED if not drifted else DRIFT,
        # A drifted book refuses entries for THAT symbol; the run-level flag is the
        # conjunction, so a caller that only checks the top level still fails closed.
        "entries_allowed": not drifted,
        "closes_allowed": True,
        "books": books,
        "reasons": sorted({r for book in books.values() for r in book["reasons"]}),
        "drifted_symbols": drifted,
        "created_at": now,
    }
