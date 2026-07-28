"""LP5.3 tests — the live entry decision. Decides everything, sends nothing.

Two properties carry the weight here.

**Nothing reaches READY except through the real guard.** Every door — the C4 verdict, the
LP5.1 reconciliation, LP5's concurrency caps, the venue filters, the bracket, LP5.2's
sizing and finally LP3's ``evaluate_live_order_guard`` — is exercised as a refusal, and
``ready`` is asserted to be derived from the guard's own ``approved`` rather than claimed.

**The size corresponds to the stop that would actually be placed.** The bracket is priced
and tick-rounded first, and the quantity is computed from the *rounded* distance. A stop
that rounds away from the entry would otherwise leave a position risking more than the
size was chosen for.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import live_entry as le
from runtime.mvp_runtime.crypto.account import AccountPosition, AccountSnapshot
from runtime.mvp_runtime.crypto.live_order import (
    CANARY_CONFIRMATION_PHRASE,
    LIVE_CONFIRMATION_PHRASE,
    LiveOrderLimits,
)
from runtime.mvp_runtime.crypto.live_position import reconcile_positions
from runtime.mvp_runtime.crypto.live_sizing import SymbolFilters

NOW = "2026-07-25T12:00:00Z"

FILTERS = SymbolFilters(step_size=0.001, min_qty=0.001, min_notional=5.0, tick_size=0.1)

PLAN = {
    "symbol": "BTCUSDT", "timeframe": "1d", "direction": "LONG",
    "entry_price": 60000.0, "stop_loss": 59000.0, "take_profit": 62000.0,
    "risk": 1000.0, "strategy_id": "S001", "candidate_id": "cand_1",
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

FLAT = AccountSnapshot(
    asset="USDT", wallet_balance=1000.0, margin_balance=1000.0, available_balance=1000.0,
    unrealized_pnl=0.0, positions=[], realized_windows={}, source="test", collected_at=NOW,
)


def _snapshot(*positions):
    return AccountSnapshot(
        asset="USDT", wallet_balance=1000.0, margin_balance=1000.0, available_balance=1000.0,
        unrealized_pnl=0.0, positions=list(positions), realized_windows={},
        source="test", collected_at=NOW,
    )


def _position(symbol="BTCUSDT", side="LONG", quantity=0.001, notional=60.0):
    return AccountPosition(
        symbol=symbol, side=side, quantity=quantity, entry_price=60000.0, mark_price=60000.0,
        unrealized_pnl=0.0, leverage=1.0, notional=notional,
    )


ALLOWING_VERDICT = {"allow_new_position": True, "problems": []}


def _plan(**kw):
    """Every door open by default; each test closes exactly one.

    The verdict is supplied explicitly, like every other fact. It used to be omitted, which made
    the *unguarded* path the tested happy path — the review finding that closed this fail-open.
    """
    args = dict(
        verdict=kw.pop("verdict", ALLOWING_VERDICT),
        plan=kw.pop("plan", PLAN),
        symbol=kw.pop("symbol", "BTCUSDT"),
        reconciliation=kw.pop("reconciliation", reconcile_positions([], FLAT, now=NOW)),
        local_positions=kw.pop("local_positions", []),
        snapshot=kw.pop("snapshot", FLAT),
        filters=kw.pop("filters", FILTERS),
        limits=kw.pop("limits", LIMITS),
        budget_registered=kw.pop("budget_registered", True),
        gate_open=kw.pop("gate_open", True),
        runtime_active=kw.pop("runtime_active", True),
        daily_loss_breached=kw.pop("daily_loss_breached", False),
        clean_canary_orders=kw.pop("clean_canary_orders", 3),
        submitted_today=kw.pop("submitted_today", 0),
        equity_usdt=kw.pop("equity_usdt", 1000.0),
        now=kw.pop("now", NOW),
        **kw,
    )
    return le.plan_live_entry(**args)


# --- the happy path, and what READY actually means -----------------------------

def test_every_door_open_reaches_ready():
    decision = _plan()
    assert decision["status"] == le.STATUS_READY
    assert decision["ready"] is True
    assert decision["reasons"] == []
    assert decision["guard"]["approved"] is True
    assert decision["intent"]["symbol"] == "BTCUSDT"
    assert decision["sizing"]["sizable"] is True


def test_ready_is_derived_from_the_guard_never_asserted():
    """`ready` is the one boolean an execution step reads, so it must not be possible for
    it to be True while the guard said no."""
    decision = _plan(gate_open=False)
    assert decision["ready"] is False
    assert decision["guard"]["approved"] is False


def test_the_decision_carries_everything_an_executor_needs():
    decision = _plan()
    for key in ("intent", "sizing", "bracket", "guard"):
        assert key in decision, key
    assert decision["intent"]["order_type_exchange"] == "MARKET"
    assert decision["intent"]["reduce_only"] is False


def test_the_module_cannot_send_an_order():
    """The safety property that lets everything above be tested without a venue: this
    module has no adapter, and imports none."""
    import runtime.mvp_runtime.crypto.live_entry as module

    source = module.__doc__ or ""
    assert "sends nothing" in source.lower() or "Decides everything; sends nothing" in source
    assert not hasattr(module, "select_order_adapter")
    assert not hasattr(module, "submit_and_reconcile")


# --- no route ------------------------------------------------------------------

@pytest.mark.parametrize("plan", [None, {}])
def test_no_plan_is_no_route_not_a_refusal(plan):
    decision = _plan(plan=plan)
    assert decision["status"] == le.STATUS_NO_ROUTE
    assert decision["reasons"] == [le.NO_PLAN]
    assert decision["ready"] is False


# --- the cheap doors, each closed on its own -----------------------------------

def test_a_refusing_verdict_stops_the_entry():
    decision = _plan(verdict={"allow_new_position": False, "problems": ["stale data"]})
    assert decision["status"] == le.STATUS_REFUSED
    assert le.VERDICT_REFUSED in decision["reasons"]
    assert decision["verdict_problems"] == ["stale data"]


def test_an_allowing_verdict_passes_through():
    assert _plan(verdict={"allow_new_position": True, "problems": []})["status"] == le.STATUS_READY


@pytest.mark.parametrize("verdict", [None, {}, "ALLOW", 1, {"problems": []}])
def test_an_absent_or_malformed_verdict_refuses_rather_than_skipping_the_check(verdict):
    """The fail-open this closed. `verdict` used to default to None and be skipped when absent,
    so a caller that forgot it got an entry the C4 guards never saw — the same class as the
    `current_open_notional_usdt = 0.0` default LP5.1c closed. 'I could not read the guards' and
    'the guards said no' have the same correct consequence."""
    decision = _plan(verdict=verdict)
    assert decision["status"] == le.STATUS_REFUSED
    assert le.VERDICT_REFUSED in decision["reasons"]


def test_the_verdict_has_no_default():
    """Structural, not merely tested: omitting it is a TypeError, so no future caller can
    reintroduce the skip by forgetting the argument."""
    import inspect

    parameter = inspect.signature(le.plan_live_entry).parameters["verdict"]
    assert parameter.default is inspect.Parameter.empty


def test_a_drifted_book_refuses_the_entry():
    """LP5.1: the venue is the truth. Something is open at the venue this runtime is not
    tracking, so entering again would stack onto an unknown position."""
    reconciliation = reconcile_positions([], _snapshot(_position()), now=NOW)
    decision = _plan(reconciliation=reconciliation)
    assert le.RECONCILE_REFUSED in decision["reasons"]
    assert decision["reconcile_status"] == "DRIFT"


def test_an_unreadable_account_refuses_the_entry():
    decision = _plan(reconciliation=reconcile_positions([], None, now=NOW))
    assert le.RECONCILE_REFUSED in decision["reasons"]
    assert decision["reconcile_status"] == "ACCOUNT_UNREADABLE"


def test_the_concurrency_cap_refuses_a_third_position():
    local = [
        {"symbol": "ETHUSDT", "quantity": 1.0, "notional_usdt": 50.0},
        {"symbol": "SOLUSDT", "quantity": 1.0, "notional_usdt": 50.0},
    ]
    decision = _plan(local_positions=local,
                     reconciliation={"status": "RECONCILED", "books": {}})
    assert le.CAPACITY_REFUSED in decision["reasons"]
    assert decision["capacity"]["blocks"] == ["LIVE_MAX_CONCURRENT_POSITIONS"]


def test_a_second_position_on_one_symbol_refuses():
    local = [{"symbol": "BTCUSDT", "quantity": 0.001, "notional_usdt": 60.0}]
    decision = _plan(local_positions=local,
                     reconciliation={"status": "RECONCILED", "books": {}})
    assert le.CAPACITY_REFUSED in decision["reasons"]
    assert "LIVE_MAX_POSITIONS_PER_SYMBOL" in decision["capacity"]["blocks"]


@pytest.mark.parametrize("filters", [
    None,
    SymbolFilters(step_size=0.0, min_qty=0.001, min_notional=5.0, tick_size=0.1),
    SymbolFilters(step_size=0.001, min_qty=0.001, min_notional=5.0, tick_size=0.0),
])
def test_absent_or_unusable_filters_refuse(filters):
    """No venue filters means no honest size and no roundable stop — the design forbids
    inventing either."""
    decision = _plan(filters=filters, filters_reason="LIVE_FILTERS_DEGRADED")
    assert le.NO_FILTERS in decision["reasons"]
    assert decision["filters_reason"] == "LIVE_FILTERS_DEGRADED"


def test_the_cheap_doors_accumulate():
    """The guard's own posture: an operator sees every closed door at once, not the first."""
    decision = _plan(
        verdict={"allow_new_position": False, "problems": []},
        reconciliation=reconcile_positions([], None, now=NOW),
        filters=None,
    )
    assert {le.VERDICT_REFUSED, le.RECONCILE_REFUSED, le.NO_FILTERS} <= set(decision["reasons"])


# --- the bracket ----------------------------------------------------------------

def test_both_bracket_legs_round_toward_the_entry_on_a_long():
    plan = {**PLAN, "entry_price": 60000.0, "stop_loss": 58999.95, "take_profit": 62000.05}
    bracket, reason = le.price_bracket(plan, FILTERS)
    assert reason is None
    # stop rounds UP (toward the entry) so realised risk cannot exceed the plan;
    # target rounds DOWN (toward the entry) so profit is taken no later than planned.
    assert bracket["stop_loss"] == 59000.0
    assert bracket["take_profit"] == 62000.0


def test_both_bracket_legs_round_toward_the_entry_on_a_short():
    plan = {**PLAN, "direction": "SHORT", "entry_price": 60000.0,
            "stop_loss": 61000.05, "take_profit": 57999.95}
    bracket, reason = le.price_bracket(plan, FILTERS)
    assert reason is None
    assert bracket["stop_loss"] == 61000.0    # rounded DOWN, toward the entry
    assert bracket["take_profit"] == 58000.0  # rounded UP, toward the entry


def test_the_bracket_sides_close_the_position():
    long_bracket, _ = le.price_bracket(PLAN, FILTERS)
    short_bracket, _ = le.price_bracket({**PLAN, "direction": "SHORT", "stop_loss": 61000.0,
                                         "take_profit": 58000.0}, FILTERS)
    assert long_bracket["stop_side"] == long_bracket["take_profit_side"] == "SELL"
    assert short_bracket["stop_side"] == short_bracket["take_profit_side"] == "BUY"


def test_the_bracket_triggers_on_the_mark_price():
    """A stop triggering on a single wick print of the last-traded tape is the classic way
    to be stopped out of a position that never really moved."""
    bracket, _ = le.price_bracket(PLAN, FILTERS)
    assert bracket["working_type"] == "MARK_PRICE"


def test_a_stop_that_rounds_onto_the_entry_refuses_rather_than_being_repaired():
    coarse = SymbolFilters(step_size=0.001, min_qty=0.001, min_notional=5.0, tick_size=1000.0)
    plan = {**PLAN, "entry_price": 60000.0, "stop_loss": 59900.0, "take_profit": 60100.0}
    bracket, reason = le.price_bracket(plan, coarse)
    assert bracket is None and reason == le.BRACKET_UNPRICEABLE


@pytest.mark.parametrize("plan", [
    {**PLAN, "entry_price": 0.0},
    {**PLAN, "stop_loss": 0.0},
    {**PLAN, "take_profit": 0.0},
    {**PLAN, "direction": "SIDEWAYS"},
])
def test_an_unpriceable_bracket_refuses(plan):
    bracket, reason = le.price_bracket(plan, FILTERS)
    assert bracket is None and reason == le.BRACKET_UNPRICEABLE


def test_an_unpriceable_bracket_refuses_the_whole_entry():
    coarse = SymbolFilters(step_size=0.001, min_qty=0.001, min_notional=5.0, tick_size=1000.0)
    decision = _plan(filters=coarse,
                     plan={**PLAN, "stop_loss": 59900.0, "take_profit": 60100.0})
    assert decision["status"] == le.STATUS_REFUSED
    assert le.BRACKET_UNPRICEABLE in decision["reasons"]


# --- the size corresponds to the stop that will actually be placed --------------

def test_the_size_is_computed_from_the_ROUNDED_stop_distance():
    """The property this ordering exists for. The plan's own risk is 1000; after tick
    rounding the real distance differs, and the size must follow the real one."""
    coarse = SymbolFilters(step_size=0.0001, min_qty=0.0001, min_notional=5.0, tick_size=100.0)
    plan = {**PLAN, "entry_price": 60000.0, "stop_loss": 59050.0, "take_profit": 62000.0,
            "risk": 950.0}
    decision = _plan(filters=coarse, plan=plan, equity_usdt=1_000_000.0,
                     limits=LiveOrderLimits(
                         max_order_notional_usdt=200.0, absolute_max_notional_usdt=200.0,
                         max_daily_order_count=2, max_open_notional_usdt=400.0,
                         daily_loss_limit_usdt=20.0, min_clean_canary_orders=3,
                         confirmation=LIVE_CONFIRMATION_PHRASE))
    # A LONG stop rounds UP (toward the entry): 59050 -> 59100 on a 100 tick, so the real
    # distance is 900, not the plan's 950. The size must follow the 900.
    assert decision["bracket"]["stop_loss"] == 59100.0
    assert decision["bracket"]["risk_per_unit"] == 900.0
    assert decision["sizing"]["risk_per_unit"] == 900.0


def test_the_intent_carries_the_rounded_bracket_prices():
    """What the guard judges and what would be sent must be the same numbers."""
    decision = _plan()
    assert decision["intent"]["stop_loss"] == decision["bracket"]["stop_loss"]
    assert decision["intent"]["take_profit"] == decision["bracket"]["take_profit"]


def test_an_unsizable_plan_refuses():
    decision = _plan(equity_usdt=0.0)
    assert decision["status"] == le.STATUS_REFUSED
    assert le.SIZING_REFUSED in decision["reasons"]
    assert decision["sizing"]["sizable"] is False


# --- the final guard ------------------------------------------------------------

@pytest.mark.parametrize("closed,expected", [
    ({"gate_open": False}, "live trading is not enabled"),
    ({"runtime_active": False}, "runtime"),
    ({"daily_loss_breached": True}, "loss"),
    ({"budget_registered": False}, "budget"),
    ({"clean_canary_orders": 0}, "canary"),
    ({"submitted_today": 2}, "daily order cap"),
])
def test_each_guard_door_refuses(closed, expected):
    decision = _plan(**closed)
    assert decision["status"] == le.STATUS_REFUSED
    assert decision["reasons"] == [le.GUARD_REFUSED]
    assert any(expected in block.lower() for block in decision["guard"]["blocks"]), \
        decision["guard"]["blocks"]


def test_the_exposure_the_guard_sees_comes_from_the_venue():
    decision = _plan(snapshot=_snapshot(_position(symbol="ETHUSDT", notional=100.0)),
                     reconciliation={"status": "RECONCILED", "books": {}})
    assert decision["guard"]["current_open_notional_usdt"] == 100.0


def test_an_unreadable_account_reports_exposure_at_the_cap():
    """LP5.1's fail-closed rule reaching the guard: unknown exposure blocks, never admits."""
    decision = _plan(snapshot=None, reconciliation={"status": "RECONCILED", "books": {}})
    assert decision["guard"]["current_open_notional_usdt"] == LIMITS.max_open_notional_usdt
    assert decision["status"] == le.STATUS_REFUSED
    assert any("open exposure" in b for b in decision["guard"]["blocks"])


def test_the_canary_phrase_cannot_authorize_an_autonomous_entry():
    """One phrase per capability. A machine armed for canaries must not trade autonomously."""
    limits = LiveOrderLimits(
        max_order_notional_usdt=60.0, absolute_max_notional_usdt=200.0,
        max_daily_order_count=2, max_open_notional_usdt=120.0, daily_loss_limit_usdt=20.0,
        min_clean_canary_orders=3, confirmation="", canary_confirmation=CANARY_CONFIRMATION_PHRASE,
    )
    decision = _plan(limits=limits)
    assert decision["status"] == le.STATUS_REFUSED
    assert any("confirmation phrase" in b for b in decision["guard"]["blocks"])


# --- purity + reporting ----------------------------------------------------------

def test_the_decision_is_pure():
    plan = dict(PLAN)
    before = dict(plan)
    _plan(plan=plan)
    assert plan == before


def test_the_status_line_is_ascii():
    for decision in (_plan(), _plan(gate_open=False), _plan(plan=None)):
        line = le.entry_status_line(decision)
        assert line.isascii() and line
