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
    assert outcome["outcome_closed"] is True and outcome["result_R"] == -1.0
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
    assert load_open_position(tmp_path) is None


def test_load_position_corrupt_raises(tmp_path):
    path = paper.state_dir(tmp_path)
    path.mkdir(parents=True)
    (path / "paper_position.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        load_open_position(tmp_path)
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
    assert load_open_position(tmp_path) is None  # ...but nothing persisted
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
    opened = load_open_position(tmp_path)
    assert opened is not None and opened["entry_price"] == 105.0

    # Next cycle: the candle trades through the stop (102) — settle to -1R.
    sl_candle = {"open_time": "2026-07-22T00:00:00Z", "open": 104.0, "high": 104.5, "low": 101.0,
                 "close": 103.0, "volume": 9.0, "close_time": "2026-07-23T00:00:00Z"}
    summary2, records2 = run_paper_update(
        _snapshot(sl_candle), ROW_NO_MATCH, _pool(_pool_entry()), _verdict(),
        store=_real_store(tmp_path), now="2026-07-23T12:00:00Z", root=tmp_path, control_store=control_store,
    )
    assert summary2["settled"]["close_reason"] == "stop_loss"
    assert load_open_position(tmp_path) is None
    outcomes = read_outcomes(tmp_path)
    assert len(outcomes) == 1 and outcomes[0]["result_R"] == -1.0
    assert records2[0]["filesystem_write"] is True


def test_single_position_no_double_open(tmp_path):
    control_store = ControlStore(tmp_path)
    run_paper_update(_snapshot(), ROW, _pool(_pool_entry()), _verdict(),
                     store=_real_store(tmp_path), now=NOW, root=tmp_path, control_store=control_store)
    first = load_open_position(tmp_path)
    # Second cycle still matches but must not double the position (one open book).
    calm = {"open_time": "2026-07-22T00:00:00Z", "open": 105.0, "high": 106.0, "low": 104.0,
            "close": 105.5, "volume": 9.0, "close_time": "2026-07-23T00:00:00Z"}
    summary, _ = run_paper_update(
        _snapshot(calm), ROW, _pool(_pool_entry()), _verdict(),
        store=_real_store(tmp_path), now="2026-07-23T12:00:00Z", root=tmp_path, control_store=control_store,
    )
    assert summary["opened"] is None
    assert load_open_position(tmp_path)["position_id"] == first["position_id"]


@pytest.mark.parametrize("symbol,timeframe", [("ETHUSDT", "1d"), ("BTCUSDT", "1h")])
def test_foreign_context_cycle_refuses_settlement(symbol, timeframe, tmp_path):
    control_store = ControlStore(tmp_path)
    run_paper_update(_snapshot(), ROW, _pool(_pool_entry()), _verdict(),
                     store=_real_store(tmp_path), now=NOW, root=tmp_path, control_store=control_store)
    opened = load_open_position(tmp_path)
    # A foreign cycle whose candle trades through the BTC stop must not settle it.
    sl_candle = {"open_time": "2026-07-22T00:00:00Z", "open": 104.0, "high": 104.5, "low": 101.0,
                 "close": 103.0, "volume": 9.0, "close_time": "2026-07-23T00:00:00Z"}
    summary, records = run_paper_update(
        _snapshot(sl_candle, symbol=symbol, timeframe=timeframe), ROW, _pool(_pool_entry()), _verdict(),
        store=_real_store(tmp_path), now="2026-07-23T12:00:00Z", root=tmp_path, control_store=control_store,
    )
    assert summary["settled"] is None and summary["opened"] is None
    assert summary["settle_refused"]["reason_code"] == "POSITION_CONTEXT_MISMATCH"
    assert summary["settle_refused"]["snapshot_symbol"] == symbol
    assert records[0]["operation"] == "settle_refused" and records[0]["read_only"] is True
    assert read_outcomes(tmp_path) == []  # no outcome fabricated from foreign candles
    untouched = load_open_position(tmp_path)
    assert untouched["position_id"] == opened["position_id"]
    # Foreign candles must not advance time_exit either.
    assert untouched["holding_candles"] == opened["holding_candles"]


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
    opened = load_open_position(tmp_path)
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
    opened = load_open_position(tmp_path)
    # Simulate crash window A: the outcome got durably written, the clear was lost.
    store = _real_store(tmp_path)
    store.append_outcome(paper.build_outcome_record(opened, "stop_loss", 102.0, -1.0, now=NOW))
    assert load_open_position(tmp_path) is not None  # position still OPEN — the corpse

    summary, records = run_paper_update(
        _snapshot(SL_CANDLE), ROW_NO_MATCH, _pool(_pool_entry()), _verdict(allow=False),
        store=store, now="2026-07-23T12:00:00Z", root=tmp_path, control_store=control_store,
    )
    assert summary["settle_recovered"]["reason_code"] == "SETTLEMENT_ALREADY_RECORDED"
    assert summary["settled"] is None
    assert records[0]["operation"] == "settle_recovered"
    assert len(read_outcomes(tmp_path)) == 1  # never a second outcome for one position
    assert load_open_position(tmp_path) is None  # the interrupted settlement finished


def test_recovered_slot_can_open_in_the_same_cycle(tmp_path):
    control_store = ControlStore(tmp_path)
    run_paper_update(_snapshot(), ROW, _pool(_pool_entry()), _verdict(),
                     store=_real_store(tmp_path), now=NOW, root=tmp_path, control_store=control_store)
    opened = load_open_position(tmp_path)
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
    assert load_open_position(tmp_path)["position_id"] != opened["position_id"]


def test_unreadable_history_refuses_settlement(tmp_path):
    control_store = ControlStore(tmp_path)
    run_paper_update(_snapshot(), ROW, _pool(_pool_entry()), _verdict(),
                     store=_real_store(tmp_path), now=NOW, root=tmp_path, control_store=control_store)
    opened = load_open_position(tmp_path)
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
    assert load_open_position(tmp_path)["position_id"] == opened["position_id"]


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
    assert load_open_position(tmp_path)["max_holding_bars"] == 2

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
