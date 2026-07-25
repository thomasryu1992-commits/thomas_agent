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

The ledger write is gated by the **one live-trading grant** (``live_trading``), the same
per-machine grant that will authorize order egress. One grant means one switch: minting it
enables live trading, deleting it revokes the whole capability at once — including the
ability to append to this ledger.
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

# THE live-trading switch. One provider id, one per-machine grant, minted only by the
# operator via scripts/activate_safety_flag.py. It carries BOTH flags the live path needs —
# network_access to reach the venue, filesystem_write to record what happened — so the
# capability cannot be half-enabled, and deleting the grant revokes all of it at once
# (assert_authorization re-reads the record at every egress).
LIVE_TRADING_ENV = "MVP_LIVE_TRADING"
REAL_LIVE_TRADING = "real"
LIVE_TRADING_PROVIDER_ID = "live_trading"
LIVE_TRADING_FLAGS = (NETWORK_ACCESS, FILESYSTEM_WRITE)

STATE_REL = ".runtime_governance_state/crypto"
LIVE_OUTCOMES_FILENAME = "live_outcomes.jsonl"
LIVE_PROVENANCE = "mvp_live_kernel"

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


def live_risk_snapshot(
    *,
    limit_usdt: float | None,
    day: str | None = None,
    root: Path | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Today's live risk state, for the guard, the dashboard, and the operator.

    Reads fail-closed: if the history cannot be verified the snapshot reports the breaker as
    tripped and names the reason, rather than reporting a comfortable zero.
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
        }
    todays = [r for r in outcomes if str(r.get("closed_at_utc") or "")[:10] == target]
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
        "history_error": None,
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

    A live outcome should be structurally impossible to produce without the grant, but if one
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
    re-asserts that authorization on every append, so revoking the grant stops the ledger
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
    """Return the durable live ledger if the live-trading grant is open, else the inert one."""
    return safety_gate.select_gated(
        env_var=LIVE_TRADING_ENV,
        opt_in_value=REAL_LIVE_TRADING,
        flags=LIVE_TRADING_FLAGS,
        provider_id=LIVE_TRADING_PROVIDER_ID,
        default_factory=DryRunLiveLedger,
        gated_factory=lambda authorization: RealLiveLedger(root=root, authorization=authorization),
        now=now,
        root=root,
    )
