"""Routing-freshness gate — evaluate a NEW entry at most once per closed candle.

One empty-request ``crypto_pipeline`` schedule fans out over every pool context at the
finest (15-min) cadence. The gate holds a coarser-timeframe strategy's NEW entry until
a newer candle closes, so a 4h/1d strategy is not re-entered every tick nor re-opened on
the same candle right after a settle. Settlement is never gated (step 1, before the gate)
— an open position must always be able to close every tick. Absent a marks store the gate
is a no-op (backtest / existing callers unchanged)."""

from __future__ import annotations

import pytest
from tests._helpers import make_gate_authorization

from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.crypto.paper import (
    CANDLE_NOT_FRESH,
    STATUS_ENTRY_CANDIDATE,
    PositionContext,
    RealPaperStore,
    DryRunPaperStore,
    load_open_position,
    run_paper_update,
)
from runtime.mvp_runtime.crypto.routing_marks import RoutingMarkStore, is_fresh
from runtime.mvp_runtime.safety_gate import FILESYSTEM_WRITE

CTX = PositionContext(venue="binance_futures", symbol="BTCUSDT", timeframe="1d")
T1 = "2026-07-22T00:00:00Z"
T2 = "2026-07-23T00:00:00Z"

ROW = {"timestamp": T1, "close": 105.0, "ma20": 100.0, "adx": 25.0, "atr": 2.0}
ROW_NO_MATCH = {**ROW, "close": 95.0}

_AUTH = make_gate_authorization(flags=(FILESYSTEM_WRITE,), provider_id="paper_trading")


def _spec():
    return {
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
    }


def _pool():
    return {
        "pool_version": "active_strategy_pool.v1",
        "active_strategies": [{
            "strategy_id": "S1", "status": "PAPER_ACTIVE", "champion_score": 0.5,
            "strategy_rule_hash": "h", "generation_id": "GEN-1", "strategy_spec": _spec(),
        }],
    }


def _snapshot(close_time=T1, *, high=106.0, low=103.0, close=105.0):
    candle = {
        "open_time": "2026-07-21T00:00:00Z", "open": 104.0, "high": high, "low": low,
        "close": close, "volume": 10.0, "close_time": close_time,
    }
    return {"symbol": "BTCUSDT", "timeframe": "1d", "candles": [candle]}


def _verdict(allow=True):
    return {"allow_new_position": allow}


def _real(tmp_path):
    return RealPaperStore(root=tmp_path, authorization=_AUTH)


# --- is_fresh unit ------------------------------------------------------------

@pytest.mark.parametrize("last,candle,expected", [
    (None, T1, True),       # no mark yet → fresh
    (T1, T2, True),         # strictly newer candle → fresh
    (T1, T1, False),        # same candle already routed → not fresh
    (T2, T1, False),        # older candle (out of order) → not fresh
    (None, None, False),    # unidentifiable candle → never fresh
    (T1, None, False),
])
def test_is_fresh(last, candle, expected):
    assert is_fresh(last, candle) is expected


# --- the gate on the live paper path ------------------------------------------

def test_no_match_still_marks_candle_then_blocks_reeval(tmp_path):
    """A fresh candle is marked even on no-match, so a match on the SAME candle later
    in the tick sequence is held rather than re-evaluated."""
    marks = RoutingMarkStore(tmp_path)
    control = ControlStore(tmp_path)

    # Cycle 1 — no match on candle T1, but the candle is evaluated and marked.
    s1, _ = run_paper_update(_snapshot(T1), ROW_NO_MATCH, _pool(), _verdict(),
                             store=_real(tmp_path), now="2026-07-22T06:00:00Z", root=tmp_path,
                             control_store=control, routing_marks=marks)
    assert s1["opened"] is None and s1["open_skipped"] is None
    assert marks.last(CTX.key) == T1

    # Cycle 2 — a MATCH now arrives on the SAME candle T1: held, not opened.
    s2, recs2 = run_paper_update(_snapshot(T1), ROW, _pool(), _verdict(),
                                 store=_real(tmp_path), now="2026-07-22T12:00:00Z", root=tmp_path,
                                 control_store=control, routing_marks=marks)
    assert s2["opened"] is None
    assert s2["open_skipped"]["reason_code"] == CANDLE_NOT_FRESH
    assert s2["route_status"] == STATUS_ENTRY_CANDIDATE  # the router still reports the signal
    assert any(r["operation"] == "open_skipped" for r in recs2)
    assert load_open_position(CTX, tmp_path) is None


def test_new_candle_opens_again(tmp_path):
    marks = RoutingMarkStore(tmp_path)
    control = ControlStore(tmp_path)
    # Seed the mark as if T1 were already routed.
    marks.record(CTX.key, T1)

    # Same candle T1 → held.
    s1, _ = run_paper_update(_snapshot(T1), ROW, _pool(), _verdict(),
                             store=_real(tmp_path), now="2026-07-22T12:00:00Z", root=tmp_path,
                             control_store=control, routing_marks=marks)
    assert s1["opened"] is None and s1["open_skipped"]["reason_code"] == CANDLE_NOT_FRESH

    # Newer candle T2 → opens, mark advances.
    s2, _ = run_paper_update(_snapshot(T2), ROW, _pool(), _verdict(),
                             store=_real(tmp_path), now="2026-07-23T12:00:00Z", root=tmp_path,
                             control_store=control, routing_marks=marks)
    assert s2["opened"] is not None
    assert marks.last(CTX.key) == T2


def test_settlement_runs_even_when_candle_not_fresh(tmp_path):
    """The gate wraps only the open path. An open position still settles on a stale
    candle, and only the *re-open* on that same candle is held."""
    marks = RoutingMarkStore(tmp_path)
    control = ControlStore(tmp_path)

    # Cycle 1 — open on a calm candle T1; mark records T1.
    s1, _ = run_paper_update(_snapshot(T1), ROW, _pool(), _verdict(),
                             store=_real(tmp_path), now="2026-07-22T12:00:00Z", root=tmp_path,
                             control_store=control, routing_marks=marks)
    assert s1["opened"] is not None
    assert marks.last(CTX.key) == T1

    # Cycle 2 — SAME candle T1, but the low now breaches the stop. Settlement (step 1)
    # runs regardless of freshness; the re-open (step 2) is then held on the stale candle.
    s2, _ = run_paper_update(_snapshot(T1, high=106.0, low=100.0, close=101.0), ROW, _pool(), _verdict(),
                             store=_real(tmp_path), now="2026-07-22T18:00:00Z", root=tmp_path,
                             control_store=control, routing_marks=marks)
    assert s2["settled"] is not None and s2["settled"]["close_reason"] == "stop_loss"
    assert s2["opened"] is None
    assert s2["open_skipped"]["reason_code"] == CANDLE_NOT_FRESH
    assert load_open_position(CTX, tmp_path) is None


def test_gate_is_noop_without_marks_store(tmp_path):
    """No marks store injected → behaviour is exactly as before (no gate)."""
    control = ControlStore(tmp_path)
    # No match on T1 (nothing opened, no book occupied), then a match on the SAME candle
    # T1 opens — because with no marks store the candle is never considered 'already routed'.
    run_paper_update(_snapshot(T1), ROW_NO_MATCH, _pool(), _verdict(),
                     store=_real(tmp_path), now="2026-07-22T06:00:00Z", root=tmp_path,
                     control_store=control)
    s, _ = run_paper_update(_snapshot(T1), ROW, _pool(), _verdict(),
                            store=_real(tmp_path), now="2026-07-22T12:00:00Z", root=tmp_path,
                            control_store=control)
    assert s["opened"] is not None and s["open_skipped"] is None


def test_dry_run_keeps_no_marks(tmp_path):
    """A dry-run store persists nothing, so it also records no marks — an ungated dry run."""
    marks = RoutingMarkStore(tmp_path)
    control = ControlStore(tmp_path)
    s, _ = run_paper_update(_snapshot(T1), ROW, _pool(), _verdict(),
                            store=DryRunPaperStore(), now="2026-07-22T12:00:00Z", root=tmp_path,
                            control_store=control, routing_marks=marks)
    assert s["opened"] is not None            # the dry path still runs end to end
    assert marks.last(CTX.key) is None        # ...but no durable mark was written


def test_record_never_regresses(tmp_path):
    marks = RoutingMarkStore(tmp_path)
    marks.record(CTX.key, T2)
    marks.record(CTX.key, T1)                  # an out-of-order older candle
    assert marks.last(CTX.key) == T2