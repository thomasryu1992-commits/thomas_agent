"""C5 paper kernel tests — routing, settlement math, the gate, the kill switch.

The R8-pattern gate tests the contract requires: DryRun by default, env alone fails
closed, the real store re-asserts authorization per mutation, the chokepoint refuses
while PAUSED/KILLED, and settlement always runs even when the verdict forbids opening."""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime import control, safety_gate
from runtime.mvp_runtime.control import ControlState, ControlStore
from runtime.mvp_runtime.crypto import paper
from runtime.mvp_runtime.crypto.guards import run_risk_guard
from runtime.mvp_runtime.crypto.paper import (
    BLOCK_DIRECTION_CONFLICT,
    PAPER_ENV,
    PAPER_PROVIDER_ID,
    STATUS_BLOCKED,
    STATUS_ENTRY_CANDIDATE,
    STATUS_NO_ENTRY,
    DryRunPaperStore,
    RealPaperStore,
    build_entry_plan,
    build_outcome_record,
    PositionContext,
    load_open_position,
    open_position,
    read_outcomes,
    route_entries,
    run_paper_update,
    select_paper_store,
    settle_trade_plan,
)
from runtime.mvp_runtime.errors import SafetyGateBlocked, ToolBlocked, ToolError
from runtime.mvp_runtime.safety_gate import FILESYSTEM_WRITE, Authorization, build_activation_record

NOW = "2026-07-22T12:00:00Z"

# The book every fixture in this file trades in.
CTX = PositionContext(venue="binance_futures", symbol="BTCUSDT", timeframe="1d")

_AUTH = Authorization(
    flags=(FILESYSTEM_WRITE,),
    provider_id=PAPER_PROVIDER_ID,
    activation_sha256="sha256:test",
    expires_at="2999-01-01T00:00:00Z",
    evidence_ref=".runtime_governance_state/evidence.md",
)


def _spec_dict(**overrides):
    base = {
        "schema_version": "strategy_spec.v1",
        "strategy_id": "S1",
        "strategy_version": "1.0",
        "strategy_family": "breakout",
        "symbol_scope": ["BTCUSDT"],
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
    base.update(overrides)
    return base


def _pool_entry(spec=None, *, strategy_id="S1", status="PAPER_ACTIVE", champion_score=0.5):
    return {
        "strategy_id": strategy_id,
        "status": status,
        "champion_score": champion_score,
        "strategy_rule_hash": "deadbeef",
        "generation_id": "GEN-001",
        "strategy_spec": spec or _spec_dict(strategy_id=strategy_id),
    }


def _pool(*entries):
    return {"pool_version": "active_strategy_pool.v1", "active_strategies": list(entries)}


ROW = {"timestamp": "2026-07-22T00:00:00Z", "close": 105.0, "ma20": 100.0, "adx": 25.0, "atr": 2.0}
ROW_NO_MATCH = {**ROW, "close": 95.0}


def _snapshot(last_candle=None, symbol="BTCUSDT", timeframe="1d"):
    candle = last_candle or {
        "open_time": "2026-07-21T00:00:00Z", "open": 104.0, "high": 106.0, "low": 103.0,
        "close": 105.0, "volume": 10.0, "close_time": "2026-07-22T00:00:00Z",
    }
    return {"symbol": symbol, "timeframe": timeframe, "candles": [candle]}


# --- routing ------------------------------------------------------------------

def test_route_no_match_is_no_entry():
    route = route_entries(_pool(_pool_entry()), ROW_NO_MATCH, symbol="BTCUSDT", timeframe="1d", now=NOW)
    assert route["status"] == STATUS_NO_ENTRY and route["matched_strategy_ids"] == []


def test_route_match_selects_primary_and_supporting():
    weak = _pool_entry(strategy_id="S_weak", champion_score=0.1)
    strong = _pool_entry(strategy_id="S_strong", champion_score=0.9)
    route = route_entries(_pool(weak, strong), ROW, symbol="BTCUSDT", timeframe="1d", now=NOW)
    assert route["status"] == STATUS_ENTRY_CANDIDATE
    assert route["primary_strategy_id"] == "S_strong"  # strongest champion wins
    assert route["supporting_strategy_ids"] == ["S_weak"]  # one order, not N — attribution kept
    assert route["direction"] == "LONG"


def test_route_direction_conflict_fails_closed():
    long_e = _pool_entry(strategy_id="S_long")
    short_spec = _spec_dict(strategy_id="S_short", direction="short", entry_rules={
        "operator": "AND", "conditions": [{"feature": "adx", "comparison": ">=", "value": 20.0}],
    })
    short_e = _pool_entry(spec=short_spec, strategy_id="S_short")
    route = route_entries(_pool(long_e, short_e), ROW, symbol="BTCUSDT", timeframe="1d", now=NOW)
    assert route["status"] == STATUS_BLOCKED
    assert route["block_reason"] == BLOCK_DIRECTION_CONFLICT


@pytest.mark.parametrize("status", ["SUSPENDED", "ARCHIVED", "GENERATED"])
def test_route_non_occupying_statuses_cannot_enter(status):
    route = route_entries(_pool(_pool_entry(status=status)), ROW, symbol="BTCUSDT", timeframe="1d", now=NOW)
    assert route["status"] == STATUS_NO_ENTRY and route["strategies_evaluated"] == 0


def test_route_wrong_symbol_or_timeframe_is_unevaluable():
    route = route_entries(_pool(_pool_entry()), ROW, symbol="ETHUSDT", timeframe="1d", now=NOW)
    assert route["status"] == STATUS_NO_ENTRY
    assert "unevaluable" in route["evaluations"][0]


def test_route_matches_any_symbol_in_scope_not_just_primary():
    # A strategy scoped to two symbols is evaluable on EACH — including the one that
    # is not symbol_scope[0], which the old primary-only router left starved.
    multi = _pool_entry(spec=_spec_dict(symbol_scope=["BTCUSDT", "ETHUSDT"]))
    btc = route_entries(_pool(multi), ROW, symbol="BTCUSDT", timeframe="1d", now=NOW)
    eth = route_entries(_pool(multi), ROW, symbol="ETHUSDT", timeframe="1d", now=NOW)
    assert btc["status"] == STATUS_ENTRY_CANDIDATE and eth["status"] == STATUS_ENTRY_CANDIDATE
    # A symbol outside the scope is still unevaluable.
    sol = route_entries(_pool(multi), ROW, symbol="SOLUSDT", timeframe="1d", now=NOW)
    assert sol["status"] == STATUS_NO_ENTRY


def test_multi_symbol_plan_books_under_the_traded_symbol():
    # On its non-primary symbol the plan must book ETHUSDT, not symbol_scope[0] (BTC).
    multi = _pool_entry(spec=_spec_dict(symbol_scope=["BTCUSDT", "ETHUSDT"]))
    route = route_entries(_pool(multi), ROW, symbol="ETHUSDT", timeframe="1d", now=NOW)
    plan = build_entry_plan(route, ROW, now=NOW)
    assert plan["symbol"] == "ETHUSDT" and plan["timeframe"] == "1d"
    assert open_position(plan, now=NOW)["symbol"] == "ETHUSDT"


# --- entry plan + position ----------------------------------------------------

def _candidate_route(row=ROW):
    return route_entries(_pool(_pool_entry()), row, symbol="BTCUSDT", timeframe="1d", now=NOW)


def test_entry_plan_long_math():
    plan = build_entry_plan(_candidate_route(), ROW, now=NOW)
    assert plan["entry_price"] == 105.0
    assert plan["stop_loss"] == 105.0 - 1.5 * 2.0  # entry - stop_atr * atr
    assert plan["take_profit"] == 105.0 + 2.0 * 2.0  # entry + target_atr * atr
    assert plan["risk"] == 3.0
    assert plan["strategy_id"] == "S1" and plan["symbol"] == "BTCUSDT"


def test_entry_plan_short_math():
    spec = _spec_dict(direction="short", entry_rules={
        "operator": "AND", "conditions": [{"feature": "adx", "comparison": ">=", "value": 20.0}],
    })
    route = route_entries(_pool(_pool_entry(spec=spec)), ROW, symbol="BTCUSDT", timeframe="1d", now=NOW)
    plan = build_entry_plan(route, ROW, now=NOW)
    assert plan["direction"] == "SHORT"
    assert plan["stop_loss"] == 105.0 + 3.0 and plan["take_profit"] == 105.0 - 4.0


def test_entry_plan_refuses_indeterminate_atr():
    row = {**ROW, "atr": None}
    route = route_entries(_pool(_pool_entry()), row, symbol="BTCUSDT", timeframe="1d", now=NOW)
    assert build_entry_plan(route, row, now=NOW) is None  # never price a plan on unwarmed data


def test_entry_plan_none_for_no_entry_route():
    assert build_entry_plan(
        route_entries(_pool(_pool_entry()), ROW_NO_MATCH, symbol="BTCUSDT", timeframe="1d", now=NOW),
        ROW_NO_MATCH, now=NOW,
    ) is None


def test_open_position_shape_and_deterministic_id():
    plan = build_entry_plan(_candidate_route(), ROW, now=NOW)
    a, b = open_position(plan, now=NOW), open_position(plan, now=NOW)
    assert a == b  # deterministic id from a seeded short_id, per the repo rule
    assert a["status"] == "OPEN" and a["holding_candles"] == 0
    assert a["intrabar_policy"] == "pessimistic_sl_first"


def test_lineage_flows_route_to_plan_to_position_to_outcome():
    # The chain the mid-review found broken: candidate_id stopped at the pool.
    entry = {**_pool_entry(), "candidate_id": "cand_abc123"}
    route = route_entries(_pool(entry), ROW, symbol="BTCUSDT", timeframe="1d", now=NOW)
    assert route["primary_candidate_id"] == "cand_abc123"
    plan = build_entry_plan(route, ROW, now=NOW)
    assert plan["candidate_id"] == "cand_abc123"
    position = open_position(plan, now=NOW)
    assert position["candidate_id"] == "cand_abc123"
    outcome = build_outcome_record(position, "stop_loss", 102.0, -1.0, now=NOW)
    assert outcome["candidate_id"] == "cand_abc123"


def test_plan_and_position_carry_the_spec_time_exit():
    # Exit parity: the spec was backtested with max_holding_bars=10, so the plan and
    # the position must carry exactly that — never the timeframe table.
    plan = build_entry_plan(_candidate_route(), ROW, now=NOW)
    assert plan["max_holding_bars"] == 10
    assert open_position(plan, now=NOW)["max_holding_bars"] == 10


@pytest.mark.parametrize("stored,expected,legacy", [
    (12, 12, False),           # the spec's own value wins
    (None, 48, True),          # legacy position, 1d -> table default
    (0, 48, True),             # non-positive can never be a real limit
    (True, 48, True),          # bool is not a bar count
])
def test_position_max_hold_prefers_the_position_value(stored, expected, legacy):
    position = {"max_holding_bars": stored} if stored is not None else {}
    assert paper.position_max_hold(position, "1d") == (expected, legacy)


# --- settlement (source math) -------------------------------------------------

def _position(**overrides):
    base = open_position(build_entry_plan(_candidate_route(), ROW, now=NOW), now=NOW)
    base.update(overrides)
    return base  # entry 105, sl 102, tp 109, risk 3


def _candle(high, low, close, ts="2026-07-23T00:00:00Z"):
    return {"high": high, "low": low, "close": close, "close_time": ts}


def test_settle_stop_loss_is_minus_one_r():
    reason, exit_price, r = settle_trade_plan(_position(), _candle(104.0, 101.0, 103.0), 103.0, 48, False)
    assert (reason, exit_price, r) == ("stop_loss", 102.0, -1.0)


def test_settle_take_profit_r():
    reason, exit_price, r = settle_trade_plan(_position(), _candle(110.0, 104.0, 109.5), 109.5, 48, False)
    assert (reason, exit_price) == ("take_profit", 109.0)
    assert r == (109.0 - 105.0) / 3.0


def test_settle_pessimistic_sl_first_when_both_hit():
    reason, exit_price, r = settle_trade_plan(_position(), _candle(110.0, 101.0, 108.0), 108.0, 48, False)
    assert (reason, exit_price, r) == ("stop_loss", 102.0, -1.0)


def test_settle_manual_exit_takes_precedence():
    reason, exit_price, r = settle_trade_plan(_position(), _candle(110.0, 101.0, 106.0), 106.0, 48, True)
    assert reason == "manual_exit" and exit_price == 106.0
    assert r == (106.0 - 105.0) / 3.0


def test_settle_time_exit_at_max_hold():
    position = _position(holding_candles=47)
    reason, exit_price, r = settle_trade_plan(position, _candle(106.0, 104.0, 105.9), 105.9, 48, False)
    assert reason == "time_exit" and exit_price == 105.9


def test_settle_still_open_returns_none_and_advances_holding():
    position = _position()
    result = settle_trade_plan(position, _candle(106.0, 104.0, 105.5), 105.5, 48, False)
    assert result == (None, None, None)
    assert position["holding_candles"] == 1


def test_settle_same_candle_counts_once():
    position = _position()
    candle = _candle(106.0, 104.0, 105.5)
    settle_trade_plan(position, candle, 105.5, 48, False)
    settle_trade_plan(position, candle, 105.5, 48, False)  # same close_time re-run
    assert position["holding_candles"] == 1  # a re-run cannot accelerate time_exit


def test_short_settlement_mirrors():
    position = _position(direction="SHORT", stop_loss=108.0, take_profit=101.0)
    reason, exit_price, r = settle_trade_plan(position, _candle(107.0, 100.5, 101.2), 101.2, 48, False)
    assert (reason, exit_price) == ("take_profit", 101.0)
    assert r == (105.0 - 101.0) / 3.0


def test_outcome_record_feeds_the_risk_guard():
    outcome = build_outcome_record(_position(), "stop_loss", 102.0, -1.0, now=NOW)
    assert outcome["outcome_closed"] is True
    # The gross number is still the stop's exact -1R; result_R is that MINUS the two legs'
    # costs, so a stopped-out trade loses more than the risk it planned — which is the point.
    assert outcome["gross_result_R"] == -1.0
    assert outcome["result_R"] < -1.0
    assert outcome["result_R"] == round(-1.0 - outcome["fee_cost_r"] - outcome["slippage_cost_r"], 8)
    assert outcome["win_loss"] == "LOSS" and outcome["record_sha256"].startswith("sha256:")
    verdict = run_risk_guard([outcome], now=NOW)  # C4 reads C5 records natively
    assert verdict["consecutive_losses"] == 1


# --- state reads fail closed --------------------------------------------------

def test_read_outcomes_missing_is_empty(tmp_path):
    assert read_outcomes(tmp_path) == []


def test_read_outcomes_corrupt_line_raises(tmp_path):
    path = paper.state_dir(tmp_path)
    path.mkdir(parents=True)
    (path / "paper_outcomes.jsonl").write_text('{"ok": true}\n{broken\n', encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        read_outcomes(tmp_path)
    assert exc.value.reason_code == "OUTCOME_HISTORY_UNREADABLE"


def test_read_outcomes_tampered_native_record_raises(tmp_path):
    path = paper.state_dir(tmp_path)
    path.mkdir(parents=True)
    outcome = build_outcome_record(_position(), "stop_loss", 102.0, -1.0, now=NOW)
    tampered = {**outcome, "result_R": 5.0}  # edited AFTER the self-hash was minted
    (path / "paper_outcomes.jsonl").write_text(
        json.dumps(tampered) + "\n", encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        read_outcomes(tmp_path)
    assert exc.value.reason_code == "OUTCOME_HISTORY_TAMPERED"


def test_read_outcomes_native_roundtrip_verifies(tmp_path):
    path = paper.state_dir(tmp_path)
    path.mkdir(parents=True)
    outcome = build_outcome_record(_position(), "stop_loss", 102.0, -1.0, now=NOW)
    (path / "paper_outcomes.jsonl").write_text(json.dumps(outcome) + "\n", encoding="utf-8")
    assert read_outcomes(tmp_path)[0]["outcome_id"] == outcome["outcome_id"]


@pytest.mark.parametrize("field", ["outcome_id", "settlement_id"])
def test_read_outcomes_duplicate_ids_raise(field, tmp_path):
    path = paper.state_dir(tmp_path)
    path.mkdir(parents=True)
    # No provenance => no self-hash check; the duplicate id alone must trip.
    record = {"outcome_closed": True, "result_R": 1.0, field: "dup_1"}
    (path / "paper_outcomes.jsonl").write_text(
        json.dumps(record) + "\n" + json.dumps(record) + "\n", encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        read_outcomes(tmp_path)
    assert exc.value.reason_code == "OUTCOME_HISTORY_DUPLICATE"


def test_read_outcomes_imported_records_skip_hash_recompute(tmp_path):
    # Imported rows carry the SOURCE's hash over pre-import fields; recomputing it
    # here would poison every import. Their tamper evidence is the audited batch.
    path = paper.state_dir(tmp_path)
    path.mkdir(parents=True)
    imported = {"outcome_id": "orig_1", "outcome_closed": True, "result_R": 1.5,
                "provenance": "crypto_ai_system_import", "record_sha256": "sha256:source-scheme"}
    (path / "paper_outcomes.jsonl").write_text(json.dumps(imported) + "\n", encoding="utf-8")
    assert read_outcomes(tmp_path) == [imported]


def test_load_position_missing_is_none(tmp_path):
    assert load_open_position(CTX, tmp_path) is None


def test_load_position_corrupt_raises(tmp_path):
    path = paper.state_dir(tmp_path)
    path.mkdir(parents=True)
    (path / "paper_position.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        load_open_position(CTX, tmp_path)
    assert exc.value.reason_code == "POSITION_STATE_UNREADABLE"


# --- the gate -----------------------------------------------------------------

def test_select_store_defaults_to_dry_run(monkeypatch):
    monkeypatch.delenv(PAPER_ENV, raising=False)
    assert isinstance(select_paper_store(), DryRunPaperStore)


def test_env_alone_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv(PAPER_ENV, "real")
    with pytest.raises(SafetyGateBlocked) as exc:
        select_paper_store(now=NOW, root=tmp_path)
    assert exc.value.reason_code == "ACTIVATION_MISSING"


def test_activation_enables_real_store(monkeypatch, tmp_path):
    (tmp_path / ".runtime_governance_state").mkdir()
    evidence_rel = ".runtime_governance_state/paper_gate_approval.md"
    (tmp_path / evidence_rel).write_text("operator decision evidence", encoding="utf-8")
    record = build_activation_record(
        flags=[FILESYSTEM_WRITE], provider_id=PAPER_PROVIDER_ID,
        activated_at="2026-07-01T00:00:00Z", expires_at="2026-12-31T23:59:59Z",
        evidence_ref=evidence_rel, authority_level="P1",
    )
    path = safety_gate.activation_path(tmp_path, PAPER_PROVIDER_ID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setenv(PAPER_ENV, "real")
    assert isinstance(select_paper_store(now=NOW, root=tmp_path), RealPaperStore)


def test_directly_constructed_real_store_cannot_mutate(tmp_path):
    with pytest.raises(SafetyGateBlocked) as exc:
        RealPaperStore(root=tmp_path).save_position({"status": "OPEN"})
    assert exc.value.reason_code == "NOT_AUTHORIZED"


# --- run_paper_update chokepoint ----------------------------------------------

def _verdict(allow=True):
    return {"allow_new_position": allow}


@pytest.mark.parametrize("mode", [control.PAUSED, control.KILLED])
def test_update_refused_while_not_active(mode, tmp_path):
    store = ControlStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        json.dumps(ControlState(mode=mode, updated_by="op", updated_at=NOW, reason="test").as_record()),
        encoding="utf-8",
    )
    with pytest.raises(ToolBlocked) as exc:
        run_paper_update(_snapshot(), ROW, _pool(_pool_entry()), _verdict(),
                         store=DryRunPaperStore(), now=NOW, root=tmp_path, control_store=store)
    assert exc.value.reason_code == ("RUNTIME_KILLED" if mode == control.KILLED else "RUNTIME_PAUSED")


def test_dry_run_opens_nothing_durable(tmp_path):
    summary, records = run_paper_update(
        _snapshot(), ROW, _pool(_pool_entry()), _verdict(),
        store=DryRunPaperStore(), now=NOW, root=tmp_path, control_store=ControlStore(tmp_path),
    )
    assert summary["opened"] is not None  # the full path ran...
    assert load_open_position(CTX, tmp_path) is None  # ...but nothing persisted
    assert records and all(r["filesystem_write"] is False for r in records)


def test_no_trade_verdict_skips_open(tmp_path):
    summary, records = run_paper_update(
        _snapshot(), ROW, _pool(_pool_entry()), _verdict(allow=False),
        store=DryRunPaperStore(), now=NOW, root=tmp_path, control_store=ControlStore(tmp_path),
    )
    assert summary["opened"] is None and summary["route_status"] == STATUS_ENTRY_CANDIDATE
    assert records == []


def _real_store(tmp_path):
    return RealPaperStore(root=tmp_path, authorization=_AUTH)


def test_real_open_then_settle_cycle(tmp_path):
    control_store = ControlStore(tmp_path)
    summary, records = run_paper_update(
        _snapshot(), ROW, _pool(_pool_entry()), _verdict(),
        store=_real_store(tmp_path), now=NOW, root=tmp_path, control_store=control_store,
    )
    assert summary["opened"] is not None
    opened = load_open_position(CTX, tmp_path)
    assert opened is not None and opened["entry_price"] == 105.0

    # Next cycle: the candle trades through the stop (102) — settle to -1R.
    sl_candle = {"open_time": "2026-07-22T00:00:00Z", "open": 104.0, "high": 104.5, "low": 101.0,
                 "close": 103.0, "volume": 9.0, "close_time": "2026-07-23T00:00:00Z"}
    summary2, records2 = run_paper_update(
        _snapshot(sl_candle), ROW_NO_MATCH, _pool(_pool_entry()), _verdict(),
        store=_real_store(tmp_path), now="2026-07-23T12:00:00Z", root=tmp_path, control_store=control_store,
    )
    assert summary2["settled"]["close_reason"] == "stop_loss"
    assert load_open_position(CTX, tmp_path) is None
    outcomes = read_outcomes(tmp_path)
    assert len(outcomes) == 1
    assert outcomes[0]["gross_result_R"] == -1.0 and outcomes[0]["result_R"] < -1.0
    assert records2[0]["filesystem_write"] is True


def test_single_position_no_double_open(tmp_path):
    control_store = ControlStore(tmp_path)
    run_paper_update(_snapshot(), ROW, _pool(_pool_entry()), _verdict(),
                     store=_real_store(tmp_path), now=NOW, root=tmp_path, control_store=control_store)
    first = load_open_position(CTX, tmp_path)
    # Second cycle still matches but must not double the position (one open book).
    calm = {"open_time": "2026-07-22T00:00:00Z", "open": 105.0, "high": 106.0, "low": 104.0,
            "close": 105.5, "volume": 9.0, "close_time": "2026-07-23T00:00:00Z"}
    summary, _ = run_paper_update(
        _snapshot(calm), ROW, _pool(_pool_entry()), _verdict(),
        store=_real_store(tmp_path), now="2026-07-23T12:00:00Z", root=tmp_path, control_store=control_store,
    )
    assert summary["opened"] is None
    assert load_open_position(CTX, tmp_path)["position_id"] == first["position_id"]


@pytest.mark.parametrize("symbol,timeframe", [("ETHUSDT", "1d"), ("BTCUSDT", "1h")])
def test_foreign_context_cycle_cannot_touch_another_book(symbol, timeframe, tmp_path):
    """The safety property survives context keying, now structurally.

    Before, a foreign cycle could *reach* the single global position and had to be
    refused; now it reads its own book and the other one is simply not there. What
    must still hold: no settlement, no fabricated outcome, no advanced time-exit."""
    control_store = ControlStore(tmp_path)
    run_paper_update(_snapshot(), ROW, _pool(_pool_entry()), _verdict(),
                     store=_real_store(tmp_path), now=NOW, root=tmp_path, control_store=control_store)
    opened = load_open_position(CTX, tmp_path)
    # A foreign cycle whose candle trades through the BTC stop must not settle it.
    sl_candle = {"open_time": "2026-07-22T00:00:00Z", "open": 104.0, "high": 104.5, "low": 101.0,
                 "close": 103.0, "volume": 9.0, "close_time": "2026-07-23T00:00:00Z"}
    summary, _ = run_paper_update(
        _snapshot(sl_candle, symbol=symbol, timeframe=timeframe), ROW, _pool(_pool_entry()), _verdict(),
        store=_real_store(tmp_path), now="2026-07-23T12:00:00Z", root=tmp_path, control_store=control_store,
    )
    assert summary["settled"] is None
    assert summary["settle_refused"] is None  # nothing to refuse: a separate book
    assert read_outcomes(tmp_path) == []  # no outcome fabricated from foreign candles
    untouched = load_open_position(CTX, tmp_path)
    assert untouched["position_id"] == opened["position_id"]
    # Foreign candles must not advance time_exit either.
    assert untouched["holding_candles"] == opened["holding_candles"]


def _eth_entry():
    spec = _spec_dict(strategy_id="S2", symbol_scope=["ETHUSDT"])
    return _pool_entry(spec, strategy_id="S2")


def test_two_symbols_hold_positions_at_the_same_time(tmp_path):
    """The point of context keying: an occupied BTC book no longer blocks ETH."""
    control_store = ControlStore(tmp_path)
    pool = _pool(_pool_entry(), _eth_entry())
    run_paper_update(_snapshot(), ROW, pool, _verdict(), store=_real_store(tmp_path),
                     now=NOW, root=tmp_path, control_store=control_store)
    summary, _ = run_paper_update(
        _snapshot(symbol="ETHUSDT"), ROW, pool, _verdict(), store=_real_store(tmp_path),
        now=NOW, root=tmp_path, control_store=control_store,
    )
    assert summary["opened"] is not None  # would have been blocked by the old single slot
    eth = paper.PositionContext(venue="binance_futures", symbol="ETHUSDT", timeframe="1d")
    assert load_open_position(eth, tmp_path)["symbol"] == "ETHUSDT"
    assert load_open_position(CTX, tmp_path)["symbol"] == "BTCUSDT"
    assert len(paper.list_open_positions(tmp_path)) == 2


def test_portfolio_cap_refuses_the_third_book(tmp_path, monkeypatch):
    # Pin the ceiling so this stays a test of the cap MECHANISM, not of whatever the
    # production number currently is (the per-symbol test does the same).
    monkeypatch.setattr(paper, "MAX_CONCURRENT_POSITIONS", 2)
    control_store = ControlStore(tmp_path)
    sol = _pool_entry(_spec_dict(strategy_id="S3", symbol_scope=["SOLUSDT"]), strategy_id="S3")
    pool = _pool(_pool_entry(), _eth_entry(), sol)
    for symbol in ("BTCUSDT", "ETHUSDT"):
        run_paper_update(_snapshot(symbol=symbol), ROW, pool, _verdict(), store=_real_store(tmp_path),
                         now=NOW, root=tmp_path, control_store=control_store)
    assert len(paper.list_open_positions(tmp_path)) == paper.MAX_CONCURRENT_POSITIONS
    summary, records = run_paper_update(
        _snapshot(symbol="SOLUSDT"), ROW, pool, _verdict(), store=_real_store(tmp_path),
        now=NOW, root=tmp_path, control_store=control_store,
    )
    assert summary["opened"] is None
    assert summary["open_refused"]["reason_code"] == "POSITION_LIMIT_PORTFOLIO"
    assert summary["open_refused"]["limit"] == paper.MAX_CONCURRENT_POSITIONS
    assert records[-1]["operation"] == "open_refused" and records[-1]["read_only"] is True
    assert len(paper.list_open_positions(tmp_path)) == paper.MAX_CONCURRENT_POSITIONS


def test_per_symbol_cap_refuses_a_third_timeframe_on_one_symbol(tmp_path, monkeypatch):
    # Raise the portfolio ceiling so the per-symbol cap is the binding one, and pin the
    # per-symbol cap itself so this stays a test of the cap MECHANISM, not of whatever
    # the production number currently is (the portfolio test does the same).
    monkeypatch.setattr(paper, "MAX_CONCURRENT_POSITIONS", 9)
    monkeypatch.setattr(paper, "MAX_POSITIONS_PER_SYMBOL", 2)
    control_store = ControlStore(tmp_path)
    pool = _pool(*[_pool_entry(_spec_dict(strategy_id=f"S{i}", timeframe=tf), strategy_id=f"S{i}")
                   for i, tf in enumerate(("1d", "4h", "1h"), start=1)])
    for timeframe in ("1d", "4h"):
        run_paper_update(_snapshot(timeframe=timeframe), ROW, pool, _verdict(), store=_real_store(tmp_path),
                         now=NOW, root=tmp_path, control_store=control_store)
    assert len(paper.list_open_positions(tmp_path)) == paper.MAX_POSITIONS_PER_SYMBOL
    summary, _ = run_paper_update(
        _snapshot(timeframe="1h"), ROW, pool, _verdict(), store=_real_store(tmp_path),
        now=NOW, root=tmp_path, control_store=control_store,
    )
    assert summary["opened"] is None
    assert summary["open_refused"]["reason_code"] == "POSITION_LIMIT_SYMBOL"
    assert summary["open_refused"]["symbol"] == "BTCUSDT"


# --- the third cap: the book's directional SHAPE, not its size ------------------
#
# The two caps above bound how many positions exist and how many share a symbol. Neither
# bounds how many point the same way, so twenty simultaneous longs were permitted — the F1
# hazard in docs/TRADING_STRATEGY_REVIEW_RECORD.md, where one market move becomes twenty
# correlated verdicts about twenty different strategies.

def _book(longs, shorts, *, unknown=0):
    """An `open_positions`-shaped list — only `direction` is read."""
    return [
        (None, {"direction": d})
        for d in (["LONG"] * longs + ["SHORT"] * shorts + [None] * unknown)
    ]


def test_the_skew_cap_is_derived_from_the_per_symbol_cap():
    """Not a chosen number: the rule is "the net directional bet may never exceed one symbol's
    full allocation", which is why it has an English statement with no constant in it."""
    assert paper.MAX_DIRECTIONAL_SKEW == paper.MAX_POSITIONS_PER_SYMBOL


def test_the_skew_cap_is_not_a_concurrency_cap_in_disguise():
    """The property that makes the derived value the right one. A full book is still reachable —
    12 long + 8 short is net 4 at the cap — so what is forbidden is a full book that is ONE-WAY,
    not a full book. If this ever failed, the cap would be silently shrinking the portfolio."""
    longs = (paper.MAX_CONCURRENT_POSITIONS + paper.MAX_DIRECTIONAL_SKEW) // 2
    shorts = paper.MAX_CONCURRENT_POSITIONS - longs
    admitted, refusal = paper.directional_skew_admits(_book(longs - 1, shorts), "LONG")
    assert admitted and refusal is None
    assert longs + shorts == paper.MAX_CONCURRENT_POSITIONS


def test_an_empty_book_admits_up_to_the_cap_then_declines():
    for aligned in range(paper.MAX_DIRECTIONAL_SKEW):
        admitted, _ = paper.directional_skew_admits(_book(aligned, 0), "LONG")
        assert admitted, aligned
    admitted, refusal = paper.directional_skew_admits(_book(paper.MAX_DIRECTIONAL_SKEW, 0), "LONG")
    assert not admitted
    assert refusal["reason_code"] == "POSITION_LIMIT_DIRECTIONAL_SKEW"
    assert refusal["lean_after"] == paper.MAX_DIRECTIONAL_SKEW + 1
    assert refusal["limit"] == paper.MAX_DIRECTIONAL_SKEW


def test_an_opposing_position_buys_back_a_slot():
    """The mechanism by which a book grows past the cap at all: the lean is what is bounded, so
    a hedged pair costs nothing. Without this the cap WOULD be a concurrency cap."""
    at_cap = _book(paper.MAX_DIRECTIONAL_SKEW, 0)
    assert paper.directional_skew_admits(at_cap, "LONG")[0] is False
    assert paper.directional_skew_admits(at_cap + _book(0, 1), "LONG")[0] is True


def test_the_corrective_side_is_never_declined_even_past_the_cap():
    """**The pathology the sign convention exists to prevent.** Exits are not synchronised, so a
    balanced book becomes one-way on its own when the opposing side times out. Written as
    `abs(new_skew) > cap` the rule then refuses the very trades that would rebalance it — and it
    looks equivalent. A book leaning twice the cap must still accept the opposite direction."""
    lopsided = _book(2 * paper.MAX_DIRECTIONAL_SKEW, 0)
    assert paper.directional_skew_admits(lopsided, "LONG")[0] is False
    admitted, refusal = paper.directional_skew_admits(lopsided, "SHORT")
    assert admitted and refusal is None


def test_the_cap_is_symmetric_between_the_two_directions():
    at_cap_short = _book(0, paper.MAX_DIRECTIONAL_SKEW)
    assert paper.directional_skew_admits(at_cap_short, "SHORT")[0] is False
    assert paper.directional_skew_admits(at_cap_short, "LONG")[0] is True


def test_an_unreadable_direction_makes_the_cap_bind_sooner_not_later():
    """`opposing` is what is measured, so anything not provably opposite counts as aligned — no
    special case, and the failure direction is the conservative one. `list_open_positions` fails
    the same way when it cannot attribute a position at all."""
    admitted, refusal = paper.directional_skew_admits(
        _book(paper.MAX_DIRECTIONAL_SKEW - 1, 0, unknown=1), "LONG"
    )
    assert not admitted
    assert refusal["aligned"] == paper.MAX_DIRECTIONAL_SKEW
    assert refusal["opposing"] == 0


def test_a_proposal_with_no_usable_direction_is_not_declined_on_a_guess():
    """There is nothing to count toward, so inventing a lean would decline on a guess. The plan
    builder cannot produce this; a caller passing None can."""
    assert paper.directional_skew_admits(_book(9, 0), None)[0] is True
    assert paper.directional_skew_admits(_book(9, 0), "")[0] is True


def test_the_skew_cap_refuses_a_real_open_and_writes_nothing(tmp_path, monkeypatch):
    """End to end through the portfolio lock, with the other two caps raised out of the way so
    this is the binding one."""
    monkeypatch.setattr(paper, "MAX_CONCURRENT_POSITIONS", 9)
    monkeypatch.setattr(paper, "MAX_POSITIONS_PER_SYMBOL", 9)
    monkeypatch.setattr(paper, "MAX_DIRECTIONAL_SKEW", 2)
    control_store = ControlStore(tmp_path)
    symbols = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    pool = _pool(*[
        _pool_entry(_spec_dict(strategy_id=f"S{i}", symbol_scope=[s]), strategy_id=f"S{i}")
        for i, s in enumerate(symbols, start=1)
    ])
    for symbol in symbols[:2]:
        run_paper_update(_snapshot(symbol=symbol), ROW, pool, _verdict(), store=_real_store(tmp_path),
                         now=NOW, root=tmp_path, control_store=control_store)
    assert len(paper.list_open_positions(tmp_path)) == 2
    summary, records = run_paper_update(
        _snapshot(symbol=symbols[2]), ROW, pool, _verdict(), store=_real_store(tmp_path),
        now=NOW, root=tmp_path, control_store=control_store,
    )
    assert summary["opened"] is None
    assert summary["open_refused"]["reason_code"] == "POSITION_LIMIT_DIRECTIONAL_SKEW"
    assert summary["open_refused"]["direction"] == "LONG"
    assert summary["open_refused"]["aligned"] == 2 and summary["open_refused"]["opposing"] == 0
    assert records[-1]["operation"] == "open_refused" and records[-1]["read_only"] is True
    assert len(paper.list_open_positions(tmp_path)) == 2, "a refused open must write nothing"


def test_the_skew_cap_still_admits_the_short_that_rebalances_a_real_book(tmp_path, monkeypatch):
    """The corrective case through the real store, not just the pure function: a book at the cap
    must still be able to hedge, or the cap becomes a hard stop on the whole fan-out."""
    monkeypatch.setattr(paper, "MAX_CONCURRENT_POSITIONS", 9)
    monkeypatch.setattr(paper, "MAX_POSITIONS_PER_SYMBOL", 9)
    monkeypatch.setattr(paper, "MAX_DIRECTIONAL_SKEW", 2)
    control_store = ControlStore(tmp_path)
    short_spec = _spec_dict(
        strategy_id="S_short", symbol_scope=["SOLUSDT"], direction="short",
        entry_rules={"operator": "AND", "conditions": [
            {"feature": "close", "comparison": ">", "value_from": "ma20"},
        ]},
    )
    pool = _pool(
        _pool_entry(_spec_dict(strategy_id="S1", symbol_scope=["BTCUSDT"]), strategy_id="S1"),
        _pool_entry(_spec_dict(strategy_id="S2", symbol_scope=["ETHUSDT"]), strategy_id="S2"),
        _pool_entry(short_spec, strategy_id="S_short"),
    )
    for symbol in ("BTCUSDT", "ETHUSDT"):
        run_paper_update(_snapshot(symbol=symbol), ROW, pool, _verdict(), store=_real_store(tmp_path),
                         now=NOW, root=tmp_path, control_store=control_store)
    summary, _ = run_paper_update(
        _snapshot(symbol="SOLUSDT"), ROW, pool, _verdict(), store=_real_store(tmp_path),
        now=NOW, root=tmp_path, control_store=control_store,
    )
    assert summary.get("open_refused") is None
    assert summary["opened"]["direction"] == "SHORT"


def test_the_live_book_is_deliberately_untouched():
    """Scoped to paper on purpose, and the reason is that the authority already exists there:
    live holds at most `MAX_LIVE_CONCURRENT_POSITIONS` positions, so its largest possible skew
    is that number — a cap is either inert or halves an already-tiny capacity — and live one-way
    risk is governed by the operator-registered `max_open_notional_usdt`."""
    from runtime.mvp_runtime.crypto import live_position

    # A tripwire, not a tautology: while live concurrency stays at or under the paper cap, the
    # largest lean live can reach is one this cap would have admitted anyway — so leaving live
    # alone costs nothing. Raise live concurrency past it and this fails, which is exactly when
    # somebody has to decide whether the live book needs its own directional answer.
    assert live_position.MAX_LIVE_CONCURRENT_POSITIONS <= paper.MAX_DIRECTIONAL_SKEW


def test_context_parts_cannot_escape_the_positions_directory(tmp_path):
    with pytest.raises(ToolError) as exc:
        paper.PositionContext(venue="binance_futures", symbol="../../etc", timeframe="1d")
    assert exc.value.reason_code == "POSITION_CONTEXT_MISMATCH"


def test_legacy_position_is_settled_by_its_own_context(tmp_path):
    """A position written before context keying still closes — in its own book."""
    state = paper.state_dir(tmp_path)
    state.mkdir(parents=True)
    (state / "paper_position.json").write_text(json.dumps({
        "status": "OPEN", "position_id": "p-legacy", "symbol": "BTCUSDT", "timeframe": "1d",
        "direction": "LONG", "entry_price": 105.0, "stop_loss": 102.0, "take_profit": 111.0,
        "risk": 3.0, "holding_candles": 0, "max_holding_bars": 10,
    }), encoding="utf-8")
    assert load_open_position(CTX, tmp_path)["position_id"] == "p-legacy"
    sl_candle = {"open_time": "2026-07-22T00:00:00Z", "open": 104.0, "high": 104.5, "low": 101.0,
                 "close": 103.0, "volume": 9.0, "close_time": "2026-07-23T00:00:00Z"}
    summary, _ = run_paper_update(
        _snapshot(sl_candle), ROW, _pool(_pool_entry()), _verdict(allow=False),
        store=_real_store(tmp_path), now="2026-07-23T12:00:00Z", root=tmp_path,
        control_store=ControlStore(tmp_path),
    )
    assert summary["settled"]["position_id"] == "p-legacy"
    # Closed in the legacy file too, or the next cycle would settle it a second time.
    assert load_open_position(CTX, tmp_path) is None
    assert paper.list_open_positions(tmp_path) == []


def test_position_missing_context_fields_refuses_settlement(tmp_path):
    # A legacy/hand-written position without symbol/timeframe can never prove it
    # belongs to this cycle — refused, never settled on a guess.
    path = paper.state_dir(tmp_path)
    path.mkdir(parents=True)
    (path / "paper_position.json").write_text(
        json.dumps({"status": "OPEN", "position_id": "p-legacy", "direction": "LONG",
                    "entry_price": 105.0, "stop_loss": 102.0, "take_profit": 111.0, "risk": 3.0}),
        encoding="utf-8",
    )
    summary, _ = run_paper_update(
        _snapshot(), ROW, _pool(_pool_entry()), _verdict(),
        store=DryRunPaperStore(), now=NOW, root=tmp_path, control_store=ControlStore(tmp_path),
    )
    assert summary["settled"] is None and summary["opened"] is None
    assert summary["settle_refused"]["reason_code"] == "POSITION_CONTEXT_MISMATCH"
    assert summary["settle_refused"]["position_id"] == "p-legacy"


def test_matching_context_still_settles(tmp_path):
    # The guard must not break the normal single-context path (regression pin).
    control_store = ControlStore(tmp_path)
    run_paper_update(_snapshot(), ROW, _pool(_pool_entry()), _verdict(),
                     store=_real_store(tmp_path), now=NOW, root=tmp_path, control_store=control_store)
    sl_candle = {"open_time": "2026-07-22T00:00:00Z", "open": 104.0, "high": 104.5, "low": 101.0,
                 "close": 103.0, "volume": 9.0, "close_time": "2026-07-23T00:00:00Z"}
    summary, _ = run_paper_update(
        _snapshot(sl_candle), ROW_NO_MATCH, _pool(_pool_entry()), _verdict(),
        store=_real_store(tmp_path), now="2026-07-23T12:00:00Z", root=tmp_path, control_store=control_store,
    )
    assert summary["settle_refused"] is None
    assert summary["settled"]["close_reason"] == "stop_loss"


SL_CANDLE = {"open_time": "2026-07-22T00:00:00Z", "open": 104.0, "high": 104.5, "low": 101.0,
             "close": 103.0, "volume": 9.0, "close_time": "2026-07-23T00:00:00Z"}


def test_settlement_carries_a_position_derived_idempotency_key(tmp_path):
    control_store = ControlStore(tmp_path)
    run_paper_update(_snapshot(), ROW, _pool(_pool_entry()), _verdict(),
                     store=_real_store(tmp_path), now=NOW, root=tmp_path, control_store=control_store)
    opened = load_open_position(CTX, tmp_path)
    summary, records = run_paper_update(
        _snapshot(SL_CANDLE), ROW_NO_MATCH, _pool(_pool_entry()), _verdict(),
        store=_real_store(tmp_path), now="2026-07-23T12:00:00Z", root=tmp_path, control_store=control_store,
    )
    outcome = read_outcomes(tmp_path)[0]
    from runtime.read_only_kernel import integrity
    # settlement_id derives from the position alone: a retry would mint the SAME id.
    assert outcome["settlement_id"] == integrity.short_id(
        "settle", {"position_id": opened["position_id"]})
    assert records[0]["settlement_id"] == outcome["settlement_id"]


def test_crash_between_append_and_clear_recovers_without_second_outcome(tmp_path):
    control_store = ControlStore(tmp_path)
    run_paper_update(_snapshot(), ROW, _pool(_pool_entry()), _verdict(),
                     store=_real_store(tmp_path), now=NOW, root=tmp_path, control_store=control_store)
    opened = load_open_position(CTX, tmp_path)
    # Simulate crash window A: the outcome got durably written, the clear was lost.
    store = _real_store(tmp_path)
    store.append_outcome(paper.build_outcome_record(opened, "stop_loss", 102.0, -1.0, now=NOW))
    assert load_open_position(CTX, tmp_path) is not None  # position still OPEN — the corpse

    summary, records = run_paper_update(
        _snapshot(SL_CANDLE), ROW_NO_MATCH, _pool(_pool_entry()), _verdict(allow=False),
        store=store, now="2026-07-23T12:00:00Z", root=tmp_path, control_store=control_store,
    )
    assert summary["settle_recovered"]["reason_code"] == "SETTLEMENT_ALREADY_RECORDED"
    assert summary["settled"] is None
    assert records[0]["operation"] == "settle_recovered"
    assert len(read_outcomes(tmp_path)) == 1  # never a second outcome for one position
    assert load_open_position(CTX, tmp_path) is None  # the interrupted settlement finished


def test_recovered_slot_can_open_in_the_same_cycle(tmp_path):
    control_store = ControlStore(tmp_path)
    run_paper_update(_snapshot(), ROW, _pool(_pool_entry()), _verdict(),
                     store=_real_store(tmp_path), now=NOW, root=tmp_path, control_store=control_store)
    opened = load_open_position(CTX, tmp_path)
    store = _real_store(tmp_path)
    store.append_outcome(paper.build_outcome_record(opened, "stop_loss", 102.0, -1.0, now=NOW))
    # Matching row again: after recovery the slot is honestly free, so a fresh
    # position may open this very cycle (the settle-then-reopen order).
    calm = {"open_time": "2026-07-22T00:00:00Z", "open": 105.0, "high": 106.0, "low": 104.0,
            "close": 105.0, "volume": 9.0, "close_time": "2026-07-23T00:00:00Z"}
    summary, _ = run_paper_update(
        _snapshot(calm), ROW, _pool(_pool_entry()), _verdict(),
        store=store, now="2026-07-23T12:00:00Z", root=tmp_path, control_store=control_store,
    )
    assert summary["settle_recovered"] is not None
    assert summary["opened"] is not None
    assert load_open_position(CTX, tmp_path)["position_id"] != opened["position_id"]


def test_unreadable_history_refuses_settlement(tmp_path):
    control_store = ControlStore(tmp_path)
    run_paper_update(_snapshot(), ROW, _pool(_pool_entry()), _verdict(),
                     store=_real_store(tmp_path), now=NOW, root=tmp_path, control_store=control_store)
    opened = load_open_position(CTX, tmp_path)
    outcomes_path = paper.state_dir(tmp_path) / "paper_outcomes.jsonl"
    outcomes_path.write_text("{broken\n", encoding="utf-8")
    # The candle sweeps the stop, but not-settled cannot be proven: refuse, touch nothing.
    summary, records = run_paper_update(
        _snapshot(SL_CANDLE), ROW_NO_MATCH, _pool(_pool_entry()), _verdict(),
        store=_real_store(tmp_path), now="2026-07-23T12:00:00Z", root=tmp_path, control_store=control_store,
    )
    assert summary["settled"] is None
    assert summary["settle_refused"]["reason_code"] == "SETTLEMENT_UNVERIFIABLE"
    assert summary["settle_refused"]["cause_reason_code"] == "OUTCOME_HISTORY_UNREADABLE"
    assert summary["opened"] is None  # occupied slot still blocks any open
    assert load_open_position(CTX, tmp_path)["position_id"] == opened["position_id"]


def _calm_candle(n):
    # Never touches stop (102) or target (109); distinct close_time advances holding.
    return {"open_time": f"2026-07-2{n}T00:00:00Z", "open": 105.0, "high": 106.0, "low": 104.0,
            "close": 105.0, "volume": 9.0, "close_time": f"2026-07-2{n + 1}T00:00:00Z"}


def test_settlement_time_exits_on_the_spec_limit_not_the_table(tmp_path):
    """The parity bug: a spec backtested with max_holding_bars=2 used to hold 48 bars
    live (the 1d table default), so promotion evidence and paper behavior diverged."""
    spec = _spec_dict(exit_rules={"stop_model": "atr", "stop_atr": 1.5, "target_atr": 2.0,
                                  "max_holding_bars": 2})
    pool = _pool(_pool_entry(spec=spec))
    control_store = ControlStore(tmp_path)
    run_paper_update(_snapshot(_calm_candle(1)), ROW, pool, _verdict(),
                     store=_real_store(tmp_path), now=NOW, root=tmp_path, control_store=control_store)
    assert load_open_position(CTX, tmp_path)["max_holding_bars"] == 2

    # Two calm bars: bar 1 holds, bar 2 hits the SPEC limit and time-exits.
    run_paper_update(_snapshot(_calm_candle(2)), ROW_NO_MATCH, pool, _verdict(allow=False),
                     store=_real_store(tmp_path), now="2026-07-23T12:00:00Z", root=tmp_path,
                     control_store=control_store)
    summary, records = run_paper_update(
        _snapshot(_calm_candle(3)), ROW_NO_MATCH, pool, _verdict(allow=False),
        store=_real_store(tmp_path), now="2026-07-24T12:00:00Z", root=tmp_path, control_store=control_store,
    )
    assert summary["settled"]["close_reason"] == "time_exit"
    assert records[0]["max_hold"] == 2
    assert "max_hold_fallback" not in records[0]     # the spec value ruled, not the table


def test_legacy_position_settles_on_the_table_and_says_so(tmp_path):
    # A position opened before plans carried max_holding_bars: the table default
    # applies, and the settle event names the fallback so the gap is attributable.
    path = paper.state_dir(tmp_path)
    path.mkdir(parents=True)
    legacy = {"status": "OPEN", "position_id": "p-legacy", "symbol": "BTCUSDT", "timeframe": "1d",
              "direction": "LONG", "entry_price": 105.0, "stop_loss": 102.0, "take_profit": 111.0,
              "risk": 3.0, "holding_candles": 47}
    (path / "paper_position.json").write_text(json.dumps(legacy), encoding="utf-8")
    summary, records = run_paper_update(
        _snapshot(_calm_candle(1)), ROW_NO_MATCH, _pool(_pool_entry()), _verdict(allow=False),
        store=_real_store(tmp_path), now=NOW, root=tmp_path, control_store=ControlStore(tmp_path),
    )
    # holding advanced 47 -> 48 = the 1d table default -> time_exit under the fallback.
    assert summary["settled"]["close_reason"] == "time_exit"
    assert records[0]["max_hold"] == 48
    assert records[0]["max_hold_fallback"] == "LEGACY_POSITION_MAX_HOLD_FALLBACK"


# --- concurrent settlement: the CAS lives inside the lock ---------------------

def _open_one(tmp_path, control_store):
    run_paper_update(_snapshot(), ROW, _pool(_pool_entry()), _verdict(),
                     store=_real_store(tmp_path), now=NOW, root=tmp_path, control_store=control_store)
    return load_open_position(CTX, tmp_path)


def test_a_settler_that_lost_the_race_writes_nothing(tmp_path):
    """The race the chokepoint check cannot close: two settlers both pass
    already_settled() before either takes the lock. The loser must not append."""
    store = _real_store(tmp_path)
    opened = _open_one(tmp_path, ControlStore(tmp_path))
    outcome = paper.build_outcome_record(opened, "stop_loss", 102.0, -1.0, now=NOW)

    # A concurrent settler wins: it settles and clears while we hold a stale read.
    store.settle_position(paper.build_outcome_record(opened, "stop_loss", 102.0, -1.0, now=NOW))
    assert len(read_outcomes(tmp_path)) == 1

    with pytest.raises(ToolError) as exc:
        store.settle_position(outcome)
    assert exc.value.reason_code == "SETTLEMENT_RACE_LOST"
    assert len(read_outcomes(tmp_path)) == 1          # the loser appended nothing


def test_a_settler_whose_position_was_replaced_loses(tmp_path):
    # Winner settled ours AND opened a different position: the slot is occupied, but
    # not by the position our outcome was computed for.
    store = _real_store(tmp_path)
    opened = _open_one(tmp_path, ControlStore(tmp_path))
    stale = paper.build_outcome_record(opened, "stop_loss", 102.0, -1.0, now=NOW)
    store.settle_position(paper.build_outcome_record(opened, "stop_loss", 102.0, -1.0, now=NOW))
    store.save_position({**opened, "position_id": "p-different", "status": "OPEN"})

    with pytest.raises(ToolError) as exc:
        store.settle_position(stale)
    assert exc.value.reason_code == "SETTLEMENT_RACE_LOST"
    assert len(read_outcomes(tmp_path)) == 1
    assert load_open_position(CTX, tmp_path)["position_id"] == "p-different"   # untouched


def test_an_interrupted_settlement_completes_without_doubling(tmp_path):
    # Crash window: the outcome is durable but the clear was lost. Re-settling the
    # SAME position must finish the job (clear) and append nothing new.
    store = _real_store(tmp_path)
    opened = _open_one(tmp_path, ControlStore(tmp_path))
    outcome = paper.build_outcome_record(opened, "stop_loss", 102.0, -1.0, now=NOW)
    store.append_outcome(outcome)                      # append succeeded, clear did not
    assert load_open_position(CTX, tmp_path) is not None

    store.settle_position(outcome)                     # same settlement_id
    assert len(read_outcomes(tmp_path)) == 1           # not doubled
    assert load_open_position(CTX, tmp_path) is None        # and the position is closed


def test_chokepoint_reports_a_lost_race_instead_of_claiming_a_settlement(tmp_path):
    control_store = ControlStore(tmp_path)
    _open_one(tmp_path, control_store)

    class _LosingStore(RealPaperStore):
        def settle_position(self, outcome):
            raise ToolError("SETTLEMENT_RACE_LOST", "another settler won")

    summary, records = run_paper_update(
        _snapshot(SL_CANDLE), ROW, _pool(_pool_entry()), _verdict(),
        store=_LosingStore(root=tmp_path, authorization=_AUTH), now="2026-07-23T12:00:00Z",
        root=tmp_path, control_store=control_store,
    )
    assert summary["settled"] is None
    assert summary["settle_refused"]["reason_code"] == "SETTLEMENT_RACE_LOST"
    assert summary["opened"] is None                   # never fills a slot it lost
    assert records[0]["operation"] == "settle_refused"


def test_settlement_runs_even_under_no_trade_verdict(tmp_path):
    control_store = ControlStore(tmp_path)
    run_paper_update(_snapshot(), ROW, _pool(_pool_entry()), _verdict(),
                     store=_real_store(tmp_path), now=NOW, root=tmp_path, control_store=control_store)
    sl_candle = {"open_time": "2026-07-22T00:00:00Z", "open": 104.0, "high": 104.5, "low": 101.0,
                 "close": 103.0, "volume": 9.0, "close_time": "2026-07-23T00:00:00Z"}
    summary, _ = run_paper_update(
        _snapshot(sl_candle), ROW_NO_MATCH, _pool(_pool_entry()), _verdict(allow=False),
        store=_real_store(tmp_path), now="2026-07-23T12:00:00Z", root=tmp_path, control_store=control_store,
    )
    # Closing is risk-reducing: the verdict forbids OPENING, never settling.
    assert summary["settled"] is not None and summary["opened"] is None


# --- intrabar exit resolution: observe the order instead of assuming it ---------


def _fine(open_time: str, high: float, low: float) -> dict:
    return {"open_time": open_time, "high": high, "low": low, "close": (high + low) / 2}


# The coarse bar touches BOTH levels (position: entry 105, sl 102, tp 109).
_AMBIGUOUS = (110.0, 101.0, 108.0)


def test_intrabar_ambiguous_only_when_both_levels_are_touched():
    assert paper.intrabar_ambiguous(_position(), _candle(*_AMBIGUOUS)) is True
    assert paper.intrabar_ambiguous(_position(), _candle(104.0, 101.0, 103.0)) is False  # stop only
    assert paper.intrabar_ambiguous(_position(), _candle(110.0, 104.0, 109.5)) is False  # target only
    assert paper.intrabar_ambiguous(_position(), None) is False


def test_resolve_intrabar_exit_returns_whichever_came_first():
    tp_first = [_fine("2026-07-23T00:01:00Z", 109.5, 105.0), _fine("2026-07-23T00:02:00Z", 106.0, 101.0)]
    sl_first = [_fine("2026-07-23T00:01:00Z", 106.0, 101.5), _fine("2026-07-23T00:02:00Z", 110.0, 105.0)]
    assert paper.resolve_intrabar_exit(_position(), tp_first) == "take_profit"
    assert paper.resolve_intrabar_exit(_position(), sl_first) == "stop_loss"


def test_resolve_intrabar_exit_orders_by_time_not_by_argument_order():
    # Same two bars, reversed: the TIME order decides, not the sequence handed in.
    later_first = [_fine("2026-07-23T00:02:00Z", 106.0, 101.0), _fine("2026-07-23T00:01:00Z", 109.5, 105.0)]
    assert paper.resolve_intrabar_exit(_position(), later_first) == "take_profit"


def test_resolve_intrabar_exit_is_none_when_nothing_is_touched():
    # A gap or a too-short window must read as UNRESOLVED, never as a resolution.
    assert paper.resolve_intrabar_exit(_position(), [_fine("2026-07-23T00:01:00Z", 106.0, 104.0)]) is None
    assert paper.resolve_intrabar_exit(_position(), []) is None


def test_settle_uses_observed_order_over_the_pessimistic_assumption():
    position = _position()
    tp_first = [_fine("2026-07-23T00:01:00Z", 109.5, 105.0), _fine("2026-07-23T00:02:00Z", 106.0, 101.0)]
    reason, exit_price, r = settle_trade_plan(
        position, _candle(*_AMBIGUOUS), 108.0, 48, False, fine_candles=tp_first
    )
    assert (reason, exit_price) == ("take_profit", 109.0)
    assert r == (109.0 - 105.0) / 3.0
    assert position["exit_resolution"] == "observed_fine_candles"


def test_settle_observed_order_can_also_confirm_the_stop():
    position = _position()
    sl_first = [_fine("2026-07-23T00:01:00Z", 106.0, 101.5)]
    reason, exit_price, r = settle_trade_plan(
        position, _candle(*_AMBIGUOUS), 108.0, 48, False, fine_candles=sl_first
    )
    assert (reason, exit_price, r) == ("stop_loss", 102.0, -1.0)
    assert position["exit_resolution"] == "observed_fine_candles"


def test_settle_keeps_pessimism_when_fine_candles_resolve_nothing():
    position = _position()
    reason, exit_price, r = settle_trade_plan(
        position, _candle(*_AMBIGUOUS), 108.0, 48, False,
        fine_candles=[_fine("2026-07-23T00:01:00Z", 106.0, 104.0)],
    )
    assert (reason, exit_price, r) == ("stop_loss", 102.0, -1.0)
    assert position["exit_resolution"] == "pessimistic_sl_first"


def test_settle_without_fine_candles_is_unchanged():
    # The pre-existing contract: no finer evidence supplied -> SL-first assumption.
    position = _position()
    reason, exit_price, r = settle_trade_plan(position, _candle(*_AMBIGUOUS), 108.0, 48, False)
    assert (reason, exit_price, r) == ("stop_loss", 102.0, -1.0)
    assert position["exit_resolution"] == "pessimistic_sl_first"


def test_settle_marks_an_unambiguous_exit_as_such():
    position = _position()
    settle_trade_plan(position, _candle(104.0, 101.0, 103.0), 103.0, 48, False)
    assert position["exit_resolution"] == "unambiguous"


def test_outcome_record_carries_the_resolution_basis():
    position = _position()
    settle_trade_plan(position, _candle(*_AMBIGUOUS), 108.0, 48, False)
    outcome = paper.build_outcome_record(position, "stop_loss", 102.0, -1.0, now=NOW)
    assert outcome["exit_resolution"] == "pessimistic_sl_first"
    # An imported/legacy position carries no basis; it must not read as assumed.
    assert paper.build_outcome_record(_position(), "time_exit", 106.0, 0.3, now=NOW)[
        "exit_resolution"] == "unambiguous"


# --- settlement charges fees and slippage (PM2, fee half) ----------------------

def _costed(reason, exit_price, gross):
    return build_outcome_record(_position(), reason, exit_price, gross, now=NOW)


def test_the_recorded_r_is_net_and_the_gross_is_kept_beside_it():
    """Both numbers on the row, so nothing downstream has to re-derive one from prices."""
    out = _costed("time_exit", 106.0, 1 / 3)
    assert out["r_basis"] == "intent_net_of_costs"
    assert out["gross_result_R"] == round(1 / 3, 8)
    assert out["fee_cost_r"] > 0 and out["slippage_cost_r"] > 0
    assert out["result_R"] == round(out["gross_result_R"] - out["fee_cost_r"] - out["slippage_cost_r"], 8)
    assert out["result_R"] < out["gross_result_R"]


def test_settle_trade_plan_stays_the_authority_on_gross():
    """The stop's exactly--1R convention lives in settle_trade_plan and must survive costing.

    `apply_cost_model` can re-derive gross from the prices, and today it would agree — `risk`
    is `|entry - stop|` by construction. The record deliberately does NOT ask it to: one
    authority for the gross number means there is no second derivation to keep agreeing."""
    reason, exit_price, gross = settle_trade_plan(
        _position(), _candle(104.0, 101.0, 103.0), 103.0, 48, False)
    assert (reason, exit_price, gross) == ("stop_loss", 102.0, -1.0)
    assert _costed(reason, exit_price, gross)["gross_result_R"] == -1.0


def test_a_stop_out_now_loses_more_than_the_r_it_risked():
    """The headline consequence: -1R was never the true cost of being stopped out."""
    out = _costed("stop_loss", 102.0, -1.0)
    assert out["result_R"] < -1.0 and out["win_loss"] == "LOSS"


def test_a_target_exit_is_charged_the_maker_rate_and_no_slippage():
    """A resting limit fills AT the target: it does not cross the spread (see cost.py)."""
    target = _costed("take_profit", 109.0, 4 / 3)
    market = _costed("time_exit", 109.0, 4 / 3)
    assert target["maker_fee_cost_r"] > 0 and market["maker_fee_cost_r"] == 0.0
    assert target["slippage_cost_r"] < market["slippage_cost_r"]
    assert target["result_R"] > market["result_R"]      # cheaper exit, same prices


def test_win_loss_is_judged_on_the_net_number():
    """A trade that cleared its stop but not its costs is not a win.

    Calling it one is how a losing book reports a healthy win rate — and win rate feeds the
    lifecycle's live-vs-backtest drop check."""
    out = _costed("time_exit", 105.05, 0.05 / 3)          # +0.017R gross, under the costs
    assert out["gross_result_R"] > 0
    assert out["result_R"] < 0 and out["win_loss"] == "LOSS"


def test_a_zero_risk_position_is_not_charged_rather_than_dividing_by_zero():
    """cost.apply_cost_model's own guard, reached through the record builder."""
    out = build_outcome_record(_position(risk=0.0), "time_exit", 106.0, 0.0, now=NOW)
    assert out["fee_cost_r"] == 0.0 and out["slippage_cost_r"] == 0.0
    assert out["result_R"] == out["gross_result_R"] == 0.0


def test_the_costed_r_reaches_the_risk_guard():
    """The reason this change exists: the breaker reads result_R, and it was reading gross.

    Three stop-outs are -3R gross; the breaker's daily limit is -2R, so it tripped either way
    here — what changes is that the number it reports is now the honest one."""
    outs = [_costed("stop_loss", 102.0, -1.0) for _ in range(3)]
    verdict = run_risk_guard(outs, now=NOW)
    assert verdict["daily_pnl_r"] < -3.0                  # worse than the gross -3.0
    assert verdict["allow_new_position"] is False
