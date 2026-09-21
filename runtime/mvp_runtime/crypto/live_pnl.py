"""LP2 live P&L ledger and daily-loss circuit breaker (source L1).

The money-side counterpart to the paper outcome store: what a *real* closed live position
actually cost or earned, in USDT, and whether today's realized loss has reached the limit
that halts new entries. Ported from the source system's ``execution/live_pnl_ledger.py``.

Nothing here places, closes, or even sees an order. It records outcomes and answers one
question — "is the breaker tripped?" — which the order guard (LP3) then obeys. Building the
ledger before anything can trade is deliberate: the breaker must already work on the day the
first live order becomes possible, not be wired up afterwards.

**The unconfigured limit is a breach.** ``daily_loss_limit_breached(None)`` and
``daily_loss_limit_breached(0)`` both return True. A missing risk limit is the most dangerous
possible state, so it reads as "halted", never as "unlimited" — the source system encoded the
same rule and it is the single most important line in this module.

The ledger write rides the **one live-trading switch**, the same switch that authorizes order
egress. One switch means the capability cannot be half-enabled: turning it on enables live
trading, turning it off revokes the whole capability at once — including the ability to append
to this ledger. Since 2026-07-28 that switch is the environment alone
(``MVP_LIVE_TRADING=real``); see the constant block below.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol


from .. import safety_gate, timeutil
from ..errors import ToolError
from ..filelock import locked
from ..safety_gate import Authorization

LIVE_LEDGER_TOOL_ID = "crypto.live.pnl_ledger"
LIVE_LEDGER_TOOL_VERSION = "0.1.0"

# The live-trading opt-in's names, the R-basis labels, the stop-exit reasons and `utc_day` live in
# `vocabulary` since crypto PR7b-2, below every layer that reads them. They are re-exported here, as
# the same objects, for this module's importers.
from .vocabulary import (  # noqa: E402,F401
    LIVE_TRADING_ENV,
    LIVE_TRADING_FLAGS,
    LIVE_TRADING_PROVIDER_ID,
    R_BASES_NET_OF_COSTS,
    R_BASIS_FILLED,
    R_BASIS_INTENT,
    R_BASIS_INTENT_NET,
    REAL_LIVE_TRADING,
    STOP_EXIT_REASONS,
    utc_day,
)

from .state import STATE_REL, VENUE_MAINNET, state_dir, venue_state_dir  # noqa: E402  (one root for both trading planes; re-exported for the live-plane importers)
# The ledger's readers live in `live_ledger` (store) and the outcome-row builder in `live_settlement`
# (execution) since crypto PR7d-3; both are re-exported here, as the same objects, for this module's
# writer and its importers. `_approvals_for` is not: the corrected read looks it up in `live_ledger`,
# so a patch on this module would miss it, and without the name here such a patch fails loudly.
from .live_ledger import (  # noqa: E402,F401
    LIVE_HISTORY_DUPLICATE,
    LIVE_HISTORY_TAMPERED,
    LIVE_HISTORY_UNREADABLE,
    LIVE_OUTCOMES_FILENAME,
    UNKNOWN_R,
    live_outcomes_for_analysis,
    read_live_outcomes,
    read_live_outcomes_raw,
    stop_slippage_observations,
)
from .live_settlement import LIVE_PROVENANCE, build_live_outcome_record, realized_stop_slippage_bps  # noqa: E402,F401


# --- LP5.4: the outcome bridge -------------------------------------------------


def excluded_outcomes_digest(excluded: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """A FIXED-SIZE account of what :func:`live_outcomes_for_analysis` dropped, for the ledger.

    `live_analysis_summary` below carries both row lists whole. That is right for a caller
    holding them in memory and wrong for a record appended every cycle: the readable side alone
    is the entire live history, restated every fifteen minutes for as long as the runtime runs.
    This keeps four keys whose size does not depend on how many rows were dropped. The rows stay
    recoverable without them — this module is pure and reads an append-only file, so any later
    reader reproduces the exact ids by running the split again over `live_outcomes.jsonl`.

    **Net and gross, both.** Two dropped rows that cancel are not "no money left the R
    statistics", and a single figure of `+0.0090` over `-0.0081` and `+0.0171` says they are.
    The same distinction `_pnl_agrees_with_prices` draws between a number that is zero and one
    nobody could compute.

    Either amount is ``None`` when any row's is missing or non-numeric, following the rule the
    rest of this module already follows: a figure that could not be computed is not a zero.
    A missing amount does not blank the ``count`` or the ``reasons`` — those are still known,
    and the reason this row was dropped is the thing the operator is reading.
    """
    rows = list(excluded)
    reasons: dict[str, int] = {}
    for row in rows:
        key = str(row.get("reason") or UNKNOWN_R)
        reasons[key] = reasons.get(key, 0) + 1
    net: float | None = 0.0
    gross: float | None = 0.0
    for row in rows:
        amount = row.get("realized_pnl_usdt")
        if not isinstance(amount, (int, float)) or isinstance(amount, bool):
            net = gross = None
            break
        net += float(amount)
        gross += abs(float(amount))
    return {
        "count": len(rows),
        "realized_pnl_usdt": None if net is None else round(net, 8),
        "abs_realized_pnl_usdt": None if gross is None else round(gross, 8),
        "reasons": reasons,
    }


def live_analysis_summary(
    outcomes: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """What the live stream contributes to the R-based view, and what it could not.

    Deliberately a **separate** figure rather than something folded into the paper report:
    silently adding live trades to a paper expectancy would change what a previously
    reported number means. Whether the two streams are ever merged for a given decision is
    the caller's call, and it can only be made honestly if both counts are visible.
    """
    readable, excluded = live_outcomes_for_analysis(outcomes)
    return {
        "readable_count": len(readable),
        "excluded_count": len(excluded),
        "excluded": excluded,
        "readable": readable,
    }


def daily_realized_pnl(outcomes: Iterable[Mapping[str, Any]], *, day: str | None = None) -> float:
    """Sum of realized live P&L for one UTC day, in USDT."""
    target = day or utc_day()
    total = 0.0
    for record in outcomes:
        stamp = record.get("closed_at_utc") or record.get("created_at_utc") or ""
        if str(stamp)[:10] != target:
            continue
        try:
            total += float(record.get("realized_pnl_usdt") or 0.0)
        except (TypeError, ValueError):
            # A malformed amount must not be read as zero profit — that would understate a
            # loss and could clear a breaker that should be tripped.
            raise ToolError(
                LIVE_HISTORY_TAMPERED,
                f"live outcome {record.get('outcome_id')} has a non-numeric realized_pnl_usdt",
            ) from None
    return round(total, 8)


def daily_loss_limit_breached(
    limit_usdt: float | None,
    *,
    outcomes: Iterable[Mapping[str, Any]] | None = None,
    day: str | None = None,
    root: Path | None = None,
) -> bool:
    """Has today's realized live loss reached the configured limit?

    **An unconfigured limit counts as breached.** ``None``, ``0`` and any negative value all
    return True. Trading with no loss limit is the state this whole module exists to prevent,
    so the absence of a limit halts entries rather than permitting unlimited ones.
    """
    if limit_usdt is None:
        return True
    try:
        limit = float(limit_usdt)
    except (TypeError, ValueError):
        return True
    if limit <= 0:
        return True
    rows = list(outcomes) if outcomes is not None else read_live_outcomes(root)
    return daily_realized_pnl(rows, day=day) <= -abs(limit)


PNL_SOURCE_VENUE = "venue"
# The two windows the venue figure is taken from. Named here rather than spelled as literals
# at each call site: which windows the breaker consults is a safety rule, not a lookup key.
PNL_WINDOW_TODAY = "today"
PNL_WINDOW_ROLLING_1D = "1d"
PNL_SOURCE_LOCAL_LEDGER = "local_ledger"

# The breaker read a ledger nothing writes on the only path that can place an order.
LIVE_PNL_NO_SOURCE = "LIVE_PNL_NO_SOURCE"
# An entry path asked for the venue's own figure and the account read carried none — the income
# call failed, or its page came back full and the windows were withheld as possibly truncated,
# while the balance/positions call succeeded. The breaker reads that as TRIPPED on those paths
# (`live_risk_snapshot(venue_required=True)`), never as the local ledger's reading: see there.
LIVE_PNL_VENUE_FIGURE_MISSING = "LIVE_PNL_VENUE_FIGURE_MISSING"


def venue_daily_realized_net(realized_windows: Mapping[str, Any] | None) -> float | None:
    """The venue figure the daily-loss breaker measures against, or ``None`` if unreadable.

    The **stricter** (most negative) of the UTC calendar day and the rolling 24 hours, and
    the choice is not fussiness. Neither window dominates the other, because these are NET
    sums: a wider window also picks up the wider window's profits, so the rolling figure can
    be *less* negative than the calendar day. Yesterday 23:00 +50, today 01:00 -30 reads as
    -30 on the calendar day and +20 on the rolling 24h — today's loss hidden behind
    yesterday's profit, on the one measure that is supposed to stop the day.

    Taking the stricter of the two means the limit cannot be escaped by which 24 hours it is
    measured over, and needs no ruling about which window is "really" a day. It can only make
    the breaker trip earlier, never later — the direction a money guard is allowed to be wrong
    in. (The rest of the runtime means the calendar day when it says daily:
    ``live_order.count_today`` counts the daily order cap on ``utc_day()``.)

    A window that is missing or unparseable is skipped rather than read as zero; if neither
    is readable the caller gets ``None``. An entry path then refuses (``venue_required``); only
    a reporting caller falls back to the local-ledger path, which has its own no-source rule.
    """
    if not isinstance(realized_windows, Mapping):
        return None
    candidates: list[float] = []
    for key in (PNL_WINDOW_TODAY, PNL_WINDOW_ROLLING_1D):
        bucket = realized_windows.get(key)
        if not isinstance(bucket, Mapping):
            continue
        net = bucket.get("net")
        if isinstance(net, (int, float)) and not isinstance(net, bool):
            candidates.append(float(net))
    return min(candidates) if candidates else None


def live_risk_snapshot(
    *,
    limit_usdt: float | None,
    day: str | None = None,
    root: Path | None = None,
    now: str | None = None,
    venue_realized_pnl_usdt: float | None = None,
    venue_required: bool = False,
) -> dict[str, Any]:
    """Today's live risk state, for the guard, the dashboard, and the operator.

    Reads fail-closed: if the history cannot be verified the snapshot reports the breaker as
    tripped and names the reason, rather than reporting a comfortable zero.

    ``venue_realized_pnl_usdt``, when given, is the authority for the day and the local ledger
    is not consulted for the figure. It exists because the local ledger was not merely stale —
    it was **empty by construction**. The only writer is ``live_leg.execute_live_exit``, the
    autonomous leg no entry point may import; the canary path is entry-only and its positions
    are closed by the operator on the venue, so no closed outcome could ever reach the ledger.
    The breaker therefore reported ``0.0, not breached`` with total confidence while the venue
    reported a real realized loss for the same day. ``cycle.py`` states the rule this broke:
    *a breaker that cannot trip is not a breaker.*

    The venue figure covers the whole account rather than only this runtime's trades, and that
    is the safe direction for a loss limit: it can make the breaker trip earlier, never later.
    It is also the truer number — it is what the venue actually took, fees and funding included.

    ``pnl_source`` names where the figure came from, so a caller can tell "0.0 because nothing
    was lost" from "0.0 because nothing was recorded". ``LIVE_PNL_NO_SOURCE`` marks the second
    case explicitly — the distinction the retired canary count made by returning its error
    alongside its count.

    ``venue_required`` is for the callers that are about to OPEN a position — the autonomous
    leg and the probe door (and the canary door, until it was removed 2026-09-15) — and for the
    board once it has read the account on their behalf. For them a missing venue figure is a
    trip (``LIVE_PNL_VENUE_FIGURE_MISSING``), not a fall back to the local ledger. Found
    2026-09-15 (execution-authority audit, verified): the account read can succeed while its
    income call fails or comes back as a full page, and the local ledger then answers for a
    venue it cannot see — venue-side and operator-side closes never reach it — so "no closed
    row today" read as 0.0 and the entry went ahead with the daily cap bounding nothing. Those
    callers always hold a snapshot when they get here (each refuses an unreadable account
    first), so a missing figure there is never the fresh-machine case the local branch exists
    for.
    """
    stamp = now or timeutil.utc_now_iso()
    target = day or utc_day(stamp)
    configured = limit_usdt is not None and _positive(limit_usdt)
    try:
        outcomes = read_live_outcomes(root)
    except ToolError as exc:
        return {
            "created_at": stamp,
            "day_utc": target,
            "daily_realized_pnl_usdt": None,
            "daily_loss_limit_usdt": float(limit_usdt) if configured else 0.0,
            "daily_loss_limit_configured": configured,
            "daily_loss_limit_breached": True,
            "closed_trade_count": None,
            "history_error": exc.reason_code,
            "pnl_source": PNL_SOURCE_LOCAL_LEDGER,
        }
    todays = [r for r in outcomes if str(r.get("closed_at_utc") or "")[:10] == target]

    if venue_realized_pnl_usdt is not None:
        realized = float(venue_realized_pnl_usdt)
        # An unconfigured limit counts as breached here exactly as it does in
        # `daily_loss_limit_breached`: None, 0 and any negative value halt entries rather than
        # permitting unlimited ones. Writing this as `configured and realized <= -limit` reads
        # naturally and is backwards — it hands an unconfigured limit a clean bill of health,
        # which is the one answer this module exists to never give.
        breached = True if not configured else realized <= -float(limit_usdt)  # type: ignore[arg-type]
        return {
            "created_at": stamp,
            "day_utc": target,
            "daily_realized_pnl_usdt": realized,
            "daily_loss_limit_usdt": float(limit_usdt) if configured else 0.0,
            "daily_loss_limit_configured": configured,
            "daily_loss_limit_breached": bool(breached),
            "closed_trade_count": len(todays),
            "history_error": None,
            "pnl_source": PNL_SOURCE_VENUE,
        }
    if venue_required:
        return {
            "created_at": stamp,
            "day_utc": target,
            # Informational only: what the local ledger holds. It decides nothing here.
            "daily_realized_pnl_usdt": daily_realized_pnl(todays, day=target),
            "daily_loss_limit_usdt": float(limit_usdt) if configured else 0.0,
            "daily_loss_limit_configured": configured,
            "daily_loss_limit_breached": True,
            "closed_trade_count": len(todays),
            "history_error": LIVE_PNL_VENUE_FIGURE_MISSING,
            "pnl_source": PNL_SOURCE_LOCAL_LEDGER,
        }

    return {
        "created_at": stamp,
        "day_utc": target,
        "daily_realized_pnl_usdt": daily_realized_pnl(todays, day=target),
        "daily_loss_limit_usdt": float(limit_usdt) if configured else 0.0,
        "daily_loss_limit_configured": configured,
        "daily_loss_limit_breached": daily_loss_limit_breached(
            limit_usdt, outcomes=todays, day=target
        ),
        "closed_trade_count": len(todays),
        # Not an error — a fresh machine has no closed trades either. It is a statement about
        # what the figure above is worth, so a board can stop rendering an empty ledger as
        # "clear" and an operator can see that the limit is currently bounding nothing.
        "history_error": LIVE_PNL_NO_SOURCE if not todays else None,
        "pnl_source": PNL_SOURCE_LOCAL_LEDGER,
    }


def _positive(value: Any) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


class LiveLedger(Protocol):
    """Append-only live outcome recording. No read method — reads are ungated module
    functions, so a caller never needs the gated object just to check the breaker.

    ``append_outcome`` returns ``False`` only when it wrote nothing because a row with the
    same ``settlement_id`` is already durable — the signal a retrying settle path reads as
    "the money is recorded; finish the book-clear". Anything else (including the inert
    ledger's accepted-and-dropped) is ``True``."""

    tool_id: str
    tool_version: str

    def append_outcome(self, record: Mapping[str, Any]) -> bool: ...


class DryRunLiveLedger:
    """Default, inert ledger: accepts the record and writes nothing.

    A live outcome should be structurally impossible to produce with the switch off, but if one
    ever arrives here it is dropped rather than persisted — an unbacked record in the live
    ledger would misinform the breaker.
    """

    tool_id = LIVE_LEDGER_TOOL_ID
    tool_version = f"{LIVE_LEDGER_TOOL_VERSION}-dryrun"
    filesystem_write = False

    def append_outcome(self, record: Mapping[str, Any]) -> bool:
        # True, never False: this ledger holds nothing a record could duplicate, and False
        # would tell a settle path an outcome is durable when nothing is.
        return True


class RealLiveLedger:
    """Durable outcomes under this venue's state directory (``venue_state_dir``).

    Constructed only behind the Safety-Flag Gate for the ``live_trading`` provider, and it
    re-asserts that authorization on every append, so revoking the opt-in stops the ledger
    mid-flight exactly as it stops order egress.
    """

    tool_id = LIVE_LEDGER_TOOL_ID
    tool_version = LIVE_LEDGER_TOOL_VERSION
    provider_id = LIVE_TRADING_PROVIDER_ID
    filesystem_write = True

    def __init__(self, *, root: Path | None = None, authorization: Authorization | None = None,
                 venue: str = VENUE_MAINNET):
        self._root = root
        self._authorization = authorization
        # Which venue's outcome ledger this is (PR1d-0). Default mainnet, so every existing
        # caller writes exactly where it always did.
        self._venue = venue

    def _assert(self) -> None:
        safety_gate.assert_authorization(
            self._authorization,
            required_flags=LIVE_TRADING_FLAGS,
            provider_id=self.provider_id,
            now=timeutil.utc_now_iso(),
        )

    def append_outcome(self, record: Mapping[str, Any]) -> bool:
        self._assert()
        target = venue_state_dir(self._root, venue=self._venue)
        target.mkdir(parents=True, exist_ok=True)
        path = target / LIVE_OUTCOMES_FILENAME
        with locked(path.with_suffix(".lock"), code="LIVE_STATE_LOCKED", label="live outcomes"):
            # Idempotent on settlement_id, checked under the lock — the same rule
            # `paper.RealPaperStore.settle_position` applies, for the same crash window: the
            # settle paths record the money BEFORE clearing the book, so a clear that fails
            # leaves an OPEN book whose outcome is already durable, and the next fire's
            # reconciliation re-settles it with the identical settlement_id. Writing that row
            # again would not double-count — worse, one duplicate fails EVERY verified read
            # of this history (LIVE_HISTORY_DUPLICATE): breaker, risk guard, promotion, all
            # unreadable until an operator hand-edits the fsync'd money ledger. The check
            # rides the verified read, so an unverifiable history refuses the append rather
            # than being treated as not-yet-recorded.
            settlement_id = record.get("settlement_id")
            if isinstance(settlement_id, str) and settlement_id and any(
                o.get("settlement_id") == settlement_id
                for o in read_live_outcomes_raw(self._root, venue=self._venue)
            ):
                return False
            with open(path, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(dict(record), ensure_ascii=False) + "\n")
                # A live outcome that reaches the disk buffer but not the disk would let the
                # breaker forget a real loss across a crash. Force it down.
                handle.flush()
                os.fsync(handle.fileno())
        return True


def select_live_ledger(*, now: str | None = None, root: Path | None = None) -> LiveLedger:
    """Return the durable live ledger if live trading is opted in, else the inert one.

    On ``select_env_gated`` with the rest of the live surface (Thomas, 2026-07-28). This one is
    the least optional of the set: the daily loss breaker reads this ledger, so a durable order
    adapter over an inert ledger is real money traded with the breaker permanently reading zero
    loss. The whole surface moves together or the safety devices come apart from the capability
    they guard."""
    return safety_gate.select_env_gated(
        env_var=LIVE_TRADING_ENV,
        opt_in_value=REAL_LIVE_TRADING,
        flags=LIVE_TRADING_FLAGS,
        provider_id=LIVE_TRADING_PROVIDER_ID,
        default_factory=DryRunLiveLedger,
        gated_factory=lambda authorization: RealLiveLedger(root=root, authorization=authorization),
    )
