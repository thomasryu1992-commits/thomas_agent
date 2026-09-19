"""The readiness state model v2 (crypto PR5a).

`ready` answered "can THIS process trade" and was read as "live trading is on" (the 2026-09-13
review: `ready: true` beside zero armed strategies). `live_entry_possible` then answered from four
facts and missed the kill, the disarm and every breaker: a KILLED runtime read True for the two hours
its last cycle record stayed fresh, a disarmed one for as long as it kept cycling (FO-10).

v2 names every fact an entry needs as a component, each three-valued, and `live_entry_possible` is
their three-valued AND:

* ``False`` only where an entry door refuses on the fact — never a guess;
* ``None`` where this process cannot observe it — never read as possible;
* ``True`` observed and admitting.

Any False is False, else any None is None, and only all True is True.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.control import ACTIVE, KILLED, PAUSED, ControlState, ControlStore
from runtime.mvp_runtime.crypto import account_store, live_readiness
from runtime.mvp_runtime.crypto import pool as pool_store
from runtime.mvp_runtime.crypto.live_order import CONFIRMATION_ENV, LIVE_CONFIRMATION_PHRASE
from runtime.mvp_runtime.crypto.live_pnl import LIVE_TRADING_ENV
from runtime.mvp_runtime.crypto.market_data import BINANCE_FUTURES, MARKET_DATA_ENV

NOW = "2026-07-23T12:00:00Z"
FIVE_MINUTES_AGO = "2026-07-23T11:55:00Z"
THREE_HOURS_AGO = "2026-07-23T09:00:00Z"

_LIVE_ENVS = (
    LIVE_TRADING_ENV, CONFIRMATION_ENV, "MVP_LIVE_MANUAL_KILL_SWITCH",
    "MVP_LIVE_MAX_ORDER_NOTIONAL_USDT", "MVP_LIVE_ABSOLUTE_MAX_NOTIONAL_USDT",
    "MVP_LIVE_MAX_DAILY_ORDER_COUNT", "MVP_LIVE_MAX_OPEN_NOTIONAL_USDT",
    "MVP_LIVE_DAILY_LOSS_LIMIT_USDT",
    "MVP_ACCOUNT_FEED", "BINANCE_ACCOUNT_API_KEY", "BINANCE_ACCOUNT_API_SECRET",
    "MVP_MARKET_DATA",
)


@pytest.fixture
def clean_env(monkeypatch):
    """A process with no live-trading environment — the console and the assistant's read door."""
    for name in _LIVE_ENVS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


# === the model, over a status (pure) ==========================================================

_CHECKS_CONSOLE = {
    # The env rows as a process without the live-trading environment computes them.
    "live_trading_opt_in": False, "confirmation_phrase": False, "manual_kill_switch": True,
    "account_visibility": False, "market_data_visibility": False, "daily_loss_breaker": False,
    # Every row read from state files, passing.
    "registered_budget": True, "risk_limits_record": True, "runtime_active": True,
    "trading_armed": True, "live_armed_strategies": True, "bracket_breaker": True,
    "api_breaker": True, "entry_marks": True, "pre_order_snapshots": True,
    "execution_stage": True, "venue_contract": True, "order_path_implemented": True,
    "autonomous_routing_wired": True,
}
_ENV_ROWS = ("live_trading_opt_in", "confirmation_phrase", "account_visibility",
             "market_data_visibility", "daily_loss_breaker")


def _status(*, opted=False, **overrides):
    """A board with every component admitting: a console (``opted=False``) reading the trading
    process's fresh records and account snapshot, or the trading process itself (``opted=True``)
    reading its own environment and account."""
    checks = dict(_CHECKS_CONSOLE)
    if opted:
        checks.update({row: True for row in _ENV_ROWS})
    checks.update(overrides.pop("checks", {}))
    inputs = {
        "runtime_control": {"mode": ACTIVE, "trading_armed": True, "fail_closed": False, "error": None},
        "tradable_armed": 1,
        "cycle_window": {"scheduled": True, "interval_seconds": 900, "window_seconds": 2700, "error": None},
        "recorded_fire": {
            "known": True, "created_at": FIVE_MINUTES_AGO, "age_seconds": 300.0, "recent": True,
            "window_seconds": 2700, "contexts": 13, "synthetic": 0, "degraded": 0, "data_health": 0,
            "optional_data": 0, "account_unreadable": 0, "blocked": 0, "blocked_code": None,
            "incident": [], "gate": {"confirmation_present": True, "manual_kill_switch": False},
            "allowance_held": [], "error": None,
        },
        "position_book": {"readable": True, "open": 0, "error": None},
        "account": ({"source": "this_process", "configured": True, "readable": True, "error": None}
                    if opted else
                    {"source": "recorded", "configured": False, "recorded": True,
                     "as_of": FIVE_MINUTES_AGO, "age_seconds": 300.0, "stale": False,
                     "realized_net": 0.0, "daily_loss_breached": False, "loss_error": None}),
        "c4": {"allow": True, "problems": [], "error": None},
        "daily_order_cap": {"submitted_today": 0, "cap": 2, "error": None},
    }
    for key, value in overrides.pop("inputs", {}).items():
        inputs[key] = ({**inputs[key], **value}
                       if isinstance(value, dict) and isinstance(inputs.get(key), dict) else value)
    status = {
        "created_at": NOW,
        "ready": all(checks.values()),
        "checks": [{"check": name, "ok": ok, "detail": ""} for name, ok in checks.items()],
        "recorded_gate": {"known": True, "open": True, "status": "HELD", "recorded_at": FIVE_MINUTES_AGO,
                          "age_seconds": 300.0, "stale": False, "error": None},
        "live_armed_strategies": {"known": True, "armed": 1, "occupying": 3, "error": None},
        "guard_dry_run": {"status": "BLOCKED", "blocks": [], "repairs": []},
        "guard_dry_run_symbol": "BTCUSDT",
        "submitted_today": 0, "counter_error": None,
        "order_path_implemented": True, "autonomous_routing_wired": True,
        "execution_stage": {"stage": "LIVE_AUTONOMOUS", "valid": True, "reason_code": None,
                            "enforced": True, "admits_entry": True},
        "venue_contract": {"error": None, "recorded": True, "status": "PASS", "usable": True,
                           "refusal": None, "symbols": ["BTCUSDT"], "budget_symbols": ["BTCUSDT"],
                           "uncovered": []},
        "pre_order_snapshots": {"readable": True, "appendable": True, "error": None, "count": 0,
                                "last_created_at": None},
        "readiness_inputs": inputs,
    }
    for key, value in overrides.items():
        status[key] = {**status[key], **value} if isinstance(value, dict) and isinstance(status.get(key), dict) else value
    return status


def _state(status):
    data = live_readiness.readiness_data(status)
    return data["live_entry_possible"], data["readiness"]


@pytest.mark.parametrize("opted", [False, True], ids=["console", "trading_process"])
def test_every_component_admitting_is_the_only_true(opted):
    possible, state = _state(_status(opted=opted))
    assert possible is True
    assert state["blocking"] == [] and state["unknown"] == []
    assert list(state["components"]) == list(live_readiness.READINESS_COMPONENTS)
    assert all(c["ok"] is True for c in state["components"].values())


def test_ready_true_with_nothing_armed_is_not_live_entry_possible():
    """The 2026-09-13 review's misreading: `ready: true` beside zero armed strategies, summarised as
    "live trading is on". Every row of this process passes, and nothing can open."""
    data = live_readiness.readiness_data(_status(opted=True, live_armed_strategies={"armed": 0},
                                                 inputs={"tradable_armed": 0}))
    assert data["infrastructure_ready"] is True
    assert data["live_armed_strategies"] == {"known": True, "armed": 0, "occupying": 3, "error": None}
    assert data["live_entry_possible"] is False
    assert data["readiness"]["blocking"] == ["armed_strategy_count:NONE_ARMED"]


def test_the_components_are_the_ones_the_directive_names_the_runtime_control_and_the_cycle():
    """The directive's facts, the runtime control every entry reads (FO-10), and whether the trading
    process is still firing (review of #906) — the one fact the last fire's other readings rest on."""
    assert set(live_readiness.READINESS_COMPONENTS) == {
        "execution_stage", "live_capability_installed", "live_gate_open", "runtime_control",
        "armed_strategy_count", "market_data_ready", "account_ready", "venue_ready", "risk_ready",
        "reconciliation_ready", "trading_cycle_recent",
    }


# One fact at a time, from a board where everything else admits: the component it lands in, the
# value, the reason. `possible` is what the whole answer becomes.
_FLIPS = {
    # FO-10: the kill and the disarm, which the old four-fact answer never read.
    "killed": (dict(inputs={"runtime_control": {"mode": KILLED, "trading_armed": False}}),
               "runtime_control", False, "RUNTIME_KILLED"),
    "paused": (dict(inputs={"runtime_control": {"mode": PAUSED}}),
               "runtime_control", False, "RUNTIME_PAUSED"),
    "disarmed": (dict(inputs={"runtime_control": {"trading_armed": False}}),
                 "runtime_control", False, "TRADING_DISARMED"),
    # PR6: a named halt reads before the arm, as `ControlState.trading_allowed` refuses on either.
    "soft_halt": (dict(inputs={"runtime_control": {"trading_armed": False, "halt_level": "SOFT"}}),
                  "runtime_control", False, "SOFT_HALT"),
    "hard_halt": (dict(inputs={"runtime_control": {"trading_armed": False, "halt_level": "HARD"}}),
                  "runtime_control", False, "HARD_HALT"),
    "halt_whatever_the_arm": (dict(inputs={"runtime_control": {"trading_armed": True, "halt_level": "HARD"}}),
                              "runtime_control", False, "HARD_HALT"),
    "control_fail_closed": (dict(inputs={"runtime_control": {"mode": KILLED, "fail_closed": True}}),
                            "runtime_control", False, "CONTROL_FAIL_CLOSED"),
    "control_unreadable": (dict(inputs={"runtime_control": {"mode": None, "error": "CONTROL_X"}}),
                           "runtime_control", False, "CONTROL_UNREADABLE"),
    # The pool: an unreadable one is refused by the entry door, so it is False, not unknown.
    "pool_unreadable": (dict(live_armed_strategies={"known": False, "armed": None, "error": "POOL_X"},
                             inputs={"tradable_armed": None}),
                        "armed_strategy_count", False, "POOL_UNREADABLE"),
    "none_armed": (dict(live_armed_strategies={"armed": 0}, inputs={"tradable_armed": 0}),
                   "armed_strategy_count", False, "NONE_ARMED"),
    "armed_cannot_trade": (dict(inputs={"tradable_armed": 0}),
                           "armed_strategy_count", False, "ARMED_CANNOT_TRADE"),
    "stage_paper": (dict(execution_stage={"stage": "PAPER", "admits_entry": False}),
                    "execution_stage", False, "STAGE_PAPER"),
    "stage_unbound": (dict(execution_stage={"stage": "READ_ONLY", "valid": False, "admits_entry": False,
                                            "reason_code": "EXECUTION_STAGE_RECORD_MISSING"}),
                      "execution_stage", False, "EXECUTION_STAGE_RECORD_MISSING"),
    "not_installed": (dict(order_path_implemented=False),
                      "live_capability_installed", False, "ORDER_PATH_NOT_IMPLEMENTED"),
    # The gate, from the trading process's own record on a console.
    "gate_disabled": (dict(recorded_gate={"open": False, "status": "DISABLED"}),
                      "live_gate_open", False, "GATE_DISABLED"),
    "confirmation_missing": (dict(inputs={"recorded_fire": {"gate": {"confirmation_present": False,
                                                                      "manual_kill_switch": False}}}),
                             "live_gate_open", False, "CONFIRMATION_MISSING"),
    "manual_kill": (dict(inputs={"recorded_fire": {"gate": {"confirmation_present": True,
                                                             "manual_kill_switch": True}}}),
                    "live_gate_open", False, "MANUAL_KILL_ENGAGED"),
    "switches_not_recorded": (dict(inputs={"recorded_fire": {"gate": None}}),
                              "live_gate_open", None, "SWITCHES_NOT_RECORDED"),
    # The venue contract row's own judgement.
    "contract_stale": (dict(checks={"venue_contract": False},
                            venue_contract={"usable": False, "refusal": "ENTRY_CONTRACT_STALE"}),
                       "venue_ready", False, "ENTRY_CONTRACT_STALE"),
    "contract_unreadable": (dict(checks={"venue_contract": False},
                                 venue_contract={"error": "VENUE_CONTRACT_UNREADABLE", "usable": None}),
                            "venue_ready", False, "CONTRACT_UNREADABLE"),
    "contract_uncovered": (dict(checks={"venue_contract": False},
                                venue_contract={"uncovered": ["BTCUSDT"]}),
                           "venue_ready", False, "BUDGET_SYMBOL_UNCOVERED"),
    # Risk: every breaker and record the entry door refuses on.
    "budget_invalid": (dict(checks={"registered_budget": False}), "risk_ready", False, "BUDGET_INVALID"),
    "risk_limits": (dict(checks={"risk_limits_record": False}), "risk_ready", False, "RISK_LIMITS_UNUSABLE"),
    "bracket": (dict(checks={"bracket_breaker": False}), "risk_ready", False, "BRACKET_BREAKER"),
    "api": (dict(checks={"api_breaker": False}), "risk_ready", False, "API_BREAKER"),
    "marks": (dict(checks={"entry_marks": False}), "risk_ready", False, "ENTRY_MARKS_UNREADABLE"),
    "snapshots": (dict(checks={"pre_order_snapshots": False},
                       pre_order_snapshots={"readable": False, "appendable": False,
                                            "error": "RISK_SNAPSHOT_STORE_TAMPERED"}),
                  "risk_ready", False, "PRE_ORDER_SNAPSHOTS_UNREADABLE"),
    "c4": (dict(inputs={"c4": {"allow": False, "problems": ["DAILY_LOSS_LIMIT"]}}),
           "risk_ready", False, "C4_BREAKER"),
    "c4_unreadable": (dict(inputs={"c4": {"allow": None, "error": "ToolError"}}),
                      "risk_ready", None, "C4_UNREADABLE"),
    "daily_loss": (dict(inputs={"account": {"realized_net": -25.0, "daily_loss_breached": True}}),
                   "risk_ready", False, "DAILY_LOSS_BREACHED"),
    "daily_loss_figure_missing": (dict(inputs={"account": {
        "realized_net": None, "daily_loss_breached": True, "loss_error": "LIVE_PNL_VENUE_FIGURE_MISSING"}}),
        "risk_ready", False, "DAILY_LOSS_FIGURE_MISSING"),
    "order_cap": (dict(inputs={"daily_order_cap": {"submitted_today": 2}}),
                  "risk_ready", False, "DAILY_ORDER_CAP"),
    "order_counter": (dict(inputs={"daily_order_cap": {"error": "LIVE_ORDER_COUNTER_UNREADABLE"}}),
                      "risk_ready", False, "ORDER_COUNTER_UNREADABLE"),
    # Whether the trading process is still firing.
    "pipeline_disabled": (dict(inputs={"cycle_window": {"scheduled": False}}),
                          "trading_cycle_recent", False, "PIPELINE_DISABLED"),
    # The trading process's last fire.
    "account_unreadable_at_the_fire": (dict(inputs={"recorded_fire": {"account_unreadable": 13}}),
                                       "account_ready", False, "ACCOUNT_UNREADABLE"),
    "majority_degraded": (dict(inputs={"recorded_fire": {"degraded": 7}}),
                          "market_data_ready", False, "MAJORITY_DEGRADED"),
    "majority_synthetic": (dict(inputs={"recorded_fire": {"synthetic": 13}}),
                           "market_data_ready", False, "MAJORITY_SYNTHETIC"),
    "majority_data_health": (dict(inputs={"recorded_fire": {"data_health": 13}}),
                             "market_data_ready", False, "MAJORITY_DATA_HEALTH"),
    "majority_optional": (dict(inputs={"recorded_fire": {"optional_data": 9}}),
                          "market_data_ready", False, "MAJORITY_OPTIONAL_DATA"),
    "incident": (dict(inputs={"recorded_fire": {"incident": ["BTCUSDT__4h"]}}),
                 "reconciliation_ready", False, "INCIDENT"),
    "leg_blocked": (dict(inputs={"recorded_fire": {"blocked": 13,
                                                   "blocked_code": "LIVE_POSITION_STATE_UNREADABLE"}}),
                    "reconciliation_ready", False, "LEG_BLOCKED_LIVE_POSITION_STATE_UNREADABLE"),
    "position_book": (dict(inputs={"position_book": {"readable": False, "open": None,
                                                     "error": "LIVE_POSITION_STATE_UNREADABLE"}}),
                      "reconciliation_ready", False, "POSITION_BOOK_UNREADABLE"),
}


@pytest.mark.parametrize("flip", sorted(_FLIPS))
def test_one_fact_lands_in_its_component_and_decides_the_answer(flip):
    overrides, component, ok, reason = _FLIPS[flip]
    possible, state = _state(_status(**overrides))
    got = state["components"][component]
    assert (got["ok"], got["reason"]) == (ok, reason)
    assert possible is ok
    others = {name: c["ok"] for name, c in state["components"].items() if name != component}
    assert all(value is True for value in others.values()), others
    listed = state["blocking"] if ok is False else state["unknown"]
    assert listed == [f"{component}:{reason}"]


@pytest.mark.parametrize("account,reason", [
    ({"recorded": False, "as_of": None, "age_seconds": None, "stale": True, "realized_net": None,
      "daily_loss_breached": None}, "SNAPSHOT_MISSING"),
    ({"age_seconds": 3 * 3600.0, "stale": True, "daily_loss_breached": None}, "SNAPSHOT_STALE"),
], ids=["missing", "stale"])
def test_an_account_snapshot_absent_or_too_old_is_unknown_for_the_account_and_the_daily_loss(account, reason):
    """On a console the account is the trading process's snapshot, dated. Absent or older than two
    hours it speaks for no now — and neither does the loss it carries."""
    possible, state = _state(_status(inputs={"account": account}))
    assert possible is None and state["blocking"] == []
    assert state["unknown"] == ["risk_ready:DAILY_LOSS_UNMEASURED", f"account_ready:{reason}"]


@pytest.mark.parametrize("fire", [
    {"degraded": 6}, {"data_health": 6}, {"account_unreadable": 6},
    {"blocked": 6, "blocked_code": "LIVE_POSITION_STATE_UNREADABLE"},
], ids=["degraded", "data_health", "account_unreadable", "blocked"])
def test_a_minority_of_refused_contexts_refuses_nothing(fire):
    """The rule `pool_cycle_is_stalled` uses, for every refusal the last fire recorded per context:
    half or more. One flaky context of thirteen does not stop the other twelve entering."""
    possible, state = _state(_status(inputs={"recorded_fire": fire}))
    assert possible is True, state["blocking"]


def test_a_venue_contract_that_covers_some_budget_symbols_admits_those():
    """The door judges each entry's own symbol (review of #906): a PASS naming BTC and not ETH admits
    BTC. The check row still fails — the budget names a symbol the verification did not — and the
    component names what it leaves out."""
    status = _status(checks={"venue_contract": False},
                     venue_contract={"budget_symbols": ["BTCUSDT", "ETHUSDT"], "uncovered": ["ETHUSDT"]})
    possible, state = _state(status)
    assert state["components"]["venue_ready"] == {"ok": True, "reason": "USABLE_PARTIAL", "uncovered": ["ETHUSDT"]}
    assert possible is True and status["ready"] is False
    _, state = _state(_status(checks={"venue_contract": False},
                              venue_contract={"budget_symbols": ["BTCUSDT", "ETHUSDT"],
                                              "uncovered": ["BTCUSDT", "ETHUSDT"]}))
    assert state["blocking"] == ["venue_ready:BUDGET_SYMBOL_UNCOVERED"]


def test_a_snapshot_record_that_fails_its_seal_but_takes_rows_refuses_no_entry():
    """The entry path appends and refuses only a damaged line; a row that fails its seal fails the
    check row (the record no longer proves past orders) and leaves entries open (review of #906)."""
    status = _status(checks={"pre_order_snapshots": False},
                     pre_order_snapshots={"readable": False, "appendable": True,
                                          "error": "RISK_SNAPSHOT_STORE_TAMPERED"})
    possible, state = _state(status)
    assert state["components"]["risk_ready"] == {"ok": True, "reason": "CLEAR"}
    assert possible is True and status["ready"] is False
    # A board that does not say whether the store takes rows is read the strict way.
    _, state = _state(_status(checks={"pre_order_snapshots": False}, pre_order_snapshots={"appendable": None}))
    assert state["blocking"] == ["risk_ready:PRE_ORDER_SNAPSHOTS_UNREADABLE"]


def test_a_process_holding_the_account_feed_measures_the_loss_itself():
    """Whose word the daily loss is follows who read the account, not the opt-in (review of #906): a
    process that holds the feed has its own figure, the row, and does not fall back on the snapshot."""
    feed = {"source": "this_process", "configured": True, "readable": True, "error": None}
    _, state = _state(_status(inputs={"account": feed}, checks={"daily_loss_breaker": True}))
    assert state["components"]["risk_ready"] == {"ok": True, "reason": "CLEAR"}
    _, state = _state(_status(inputs={"account": feed}, checks={"daily_loss_breaker": False}))
    assert state["blocking"] == ["risk_ready:DAILY_LOSS"]


def test_a_budget_that_backs_no_order_is_named_once():
    """A daily cap of zero is a budget that backs nothing, which BUDGET_INVALID already names; the cap
    is not a second refusal with nothing submitted against it."""
    _, state = _state(_status(checks={"registered_budget": False},
                              inputs={"daily_order_cap": {"submitted_today": 0, "cap": 0}}))
    assert state["components"]["risk_ready"] == {"ok": False, "reason": "BUDGET_INVALID"}


def test_several_risk_refusals_are_all_named():
    _, state = _state(_status(checks={"api_breaker": False},
                              inputs={"c4": {"allow": False}, "daily_order_cap": {"submitted_today": 5}}))
    assert state["components"]["risk_ready"] == {"ok": False, "reason": "API_BREAKER+C4_BREAKER+DAILY_ORDER_CAP"}


def test_false_beats_unknown():
    """A known refusal is the answer even beside facts nobody can see: a KILLED runtime with no cycle
    on record is not "unknown", it is refused."""
    possible, state = _state(_status(recorded_gate={"known": False, "open": None, "status": None,
                                                    "recorded_at": None, "age_seconds": None},
                                     inputs={"runtime_control": {"mode": KILLED},
                                             "recorded_fire": {"known": False, "contexts": 0}}))
    assert possible is False
    assert state["blocking"] == ["runtime_control:RUNTIME_KILLED"]
    assert "live_gate_open:NO_RECORD" in state["unknown"]


def test_no_cycle_on_record_is_unknown_never_off():
    possible, state = _state(_status(recorded_gate={"known": False, "open": None, "status": None,
                                                    "recorded_at": None, "age_seconds": None},
                                     inputs={"recorded_fire": {"known": False, "created_at": None,
                                                               "contexts": 0, "gate": None}}))
    assert possible is None and state["blocking"] == []
    assert state["unknown"] == ["live_gate_open:NO_RECORD", "trading_cycle_recent:NO_RECORD",
                                "market_data_ready:NO_RECORD", "reconciliation_ready:NO_RECORD"]


def test_an_unreadable_ledger_is_unknown_and_says_so():
    possible, state = _state(_status(recorded_gate={"known": False, "open": None, "error": "OSError"},
                                     inputs={"recorded_fire": {"known": False, "contexts": 0,
                                                               "error": "cycle ledger unreadable: OSError"}}))
    assert possible is None
    assert state["unknown"] == ["live_gate_open:RECORD_UNREADABLE", "trading_cycle_recent:RECORD_UNREADABLE",
                                "market_data_ready:RECORD_UNREADABLE", "reconciliation_ready:RECORD_UNREADABLE"]


def test_a_stale_record_is_no_recent_cycle_and_the_gate_is_unknown_not_off():
    """Entries happen only inside a cycle, so a trading process that has not recorded one lately opens
    nothing — False, under `trading_cycle_recent` alone. What that old fire saw is unknown, not refused,
    and so is the gate: an old record is not evidence about now, and the env rows of a process that
    cannot see the env are not evidence either (FC-10)."""
    possible, state = _state(_status(recorded_gate={"stale": True, "age_seconds": 3 * 3600.0},
                                     inputs={"recorded_fire": {"recent": False, "age_seconds": 3 * 3600.0}}))
    assert possible is False
    assert state["blocking"] == ["trading_cycle_recent:NO_RECENT_CYCLE"]
    assert state["unknown"] == ["live_gate_open:RECORD_STALE", "market_data_ready:NO_RECENT_CYCLE",
                                "reconciliation_ready:NO_RECENT_CYCLE"]
    assert state["components"]["live_gate_open"] == {"ok": None, "reason": "RECORD_STALE", "source": "recorded"}


def test_a_fire_older_than_three_intervals_is_no_recent_cycle_while_its_gate_still_reads():
    """Three missed fires are a scheduler that stopped or a schedule turned off (review of #906): the
    window is the schedule's, not the two hours that keep the recorded gate readable."""
    possible, state = _state(_status(recorded_gate={"age_seconds": 3000.0},
                                     inputs={"recorded_fire": {"recent": False, "age_seconds": 3000.0}}))
    assert possible is False
    assert state["blocking"] == ["trading_cycle_recent:NO_RECENT_CYCLE"]
    assert state["components"]["live_gate_open"]["ok"] is True
    assert state["unknown"] == ["market_data_ready:NO_RECENT_CYCLE", "reconciliation_ready:NO_RECENT_CYCLE"]


def test_a_fire_this_board_cannot_date_is_unknown():
    possible, state = _state(_status(inputs={"recorded_fire": {"recent": None, "age_seconds": None}}))
    assert possible is None and state["blocking"] == []
    assert state["unknown"] == ["trading_cycle_recent:RECORD_UNDATED", "market_data_ready:RECORD_UNDATED",
                                "reconciliation_ready:RECORD_UNDATED"]


def test_the_account_reads_at_an_old_fire_say_nothing_about_now():
    """The leg's failed account reads refuse on the fire they happened in; a fire too old to speak for
    the next one leaves the account to its snapshot."""
    _, state = _state(_status(inputs={"recorded_fire": {"recent": False, "account_unreadable": 13}}))
    assert state["components"]["account_ready"] == {"ok": True, "reason": "SNAPSHOT_FRESH", "source": "recorded"}


# The trading process answers for itself from its own environment and its own account read.
_OPTED_FLIPS = {
    "confirmation": (dict(checks={"confirmation_phrase": False}), "live_gate_open", "CONFIRMATION_MISSING"),
    "manual_kill": (dict(checks={"manual_kill_switch": False}), "live_gate_open", "MANUAL_KILL_ENGAGED"),
    "market_data_env": (dict(checks={"market_data_visibility": False}), "market_data_ready",
                        "MARKET_DATA_NOT_CONFIGURED"),
    "account_feed": (dict(checks={"account_visibility": False},
                          inputs={"account": {"configured": False, "readable": False}}),
                     "account_ready", "ACCOUNT_FEED_NOT_CONFIGURED"),
    "account_read": (dict(inputs={"account": {"readable": False, "error": "ACCOUNT_TIMEOUT"}}),
                     "account_ready", "ACCOUNT_UNREADABLE"),
    "daily_loss_row": (dict(checks={"daily_loss_breaker": False}), "risk_ready", "DAILY_LOSS"),
}


@pytest.mark.parametrize("flip", sorted(_OPTED_FLIPS))
def test_the_trading_process_answers_from_its_own_environment(flip):
    overrides, component, reason = _OPTED_FLIPS[flip]
    possible, state = _state(_status(opted=True, **overrides))
    assert state["components"][component]["ok"] is False
    assert state["components"][component]["reason"] == reason
    assert possible is False and state["blocking"] == [f"{component}:{reason}"]


def test_the_trading_process_gate_is_its_own_env_not_its_last_record():
    """Its own switches are the gate for the process that carries them; a record written before an
    env change says nothing the env does not."""
    _, state = _state(_status(opted=True, recorded_gate={"open": False, "status": "DISABLED"},
                              inputs={"recorded_fire": {"gate": None}}))
    assert state["components"]["live_gate_open"] == {"ok": True, "reason": "OPEN", "source": "this_process"}


def test_a_console_never_reads_its_own_env_rows_as_the_gate():
    """The console's env rows fail because the env is absent, which is a fact about the console
    (#382). With the trading process's fresh record open, the console's gate is open."""
    _, state = _state(_status())
    rows = {c["check"]: c["ok"] for c in _status()["checks"]}
    assert rows["live_trading_opt_in"] is False and rows["confirmation_phrase"] is False
    assert state["components"]["live_gate_open"] == {"ok": True, "reason": "OPEN", "source": "recorded"}
    assert state["components"]["market_data_ready"]["ok"] is True
    assert state["components"]["account_ready"]["ok"] is True


@pytest.mark.parametrize("status", [{}, {"checks": 5}, {"checks": [5, "x", None]}, {"readiness_inputs": [1]}],
                         ids=["empty", "checks_not_a_list", "checks_not_rows", "inputs_not_a_mapping"])
def test_the_model_never_raises_and_never_says_true_on_a_malformed_status(status):
    possible, state = _state(status)
    assert possible is not True
    assert all(c["ok"] is not True for c in state["components"].values())
    json.dumps(state)


def test_the_armed_count_is_the_strategies_the_door_would_accept():
    _, state = _state(_status(live_armed_strategies={"armed": 3}, inputs={"tradable_armed": 2}))
    assert state["components"]["armed_strategy_count"] == {"ok": True, "reason": "ARMED", "count": 2, "armed": 3}


def test_the_view_keeps_the_fields_an_installed_shim_names():
    """`infrastructure_ready`, `live_armed_strategies`, `recorded_gate` and `live_entry_possible` stay
    where an assistant shim installed before v2 looks for them; v2 adds `readiness` beside them."""
    data = live_readiness.readiness_data(_status())
    for field in ("infrastructure_ready", "live_armed_strategies", "recorded_gate", "live_entry_possible",
                  "execution_stage", "venue_contract", "checks", "env_scope", "env_out_of_scope"):
        assert field in data, field
    assert data["live_entry_possible"] is True and data["readiness"]["model"] == live_readiness.READINESS_MODEL


def test_the_data_line_fits_the_assistant_door_on_a_heavy_board():
    """The read shim sends the view as one sorted JSON line cut at 4000 characters
    (`thomas_door_client.DATA_MAX_CHARS`). Keys sort, so a cut loses the tail first: `venue_contract`,
    then `submitted_today`, `recorded_gate`, and `readiness.unknown`. Every component refusing with its
    longest reason, a contract naming thirteen symbols and failing every check, and a venue component
    naming twelve of them uncovered, must still fit whole."""
    from runtime.mvp_runtime.crypto import venue_contract

    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT",
               "LINKUSDT", "DOTUSDT", "LTCUSDT", "TRXUSDT", "MATICUSDT"]
    status = _status(
        checks={name: False for name in _CHECKS_CONSOLE},
        live_armed_strategies={"known": False, "armed": None, "error": "POOL_STRATEGY_ARTIFACT_MISMATCH"},
        execution_stage={"stage": "READ_ONLY", "valid": False, "admits_entry": False,
                         "reason_code": "EXECUTION_STAGE_RECORD_TAMPERED", "recorded_stage": "LIVE_AUTONOMOUS",
                         "stage_id": "stage_" + "f" * 20, "record_sha256": "sha256:" + "e" * 64,
                         "approval_id": "approval_" + "d" * 20, "policy_version": "1.5.1"},
        venue_contract={"error": None, "usable": True, "refusal": None,
                        "failed_checks": list(venue_contract.CHECK_IDS), "symbols": symbols,
                        "budget_symbols": symbols, "uncovered": symbols[1:],
                        "verified_at": NOW, "age_seconds": 20000.0, "stale": True,
                        "version_current": False, "recorded": True, "status": "FAIL"},
        pre_order_snapshots={"readable": False, "appendable": False},
        inputs={"runtime_control": {"mode": KILLED, "fail_closed": True},
                "cycle_window": {"scheduled": False},
                "position_book": {"readable": False},
                "recorded_fire": {"degraded": 13, "account_unreadable": 13,
                                  "gate": {"confirmation_present": False, "manual_kill_switch": True}},
                "c4": {"allow": False}, "daily_order_cap": {"error": "LIVE_ORDER_COUNTER_UNREADABLE"},
                "account": {"daily_loss_breached": True, "loss_error": "LIVE_PNL_VENUE_FIGURE_MISSING"}},
    )
    data = live_readiness.readiness_data(status)
    line = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    # Nine refuse; the order path is installed, and the venue admits its one covered symbol.
    assert len(data["readiness"]["blocking"]) == 9, data["readiness"]["blocking"]
    assert len(line) < 4000, len(line)
    assert list(json.loads(line))[-3:] == ["recorded_gate", "submitted_today", "venue_contract"]


# === the model, over the real board ===========================================================

def _append_records(root, records):
    from runtime.mvp_runtime.store import LEDGER_REL, RECORDS_FILE

    ledger = root / LEDGER_REL
    ledger.mkdir(parents=True, exist_ok=True)
    with (ledger / RECORDS_FILE).open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps({"kind": "crypto_cycle", "record": record}) + "\n")


def _write_fire(root, *, created_at, statuses=("HELD", "HELD"), gate=True, degraded=(), incident=(),
                synthetic=(), switches=None, optional=None, unhealthy=(), loss_refused=(),
                account_unreadable=(), blocked=None, held=()):
    """One fire's cycle records, as the trading process writes them (`cycle.run_crypto_cycle`): one per
    routed context, all stamped with the fire's instant, each carrying the verdicts its data got, the
    gate switches its live leg read and the leg's reason codes.

    ``incident`` names the contexts that halted; ``switches`` overrides one context's switches;
    ``optional`` maps a context to how its optional data failed (PR2d-2): ``stale``, ``missing`` or a
    degrade ``code``; ``unhealthy`` names the contexts whose candles the health check refused, and
    ``loss_refused`` those only the loss breakers refused (the live verdict, not the paper one);
    ``account_unreadable`` those whose leg could not read the account; ``blocked`` maps a context to the
    refusal its leg was BLOCKED on; ``held`` names the strategies the live allowance held back."""
    from runtime.mvp_runtime.crypto.cycle import OPTIONAL_DATA_DEGRADED_CODES
    from runtime.mvp_runtime.crypto.live_route import ACCOUNT_UNREADABLE, ROUTING_PRECONDITION

    records = []
    for index, status in enumerate(statuses):
        if index in (blocked or {}):
            status = "BLOCKED"
        paper = "NO_NEW_POSITION" if index in unhealthy else "ALLOW"
        verdict = "NO_NEW_POSITION" if index in unhealthy or index in loss_refused else "ALLOW"
        record = {
            "symbol": "BTCUSDT", "timeframe": ("1h", "4h", "1d")[index % 3],
            "live_route_status": status, "created_at": created_at,
            "degraded": index in degraded, "collection": {"is_synthetic": index in synthetic},
            "verdict_status": verdict, "paper_verdict_status": paper,
            "live_halt": index in incident,
            "live_gate": ((switches or {}).get(index, {"confirmation_present": True,
                                                      "manual_kill_switch": False})
                          if gate and status != "DISABLED" else None),
            "reason_codes": [],
            "live_reason_codes": ["LIVE_ROUTING_DISABLED"] if status == "DISABLED" else [],
            "live_allowance": ({"breached": [], "blocked_from_live_this_cycle": sorted(held)} if held
                               else None),
        }
        if index in account_unreadable:
            record["live_reason_codes"].append(ACCOUNT_UNREADABLE)
        if index in (blocked or {}):
            record["live_reason_codes"] += [ROUTING_PRECONDITION, blocked[index]]
        how = (optional or {}).get(index)
        if how == "stale":
            record["optional_data_stale"] = ["funding"]
        elif how == "missing":
            record["optional_data_missing"] = ["open_interest"]
        elif how == "code":
            record["reason_codes"] = [sorted(OPTIONAL_DATA_DEGRADED_CODES)[0]]
        records.append(record)
    _append_records(root, records)


_SPEC = {
    "schema_version": "strategy_spec.v1", "strategy_id": "S1", "strategy_version": "1.0",
    "strategy_family": "breakout", "symbol_scope": ["BTCUSDT"], "timeframe": "1d",
    "direction": "long",
    "entry_rules": {"operator": "AND",
                    "conditions": [{"feature": "close", "comparison": ">", "value": 0.0}]},
    "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 2.0, "max_holding_bars": 10},
    "risk_constraints": {"max_risk_per_trade_R": 1.0},
}


def _arm(root, *, approve=True, name_approval=True):
    """Arm S1 as the promotion door does (PR2c-2b, PR3a): a stamped entry at the live tier, naming the
    approval Thomas answered — recorded in the approval store when ``approve``. The gate verifies it
    at order time (`live_route.verify_live_arm`); so does the board."""
    from runtime.mvp_runtime.approval_store import ApprovalStore
    from runtime.mvp_runtime.crypto.strategy import StrategySpec
    from tests._helpers import live_arm_approval, stamped_pool_entry

    entry = stamped_pool_entry({
        "strategy_id": "S1", "candidate_id": "c_S1", "status": "PAPER_ACTIVE", "strategy_spec": _SPEC,
        "strategy_rule_hash": StrategySpec.from_dict(_SPEC).strategy_rule_hash,
        # Inside the approval's window, as the door installs an arm.
        "promoted_at": "2026-07-27T23:55:00Z",
        pool_store.LIVE_TIER_FIELD: pool_store.LIVE_TIER_LIVE,
    })
    approval = live_arm_approval(("c_S1",), (entry["strategy_rule_hash"],),
                                 artifacts=(entry[pool_store.ARTIFACT_SHA256_FIELD],))
    if name_approval:
        entry[pool_store.LIVE_TIER_APPROVAL_FIELD] = approval["approval_id"]
    if approve:
        ApprovalStore.default(root).append([approval])
    path = pool_store.pool_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"active_strategies": [entry]}), encoding="utf-8")


def _schedule_pipeline(root, *, interval_seconds=900, enabled=True):
    """The pool fan-out's schedule, as the operator registers it: the trading process's cadence."""
    from runtime.mvp_runtime.scheduler import KIND_CRYPTO, ScheduleStore, build_schedule

    ScheduleStore(root).add(build_schedule(kind=KIND_CRYPTO, request="", interval_seconds=interval_seconds,
                                           created_by="op", now="2026-07-01T00:00:00Z", enabled=enabled))


def _ready_console_machine(root, monkeypatch):
    """A machine the trading process has made ready, read from a process without its environment:
    armed under a recorded approval and active, a live-autonomous stage, a usable contract over the
    budget, the pipeline scheduled, a fresh fire and a fresh account snapshot."""
    from tests._helpers import gate_stage, record_venue_contract

    from runtime.mvp_runtime.crypto import live_budget

    ControlStore(root).save(ControlState(mode=ACTIVE, updated_by="op", updated_at=NOW, reason="go",
                                         trading_armed=True))
    _arm(root)
    monkeypatch.setattr(live_readiness, "resolve_execution_stage", lambda root=None, **kw: gate_stage())
    rec = live_budget.build_live_trading_budget_record(
        caps=dict(max_order_notional_usdt=60.0, absolute_max_notional_usdt=200.0, max_daily_order_count=2,
                  max_open_notional_usdt=120.0, daily_loss_limit_usdt=20.0),
        symbol_allowlist=["BTCUSDT"], registered_by="thomas", registered_at="2026-07-01T00:00:00Z")
    live_budget.write_registered_budget(rec, root=root)
    record_venue_contract(root, ["BTCUSDT"], verified_at=NOW)
    _schedule_pipeline(root)
    _write_fire(root, created_at=FIVE_MINUTES_AGO)
    _write_snapshot(root, as_of=FIVE_MINUTES_AGO, net=-3.0)


def _write_snapshot(root, *, as_of, net, one_day=None):
    """The account snapshot the trading process stores after each fire (`account_store`): today's
    realized net and the rolling day's, ``one_day`` when they differ. ``as_of`` None is an undated one."""
    path = account_store.snapshot_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "record_type": account_store.RECORD_TYPE, "written_at": as_of,
        "configured": True, "asset": "USDT", "wallet_balance": 500.0, "open_position_count": 0,
        "realized_windows": {"today": {"net": net}, "1d": {"net": net if one_day is None else one_day}},
    }
    if as_of is not None:
        body["as_of"] = as_of
    path.write_text(json.dumps(body), encoding="utf-8")


def _board(root):
    status = live_readiness.build_readiness(root=root, now=NOW)
    data = live_readiness.readiness_data(status)
    return status, data


def test_a_ready_machine_reads_true_from_a_console(tmp_path, clean_env, monkeypatch):
    """The one consumer that matters reads from a process with no live-trading environment. A
    machine the trading process has made ready must read True there — not a permanent unknown."""
    _ready_console_machine(tmp_path, monkeypatch)
    status, data = _board(tmp_path)
    assert status["ready"] is False            # this console cannot trade, and says so
    assert data["live_entry_possible"] is True, data["readiness"]
    assert data["readiness"]["components"]["live_gate_open"]["source"] == "recorded"


def test_a_kill_is_read_at_once_under_a_fresh_open_record(tmp_path, clean_env, monkeypatch):
    """FO-10: a kill drops every later fire, so the last record stays OPEN and fresh for two hours.
    The four-fact answer read True all that time; v2 reads the control state itself."""
    _ready_console_machine(tmp_path, monkeypatch)
    ControlStore(tmp_path).save(ControlState(mode=KILLED, updated_by="op", updated_at=NOW, reason="stop",
                                             trading_armed=False))
    status, data = _board(tmp_path)
    assert status["recorded_gate"]["open"] is True and status["recorded_gate"]["stale"] is False
    assert data["live_entry_possible"] is False
    assert data["readiness"]["blocking"] == ["runtime_control:RUNTIME_KILLED"]


def test_a_control_state_that_cannot_be_read_is_named_as_fail_closed(tmp_path, clean_env, monkeypatch):
    """A damaged control-state file reads KILLED and fail-closed (`ControlStore._corrupt_killed`): the
    board names the fail-closed, which an operator repairs differently from a kill they issued."""
    _ready_console_machine(tmp_path, monkeypatch)
    ControlStore(tmp_path).path.write_text("{damaged", encoding="utf-8")
    _, data = _board(tmp_path)
    assert data["readiness"]["blocking"] == ["runtime_control:CONTROL_FAIL_CLOSED"]


def test_a_disarm_is_read_while_the_runtime_keeps_cycling(tmp_path, clean_env, monkeypatch):
    """FO-10: a disarmed runtime keeps firing and records HELD, which the recorded gate reads as
    open — so the four-fact answer read True indefinitely."""
    _ready_console_machine(tmp_path, monkeypatch)
    ControlStore(tmp_path).save(ControlState(mode=ACTIVE, updated_by="op", updated_at=NOW, reason="soft halt",
                                             trading_armed=False))
    _write_fire(tmp_path, created_at=NOW, statuses=("HELD", "BLOCKED", "HELD"))
    _, data = _board(tmp_path)
    assert data["live_entry_possible"] is False
    assert data["readiness"]["blocking"] == ["runtime_control:TRADING_DISARMED"]


@pytest.mark.parametrize("level", ["SOFT", "HARD"])
def test_a_halt_is_named_on_the_board(tmp_path, clean_env, monkeypatch, level):
    """PR6: the halt the operator placed is its own reason, apart from a disarm nobody named."""
    _ready_console_machine(tmp_path, monkeypatch)
    ControlStore(tmp_path).save(ControlState(mode=ACTIVE, updated_by="op", updated_at=NOW, reason="halt",
                                             trading_armed=False, halt_level=level))
    _write_fire(tmp_path, created_at=NOW, statuses=("HELD", "BLOCKED", "HELD"))
    _, data = _board(tmp_path)
    assert data["live_entry_possible"] is False
    assert data["readiness"]["blocking"] == [f"runtime_control:{level}_HALT"]


def test_a_stale_record_on_a_console_is_not_live_trading_off(tmp_path, clean_env, monkeypatch):
    _ready_console_machine(tmp_path, monkeypatch)
    from runtime.mvp_runtime.store import LEDGER_REL, RECORDS_FILE

    (tmp_path / LEDGER_REL / RECORDS_FILE).unlink()
    _write_fire(tmp_path, created_at=THREE_HOURS_AGO)
    _, data = _board(tmp_path)
    components = data["readiness"]["components"]
    assert components["live_gate_open"]["ok"] is None and components["live_gate_open"]["reason"] == "RECORD_STALE"
    assert data["live_entry_possible"] is False
    assert data["readiness"]["blocking"] == ["trading_cycle_recent:NO_RECENT_CYCLE"]


def test_the_last_fire_is_every_record_at_the_newest_instant(tmp_path, clean_env, monkeypatch):
    """An incident two fires ago is history; one in the last fire halts the next pass the same way."""
    _ready_console_machine(tmp_path, monkeypatch)
    _write_fire(tmp_path, created_at="2026-07-23T11:57:00Z", statuses=("HELD", "INCIDENT", "HELD"),
                incident=(1,))
    _write_fire(tmp_path, created_at="2026-07-23T11:59:00Z", statuses=("HELD", "HELD", "HELD"))
    status, data = _board(tmp_path)
    assert status["readiness_inputs"]["recorded_fire"]["contexts"] == 3
    assert data["readiness"]["components"]["reconciliation_ready"]["ok"] is True
    _write_fire(tmp_path, created_at=NOW, statuses=("HELD", "INCIDENT"), incident=(1,))
    _, data = _board(tmp_path)
    assert data["readiness"]["blocking"] == ["reconciliation_ready:INCIDENT"]


def test_a_breached_daily_loss_in_the_trading_process_snapshot_refuses(tmp_path, clean_env, monkeypatch):
    """The console reads the loss the trading process last measured, against the budget's limit,
    with the entry door's own rule (the stricter of the calendar day and the rolling 24 hours)."""
    _ready_console_machine(tmp_path, monkeypatch)
    _write_snapshot(tmp_path, as_of=FIVE_MINUTES_AGO, net=-25.0)
    _, data = _board(tmp_path)
    assert data["readiness"]["blocking"] == ["risk_ready:DAILY_LOSS_BREACHED"]
    _write_snapshot(tmp_path, as_of=THREE_HOURS_AGO, net=-3.0)
    _, data = _board(tmp_path)
    assert data["live_entry_possible"] is None
    assert data["readiness"]["unknown"] == ["risk_ready:DAILY_LOSS_UNMEASURED", "account_ready:SNAPSHOT_STALE"]


def test_a_tripped_c4_breaker_refuses(tmp_path, clean_env, monkeypatch):
    from runtime.mvp_runtime.crypto import breaker_watch

    _ready_console_machine(tmp_path, monkeypatch)
    monkeypatch.setattr(breaker_watch, "live_risk_verdict", lambda root=None, **kw: {
        "status": "BLOCK", "allow_new_position": False, "problems": ["WEEKLY_LOSS_LIMIT"]})
    status, data = _board(tmp_path)
    assert data["readiness"]["blocking"] == ["risk_ready:C4_BREAKER"]
    assert status["readiness_inputs"]["c4"]["problems"] == ["WEEKLY_LOSS_LIMIT"]


def test_an_armed_entry_the_gate_refuses_is_not_counted(tmp_path, clean_env, monkeypatch):
    _ready_console_machine(tmp_path, monkeypatch)
    monkeypatch.setattr(pool_store, "live_arm_unsound", lambda entry: "spec")
    _, data = _board(tmp_path)
    assert data["readiness"]["components"]["armed_strategy_count"] == {
        "ok": False, "reason": "ARMED_CANNOT_TRADE", "count": 0, "armed": 1}


def test_the_trading_process_answers_from_its_own_switches(tmp_path, clean_env, monkeypatch):
    """Where the environment is present the process's own switches are the gate: a missing phrase is
    refused there whatever the record says."""
    _ready_console_machine(tmp_path, monkeypatch)
    monkeypatch.setenv(LIVE_TRADING_ENV, "real")
    monkeypatch.setenv(MARKET_DATA_ENV, BINANCE_FUTURES)
    _, data = _board(tmp_path)
    components = data["readiness"]["components"]
    assert components["live_gate_open"] == {"ok": False, "reason": "CONFIRMATION_MISSING", "source": "this_process"}
    assert components["account_ready"]["reason"] == "ACCOUNT_FEED_NOT_CONFIGURED"
    monkeypatch.setenv(CONFIRMATION_ENV, LIVE_CONFIRMATION_PHRASE)
    _, data = _board(tmp_path)
    assert data["readiness"]["components"]["live_gate_open"]["ok"] is True


def test_a_halt_is_an_incident_whatever_status_it_was_recorded_with(tmp_path, clean_env, monkeypatch):
    """A halt stops the fan-out on money the runtime cannot account for; the next pass halts again
    until the book and the venue agree. The leg writes INCIDENT with it today — the halt is read on
    its own, so a record that carries one is never read as clear."""
    _ready_console_machine(tmp_path, monkeypatch)
    _write_fire(tmp_path, created_at=NOW, statuses=("HELD", "HELD"), incident=(1,))
    _, data = _board(tmp_path)
    assert data["readiness"]["blocking"] == ["reconciliation_ready:INCIDENT"]


@pytest.mark.parametrize("key,two,one,reason", [
    ("degraded", {"degraded": (0, 1)}, {"degraded": (0,)}, "MAJORITY_DEGRADED"),
    ("synthetic", {"synthetic": (0, 1)}, {"synthetic": (0,)}, "MAJORITY_SYNTHETIC"),
    ("optional_data", {"optional": {0: "stale", 1: "missing"}}, {"optional": {2: "code"}},
     "MAJORITY_OPTIONAL_DATA"),
    ("optional_data", {"optional": {0: "code", 2: "code"}}, {"optional": {1: "stale"}},
     "MAJORITY_OPTIONAL_DATA"),
], ids=["degraded", "synthetic", "optional_stale_missing", "optional_code"])
def test_the_last_fire_counts_the_contexts_refused_on_their_data(tmp_path, clean_env, monkeypatch,
                                                                 key, two, one, reason):
    """The door refuses a context on a synthetic feed, a degraded collection, or optional data that is
    degraded, past its bound or missing from the bar (PR2d-2); two of three is the pool not trading."""
    _ready_console_machine(tmp_path, monkeypatch)
    _write_fire(tmp_path, created_at="2026-07-23T11:59:00Z", statuses=("HELD", "HELD", "HELD"), **two)
    status, data = _board(tmp_path)
    assert status["readiness_inputs"]["recorded_fire"][key] == 2
    assert data["readiness"]["blocking"] == [f"market_data_ready:{reason}"]
    _write_fire(tmp_path, created_at=NOW, statuses=("HELD", "HELD", "HELD"), **one)
    _, data = _board(tmp_path)
    assert data["live_entry_possible"] is True


def test_refusals_of_different_kinds_add_up_to_the_majority():
    """Seven of thirteen contexts refused on their data is a pool that is not trading, whichever
    mix of reasons refused them; the reason names the commonest."""
    _, state = _state(_status(inputs={"recorded_fire": {"degraded": 3, "optional_data": 4}}))
    assert state["components"]["market_data_ready"] == {"ok": False, "reason": "MAJORITY_OPTIONAL_DATA"}
    _, state = _state(_status(inputs={"recorded_fire": {"degraded": 3, "optional_data": 3}}))
    assert state["components"]["market_data_ready"]["ok"] is True


def test_half_the_contexts_degraded_is_the_majority_rule():
    """`pool_cycle_is_stalled`'s rule, half included: one of two contexts is a stalled pool."""
    _, state = _state(_status(inputs={"recorded_fire": {"contexts": 2, "degraded": 1}}))
    assert state["components"]["market_data_ready"] == {"ok": False, "reason": "MAJORITY_DEGRADED"}


@pytest.mark.parametrize("switches,reason", [
    ({1: {"confirmation_present": False, "manual_kill_switch": False}}, "CONFIRMATION_MISSING"),
    ({1: {"confirmation_present": True, "manual_kill_switch": True}}, "MANUAL_KILL_ENGAGED"),
], ids=["one_without_phrase", "one_with_kill"])
def test_one_context_reading_a_closed_switch_closes_the_gate(tmp_path, clean_env, monkeypatch, switches, reason):
    """One process wrote the fire, so its contexts read the same switches; folded the strict way
    anyway, so one context that read a closed switch is never outvoted."""
    _ready_console_machine(tmp_path, monkeypatch)
    _write_fire(tmp_path, created_at=NOW, statuses=("HELD", "HELD", "HELD"), switches=switches)
    _, data = _board(tmp_path)
    assert data["readiness"]["blocking"] == [f"live_gate_open:{reason}"]


@pytest.mark.parametrize("error,code", [("tool", "RISK_LIMITS_TAMPERED"), ("other", "OSError")])
def test_a_c4_verdict_that_cannot_be_computed_is_unknown(tmp_path, clean_env, monkeypatch, error, code):
    from runtime.mvp_runtime.crypto import breaker_watch
    from runtime.mvp_runtime.errors import ToolError

    _ready_console_machine(tmp_path, monkeypatch)

    def _explode(root=None, **kw):
        raise ToolError(code, "unusable") if error == "tool" else OSError("disk")

    monkeypatch.setattr(breaker_watch, "live_risk_verdict", _explode)
    status, data = _board(tmp_path)
    assert status["readiness_inputs"]["c4"] == {"allow": None, "problems": [], "error": code}
    assert data["live_entry_possible"] is None
    assert data["readiness"]["unknown"] == ["risk_ready:C4_UNREADABLE"]


def test_a_failed_account_read_in_the_trading_process_refuses(tmp_path, clean_env, monkeypatch):
    """Where the process holds the feed it reads the account itself, and the leg refuses an entry
    without it."""
    from runtime.mvp_runtime.crypto import account
    from runtime.mvp_runtime.errors import ToolError

    _ready_console_machine(tmp_path, monkeypatch)
    monkeypatch.setenv(LIVE_TRADING_ENV, "real")
    monkeypatch.setenv(account.ACCOUNT_FEED_ENV, account.BINANCE_ACCOUNT)
    monkeypatch.setenv(account.ACCOUNT_API_KEY_ENV, "k")
    monkeypatch.setenv(account.ACCOUNT_API_SECRET_ENV, "s")

    def _fail(**kw):
        raise ToolError("ACCOUNT_READ_TIMEOUT", "no answer")

    monkeypatch.setattr(live_readiness, "read_account", _fail)
    _, data = _board(tmp_path)
    assert data["readiness"]["components"]["account_ready"] == {
        "ok": False, "reason": "ACCOUNT_UNREADABLE", "source": "this_process"}


# === the review of #906: what the board read as possible while the doors refused ================

def test_an_account_the_trading_process_cannot_read_is_refused_on_a_console(tmp_path, clean_env, monkeypatch):
    """The snapshot store keeps its previous snapshot when a read fails, so a console saw a fresh
    snapshot for up to two hours while every leg refused on the account. The leg's own reads at the
    last fire decide first."""
    from runtime.mvp_runtime.crypto import account as account_mod
    from runtime.mvp_runtime.crypto.live_position import entry_allowed, reconcile_positions

    _ready_console_machine(tmp_path, monkeypatch)
    ninety_minutes_ago = "2026-07-23T10:30:00Z"
    _write_snapshot(tmp_path, as_of=ninety_minutes_ago, net=-3.0)
    monkeypatch.setattr(account_mod, "read_account", lambda **kw: (None, {
        "configured": True, "degraded": True, "degraded_reason_code": "ACCOUNT_READ_FAILED",
        "error_reason_code": "BINANCE_HTTP_401"}))
    assert "kept the previous one" in account_store.refresh_snapshot(now=NOW, root=tmp_path)
    assert account_store.read_snapshot(tmp_path)["as_of"] == ninety_minutes_ago
    _write_fire(tmp_path, created_at=NOW, statuses=("HELD", "HELD", "HELD"), account_unreadable=(0, 1, 2))
    status, data = _board(tmp_path)
    assert status["readiness_inputs"]["recorded_fire"]["account_unreadable"] == 3
    assert data["live_entry_possible"] is False
    assert data["readiness"]["blocking"] == ["account_ready:ACCOUNT_UNREADABLE"]
    # The door's side: no account, no entry.
    assert entry_allowed(reconcile_positions([], None, now=NOW), "BTCUSDT") is False


def test_a_damaged_position_book_is_refused_now_and_at_the_fire(tmp_path, clean_env, monkeypatch):
    """The leg reads the book before anything else, and a record it cannot read refuses the whole leg,
    every context (BLOCKED). The board reads the book the same way, and the fire says so too."""
    from runtime.mvp_runtime.crypto.live_position import list_open_live_positions, live_positions_dir
    from runtime.mvp_runtime.errors import ToolError

    _ready_console_machine(tmp_path, monkeypatch)
    book = live_positions_dir(tmp_path)
    book.mkdir(parents=True, exist_ok=True)
    (book / "BTCUSDT.json").write_text("{damaged", encoding="utf-8")
    with pytest.raises(ToolError) as refused:
        list_open_live_positions(tmp_path)
    code = refused.value.reason_code
    _write_fire(tmp_path, created_at=NOW, statuses=("HELD", "HELD", "HELD"),
                blocked={0: code, 1: code, 2: code})
    status, data = _board(tmp_path)
    assert status["readiness_inputs"]["position_book"] == {"readable": False, "open": None, "error": code}
    assert data["readiness"]["blocking"] == ["reconciliation_ready:POSITION_BOOK_UNREADABLE"]
    # Repaired since the fire: the fire still says the legs were refused whole, until the next one.
    (book / "BTCUSDT.json").unlink()
    _, data = _board(tmp_path)
    assert data["readiness"]["blocking"] == [f"reconciliation_ready:LEG_BLOCKED_{code}"]


def test_candles_the_health_check_refuses_are_a_market_data_refusal(tmp_path, clean_env, monkeypatch):
    """Stale market data on every context: the health check refuses, the live verdict merges it, and
    the door refuses the entry. The board counted only synthetic and degraded collections."""
    from datetime import timedelta

    from runtime.mvp_runtime import timeutil
    from runtime.mvp_runtime.crypto.guards import paper_trade_verdict, run_data_health_check

    day = timedelta(days=1)
    start = timeutil.parse_iso(NOW) - 64 * day
    candles = [{"open_time": timeutil.format_iso(start + i * day), "close_time": timeutil.format_iso(start + (i + 1) * day),
                "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0} for i in range(60)]
    health = run_data_health_check({"candles": candles, "is_synthetic": False, "degraded": False},
                                   now=NOW, timeframe_minutes=1440)
    assert health["allow_trading"] is False and paper_trade_verdict(health)["status"] != "ALLOW"
    _ready_console_machine(tmp_path, monkeypatch)
    _write_fire(tmp_path, created_at=NOW, statuses=("HELD", "HELD", "HELD"), unhealthy=(0, 1))
    status, data = _board(tmp_path)
    assert status["readiness_inputs"]["recorded_fire"]["data_health"] == 2
    assert data["readiness"]["blocking"] == ["market_data_ready:MAJORITY_DATA_HEALTH"]


def test_a_fire_the_loss_breakers_refused_is_not_a_data_refusal(tmp_path, clean_env, monkeypatch):
    """The live verdict merges the loss breakers into the health verdict; `risk_ready` names those.
    Market data reads the paper verdict, the health verdict alone, so a breaker is not counted twice."""
    _ready_console_machine(tmp_path, monkeypatch)
    _write_fire(tmp_path, created_at=NOW, statuses=("HELD", "HELD", "HELD"), loss_refused=(0, 1, 2))
    _, data = _board(tmp_path)
    assert data["readiness"]["components"]["market_data_ready"] == {"ok": True, "reason": "FRESH"}


def test_legs_refused_whole_name_the_commonest_refusal(tmp_path, clean_env, monkeypatch):
    _ready_console_machine(tmp_path, monkeypatch)
    _write_fire(tmp_path, created_at=NOW, statuses=("HELD", "HELD", "HELD"),
                blocked={0: "LIVE_BUDGET_UNREADABLE", 1: "LIVE_POSITION_STATE_UNREADABLE",
                         2: "LIVE_POSITION_STATE_UNREADABLE"})
    status, data = _board(tmp_path)
    assert status["readiness_inputs"]["recorded_fire"]["blocked"] == 3
    assert data["readiness"]["blocking"] == ["reconciliation_ready:LEG_BLOCKED_LIVE_POSITION_STATE_UNREADABLE"]


@pytest.mark.parametrize("approve,name_approval,why", [
    (False, True, "LIVE_ARM_APPROVAL_MISSING"),
    (True, False, "no approval"),
], ids=["approval_not_recorded", "no_approval_named"])
def test_an_arm_the_gate_cannot_verify_is_not_counted(tmp_path, clean_env, monkeypatch, approve, name_approval, why):
    """The gate verifies the approval behind an arm at order time (`live_route.verify_live_arm`); the
    board counted every sound entry at the live tier as tradable."""
    _ready_console_machine(tmp_path, monkeypatch)
    _arm(tmp_path, approve=approve, name_approval=name_approval)
    if not approve:
        from runtime.mvp_runtime.approval_store import ApprovalStore

        ApprovalStore.default(tmp_path).path.unlink()
    status, data = _board(tmp_path)
    assert data["readiness"]["components"]["armed_strategy_count"] == {
        "ok": False, "reason": "ARMED_CANNOT_TRADE", "count": 0, "armed": 1}
    row = next(c for c in status["checks"] if c["check"] == "live_armed_strategies")
    assert f"1 of them cannot trade: S1 ({why})" in row["detail"]


def test_an_arm_the_live_allowance_held_back_is_not_counted(tmp_path, clean_env, monkeypatch):
    """A spent allowance holds the lineage out of the leg every fire until the disarm it asks for lands;
    a pool that could not be written still says LIVE."""
    _ready_console_machine(tmp_path, monkeypatch)
    _write_fire(tmp_path, created_at=NOW, statuses=("HELD", "HELD"), held=("S1",))
    status, data = _board(tmp_path)
    assert data["readiness"]["components"]["armed_strategy_count"]["reason"] == "ARMED_CANNOT_TRADE"
    row = next(c for c in status["checks"] if c["check"] == "live_armed_strategies")
    assert "S1 (LIVE_ALLOWANCE_SPENT)" in row["detail"]


def test_a_snapshot_row_that_fails_its_seal_leaves_entries_open(tmp_path, clean_env, monkeypatch):
    """A row that parses but no longer matches its seal fails the check row, and the entry path's
    append still goes through; only a damaged line refuses entries."""
    from runtime.mvp_runtime.crypto import pre_order_gate as g
    from tests._helpers import approved_snapshot
    from tests.test_mvp_runtime_crypto_pre_order_gate import _intent, _store

    _ready_console_machine(tmp_path, monkeypatch)
    _, first = approved_snapshot(_intent())
    store = _store(tmp_path)
    store.append(first)
    path = g.snapshot_path(tmp_path)
    row = json.loads(path.read_text(encoding="ascii").splitlines()[0])
    row["created_at"] = "2026-07-25T12:00:01Z"             # parses; no longer matches its seal
    path.write_text(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n", encoding="ascii")
    status, data = _board(tmp_path)
    assert status["pre_order_snapshots"]["readable"] is False and status["pre_order_snapshots"]["appendable"] is True
    assert data["readiness"]["components"]["risk_ready"] == {"ok": True, "reason": "CLEAR"}
    _, second = approved_snapshot(_intent(candle_time="2026-09-17T04:00:00Z"))
    assert store.append(second) == second["risk_snapshot_sha256"]
    path.write_text(path.read_text(encoding="ascii") + "{damaged\n", encoding="ascii")
    status, data = _board(tmp_path)
    assert status["pre_order_snapshots"]["appendable"] is False
    assert data["readiness"]["blocking"] == ["risk_ready:PRE_ORDER_SNAPSHOTS_UNREADABLE"]


def test_a_budget_symbol_the_contract_does_not_cover_leaves_the_covered_ones_open(tmp_path, clean_env, monkeypatch):
    from runtime.mvp_runtime.crypto import live_budget, venue_contract

    _ready_console_machine(tmp_path, monkeypatch)
    rec = live_budget.build_live_trading_budget_record(
        caps=dict(max_order_notional_usdt=60.0, absolute_max_notional_usdt=200.0, max_daily_order_count=2,
                  max_open_notional_usdt=120.0, daily_loss_limit_usdt=20.0),
        symbol_allowlist=["BTCUSDT", "ETHUSDT"], registered_by="thomas", registered_at="2026-07-01T00:00:00Z")
    live_budget.write_registered_budget(rec, root=tmp_path)
    status, data = _board(tmp_path)
    assert next(c for c in status["checks"] if c["check"] == "venue_contract")["ok"] is False
    assert data["readiness"]["components"]["venue_ready"] == {
        "ok": True, "reason": "USABLE_PARTIAL", "uncovered": ["ETHUSDT"]}
    assert data["live_entry_possible"] is True
    fact = venue_contract.entry_fact(tmp_path)
    assert venue_contract.entry_refusal(fact, symbol="BTCUSDT", at=NOW) is None
    assert venue_contract.entry_refusal(fact, symbol="ETHUSDT", at=NOW)["reason_code"] == \
        venue_contract.ENTRY_CONTRACT_SYMBOL


def test_a_fresh_snapshot_without_the_realized_figure_is_a_trip(tmp_path, clean_env, monkeypatch):
    """The door trips on an account read that carries no realized figure (the venue's income page
    filled); the board read it as unmeasured."""
    from runtime.mvp_runtime.crypto.live_pnl import LIVE_PNL_VENUE_FIGURE_MISSING, live_risk_snapshot

    _ready_console_machine(tmp_path, monkeypatch)
    path = account_store.snapshot_path(tmp_path)
    body = json.loads(path.read_text(encoding="utf-8"))
    body["realized_windows"] = {"today": None, "1d": None}
    path.write_text(json.dumps(body), encoding="utf-8")
    status, data = _board(tmp_path)
    assert status["readiness_inputs"]["account"]["loss_error"] == LIVE_PNL_VENUE_FIGURE_MISSING
    assert data["readiness"]["blocking"] == ["risk_ready:DAILY_LOSS_FIGURE_MISSING"]
    door = live_risk_snapshot(limit_usdt=20.0, root=tmp_path, now=NOW, venue_realized_pnl_usdt=None,
                              venue_required=True)
    assert door["daily_loss_limit_breached"] is True


@pytest.mark.parametrize("interval,enabled,age,reason", [
    (900, False, 300, "PIPELINE_DISABLED"),
    (300, True, 20 * 60, "NO_RECENT_CYCLE"),
], ids=["schedule_off", "three_intervals_missed"])
def test_a_pipeline_that_stopped_firing_is_read_within_its_own_cadence(tmp_path, clean_env, monkeypatch,
                                                                       interval, enabled, age, reason):
    """Two hours was the window: a schedule turned off, or a scheduler that stopped, read True all that
    time. Now the schedule decides — off is off, and three missed intervals are no recent cycle."""
    from runtime.mvp_runtime import timeutil
    from runtime.mvp_runtime.scheduler import SCHEDULES_REL

    _ready_console_machine(tmp_path, monkeypatch)
    (tmp_path / SCHEDULES_REL).unlink()
    _schedule_pipeline(tmp_path, interval_seconds=interval, enabled=enabled)
    _write_fire(tmp_path, created_at=timeutil.format_iso(timeutil.parse_iso(NOW) - __import__("datetime").timedelta(seconds=age)))
    status, data = _board(tmp_path)
    assert status["readiness_inputs"]["cycle_window"]["window_seconds"] == 3 * interval
    assert data["live_entry_possible"] is False
    assert data["readiness"]["blocking"] == [f"trading_cycle_recent:{reason}"]


def test_an_unreadable_schedule_store_falls_back_to_this_hosts_cadence(tmp_path, clean_env, monkeypatch):
    from runtime.mvp_runtime.scheduler import SCHEDULES_REL

    _ready_console_machine(tmp_path, monkeypatch)
    (tmp_path / SCHEDULES_REL).write_text("{damaged\n", encoding="utf-8")
    status, data = _board(tmp_path)
    window = status["readiness_inputs"]["cycle_window"]
    assert window["scheduled"] is None and window["window_seconds"] == 45 * 60 and window["error"]
    assert data["live_entry_possible"] is True


def test_a_record_dated_ahead_of_this_clock_speaks_for_no_now(tmp_path, clean_env, monkeypatch):
    _ready_console_machine(tmp_path, monkeypatch)
    _write_fire(tmp_path, created_at="2027-01-01T00:00:00Z")
    status, data = _board(tmp_path)
    assert status["recorded_gate"]["stale"] is True
    assert status["readiness_inputs"]["recorded_fire"]["recent"] is None
    assert data["live_entry_possible"] is None
    assert "trading_cycle_recent:RECORD_UNDATED" in data["readiness"]["unknown"]


def test_a_newest_record_that_is_not_an_object_is_not_skipped_for_an_older_one(tmp_path, clean_env, monkeypatch):
    """A damaged newest row reads as a newest row that says nothing, never as the fire before it."""
    _ready_console_machine(tmp_path, monkeypatch)
    _append_records(tmp_path, [["not", "a", "record"]])
    status, data = _board(tmp_path)
    assert status["recorded_gate"]["known"] is False
    assert status["readiness_inputs"]["recorded_fire"]["known"] is False
    assert data["live_entry_possible"] is None
    assert "trading_cycle_recent:RECORD_UNREADABLE" in data["readiness"]["unknown"]


# === gaps the review named in the tests themselves ================================================

def test_a_fire_written_before_the_leg_stamped_its_switches_leaves_the_gate_unknown(tmp_path, clean_env, monkeypatch):
    """Records written before PR5a carry no switches: the opt-in is known, the rest is not."""
    _ready_console_machine(tmp_path, monkeypatch)
    _write_fire(tmp_path, created_at=NOW, gate=False)
    _, data = _board(tmp_path)
    assert data["readiness"]["components"]["live_gate_open"] == {
        "ok": None, "reason": "SWITCHES_NOT_RECORDED", "source": "recorded"}
    assert data["live_entry_possible"] is None


def test_the_daily_loss_is_the_stricter_of_the_calendar_day_and_the_rolling_day(tmp_path, clean_env, monkeypatch):
    _ready_console_machine(tmp_path, monkeypatch)
    _write_snapshot(tmp_path, as_of=FIVE_MINUTES_AGO, net=-3.0, one_day=-25.0)
    _, data = _board(tmp_path)
    assert data["readiness"]["blocking"] == ["risk_ready:DAILY_LOSS_BREACHED"]
    _write_snapshot(tmp_path, as_of=FIVE_MINUTES_AGO, net=-25.0, one_day=-3.0)
    _, data = _board(tmp_path)
    assert data["readiness"]["blocking"] == ["risk_ready:DAILY_LOSS_BREACHED"]


def test_an_undated_snapshot_speaks_for_no_now(tmp_path, clean_env, monkeypatch):
    _ready_console_machine(tmp_path, monkeypatch)
    _write_snapshot(tmp_path, as_of=None, net=-3.0)
    _, data = _board(tmp_path)
    assert data["readiness"]["unknown"] == ["risk_ready:DAILY_LOSS_UNMEASURED", "account_ready:SNAPSHOT_STALE"]


# === the text board leads and ends with the answer (PR5b) =====================================

def _text(root):
    status = live_readiness.build_readiness(root=root, now=NOW)
    text = live_readiness.render_readiness_text(status)
    text.encode("ascii")
    return status, text


def _verdict(text):
    """The board's last line, which carries the whole verdict: it is never wrapped (review of #907)."""
    last = text.splitlines()[-1]
    assert last.startswith("LIVE ENTRY POSSIBLE:"), last
    return last


def test_a_ready_machine_says_yes_first_and_last_while_the_console_is_not_ready(tmp_path, clean_env, monkeypatch):
    """The console is never READY — it carries no live-trading environment — and that verdict is
    printed as this process's. The answer a reader keeps is the system's, at both ends."""
    _ready_console_machine(tmp_path, monkeypatch)
    _, text = _text(tmp_path)
    lines = text.splitlines()
    assert lines[1] == "LIVE ENTRY POSSIBLE: YES - the next cycle may open a REAL position"
    assert lines[-1] == "LIVE ENTRY POSSIBLE: YES - the next cycle may open a REAL position"
    assert "THIS PROCESS: NOT READY (THIS PROCESS ONLY)" in text
    # The head says which verdict is this process's by its name, not by where it sits: the board no
    # longer ends with it.
    assert "and so is the THIS PROCESS line" in "\n".join(lines[:12])
    assert not any(line in ("READY", "NOT READY") for line in lines), "a bare READY/NOT READY line is back"


def test_a_kill_is_named_at_both_ends_of_the_board(tmp_path, clean_env, monkeypatch):
    _ready_console_machine(tmp_path, monkeypatch)
    ControlStore(tmp_path).save(ControlState(mode=KILLED, updated_by="op", updated_at=NOW, reason="stop",
                                             trading_armed=False))
    _, text = _text(tmp_path)
    head = text.splitlines()[1:3]
    assert head == ["LIVE ENTRY POSSIBLE: NO - no autonomous entry can open now",
                    "  blocked by : runtime_control (RUNTIME_KILLED)"]
    assert _verdict(text) == "LIVE ENTRY POSSIBLE: NO - blocked by runtime_control (RUNTIME_KILLED)"


def test_what_cannot_be_seen_is_named_and_never_read_as_yes(tmp_path, clean_env, monkeypatch):
    """A fire recorded before the leg stamped its switches: nothing refuses, one fact is unseen."""
    _ready_console_machine(tmp_path, monkeypatch)
    _write_fire(tmp_path, created_at=NOW, gate=False)
    _, text = _text(tmp_path)
    # It leads with what cannot be seen, not with "nothing refuses" (review of #907).
    assert text.splitlines()[1] == "LIVE ENTRY POSSIBLE: UNKNOWN - this process cannot see every fact it needs"
    assert "  unknown    : live_gate_open (SWITCHES_NOT_RECORDED)" in text
    assert _verdict(text) == ("LIVE ENTRY POSSIBLE: UNKNOWN - this process cannot see live_gate_open "
                              "(SWITCHES_NOT_RECORDED)")


def test_every_component_appears_once_in_the_head(tmp_path, clean_env, monkeypatch):
    _ready_console_machine(tmp_path, monkeypatch)
    ControlStore(tmp_path).save(ControlState(mode=ACTIVE, updated_by="op", updated_at=NOW, reason="halt",
                                             trading_armed=False))
    _write_snapshot(tmp_path, as_of=THREE_HOURS_AGO, net=0.0)
    _, text = _text(tmp_path)
    head = text[:text.index("  The rows below are THIS process's own checks")]
    for name in live_readiness.READINESS_COMPONENTS:
        assert head.count(name) == 1, name
    assert "  blocked by : runtime_control (TRADING_DISARMED)" in head
    assert "account_ready (SNAPSHOT_STALE)" in head and "risk_ready (DAILY_LOSS_UNMEASURED)" in head


def test_the_trading_process_board_still_ends_with_the_system_answer(tmp_path, clean_env, monkeypatch):
    """Where the environment is present the rows are the gate, and THIS PROCESS is the machine
    that trades — READY there still is not the answer to "can an entry open"."""
    _ready_console_machine(tmp_path, monkeypatch)
    monkeypatch.setenv(LIVE_TRADING_ENV, "real")
    monkeypatch.setenv(CONFIRMATION_ENV, LIVE_CONFIRMATION_PHRASE)
    monkeypatch.setenv(MARKET_DATA_ENV, BINANCE_FUTURES)
    status, text = _text(tmp_path)
    assert "THIS PROCESS CANNOT SEE" not in text
    assert "THIS PROCESS: NOT READY - every FAIL above must clear first" in text
    # No account feed in this test process: the trading process's leg would refuse without one.
    assert "account_ready (ACCOUNT_FEED_NOT_CONFIGURED)" in _verdict(text)
    assert status["ready"] is False


def test_a_ready_process_is_named_as_this_process_and_the_answer_still_ends_the_board():
    status = _status(opted=True)
    text = live_readiness.render_readiness_text(status)
    text.encode("ascii")
    assert status["ready"] is True
    assert "THIS PROCESS: READY - every check above passes" in text
    assert text.splitlines()[-1] == "LIVE ENTRY POSSIBLE: YES - the next cycle may open a REAL position"


def test_an_old_closed_record_does_not_speak_for_now_either(tmp_path, clean_env):
    """Only a FRESH record of a closed gate lets this container's env rows say "off" for the system;
    an old one is no statement about now, whichever way it pointed."""
    from runtime.mvp_runtime.store import LEDGER_REL, RECORDS_FILE

    ledger = tmp_path / LEDGER_REL
    ledger.mkdir(parents=True, exist_ok=True)
    (ledger / RECORDS_FILE).write_text(json.dumps({"kind": "crypto_cycle", "record": {
        "live_route_status": "DISABLED", "created_at": THREE_HOURS_AGO}}) + "\n", encoding="utf-8")
    status, text = _text(tmp_path)
    assert live_readiness.env_out_of_scope(status) is True
    opt_in = next(line for line in text.splitlines() if line.startswith("[") and "live_trading_opt_in" in line)
    assert opt_in.startswith(f"[{live_readiness.OUT_OF_SCOPE_MARK}]"), opt_in
    assert "is over two hours old - not a statement about now" in text
    (ledger / RECORDS_FILE).write_text(json.dumps({"kind": "crypto_cycle", "record": {
        "live_route_status": "DISABLED", "created_at": FIVE_MINUTES_AGO}}) + "\n", encoding="utf-8")
    status, _ = _text(tmp_path)
    assert live_readiness.env_out_of_scope(status) is False


def test_the_cli_json_carries_the_readiness_state_and_the_exit_code_stays_this_process(monkeypatch, capsys):
    status = _status(opted=True, inputs={"runtime_control": {"mode": KILLED}})
    monkeypatch.setattr(live_readiness, "build_readiness", lambda *a, **kw: status)
    assert live_readiness.main(["--json"]) == 0          # every check of THIS process passes
    payload = json.loads(capsys.readouterr().out)
    assert payload["ready"] is True
    # The [data] line's shape: the answer at the top level, the state under `readiness` (review of #907).
    assert payload["live_entry_possible"] is False
    assert payload["readiness"]["blocking"] == ["runtime_control:RUNTIME_KILLED"]
    assert set(payload["readiness"]) == {"model", "blocking", "unknown", "components"}
    data = live_readiness.readiness_data(status)
    assert {key: payload[key] for key in ("live_entry_possible", "readiness")} == {
        key: data[key] for key in ("live_entry_possible", "readiness")}


def test_the_text_cli_exit_code_is_this_process_too(monkeypatch, capsys):
    """The exit code follows THIS process's `ready` on the text path as on `--json` — a script
    precondition run where trading runs — and the text still ends with the system's answer."""
    status = _status(opted=True, inputs={"runtime_control": {"mode": KILLED}})
    monkeypatch.setattr(live_readiness, "build_readiness", lambda *a, **kw: status)
    assert live_readiness.main([]) == 0
    assert _verdict(capsys.readouterr().out.rstrip("\n")) == (
        "LIVE ENTRY POSSIBLE: NO - blocked by runtime_control (RUNTIME_KILLED)")
    blind = _status()                                     # a console: its env rows fail
    monkeypatch.setattr(live_readiness, "build_readiness", lambda *a, **kw: blind)
    assert live_readiness.main([]) == 1
    assert _verdict(capsys.readouterr().out.rstrip("\n")) == (
        "LIVE ENTRY POSSIBLE: YES - the next cycle may open a REAL position")


# === review of #907 ===========================================================================

def test_the_last_line_carries_the_answer_however_many_facts_it_names(tmp_path, clean_env, monkeypatch):
    """Wrapped, the verdict ended the board on a bare item — on production, `armed_strategy_count
    (NONE_ARMED)` — and a reader keeping the last line kept no answer."""
    _ready_console_machine(tmp_path, monkeypatch)
    _write_snapshot(tmp_path, as_of=THREE_HOURS_AGO, net=0.0)
    _, text = _text(tmp_path)
    assert _verdict(text) == ("LIVE ENTRY POSSIBLE: UNKNOWN - this process cannot see risk_ready "
                              "(DAILY_LOSS_UNMEASURED), account_ready (SNAPSHOT_STALE)")
    ControlStore(tmp_path).save(ControlState(mode=KILLED, updated_by="op", updated_at=NOW, reason="stop",
                                             trading_armed=False))
    # A fire later than the fixture's, alone at its instant: two of three legs refused whole.
    _write_fire(tmp_path, created_at="2026-07-23T11:56:00Z", statuses=("HELD",) * 3,
                blocked={0: "X", 1: "Y"})
    _write_snapshot(tmp_path, as_of=FIVE_MINUTES_AGO, net=0.0)
    _, text = _text(tmp_path)
    assert _verdict(text) == ("LIVE ENTRY POSSIBLE: NO - blocked by runtime_control (RUNTIME_KILLED), "
                              "reconciliation_ready (LEG_BLOCKED_X)")


def test_the_head_lists_wrap_between_items_and_never_inside_one(tmp_path, clean_env, monkeypatch):
    """The head's lists wrap at 80 columns; an item wider than the room left keeps a line of its own."""
    _ready_console_machine(tmp_path, monkeypatch)
    _, text = _text(tmp_path)
    lines = text.splitlines()
    head = lines[1:lines.index("")]
    admitting = [line for line in head if line.startswith(("  admitting  : ", " " * 15))]
    assert len(admitting) > 1, head
    assert all(len(line) <= 80 for line in head), [len(line) for line in head]
    assert ", ".join(line[15:].rstrip(",") for line in admitting).split(", ") == [
        name for name in live_readiness.READINESS_COMPONENTS]
    wide = "reconciliation_ready (LEG_BLOCKED_LIVE_POSITION_STATE_UNREADABLE+AND_A_LONG_TAIL_OF_REASONS)"
    assert live_readiness._wrap("  blocked by : ", ["risk_ready (C4_BREAKER)", wide]) == [
        "  blocked by : risk_ready (C4_BREAKER),", " " * 15 + wide]


# The flips the stall rule decides (`_majority`): False for most of the last fire's contexts, while a
# minority may still enter. Every other False refuses every entry.
_STALL_RULE_FLIPS = {"account_unreadable_at_the_fire", "majority_degraded", "majority_synthetic",
                     "majority_data_health", "majority_optional", "leg_blocked"}


@pytest.mark.parametrize("flip", sorted(name for name, flip in _FLIPS.items() if flip[2] is False))
def test_only_the_stall_rule_leaves_a_minority_that_may_still_enter(flip):
    """7 of 13 contexts refused on their candles is False by the stall rule, and the other 6 may open
    REAL positions at the next cycle: "no autonomous entry can open now" was false there."""
    overrides, component, _, reason = _FLIPS[flip]
    status = _status(**overrides)
    minority = flip in _STALL_RULE_FLIPS
    assert live_readiness.minority_may_enter(live_readiness.readiness_state(status)) is minority
    text = live_readiness.render_readiness_text(status)
    text.encode("ascii")
    lines = text.splitlines()
    assert lines[1] == ("LIVE ENTRY POSSIBLE: NO - for most contexts; a minority may still enter" if minority
                        else "LIVE ENTRY POSSIBLE: NO - no autonomous entry can open now")
    assert lines[-1] == (f"LIVE ENTRY POSSIBLE: NO - blocked by {component} ({reason})"
                         + ("; a minority of contexts may still enter" if minority else ""))


def test_a_stall_rule_refusal_beside_a_hard_one_leaves_no_minority():
    status = _status(inputs={"recorded_fire": {"data_health": 7},
                             "runtime_control": {"mode": KILLED, "trading_armed": False}})
    state = live_readiness.readiness_state(status)
    assert state["blocking"] == ["runtime_control:RUNTIME_KILLED", "market_data_ready:MAJORITY_DATA_HEALTH"]
    assert live_readiness.minority_may_enter(state) is False
    lines = live_readiness.render_readiness_text(status).splitlines()
    assert lines[1] == "LIVE ENTRY POSSIBLE: NO - no autonomous entry can open now"
    assert "minority" not in lines[-1]


def test_the_trading_process_own_failed_account_read_refuses_every_entry():
    """Its own read failing is not the stall rule: the leg reads the account before every entry."""
    state = live_readiness.readiness_state(
        _status(opted=True, inputs={"account": {"readable": False, "error": "ACCOUNT_TIMEOUT"}}))
    assert state["blocking"] == ["account_ready:ACCOUNT_UNREADABLE"]
    assert live_readiness.minority_may_enter(state) is False


def test_a_majority_refusal_on_a_real_board_is_named_as_one(tmp_path, clean_env, monkeypatch):
    _ready_console_machine(tmp_path, monkeypatch)
    _write_fire(tmp_path, created_at="2026-07-23T11:56:00Z", statuses=("HELD",) * 13, unhealthy=range(7))
    status, text = _text(tmp_path)
    assert live_readiness.readiness_state(status)["blocking"] == ["market_data_ready:MAJORITY_DATA_HEALTH"]
    assert text.splitlines()[1] == "LIVE ENTRY POSSIBLE: NO - for most contexts; a minority may still enter"
    assert _verdict(text) == ("LIVE ENTRY POSSIBLE: NO - blocked by market_data_ready (MAJORITY_DATA_HEALTH); "
                              "a minority of contexts may still enter")


def _write_gate_record(root, created_at, status="HELD"):
    from runtime.mvp_runtime.store import LEDGER_REL, RECORDS_FILE

    ledger = root / LEDGER_REL
    ledger.mkdir(parents=True, exist_ok=True)
    (ledger / RECORDS_FILE).write_text(json.dumps({"kind": "crypto_cycle", "record": {
        "live_route_status": status, "created_at": created_at}}) + "\n", encoding="utf-8")


@pytest.mark.parametrize("created_at, kind", [
    (THREE_HOURS_AGO, "is over two hours old"),
    ("2026-07-23T12:30:00Z", "is dated ahead of this clock"),     # 30 minutes AHEAD of NOW
    ("not-a-timestamp", "cannot be dated"),
], ids=["old", "future", "undatable"])
def test_an_old_record_is_named_by_its_kind_at_both_places_it_is_quoted(tmp_path, clean_env, created_at, kind):
    """Every record the gate's two-hour rule sets aside was called "over two hours old" — one dated
    half an hour ahead of this clock, and one with no date at all, among them."""
    _write_gate_record(tmp_path, created_at)
    status, text = _text(tmp_path)
    assert status["recorded_gate"]["stale"] is True
    lines = text.splitlines()
    banner = lines.index("!! THIS PROCESS CANNOT SEE THE LIVE-TRADING ENVIRONMENT")
    assert lines[banner + 1:banner + 3] == [f"   the trading process's last record ({created_at})",
                                            f"   {kind} - not a statement about now"]
    verdict = lines.index("THIS PROCESS: NOT READY (THIS PROCESS ONLY) - no live-trading environment here;")
    assert lines[verdict + 1:verdict + 3] == [f"           the system's last record of its gate ({created_at})",
                                              f"           {kind}"]
    assert ("over two hours old" in text) is (kind == "is over two hours old")
    assert "was recorded OPEN" not in text and "no record of" not in text


@pytest.mark.parametrize("record", [FIVE_MINUTES_AGO, THREE_HOURS_AGO, None], ids=["fresh", "old", "none"])
def test_what_the_board_says_about_the_answer_fits_80_columns(tmp_path, clean_env, record):
    """The head, the banner's account of the record and the THIS PROCESS lines fit an 80-column
    console in every banner variant. The verdict is one line however long, by design."""
    if record is not None:
        _write_gate_record(tmp_path, record)
    _, text = _text(tmp_path)
    lines = text.splitlines()
    said = lines[1:lines.index("")]
    banner = lines.index("!! THIS PROCESS CANNOT SEE THE LIVE-TRADING ENVIRONMENT")
    said += lines[banner:lines.index("   authoritative board:")]
    verdict = next(i for i, line in enumerate(lines) if line.startswith("THIS PROCESS:"))
    said += [lines[verdict]] + [line for line in lines[verdict + 1:verdict + 3] if line.startswith(" " * 11)]
    assert all(len(line) <= 80 for line in said), [line for line in said if len(line) > 80]
