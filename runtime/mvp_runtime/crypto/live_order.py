"""LP3 live order intent and the final guard (source L2); the intent's identity is ``order_identity``'s.

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

The four stores the entry door keeps on disk (the daily counter, the two breakers, the entry
marks) live in ``live_order_stores`` and are re-exported here. The guard does not read them: it is
handed what they say. Their four selectors stay in this module, because they are the
``select_env_gated`` call sites.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .. import safety_gate, timeutil
from ..errors import ToolError
from ..paths import repo_root as _repo_root
from .execution_stage import (
    PURPOSE_AUTONOMOUS,
    PURPOSE_PROBE,
    StageStatus,
    required_stage,
)
from . import live_budget
# The order intent's identity lives in `order_identity` (foundation) and the account-age bound in
# `pre_order_gate`, which enforces it, since crypto PR7d-1; both re-exported here as the same objects.
# A test that means to change how an id is derived patches `order_identity`: `enrich_order_identity`
# reads its helpers there, so a patch on this module's copies of them reaches nothing.
from .order_identity import enrich_order_identity  # noqa: F401
from .pre_order_gate import MAX_ACCOUNT_AGE_SECONDS
from .vocabulary import (
    LIVE_TRADING_ENV,
    LIVE_TRADING_FLAGS,
    LIVE_TRADING_PROVIDER_ID,
    REAL_LIVE_TRADING,
)
# The four stores live in `live_order_stores.py` (moved whole, refactor plan PR-12): the daily
# counter, the two breakers and the entry marks, each with its reader. Re-exported here as the same
# objects. The selectors below stay in this file: they are the `select_env_gated` call sites.
from .live_order_stores import (  # noqa: F401
    DryRunLiveApiErrorBreaker, DryRunLiveBracketFailureBreaker, DryRunLiveEntryMarks,
    DryRunLiveOrderCounter, LiveApiErrorBreaker, LiveBracketFailureBreaker, LiveEntryMarks,
    LiveOrderCounter,
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


def limits_from_budget(record: Mapping[str, Any]) -> LiveOrderLimits:
    """A ``LiveOrderLimits`` carrying the registered caps (for a later guard-rewiring increment).

    Beside the class it builds since crypto PR7b-2: in ``live_budget`` it was that module's only
    import of this one, and the import that closed the lane's one cycle.

    Maps the five registered caps (a legacy record's ``min_clean_canary_orders`` is not one of
    them and is never indexed); ``confirmation`` and ``manual_kill_switch`` are deliberately
    left at their defaults — they are operator env state (a phrase and a halt), not
    budget-registered caps, so a budget can never carry the confirmation that proves intent."""
    caps = record["caps"]
    return LiveOrderLimits(
        max_order_notional_usdt=float(caps["max_order_notional_usdt"]),
        absolute_max_notional_usdt=float(caps["absolute_max_notional_usdt"]),
        max_daily_order_count=int(caps["max_daily_order_count"]),
        max_open_notional_usdt=float(caps["max_open_notional_usdt"]),
        daily_loss_limit_usdt=float(caps["daily_loss_limit_usdt"]),
    )


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
        # The artifact the plan's pool entry was installed as (PR3a-2). Bound by the pre-order
        # snapshot with the rest of the lineage, so the order names the strategy it was approved
        # as, not only its rule; the leg carries it to the position and the outcome.
        "strategy_artifact_sha256": plan.get("strategy_artifact_sha256"),
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


# --- the four stores' selectors --------------------------------------------------------
#
# The stores themselves are in `live_order_stores`. These stay here because they are where live
# trading's environment opt-in chooses the durable store over the inert one.

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


def select_live_api_breaker(*, now: str | None = None, root: Path | None = None) -> Any:
    """Return the durable API breaker if live trading is opted in, else the inert one."""
    return safety_gate.select_env_gated(
        env_var=LIVE_TRADING_ENV,
        opt_in_value=REAL_LIVE_TRADING,
        flags=LIVE_TRADING_FLAGS,
        provider_id=LIVE_TRADING_PROVIDER_ID,
        default_factory=DryRunLiveApiErrorBreaker,
        gated_factory=lambda authorization: LiveApiErrorBreaker(root=root, authorization=authorization),
    )


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
