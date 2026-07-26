"""LP5.1 — live position state + venue reconciliation. **No orders, no network.**

The live sibling of the paper position book, and deliberately *not* a copy of it. This
increment holds only what can be built and tested with no venue: the record, its gated
store, the reconciliation of that store against a real account snapshot, and the open
exposure the guard must be told truthfully. Nothing here can place, amend, or cancel an
order — that capability lives in ``live_execution`` (LP4) and is not reached from this
module. Design: ``docs/runtime-contracts/LP5_POSITION_KERNEL_DESIGN_V0.1.md``.

**State separation is mandatory, not stylistic.** Paper keys its book
``(venue, symbol, timeframe)`` where ``venue`` defaults to ``binance_futures`` — the same
string a live position would carry — and ``paper.list_open_positions`` globs every
``*.json`` under ``positions/``. The paper record has no ``stage`` field. Sharing that
directory would let the **paper cycle settle a real position with simulated math and mark
it CLOSED**, or strand it behind ``POSITION_CONTEXT_MISMATCH``. So live positions live in
their own ``live_positions/`` namespace and every record is stamped ``stage: "live"``.
The books share no directory, no key, and no caps (paper's 20/4 are explicitly documented
as safe *only because* live and paper share no code; LP5 sets its own, far smaller).

**Keyed by symbol, because the venue is.** The open engineering question the design left
to implementation — one book per ``(symbol, timeframe)`` like paper, or one per symbol —
is settled by what reconciliation compares against: the venue nets per symbol in one-way
mode, so ``AccountSnapshot.positions`` has at most one row per symbol. Keying locally by
``(symbol, timeframe)`` would let two local books map onto that single venue row, and no
sound comparison could be made. The venue's shape wins.

**The venue is the truth: reconcile-or-refuse.** Paper cannot drift; live can — a partial
fill, a venue-side stop, a liquidation, or a manual close on the phone all move the
position without the runtime knowing. So a drifted book, or an account that cannot be
read, **refuses new entries for that book** and says so. Closes stay permitted: a halt
that traps a losing position open is worse than the halt prevents.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping, Protocol

from runtime.read_only_kernel import integrity

from .. import safety_gate, timeutil
from ..errors import ToolError
from ..filelock import locked
from ..safety_gate import Authorization
from .account import AccountSnapshot
from ..coerce import as_float as _f
from .live_pnl import (
    LIVE_TRADING_ENV,
    LIVE_TRADING_FLAGS,
    LIVE_TRADING_PROVIDER_ID,
    REAL_LIVE_TRADING,
    state_dir,
)

LIVE_POSITION_KERNEL_VERSION = "live_position_kernel.v0.1"

# The distinct namespace. Never `positions/` — see the module docstring.
LIVE_POSITIONS_DIRNAME = "live_positions"
# Stamped on every record so a live position is identifiable even if one were ever copied
# somewhere else. Paper records carry no `stage`, so presence alone is a live marker.
LIVE_STAGE = "live"

# LP5's own caps, deliberately far below paper's 20/4. With the approved starting boundary
# (60 USDT per order, 120 USDT open exposure) two positions is the most that fits, and the
# venue nets per symbol in one-way mode, so a second book on one symbol cannot exist.
MAX_LIVE_CONCURRENT_POSITIONS = 2
MAX_LIVE_POSITIONS_PER_SYMBOL = 1

# Reconciliation verdicts.
RECONCILED = "RECONCILED"
DRIFT = "DRIFT"
ACCOUNT_UNREADABLE = "ACCOUNT_UNREADABLE"

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

# What an unreadable account means for the exposure cap. Not 0.0 — see
# `compute_open_notional_usdt`.
EXPOSURE_UNKNOWN_AT_CAP = "EXPOSURE_UNKNOWN_TREATED_AS_AT_CAP"


# --- the record ---------------------------------------------------------------

def build_live_position(
    *,
    symbol: str,
    direction: str,
    quantity: float,
    entry_price: float,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    opened_at: str,
    entry_client_order_id: str | None = None,
    entry_exchange_order_id: Any = None,
    strategy_id: str | None = None,
    candidate_id: str | None = None,
    strategy_rule_hash: str | None = None,
    strategy_generation_id: str | None = None,
    cycle_id: str | None = None,
) -> dict[str, Any]:
    """One OPEN live position, from a real fill. Pure — persisting it is the store's job.

    ``entry_price`` and ``quantity`` are the venue's **actual fill**, never the intent's
    request: the book tracks what happened, not what was asked for. That is also what
    makes reconciliation meaningful — comparing an intent against the venue would report
    drift on every partial fill.

    The order-id fields are accepted (and default to None) because LP5.3 fills them from
    the LP4 submit result; nothing in this increment produces them.
    """
    direction = str(direction).upper()
    if direction not in {"LONG", "SHORT"}:
        raise ToolError("MALFORMED_DIRECTION", "a live position needs an explicit LONG or SHORT")
    if not symbol:
        raise ToolError("MISSING_SYMBOL", "a live position needs a symbol")
    quantity = _f(quantity)
    entry_price = _f(entry_price)
    if quantity <= 0:
        raise ToolError("MISSING_POSITION_QUANTITY", "a live position needs a positive filled quantity")
    if entry_price <= 0:
        raise ToolError("MISSING_ENTRY_PRICE", "a live position needs a positive fill price")

    stop = _f(stop_loss) if stop_loss is not None else 0.0
    position = {
        "position_kernel_version": LIVE_POSITION_KERNEL_VERSION,
        "status": "OPEN",
        # The live marker. Paper records have no such field, so this is unambiguous.
        "stage": LIVE_STAGE,
        "symbol": symbol,
        "direction": direction,
        "quantity": quantity,
        "entry_price": entry_price,
        # Notional at entry, the figure the exposure cap is denominated in.
        "notional_usdt": round(quantity * entry_price, 8),
        "stop_loss": stop,
        "take_profit": _f(take_profit) if take_profit is not None else 0.0,
        # Risk in quote terms, 0.0 when no stop is known — never guessed from the cap.
        "risk": round(abs(entry_price - stop) * quantity, 8) if stop > 0 else 0.0,
        "opened_at_utc": opened_at,
        "entry_client_order_id": entry_client_order_id,
        "entry_exchange_order_id": entry_exchange_order_id,
        # Attribution rides to the outcome so a live result can be credited to the lineage
        # that produced it (the bridge itself is LP5.4).
        "strategy_id": strategy_id,
        "candidate_id": candidate_id,
        "strategy_rule_hash": strategy_rule_hash,
        # The third lineage field the exit reads (`live_leg` -> build_live_outcome_record) and
        # the one the paper position has always carried. Without it the outcome's generation is
        # None however faithfully the other two travel.
        "strategy_generation_id": strategy_generation_id,
        "cycle_id": cycle_id,
    }
    position["position_id"] = integrity.short_id(
        "live_position",
        {"symbol": symbol, "entry": str(entry_price), "qty": str(quantity), "at": opened_at},
    )
    return position


def position_symbol(position: Mapping[str, Any]) -> str:
    """The book a stored live position belongs to, or fail closed.

    A position that cannot say what it trades cannot be reconciled against the venue and
    must not be silently excluded from the exposure count (paper learned this as
    ``POSITION_CONTEXT_MISMATCH``)."""
    symbol = str(position.get("symbol") or "")
    if not symbol:
        raise ToolError(
            "LIVE_POSITION_UNATTRIBUTABLE",
            f"live position {position.get('position_id')!r} is OPEN but names no symbol",
        )
    return symbol


# --- paths + reads (ungated: reading local state is not a capability) ----------

def live_positions_dir(root: Path | None = None) -> Path:
    return state_dir(root) / LIVE_POSITIONS_DIRNAME


def live_position_path(symbol: str, root: Path | None = None) -> Path:
    """Where this symbol's live book lives — containment-checked.

    The symbol reaches here from a schedule request and an exchange payload, so it is a
    caller-influenced path segment: the resolved file must stay inside the live positions
    directory. A symbol may name a book, never a location (the workspace-writer rule)."""
    if not symbol or not symbol.replace("_", "").replace("-", "").isalnum():
        raise ToolError("LIVE_POSITION_SYMBOL_INVALID", "live position symbol is not a valid book name")
    base = live_positions_dir(root)
    base.mkdir(parents=True, exist_ok=True)
    resolved_base = base.resolve()
    path = (resolved_base / f"{symbol}.json").resolve()
    if path.parent != resolved_base:
        raise ToolError(
            "LIVE_POSITION_SYMBOL_INVALID",
            f"live position symbol {symbol!r} resolves outside the live positions directory",
        )
    return path


def _read_position_file(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # An unreadable live position cannot honestly mean "no position": refuse rather
        # than trade over a real one (the paper POSITION_STATE_UNREADABLE posture).
        raise ToolError(
            "LIVE_POSITION_STATE_UNREADABLE", f"live position file unreadable: {type(exc).__name__}"
        ) from exc
    if not isinstance(data, dict) or data.get("status") != "OPEN":
        return None
    if data.get("stage") != LIVE_STAGE:
        # A record without the live marker in the live namespace is not something to guess
        # about — it may be a paper record copied by hand.
        raise ToolError(
            "LIVE_POSITION_STAGE_MISMATCH",
            f"record in {LIVE_POSITIONS_DIRNAME}/ is not stamped stage={LIVE_STAGE!r}",
        )
    return data


def load_open_live_position(symbol: str, root: Path | None = None) -> dict[str, Any] | None:
    """This symbol's OPEN live position, or None. Reading local state needs no gate."""
    return _read_position_file(live_position_path(symbol, root))


def list_open_live_positions(root: Path | None = None) -> list[dict[str, Any]]:
    """Every OPEN live position. Fails closed on an unreadable or unattributable record —
    counting exposure without one would understate what is really at stake."""
    directory = live_positions_dir(root)
    if not directory.is_dir():
        return []
    found: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        position = _read_position_file(path)
        if position is None:
            continue
        position_symbol(position)  # fail closed on an unattributable record
        found.append(position)
    return found


def local_open_notional_usdt(root: Path | None = None) -> float:
    """Exposure as the LOCAL book believes it. Diagnostic only — the exposure the guard is
    told comes from the venue (`compute_open_notional_usdt`), because only the venue knows
    what is actually open."""
    return round(sum(_f(p.get("notional_usdt")) for p in list_open_live_positions(root)), 8)


# --- the gated store ----------------------------------------------------------

class LivePositionStore(Protocol):
    """Mutating the live book is gated; reading it is not."""

    filesystem_write: bool

    def save_position(self, position: Mapping[str, Any]) -> None: ...
    def clear_position(self, symbol: str) -> None: ...


class DryRunLivePositionStore:
    """The default: touches nothing. Lets the whole LP5 flow run and be tested with no
    grant, no disk, and no venue. ``filesystem_write=False`` rides into every record so a
    dry run can never be read back as a real one."""

    provider_id = "dry_run"
    filesystem_write = False

    def save_position(self, position: Mapping[str, Any]) -> None:
        return None

    def clear_position(self, symbol: str) -> None:
        return None


class RealLivePositionStore:
    """Durable live book under ``.runtime_governance_state/crypto/live_positions/``.

    Constructed only behind the Safety-Flag Gate for the one ``live_trading`` provider, and
    it re-asserts that authorization on **every** mutation — so deleting the grant file
    stops the book mid-flight exactly as it stops order egress and the P&L ledger. Writes
    are atomic (temp + replace) under a per-symbol cross-process lock, and fsynced: a live
    position that reached the disk buffer but not the disk would be a real position the
    runtime forgets across a crash.
    """

    provider_id = LIVE_TRADING_PROVIDER_ID
    filesystem_write = True

    def __init__(self, *, root: Path | None = None, authorization: Authorization | None = None):
        self._root = root
        self._authorization = authorization

    def _assert(self) -> None:
        safety_gate.assert_authorization(
            self._authorization,
            required_flags=LIVE_TRADING_FLAGS,
            provider_id=self.provider_id,
            now=timeutil.utc_now_iso(),
        )

    def _write(self, path: Path, payload: Mapping[str, Any] | None) -> None:
        with locked(path.with_suffix(".lock"), code="LIVE_STATE_LOCKED", label="live position"):
            if payload is None:
                path.unlink(missing_ok=True)
                return
            tmp = path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(dict(payload), ensure_ascii=False, indent=1))
                handle.flush()
                os.fsync(handle.fileno())
            tmp.replace(path)

    def save_position(self, position: Mapping[str, Any]) -> None:
        self._assert()
        self._write(live_position_path(position_symbol(position), self._root), position)

    def clear_position(self, symbol: str) -> None:
        self._assert()
        self._write(live_position_path(symbol, self._root), None)


def select_live_position_store(
    *, now: str | None = None, root: Path | None = None
) -> LivePositionStore:
    """Return the durable live book if the live-trading grant is open, else the inert one.

    The capable store is constructed **by** the gate, so it cannot exist before the
    authorization does; ``MVP_LIVE_TRADING=real`` without a valid local grant fails closed.
    """
    return safety_gate.select_gated(
        env_var=LIVE_TRADING_ENV,
        opt_in_value=REAL_LIVE_TRADING,
        flags=LIVE_TRADING_FLAGS,
        provider_id=LIVE_TRADING_PROVIDER_ID,
        default_factory=DryRunLivePositionStore,
        gated_factory=lambda authorization: RealLivePositionStore(root=root, authorization=authorization),
        now=now,
        root=root,
    )


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


def entry_allowed(reconciliation: Mapping[str, Any], symbol: str) -> bool:
    """May a NEW entry be opened on this symbol, per this reconciliation? Fail-closed.

    An unknown symbol in a RECONCILED record is allowed (nothing is open there and the
    venue agrees); anything else — a drifted book, an unreadable account, or a malformed
    record — refuses."""
    if not isinstance(reconciliation, Mapping):
        return False
    if reconciliation.get("status") == ACCOUNT_UNREADABLE:
        return False
    books = reconciliation.get("books")
    if not isinstance(books, Mapping):
        return False
    book = books.get(symbol)
    if book is None:
        return reconciliation.get("status") == RECONCILED
    return bool(isinstance(book, Mapping) and book.get("entry_allowed"))


# --- open exposure: the fail-open default, closed ------------------------------

def compute_open_notional_usdt(snapshot: AccountSnapshot | None, *, at_cap: float) -> float:
    """The open exposure the live-order guard must be told — from the **venue**.

    ``evaluate_live_order_guard`` used to default this to ``0.0``, the single fail-open
    path in an otherwise fail-closed guard: a caller that omitted it silently disabled the
    exposure cap. This is the truthful supplier, and it fails **closed** — an unreadable
    account (``snapshot is None``) reports ``at_cap``, so the cap refuses rather than
    admits. Zero exposure is only ever reported when the venue actually says the account
    is flat.

    ``at_cap`` is the configured open-exposure ceiling, passed in rather than read here so
    this stays pure and the caller keeps one source for its limits.
    """
    if snapshot is None:
        return max(0.0, _f(at_cap))
    return round(sum(abs(_f(p.notional)) for p in snapshot.positions), 8)


def live_capacity(local_positions: list[Mapping[str, Any]], *, symbol: str) -> dict[str, Any]:
    """Whether LP5's own concurrency caps admit a new position on ``symbol``.

    Separate from the budget's notional caps: these bound how many positions may be open
    at once, not how large they are. Deliberately far below paper's 20/4 — and per-symbol
    is 1 because the venue nets per symbol in one-way mode, so a second book on one symbol
    could not correspond to anything real."""
    open_count = len(local_positions)
    same_symbol = sum(1 for p in local_positions if str(p.get("symbol") or "") == symbol)
    blocks: list[str] = []
    if open_count >= MAX_LIVE_CONCURRENT_POSITIONS:
        blocks.append("LIVE_MAX_CONCURRENT_POSITIONS")
    if same_symbol >= MAX_LIVE_POSITIONS_PER_SYMBOL:
        blocks.append("LIVE_MAX_POSITIONS_PER_SYMBOL")
    return {
        "symbol": symbol,
        "open_positions": open_count,
        "same_symbol_positions": same_symbol,
        "max_concurrent": MAX_LIVE_CONCURRENT_POSITIONS,
        "max_per_symbol": MAX_LIVE_POSITIONS_PER_SYMBOL,
        "allowed": not blocks,
        "blocks": blocks,
    }


__all__ = [
    "ACCOUNT_UNREADABLE",
    "DRIFT",
    "LIVE_POSITIONS_DIRNAME",
    "LIVE_STAGE",
    "MAX_LIVE_CONCURRENT_POSITIONS",
    "MAX_LIVE_POSITIONS_PER_SYMBOL",
    "RECONCILED",
    "DryRunLivePositionStore",
    "LivePositionStore",
    "RealLivePositionStore",
    "build_live_position",
    "compute_open_notional_usdt",
    "entry_allowed",
    "list_open_live_positions",
    "live_capacity",
    "live_position_path",
    "live_positions_dir",
    "load_open_live_position",
    "local_open_notional_usdt",
    "reconcile_positions",
    "select_live_position_store",
]
