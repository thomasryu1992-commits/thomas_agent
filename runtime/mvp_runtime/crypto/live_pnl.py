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

from runtime.read_only_kernel import integrity

from .. import safety_gate, timeutil
from ..errors import ToolError
from ..filelock import locked
from ..paths import repo_root as _repo_root
from ..safety_gate import FILESYSTEM_WRITE, NETWORK_ACCESS, Authorization

LIVE_LEDGER_TOOL_ID = "crypto.live.pnl_ledger"
LIVE_LEDGER_TOOL_VERSION = "0.1.0"

# THE live-trading switch, and the only one: `MVP_LIVE_TRADING=real` in the process
# environment (Thomas, 2026-07-28). It used to ALSO require a per-machine grant record minted
# by scripts/activate_safety_flag.py; Thomas removed that second requirement because the
# deployment already places these vars under operator-only control and the grant's expiry could
# trap an open position — an expired grant closed the gate on the CLOSE path too. Under the
# grant this was one switch across two mechanisms; now it is one switch, full stop.
#
# The provider id and flag pair survive the removal. They are what `assert_authorization`
# re-checks at every egress and what each capable class declares, so they still keep the
# capability from being half-enabled — network_access to reach the venue, filesystem_write to
# record what happened, never one without the other. What changed is what opens the gate, not
# what the gate covers.
LIVE_TRADING_ENV = "MVP_LIVE_TRADING"
REAL_LIVE_TRADING = "real"
LIVE_TRADING_PROVIDER_ID = "live_trading"
LIVE_TRADING_FLAGS = (NETWORK_ACCESS, FILESYSTEM_WRITE)

STATE_REL = ".runtime_governance_state/crypto"
LIVE_OUTCOMES_FILENAME = "live_outcomes.jsonl"
LIVE_PROVENANCE = "mvp_live_kernel"

# How an outcome's `result_R` was measured. Recorded on every row so a consumer pooling
# populations can see it is doing so — and, since 2026-07-30, so it can tell a paper row that
# paid costs from one that did not.
#
# - `intent`             intended fills, NO costs. Every paper row written before 2026-07-30.
# - `intent_net_of_costs` intended fills, fees + slippage charged (`cost.apply_cost_model`).
#                        Paper rows written since. This is the basis the factory backtest has
#                        always used, so a paper expectancy is finally comparable to the
#                        backtest expectancy that scored the same strategy.
# - `filled`             actual venue fills, slippage included, fees still excluded (`live_leg`).
#
# A window spanning the 2026-07-30 boundary mixes `intent` and `intent_net_of_costs`, and
# therefore UNDER-states its losses by the legacy rows' unpaid costs. There is no backfill: the
# stored outcome keeps `entry_price`/`exit_price`/`direction` but not `risk`, and the cost model
# is denominated in risk-per-unit — so an old row cannot be re-priced, only labelled.
R_BASIS_INTENT = "intent"
R_BASIS_INTENT_NET = "intent_net_of_costs"
R_BASIS_FILLED = "filled"

# The bases that already carry costs. `filled` is deliberately absent: live R includes venue
# slippage but not fees, so it is neither of the two paper bases and must not be read as one.
R_BASES_NET_OF_COSTS = frozenset({R_BASIS_INTENT_NET})

LIVE_HISTORY_UNREADABLE = "LIVE_HISTORY_UNREADABLE"
LIVE_HISTORY_TAMPERED = "LIVE_HISTORY_TAMPERED"
LIVE_HISTORY_DUPLICATE = "LIVE_HISTORY_DUPLICATE"


def state_dir(root: Path | None = None) -> Path:
    return (root if root is not None else _repo_root()) / STATE_REL


def build_live_outcome_record(
    *,
    realized_pnl_usdt: float,
    symbol: str,
    side: str,
    quantity: float,
    entry_price: float | None = None,
    exit_price: float | None = None,
    entry_order_id: Any = None,
    exit_order_id: Any = None,
    strategy_id: str | None = None,
    position_id: str | None = None,
    close_reason: str | None = None,
    opened_at_utc: str | None = None,
    risk_usdt: float | None = None,
    candidate_id: str | None = None,
    strategy_rule_hash: str | None = None,
    strategy_generation_id: str | None = None,
    now: str,
) -> dict[str, Any]:
    """One closed live position, self-hashed.

    ``settlement_id`` is derived from the position identity, so a second attempt to record
    the same settlement is detectable as a duplicate rather than quietly doubling the day's
    realized P&L — which would move the breaker in the dangerous direction.

    **LP5.4 — the outcome bridge.** The original shape carried only what the daily-loss
    breaker needs (``realized_pnl_usdt``), which left the risk guard, the lifecycle demoter,
    and the C6 feedback report blind to live results: they key on ``result_R``,
    ``created_at_utc``, and strategy LINEAGE, none of which existed here. The four additive
    arguments close that gap at the source, so a settled live position is legible to the
    same machinery a paper one is:

    - ``risk_usdt`` — the position's entry↔stop distance in quote terms (LP5.1 records it
      as ``risk``). ``result_R`` is computed from it, and **only** from it: with no recorded
      risk there is no honest R, so ``result_R`` stays ``None`` rather than becoming 0.0.
      That distinction is load-bearing — ``guards._closed_rows`` reads a missing
      ``result_R`` as ``0.0``, i.e. a breakeven, so a real live loss with no risk recorded
      would *shorten* a loss streak instead of extending it. The bridge below therefore
      excludes such rows rather than passing them.
    - ``candidate_id`` / ``strategy_rule_hash`` / ``strategy_generation_id`` — the lineage
      the lifecycle groups by. Without it a live result would be attributed to whatever
      strategy currently answers to that display id, which the factory restarts at S001
      every generation.

    ``created_at_utc`` mirrors ``closed_at_utc`` because that is the field name every
    consumer reads for an outcome's time. Both are emitted rather than one renamed: the
    live record's own vocabulary stays intact, and the analytic key is present too.
    """
    risk = float(risk_usdt) if isinstance(risk_usdt, (int, float)) and risk_usdt else 0.0
    realized = round(float(realized_pnl_usdt), 8)
    body: dict[str, Any] = {
        "realized_pnl_usdt": realized,
        # R is the realized P&L over what was risked. None when the risk was not recorded —
        # never 0.0, which would read as a breakeven trade to every consumer.
        "result_R": round(realized / risk, 8) if risk > 0 else None,
        "risk_usdt": risk if risk > 0 else None,
        "symbol": symbol,
        "side": side,
        "quantity": float(quantity),
        "entry_price": entry_price,
        "exit_price": exit_price,
        "entry_order_id": entry_order_id,
        "exit_order_id": exit_order_id,
        "strategy_id": strategy_id,
        "candidate_id": candidate_id,
        "strategy_rule_hash": strategy_rule_hash,
        "strategy_generation_id": strategy_generation_id,
        "position_id": position_id,
        "close_reason": close_reason,
        "opened_at_utc": opened_at_utc,
        "closed_at_utc": now,
        # The analytic time key. Same instant as closed_at_utc; named as the consumers read it.
        "created_at_utc": now,
        "outcome_closed": True,
        "stage": "live",
        "provenance": LIVE_PROVENANCE,
        # What this R is measured against. Paper R is computed on INTENDED fills and, since
        # 2026-07-30, NET of modelled fees and slippage (`R_BASIS_INTENT_NET`). Live R is computed
        # on ACTUAL fills, so real slippage is already inside it but fees are NOT charged here.
        # The three bases are therefore still not the same statistic, and the
        # consumers that pool them — the risk guard, the lifecycle demoter, the C6 report — can
        # only be read honestly if the difference is visible in the row rather than known by
        # whoever remembers it. Live R is the more pessimistic of the two, so the breaker trips
        # sooner and demotion comes faster: the distortion runs in the conservative direction,
        # which is why this is recorded rather than corrected.
        "r_basis": R_BASIS_FILLED,
    }
    body["outcome_id"] = integrity.short_id(
        "live_out", {"position_id": position_id, "closed_at": now, "symbol": symbol}
    )
    body["settlement_id"] = integrity.short_id(
        "live_settle", {"position_id": position_id, "exit_order_id": exit_order_id}
    )
    body["record_sha256"] = integrity.sha256_record(body)
    return body


def read_live_outcomes(root: Path | None = None) -> list[dict[str, Any]]:
    """All persisted live outcomes, oldest first — a VERIFIED read.

    Missing store = honestly empty (nothing has traded yet). Anything unreadable, tampered,
    or duplicated raises, because every caller of this history is a risk decision: a history
    that cannot prove itself must not be allowed to argue that the breaker is clear.
    """
    path = state_dir(root) / LIVE_OUTCOMES_FILENAME
    if not path.is_file():
        return []
    outcomes: list[dict[str, Any]] = []
    seen_outcome_ids: set[str] = set()
    seen_settlement_ids: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ToolError(LIVE_HISTORY_UNREADABLE, f"live outcomes unreadable: {exc.strerror}") from exc
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise ToolError(LIVE_HISTORY_UNREADABLE, f"live outcomes line {i + 1} is not valid JSON") from exc
        if not isinstance(record, dict):
            continue
        stored = record.get("record_sha256")
        body = {k: v for k, v in record.items() if k != "record_sha256"}
        if not isinstance(stored, str) or integrity.sha256_record(body) != stored:
            raise ToolError(LIVE_HISTORY_TAMPERED, f"live outcomes line {i + 1} fails its self-hash")
        outcome_id = record.get("outcome_id")
        if isinstance(outcome_id, str) and outcome_id:
            if outcome_id in seen_outcome_ids:
                raise ToolError(LIVE_HISTORY_DUPLICATE, f"duplicate outcome_id: {outcome_id}")
            seen_outcome_ids.add(outcome_id)
        settlement_id = record.get("settlement_id")
        if isinstance(settlement_id, str) and settlement_id:
            if settlement_id in seen_settlement_ids:
                raise ToolError(LIVE_HISTORY_DUPLICATE, f"duplicate settlement_id: {settlement_id}")
            seen_settlement_ids.add(settlement_id)
        outcomes.append(record)
    return outcomes


# --- LP5.4: the outcome bridge -------------------------------------------------

# Why a row can be legible to the breaker but not to the R-based consumers.
UNKNOWN_R = "LIVE_OUTCOME_NO_RECORDED_RISK"


def live_outcomes_for_analysis(
    outcomes: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split live outcomes into rows the R-based consumers may read, and rows they may not.

    Returns ``(readable, excluded)``. A readable row is exactly the shape
    ``guards.run_risk_guard``, ``lifecycle`` and ``feedback.summarize_outcomes`` already
    consume — ``result_R``, ``created_at_utc``, ``outcome_closed``, plus the lineage the
    lifecycle groups by — so no consumer needs a live-specific branch.

    **The exclusion is the point.** ``guards._closed_rows`` reads a missing ``result_R`` as
    ``0.0``, which is a *breakeven*. A live loss whose risk was never recorded would
    therefore shorten a loss streak instead of extending it, and dilute expectancy toward
    zero — a fail-open in the one direction that matters. Such rows are excluded and
    reported with ``UNKNOWN_R`` rather than passed through with a fabricated R. They remain
    fully visible to the daily-loss breaker, which reads ``realized_pnl_usdt`` and needs no
    R at all: the money is never lost from the accounting, only from the R statistics that
    cannot honestly include it.

    Pure: no I/O. The caller decides what to do with each list.
    """
    readable: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for record in outcomes:
        if not isinstance(record, Mapping) or record.get("outcome_closed") is not True:
            continue
        result_r = record.get("result_R")
        if not isinstance(result_r, (int, float)) or isinstance(result_r, bool):
            excluded.append({
                "outcome_id": record.get("outcome_id"),
                "symbol": record.get("symbol"),
                "realized_pnl_usdt": record.get("realized_pnl_usdt"),
                "reason": UNKNOWN_R,
            })
            continue
        readable.append({
            "outcome_closed": True,
            "result_R": float(result_r),
            # Pre-bridge rows carry only closed_at_utc; either satisfies the consumers.
            "created_at_utc": record.get("created_at_utc") or record.get("closed_at_utc"),
            "strategy_id": record.get("strategy_id"),
            "candidate_id": record.get("candidate_id"),
            "strategy_rule_hash": record.get("strategy_rule_hash"),
            "strategy_generation_id": record.get("strategy_generation_id"),
            "symbol": record.get("symbol"),
            "close_reason": record.get("close_reason"),
            "realized_pnl_usdt": record.get("realized_pnl_usdt"),
            # Kept so a consumer that mixes streams can still tell them apart.
            "stage": record.get("stage") or "live",
        })
    return readable, excluded


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


def utc_day(stamp: str | None = None) -> str:
    """The UTC calendar day a timestamp belongs to. The breaker resets at UTC midnight."""
    return (stamp or timeutil.utc_now_iso())[:10]


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
    is readable the caller gets ``None`` and falls back to the local-ledger path, which has
    its own no-source rule.
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
    case explicitly — the distinction ``clean_canary_order_count`` already makes by returning
    its error alongside its count.
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
    functions, so a caller never needs the gated object just to check the breaker."""

    tool_id: str
    tool_version: str

    def append_outcome(self, record: Mapping[str, Any]) -> None: ...


class DryRunLiveLedger:
    """Default, inert ledger: accepts the record and writes nothing.

    A live outcome should be structurally impossible to produce with the switch off, but if one
    ever arrives here it is dropped rather than persisted — an unbacked record in the live
    ledger would misinform the breaker.
    """

    tool_id = LIVE_LEDGER_TOOL_ID
    tool_version = f"{LIVE_LEDGER_TOOL_VERSION}-dryrun"
    filesystem_write = False

    def append_outcome(self, record: Mapping[str, Any]) -> None:
        return None


class RealLiveLedger:
    """Durable live outcomes under ``.runtime_governance_state/crypto/``.

    Constructed only behind the Safety-Flag Gate for the ``live_trading`` provider, and it
    re-asserts that authorization on every append, so revoking the opt-in stops the ledger
    mid-flight exactly as it stops order egress.
    """

    tool_id = LIVE_LEDGER_TOOL_ID
    tool_version = LIVE_LEDGER_TOOL_VERSION
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

    def append_outcome(self, record: Mapping[str, Any]) -> None:
        self._assert()
        target = state_dir(self._root)
        target.mkdir(parents=True, exist_ok=True)
        path = target / LIVE_OUTCOMES_FILENAME
        with locked(path.with_suffix(".lock"), code="LIVE_STATE_LOCKED", label="live outcomes"):
            with open(path, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(dict(record), ensure_ascii=False) + "\n")
                # A live outcome that reaches the disk buffer but not the disk would let the
                # breaker forget a real loss across a crash. Force it down.
                handle.flush()
                os.fsync(handle.fileno())


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
