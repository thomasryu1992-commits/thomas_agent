"""LP3 live order intent, idempotency, and the final guard (source L2).

The last thing that runs before a real order could ever be sent — and, for now, the last
thing that exists at all: this module can refuse an order, but nothing here can send one.
Building the refusal before the capability is the point. Ported from the source system's
``execution/live_order_final_guard.py``, ``order_executor.build_order_intent`` and
``execution/idempotency.py``.

Three rules carried over verbatim, each learned the expensive way:

* **Zero means "not configured", never "unlimited".** Every cap defaults to 0 and a cap of 0
  blocks. A missing limit is the most dangerous state, so it must read as halted.
* **A cap above the absolute ceiling is itself a block, not a clamp.** Silently shrinking an
  order would desync the size from the decision that approved it.
* **Guards accumulate; they never short-circuit.** The operator sees every reason at once,
  not the first one alphabetically.

``blocks`` are policy or configuration refusals. ``repairs`` are the four malformed-intent
problems that a corrected intent would fix. Blocks outrank repairs; only a clean ``READY``
verdict is ``approved``.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from runtime.read_only_kernel import integrity

from .. import safety_gate, timeutil
from ..errors import ToolError
from ..filelock import locked
from ..paths import repo_root as _repo_root
from ..safety_gate import Authorization
from .live_pnl import (
    LIVE_TRADING_FLAGS,
    LIVE_TRADING_PROVIDER_ID,
    state_dir,
    utc_day,
)
# The clean-canary minimum belongs to the module that owns promotion evidence. This file
# used to declare its own `= 3` beside it — two authorities for one safety threshold, with
# nothing comparing them. No cycle: live_promotion does not import this module.
from .live_promotion import DEFAULT_MIN_CLEAN_CANARY_ORDERS

STATUS_BLOCKED = "BLOCKED"
STATUS_REPAIR_REQUIRED = "REPAIR_REQUIRED"
STATUS_READY = "READY"

# Distinct from every other confirmation phrase in the system on purpose: pasting the
# canary or testnet phrase must not authorize autonomous live trading.
LIVE_CONFIRMATION_PHRASE = "I_UNDERSTAND_THIS_TRADES_LIVE_FUNDS_AUTONOMOUSLY"

# ...and the converse: the autonomous phrase must not authorize a canary either. A canary is a
# deliberate, one-at-a-time operator order placed to BUILD the promotion evidence, so it carries
# its own phrase and its own env. One phrase per capability is the whole point — pasting the
# wrong one authorizes nothing.
CANARY_CONFIRMATION_PHRASE = "I_UNDERSTAND_THIS_PLACES_A_REAL_LIVE_MAINNET_ORDER"

# The only three the operator sets. Everything else a live order is bounded by comes from the
# registered `live_trading_budget.v0.1` record — a phrase proving intent and a halt are operator
# state, a cap is an auditable record. The `MVP_LIVE_MAX_*` / `_DAILY_LOSS_LIMIT_` /
# `_ABSOLUTE_MAX_` / `_MIN_CLEAN_CANARY_ORDERS` vars this module used to read are deliberately
# gone rather than merely unused: while `from_env()` still parsed them, `LiveOrderLimits.from_env()`
# remained a constructible second source of caps, which is exactly the footgun #203 closed at the
# call sites by making `limits` required. Not reading them is the structural version of that fix.
CONFIRMATION_ENV = "MVP_LIVE_CONFIRMATION"
CANARY_CONFIRMATION_ENV = "MVP_LIVE_CANARY_CONFIRMATION"
MANUAL_KILL_SWITCH_ENV = "MVP_LIVE_MANUAL_KILL_SWITCH"

# How the operator is told to fix an unconfigured cap. There is exactly one way, and naming an
# env var here instead sent them to a knob that has not authorized anything since step 6b —
# fail-closed, but pointing at the wrong next action, which is how #201 hid.
REGISTER_BUDGET_HINT = "register a budget with scripts/register_live_trading_budget.py"

# The ceiling a configured cap can never exceed, whatever the operator types. Source value.
DEFAULT_ABSOLUTE_MAX_NOTIONAL_USDT = 200.0

COUNTER_FILENAME = "live_order_counter.json"
LIVE_COUNTER_UNREADABLE = "LIVE_COUNTER_UNREADABLE"

_TRUTHY = frozenset({"1", "true", "yes", "y", "on", "enabled"})


def _env_bool(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


@dataclass(frozen=True)
class LiveOrderLimits:
    """The operator's registered risk budget. Every field defaults to the blocking value."""

    max_order_notional_usdt: float = 0.0
    absolute_max_notional_usdt: float = DEFAULT_ABSOLUTE_MAX_NOTIONAL_USDT
    max_daily_order_count: int = 0
    max_open_notional_usdt: float = 0.0
    daily_loss_limit_usdt: float = 0.0
    min_clean_canary_orders: int = DEFAULT_MIN_CLEAN_CANARY_ORDERS
    confirmation: str = ""
    canary_confirmation: str = ""
    manual_kill_switch: bool = False

    @classmethod
    def from_env(cls) -> "LiveOrderLimits":
        """The operator-env half only: both confirmation phrases and the manual halt.

        **Reads no cap.** The caps keep their blocking class defaults here, so an instance
        built from env alone can authorize nothing — the numbers must come from
        ``resolve_live_order_limits``, which reads the registered budget. This used to parse
        ``MVP_LIVE_MAX_*`` and friends, which made this classmethod a second, unaudited source
        of caps that any caller could reconstruct.
        """
        return cls(
            confirmation=os.environ.get(CONFIRMATION_ENV, "").strip(),
            canary_confirmation=os.environ.get(CANARY_CONFIRMATION_ENV, "").strip(),
            manual_kill_switch=_env_bool(MANUAL_KILL_SWITCH_ENV),
        )

    @property
    def effective_max_notional_usdt(self) -> float:
        return min(self.max_order_notional_usdt, self.absolute_max_notional_usdt)

    def confirmation_present(self) -> bool:
        return bool(self.confirmation) and self.confirmation == LIVE_CONFIRMATION_PHRASE

    def canary_confirmation_present(self) -> bool:
        """The canary phrase, compared only against the canary constant — so the autonomous
        phrase can never stand in for it (and vice versa)."""
        return bool(self.canary_confirmation) and self.canary_confirmation == CANARY_CONFIRMATION_PHRASE


# --- idempotency -------------------------------------------------------------------

def make_idempotency_key(payload: Mapping[str, Any]) -> str:
    """Stable key over the order's identity. Two attempts at the same trade produce the
    same key, so a retry after an ambiguous submit reuses the client order id instead of
    opening a second position."""
    blob = json.dumps(dict(payload), sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def make_client_order_id(symbol: str, direction: str, idempotency_key: str) -> str:
    """Venue-safe client order id (Binance caps these at 36 characters)."""
    return f"TAI_{symbol}_{direction}_{idempotency_key[:18]}"[:36]


def enrich_order_identity(intent: dict[str, Any]) -> dict[str, Any]:
    """Attach the idempotency key and client order id derived from the intent itself."""
    key = make_idempotency_key(
        {
            "symbol": intent.get("symbol"),
            "direction": intent.get("direction"),
            "strategy_id": intent.get("strategy_id"),
            "candle_time": intent.get("candle_time") or intent.get("created_at"),
            "position_id": intent.get("position_id"),
        }
    )
    intent["idempotency_key"] = key
    intent["client_order_id"] = make_client_order_id(
        str(intent.get("symbol") or "UNKNOWN"), str(intent.get("direction") or "NONE"), key
    )
    intent["order_intent_id"] = integrity.short_id("live_intent", {"key": key})
    return intent


# --- intent ------------------------------------------------------------------------

def build_live_order_intent(
    plan: Mapping[str, Any],
    *,
    symbol: str,
    quantity: float,
    notional_usdt: float,
    now: str,
    reduce_only: bool = False,
    close_reason: str | None = None,
) -> dict[str, Any]:
    """Turn an approved entry plan into a live order intent.

    Refuses rather than guessing: a missing direction never defaults to a side, and a
    missing notional is **never** back-filled from the configured cap. The cap is a ceiling,
    not a size — the source system learned that the hard way and the rule is carried over.
    """
    direction = str(plan.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        raise ToolError("MALFORMED_DIRECTION", "live order intent needs an explicit LONG or SHORT")
    if not symbol:
        raise ToolError("MISSING_SYMBOL", "live order intent needs a symbol")
    if quantity <= 0:
        raise ToolError("MISSING_ORDER_QUANTITY", "live order intent needs a positive quantity")
    if notional_usdt <= 0:
        raise ToolError(
            "MISSING_ORDER_NOTIONAL",
            "live order intent needs an explicit positive notional (the cap is a ceiling, not a size)",
        )
    if reduce_only:
        side = "SELL" if direction == "LONG" else "BUY"
    else:
        side = "BUY" if direction == "LONG" else "SELL"
    intent: dict[str, Any] = {
        "status": "ORDER_INTENT_CREATED",
        "execution_stage": "live",
        "created_at": now,
        "symbol": symbol,
        "direction": direction,
        "side": side,
        "order_type_exchange": "MARKET",
        "quantity": float(quantity),
        "order_notional_usdt": round(float(notional_usdt), 2),
        "reduce_only": bool(reduce_only),
        "close_reason": close_reason,
        "entry_price": plan.get("entry_price"),
        "stop_loss": plan.get("stop_loss"),
        "take_profit": plan.get("take_profit"),
        "strategy_id": plan.get("strategy_id"),
        # The lineage, carried for the same reason the paper plan carries it (paper.py's
        # open_position): `strategy_id` is a DISPLAY id the factory restarts at S001 every
        # generation, so it cannot attribute a result. LP5.4's bridge, `lifecycle` and the C6
        # feedback all group live outcomes by these three — and the executing leg reads them
        # off the intent, which is the record that crosses from planning to execution. Until
        # 2026-07-26 only `strategy_id` was copied, so a live trade placed through the real
        # pipeline reached the ledger with no lineage at all: a live loss could not demote the
        # strategy that caused it, and a later generation answering to the same display name
        # could inherit or be judged by it. Exactly what LP5.4 exists to prevent.
        "candidate_id": plan.get("candidate_id"),
        "strategy_rule_hash": plan.get("strategy_rule_hash"),
        "strategy_generation_id": plan.get("strategy_generation_id"),
        "position_id": plan.get("position_id"),
        "candle_time": plan.get("candle_time"),
        "connectivity_test": False,
    }
    return enrich_order_identity(intent)


def _notional_of(intent: Mapping[str, Any]) -> float:
    for key in ("order_notional_usdt", "notional_usdt"):
        try:
            value = float(intent.get(key))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0.0


def _shape_repairs(intent: Mapping[str, Any]) -> list[str]:
    repairs: list[str] = []
    if intent.get("status") != "ORDER_INTENT_CREATED":
        repairs.append("intent status is not ORDER_INTENT_CREATED")
    if not intent.get("symbol"):
        repairs.append("intent has no symbol")
    try:
        if float(intent.get("quantity")) <= 0:  # type: ignore[arg-type]
            repairs.append("intent quantity must be positive")
    except (TypeError, ValueError):
        repairs.append("intent quantity is missing or non-numeric")
    return repairs


# --- the final guard ---------------------------------------------------------------

def resolve_live_order_limits(
    root: Path | None = None, *, now: str | None = None
) -> tuple["LiveOrderLimits", dict[str, Any]]:
    """The guard's authoritative caps: the **registered budget**, plus the confirmation phrase
    and manual kill from operator env (those are never budget-registered).

    Returns ``(limits, budget_status)``. A missing / expired / tampered budget yields the
    blocking-default caps and a status whose ``valid`` is ``False``, so the guard's budget check
    (and its unconfigured-caps checks) block — no live order without a registered budget
    (``autonomous_spend_without_registered_budget: '0'``). The env cap vars (``MVP_LIVE_MAX_*``)
    no longer authorize an order; the registered budget supersedes them.

    **Both** confirmation phrases and the manual kill remain env, and are carried through on
    every branch: ``MVP_LIVE_CONFIRMATION`` (autonomous), ``MVP_LIVE_CANARY_CONFIRMATION`` (the
    deliberate canary), ``MVP_LIVE_MANUAL_KILL_SWITCH``. A phrase proving intent and a halt are
    operator state, not registered caps. The canary phrase was omitted here until 2026-07-26,
    which left ``place_canary_order.py`` — the only live door there is, and the one that has to
    work before any autonomous path can — permanently refused with "canary confirmation phrase
    not present". Failing closed, but on the step the operator has to take next, and only
    discoverable standing at the terminal with real keys."""
    from . import live_budget  # lazy: live_budget imports LiveOrderLimits

    status = live_budget.budget_status(root, now=now or timeutil.utc_now_iso())
    env = LiveOrderLimits.from_env()  # confirmation + manual kill only
    caps = status.get("caps")
    if status.get("valid") and isinstance(caps, Mapping):
        limits = LiveOrderLimits(
            max_order_notional_usdt=float(caps["max_order_notional_usdt"]),
            absolute_max_notional_usdt=float(caps["absolute_max_notional_usdt"]),
            max_daily_order_count=int(caps["max_daily_order_count"]),
            max_open_notional_usdt=float(caps["max_open_notional_usdt"]),
            daily_loss_limit_usdt=float(caps["daily_loss_limit_usdt"]),
            min_clean_canary_orders=int(caps["min_clean_canary_orders"]),
            confirmation=env.confirmation,
            canary_confirmation=env.canary_confirmation,
            manual_kill_switch=env.manual_kill_switch,
        )
    else:
        # No valid budget: caps stay at the blocking defaults (0), so even the per-order / daily /
        # exposure / loss caps read as unconfigured on top of the budget block.
        limits = LiveOrderLimits(
            confirmation=env.confirmation,
            canary_confirmation=env.canary_confirmation,
            manual_kill_switch=env.manual_kill_switch,
        )
    return limits, status


def evaluate_live_order_guard(
    intent: Mapping[str, Any],
    *,
    gate_open: bool,
    runtime_active: bool,
    daily_loss_breached: bool,
    clean_canary_orders: int,
    submitted_today: int,
    # LP5.1: no default. This used to be `= 0.0` — the single fail-open path in an
    # otherwise fail-closed guard, because a caller that forgot it silently disabled the
    # exposure cap. It is now required, so the exposure a live order is judged against is
    # always something a caller stated on purpose. `live_position.compute_open_notional_usdt`
    # supplies it from the venue and reports the cap itself when the account is unreadable.
    current_open_notional_usdt: float,
    # No default, for the same reason `current_open_notional_usdt` has none: a fallback to
    # `LiveOrderLimits.from_env()` was a SECOND source of caps, and the registered budget is
    # supposed to be the only one (`resolve_live_order_limits`). Two sources means the
    # question "which numbers is this order being judged against?" has two answers, and the
    # env one is reachable by forgetting an argument. Callers state it.
    limits: LiveOrderLimits,
    budget_registered: bool = False,
    canary: bool = False,
) -> dict[str, Any]:
    """The last gate before a live entry. Pure: it reads no file and opens no socket.

    Every runtime fact arrives as an argument so this can be exhaustively tested without a
    venue, a grant, or a clock. Checks accumulate — the caller sees the complete refusal.

    ``budget_registered`` states whether a valid registered live-trading budget backs the
    ``limits`` — the caller resolves both together (``resolve_live_order_limits``). It defaults
    to ``False`` (fail-closed): a caller that does not resolve a budget cannot accidentally
    authorize an order on caps that no budget backs.

    ``canary`` marks the deliberate operator canary — the one-at-a-time real order placed to
    BUILD the promotion evidence. It changes exactly two things, and nothing else:

    1. the **promotion gate is not applied**, because requiring >= 3 clean canaries before the
       first canary can be placed is unsatisfiable — that check exists to gate the *autonomous*
       path, and the canary is what earns it;
    2. the confirmation phrase compared is the **canary** phrase, not the autonomous one, so the
       autonomous phrase cannot authorize a canary (and the canary phrase cannot authorize
       autonomous trading — that was already true).

    Every other check — the grant, both kill switches, the loss breaker, the registered budget,
    the size / daily-count / exposure caps, the connectivity refusal, the intent shape — applies
    identically. Sharing one guard rather than writing a second one is deliberate: a check added
    later cannot land on only one of the two paths. ``canary`` defaults to ``False``, so the
    autonomous path keeps the promotion gate by default (fail-closed).
    """
    cfg = limits
    blocks: list[str] = []
    repairs: list[str] = []

    # 0. The registered trading budget. ``autonomous_spend_without_registered_budget: '0'`` —
    #    no live order until a self-hashed budget record is registered and valid. The caps below
    #    come FROM that budget (via resolve_live_order_limits); a missing/expired/tampered budget
    #    arrives here as budget_registered=False and blocks regardless of the env caps.
    if not budget_registered:
        blocks.append(
            "no valid registered live-trading budget "
            "(autonomous_spend_without_registered_budget); register one with "
            "scripts/register_live_trading_budget.py"
        )
    # 1. The switch. Without the operator's live-trading grant nothing else matters.
    if not gate_open:
        blocks.append("live trading grant is not active (safety-flag gate closed)")
    # 2. The phrase. A grant enables the capability; the phrase proves intent to use it. One
    #    phrase per capability: a canary is authorized by the canary phrase, never the autonomous
    #    one, so a machine armed for canaries cannot start trading autonomously.
    if canary:
        if not cfg.canary_confirmation_present():
            blocks.append(f"canary confirmation phrase not present ({CANARY_CONFIRMATION_ENV})")
    elif not cfg.confirmation_present():
        blocks.append(f"live confirmation phrase not present ({CONFIRMATION_ENV})")
    # 3. The trader's own halt.
    if cfg.manual_kill_switch:
        blocks.append(f"manual kill switch is engaged ({MANUAL_KILL_SWITCH_ENV})")
    # 4. The runtime's halt. Binds kill_blocks: external_execution, which had no door until
    #    now — a PAUSED or KILLED runtime must not open a live position.
    if not runtime_active:
        blocks.append("runtime is not ACTIVE; kill_blocks external_execution forbids a live entry")
    # 5. Today's realized loss. An unconfigured limit arrives here already True.
    if daily_loss_breached:
        if cfg.daily_loss_limit_usdt <= 0:
            blocks.append(f"daily loss limit is not configured; {REGISTER_BUDGET_HINT}")
        else:
            blocks.append(
                f"daily realized-loss limit {cfg.daily_loss_limit_usdt} USDT reached - halted for today"
            )
    # 6. Promotion evidence: clean canary orders actually placed and reconciled. NOT applied to a
    #    canary — that order is what earns this evidence, so gating it on the evidence would make
    #    the first canary unplaceable and the count would stay 0 forever. The autonomous path
    #    keeps the gate (canary defaults to False).
    if not canary:
        if cfg.min_clean_canary_orders <= 0:
            blocks.append(
                f"promotion minimum is not configured (would be promotion with no "
                f"evidence); {REGISTER_BUDGET_HINT}"
            )
        elif clean_canary_orders < cfg.min_clean_canary_orders:
            blocks.append(
                f"live promotion not ready - need >= {cfg.min_clean_canary_orders} clean canary "
                f"orders, have {clean_canary_orders}"
            )
    # 7. A connectivity probe must never ride the autonomous path.
    if intent.get("connectivity_test"):
        blocks.append("connectivity_test intent cannot use the live order path")

    # 8. Per-order size.
    notional = _notional_of(intent)
    if cfg.max_order_notional_usdt <= 0:
        blocks.append(f"per-order cap is not configured; {REGISTER_BUDGET_HINT}")
    elif cfg.max_order_notional_usdt > cfg.absolute_max_notional_usdt:
        blocks.append(
            f"configured cap {cfg.max_order_notional_usdt} exceeds the absolute ceiling "
            f"{cfg.absolute_max_notional_usdt}"
        )
    if notional <= 0:
        repairs.append("order notional missing or non-positive")
    elif cfg.max_order_notional_usdt > 0 and notional > cfg.effective_max_notional_usdt:
        blocks.append(
            f"order notional {notional} exceeds the effective cap {cfg.effective_max_notional_usdt}"
        )

    # 9. Orders per UTC day.
    if cfg.max_daily_order_count <= 0:
        blocks.append(f"daily order cap is not configured; {REGISTER_BUDGET_HINT}")
    elif submitted_today >= cfg.max_daily_order_count:
        blocks.append(
            f"daily order cap reached ({submitted_today}/{cfg.max_daily_order_count})"
        )

    # 10. Total open exposure, counting what this order would add.
    if cfg.max_open_notional_usdt <= 0:
        blocks.append(f"open exposure cap is not configured; {REGISTER_BUDGET_HINT}")
    elif current_open_notional_usdt + notional > cfg.max_open_notional_usdt:
        blocks.append(
            f"open exposure {current_open_notional_usdt} + {notional} exceeds the cap "
            f"{cfg.max_open_notional_usdt}"
        )

    # 11. Intent shape.
    repairs.extend(_shape_repairs(intent))

    status = STATUS_BLOCKED if blocks else (STATUS_REPAIR_REQUIRED if repairs else STATUS_READY)
    return {
        "status": status,
        "approved": status == STATUS_READY,
        "blocks": blocks,
        "repairs": repairs,
        "notional_usdt": notional,
        "notional_cap_usdt": cfg.max_order_notional_usdt,
        "effective_cap_usdt": cfg.effective_max_notional_usdt,
        "absolute_ceiling_usdt": cfg.absolute_max_notional_usdt,
        "open_exposure_cap_usdt": cfg.max_open_notional_usdt,
        "current_open_notional_usdt": current_open_notional_usdt,
        "submitted_today": submitted_today,
        "max_daily_order_count": cfg.max_daily_order_count,
        "daily_loss_limit_usdt": cfg.daily_loss_limit_usdt,
        "daily_loss_breached": daily_loss_breached,
        "clean_canary_orders": clean_canary_orders,
        "close_guard": False,
        "canary": bool(canary),
    }


def evaluate_live_close_guard(
    intent: Mapping[str, Any],
    *,
    gate_open: bool,
    limits: LiveOrderLimits,
) -> dict[str, Any]:
    """The deliberately narrower gate for closing an open live position.

    A reduceOnly close **reduces** risk, so it is exempt from the loss breaker, the daily
    order count, the exposure cap, the promotion gate, and both kill switches. The reasoning
    is the source system's and it is worth stating plainly: a halt that traps you in a losing
    position is more dangerous than the halt was meant to prevent. What survives is the
    structural boundary — the grant, the confirmation phrase, and reduceOnly itself, so this
    path can only ever shrink a position, never open one.

    ``limits`` is required here too — see ``evaluate_live_order_guard``. The close path needs
    only the confirmation phrase from it, but taking it from the same resolved object keeps
    one answer to "which limits was this judged against".
    """
    cfg = limits
    blocks: list[str] = []
    if not gate_open:
        blocks.append("live trading grant is not active (safety-flag gate closed)")
    if not cfg.confirmation_present():
        blocks.append(f"live confirmation phrase not present ({CONFIRMATION_ENV})")
    if not intent.get("reduce_only"):
        blocks.append("close guard requires a reduceOnly intent")
    repairs = _shape_repairs(intent)
    status = STATUS_BLOCKED if blocks else (STATUS_REPAIR_REQUIRED if repairs else STATUS_READY)
    return {
        "status": status,
        "approved": status == STATUS_READY,
        "blocks": blocks,
        "repairs": repairs,
        "close_guard": True,
    }


# --- the daily submission counter --------------------------------------------------

def count_today(root: Path | None = None, *, day: str | None = None) -> int:
    """Live orders submitted today. Ungated read; an unreadable counter fails closed by
    raising, because a counter that reads as zero would hand back the whole daily budget."""
    path = state_dir(root) / COUNTER_FILENAME
    if not path.is_file():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError(LIVE_COUNTER_UNREADABLE, "live order counter is unreadable") from exc
    if not isinstance(data, dict):
        raise ToolError(LIVE_COUNTER_UNREADABLE, "live order counter is malformed")
    try:
        return int(data.get(day or utc_day(), 0))
    except (TypeError, ValueError) as exc:
        raise ToolError(LIVE_COUNTER_UNREADABLE, "live order counter holds a non-integer") from exc


class LiveOrderCounter:
    """Durable per-day submission counter, behind the same live-trading grant as the ledger.

    Incremented for an *ambiguous* submit too: an order that may have reached the venue has
    to consume budget, or a flapping connection could spend the daily cap many times over.
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

    def record_submission(self, *, day: str | None = None) -> int:
        self._assert()
        target = state_dir(self._root)
        target.mkdir(parents=True, exist_ok=True)
        path = target / COUNTER_FILENAME
        key = day or utc_day()
        with locked(path.with_suffix(".lock"), code="LIVE_COUNTER_LOCKED", label="live order counter"):
            data: dict[str, Any] = {}
            if path.is_file():
                try:
                    loaded = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        data = loaded
                except (OSError, ValueError) as exc:
                    raise ToolError(LIVE_COUNTER_UNREADABLE, "live order counter is unreadable") from exc
            try:
                current = int(data.get(key, 0))
            except (TypeError, ValueError):
                current = 0
            data[key] = current + 1
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(path)
            return data[key]


class DryRunLiveOrderCounter:
    """Inert counter: counts nothing because nothing can be submitted without the grant."""

    filesystem_write = False

    def record_submission(self, *, day: str | None = None) -> int:
        return 0


def select_live_order_counter(*, now: str | None = None, root: Path | None = None) -> Any:
    """Return the durable counter if the live-trading grant is open, else the inert one."""
    from .live_pnl import LIVE_TRADING_ENV, REAL_LIVE_TRADING

    return safety_gate.select_gated(
        env_var=LIVE_TRADING_ENV,
        opt_in_value=REAL_LIVE_TRADING,
        flags=LIVE_TRADING_FLAGS,
        provider_id=LIVE_TRADING_PROVIDER_ID,
        default_factory=DryRunLiveOrderCounter,
        gated_factory=lambda authorization: LiveOrderCounter(root=root, authorization=authorization),
        now=now,
        root=root,
    )


def render_guard_text(verdict: Mapping[str, Any]) -> str:
    """ASCII-only guard report for the console."""
    lines = [f"live order guard: {verdict['status']} (approved={verdict['approved']})"]
    for block in verdict.get("blocks") or []:
        lines.append(f"  BLOCK  : {block}")
    for repair in verdict.get("repairs") or []:
        lines.append(f"  REPAIR : {repair}")
    return "\n".join(lines)
