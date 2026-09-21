"""LP5.1 — live position state. **No orders, no network.**

The live sibling of the paper position book, and deliberately *not* a copy of it. This
increment holds only what can be built and tested with no venue: the record, its gated
store, the reconciliation of that store against a real account snapshot (``live_reconcile``
since crypto PR7d-2), and the open exposure the guard must be told truthfully. Nothing here can place, amend, or cancel an
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

Since crypto PR7d-2 the comparison itself (``reconcile_positions`` and its drift reasons) lives in
``live_reconcile``, the reconciliation layer. This module keeps the book, which the order path reads
and writes, and the verdicts (``RECONCILED``, ``DRIFT``, ``ACCOUNT_UNREADABLE``): its entry check reads
two of them back and ``live_route`` the third.
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
from .state import VENUE_MAINNET, venue_state_dir
from .vocabulary import (
    LIVE_TRADING_ENV,
    LIVE_TRADING_FLAGS,
    LIVE_TRADING_PROVIDER_ID,
    REAL_LIVE_TRADING,
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


# --- the record ---------------------------------------------------------------

def unbooked_position_id(*, symbol: str, entry_client_order_id: Any, opened_at: str) -> str:
    """The identity of a position that existed at the venue and was never written to the book.

    A naked close settles exactly that: an entry filled, could not be protected, and was closed
    again before :func:`build_live_position` ever ran, so there is no stored id to carry onto
    the outcome. Passing ``None`` instead is not free. ``build_live_outcome_record`` derives
    ``outcome_id`` from ``{position_id, closed_at, symbol}``, so two naked closes on one symbol
    that share a cycle timestamp derive the **same** id — and ``read_live_outcomes`` raises
    ``LIVE_HISTORY_DUPLICATE`` on that rather than return a history it cannot prove. One extra
    unaccounted round trip would make the whole live history unreadable, and every risk
    decision that reads it fails closed — on a duplicate this runtime minted itself.

    Seeded from the entry's client order id, which ``order_identity.make_client_order_id`` builds
    over an idempotency key and is therefore unique per submitted order. Unique per trade, and
    still a pure function of recorded facts, so a replay derives the same id.

    Kept beside :func:`build_live_position` rather than in the leg: an id for a live position is
    this module's to mint, whether or not the position survived long enough to be stored.
    """
    return integrity.short_id(
        "live_position",
        {
            "symbol": symbol,
            "entry_client_order_id": str(entry_client_order_id or ""),
            "at": opened_at,
            # Never booked, and the seed says so rather than leaving the id indistinguishable
            # from one a stored position could hold.
            "unbooked": True,
        },
    )


def position_risk_usdt(*, entry_price: Any, stop_loss: Any, quantity: Any) -> float:
    """What a position stands to lose to its stop, in quote terms. Pure.

    The entry↔stop distance times the size actually held — measured from the **filled** price,
    not the intended one, which is what makes it the same number the venue would take.

    0.0 when no stop is known, never a guess from the order cap: a fabricated risk produces a
    fabricated R, and R is what every breaker downstream is denominated in.

    Extracted from :func:`build_live_position` so the naked-close path can state a position's
    risk without booking one. Both callers must produce the identical figure for the same fill
    — an outcome row whose R was computed a second way is not comparable to the rows beside it.
    """
    entry = _f(entry_price)
    stop = _f(stop_loss)
    size = _f(quantity)
    if stop <= 0 or entry <= 0 or size <= 0:
        return 0.0
    return round(abs(entry - stop) * size, 8)


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
    strategy_artifact_sha256: str | None = None,
    cycle_id: str | None = None,
    timeframe: str | None = None,
    max_holding_bars: int | None = None,
    risk_snapshot_sha256: str | None = None,
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
        "risk": position_risk_usdt(entry_price=entry_price, stop_loss=stop, quantity=quantity),
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
        # The artifact the order was approved as (PR3a-2), from the intent the pre-order snapshot
        # bound; it rides to the outcome with the other three.
        "strategy_artifact_sha256": strategy_artifact_sha256,
        "cycle_id": cycle_id,
        # The pre-order snapshot the entry left under (PR2b); it rides to the outcome.
        "risk_snapshot_sha256": risk_snapshot_sha256,
        # --- the time exit (2026-07-29) ------------------------------------------------
        # Until now a live position carried no holding count and no timeframe, so the live
        # leg had nothing to judge a max-hold rule against and deliberately enforced none.
        # That made live and paper end trades on different rules while the promotion evidence
        # was built under paper's — and unfavourably, since a time exit usually cuts losers,
        # so live held them longer. These four fields are what closed that gap.
        #
        # `max_holding_bars` rides on the POSITION, not read from the spec at exit time: the
        # parity rule paper already enforces (`position_max_hold`) is that a position is judged
        # by the number its own backtest was built on. A spec edited mid-hold must not move the
        # exit of a trade already open.
        "timeframe": timeframe,
        "max_holding_bars": max_holding_bars,
        "holding_candles": 0,
        # Dedup key for the counter: one bar counts once however many times a cycle re-runs
        # within it. Paper learned this from the source system; live inherits it via the
        # shared `trade_plan.advance_holding` rather than by keeping a second copy of the rule.
        "last_counted_candle_ts": None,
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

def live_positions_dir(root: Path | None = None, *, venue: str = VENUE_MAINNET) -> Path:
    """One book per venue (PR1d-0). A testnet fill must never land in the live book: the leg
    reconciles the book it reads against the account of the venue it read it for, and a mixed
    book would report DRIFT on one venue for a position held on the other."""
    return venue_state_dir(root, venue=venue) / LIVE_POSITIONS_DIRNAME


def live_position_path(symbol: str, root: Path | None = None, *, venue: str = VENUE_MAINNET) -> Path:
    """Where this symbol's live book lives — containment-checked.

    The symbol reaches here from a schedule request and an exchange payload, so it is a
    caller-influenced path segment: the resolved file must stay inside the live positions
    directory. A symbol may name a book, never a location (the workspace-writer rule)."""
    if not symbol or not symbol.replace("_", "").replace("-", "").isalnum():
        raise ToolError("LIVE_POSITION_SYMBOL_INVALID", "live position symbol is not a valid book name")
    base = live_positions_dir(root, venue=venue)
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


def load_open_live_position(symbol: str, root: Path | None = None, *,
                            venue: str = VENUE_MAINNET) -> dict[str, Any] | None:
    """This symbol's OPEN live position at ``venue``, or None. Reading local state needs no gate."""
    return _read_position_file(live_position_path(symbol, root, venue=venue))


def list_open_live_positions(root: Path | None = None, *,
                             venue: str = VENUE_MAINNET) -> list[dict[str, Any]]:
    """Every OPEN position at ``venue``. Fails closed on an unreadable or unattributable record —
    counting exposure without one would understate what is really at stake."""
    directory = live_positions_dir(root, venue=venue)
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


def local_open_notional_usdt(root: Path | None = None, *, venue: str = VENUE_MAINNET) -> float:
    """Exposure as the LOCAL book believes it. Diagnostic only — the exposure the guard is
    told comes from the venue (`compute_open_notional_usdt`), because only the venue knows
    what is actually open."""
    return round(sum(_f(p.get("notional_usdt")) for p in list_open_live_positions(root, venue=venue)), 8)


# --- the gated store ----------------------------------------------------------

# The book holds one record per symbol. A write that would replace, or a clear that would remove,
# the record of ANOTHER position is refused (PR2b-2 review): two positions on one symbol are an
# incident for the operator, and silently keeping only one of them hides the other one.
LIVE_POSITION_SLOT_TAKEN = "LIVE_POSITION_SLOT_TAKEN"


class LivePositionStore(Protocol):
    """Mutating the live book is gated; reading it is not."""

    filesystem_write: bool

    def save_position(self, position: Mapping[str, Any]) -> None: ...
    def clear_position(self, symbol: str, *, position_id: Any = ...) -> None: ...


_ANY_POSITION = object()


def _readable_record(path: Path) -> dict[str, Any] | None:
    """The OPEN record at ``path`` if it can be read, else None. A record nobody can read cannot be
    shown to belong to another position, so it does not block the write (the pre-review behaviour)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("status") == "OPEN" else None


class DryRunLivePositionStore:
    """The default: touches nothing. Lets the whole LP5 flow run and be tested with no
    switch, no disk, and no venue. ``filesystem_write=False`` rides into every record so a
    dry run can never be read back as a real one."""

    provider_id = "dry_run"
    filesystem_write = False

    def save_position(self, position: Mapping[str, Any]) -> None:
        return None

    def clear_position(self, symbol: str, *, position_id: Any = None) -> None:
        return None


class RealLivePositionStore:
    """Durable book under this venue's ``live_positions/`` (``venue_state_dir``).

    Constructed only behind the Safety-Flag Gate for the one ``live_trading`` provider, and
    it re-asserts that authorization on **every** mutation — so clearing ``MVP_LIVE_TRADING``
    stops the book mid-flight exactly as it stops order egress and the P&L ledger. Writes
    are atomic (temp + replace) under a per-symbol cross-process lock, and fsynced: a live
    position that reached the disk buffer but not the disk would be a real position the
    runtime forgets across a crash.
    """

    provider_id = LIVE_TRADING_PROVIDER_ID
    filesystem_write = True

    def __init__(self, *, root: Path | None = None, authorization: Authorization | None = None,
                 venue: str = VENUE_MAINNET):
        self._root = root
        self._authorization = authorization
        # Which venue's book this store writes (PR1d-0). Default mainnet: an existing caller
        # keeps the book it always had.
        self._venue = venue

    def _assert(self) -> None:
        safety_gate.assert_authorization(
            self._authorization,
            required_flags=LIVE_TRADING_FLAGS,
            provider_id=self.provider_id,
            now=timeutil.utc_now_iso(),
        )

    def _write(self, path: Path, payload: Mapping[str, Any] | None, *, owner: Any = _ANY_POSITION) -> None:
        with locked(path.with_suffix(".lock"), code="LIVE_STATE_LOCKED", label="live position"):
            if owner is not _ANY_POSITION:
                held = _readable_record(path)
                if held is not None and held.get("position_id") != owner:
                    raise ToolError(
                        LIVE_POSITION_SLOT_TAKEN,
                        f"{path.stem} holds position {held.get('position_id')!r}, not {owner!r}",
                    )
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
        """Book ``position``, or update its own record. Never replaces another position's."""
        self._assert()
        self._write(live_position_path(position_symbol(position), self._root, venue=self._venue), position,
                    owner=position.get("position_id"))

    def clear_position(self, symbol: str, *, position_id: Any = _ANY_POSITION) -> None:
        """Remove the symbol's record. Given ``position_id``, only that position's record: one the
        symbol now holds for another position is left alone, and the call refuses."""
        self._assert()
        self._write(live_position_path(symbol, self._root, venue=self._venue), None, owner=position_id)


def select_live_position_store(
    *, now: str | None = None, root: Path | None = None
) -> LivePositionStore:
    """Return the durable live book if live trading is opted in, else the inert one.

    The capable store is still constructed **by** the gate, so it cannot exist before the
    authorization does. What changed on 2026-07-28 (Thomas) is what opens the gate: the opt-in
    ``MVP_LIVE_TRADING=real`` alone, no per-machine grant. It moves with the rest of the live
    surface and must — this book is what the close path reads to know a position exists. Durable
    orders over an inert book is an account holding positions the runtime cannot see to close.
    """
    return safety_gate.select_env_gated(
        env_var=LIVE_TRADING_ENV,
        opt_in_value=REAL_LIVE_TRADING,
        flags=LIVE_TRADING_FLAGS,
        provider_id=LIVE_TRADING_PROVIDER_ID,
        default_factory=DryRunLivePositionStore,
        gated_factory=lambda authorization: RealLivePositionStore(root=root, authorization=authorization),
    )


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
    "position_risk_usdt",
    "unbooked_position_id",
    "select_live_position_store",
]
