"""One live trade end to end, on the real modules, with no venue and no grant.

Every stage of the live path is covered on its own — LP5.1 the book, LP5.2 sizing, LP5.3 the
decision and the executing leg, LP4 submit+reconcile, LP5.4 the outcome bridge — and each
stage's tests build their own input. `test_mvp_runtime_crypto_live_leg.py` hands
``execute_live_entry`` a hand-written ``DECISION`` dict; `test_..._live_entry.py` checks the
record ``plan_live_entry`` produces. Nobody checks that the first is what the second emits.

That gap is not hypothetical. The canary phrase was dropped between
``resolve_live_order_limits`` and the guard for exactly this reason: both sides tested, the
join not, and the only symptom was an operator being refused at the terminal.

So this walks a single trade through the whole chain, using the real function at every step
and a double only at the venue itself:

    route (C5 strategy)
      -> plan_live_entry (LP5.3 decision, filters parsed from a venue-shaped exchangeInfo)
      -> execute_live_entry (LP5.3 leg: entry, protective bracket, position book)
      -> execute_live_exit (close, bracket withdrawal, realized outcome)
      -> live_outcomes_for_analysis -> run_risk_guard (C4, the paper-shaped consumer)

Nothing is sent: the adapter is injected and answers from a scripted table, the position book
and the P&L ledger are the durable ones pointed at ``tmp_path``, and no grant is involved.

What this pins is the **handover** at each seam — the fields that cross it and the direction
they may move. Refusal branches (unconfirmed entry, failed bracket, naked close, cancel
failure) belong to each stage's own suite and are not duplicated here.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import live_entry, live_execution, live_filters, live_leg
from runtime.mvp_runtime.crypto.account import AccountSnapshot
from runtime.mvp_runtime.crypto.guards import run_risk_guard
from runtime.mvp_runtime.crypto.live_order import (
    CANARY_CONFIRMATION_PHRASE,
    LIVE_CONFIRMATION_PHRASE,
    LiveOrderLimits,
)
from runtime.mvp_runtime.crypto.live_pnl import (
    LIVE_TRADING_FLAGS,
    LIVE_TRADING_PROVIDER_ID,
    RealLiveLedger,
    live_outcomes_for_analysis,
    read_live_outcomes,
)
from runtime.mvp_runtime.crypto.live_position import (
    RealLivePositionStore,
    list_open_live_positions,
    live_capacity,
    reconcile_positions,
)
from runtime.mvp_runtime.crypto.live_sizing import usable_equity_usdt
from runtime.mvp_runtime.crypto.paper import build_entry_plan, route_entries
from runtime.mvp_runtime.safety_gate import Authorization

NOW = "2026-07-26T12:00:00Z"
SYMBOL = "BTCUSDT"

# --- the fixtures each stage would receive in production -----------------------

# A routable pool entry and a feature row that matches it. Deliberately its own copy rather
# than an import from the C5 paper tests: this file must keep asserting the seam even if that
# file's fixture changes, and a seam test that breaks for an unrelated reason is one people
# learn to ignore.
_SPEC = {
    "schema_version": "strategy_spec.v1",
    "strategy_id": "S001",
    "strategy_version": "1.0",
    "strategy_family": "breakout",
    "symbol_scope": [SYMBOL],
    "timeframe": "1d",
    "direction": "long",
    "entry_rules": {
        "operator": "AND",
        "conditions": [
            {"feature": "close", "comparison": ">", "value_from": "ma20"},
            {"feature": "adx", "comparison": ">=", "value": 20.0},
        ],
    },
    "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 2.0, "max_holding_bars": 10},
    "risk_constraints": {"max_risk_per_trade_R": 1.0},
}

POOL = {
    "pool_version": "active_strategy_pool.v1",
    "active_strategies": [{
        "strategy_id": "S001",
        "status": "PAPER_ACTIVE",
        "champion_score": 0.6,
        "candidate_id": "cand_001",
        "strategy_rule_hash": "deadbeef",
        "generation_id": "GEN-001",
        "strategy_spec": _SPEC,
    }],
}

# The ATR is deliberately not a multiple of the price tick: 1.5 * 333.33 = 499.995 puts the
# strategy's stop at 59500.005, which no venue would accept. Rounding therefore actually bites
# in this fixture, so the assertions about it are not vacuous — an earlier draft used an ATR
# that landed exactly on the tick and proved nothing.
#
# It was 133.33 until 2026-07-30, and the change is worth recording rather than tidying away.
# A 1.5 x 133.33 stop is 0.33% of a 60000 entry, so a round trip costs 0.48R in fees and
# slippage — `cost.MAX_ENTRY_COST_R` refuses it, and correctly: the rehearsal had been proving
# the seams join on a trade that cannot make money. The seams are the subject here, so the
# fixture now describes an economic trade (0.19R of friction) and
# `test_a_tight_stop_is_refused_on_economics` keeps the old geometry as the refusal case.
ROW = {"timestamp": "2026-07-26T00:00:00Z", "close": 60000.0, "ma20": 59000.0, "adx": 25.0, "atr": 333.33}

# What the venue answers for BTCUSDT. Parsed by the real LP5.3 reader, so the filters the
# decision uses come out of venue-shaped data rather than a hand-built SymbolFilters.
EXCHANGE_INFO = {
    "symbols": [{
        "symbol": SYMBOL,
        "status": "TRADING",
        "filters": [
            {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "1000"},
            {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "120"},
            {"filterType": "MIN_NOTIONAL", "notional": "5"},
            {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
        ],
    }],
}

LIMITS = LiveOrderLimits(
    max_order_notional_usdt=60.0,
    absolute_max_notional_usdt=200.0,
    max_daily_order_count=2,
    max_open_notional_usdt=120.0,
    daily_loss_limit_usdt=20.0,
    min_clean_canary_orders=3,
    confirmation=LIVE_CONFIRMATION_PHRASE,
    canary_confirmation=CANARY_CONFIRMATION_PHRASE,
)

FLAT_ACCOUNT = AccountSnapshot(
    asset="USDT", wallet_balance=1000.0, margin_balance=1000.0, available_balance=1000.0,
    unrealized_pnl=0.0, positions=[], realized_windows={}, source="rehearsal", collected_at=NOW,
)

_LIVE_AUTH = Authorization(
    flags=LIVE_TRADING_FLAGS, provider_id=LIVE_TRADING_PROVIDER_ID, activation_sha256="sha256:test",
    expires_at="2999-01-01T00:00:00Z", evidence_ref=".runtime_governance_state/evidence.md",
)

# The shape `live_governance.prepare_live_order_governance` returns. Built here rather than
# called, because preparing a real one needs a Core-bound task and this file must run on a
# core-neutral checkout (CI). The leg only requires that a record be present — that it is the
# *right* record is `test_mvp_runtime_crypto_live_governance.py`'s question.
GOVERNANCE = {
    "purpose": "autonomous",
    "order_fingerprint": "sha256:rehearsal",
    "bound_task": {"identity": {"task_id": "T-rehearsal"}, "context": {"core_context_binding_id": "CCB1"}},
    "permission_decision": {"permission_decision_id": "permdec_rehearsal"},
}


class ScriptedVenue:
    """The venue, and only the venue. Every other participant in this file is the real thing.

    Faithful on the two points that decide whether the money is recorded truthfully: an order
    that has not FILLED has executed nothing, and a filled one executed exactly what was
    submitted. The bracket legs rest at NEW, as a stop and a target do until they trigger.
    """

    def __init__(self, *, entry_fill: float = 60000.0, exit_fill: float = 59800.1) -> None:
        self.submitted: list[dict] = []
        self.cancelled: list[str] = []
        self._requests: dict[str, dict] = {}
        self._entry_fill = entry_fill
        self._exit_fill = exit_fill

    @staticmethod
    def _kind(client_order_id: str) -> str:
        for marker, kind in (("_SL_", "SL"), ("_TP_", "TP"), ("_CLOSE_", "CLOSE")):
            if marker in client_order_id:
                return kind
        return "ENTRY"

    def submit(self, order_request, *, timeout_seconds: int = 10):
        self.submitted.append(dict(order_request))
        self._requests[str((order_request.get("clientAlgoId") or order_request["newClientOrderId"]))] = dict(order_request)
        return {"accepted": True}

    def fetch_order(self, symbol, client_order_id, *, timeout_seconds: int = 10, algo: bool = False):
        kind = self._kind(str(client_order_id))
        request = self._requests.get(str(client_order_id))
        if request is None:
            return None
        resting = kind in {"SL", "TP"}
        status = "NEW" if resting else "FILLED"
        price = self._entry_fill if kind == "ENTRY" else self._exit_fill
        quantity = 0.0 if resting else float(request.get("quantity") or 0.0)
        return {
            "symbol": symbol,
            "side": request["side"],
            "status": status,
            "executedQty": quantity,
            "reduceOnly": bool(request.get("reduceOnly")),
            "avgPrice": str(price),
            "cumQuote": str(round(quantity * price, 8)),
            "orderId": f"oid-{kind}",
        }

    def cancel_order(self, symbol, client_order_id, *, timeout_seconds: int = 10, algo: bool = False):
        self.cancelled.append(str(client_order_id))
        return {"status": "CANCELED"}


# --- the walk ------------------------------------------------------------------

def _routed_plan(row=None):
    row = ROW if row is None else row
    route = route_entries(POOL, row, symbol=SYMBOL, timeframe="1d", now=NOW)
    plan = build_entry_plan(route, row, now=NOW)
    assert plan is not None, "the fixture must produce an entry candidate"
    return plan


def _decision(plan, *, local_positions=None, snapshot=FLAT_ACCOUNT):
    filters, reason = live_filters.parse_symbol_filters(EXCHANGE_INFO, SYMBOL)
    assert reason is None
    local = list(local_positions or [])
    return live_entry.plan_live_entry(
        plan,
        symbol=SYMBOL,
        reconciliation=reconcile_positions(local, snapshot, now=NOW),
        local_positions=local,
        snapshot=snapshot,
        filters=filters,
        filters_reason=reason,
        limits=LIMITS,
        budget_registered=True,
        allowed_symbols=[SYMBOL],
        gate_open=True,
        runtime_active=True,
        daily_loss_breached=False,
        bracket_failures_consecutive=0,
        clean_canary_orders=3,
        submitted_today=0,
        equity_usdt=usable_equity_usdt(snapshot),
        now=NOW,
        verdict={"allow_new_position": True, "problems": []},
    )


def _open(decision, *, root, venue=None):
    venue = venue or ScriptedVenue()
    result = live_leg.execute_live_entry(
        decision,
        adapter=venue,
        position_store=RealLivePositionStore(root=root, authorization=_LIVE_AUTH),
        governance=GOVERNANCE,
        gate_open=True,
        limits=LIMITS,
        now=NOW,
    )
    return result, venue


def _close(position, *, root, venue):
    return live_leg.execute_live_exit(
        position,
        adapter=venue,
        position_store=RealLivePositionStore(root=root, authorization=_LIVE_AUTH),
        ledger=RealLiveLedger(root=root, authorization=_LIVE_AUTH),
        gate_open=True,
        limits=LIMITS,
        close_reason="stop_loss",
        now=NOW,
    )


# --- the seams -----------------------------------------------------------------

def test_a_routed_strategy_reaches_a_ready_live_decision():
    """Stage 1 -> 2. The C5 plan's own vocabulary is what LP5.3 reads: nothing in between
    renames direction / entry_price / stop_loss / take_profit."""
    decision = _decision(_routed_plan())
    assert decision["status"] == live_entry.STATUS_READY
    assert decision["ready"] is True
    assert decision["guard"]["approved"] is True


def test_a_tight_stop_is_refused_on_economics():
    """The geometry this fixture used until 2026-07-30, kept as the refusal it now is.

    A 1.5 x 133.33 ATR stop on a 60000 entry risks 0.33% of the notional, so 16 bps of taker
    and slippage is 0.48R of friction — nearly half the risk, gone before the market moves.
    The refusal comes from the same door the paper book uses, and it comes AFTER the bracket
    so the number judged is the tick-rounded risk that would really apply.
    """
    plan = _routed_plan({**ROW, "atr": 133.33})
    decision = _decision(plan)

    assert decision["status"] == live_entry.STATUS_REFUSED
    assert decision["reasons"] == [live_entry.COST_REFUSED]
    assert decision["round_trip_cost_r"] > live_entry.MAX_ENTRY_COST_R
    # Refused on economics, not on any of the doors before it: the bracket priced fine.
    assert decision["bracket"]["risk_per_unit"] > 0
    # And nothing downstream ran — a refused decision carries no size and no intent to send.
    assert decision.get("sizing") is None and decision.get("intent") is None


def test_the_venue_request_carries_the_decided_numbers_and_no_others():
    """Stage 2 -> 3. The quantity and symbol that reach the venue are the decision's, not a
    re-derivation. A leg that recomputed either could send a size the guard never approved."""
    decision = _decision(_routed_plan())
    request = live_execution.build_order_request(decision["intent"])
    assert request["symbol"] == SYMBOL
    assert request["side"] == "BUY"          # a LONG entry
    assert request["type"] == "MARKET"
    assert request["quantity"] == decision["sizing"]["quantity"]
    assert request["reduceOnly"] is False    # an entry, never a close


def test_the_leg_opens_a_position_from_a_decision_the_planner_actually_produced(tmp_path):
    """The join this file exists for: `execute_live_entry` is fed the real record from
    `plan_live_entry`, not a hand-written one, and must find every field it needs in it."""
    decision = _decision(_routed_plan())
    result, venue = _open(decision, root=tmp_path)

    assert result["status"] == live_leg.ENTRY_OPENED
    assert result["reason_codes"] == []

    # Entry plus both protective legs, at the decision's own rounded prices and sides. The two
    # legs have different shapes on purpose: the stop is a closePosition conditional, the target
    # rests in the book as a sized reduceOnly LIMIT so it earns the maker rate.
    sent = {r["type"]: r for r in venue.submitted}
    assert set(sent) == {"MARKET", "STOP_MARKET", "LIMIT"}
    assert sent["STOP_MARKET"]["triggerPrice"] == decision["bracket"]["stop_loss"]
    assert sent["STOP_MARKET"]["side"] == decision["bracket"]["stop_side"]
    assert sent["STOP_MARKET"]["workingType"] == decision["bracket"]["working_type"]
    assert sent["LIMIT"]["price"] == decision["bracket"]["take_profit"]
    assert sent["LIMIT"]["side"] == decision["bracket"]["take_profit_side"]
    assert sent["LIMIT"]["reduceOnly"] is True
    assert sent["LIMIT"]["quantity"] == sent["MARKET"]["quantity"]


def test_the_book_records_the_actual_fill_not_the_planned_entry(tmp_path):
    """Stage 3 -> 4. The venue filled 0.5 below the plan; the book must say so, because
    reconciliation compares the book against the venue and the outcome's R is measured from
    it. Booking the *intent* would report drift on every slipped or partial fill."""
    plan = _routed_plan()
    result, _ = _open(_decision(plan), root=tmp_path, venue=ScriptedVenue(entry_fill=59999.5))
    position = result["position"]

    assert position["entry_price"] == 59999.5 != plan["entry_price"]
    assert [p["entry_price"] for p in list_open_live_positions(tmp_path)] == [59999.5]


def test_the_position_risk_is_the_risk_the_size_was_chosen_for(tmp_path):
    """Stage 2 -> 4, the load-bearing one. ``risk`` is the denominator of every ``result_R``
    the C4 breaker reads, so it must be |entry - ROUNDED stop| * SIZED quantity — not the
    plan's pre-rounding risk, and not a cap."""
    plan = _routed_plan()
    decision = _decision(plan)
    position = _open(decision, root=tmp_path)[0]["position"]

    expected = abs(position["entry_price"] - decision["bracket"]["stop_loss"]) * position["quantity"]
    assert position["risk"] == pytest.approx(expected)
    assert position["quantity"] == decision["sizing"]["quantity"]
    assert position["stop_loss"] == decision["bracket"]["stop_loss"]
    # And the risk actually taken is within the fraction LP5.2 sized for.
    assert position["risk"] <= usable_equity_usdt(FLAT_ACCOUNT) * 0.01 + 1e-9

    # The rounding is real here, and it moved the only way it may: toward the entry, so the
    # realised risk is smaller than the strategy planned, never larger.
    assert decision["bracket"]["stop_loss"] != plan["stop_loss"]
    assert plan["stop_loss"] < decision["bracket"]["stop_loss"] < position["entry_price"]
    assert decision["bracket"]["risk_per_unit"] < plan["risk"]


def test_the_booked_position_closes_the_next_entry_door(tmp_path):
    """Stage 4 -> back to stage 2. A booked position is what the concurrency cap and the
    reconciliation read on the next cycle; if the book write did not land there, the runtime
    would open a second live position on the same symbol."""
    plan = _routed_plan()
    _open(_decision(plan), root=tmp_path)

    stored = list_open_live_positions(tmp_path)
    assert [p["symbol"] for p in stored] == [SYMBOL]
    assert live_capacity(stored, symbol=SYMBOL)["allowed"] is False

    second = _decision(plan, local_positions=stored)
    assert second["status"] == live_entry.STATUS_REFUSED
    # Both doors close: LP5's own per-symbol cap, and reconciliation (the venue snapshot still
    # says flat, so the book and the venue disagree).
    assert live_entry.CAPACITY_REFUSED in second["reasons"]
    assert live_entry.RECONCILE_REFUSED in second["reasons"]


def test_a_settled_live_trade_reaches_the_risk_guard_with_no_live_branch(tmp_path):
    """Stages 4 -> 5 -> 6, the LP5.4 bridge. The outcome a real close produces is read by
    ``run_risk_guard`` — the paper-shaped consumer — with no translation, and its R is
    denominated in the risk the position actually carried."""
    plan = _routed_plan()
    decision = _decision(plan)
    # Closed at the bracket stop: a full -1R loss, by construction.
    opened, venue = _open(decision, root=tmp_path,
                          venue=ScriptedVenue(exit_fill=decision["bracket"]["stop_loss"]))
    closed = _close(opened["position"], root=tmp_path, venue=venue)

    assert closed["status"] == live_leg.EXIT_CLOSED
    assert closed["outcome"]["result_R"] == pytest.approx(-1.0, abs=1e-6)
    # The surviving bracket leg is withdrawn — the venue auto-cancels nothing.
    assert venue.cancelled
    # And the book is clear, so the symbol is tradable again.
    assert list_open_live_positions(tmp_path) == []

    readable, excluded = live_outcomes_for_analysis(read_live_outcomes(tmp_path))
    assert excluded == [] and len(readable) == 1

    # The whole lineage survives the crossing, not just the display id. `lifecycle` groups by
    # these, so a live loss can only demote the strategy that caused it if all three arrive —
    # the factory restarts `strategy_id` at S001 every generation.
    for field in ("candidate_id", "strategy_rule_hash", "strategy_generation_id"):
        assert readable[0][field] == plan[field], field

    verdict = run_risk_guard(readable, now=NOW)
    assert verdict["consecutive_losses"] == 1
    assert verdict["daily_pnl_r"] == pytest.approx(-1.0, abs=1e-6)
