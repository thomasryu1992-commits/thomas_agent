"""C7 cycle + scheduler-template + import E2E tests.

The contract's gate condition: the five ported stages run as one governed cycle
(degrade on backend failure, refuse on kill, never trade synthetic data), the R6
template fires it with live gate selection, and the one-time import is idempotent,
provenance-marked, counterfactual-separated, and pool-activation-explicit."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from runtime.mvp_runtime import control, timeutil
from runtime.mvp_runtime.control import ControlState, ControlStore
from runtime.mvp_runtime.crypto import paper, pool
from runtime.mvp_runtime.crypto.cycle import cycle_status_line, run_crypto_cycle
from runtime.mvp_runtime.crypto.market_data import MARKET_DATA_ENV, Candle, MarketSnapshot
from runtime.mvp_runtime.crypto.paper import (
    PAPER_ENV, DryRunPaperStore, PositionContext, RealPaperStore, load_open_position,
)

CTX = PositionContext(venue="binance_futures", symbol="BTCUSDT", timeframe="1d")
from runtime.mvp_runtime.errors import ToolBlocked, ToolError
from runtime.mvp_runtime.safety_gate import FILESYSTEM_WRITE, Authorization
from runtime.mvp_runtime.scheduler import KIND_CRYPTO, ScheduleStore, build_schedule, run_due
from runtime.mvp_runtime.store import CONTROL_FILE, LEDGER_REL, RECORDS_FILE, LedgerStore

from scripts.import_crypto_history import run_import

NOW_DT = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)
NOW = "2026-07-22T12:00:00Z"

_AUTH = Authorization(
    flags=(FILESYSTEM_WRITE,), provider_id="paper_trading", activation_sha256="sha256:test",
    expires_at="2999-01-01T00:00:00Z", evidence_ref=".runtime_governance_state/evidence.md",
)


class FakeExchangeCollector:
    """Deterministic non-synthetic collector: flat candles ending 1h before NOW,
    optionally with one extra candle appended (the next cycle's fresh bar)."""

    tool_id = "crypto.market_data.readonly"
    tool_version = "0.1.0-fake"
    network_egress = False
    source = "fake_exchange"

    def __init__(self, extra_candle: dict | None = None):
        self._extra = extra_candle

    def collect(self, symbol, timeframe, *, limit, timeout_seconds):
        step = timedelta(days=1)
        last_close = NOW_DT - timedelta(hours=1)
        n = 60
        candles = []
        for i in range(n):
            close_time = last_close - (n - 1 - i) * step
            candles.append(Candle(
                open_time=timeutil.format_iso(close_time - step),
                open=100.0, high=101.0, low=99.0, close=100.0, volume=10.0,
                close_time=timeutil.format_iso(close_time),
            ))
        if self._extra is not None:
            prev_close_time = timeutil.parse_iso(candles[-1].close_time)
            candles.append(Candle(
                open_time=candles[-1].close_time,
                open=self._extra.get("open", 100.0), high=self._extra["high"],
                low=self._extra["low"], close=self._extra["close"], volume=10.0,
                close_time=timeutil.format_iso(prev_close_time + step),
            ))
        return MarketSnapshot(symbol=symbol, timeframe=timeframe, candles=candles,
                              source=self.source, is_synthetic=False)


class BrokenCollector:
    tool_id, tool_version = "crypto.market_data.readonly", "0.1.0-broken"
    network_egress = True
    source = "fake_exchange"

    def collect(self, symbol, timeframe, *, limit, timeout_seconds):
        raise ToolError("TOOL_TRANSPORT", "exchange unreachable")


def _always_spec(strategy_id="S_ALWAYS"):
    return {
        "schema_version": "strategy_spec.v1",
        "strategy_id": strategy_id,
        "strategy_version": "1.0",
        "strategy_family": "breakout",
        "symbol_scope": ["BTCUSDT"],
        "timeframe": "1d",
        "direction": "long",
        "entry_rules": {"operator": "AND",
                        "conditions": [{"feature": "close", "comparison": ">", "value": 0.0}]},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 2.0, "max_holding_bars": 10},
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    }


def _install_pool(root, *specs):
    pool.install_active_pool(
        {"active_strategies": [
            {"strategy_id": s["strategy_id"], "status": "PAPER_ACTIVE", "champion_score": 0.5,
             "strategy_spec": s}
            for s in specs
        ]},
        root=root,
    )


def _cycle(root, collector, store=None, now=NOW, **kwargs):
    return run_crypto_cycle(
        collector=collector, store=store or DryRunPaperStore(), now=now, root=root,
        control_store=ControlStore(root), **kwargs,
    )


# --- the full cycle -----------------------------------------------------------

def test_full_cycle_opens_then_settles_with_real_store(tmp_path):
    _install_pool(tmp_path, _always_spec())
    store = RealPaperStore(root=tmp_path, authorization=_AUTH)

    record = _cycle(tmp_path, FakeExchangeCollector(), store)
    assert record["verdict_status"] == "ALLOW"
    assert record["opened"] is not None and record["settled"] is None
    opened = load_open_position(CTX, tmp_path)
    # Flat candles: ATR 2 -> entry 100, stop 97, target 104.
    assert opened["entry_price"] == 100.0 and opened["stop_loss"] == 97.0

    # Next day's candle sweeps the stop.
    sl_candle = {"high": 100.5, "low": 96.0, "close": 98.0}
    record2 = _cycle(tmp_path, FakeExchangeCollector(extra_candle=sl_candle), store,
                     now="2026-07-23T12:00:00Z")
    assert record2["settled"]["close_reason"] == "stop_loss"
    assert record2["settled"]["result_R"] == -1.0
    # Settle-then-reopen within one cycle is the source trading-cycle order: the
    # stopped position closed AND the still-matching strategy opened a fresh one.
    assert record2["opened"] is not None
    reopened = load_open_position(CTX, tmp_path)
    assert reopened["position_id"] != opened["position_id"]
    outcomes = paper.read_outcomes(tmp_path)
    assert len(outcomes) == 1 and outcomes[0]["strategy_id"] == "S_ALWAYS"
    # Feedback ran on the persisted truth and the digest carries it.
    assert record2["report_status"] is not None
    assert "paper performance report" in record2["report_text"]


def test_cycle_refused_while_killed(tmp_path):
    store = ControlStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        json.dumps(ControlState(mode=control.KILLED, updated_by="op", updated_at=NOW, reason="t").as_record()),
        encoding="utf-8",
    )
    with pytest.raises(ToolBlocked) as exc:
        run_crypto_cycle(collector=FakeExchangeCollector(), store=DryRunPaperStore(),
                         now=NOW, root=tmp_path, control_store=store)
    assert exc.value.reason_code == "RUNTIME_KILLED"


def test_backend_failure_degrades_never_blocks(tmp_path):
    _install_pool(tmp_path, _always_spec())
    record = _cycle(tmp_path, BrokenCollector())
    assert record["degraded"] is True
    assert "MARKET_DATA_DEGRADED" in record["reason_codes"]
    assert record["verdict_status"] == "NO_NEW_POSITION"
    assert record["opened"] is None
    assert record["report_text"]  # feedback still ran


def test_synthetic_data_cycles_but_never_trades(tmp_path):
    from runtime.mvp_runtime.crypto.market_data import MockMarketDataCollector

    _install_pool(tmp_path, _always_spec())
    record = _cycle(tmp_path, MockMarketDataCollector())
    assert record["verdict_status"] == "NO_NEW_POSITION"
    assert "synthetic_data_source_blocks_trading" in record["verdict_problems"]
    assert record["opened"] is None


def test_tampered_pool_refuses_routing_not_the_cycle(tmp_path):
    path = pool.pool_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"active_strategies": [{"strategy_spec": {"strategy_id": "x"}}]}),
                    encoding="utf-8")
    record = _cycle(tmp_path, FakeExchangeCollector())
    assert "STRATEGY_POOL_INVALID" in record["reason_codes"]
    assert record["route_status"] == "NO_ENTRY"
    assert record["cycle_id"].startswith("crypto_cycle")


def test_foreign_symbol_cycle_leaves_the_other_book_untouched(tmp_path):
    _install_pool(tmp_path, _always_spec())
    store = RealPaperStore(root=tmp_path, authorization=_AUTH)
    _cycle(tmp_path, FakeExchangeCollector(), store)
    opened = load_open_position(CTX, tmp_path)
    assert opened is not None

    # An ETH cycle whose candle sweeps the BTC stop trades its own book; the BTC
    # position is not merely refused, it is not reachable from here at all.
    sl_candle = {"high": 100.5, "low": 96.0, "close": 98.0}
    record = _cycle(tmp_path, FakeExchangeCollector(extra_candle=sl_candle), store,
                    now="2026-07-23T12:00:00Z", symbol="ETHUSDT")
    assert record["settled"] is None
    assert paper.read_outcomes(tmp_path) == []
    untouched = load_open_position(CTX, tmp_path)
    assert untouched["position_id"] == opened["position_id"]
    assert untouched["holding_candles"] == opened["holding_candles"]


def test_status_line_summarizes(tmp_path):
    _install_pool(tmp_path, _always_spec())
    record = _cycle(tmp_path, FakeExchangeCollector(),
                    RealPaperStore(root=tmp_path, authorization=_AUTH))
    line = cycle_status_line(record)
    assert "verdict=ALLOW" in line and "opened=LONG:S_ALWAYS" in line


# --- the scheduler template ---------------------------------------------------

def test_scheduler_fires_crypto_cycle_and_ledgers_it(tmp_path, monkeypatch):
    monkeypatch.delenv(MARKET_DATA_ENV, raising=False)
    monkeypatch.delenv(PAPER_ENV, raising=False)
    schedule = build_schedule(kind=KIND_CRYPTO, request="", interval_seconds=900,
                              created_by="op", now="2026-07-22T11:00:00Z")
    store = ScheduleStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.add(schedule)
    ledger = LedgerStore(tmp_path / LEDGER_REL)

    summary = run_due(store, now="2026-07-22T13:00:00Z", control_store=ControlStore(tmp_path),
                      ledger=ledger, repo_root=tmp_path)
    assert summary["fired"] == 1
    # Default gates: mock collector (synthetic) -> the cycle ran and refused to trade.
    assert "verdict=NO_NEW_POSITION" in summary["results"][0]["status"]
    rows = [json.loads(line) for line in
            (tmp_path / LEDGER_REL / RECORDS_FILE).read_text(encoding="utf-8").splitlines()]
    assert any(r["kind"] == "crypto_cycle" for r in rows)


def test_scheduler_crypto_request_overrides_symbol(tmp_path, monkeypatch):
    monkeypatch.delenv(MARKET_DATA_ENV, raising=False)
    monkeypatch.delenv(PAPER_ENV, raising=False)
    schedule = build_schedule(kind=KIND_CRYPTO, request="ETHUSDT 4h", interval_seconds=900,
                              created_by="op", now="2026-07-22T11:00:00Z")
    store = ScheduleStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.add(schedule)
    ledger = LedgerStore(tmp_path / LEDGER_REL)
    run_due(store, now="2026-07-22T13:00:00Z", control_store=ControlStore(tmp_path),
            ledger=ledger, repo_root=tmp_path)
    rows = [json.loads(line) for line in
            (tmp_path / LEDGER_REL / RECORDS_FILE).read_text(encoding="utf-8").splitlines()]
    cycle_rows = [r for r in rows if r["kind"] == "crypto_cycle"]
    assert cycle_rows[0]["record"]["symbol"] == "ETHUSDT"
    assert cycle_rows[0]["record"]["timeframe"] == "4h"


# --- the one-time import ------------------------------------------------------

def _fake_source(tmp_path):
    src = tmp_path / "crypto_src"
    (src / "storage/registries").mkdir(parents=True)
    (src / "storage/latest").mkdir(parents=True)
    outcomes = [
        {"outcome_feedback_registry_record_id": "orig_1", "outcome_id": "out_1",
         "outcome_closed": True, "result_R": 1.5, "created_at_utc": "2026-07-01T00:00:00Z"},
        {"outcome_feedback_registry_record_id": "orig_2", "outcome_id": "out_2",
         "outcome_closed": True, "result_R": -1.0, "created_at_utc": "2026-07-02T00:00:00Z"},
    ]
    (src / "storage/registries/outcome_feedback_registry.jsonl").write_text(
        "".join(json.dumps(o) + "\n" for o in outcomes), encoding="utf-8")
    (src / "storage/registries/counterfactual_outcome_registry.jsonl").write_text(
        json.dumps({"counterfactual_id": "cf_1", "outcome_closed": True, "result_R": -2.0}) + "\n",
        encoding="utf-8")
    (src / "storage/latest/active_strategy_pool.json").write_text(
        json.dumps({"pool_version": "active_strategy_pool.v1",
                    "active_strategies": [{"strategy_id": "S_ALWAYS", "status": "PAPER_ACTIVE",
                                           "champion_score": 0.7, "strategy_spec": _always_spec()}]}),
        encoding="utf-8")
    return src


def test_import_dry_run_writes_nothing(tmp_path):
    src = _fake_source(tmp_path)
    summary = run_import(source=src, root=tmp_path, confirm=False, now=NOW)
    assert summary["outcomes_imported"] == 2 and summary["confirmed"] is False
    assert paper.read_outcomes(tmp_path) == []


def test_import_is_provenance_marked_separated_and_idempotent(tmp_path):
    src = _fake_source(tmp_path)
    summary = run_import(source=src, root=tmp_path, confirm=True, now=NOW)
    assert summary["outcomes_imported"] == 2 and summary["candidates_imported"] == 1

    outcomes = paper.read_outcomes(tmp_path)
    assert len(outcomes) == 2
    assert all(o["provenance"] == "crypto_ai_system_import" for o in outcomes)
    # Counterfactuals live in their own file: the risk guard must not count shadows.
    assert all(o["kind"] == "outcome" for o in outcomes)
    counter = (paper.state_dir(tmp_path) / "counterfactual_outcomes.jsonl").read_text(encoding="utf-8")
    assert "cf_1" in counter
    # The active pool is NOT installed without the explicit flag.
    assert pool.load_active_pool(tmp_path) == {"active_strategies": []}
    # Audited on the control ledger.
    control_lines = (tmp_path / LEDGER_REL / CONTROL_FILE).read_text(encoding="utf-8")
    assert "crypto_import_event.v0" in control_lines

    # Re-run: nothing new.
    again = run_import(source=src, root=tmp_path, confirm=True, now="2026-07-23T00:00:00Z")
    assert again["outcomes_imported"] == 0 and again["candidates_imported"] == 0
    assert len(paper.read_outcomes(tmp_path)) == 2


def test_import_activate_pool_routes_next_cycle(tmp_path):
    src = _fake_source(tmp_path)
    summary = run_import(source=src, root=tmp_path, confirm=True, activate_pool=True, now=NOW)
    assert summary["pool_activated"] is True
    record = _cycle(tmp_path, FakeExchangeCollector())
    assert record["route_status"] == "ENTRY_CANDIDATE"
    # Imported history feeds the risk guard immediately.
    assert record["verdict_status"] == "ALLOW"


def test_imported_history_drives_risk_guard(tmp_path):
    src = _fake_source(tmp_path)
    run_import(source=src, root=tmp_path, confirm=True, now=NOW)
    from runtime.mvp_runtime.crypto.guards import run_risk_guard

    verdict = run_risk_guard(paper.read_outcomes(tmp_path), now=NOW)
    assert verdict["consecutive_losses"] == 1  # orig_2 is the latest and a loss
