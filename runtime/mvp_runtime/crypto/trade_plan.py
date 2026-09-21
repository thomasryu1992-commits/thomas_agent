"""The trade-plan maths the paper kernel, the factory backtest and the forward book share (crypto PR7c).

How an entry is planned (size, stop, target, liquidation), how a bar advances a position (the managed
stop, the intrabar exit), how a plan settles, and the outcome record it leaves. One set of rules for
backtest and paper is deliberate: the factory scores a strategy with the maths the paper book then trades
it with. It lived in `paper`, beside the stateful position kernel (`run_paper_update`, `RealPaperStore`,
`route_entries`), which put it above the factory and the forward book that read it. By what it does it is
strategy-layer work, pure computation over a strategy's spec and the cost model, and it imports nothing
from `paper`. `paper` re-exports every name as the same object, so its importers keep their lines.

The labels these functions stamp into records, and that the kernel reads back (`PAPER_PROVENANCE`,
`PAPER_KERNEL_VERSION`, `DEFAULT_VENUE`, the router statuses), live in `vocabulary`.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from runtime.read_only_kernel import integrity

from . import cost as costs
from .cost import CostModel, apply_cost_model
from .strategy import StrategySpec
from .strategy_artifact import ARTIFACT_SHA256_FIELD
from .vocabulary import (
    DEFAULT_VENUE,
    PAPER_KERNEL_VERSION,
    PAPER_PROVENANCE,
    R_BASIS_INTENT_NET,
    STATUS_ENTRY_CANDIDATE,
)


# The planned stop sits beyond the isolated-margin liquidation price, so the position
# would be liquidated before the stop triggers. Applied identically in `_replay`
# (backtest) and `run_paper_update` (live paper) and `plan_live_entry` (live).
STOP_BEYOND_LIQUIDATION = "STOP_BEYOND_LIQUIDATION"


# After a stop-loss, block re-entry on the same (venue, symbol, timeframe) for this
# many bars.  Applied identically in `_replay` (backtest) and `run_paper_update`
# (live paper) so the evidence the lifecycle reads reflects the actual trading rule.
# 2 bars is conservative: long enough to skip the immediate reversion bar and the
# one after it, short enough to re-enter on a genuine trend resumption.
COOLDOWN_BARS_AFTER_STOPLOSS = 2


# The leverage the liquidation guard assumes where there is no account to ask: the backtest
# and the paper book. A higher assumption makes the guard MORE restrictive (liquidation price
# closer to entry); a lower one more permissive.
#
# **5 because that is what the account is actually set to, verified 2026-09-02** over
# `/fapi/v2/account`: every symbol in the traded universe (BTC, ETH, SOL, BNB, DOGE, XRP)
# reports 5x, as do 880 of the 884 symbols the venue lists for it.
#
# It was 20 — the venue's DEFAULT for most perpetuals — and that was never a posture anyone
# chose. What the default cost, measured across the candidate store on rows where the guard
# ran: it refused 98.4% of the 1d tier's entries (112,358 against 1,837 closed), 56.4% at 4h
# and 4.4% at 1h. A controlled re-backtest of ten real 1d specs over the same 2,000 bars x 5
# symbols, changing nothing but this number: **median closed trades per spec 1 at 20x, 145 at
# 5x** — 37 total closes against 1,623. At 20x the median 1d spec cannot fill a single
# lifecycle window, so the tier was unjudgeable by arithmetic rather than by signal.
#
# The starved sample was also BIASED, which is why the old evidence looked fine: the guard
# refuses where ATR is large against price, so the survivors are drawn from calm regimes.
# Median 1d expectancy read +0.3172 on guard-era rows against +0.0504 before it (#742,
# 2026-08-21, which introduced the guard and dropped median 1d closes 80.5 -> 10.5 in the
# forty generations either side of it). The number improved as the sample vanished.
#
# The assumption is now a fact rather than a default, and what would reopen it is the account
# changing — five symbols outside the traded universe still read above 5x (SUIUSDT 6,
# KAITOUSDT 10, ZORAUSDT 15, UNIUSDT 20, BTCUSDC 35), so widening the universe to any of them
# needs this re-verified, not assumed. The LIVE path does not depend on this being current:
# it reads the venue per symbol (`live_entry.configured_leverage_for`) and falls back to
# `live_entry.UNREADABLE_ACCOUNT_LEVERAGE`, which is deliberately NOT this constant.
ASSUMED_LEVERAGE = 5


# Tier-1 maintenance margin rate on Binance USDM (0.4%). Using the lowest tier is
# permissive — larger positions have higher MMR and get liquidated sooner, so a
# stop that clears this check can still be beyond liquidation at a higher tier.
MAINTENANCE_MARGIN_RATE = 0.004


# The friction this plan would pay is too large a share of the one R it is risking, so the
# trade cannot be profitable at any win rate this system has ever produced. The limit itself
# is `cost.MAX_ENTRY_COST_R` — the live leg refuses on the same number, and two spellings of
# one threshold is how the paper book and the money path come to disagree about what is
# economic.
ENTRY_COST_UNECONOMIC = "ENTRY_COST_UNECONOMIC"


# Kernel settlement limits (source paper_position_kernel; timeframes outside the
# table — e.g. 1d — use the default, the source runtime's own behavior). Since the
# exit-parity fix these are the LEGACY fallback only: a position normally carries its
# spec's own max_holding_bars (the value its backtest evidence was built on).
MAX_HOLD_BARS = {"15m": 96, "1h": 48, "4h": 30}


DEFAULT_MAX_HOLD_BARS = 48


# How many closed trades a regime slice needs before its sign may exclude a strategy from
# trading there. Ten because that is this repo's own stated line for "enough observations to
# mean anything" (`robustness.HEALTHY_TRADES_PER_PARAMETER`), reused rather than re-invented.
#
# The direction of the two errors is not symmetric, which is what sets the number. Excluding on
# too little evidence stops a strategy trading where it actually works, and that cost is
# **silent** — no record shows the trades that did not happen. Requiring too much evidence just
# leaves today's behaviour in place. Slicing an outcome history by regime also multiplies the
# comparisons being made on it, which is the overfitting hazard `docs/TRADING_STRATEGY_REVIEW_
# RECORD.md` raises about this pipeline generally; a per-regime sign read off three trades is
# exactly that hazard wired into live routing. So the bar is evidence, not suspicion.
MIN_REGIME_TRADES_TO_EXCLUDE = 10


# Why a regime was declined, on the route record, so an operator sees a filter rather than a
# strategy that mysteriously stopped matching.
REGIME_EXCLUDED = "regime_demonstrated_unprofitable"


def regime_admits(entry: Mapping[str, Any], regime: Any) -> tuple[bool, str | None]:
    """May this pool entry trade in ``regime``? Returns ``(admitted, reason)``.

    **Derived from the stored numbers, never from a stored verdict**, and that is the whole
    reason this is a function rather than a field written at promotion time. `pool.
    candidate_quality` already carries the scar: robustness verdicts were written once at mint
    time, the rule that produced them changed, and twelve candidates kept a label the rule could
    no longer produce — inverting the shortlist on exactly the property the new rule existed to
    enforce. A baked `excluded: [...]` list would repeat that the first time
    :data:`MIN_REGIME_TRADES_TO_EXCLUDE` moved.

    The rule, and the direction it fails in:

    - No ``regime_evidence`` on the entry (every strategy promoted before this existed) →
      **admitted**. Absent evidence is not evidence of failure, and a filter that silenced the
      whole installed pool on the day it shipped would be a worse error than the one it fixes.
    - The regime is unknown, or was never traded in the replay → **admitted**. Same reason: the
      backtest says nothing about it, and this gate only ever *declines* on a demonstration.
    - Traded, but on fewer than :data:`MIN_REGIME_TRADES_TO_EXCLUDE` closed trades →
      **admitted**, however bad the sign. This is the clause that keeps the gate from being the
      overfitting it is meant to guard against.
    - Traded enough, and the regime's total R is at or below zero → **declined**. The strategy
      was promoted on an aggregate that blended this regime with the ones that paid for it.

    Deliberately one-directional: this can only ever make the pool trade *less*. It never admits
    an entry the existing checks would have refused, which is why it needs no gate of its own.
    """
    evidence = entry.get("regime_evidence")
    if not isinstance(evidence, Mapping) or not evidence:
        return True, None
    if not isinstance(regime, str) or not regime:
        return True, None
    cell = evidence.get(regime)
    if not isinstance(cell, Mapping):
        return True, None
    trades = cell.get("trades")
    total_r = cell.get("total_r")
    if not isinstance(trades, int) or isinstance(trades, bool) or trades < MIN_REGIME_TRADES_TO_EXCLUDE:
        return True, None
    if isinstance(total_r, bool) or not isinstance(total_r, (int, float)):
        return True, None
    if float(total_r) > 0:
        return True, None
    return False, REGIME_EXCLUDED


# How far the live size may be scaled DOWN when volatility is above its own normal. A floor
# rather than an open-ended divisor: below it the venue's minimum quantity and minimum notional
# refuse the entry anyway, and a refusal on a chaotic tape is a defensible outcome — but it
# should come from the venue's own limits rather than from an arithmetic slide to zero.
MIN_VOL_SIZE_MULTIPLIER = 0.25


def volatility_size_multiplier(feature_row: Mapping[str, Any]) -> float:
    """How much of the otherwise-permitted live size this bar's volatility allows, in (0, 1].

    **Why this is needed, measured rather than assumed.** ``live_sizing.size_live_order`` takes
    ``min(risk-based, budget cap)``, and on any realistic equity the *budget cap* binds: at a 60
    USDT per-order cap, the risk leg only wins below roughly 30 USDT of equity. When the cap
    binds the notional is fixed, so the risk actually taken is ``cap × (stop distance / price)``
    — which **rises with volatility**. Measured across plausible stop distances at a fixed cap,
    the risk taken swings 0.18 → 1.20 USDT, a factor of 6.7, in the direction nobody wants:
    biggest bets on the most chaotic tape. That is the opposite of volatility targeting, and it
    is why the multiplier is applied to the FINAL quantity rather than to the risk fraction —
    scaling the risk fraction changes nothing at all while the cap is what binds.

    ``atr_pct_reference / atr_pct_of_price``, clamped. Both legs are measured from the same
    series (see ``features.atr_pct_reference``), so there is no target-volatility constant for
    anybody to have failed to authorize and no number that means one thing on BTC and another on
    DOGE.

    **One-directional, and the name is chosen to say so.** The ratio is capped at 1.0, so this
    can only ever make an order smaller. Letting it exceed 1.0 would size *above* the operator's
    registered per-order cap in quiet markets — widening an authorization from inside the
    runtime, which is a Thomas decision and not a multiplier's business. So this is a
    volatility-scaled ceiling, not a two-sided target: in unusually quiet conditions it leaves
    size where the cap already put it rather than reaching for a risk target.

    Returns ``1.0`` — unscaled — whenever the inputs cannot support a ratio: a missing or
    non-positive current ATR%, or a reference that has not warmed up
    (``features.ATR_REFERENCE_MIN_PERIODS``). Unscaled rather than refused, because the entry
    is already authorized by every other door and this function's only job is to shrink it; a
    missing reference is not grounds to invent one, and it is not grounds to block either.
    """
    current = feature_row.get("atr_pct_of_price")
    reference = feature_row.get("atr_pct_reference")
    for value in (current, reference):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            return 1.0
    ratio = float(reference) / float(current)
    return max(MIN_VOL_SIZE_MULTIPLIER, min(1.0, ratio))


def build_entry_plan(route: Mapping[str, Any], feature_row: Mapping[str, Any], *, now: str) -> dict[str, Any] | None:
    """Turn an ENTRY_CANDIDATE route into a concrete trade plan — pure, no I/O.

    Entry at the row's close; ATR stop/target from the winning spec's exit rules;
    risk = |entry - stop| = stop_atr * ATR. Returns None (no plan, honestly) when the
    route is not a candidate or the row's close/ATR is indeterminate — a plan is
    never built on data the features could not price."""
    if route.get("status") != STATUS_ENTRY_CANDIDATE:
        return None
    spec: StrategySpec = route["primary_spec"]
    entry = feature_row.get("close")
    atr = feature_row.get("atr")
    if not isinstance(entry, (int, float)) or not isinstance(atr, (int, float)) or entry <= 0 or atr <= 0:
        return None
    direction = route["direction"]
    stop_distance = spec.exit_rules.stop_atr * atr
    target_distance = spec.exit_rules.target_atr * atr
    if direction == "LONG":
        stop, target = entry - stop_distance, entry + target_distance
    else:
        stop, target = entry + stop_distance, entry - target_distance
    # The traded context comes from the route (this cycle's symbol/timeframe), with a
    # fall back to the spec's primary symbol for a route built before it carried one.
    plan_symbol = str(route.get("symbol") or (spec.symbol_scope[0] if spec.symbol_scope else ""))
    return {
        "symbol": plan_symbol,
        "timeframe": str(route.get("timeframe") or spec.timeframe),
        "direction": direction,
        "entry_price": float(entry),
        "stop_loss": float(stop),
        "take_profit": float(target),
        "risk": abs(float(entry) - float(stop)),
        # Volatility scaling for the LIVE size, computed here for the reason `max_holding_bars`
        # is: the plan is what the execution step reads, and a fact it could re-derive from a
        # feature row it fetched itself is a fact the two can disagree about. Paper is
        # unaffected by construction — its accounting is R-based, and R is already normalized
        # by risk, so no quantity exists there for a multiplier to scale.
        "vol_size_multiplier": volatility_size_multiplier(feature_row),
        # Backtest/runtime exit parity: the spec's own time-exit limit rides into the
        # plan so the live settlement uses the SAME rule the backtest evidence was
        # built on — a strategy promoted on max_holding_bars=12 must not hold 48.
        "max_holding_bars": int(spec.exit_rules.max_holding_bars),
        # The same parity, for the two rules that move the stop after entry. `trail_atr` is
        # multiplied out to a PRICE here, where the ATR is in hand — `advance_managed_stop` is
        # a pure function of the position and the bar, and is never handed a feature row.
        "breakeven_at_r": spec.exit_rules.breakeven_at_r,
        "trail_distance": (
            spec.exit_rules.trail_atr * float(atr) if spec.exit_rules.trail_atr is not None else None
        ),
        "strategy_id": route.get("primary_strategy_id"),
        # The lineage the promotion evidence was built on rides all the way to the
        # outcome, so a later generation reusing this display name cannot inherit
        # this trade's performance (or be judged by it).
        "candidate_id": route.get("primary_candidate_id"),
        "strategy_rule_hash": route.get("primary_strategy_rule_hash"),
        "strategy_generation_id": route.get("primary_strategy_generation_id"),
        ARTIFACT_SHA256_FIELD: route.get("primary_strategy_artifact_sha256"),
        "supporting_strategy_ids": list(route.get("supporting_strategy_ids") or []),
        "created_at_utc": now,
    }


def entry_cost_refusal(
    plan: Mapping[str, Any], *, max_cost_r: float = costs.MAX_ENTRY_COST_R
) -> dict[str, Any] | None:
    """Refuse a plan whose round-trip friction exceeds ``cost.MAX_ENTRY_COST_R``. Pure.

    The one pre-trade check that is arithmetic rather than statistics. Everything else about a
    strategy's edge is estimated from a sample and can be wrong; this is ``|entry − stop|``
    against a published fee schedule, and it says whether the trade has room to be profitable
    before asking whether it will be.

    Returns the refusal dict (``reason_code`` plus the numbers that produced it, so an operator
    reads *why* and not just *that*) or ``None`` to admit. Deliberately not folded into
    :func:`build_entry_plan`: that function returns ``None`` for unpriceable data, and a refusal
    that arrives as an absent plan is a refusal nothing can attribute, count, or shadow.
    """
    direction = str(plan.get("direction") or "")
    entry_price = float(plan.get("entry_price") or 0.0)
    risk = float(plan.get("risk") or 0.0)
    cost_r = costs.round_trip_cost_r(direction, entry_price, risk)
    if cost_r <= max_cost_r:
        return None
    return {
        "reason_code": ENTRY_COST_UNECONOMIC,
        # Recorded beside the figure that refused, never added to it — see
        # `cost.worst_case_carry_r`. The cap bounds the round trip, which every trade pays in
        # full; this is what the trade would pay if it held to its own limit, which almost none
        # of them do. On the record so a refusal can be read against the whole cost of the
        # trade rather than against the half this door prices.
        "worst_case_carry_r": costs.worst_case_carry_r(
            direction, entry_price, risk,
            timeframe=plan.get("timeframe"), max_holding_bars=plan.get("max_holding_bars"),
        ),
        # `inf` is what an unpriceable plan costs (see round_trip_cost_r) and is not JSON, so it
        # rides as a string. A refusal record that cannot be serialised is a crash on the refusal
        # path, which is the one path that must not crash.
        "round_trip_cost_r": round(cost_r, 6) if math.isfinite(cost_r) else "inf",
        "limit_r": max_cost_r,
        "symbol": plan.get("symbol"),
        "timeframe": plan.get("timeframe"),
        "strategy_id": plan.get("strategy_id"),
    }


def liquidation_price(
    entry_price: float, direction: str, *,
    leverage: float = ASSUMED_LEVERAGE, mmr: float = MAINTENANCE_MARGIN_RATE,
) -> float:
    """Approximate isolated-margin liquidation price for Binance USDM futures.

    Isolated margin is conservative relative to cross margin: the liquidation price is
    closer to entry (less margin backing), so a stop that clears this check is safe under
    both modes."""
    if direction == "LONG":
        return entry_price * (1.0 - 1.0 / leverage + mmr)
    return entry_price * (1.0 + 1.0 / leverage - mmr)


def stop_is_beyond_liquidation(
    entry_price: float, stop_price: float, is_long: bool, *,
    leverage: float = ASSUMED_LEVERAGE, mmr: float = MAINTENANCE_MARGIN_RATE,
) -> bool:
    """True when the stop sits on the wrong side of the liquidation price."""
    if entry_price <= 0 or leverage <= 0:
        return False
    liq = liquidation_price(entry_price, "LONG" if is_long else "SHORT", leverage=leverage, mmr=mmr)
    if is_long:
        return stop_price <= liq
    return stop_price >= liq


def stop_beyond_liquidation_refusal(
    plan: Mapping[str, Any], *,
    leverage: float = ASSUMED_LEVERAGE, mmr: float = MAINTENANCE_MARGIN_RATE,
) -> dict[str, Any] | None:
    """Refuse a plan whose stop sits beyond the isolated-margin liquidation price. Pure.

    At the refused leverage the position would be liquidated before the stop-loss order
    triggers, making the protective stop ineffective."""
    direction = str(plan.get("direction") or "")
    entry = float(plan.get("entry_price") or 0.0)
    stop = float(plan.get("stop_loss") or 0.0)
    if entry <= 0 or leverage <= 0:
        return None
    is_long = direction == "LONG"
    if not stop_is_beyond_liquidation(entry, stop, is_long, leverage=leverage, mmr=mmr):
        return None
    liq = liquidation_price(entry, direction, leverage=leverage, mmr=mmr)
    return {
        "reason_code": STOP_BEYOND_LIQUIDATION,
        "entry_price": entry,
        "stop_loss": stop,
        "liquidation_price": round(liq, 8),
        "assumed_leverage": leverage,
        "maintenance_margin_rate": mmr,
        "symbol": plan.get("symbol"),
        "timeframe": plan.get("timeframe"),
        "strategy_id": plan.get("strategy_id"),
    }


def open_position(plan: Mapping[str, Any], *, now: str) -> dict[str, Any]:
    """The position dict an entry plan opens — pure (source ``build_position`` shape)."""
    position = {
        "position_kernel_version": PAPER_KERNEL_VERSION,
        "status": "OPEN",
        # The book this position belongs to: its context IS its storage slot.
        "venue": str(plan.get("venue") or DEFAULT_VENUE),
        "symbol": plan["symbol"],
        "timeframe": plan["timeframe"],
        "direction": plan["direction"],
        "entry_price": plan["entry_price"],
        "stop_loss": plan["stop_loss"],
        "take_profit": plan["take_profit"],
        "risk": plan["risk"],
        "max_holding_bars": plan.get("max_holding_bars"),
        # Carried for the same reason `max_holding_bars` is: the plan is what the settlement
        # step reads, and a rule it could re-derive from the spec is a rule the two can
        # disagree about. A strategy whose evidence was built with a trailing stop must not
        # settle without one.
        "breakeven_at_r": plan.get("breakeven_at_r"),
        "trail_distance": plan.get("trail_distance"),
        "holding_candles": 0,
        "intrabar_policy": "pessimistic_sl_first",
        "opened_at_utc": now,
        "strategy_id": plan.get("strategy_id"),
        "candidate_id": plan.get("candidate_id"),
        "strategy_rule_hash": plan.get("strategy_rule_hash"),
        "strategy_generation_id": plan.get("strategy_generation_id"),
        ARTIFACT_SHA256_FIELD: plan.get(ARTIFACT_SHA256_FIELD),
        "supporting_strategy_ids": list(plan.get("supporting_strategy_ids") or []),
    }
    # short_id seeds forbid floats (fingerprint rule) — the price rides as a string.
    position["position_id"] = integrity.short_id(
        "paper_position",
        {"strategy_id": position["strategy_id"], "entry": str(position["entry_price"]), "opened_at": now},
    )
    return position


def position_max_hold(position: Mapping[str, Any], timeframe: str) -> tuple[int, bool]:
    """The time-exit limit for THIS position, and whether it is a legacy fallback.

    Parity rule: a position carries the ``max_holding_bars`` its spec was backtested
    with, and settlement must judge it by that same number — a strategy promoted on
    12-bar evidence must not silently hold 48. Only a legacy position (opened before
    the plan carried the value) falls back to the timeframe table, and the caller
    records that fallback so a backtest/paper gap is attributable, never invisible."""
    stored = position.get("max_holding_bars")
    if isinstance(stored, int) and not isinstance(stored, bool) and stored > 0:
        return stored, False
    return MAX_HOLD_BARS.get(timeframe, DEFAULT_MAX_HOLD_BARS), True


def _result_r(direction: str, entry: float, exit_price: float, risk: float) -> float:
    if risk <= 0:
        return 0.0
    signed = (exit_price - entry) if direction == "LONG" else (entry - exit_price)
    return signed / risk


def advance_holding(position: dict[str, Any], candle_ts: Any) -> None:
    """Advance holding_candles once per DISTINCT candle, so a re-run within one interval
    cannot accelerate time_exit (the source's fix).

    Takes the **timestamp**, not the candle, because the live leg counts the same bars from a
    different shape: paper dedups on a candle's ``close_time``, the live leg on the feature
    row's ``timestamp``, which is the bar's OPEN time — a different key for the same bar, and
    each leg only ever compares its own keys, so one key per bar is all the rule needs. One
    function so the two legs cannot drift on what "a bar has passed" means — the parity this
    whole rule exists to hold. A ``None`` timestamp still advances (an uncounted bar would stall
    the exit) but records nothing to dedup against, which is the pre-existing behaviour.
    """
    ts = candle_ts if candle_ts is not None else None
    if ts is not None and str(ts) == str(position.get("last_counted_candle_ts") or ""):
        return
    position["holding_candles"] = int(position.get("holding_candles", 0)) + 1
    if ts is not None:
        position["last_counted_candle_ts"] = str(ts)


def _touches(direction: str, candle: Mapping[str, Any], sl: float, tp: float) -> tuple[bool, bool]:
    """(hit_stop, hit_target) for one candle — the same test at every resolution."""
    high = float(candle.get("high") or 0.0)
    low = float(candle.get("low") or 0.0)
    if direction == "LONG":
        return low <= sl, high >= tp
    return high >= sl, low <= tp


def resolve_intrabar_exit(
    position: Mapping[str, Any], fine_candles: Sequence[Mapping[str, Any]]
) -> str | None:
    """Which of stop/target was touched FIRST, read off finer candles. None = unresolved.

    Walks the finer bars in time order and returns on the first one that touches
    either level. A finer bar touching both is ambiguous at its own resolution, so
    it resolves to the stop — the pessimistic assumption survives, but confined to
    the width of one fine bar instead of one whole position-timeframe bar. Returns
    None when the finer series never touches either level (a gap, a short window, a
    stale collection): unresolved must not read as resolved, and the caller falls
    back to the assumption rather than inventing an answer.
    """
    direction = str(position["direction"])
    sl, tp = float(position["stop_loss"]), float(position["take_profit"])
    for candle in sorted(fine_candles, key=lambda c: str(c.get("open_time") or "")):
        hit_stop, hit_target = _touches(direction, candle, sl, tp)
        if hit_stop:
            return "stop_loss"
        if hit_target:
            return "take_profit"
    return None


def advance_managed_stop(position: dict[str, Any], candle: Mapping[str, Any] | None) -> bool:
    """Ratchet the stop on a bar the position survived. Returns whether it moved.

    Two rules, both off unless the plan carries them (see ``strategy.ExitRules``):

    - ``breakeven_at_r`` — once the CLOSE is that many R in favour, the stop moves to entry.
    - ``trail_distance`` — the stop keeps that far behind the best close, in price. Carried in
      price rather than ATR so this stays a pure function of the position and the bar; the
      entry step already knows the ATR and multiplies once, and a settlement that re-derived it
      would need a feature row it is not given.

    **Three properties make this safe to run inside a backtest that gates real money.**

    It ratchets — the stop only ever moves toward profit, never away, so a rule can tighten a
    trade's risk and never widen it. It trails the CLOSE, not the high: trailing the extreme
    would raise the stop on a wick the position never held through a close, which reads as an
    improvement and is an intrabar assumption of exactly the kind ``resolve_intrabar_exit``
    exists to avoid making silently. And it runs only on a bar the position survived, so the
    stop tested on bar N is always the stop that was standing when bar N opened.

    The favourable-side check uses the close for the same reason. A LONG whose close is below
    the trail candidate leaves the stop where it is rather than lowering it.
    """
    if candle is None:
        return False
    close = candle.get("close")
    if not isinstance(close, (int, float)) or isinstance(close, bool):
        return False
    risk = float(position.get("risk") or 0.0)
    if risk <= 0:
        return False
    direction = position.get("direction")
    entry = float(position.get("entry_price") or 0.0)
    current = float(position.get("stop_loss") or 0.0)
    long = direction == "LONG"
    close = float(close)

    candidates: list[float] = []
    breakeven_at_r = position.get("breakeven_at_r")
    if isinstance(breakeven_at_r, (int, float)) and not isinstance(breakeven_at_r, bool):
        moved_r = (close - entry) / risk if long else (entry - close) / risk
        if moved_r >= float(breakeven_at_r):
            candidates.append(entry)
    trail_distance = position.get("trail_distance")
    if isinstance(trail_distance, (int, float)) and not isinstance(trail_distance, bool) and trail_distance > 0:
        candidates.append(close - float(trail_distance) if long else close + float(trail_distance))
    if not candidates:
        return False

    best = max(candidates) if long else min(candidates)
    if (best > current) if long else (best < current):
        position["stop_loss"] = best
        position["stop_moved"] = True
        return True
    return False


def settle_trade_plan(
    position: dict[str, Any],
    candle: Mapping[str, Any] | None,
    last_close: float | None,
    max_hold: int,
    manual_exit: bool,
    fine_candles: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[str | None, float | None, float | None]:
    """(close_reason, exit_price, result_R), or (None, None, None) while still open.

    Precedence: manual exit → intrabar SL/TP → time exit. When the bar touches both
    levels, ``fine_candles`` (finer bars covering that bar) decide which came first;
    without them, or when they resolve nothing, the SL-first assumption stands. Pure:
    collecting the finer bars is the caller's job (see :func:`intrabar_ambiguous`).
    """
    direction = position["direction"]
    entry = float(position["entry_price"])
    sl = float(position["stop_loss"])
    tp = float(position["take_profit"])
    risk = float(position["risk"])

    if manual_exit and last_close is not None:
        return "manual_exit", last_close, _result_r(direction, entry, last_close, risk)

    advance_holding(position, candle.get("close_time") if isinstance(candle, Mapping) else None)
    if candle is not None and risk > 0:
        hit_stop, hit_target = _touches(direction, candle, sl, tp)
        # A stop that has been moved is no longer worth exactly -1R, and the hard-coded
        # constant would report a breakeven exit as a full loss and a trailed exit as a loss
        # when it was a win. Unmoved stops keep the exact -1.0 convention `build_outcome_record`
        # names as the authority — computing it from prices instead would agree today and be a
        # second thing to keep agreeing tomorrow.
        stop_r = _result_r(direction, entry, sl, risk) if position.get("stop_moved") else -1.0
        if hit_stop and hit_target:
            # Both touched: observe the order if we were given the finer bars, and
            # record which basis decided it — an assumed exit is not evidence of the
            # same standing as an observed one.
            resolved = resolve_intrabar_exit(position, fine_candles or ())
            position["exit_resolution"] = "observed_fine_candles" if resolved else "pessimistic_sl_first"
            hit = resolved or "stop_loss"
            if hit == "take_profit":
                return "take_profit", tp, (tp - entry) / risk if direction == "LONG" else (entry - tp) / risk
            return "stop_loss", sl, stop_r
        if hit_stop:
            position["exit_resolution"] = "unambiguous"
            return "stop_loss", sl, stop_r
        if hit_target:
            position["exit_resolution"] = "unambiguous"
            return "take_profit", tp, (tp - entry) / risk if direction == "LONG" else (entry - tp) / risk

    if last_close is not None and int(position.get("holding_candles", 0)) >= int(max_hold):
        return "time_exit", last_close, _result_r(direction, entry, last_close, risk)

    # The position survived this bar, so the stop may ratchet for the NEXT one. Strictly after
    # the exit checks: a stop moved from the same bar it is then tested against would be a
    # level the trade never actually rested at.
    advance_managed_stop(position, candle)
    return None, None, None


def build_outcome_record(
    position: Mapping[str, Any], close_reason: str, exit_price: float, result_r: float, *, now: str
) -> dict[str, Any]:
    """The CLOSED outcome — source-registry field names (``result_R``,
    ``outcome_closed``, ``created_at_utc``), so the C4 risk guard and C6 feedback read
    native and C7-imported outcomes identically. Self-hashed for tamper evidence.

    ``result_R`` is NET of fees and slippage (2026-07-30). ``result_r`` from
    :func:`settle_trade_plan` remains the authority on the GROSS number — including its
    deliberate exactly-``-1.0`` stop convention — and this function only subtracts what
    ``cost.apply_cost_model`` says the two legs cost. Deriving gross a second time from the
    prices would agree today (``risk`` is ``|entry - stop|`` by construction) and would be a
    second thing to keep agreeing tomorrow.
    """
    costs = apply_cost_model(
        str(position.get("direction") or ""),
        float(position.get("entry_price") or 0.0),
        float(exit_price),
        float(position.get("risk") or 0.0),
        close_reason=close_reason,
    )
    gross_r = float(result_r)
    net_r = gross_r - costs.fee_cost_r - costs.slippage_cost_r
    record = {
        "outcome_id": integrity.short_id(
            "out", {"position_id": position.get("position_id"), "reason": close_reason, "closed_at": now}
        ),
        "outcome_closed": True,
        "result_R": round(net_r, 8),
        # The gross figure and what came off it, so the row explains its own number and a
        # reader can recover the pre-cost statistic without re-deriving it from prices. Mirrors
        # what `factory.backtest_spec` has always recorded in `cost_summary`.
        "gross_result_R": round(gross_r, 8),
        "fee_cost_r": costs.fee_cost_r,
        "slippage_cost_r": costs.slippage_cost_r,
        "maker_fee_cost_r": costs.maker_fee_cost_r,
        # Judged on the NET number: a trade that cleared its stop but not its costs is not a
        # win, and calling it one is how a losing book reports a healthy win rate.
        "win_loss": "WIN" if net_r > 0 else ("LOSS" if net_r < 0 else "FLAT"),
        "close_reason": close_reason,
        "created_at_utc": now,
        "venue": str(position.get("venue") or DEFAULT_VENUE),
        "symbol": position.get("symbol"),
        "timeframe": position.get("timeframe"),
        "direction": position.get("direction"),
        "entry_price": position.get("entry_price"),
        "exit_price": float(exit_price),
        # Risk per unit (|entry - stop|) — the denominator `result_R` was divided by. It was
        # always on the position and never on the outcome, which left a settled row unable to
        # convert its own R into money or into a cost. Recorded since 2026-07-29 so the row
        # carries every primitive its own arithmetic needs: `cost.outcome_net_r` re-prices the
        # pre-2026-07-30 rows NET of fees, slippage and carry, and that conversion needs the
        # same denominator the gross figure used. Reconstructing it from `result_R` divides by
        # zero on a break-even trade, which is exactly the row a cost model would turn negative.
        "risk": position.get("risk"),
        "holding_candles": position.get("holding_candles"),
        "position_id": position.get("position_id"),
        "opened_at_utc": position.get("opened_at_utc"),
        "strategy_id": position.get("strategy_id"),
        "candidate_id": position.get("candidate_id"),
        "strategy_rule_hash": position.get("strategy_rule_hash"),
        "strategy_generation_id": position.get("strategy_generation_id"),
        ARTIFACT_SHA256_FIELD: position.get(ARTIFACT_SHA256_FIELD),
        "supporting_strategy_ids": list(position.get("supporting_strategy_ids") or []),
        # How the exit price was decided: "unambiguous" (only one level touched),
        # "observed_fine_candles" (both touched, finer bars showed the order), or
        # "pessimistic_sl_first" (both touched, unresolved — the stop is assumed).
        # Carried so a later reading can weigh assumed exits differently from
        # observed ones instead of treating every outcome as equally measured.
        "exit_resolution": position.get("exit_resolution") or "unambiguous",
        "provenance": PAPER_PROVENANCE,
        # Intended fills, NET of costs. The value changed on 2026-07-30 and the label changed
        # with it, so a window spanning that day can SEE that it mixes two bases rather than
        # silently averaging them (see vocabulary.R_BASIS_*). Rows written before keep `intent`
        # and cannot be re-priced: the stored row has no `risk`, and the cost model is
        # denominated in risk-per-unit.
        "r_basis": R_BASIS_INTENT_NET,
    }
    # WHICH RATES charged this row. `result_R` above is already net of them, and
    # `gross_result_R` / `fee_cost_r` / `slippage_cost_r` beside it say how much came off — so
    # what is left to record is the rates themselves, without which none of those can be
    # re-derived when the schedule changes. The same disclosure `factory.backtest_spec` makes
    # in `cost_summary`, and the audit trail of what the runtime believed on the day.
    #
    # This block also wrote a `result_R_net`, from the increment where `result_R` stayed gross
    # and consumers re-derived net at read time. That field is gone rather than kept: settlement
    # charges the costs now, so the two would be one fact under two names — and
    # `cost.outcome_net_r` correctly declines to re-price a row whose own basis already reads
    # `intent_net_of_costs`, so it would have written a null on every settlement.
    #
    # `CostModel` is imported by name rather than reached through the `costs` module alias: the
    # local `costs` above is a CostBreakdown and shadows it inside this function. That shadowing
    # is what made the first pass of this merge raise on every settlement.
    default_model = CostModel()
    record["cost_model"] = {
        "taker_fee_bps": default_model.taker_fee_bps,
        "maker_fee_bps": default_model.maker_fee_bps,
        "slippage_bps": default_model.slippage_bps,
    }
    # Idempotency key: one position settles exactly once, so the settlement's
    # identity derives from the position alone — a retried settlement of the same
    # position mints the SAME settlement_id (unlike outcome_id, which varies with
    # close reason and clock), making duplicates detectable.
    record["settlement_id"] = integrity.short_id("settle", {"position_id": position.get("position_id")})
    record["record_sha256"] = integrity.sha256_record(record)
    return record


# The paper kernel reads this helper too, through the public name. The definition keeps its private name
# so that the bodies above read exactly as they did in `paper`.
touches = _touches
