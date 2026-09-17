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
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from runtime.read_only_kernel import integrity

from .. import safety_gate, timeutil
from ..errors import ToolError
from ..filelock import locked
from ..paths import repo_root as _repo_root
from ..safety_gate import Authorization
from .execution_stage import (
    PURPOSE_AUTONOMOUS,
    PURPOSE_PROBE,
    StageStatus,
    required_stage,
)
from .state import VENUE_MAINNET, venue_state_dir
from .live_pnl import (
    LIVE_TRADING_ENV,
    LIVE_TRADING_FLAGS,
    LIVE_TRADING_PROVIDER_ID,
    REAL_LIVE_TRADING,
    state_dir,
    utc_day,
)

STATUS_BLOCKED = "BLOCKED"
STATUS_REPAIR_REQUIRED = "REPAIR_REQUIRED"
STATUS_READY = "READY"

# Distinct from every other confirmation phrase in the system on purpose: pasting the
# canary or testnet phrase must not authorize autonomous live trading.
LIVE_CONFIRMATION_PHRASE = "I_UNDERSTAND_THIS_TRADES_LIVE_FUNDS_AUTONOMOUSLY"

# ...and the converse: the autonomous phrase must not authorize a canary-mode order either. That
# is a deliberate, one-at-a-time operator order — since the canary door went (2026-09-15, PR1r),
# only the slippage probe places one — so it carries its own phrase and its own env. One phrase
# per capability is the whole point — pasting the wrong one authorizes nothing.
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


def normalize_symbols(symbols: Any) -> tuple[str, ...]:
    """The budget's allowlist as the guard compares it: stripped, upper-cased, deduplicated.

    Same normalization ``build_live_trading_budget_record`` applies when the record is
    written, applied again on read rather than trusted. A record edited by hand — the one
    way an allowlist can arrive un-normalized — must not widen scope through a case
    difference, and must not narrow it through one either."""
    if isinstance(symbols, (str, bytes)):
        return ()
    try:
        items = list(symbols or ())
    except TypeError:
        return ()
    seen: list[str] = []
    for item in items:
        name = str(item).strip().upper()
        if name and name not in seen:
            seen.append(name)
    return tuple(seen)

# The ceiling a configured cap can never exceed, whatever the operator types. Source value.
DEFAULT_ABSOLUTE_MAX_NOTIONAL_USDT = 200.0

COUNTER_FILENAME = "live_order_counter.json"
LIVE_COUNTER_UNREADABLE = "LIVE_COUNTER_UNREADABLE"
LIVE_DAILY_ORDER_CAP_REACHED = "LIVE_DAILY_ORDER_CAP_REACHED"

BRACKET_BREAKER_FILENAME = "live_bracket_failures.json"
LIVE_BRACKET_BREAKER_UNREADABLE = "LIVE_BRACKET_BREAKER_UNREADABLE"

# Five consecutive failures. **2 until 2026-08-19**, and that number was the first incident's own:
# the first protective bracket this runtime ever placed was refused, so was the second on the next
# signal seventeen minutes later. One rejection can be the venue having a moment; two in a row is
# the path being broken, and every repetition is an entry that fills and is closed again
# immediately — a round trip in fees for no exposure, repeating as fast as the daily order budget
# refills. That reasoning is unchanged and is why a limit exists at all.
#
# What moved the number is the SECOND failure mode, which is precisely what the tunables record
# named as the thing that would move it: the `-1111` tick-residue rejection measured
# 2026-08-18T14:28:52Z and fixed the next day. That one is deterministic rather than intermittent —
# arithmetic refused 40% of BTCUSDT stops, on no venue condition at all — so a limit of 2 spent the
# entire budget of attempts before an operator could read the first `error_detail`, and latched the
# door on a cause the breaker record already held in full.
#
# 5 is a Thomas decision (2026-08-19), not a measurement. It buys room for an intermittent venue
# fault to clear itself, and it costs up to five naked round trips before the door shuts — ~0.03
# USDT each at current sizing, so the price of the change is fees, not exposure. Nothing else about
# the breaker relaxes: the streak still resets only on a bracket that actually RESTS, it still does
# not expire, and it still takes a written operator reason to clear.
MAX_CONSECUTIVE_BRACKET_FAILURES = 5

# How old the account read an entry is judged on may be when the entry is judged (Thomas decisions
# 18 and 24, PR2c-1). A door reads the account once, then settles, protects and prices before it
# decides — normally seconds, with no bound: each venue call in between may take its own timeout.
# Past a minute the balance and the exposure the caps are judged on may no longer be the account's.
MAX_ACCOUNT_AGE_SECONDS = 60


def account_age_seconds(collected_at: Any, *, clock: Any) -> float | None:
    """How long before ``clock`` the account was read, or None when that cannot be said. Pure."""
    try:
        return (timeutil.parse_iso(str(clock)) - timeutil.parse_iso(str(collected_at))).total_seconds()
    except (TypeError, ValueError, OverflowError):
        return None


def account_fresh(collected_at: Any, *, clock: Any) -> bool:
    """Whether an account read at ``collected_at`` may still be judged at ``clock``. A read that
    seems to come after the judgment (a clock stepped back) cannot show its age and is not fresh."""
    age = account_age_seconds(collected_at, clock=clock)
    return age is not None and 0 <= age <= MAX_ACCOUNT_AGE_SECONDS


# The checks `evaluate_live_order_guard` runs, in its order (PR2b). Every block names one of these,
# and the tests hold the roster and the blocks together.
GUARD_CHECK_IDS = (
    "budget_registered",
    "symbol_allowlisted",
    "trading_opted_in",
    "confirmation_phrase",
    "manual_kill_switch_off",
    "runtime_active",
    "daily_loss_within_limit",
    "execution_stage_admits",
    "not_connectivity_test",
    "order_notional_within_cap",
    "daily_order_count_within_cap",
    "open_exposure_within_cap",
)
INTENT_SHAPE_CHECK = "intent_shape_complete"

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
    payload = {
        "symbol": intent.get("symbol"),
        "direction": intent.get("direction"),
        "strategy_id": intent.get("strategy_id"),
        "candle_time": intent.get("candle_time") or intent.get("created_at"),
        "position_id": intent.get("position_id"),
    }
    # A bar time names a bar only together with its timeframe (PR2a review): a 4h bar and a 1d bar
    # open at the same instant every day, and a display strategy id can be reused across
    # generations, so without it two contexts mint the same client order id a day apart. Added
    # only when present, so the probe's and the testnet cycle's ids — no timeframe — are unchanged.
    if intent.get("timeframe"):
        payload["timeframe"] = intent.get("timeframe")
    key = make_idempotency_key(payload)
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
        # The context the bar belongs to (PR2a review); part of the identity when present.
        "timeframe": plan.get("timeframe"),
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


def _notional_at(intent: Mapping[str, Any], price: Any) -> float | None:
    """The order's quantity at ``price``, or None when either is not a positive finite number."""
    if isinstance(price, bool) or not isinstance(price, (int, float)):
        return None
    try:
        quantity, price = float(intent.get("quantity")), float(price)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not (0 < quantity < float("inf") and 0 < price < float("inf")):
        return None
    return round(quantity * price, 8)


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

    Returns ``(limits, budget_status)``. A missing / tampered / invalid budget, or a legacy one
    outside the validity window it was registered with (a budget registered since 2026-09-15
    carries none — PR1r), yields the blocking-default caps and a status whose ``valid`` is
    ``False``, so the guard's budget check (and its unconfigured-caps checks) block — no live
    order without a registered budget (``autonomous_spend_without_registered_budget: '0'``). The
    env cap vars (``MVP_LIVE_MAX_*``) no longer authorize an order; the registered budget
    supersedes them.

    **Both** confirmation phrases and the manual kill remain env, and are carried through on
    every branch: ``MVP_LIVE_CONFIRMATION`` (autonomous entries and every close),
    ``MVP_LIVE_CANARY_CONFIRMATION`` (canary mode — the slippage probe),
    ``MVP_LIVE_MANUAL_KILL_SWITCH``. A phrase proving intent and a halt are operator state, not
    registered caps. The canary phrase was omitted here until 2026-07-26, which left the canary
    door of the day (``place_canary_order.py``, removed 2026-09-15) permanently refused with
    "canary confirmation phrase not present". Failing closed, but on the step the operator has to
    take next, and only discoverable standing at the terminal with real keys. The probe composes
    the same join today.

    The budget's retired ``caps.min_clean_canary_orders`` is never read here: a record registered
    before PR1r still carries it (schema-accepted, self-hashed, ignored) and a newer one does not,
    so indexing it would raise on the new shape — before the leg settles or protects anything."""
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
    # The registered budget's symbol allowlist. Defaults to EMPTY, which blocks every symbol —
    # the same fail-closed default as `budget_registered=False`, and for the same reason: a
    # caller that does not state the scope must not be able to authorize an order outside it
    # by omission. The budget declared this list from the first record written; nothing read
    # it, so a budget naming BTCUSDT sat next to an active pool trading four other symbols
    # and the two never disagreed out loud.
    allowed_symbols: Sequence[str] = (),
    # The machine's execution stage, resolved by the caller (`execution_stage.resolve_execution_stage`)
    # and passed in like every other runtime fact — this function still reads no file. No default,
    # for the reason `current_open_notional_usdt` and `limits` have none: a caller that forgets it
    # must not be able to authorize an entry the stage does not admit. A record that is missing or
    # does not bind resolves to READ_ONLY, which admits nothing (Thomas decision 9).
    execution_stage: StageStatus,
    canary: bool = False,
    # The price the market shows now, when the order was priced on something older (PR2c-1,
    # decision 24): the autonomous entry is priced on its bar's close. The size and exposure caps
    # are then judged at the higher of the two, so a market that rose since the bar cannot carry
    # the order past a cap. None judges the order at its own price, as the probe (priced on this
    # very read) and every earlier caller are.
    reference_price: float | None = None,
) -> dict[str, Any]:
    """The last gate before a live entry. Pure: it reads no file and opens no socket.

    Every runtime fact arrives as an argument so this can be exhaustively tested without a
    venue, a switch, or a clock. Checks accumulate — the caller sees the complete refusal.

    ``budget_registered`` states whether a valid registered live-trading budget backs the
    ``limits`` — the caller resolves both together (``resolve_live_order_limits``). It defaults
    to ``False`` (fail-closed): a caller that does not resolve a budget cannot accidentally
    authorize an order on caps that no budget backs.

    ``canary`` marks a deliberate one-at-a-time operator order — today only the slippage probe
    (``scripts/run_slippage_probe.py --fire``). It changes two things: the confirmation phrase
    compared is the **canary** phrase, not the autonomous one, so the autonomous phrase cannot
    authorize a canary-mode order and the canary phrase cannot authorize autonomous trading; and
    the execution stage is judged against the probe's purpose rather than the autonomous one.
    Today both purposes need the same rung, so the two modes are gated alike — the split exists so
    a later decision can separate them in the ladder's own module, never here.

    It used to change a second thing — the clean-canary promotion gate did not apply to it — and
    that gate is gone for both modes (Thomas, 2026-09-15, PR1r, with the canary door). Every
    check that remains — the opt-in, both kill switches, the loss breaker, the registered
    budget, the symbol allowlist, the size / daily-count / exposure caps, the connectivity
    refusal, the intent shape — applies identically. Sharing one guard rather than writing a
    second one is deliberate: a check added later cannot land on only one of the two paths.
    ``canary`` defaults to ``False``, so a caller that does not say otherwise is judged on the
    autonomous phrase.
    """
    cfg = limits
    blocks: list[str] = []
    repairs: list[str] = []
    # Which named check each block failed (PR2b): the pre-order gate records every check by name,
    # and the prose in `blocks` stays exactly what an operator has always read.
    failed: dict[str, list[str]] = {}

    def block(check_id: str, message: str) -> None:
        blocks.append(message)
        failed.setdefault(check_id, []).append(message)

    # 0. The registered trading budget. ``autonomous_spend_without_registered_budget: '0'`` —
    #    no live order until a self-hashed budget record is registered and valid. The caps below
    #    come FROM that budget (via resolve_live_order_limits); a missing/tampered/invalid budget,
    #    or a legacy one outside its stored window, arrives here as budget_registered=False and
    #    blocks regardless of the env caps.
    if not budget_registered:
        block("budget_registered", 
            "no valid registered live-trading budget "
            "(autonomous_spend_without_registered_budget); register one with "
            "scripts/register_live_trading_budget.py"
        )
    # 0b. The symbol this budget authorizes. Registered from the first budget record and read
    #     by nothing until now, so the caps bound how MUCH could be traded while nothing bound
    #     WHAT. Applied in canary mode too: a probe is a smaller real order, not a different
    #     kind of one, and the operator widens scope by re-registering rather than by aiming a
    #     canary-mode order somewhere the budget does not name.
    allowlist = normalize_symbols(allowed_symbols)
    order_symbol = str(intent.get("symbol") or "").strip().upper()
    if not allowlist:
        block("symbol_allowlisted", 
            f"no symbol allowlist backs this order (an unstated scope authorizes nothing); "
            f"{REGISTER_BUDGET_HINT}"
        )
    elif not order_symbol:
        block("symbol_allowlisted", "live order intent names no symbol, so it cannot be checked against the allowlist")
    elif order_symbol not in allowlist:
        block("symbol_allowlisted", 
            f"{order_symbol} is not in the registered budget's symbol allowlist "
            f"({', '.join(allowlist)}); re-register the budget to widen it"
        )
    # 1. The switch. Without the operator's live-trading opt-in nothing else matters.
    if not gate_open:
        block("trading_opted_in", f"live trading is not enabled ({LIVE_TRADING_ENV} is not '{REAL_LIVE_TRADING}')")
    # 2. The phrase. The opt-in enables the capability; the phrase proves intent to use it. One
    #    phrase per capability: a canary-mode order (today the slippage probe) is authorized by the
    #    canary phrase, never the autonomous one, and the canary phrase alone cannot authorize an
    #    autonomous entry. It does not keep a probe session from trading autonomously: every close
    #    (the probe's exits included) needs the autonomous phrase, and that phrase authorizes
    #    autonomous entries too — what holds them back in a probe session is that no pool entry is
    #    LIVE-tier (docs/DEPLOYMENT.md).
    if canary:
        if not cfg.canary_confirmation_present():
            block("confirmation_phrase", f"canary confirmation phrase not present ({CANARY_CONFIRMATION_ENV})")
    elif not cfg.confirmation_present():
        block("confirmation_phrase", f"live confirmation phrase not present ({CONFIRMATION_ENV})")
    # 3. The trader's own halt.
    if cfg.manual_kill_switch:
        block("manual_kill_switch_off", f"manual kill switch is engaged ({MANUAL_KILL_SWITCH_ENV})")
    # 4. The runtime's halt. Binds kill_blocks: external_execution, which had no door until
    #    now — a PAUSED or KILLED runtime must not open a live position.
    if not runtime_active:
        block("runtime_active", "runtime is not ACTIVE; kill_blocks external_execution forbids a live entry")
    # 5. Today's realized loss. An unconfigured limit arrives here already True.
    if daily_loss_breached:
        if cfg.daily_loss_limit_usdt <= 0:
            block("daily_loss_within_limit", f"daily loss limit is not configured; {REGISTER_BUDGET_HINT}")
        else:
            block(
                "daily_loss_within_limit",
                f"daily realized-loss limit {cfg.daily_loss_limit_usdt} USDT reached - halted for today"
            )
    # 6. The execution stage: what rung this machine is registered at, and whether that record
    #    binds (PR1b, Thomas decisions 1/8/9). It took the place of the clean-canary promotion gate
    #    retired here on 2026-09-15 (PR1r) — that gate counted a frozen file; this one is a
    #    registered, approved, single-answer record. A missing or unbound record reads READ_ONLY
    #    and admits nothing, which is the fail-closed direction: no stage, no new exposure. It
    #    gates NEW exposure only — `evaluate_live_close_guard` never reads it, so a demotion can
    #    never trap an open position.
    purpose = PURPOSE_PROBE if canary else PURPOSE_AUTONOMOUS
    if not execution_stage.allows(purpose):
        needs = required_stage(purpose)
        why = (f"reads {execution_stage.stage}" if execution_stage.valid
               else f"reads READ_ONLY ({execution_stage.reason_code})")
        block(
            "execution_stage_admits",
            f"execution stage {why}; a {'probe' if canary else 'live'} entry needs {needs}"
            f" - register a transition with scripts/register_execution_stage.py (Thomas approves it)"
        )
    # 7. A connectivity probe must never ride the autonomous path.
    if intent.get("connectivity_test"):
        block("not_connectivity_test", "connectivity_test intent cannot use the live order path")

    # 8. Per-order size — at the higher of the order's own price and ``reference_price``. The
    #    order's own notional still has to be there: a reference price does not stand in for it.
    own_notional = _notional_of(intent)
    notional = own_notional
    if reference_price is not None:
        at_reference = _notional_at(intent, reference_price)
        if at_reference is None:
            repairs.append("the reference price the caps are judged at is not a positive number")
        else:
            notional = max(own_notional, at_reference)
    if cfg.max_order_notional_usdt <= 0:
        block("order_notional_within_cap", f"per-order cap is not configured; {REGISTER_BUDGET_HINT}")
    elif cfg.max_order_notional_usdt > cfg.absolute_max_notional_usdt:
        block(
            "order_notional_within_cap",
            f"configured cap {cfg.max_order_notional_usdt} exceeds the absolute ceiling "
            f"{cfg.absolute_max_notional_usdt}"
        )
    if own_notional <= 0:
        repairs.append("order notional missing or non-positive")
    elif cfg.max_order_notional_usdt > 0 and notional > cfg.effective_max_notional_usdt:
        block(
            "order_notional_within_cap",
            f"order notional {notional} exceeds the effective cap {cfg.effective_max_notional_usdt}"
        )

    # 9. Orders per UTC day.
    if cfg.max_daily_order_count <= 0:
        block("daily_order_count_within_cap", f"daily order cap is not configured; {REGISTER_BUDGET_HINT}")
    elif submitted_today >= cfg.max_daily_order_count:
        block(
            "daily_order_count_within_cap",
            f"daily order cap reached ({submitted_today}/{cfg.max_daily_order_count})"
        )

    # 10. Total open exposure, counting what this order would add.
    if cfg.max_open_notional_usdt <= 0:
        block("open_exposure_within_cap", f"open exposure cap is not configured; {REGISTER_BUDGET_HINT}")
    elif current_open_notional_usdt + notional > cfg.max_open_notional_usdt:
        block(
            "open_exposure_within_cap",
            f"open exposure {current_open_notional_usdt} + {notional} exceeds the cap "
            f"{cfg.max_open_notional_usdt}"
        )

    # 11. Intent shape.
    repairs.extend(_shape_repairs(intent))

    status = STATUS_BLOCKED if blocks else (STATUS_REPAIR_REQUIRED if repairs else STATUS_READY)
    checks = [
        {"check": check_id, "ok": check_id not in failed,
         "detail": "; ".join(failed[check_id]) if check_id in failed else None}
        for check_id in GUARD_CHECK_IDS
    ]
    checks.append({"check": INTENT_SHAPE_CHECK, "ok": not repairs,
                   "detail": "; ".join(repairs) if repairs else None})
    return {
        "status": status,
        "approved": status == STATUS_READY,
        "blocks": blocks,
        "repairs": repairs,
        # Every check this guard ran, by name, passed or not (PR2b) — what the pre-order gate
        # records on its snapshot. `approved` is still derived from `blocks` and `repairs` alone.
        "checks": checks,
        # What the caps judged: the order's own notional, or its quantity at the reference price
        # when that is higher (PR2c-1).
        "notional_usdt": notional,
        "order_notional_usdt": own_notional,
        "reference_price": reference_price,
        "notional_cap_usdt": cfg.max_order_notional_usdt,
        "effective_cap_usdt": cfg.effective_max_notional_usdt,
        "absolute_ceiling_usdt": cfg.absolute_max_notional_usdt,
        "open_exposure_cap_usdt": cfg.max_open_notional_usdt,
        "current_open_notional_usdt": current_open_notional_usdt,
        "submitted_today": submitted_today,
        "max_daily_order_count": cfg.max_daily_order_count,
        "daily_loss_limit_usdt": cfg.daily_loss_limit_usdt,
        "daily_loss_breached": daily_loss_breached,
        # Structured alongside the prose block, so a caller decides on the field rather than by
        # matching an error string — the reason `blocks` is the report and never the interface.
        "order_symbol": order_symbol,
        "allowed_symbols": list(allowlist),
        "symbol_allowlisted": bool(allowlist) and order_symbol in allowlist,
        "close_guard": False,
        "canary": bool(canary),
        # What the stage answered for this order, so the record shows the rung the machine was at
        # rather than only that something refused.
        "execution_stage": execution_stage.stage,
        "execution_stage_valid": bool(execution_stage.valid),
        "execution_stage_required": required_stage(PURPOSE_PROBE if canary else PURPOSE_AUTONOMOUS),
    }


def evaluate_live_close_guard(
    intent: Mapping[str, Any],
    *,
    gate_open: bool,
    limits: LiveOrderLimits,
) -> dict[str, Any]:
    """The deliberately narrower gate for closing an open live position.

    A reduceOnly close **reduces** risk, so it is exempt from the loss breaker, the daily
    order count, the exposure cap, and both kill switches. The reasoning
    is the source system's and it is worth stating plainly: a halt that traps you in a losing
    position is more dangerous than the halt was meant to prevent. What survives is the
    structural boundary — the live-trading opt-in, the confirmation phrase, and reduceOnly
    itself, so this path can only ever shrink a position, never open one.

    Moving the opt-in off the per-machine grant (2026-07-28) removed the sharpest remaining
    version of exactly the trap this docstring warns about: the grant carried an expiry, so a
    position opened under a valid grant could find the close path shut at 00:00 with nothing
    having gone wrong. An env var does not expire. The gate still has to be open to close —
    that is the structural boundary and it stays — but it can no longer swing shut on its own.

    ``limits`` is required here too — see ``evaluate_live_order_guard``. The close path needs
    only the confirmation phrase from it, but taking it from the same resolved object keeps
    one answer to "which limits was this judged against".
    """
    cfg = limits
    blocks: list[str] = []
    if not gate_open:
        blocks.append(f"live trading is not enabled ({LIVE_TRADING_ENV} is not '{REAL_LIVE_TRADING}')")
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

def _stored_count(value: Any) -> int:
    """One day's stored count, or refuse (PR2a review).

    Only the counter writes this file, and only non-negative ints. Anything else is damage, and a
    damaged count must not read as room under the cap: a negative one passed the reservation's
    `current >= limit` for as many orders as it was below zero."""
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ToolError(LIVE_COUNTER_UNREADABLE, "live order counter holds a malformed count")
    return value


def count_today(root: Path | None = None, *, day: str | None = None,
                venue: str = VENUE_MAINNET) -> int:
    """Orders submitted today at ``venue``. Ungated read; an unreadable counter fails closed by
    raising, because a counter that reads as zero would hand back the whole daily budget.

    Each venue counts its own orders (PR1d-0): a testnet order must not spend the live daily cap,
    and a live order must not be hidden by one."""
    path = venue_state_dir(root, venue=venue) / COUNTER_FILENAME
    if not path.is_file():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError(LIVE_COUNTER_UNREADABLE, "live order counter is unreadable") from exc
    if not isinstance(data, dict):
        raise ToolError(LIVE_COUNTER_UNREADABLE, "live order counter is malformed")
    return _stored_count(data.get(day or utc_day()))


class LiveOrderCounter:
    """Durable per-day submission counter, behind the same live-trading switch as the ledger.

    Incremented for an *ambiguous* submit too: an order that may have reached the venue has
    to consume budget, or a flapping connection could spend the daily cap many times over.
    """

    provider_id = LIVE_TRADING_PROVIDER_ID
    filesystem_write = True

    def __init__(self, *, root: Path | None = None, authorization: Authorization | None = None,
                 venue: str = VENUE_MAINNET):
        self._root = root
        self._authorization = authorization
        # Which venue's counter this is. Default mainnet: a caller that says nothing is the live
        # path, exactly as it was before the venue axis existed (PR1d-0).
        self._venue = venue

    def _assert(self) -> None:
        safety_gate.assert_authorization(
            self._authorization,
            required_flags=LIVE_TRADING_FLAGS,
            provider_id=self.provider_id,
            now=timeutil.utc_now_iso(),
        )

    def record_submission(self, *, day: str | None = None) -> int:
        """Count an order already sent. The testnet cycle's rule; the mainnet entry paths reserve
        their slot BEFORE the send instead (:meth:`reserve_submission`)."""
        return self._increment(day=day, limit=None)

    def reserve_submission(self, *, limit: int, day: str | None = None) -> int:
        """Take one of today's order slots before the order is sent, or refuse (PR2a).

        The guard judges ``submitted_today`` from a read taken earlier in the leg, so two
        processes — the scheduler's live leg and an operator's probe — could both read a count
        under the cap and both send. The comparison and the increment happen here, under the
        counter's own lock, so at most ``limit`` reservations succeed in a day whichever process
        makes them. A reserved slot stays spent even if the send then fails: the rule
        ``record_submission`` already applied to an ambiguous submit, moved ahead of the send.
        """
        return self._increment(day=day, limit=limit)

    def _increment(self, *, day: str | None, limit: int | None) -> int:
        self._assert()
        target = venue_state_dir(self._root, venue=self._venue)
        target.mkdir(parents=True, exist_ok=True)
        path = target / COUNTER_FILENAME
        key = day or utc_day()
        with locked(path.with_suffix(".lock"), code="LIVE_COUNTER_LOCKED", label="live order counter"):
            data: dict[str, Any] = {}
            if path.is_file():
                try:
                    loaded = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise ToolError(LIVE_COUNTER_UNREADABLE, "live order counter is unreadable") from exc
                if not isinstance(loaded, dict):
                    # Refused, not replaced: rewriting it would erase the evidence and hand back
                    # the day's whole budget (PR2a review).
                    raise ToolError(LIVE_COUNTER_UNREADABLE, "live order counter is malformed")
                data = loaded
            current = _stored_count(data.get(key))
            if limit is not None and current >= limit:
                # Also the answer for a cap of zero: an unconfigured cap reserves nothing.
                raise ToolError(
                    LIVE_DAILY_ORDER_CAP_REACHED,
                    f"daily order cap reached ({current}/{limit}); no order slot was reserved",
                )
            data[key] = current + 1
            tmp = path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(data, ensure_ascii=False, indent=1))
                handle.flush()
                # The count is the pre-send authority for the cap now (PR2a): a reserved slot that
                # a crash forgets is an order the day's budget no longer knows about. The file is
                # synced, as the live book's is; the directory entry is not, as for the book.
                os.fsync(handle.fileno())
            tmp.replace(path)
            return data[key]


class DryRunLiveOrderCounter:
    """Inert counter: counts nothing because nothing can be submitted with the switch off."""

    filesystem_write = False

    def record_submission(self, *, day: str | None = None) -> int:
        return 0

    def reserve_submission(self, *, limit: int, day: str | None = None) -> int:
        return 0


def select_live_order_counter(*, now: str | None = None, root: Path | None = None) -> Any:
    """Return the durable counter if live trading is opted in, else the inert one.

    On ``select_env_gated`` with the adapter and, until 2026-09-15, the canary registry (Thomas,
    2026-07-28), and for the same reason the registry was: this counter is what the daily-order
    cap reads. A durable adapter with an inert counter is an uncapped account."""
    return safety_gate.select_env_gated(
        env_var=LIVE_TRADING_ENV,
        opt_in_value=REAL_LIVE_TRADING,
        flags=LIVE_TRADING_FLAGS,
        provider_id=LIVE_TRADING_PROVIDER_ID,
        default_factory=DryRunLiveOrderCounter,
        gated_factory=lambda authorization: LiveOrderCounter(root=root, authorization=authorization),
    )


# --- the bracket-failure breaker ---------------------------------------------------
#
# A live entry that fills and cannot be protected is closed again immediately (`live_leg`
# Rule 2), which is the safe response and was never the problem. The problem is that nothing
# counted it: the loop *signal -> fill -> refuse -> close* was bounded only by the registered
# budget's orders-per-day, which refills at every UTC midnight. So the runtime could spend
# fees on entries it can never hold, indefinitely, and the only thing that would ever say so
# was a person reading the order records.
#
# This counter is what makes the loop stop on its own. It counts CONSECUTIVE failures and is
# reset by a bracket that actually rests — a transient rejection does not latch the door, and
# a broken bracket path shuts it after `MAX_CONSECUTIVE_BRACKET_FAILURES`.
#
# It deliberately does NOT reset at midnight. The daily order budget does, and that reset is
# exactly what let the loop resume; a breaker that expires on the same clock as the budget it
# is bounding would bound nothing. Only a working bracket or an operator clears this.


def _empty_bracket_record() -> dict[str, Any]:
    return {
        "consecutive": 0,
        "total": 0,
        "last_failure_at": None,
        "last_symbol": None,
        "last_status": None,
        "last_reason_codes": [],
        "last_error_detail": None,
        "cleared_at": None,
        "cleared_by": None,
        "cleared_reason": None,
    }


def read_bracket_failures(root: Path | None = None, *, venue: str = VENUE_MAINNET) -> dict[str, Any]:
    """The consecutive bracket-failure record. Ungated read; an unreadable file raises.

    Fails closed for ``count_today``'s reason pointed the other way: a breaker whose state reads
    as zero because the file is corrupt is a breaker that reopens the door it exists to hold
    shut. The readiness board and the entry decision both read through here, so there is one
    answer to "how many brackets have failed in a row" rather than two that can disagree.
    """
    path = venue_state_dir(root, venue=venue) / BRACKET_BREAKER_FILENAME
    if not path.is_file():
        return _empty_bracket_record()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError(LIVE_BRACKET_BREAKER_UNREADABLE, "bracket failure record is unreadable") from exc
    if not isinstance(data, dict):
        raise ToolError(LIVE_BRACKET_BREAKER_UNREADABLE, "bracket failure record is malformed")
    record = _empty_bracket_record()
    record.update({key: data.get(key, record[key]) for key in record})
    try:
        record["consecutive"] = int(record["consecutive"])
        record["total"] = int(record["total"])
    except (TypeError, ValueError) as exc:
        raise ToolError(
            LIVE_BRACKET_BREAKER_UNREADABLE, "bracket failure record holds a non-integer count"
        ) from exc
    if not isinstance(record["last_reason_codes"], list):
        record["last_reason_codes"] = []
    return record


def bracket_breaker_status(
    root: Path | None = None, *, limit: int = MAX_CONSECUTIVE_BRACKET_FAILURES,
    venue: str = VENUE_MAINNET,
) -> dict[str, Any]:
    """``read_bracket_failures`` plus the verdict, so no caller re-derives the comparison."""
    record = read_bracket_failures(root, venue=venue)
    return {
        **record,
        "limit": limit,
        "tripped": record["consecutive"] >= limit,
    }


class LiveBracketFailureBreaker:
    """Durable consecutive-bracket-failure counter, behind the live-trading switch.

    Gated exactly as ``LiveOrderCounter`` is, and for the same argument read the other way
    round: an inert counter beside a durable adapter is an account with no daily cap, and an
    inert breaker beside a durable adapter is a door that never shuts. Whichever way the switch
    is set, the state that bounds real money has to be as durable as the money path.
    """

    provider_id = LIVE_TRADING_PROVIDER_ID
    filesystem_write = True

    def __init__(self, *, root: Path | None = None, authorization: Authorization | None = None,
                 venue: str = VENUE_MAINNET):
        self._root = root
        self._authorization = authorization
        self._venue = venue

    def _assert(self) -> None:
        safety_gate.assert_authorization(
            self._authorization,
            required_flags=LIVE_TRADING_FLAGS,
            provider_id=self.provider_id,
            now=timeutil.utc_now_iso(),
        )

    def _update(self, mutate: Any) -> dict[str, Any]:
        self._assert()
        target = venue_state_dir(self._root, venue=self._venue)
        target.mkdir(parents=True, exist_ok=True)
        path = target / BRACKET_BREAKER_FILENAME
        with locked(
            path.with_suffix(".lock"),
            code="LIVE_BRACKET_BREAKER_LOCKED",
            label="live bracket failure record",
        ):
            # This venue's record, not the live one: a breaker that reads another venue's
            # baseline inherits its streak — and when that one is clean, resets its own on
            # every failure, which is a breaker that can never trip. Review of #876.
            record = read_bracket_failures(self._root, venue=self._venue)
            mutate(record)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(path)
            return record

    def record_failure(
        self,
        *,
        symbol: str,
        status: str,
        at: str,
        reason_codes: Sequence[str] = (),
        error_detail: Any = None,
    ) -> dict[str, Any]:
        """One entry filled and could not be protected. Returns the record as stored.

        ``error_detail`` is the venue's own numeric code and text (PR #426) carried onto the
        breaker record, because the container that held the logs is the thing most likely to be
        gone by the time anyone asks why the door shut.
        """
        def mutate(record: dict[str, Any]) -> None:
            record["consecutive"] += 1
            record["total"] += 1
            record["last_failure_at"] = at
            record["last_symbol"] = symbol
            record["last_status"] = status
            record["last_reason_codes"] = [str(code) for code in reason_codes]
            record["last_error_detail"] = error_detail

        return self._update(mutate)

    def record_success(self) -> dict[str, Any]:
        """A bracket rested at the venue: the path works, so the streak is over."""
        def mutate(record: dict[str, Any]) -> None:
            record["consecutive"] = 0

        return self._update(mutate)

    def clear(self, *, actor: str, reason: str, at: str) -> dict[str, Any]:
        """The operator's reset, after looking at why the brackets were refused.

        Separate from ``record_success`` because the two are not the same statement: one is the
        venue demonstrating the path works, the other is a person saying they have dealt with
        it. Only the second is worth recording who and why for.
        """
        def mutate(record: dict[str, Any]) -> None:
            record["consecutive"] = 0
            record["cleared_at"] = at
            record["cleared_by"] = actor
            record["cleared_reason"] = reason

        return self._update(mutate)


class DryRunLiveBracketFailureBreaker:
    """Inert breaker: counts nothing, because with the switch off no bracket is ever placed."""

    filesystem_write = False

    def record_failure(self, **_kwargs: Any) -> dict[str, Any]:
        return _empty_bracket_record()

    def record_success(self) -> dict[str, Any]:
        return _empty_bracket_record()

    def clear(self, **_kwargs: Any) -> dict[str, Any]:
        return _empty_bracket_record()


def select_live_bracket_breaker(*, now: str | None = None, root: Path | None = None) -> Any:
    """Return the durable breaker if live trading is opted in, else the inert one."""
    return safety_gate.select_env_gated(
        env_var=LIVE_TRADING_ENV,
        opt_in_value=REAL_LIVE_TRADING,
        flags=LIVE_TRADING_FLAGS,
        provider_id=LIVE_TRADING_PROVIDER_ID,
        default_factory=DryRunLiveBracketFailureBreaker,
        gated_factory=lambda authorization: LiveBracketFailureBreaker(
            root=root, authorization=authorization
        ),
    )


# --- the live entry marks (PR2a) ---------------------------------------------------
#
# Two rules paper has always kept and the live leg did not, because both live inside
# `paper.run_paper_update` BELOW the line where the route is published to the live leg:
#
# - **one entry per context per bar.** The 15-minute fan-out re-evaluates a 4h or 1d bar up to
#   96 times, and the route it hands the live leg is the same ENTRY_CANDIDATE every time. The
#   one-position-per-symbol cap hides that while a position is open; once a stop closes it
#   inside the bar, the next tick entered again on the same signal. The client order id did not
#   stop it either — it was keyed on the wall clock.
# - **the post-stop-loss cooldown** (`paper.COOLDOWN_BARS_AFTER_STOPLOSS`), the rule the paper
#   evidence behind every live promotion was produced under.
#
# Paper marks every EVALUATION of a bar; the live mark is taken only when an order is about to be
# sent. A live refusal is usually transient (an order book read, the account), and retrying it
# later in the same bar sends nothing twice: no order has carried the bar's client id yet. After
# a send the bar is spent — the retry would reuse that id, and the venue answering with the old
# filled order would book a position that no longer exists.
#
# Bars are named by the feature row's ``timestamp``, which is the bar's OPEN time
# (`features.build_feature_rows`); the cooldown bound is on that same basis.
#
# Fail direction is the opposite of paper's marks, on purpose: those read a corrupt file as "no
# mark" (one redundant paper evaluation at worst); a corrupt live file refuses entries, because
# here "no mark" is a real order on a bar that may already have had one.

ENTRY_MARKS_FILENAME = "live_entry_marks.json"
ENTRY_MARKS_VERSION = "live_entry_marks.v1"
LIVE_ENTRY_MARKS_UNREADABLE = "LIVE_ENTRY_MARKS_UNREADABLE"
LIVE_ENTRY_MARKS_UNKNOWN = "LIVE_ENTRY_MARKS_UNKNOWN"
LIVE_ENTRY_BAR_UNKNOWN = "LIVE_ENTRY_BAR_UNKNOWN"
LIVE_ENTRY_BAR_ALREADY_ENTERED = "LIVE_ENTRY_BAR_ALREADY_ENTERED"
LIVE_ENTRY_STOP_LOSS_COOLDOWN = "LIVE_ENTRY_STOP_LOSS_COOLDOWN"
LIVE_ENTRY_COOLDOWN_UNCOMPUTABLE = "LIVE_ENTRY_COOLDOWN_UNCOMPUTABLE"

# --- the symbol's entry in flight (PR2b-2) ---
#
# The book is one record per symbol, and the venue nets per symbol. Two doors can open an entry on a
# symbol: the scheduler's autonomous leg and the operator's `--fire` probe, in different processes.
# Each checked "the symbol is free" on facts it read earlier and then sent, so two entries a few
# seconds apart could both pass, and the second booking would overwrite the first. A door now takes
# the symbol before it sends. Under the marks lock it checks that no other entry is in flight there
# and that the book holds no position, and it gives the symbol back once the book says what the
# venue holds.
#
# A claim nobody gives back expires (Thomas decision 21, 2026-09-17). A process that dies mid-entry
# must not hold the symbol for good. The worst entry takes 2-3 minutes (the send, its confirmation,
# two legs and a naked close, each with its own timeout), so 30 minutes is about ten of those. If
# the dead entry did reach the venue, the next reconciliation sees a position the book does not
# have, and that refuses entries on its own. Closes never read the claim.
LIVE_ENTRY_CLAIM_TTL_MINUTES = 30
LIVE_ENTRY_SYMBOL_IN_FLIGHT = "LIVE_ENTRY_SYMBOL_IN_FLIGHT"
LIVE_ENTRY_SYMBOL_OCCUPIED = "LIVE_ENTRY_SYMBOL_OCCUPIED"
LIVE_ENTRY_CLAIM_MALFORMED = "LIVE_ENTRY_CLAIM_MALFORMED"
# The claim a door went to give back is not its own any more (it expired and another order took
# it, or it is gone). Nothing is removed; the door decides what that means (PR2b-2 review).
LIVE_ENTRY_CLAIM_LOST = "LIVE_ENTRY_CLAIM_LOST"
# The global caps, judged again at the claim (PR2c-3, Thomas decision 26). Two doors entering two
# different symbols each judged "the book + my order fits" on facts read before the other's order,
# and the symbol claim did not stop them. Under the marks lock the claim now counts the book and
# every other entry still in flight.
LIVE_ENTRY_CAPACITY_TAKEN = "LIVE_ENTRY_CAPACITY_TAKEN"
LIVE_ENTRY_EXPOSURE_TAKEN = "LIVE_ENTRY_EXPOSURE_TAKEN"
_CLAIM_FIELDS = ("claimed_at", "door", "client_order_id")
# What each claim adds since PR2c-3: the notional its door judged, beside the claim rather than in
# it (review of #888). A runtime from before reads a claim with a fourth field as a damaged file and
# refuses every entry, so a rollback would stop trading; it ignores a map it does not know, and
# drops it on its next write. A claim this map does not name for its own order counts as the whole
# exposure cap while it holds.
_CLAIM_NOTIONALS = "in_flight_notional"
_NOTIONAL_FIELDS = ("client_order_id", "notional_usdt")

_MARK_MAPS = ("entered", "cooldown")
_CONTEXT_SEP = "__"
# Bars align to the epoch on every timeframe this runtime trades (15m through 1d, UTC).
_EPOCH = "1970-01-01T00:00:00Z"


def entry_context_key(symbol: Any, timeframe: Any) -> str | None:
    """The live context a mark belongs to, or None when either half is missing."""
    symbol, timeframe = str(symbol or "").strip(), str(timeframe or "").strip()
    if not symbol or not timeframe:
        return None
    return f"{symbol}{_CONTEXT_SEP}{timeframe}"


def _is_bar_time(value: Any) -> bool:
    return isinstance(value, str) and bool(timeutil.FIXED_UTC_PATTERN.match(value))


def _empty_entry_marks() -> dict[str, Any]:
    return {"version": ENTRY_MARKS_VERSION, "entered": {}, "cooldown": {}, "in_flight": {},
            _CLAIM_NOTIONALS: {}}


def _positive_amount(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value) and value > 0
    except OverflowError:   # an integer too large for a float is no amount this runtime judges
        return False


def _is_claim_notional(symbol: Any, entry: Any) -> bool:
    return (isinstance(symbol, str) and bool(symbol.strip()) and isinstance(entry, dict)
            and set(entry) == set(_NOTIONAL_FIELDS)
            and isinstance(entry.get("client_order_id"), str) and bool(entry["client_order_id"].strip())
            and _positive_amount(entry.get("notional_usdt")))


def _is_claim(symbol: Any, claim: Any) -> bool:
    if not (isinstance(symbol, str) and bool(symbol.strip()) and isinstance(claim, dict)
            and set(claim) == set(_CLAIM_FIELDS) and _is_bar_time(claim.get("claimed_at"))
            and all(isinstance(claim.get(field), str) and claim[field].strip()
                    for field in ("door", "client_order_id"))):
        return False
    # The form alone admits "2026-99-99T99:99:99Z", which never expires, and a year-9999 stamp,
    # whose expiry cannot be computed. Either is a damaged file, not a claim.
    try:
        timeutil.parse_iso(claim_expires_at(claim))
    except (TypeError, ValueError, OverflowError):
        return False
    return True


def read_live_entry_marks(root: Path | None = None, *, venue: str = VENUE_MAINNET) -> dict[str, Any]:
    """This venue's live entry marks. Ungated read; anything unreadable raises.

    Every value is checked for the fixed UTC form, because the rules compare them as strings and
    a malformed one compares in whatever direction its first character happens to point."""
    path = venue_state_dir(root, venue=venue) / ENTRY_MARKS_FILENAME
    if not path.is_file():
        return _empty_entry_marks()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError(LIVE_ENTRY_MARKS_UNREADABLE, "live entry marks are unreadable") from exc
    if not isinstance(data, dict) or data.get("version") != ENTRY_MARKS_VERSION:
        raise ToolError(LIVE_ENTRY_MARKS_UNREADABLE, "live entry marks are malformed")
    marks = _empty_entry_marks()
    for name in _MARK_MAPS:
        table = data.get(name)
        if not isinstance(table, dict) or not all(
            isinstance(key, str) and _is_bar_time(value) for key, value in table.items()
        ):
            raise ToolError(LIVE_ENTRY_MARKS_UNREADABLE, f"live entry marks hold a malformed {name!r} map")
        marks[name] = dict(table)
    # Absent in a file written before PR2b-2: no entry was in flight under a rule that did not exist.
    in_flight = data.get("in_flight", {})
    if not isinstance(in_flight, dict) or not all(_is_claim(k, v) for k, v in in_flight.items()):
        raise ToolError(LIVE_ENTRY_MARKS_UNREADABLE, "live entry marks hold a malformed 'in_flight' map")
    marks["in_flight"] = {symbol: dict(claim) for symbol, claim in in_flight.items()}
    # Absent in a file written before PR2c-3, or by a runtime from before it.
    notionals = data.get(_CLAIM_NOTIONALS, {})
    if not isinstance(notionals, dict) or not all(_is_claim_notional(k, v) for k, v in notionals.items()):
        raise ToolError(LIVE_ENTRY_MARKS_UNREADABLE, f"live entry marks hold a malformed {_CLAIM_NOTIONALS!r} map")
    marks[_CLAIM_NOTIONALS] = {symbol: dict(entry) for symbol, entry in notionals.items()}
    return marks


def claim_notional(marks: Mapping[str, Any], symbol: Any) -> float | None:
    """The notional the claim on ``symbol`` was taken for, or None when the marks do not name one
    for that claim's own order (PR2c-3). Pure."""
    claim = ((marks.get("in_flight") or {}).get(str(symbol or "")))
    entry = ((marks.get(_CLAIM_NOTIONALS) or {}).get(str(symbol or "")))
    if not (isinstance(claim, Mapping) and isinstance(entry, Mapping)
            and entry.get("client_order_id") == claim.get("client_order_id")
            and _positive_amount(entry.get("notional_usdt"))):
        return None
    return float(entry["notional_usdt"])


def claim_expires_at(claim: Mapping[str, Any]) -> str:
    """When an in-flight claim stops holding its symbol (decision 21)."""
    return timeutil.plus_minutes(str(claim["claimed_at"]), LIVE_ENTRY_CLAIM_TTL_MINUTES)


def symbol_in_flight(marks: Mapping[str, Any] | None, symbol: Any, *, now: str) -> dict[str, Any] | None:
    """The claim that still holds ``symbol`` at ``now``, or None. Pure. A ``now`` that cannot be read
    cannot show that a claim expired, so the claim still holds."""
    claim = ((marks or {}).get("in_flight") or {}).get(str(symbol or ""))
    if not isinstance(claim, Mapping):
        return None
    try:
        expired = timeutil.parse_iso(str(now)) >= timeutil.parse_iso(claim_expires_at(claim))
    except (TypeError, ValueError, OverflowError):
        expired = False
    return None if expired else dict(claim)


def claim_caps_problem(
    marks: Mapping[str, Any], booked: Sequence[Mapping[str, Any]], *, symbol: str, now: str,
    notional_usdt: float, exposure: Mapping[str, Any], max_positions: int,
) -> tuple[str, str] | None:
    """Why one more entry on ``symbol`` does not fit the global caps, or None. Pure (PR2c-3).

    ``booked`` is the venue's book read under the marks lock. The other entries in flight are the
    unexpired claims on other symbols the book does not hold yet. A door gives its symbol back only
    after the book records what the venue holds, so under this lock every entry is in the book, in
    flight, or both.

    - **Positions:** the book plus those entries plus this one must not exceed ``max_positions``.
    - **Exposure:** it starts from what the door judged, ``exposure["open_notional_usdt"]``: the
      venue's open notional, read when the door's book held ``exposure["position_ids"]`` (an entry
      is judged only on a book the venue agrees with). To that it adds each booked position that
      book did not hold (by position, so a position replaced on the same symbol is new), each other
      entry's notional, and this order's ``notional_usdt``. The sum must stay within
      ``exposure["cap_usdt"]``. A claim whose notional is not recorded, a booked record whose
      notional cannot be read, and a door that named no positions all err toward refusing: the
      first two count as the whole cap, the last counts every booked position again."""
    booked_symbols = {str(p.get("symbol") or "") for p in booked}
    others = [
        held for held in (marks.get("in_flight") or {})
        if held != symbol and held not in booked_symbols
        and symbol_in_flight(marks, held, now=now) is not None
    ]
    if len(booked) + len(others) + 1 > max_positions:
        return (LIVE_ENTRY_CAPACITY_TAKEN,
                f"{len(booked)} booked and {len(others)} in flight leave no room for a "
                f"position on {symbol} (at most {max_positions})")
    cap = float(exposure["cap_usdt"])
    seen = exposure.get("position_ids")
    seen_ids = set(seen) if isinstance(seen, list) else set()
    since = sum(float(p["notional_usdt"]) if _positive_amount(p.get("notional_usdt")) else cap
                for p in booked if seen is None or str(p.get("position_id") or "") not in seen_ids)
    flying = 0.0
    for held in others:
        recorded = claim_notional(marks, held)
        flying += cap if recorded is None else recorded
    total = float(exposure["open_notional_usdt"]) + since + flying + float(notional_usdt)
    if total > cap:
        return (LIVE_ENTRY_EXPOSURE_TAKEN,
                f"open exposure {exposure['open_notional_usdt']} + {since} booked since + {flying} "
                f"in flight + {notional_usdt} exceeds the cap {cap}")
    return None


def live_entry_holds(
    marks: Mapping[str, Any] | None, *, symbol: Any, timeframe: Any, bar_time: Any,
    now: str | None = None,
) -> list[str]:
    """Why this context may not send an entry on this bar — empty when it may. Pure.

    A bar at or before the last one this context sent on is refused, not only the same one: out
    of order data must not reopen a spent bar (the `routing_marks.is_fresh` rule). The cooldown
    holds every bar that opens before its bound. Given ``now``, an entry still in flight on the
    symbol holds it too (PR2b-2); the claim itself re-checks that under the lock."""
    if not isinstance(marks, Mapping):
        return [LIVE_ENTRY_MARKS_UNKNOWN]
    key = entry_context_key(symbol, timeframe)
    if key is None or not _is_bar_time(bar_time):
        return [LIVE_ENTRY_BAR_UNKNOWN]
    holds: list[str] = []
    last = (marks.get("entered") or {}).get(key)
    if last is not None and bar_time <= last:
        holds.append(LIVE_ENTRY_BAR_ALREADY_ENTERED)
    until = (marks.get("cooldown") or {}).get(key)
    if until is not None and bar_time < until:
        holds.append(LIVE_ENTRY_STOP_LOSS_COOLDOWN)
    if now is not None and symbol_in_flight(marks, symbol, now=now) is not None:
        holds.append(LIVE_ENTRY_SYMBOL_IN_FLIGHT)
    return holds


def stop_cooldown_until(closed_at: str, *, timeframe_minutes: int, bars: int) -> str:
    """The first bar (by open time) a context may enter again after a stop-out. Pure.

    The anchor is the bar containing ``closed_at``; the bound is ``bars`` bars after it. With
    ``closed_at`` at the fill this is paper's window on the open-time basis: paper refuses candles
    closing before ``stop bar close + bars`` — the stop bar and the ``bars - 1`` after it. The
    caller passes an instant no earlier than the fill (see ``live_route._record_stop_cooldown``),
    so the window is never shorter than paper's, and one bar longer when that instant falls in a
    later bar than the fill did."""
    if isinstance(timeframe_minutes, bool) or not isinstance(timeframe_minutes, int) \
            or timeframe_minutes <= 0 or isinstance(bars, bool) or not isinstance(bars, int) or bars < 0:
        raise ToolError(LIVE_ENTRY_COOLDOWN_UNCOMPUTABLE, "a cooldown needs a positive bar length")
    if not _is_bar_time(closed_at):
        raise ToolError(LIVE_ENTRY_COOLDOWN_UNCOMPUTABLE, f"cannot anchor a cooldown on {closed_at!r}")
    minute = int(timeutil.parse_iso(closed_at).timestamp()) // 60
    bar_open = minute - minute % timeframe_minutes
    return timeutil.plus_minutes(_EPOCH, bar_open + bars * timeframe_minutes)


class LiveEntryMarks:
    """Durable per-venue entry marks, behind the live-trading switch like the counter and the
    breaker — and for their reason: an inert mark store beside a durable adapter is a leg that
    can enter the same bar twice."""

    provider_id = LIVE_TRADING_PROVIDER_ID
    filesystem_write = True

    def __init__(self, *, root: Path | None = None, authorization: Authorization | None = None,
                 venue: str = VENUE_MAINNET):
        self._root = root
        self._authorization = authorization
        self._venue = venue

    def _assert(self) -> None:
        safety_gate.assert_authorization(
            self._authorization,
            required_flags=LIVE_TRADING_FLAGS,
            provider_id=self.provider_id,
            now=timeutil.utc_now_iso(),
        )

    def _update(self, mutate: Any) -> dict[str, Any]:
        self._assert()
        target = venue_state_dir(self._root, venue=self._venue)
        target.mkdir(parents=True, exist_ok=True)
        path = target / ENTRY_MARKS_FILENAME
        with locked(path.with_suffix(".lock"), code="LIVE_ENTRY_MARKS_LOCKED", label="live entry marks"):
            marks = read_live_entry_marks(self._root, venue=self._venue)
            if mutate(marks) is False:
                return marks
            tmp = path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(marks, ensure_ascii=False, indent=1))
                handle.flush()
                # A claim that reached the page cache but not the disk is a bar this runtime
                # forgets across a crash — the live position store's reason for its fsync. The
                # file is synced; the directory entry is not, as for the book.
                os.fsync(handle.fileno())
            tmp.replace(path)
            return marks

    def claim_bar(self, *, symbol: Any, timeframe: Any, bar_time: Any) -> dict[str, Any]:
        """Spend this bar for this context before its order is sent, or refuse.

        Both rules are re-checked under the lock, so of two claims on one bar exactly one wins."""
        def mutate(marks: dict[str, Any]) -> None:
            holds = live_entry_holds(marks, symbol=symbol, timeframe=timeframe, bar_time=bar_time)
            if holds:
                raise ToolError(holds[0], f"bar {bar_time!r} cannot be claimed for {symbol} {timeframe}")
            marks["entered"][entry_context_key(symbol, timeframe)] = bar_time

        return self._update(mutate)

    def claim_symbol(
        self, *, symbol: Any, door: str, client_order_id: Any, now: str,
        notional_usdt: Any, exposure: Any,
    ) -> dict[str, Any]:
        """Take ``symbol`` for one entry before it is sent, or refuse (PR2b-2).

        Under the lock, the symbol must have no other entry in flight and no position in this
        venue's book, and one more position must fit the global caps (:func:`claim_caps_problem`,
        PR2c-3). ``notional_usdt`` is the notional the door's guard judged; ``exposure`` is what that
        guard judged it against: ``open_notional_usdt``, the ``position_ids`` of the book that figure
        was read beside (None when unknown), and ``cap_usdt``. The claim is stamped with the later
        of ``now`` and the wall clock: the cycle's ``now`` can be minutes old by the time its leg
        runs, and an early stamp would expire early."""
        exposure = exposure if isinstance(exposure, Mapping) else {}
        seen = exposure.get("position_ids")
        open_notional = exposure.get("open_notional_usdt")
        if not (isinstance(symbol, str) and symbol.strip() and isinstance(door, str) and door.strip()
                and isinstance(client_order_id, str) and client_order_id.strip() and _is_bar_time(now)
                and _positive_amount(notional_usdt) and _positive_amount(exposure.get("cap_usdt"))
                and (_positive_amount(open_notional)
                     or (open_notional == 0 and not isinstance(open_notional, bool)))
                and (seen is None or (isinstance(seen, list) and all(isinstance(s, str) for s in seen)))):
            raise ToolError(LIVE_ENTRY_CLAIM_MALFORMED,
                            "a symbol claim needs a symbol, a door, an order id, a time, the order's "
                            "notional and the exposure it was judged against")
        # local: the book imports this module's neighbours
        from .live_position import MAX_LIVE_CONCURRENT_POSITIONS, list_open_live_positions

        def mutate(marks: dict[str, Any]) -> None:
            at = max(now, timeutil.utc_now_iso())
            held = symbol_in_flight(marks, symbol, now=at)
            if held is not None:
                raise ToolError(LIVE_ENTRY_SYMBOL_IN_FLIGHT,
                                f"{symbol} has an entry in flight ({held['door']} since {held['claimed_at']})")
            # Re-read under the lock: a door that booked its position and gave the symbol back after
            # this one read the book is seen here, not missed.
            booked = list_open_live_positions(self._root, venue=self._venue)
            if any(str(p.get("symbol") or "") == symbol for p in booked):
                raise ToolError(LIVE_ENTRY_SYMBOL_OCCUPIED, f"{symbol} already holds a live position")
            problem = claim_caps_problem(marks, booked, symbol=symbol, now=at, notional_usdt=notional_usdt,
                                         exposure=exposure, max_positions=MAX_LIVE_CONCURRENT_POSITIONS)
            if problem is not None and problem[0] == LIVE_ENTRY_CAPACITY_TAKEN:
                raise ToolError(LIVE_ENTRY_CAPACITY_TAKEN, problem[1])
            if problem is not None:
                raise ToolError(LIVE_ENTRY_EXPOSURE_TAKEN, problem[1])
            marks["in_flight"][symbol] = {"claimed_at": at, "door": door, "client_order_id": client_order_id}
            marks[_CLAIM_NOTIONALS][symbol] = {"client_order_id": client_order_id,
                                               "notional_usdt": float(notional_usdt)}

        return self._update(mutate)

    def release_symbol(self, *, symbol: Any, client_order_id: Any) -> dict[str, Any]:
        """Give ``symbol`` back once the book says what the venue holds. Only the claim this order
        took is removed. One that is gone, or that expired and was taken by another order, is left
        alone and the call raises ``LIVE_ENTRY_CLAIM_LOST``."""
        def mutate(marks: dict[str, Any]) -> bool:
            claim = marks["in_flight"].get(symbol)
            if not (isinstance(claim, Mapping) and claim.get("client_order_id") == client_order_id):
                raise ToolError(LIVE_ENTRY_CLAIM_LOST,
                                f"{symbol} is not claimed by {client_order_id} any more")
            del marks["in_flight"][symbol]
            marks[_CLAIM_NOTIONALS].pop(symbol, None)
            return True

        return self._update(mutate)

    def record_stop_cooldown(self, *, symbol: Any, timeframe: Any, until: str) -> dict[str, Any]:
        """Hold this context until the bar ``until`` opens. Never shortens a longer hold."""
        key = entry_context_key(symbol, timeframe)
        if key is None or not _is_bar_time(until):
            raise ToolError(LIVE_ENTRY_BAR_UNKNOWN, "a cooldown needs a context and a bar time")

        def mutate(marks: dict[str, Any]) -> bool:
            current = marks["cooldown"].get(key)
            if current is not None and until <= current:
                return False
            marks["cooldown"][key] = until
            return True

        return self._update(mutate)


class DryRunLiveEntryMarks:
    """Inert marks: with the switch off nothing is sent, so there is no bar to spend."""

    filesystem_write = False

    def claim_bar(self, **_kwargs: Any) -> dict[str, Any]:
        return _empty_entry_marks()

    def claim_symbol(self, **_kwargs: Any) -> dict[str, Any]:
        return _empty_entry_marks()

    def release_symbol(self, **_kwargs: Any) -> dict[str, Any]:
        return _empty_entry_marks()

    def record_stop_cooldown(self, **_kwargs: Any) -> dict[str, Any]:
        return _empty_entry_marks()


def select_live_entry_marks(*, now: str | None = None, root: Path | None = None) -> Any:
    """Return the durable marks if live trading is opted in, else the inert ones."""
    return safety_gate.select_env_gated(
        env_var=LIVE_TRADING_ENV,
        opt_in_value=REAL_LIVE_TRADING,
        flags=LIVE_TRADING_FLAGS,
        provider_id=LIVE_TRADING_PROVIDER_ID,
        default_factory=DryRunLiveEntryMarks,
        gated_factory=lambda authorization: LiveEntryMarks(root=root, authorization=authorization),
    )


def render_guard_text(verdict: Mapping[str, Any]) -> str:
    """ASCII-only guard report for the console."""
    lines = [f"live order guard: {verdict['status']} (approved={verdict['approved']})"]
    for block in verdict.get("blocks") or []:
        lines.append(f"  BLOCK  : {block}")
    for repair in verdict.get("repairs") or []:
        lines.append(f"  REPAIR : {repair}")
    return "\n".join(lines)
