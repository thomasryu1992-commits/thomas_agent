"""C7 cycle + scheduler-template + import E2E tests.

The contract's gate condition: the five ported stages run as one governed cycle
(degrade on backend failure, refuse on kill, never trade synthetic data), the R6
template fires it with live gate selection, and the one-time import is idempotent,
provenance-marked, counterfactual-separated, and pool-activation-explicit."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from tests._helpers import make_gate_authorization

from runtime.mvp_runtime import control, timeutil
from runtime.mvp_runtime.control import ControlState, ControlStore
from runtime.mvp_runtime.crypto import paper, pool
from runtime.mvp_runtime.crypto.cycle import (
    cycle_status_line,
    pool_cycle_contexts,
    pool_cycle_status_line,
    run_crypto_cycle,
    run_pool_cycle,
)
from runtime.mvp_runtime.crypto.market_data import MARKET_DATA_ENV, Candle, MarketSnapshot
from runtime.mvp_runtime.crypto.paper import (
    PAPER_ENV, DryRunPaperStore, PositionContext, RealPaperStore, load_open_position,
)

CTX = PositionContext(venue="binance_futures", symbol="BTCUSDT", timeframe="1d")
from runtime.mvp_runtime.errors import ToolBlocked, ToolError
from runtime.mvp_runtime.safety_gate import FILESYSTEM_WRITE
from runtime.mvp_runtime.scheduler import KIND_CRYPTO, ScheduleStore, build_schedule, run_due
from runtime.mvp_runtime.store import CONTROL_FILE, LEDGER_REL, RECORDS_FILE, LedgerStore

from scripts.import_crypto_history import run_import

NOW_DT = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)
NOW = "2026-07-22T12:00:00Z"

_AUTH = make_gate_authorization(flags=(FILESYSTEM_WRITE,), provider_id="paper_trading")


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


def _always_spec(strategy_id="S_ALWAYS", symbol="BTCUSDT", timeframe="1d"):
    return {
        "schema_version": "strategy_spec.v1",
        "strategy_id": strategy_id,
        "strategy_version": "1.0",
        "strategy_family": "breakout",
        "symbol_scope": [symbol],
        "timeframe": timeframe,
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
    # Costed since 2026-07-30: a stop-out now settles WORSE than the -1R it risked, because
    # the two legs' fees and slippage come off it. (The gross-stays--1R invariant is pinned in
    # test_mvp_runtime_crypto_paper.py, on the outcome record that carries both numbers.)
    assert record2["settled"]["result_R"] < -1.0
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


# --- what a POSITION CAP costs, made measurable ---------------------------------
#
# The counterfactual book has always shadowed entries the C4 guards refused, so
# "what did the daily-loss breaker cost" is answerable from the ledger. The three POSITION
# caps — portfolio count, per-symbol count, directional lean — had no such record, so what they
# cost has only ever been answerable by simulation. That is not an academic gap: the PR that
# added the directional cap justified it with numbers measured on mock candles, because there
# was no other source. These shadow the refused plan under the cap's own reason code, which is
# what lets `counterfactual_by_reason` price each cap on the machine that actually ran it.

def _cap_refused_cycle(tmp_path, monkeypatch):
    """Fill the portfolio to a pinned cap on BTC, then run an ETH cycle that must be refused."""
    monkeypatch.setattr(paper, "MAX_CONCURRENT_POSITIONS", 1)
    _install_pool(tmp_path, _always_spec(), _always_spec("S_ETH", symbol="ETHUSDT"))
    store = RealPaperStore(root=tmp_path, authorization=_AUTH)
    _cycle(tmp_path, FakeExchangeCollector(), store)
    assert load_open_position(CTX, tmp_path) is not None
    return _cycle(tmp_path, FakeExchangeCollector(), store,
                  now="2026-07-23T12:00:00Z", symbol="ETHUSDT"), store


def test_a_cap_refusal_is_shadowed_under_its_own_reason_code(tmp_path, monkeypatch):
    from runtime.mvp_runtime.crypto import counterfactual

    record, _store = _cap_refused_cycle(tmp_path, monkeypatch)
    assert record["opened"] is None
    assert record["open_refused"]["reason_code"] == "POSITION_LIMIT_PORTFOLIO"
    assert record["counterfactual"]["opened"] is not None, "the refused plan left no shadow"

    shadows = counterfactual.load_open_counterfactuals(tmp_path)
    eth = [s for s in shadows if s.get("symbol") == "ETHUSDT"]
    assert len(eth) == 1
    # The CAP's reason, not the verdict's — the verdict allowed this entry, which is precisely
    # why the cap is the thing that needs pricing.
    assert eth[0]["block_reasons"] == ["POSITION_LIMIT_PORTFOLIO"]


def test_a_tripped_breaker_binds_the_live_gate_and_not_the_paper_leg(tmp_path):
    """The breaker binds the LIVE gate and nothing else — now tripped by the only losses that
    can trip it.

    This fixture used to seed six PAPER losses, because the guard read the paper book. It no
    longer does: the loss breakers judge live outcomes only, so paper losses are evidence for
    the `lifecycle` ladder and nothing more. Six LIVE losses trip it, the live gate refuses, and
    paper goes on trading — the same invariant, for the first time on a history that actually
    cost money."""
    from runtime.mvp_runtime.crypto import counterfactual

    _install_pool(tmp_path, _always_spec())
    store = RealPaperStore(root=tmp_path, authorization=_AUTH)
    # Written through the live ledger's own builder, so the rows are self-hashed and shaped
    # exactly as `live_leg.execute_live_exit` writes them — a hand-made row would fail the
    # verified read and report `risk_history_unreadable`, which is a refusal but not this one.
    _write_live_outcomes(tmp_path, [_live_loss(i) for i in range(1, 7)])

    record = _cycle(tmp_path, FakeExchangeCollector(), store)
    assert record["verdict_status"] != "ALLOW", "the fixture must actually trip a breaker"
    assert "max_consecutive_losses_breached" in record["verdict_problems"]
    # The paper leg trades straight through it, which is the whole point.
    assert record["paper_verdict_status"] == "ALLOW"
    assert record["paper_verdict_problems"] == []
    assert record["opened"] is not None
    # And no shadow, because nothing on the paper leg was refused.
    assert counterfactual.load_open_counterfactuals(tmp_path) == []


def test_paper_losses_no_longer_reach_the_loss_breaker_at_all(tmp_path):
    """The change this file exists to pin. A paper book deep enough to breach every R-based
    breaker leaves the live verdict ALLOW, because the breakers read live outcomes only.

    Measured on the real machine 2026-07-31, which is why it is worth a test rather than a
    comment: the live gate was reading `weekly -19.35R` and `drawdown -44.79R` off **86 paper
    rows and 0 live ones**, and 757 cycles were HELD on it while the router had a live entry
    candidate. Not one of those rows had lost any money."""
    from runtime.read_only_kernel import integrity

    _install_pool(tmp_path, _always_spec())
    store = RealPaperStore(root=tmp_path, authorization=_AUTH)
    for i in range(20):
        body = {
            "position_id": f"p{i}", "strategy_id": "S_ALWAYS", "symbol": "BTCUSDT",
            "timeframe": "1d", "direction": "LONG", "result_R": -3.0, "outcome_closed": True,
            "outcome_id": f"o{i}", "close_reason": "stop_loss",
            "created_at_utc": "2026-07-21T00:00:00Z", "closed_at_utc": "2026-07-21T00:00:00Z",
            "provenance": paper.PAPER_PROVENANCE,
        }
        store.append_outcome({**body, "record_sha256": integrity.sha256_record(body)})

    record = _cycle(tmp_path, FakeExchangeCollector(), store)
    assert record["verdict_status"] == "ALLOW"      # -60R of paper, and the live breaker is clear
    assert record["verdict_problems"] == []
    assert record["paper_verdict_status"] == "ALLOW"


def test_an_opened_position_is_never_also_shadowed(tmp_path):
    """A shadow is the trade that did NOT happen. Shadowing one that did would double-count it
    into every per-reason bucket the dashboard prices.

    Pins the BEHAVIOUR, which two independent conditions currently enforce — so removing either
    one alone leaves this green, and removing both turns it red. That is deliberate: the
    invariant is what matters, not which line happens to be carrying it today."""
    from runtime.mvp_runtime.crypto import counterfactual

    _install_pool(tmp_path, _always_spec())
    store = RealPaperStore(root=tmp_path, authorization=_AUTH)
    record = _cycle(tmp_path, FakeExchangeCollector(), store)
    assert record["opened"] is not None
    assert counterfactual.load_open_counterfactuals(tmp_path) == []


def test_the_cap_shadow_opens_once_per_refused_candle_not_once_per_tick(tmp_path, monkeypatch):
    """The property that makes this cheap, and it is not shared by the guard-blocked branch: a
    tripped breaker persists across every tick of a coarse timeframe, but `open_refused` is only
    set INSIDE the freshness gate — so re-running the same candle refuses nothing new and
    shadows nothing new."""
    from runtime.mvp_runtime.crypto import counterfactual
    from runtime.mvp_runtime.crypto.routing_marks import RoutingMarkStore

    monkeypatch.setattr(paper, "MAX_CONCURRENT_POSITIONS", 1)
    _install_pool(tmp_path, _always_spec(), _always_spec("S_ETH", symbol="ETHUSDT"))
    store = RealPaperStore(root=tmp_path, authorization=_AUTH)
    marks = RoutingMarkStore(tmp_path)
    _cycle(tmp_path, FakeExchangeCollector(), store, routing_marks=marks)
    for _tick in range(3):
        _cycle(tmp_path, FakeExchangeCollector(), store, now="2026-07-23T12:00:00Z",
               symbol="ETHUSDT", routing_marks=marks)
    eth = [s for s in counterfactual.load_open_counterfactuals(tmp_path)
           if s.get("symbol") == "ETHUSDT"]
    assert len(eth) == 1, f"three ticks on one candle left {len(eth)} shadows"


def test_status_line_summarizes(tmp_path):
    _install_pool(tmp_path, _always_spec())
    record = _cycle(tmp_path, FakeExchangeCollector(),
                    RealPaperStore(root=tmp_path, authorization=_AUTH))
    line = cycle_status_line(record)
    assert "verdict=ALLOW" in line and "opened=LONG:S_ALWAYS" in line
    assert "refused=" not in line, "a field empty on almost every line must stay off it"


def test_status_line_shows_a_cap_refusal_with_the_books_shape():
    """A refusal an operator cannot see is a refusal they cannot act on (the #359 lesson).
    Previously the two count caps reached only `paper_records`' event stream, which was
    tolerable while they fired at twenty positions and is not now that a third cap can decline
    a half-full book. The numbers ride along because the book's SHAPE is the actionable part."""
    record = {
        "verdict_status": "ALLOW", "route_status": "ENTRY_CANDIDATE",
        "open_refused": {
            "reason_code": "POSITION_LIMIT_DIRECTIONAL_SKEW", "direction": "LONG",
            "aligned": 4, "opposing": 0, "lean_after": 5, "limit": 4,
        },
    }
    line = cycle_status_line(record)
    assert "refused=POSITION_LIMIT_DIRECTIONAL_SKEW(LONG 4v0 lean=5>4)" in line

    # The two count caps surface too, without inventing numbers they do not carry.
    counted = cycle_status_line({
        "verdict_status": "ALLOW", "route_status": "ENTRY_CANDIDATE",
        "open_refused": {"reason_code": "POSITION_LIMIT_PORTFOLIO", "open_positions": 20, "limit": 20},
    })
    assert "refused=POSITION_LIMIT_PORTFOLIO" in counted and "lean=" not in counted


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


# --- the multi-symbol pool fan-out (symbol-starved router fix) -----------------

ETH_CTX = PositionContext(venue="binance_futures", symbol="ETHUSDT", timeframe="1d")


def _pool_cycle(root, collector, store=None, now=NOW, **kwargs):
    return run_pool_cycle(
        collector=collector, store=store or DryRunPaperStore(), now=now, root=root,
        control_store=ControlStore(root), **kwargs,
    )


def test_routable_contexts_dedup_and_sort(tmp_path):
    _install_pool(
        tmp_path,
        _always_spec("S_BTC", "BTCUSDT", "1d"),
        _always_spec("S_ETH", "ETHUSDT", "1d"),
        _always_spec("S_ETH_2", "ETHUSDT", "1d"),   # same context — deduped
        _always_spec("S_SOL", "SOLUSDT", "4h"),
    )
    assert pool.routable_contexts(pool.load_active_pool(tmp_path)) == [
        ("BTCUSDT", "1d"), ("ETHUSDT", "1d"), ("SOLUSDT", "4h"),
    ]


def _multi_symbol_spec(strategy_id, symbols, timeframe="1d"):
    spec = _always_spec(strategy_id, symbols[0], timeframe)
    spec["symbol_scope"] = list(symbols)
    return spec


def test_routable_contexts_includes_every_scoped_symbol(tmp_path):
    _install_pool(tmp_path, _multi_symbol_spec("S_MULTI", ["BTCUSDT", "ETHUSDT"]))
    assert pool.routable_contexts(pool.load_active_pool(tmp_path)) == [
        ("BTCUSDT", "1d"), ("ETHUSDT", "1d"),
    ]


def test_pool_cycle_opens_a_multi_symbol_strategy_on_each_symbol(tmp_path):
    # One strategy scoped to two symbols opens an independent position in each book.
    _install_pool(tmp_path, _multi_symbol_spec("S_MULTI", ["BTCUSDT", "ETHUSDT"]))
    store = RealPaperStore(root=tmp_path, authorization=_AUTH)
    summary = _pool_cycle(tmp_path, FakeExchangeCollector(), store)

    assert [c["symbol"] for c in summary["contexts"]] == ["BTCUSDT", "ETHUSDT"]
    assert load_open_position(CTX, tmp_path) is not None      # BTCUSDT book
    assert load_open_position(ETH_CTX, tmp_path) is not None  # ETHUSDT book
    opened = {c["symbol"]: c.get("opened") for c in summary["cycles"]}
    assert opened["BTCUSDT"]["strategy_id"] == "S_MULTI"
    assert opened["ETHUSDT"]["strategy_id"] == "S_MULTI"


def test_pool_cycle_evaluates_every_symbol_not_just_the_default(tmp_path):
    # Two symbols in the pool; the single-symbol cycle would only ever route one.
    _install_pool(tmp_path, _always_spec("S_BTC", "BTCUSDT"), _always_spec("S_ETH", "ETHUSDT"))
    store = RealPaperStore(root=tmp_path, authorization=_AUTH)
    summary = _pool_cycle(tmp_path, FakeExchangeCollector(), store)

    assert [c["symbol"] for c in summary["contexts"]] == ["BTCUSDT", "ETHUSDT"]
    assert summary["skipped"] == []
    # Both books opened — the ETH strategy is no longer starved.
    assert load_open_position(CTX, tmp_path) is not None
    assert load_open_position(ETH_CTX, tmp_path) is not None
    assert {r["symbol"] for r in summary["cycles"]} == {"BTCUSDT", "ETHUSDT"}


def test_pool_cycle_settles_open_position_even_after_its_strategy_leaves(tmp_path):
    # Open an ETH position, then empty the pool: the strategy is gone but the book
    # must still be visited by its own cycle so the position can settle.
    _install_pool(tmp_path, _always_spec("S_ETH", "ETHUSDT"))
    store = RealPaperStore(root=tmp_path, authorization=_AUTH)
    _pool_cycle(tmp_path, FakeExchangeCollector(), store)
    opened = load_open_position(ETH_CTX, tmp_path)
    assert opened is not None

    pool.install_active_pool({"active_strategies": []}, root=tmp_path)
    assert pool.routable_contexts(pool.load_active_pool(tmp_path)) == []
    # The open ETH position alone pulls its context into the fan-out.
    assert pool_cycle_contexts(tmp_path) == [("ETHUSDT", "1d")]

    sl_candle = {"high": 100.5, "low": 96.0, "close": 98.0}
    summary = _pool_cycle(tmp_path, FakeExchangeCollector(extra_candle=sl_candle), store,
                          now="2026-07-23T12:00:00Z")
    settled = [c for c in summary["cycles"] if c["settled"]]
    assert len(settled) == 1 and settled[0]["symbol"] == "ETHUSDT"
    assert settled[0]["settled"]["close_reason"] == "stop_loss"
    assert load_open_position(ETH_CTX, tmp_path) is None


def _book_live_position(tmp_path, symbol: str, **fields):
    """Write an OPEN live position straight to the book — no gate, no venue, no order."""
    from runtime.mvp_runtime.crypto.live_position import live_position_path

    path = live_position_path(symbol, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    position = {"stage": "live", "status": "OPEN", "symbol": symbol, "direction": "LONG",
                "quantity": 0.002, "entry_price": 100.0, "notional_usdt": 0.2,
                "opened_at_utc": "2026-07-27T00:00:00Z", "position_id": f"live-{symbol}"}
    position.update(fields)
    path.write_text(json.dumps(position), encoding="utf-8")
    return position


def test_a_live_position_pulls_in_its_own_timeframe_not_the_default(tmp_path):
    """The context that owns a live position's clock is its own timeframe, so that context has to
    be guaranteed to run. This used to add the symbol only when it was otherwise unvisited, and
    then at the DEFAULT timeframe — so a 4h position on a symbol still routed at 15m was serviced
    by a cycle counting 15m bars against a 4h rule."""
    _install_pool(tmp_path, _always_spec("S_ETH_15m", "ETHUSDT", timeframe="15m"))
    _book_live_position(tmp_path, "ETHUSDT", timeframe="4h", max_holding_bars=12)

    contexts = pool_cycle_contexts(tmp_path)
    assert ("ETHUSDT", "4h") in contexts, "the position's own timeframe never got a cycle"
    assert ("ETHUSDT", "15m") in contexts, "the routable context was dropped"


def test_a_legacy_live_position_pulls_in_the_default_timeframe(tmp_path):
    """No stored timeframe means no owner to name, so `position_timing_context` hands it to the
    default — and this is what makes that default a context that actually runs."""
    _book_live_position(tmp_path, "ETHUSDT")  # no `timeframe` field: pre-exit-terms record
    assert pool_cycle_contexts(tmp_path, default_timeframe="1d") == [("ETHUSDT", "1d")]


def test_pool_cycle_skips_a_bad_symbol_without_starving_the_rest(tmp_path):
    # A pool symbol that parses as a spec but cannot be collected (INVALID_SYMBOL)
    # must not abort the batch — the other symbol still runs.
    _install_pool(tmp_path, _always_spec("S_BTC", "BTCUSDT"), _always_spec("S_BAD", "btc"))
    store = RealPaperStore(root=tmp_path, authorization=_AUTH)
    summary = _pool_cycle(tmp_path, FakeExchangeCollector(), store)

    assert [s["symbol"] for s in summary["skipped"]] == ["btc"]
    assert summary["skipped"][0]["reason_code"] == "INVALID_SYMBOL"
    assert {r["symbol"] for r in summary["cycles"]} == {"BTCUSDT"}
    assert load_open_position(CTX, tmp_path) is not None


def test_pool_cycle_falls_back_to_default_when_nothing_to_do(tmp_path):
    # Empty pool, no open positions: still run one heartbeat cycle (data collection).
    summary = _pool_cycle(tmp_path, FakeExchangeCollector())
    assert summary["contexts"] == [{"symbol": "BTCUSDT", "timeframe": "1d"}]
    assert len(summary["cycles"]) == 1


def test_pool_cycle_kill_propagates_and_stops_the_fan_out(tmp_path):
    _install_pool(tmp_path, _always_spec("S_BTC", "BTCUSDT"), _always_spec("S_ETH", "ETHUSDT"))
    store = ControlStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        json.dumps(ControlState(mode=control.KILLED, updated_by="op", updated_at=NOW, reason="t").as_record()),
        encoding="utf-8",
    )
    with pytest.raises(ToolBlocked) as exc:
        run_pool_cycle(collector=FakeExchangeCollector(), store=DryRunPaperStore(),
                       now=NOW, root=tmp_path, control_store=store)
    assert exc.value.reason_code == "RUNTIME_KILLED"


def test_the_cycle_carries_a_live_leg_that_is_inert_without_a_grant(tmp_path, monkeypatch):
    """LP5.3 step 3 wired a live leg into the cycle. On every machine that has not been through
    the operator checklist it must report DISABLED and change nothing — the record grows live
    fields, and the paper cycle behaves exactly as it did before the wiring existed."""
    monkeypatch.delenv("MVP_LIVE_TRADING", raising=False)
    _install_pool(tmp_path, _always_spec("S_BTC", "BTCUSDT"))
    record = run_crypto_cycle(
        collector=FakeExchangeCollector(), store=DryRunPaperStore(), now=NOW, root=tmp_path,
    )
    assert record["live_route_status"] == "DISABLED"
    assert record["live_opened"] is None and record["live_settled"] is None
    assert record["live_halt"] is False
    # The status line stays exactly as it was: a DISABLED leg is every developer machine, and
    # printing it on every line would train the reader to skip the field that matters.
    assert "live=" not in cycle_status_line(record)


def test_a_live_incident_stops_the_fan_out_and_names_what_never_ran(tmp_path, monkeypatch):
    """Per-context isolation is right for paper and wrong for real money: a position this
    runtime cannot account for must stop the other contexts from opening under that
    uncertainty. The contexts that never ran are named rather than silently missing."""
    import runtime.mvp_runtime.crypto.cycle as cycle_module

    _install_pool(tmp_path, _always_spec("S_BTC", "BTCUSDT"), _always_spec("S_ETH", "ETHUSDT"))
    calls: list[str] = []

    def _fake_cycle(*, symbol, timeframe, **kw):
        calls.append(symbol)
        incident = symbol == "BTCUSDT"
        return {
            "symbol": symbol, "timeframe": timeframe,
            "verdict_status": "ALLOW", "route_status": "NO_ENTRY",
            "live_halt": incident,
            "live_route_status": "INCIDENT" if incident else "HELD",
            "live_reason_codes": ["LIVE_VENUE_CLOSE_UNSETTLEABLE"] if incident else [],
        }

    monkeypatch.setattr(cycle_module, "run_crypto_cycle", _fake_cycle)
    summary = run_pool_cycle(
        collector=FakeExchangeCollector(), store=DryRunPaperStore(), now=NOW, root=tmp_path,
    )

    assert calls == ["BTCUSDT"], "the fan-out continued past a live incident"
    assert summary["live_halt"]["symbol"] == "BTCUSDT"
    assert summary["live_halt"]["reason_codes"] == ["LIVE_VENUE_CLOSE_UNSETTLEABLE"]
    assert summary["unvisited"] == [{"symbol": "ETHUSDT", "timeframe": "1d"}]
    assert "LIVE HALT at BTCUSDT" in pool_cycle_status_line(summary)


def test_pool_cycle_status_line_lists_every_context(tmp_path):
    _install_pool(tmp_path, _always_spec("S_BTC", "BTCUSDT"), _always_spec("S_ETH", "ETHUSDT"))
    summary = _pool_cycle(tmp_path, FakeExchangeCollector(),
                          RealPaperStore(root=tmp_path, authorization=_AUTH))
    line = pool_cycle_status_line(summary)
    assert line.startswith("pool_cycle contexts=2")
    assert "BTCUSDT 1d:" in line and "ETHUSDT 1d:" in line


def test_scheduler_default_fans_out_over_the_pool(tmp_path, monkeypatch):
    monkeypatch.delenv(MARKET_DATA_ENV, raising=False)
    monkeypatch.delenv(PAPER_ENV, raising=False)
    _install_pool(tmp_path, _always_spec("S_BTC", "BTCUSDT"), _always_spec("S_ETH", "ETHUSDT"))
    schedule = build_schedule(kind=KIND_CRYPTO, request="", interval_seconds=900,
                              created_by="op", now="2026-07-22T11:00:00Z")
    store = ScheduleStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.add(schedule)
    ledger = LedgerStore(tmp_path / LEDGER_REL)

    summary = run_due(store, now="2026-07-22T13:00:00Z", control_store=ControlStore(tmp_path),
                      ledger=ledger, repo_root=tmp_path)
    assert summary["fired"] == 1
    assert "pool_cycle contexts=2" in summary["results"][0]["status"]
    rows = [json.loads(line) for line in
            (tmp_path / LEDGER_REL / RECORDS_FILE).read_text(encoding="utf-8").splitlines()]
    symbols = {r["record"]["symbol"] for r in rows if r["kind"] == "crypto_cycle"}
    assert symbols == {"BTCUSDT", "ETHUSDT"}


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


# --- LP5.3: the risk guard must see LIVE losses -------------------------------

def _write_live_outcomes(root, rows):
    """Append live outcome records the way the gated ledger does, self-hash included."""
    from runtime.mvp_runtime.crypto.live_pnl import build_live_outcome_record, state_dir

    target = state_dir(root)
    target.mkdir(parents=True, exist_ok=True)
    records = [build_live_outcome_record(**row) for row in rows]
    with open(target / "live_outcomes.jsonl", "a", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return records


def _live_loss(i, *, risk=20.0, when=NOW):
    return {
        "realized_pnl_usdt": -20.0, "symbol": "BTCUSDT", "side": "SELL", "quantity": 0.001,
        "position_id": f"live_p{i}", "risk_usdt": risk, "now": when,
    }


def test_live_losses_reach_the_risk_guard(tmp_path):
    """The gap the executing leg would otherwise open: live outcomes live in their own store,
    so the paper provenance split never sees them. Without this the breaker would ignore the
    only losses that cost real money."""
    _install_pool(tmp_path, _always_spec())
    _write_live_outcomes(tmp_path, [_live_loss(i) for i in range(1, 4)])
    record = _cycle(tmp_path, FakeExchangeCollector())
    assert record["verdict_status"] == "NO_NEW_POSITION"
    assert "max_consecutive_losses_breached" in record["verdict_problems"]


def test_a_live_loss_with_no_recorded_risk_is_excluded_not_read_as_a_breakeven(tmp_path):
    """LP5.4's exclusion rule reaching the cycle. `guards._closed_rows` reads a missing
    result_R as 0.0 — a BREAKEVEN — so an R-less live loss would SHORTEN a loss streak. It is
    dropped from the R-based guard and the drop is surfaced."""
    _install_pool(tmp_path, _always_spec())
    _write_live_outcomes(tmp_path, [_live_loss(1), _live_loss(2), _live_loss(3, risk=None)])
    record = _cycle(tmp_path, FakeExchangeCollector())
    assert "LIVE_OUTCOMES_EXCLUDED_FROM_RISK_GUARD" in record["reason_codes"]


def test_an_unreadable_live_history_fails_the_guard_closed(tmp_path):
    """A history that cannot prove itself must not be allowed to argue the breaker is clear."""
    from runtime.mvp_runtime.crypto.live_pnl import state_dir

    _install_pool(tmp_path, _always_spec())
    target = state_dir(tmp_path)
    target.mkdir(parents=True, exist_ok=True)
    (target / "live_outcomes.jsonl").write_text('{"not":"hashed"}\n', encoding="utf-8")
    record = _cycle(tmp_path, FakeExchangeCollector())
    assert record["verdict_status"] == "NO_NEW_POSITION"
    assert "LIVE_HISTORY_TAMPERED" in record["reason_codes"]


def test_no_live_history_changes_nothing(tmp_path):
    """The common case today: zero live outcomes, so this is a no-op."""
    _install_pool(tmp_path, _always_spec())
    record = _cycle(tmp_path, FakeExchangeCollector())
    assert record["verdict_status"] == "ALLOW"
    assert "LIVE_OUTCOMES_EXCLUDED_FROM_RISK_GUARD" not in record["reason_codes"]


# --- what a fan-out actually asks the venue (2026-07-29) ---------------------------------------

class _FeedCountingCollector(FakeExchangeCollector):
    """FakeExchangeCollector plus the symbol-scoped feeds, counting every real call."""

    def __init__(self, extra_candle: dict | None = None):
        super().__init__(extra_candle)
        self.calls: list[tuple] = []

    def collect(self, symbol, timeframe, *, limit, timeout_seconds):
        self.calls.append(("collect", symbol, timeframe, limit))
        return super().collect(symbol, timeframe, limit=limit, timeout_seconds=timeout_seconds)

    def funding_history(self, symbol, *, records, timeout_seconds):
        self.calls.append(("funding_history", symbol))
        return [{"timestamp": "2026-07-01T00:00:00Z", "funding_rate": 0.0001}]


class _CountingLiquidationFeed:
    feed_id = "counting"

    def __init__(self):
        self.calls: list[tuple] = []

    def liquidation_history(self, symbol, *, days, timeout_seconds):
        self.calls.append(("liquidation_history", symbol))
        return []

    def open_interest_history(self, symbol, *, days, timeout_seconds, **kwargs):
        self.calls.append(("open_interest_history", symbol))
        return []


def test_a_fan_out_asks_each_symbol_scoped_question_once(tmp_path):
    """Funding, liquidations and open interest are keyed by SYMBOL at the venue while a cycle is
    keyed by (symbol, timeframe). Two symbols across four timeframes used to be eight requests
    each where two answer — 4x, every fire, on a scheduler that runs everything sequentially."""
    from runtime.mvp_runtime.crypto.market_data import PerRunFeedCache

    _install_pool(
        tmp_path,
        *[_always_spec(f"S_{sym}_{tf}", sym, tf)
          for sym in ("BTCUSDT", "ETHUSDT") for tf in ("15m", "1h", "4h", "1d")],
    )
    inner_collector, inner_feed = _FeedCountingCollector(), _CountingLiquidationFeed()
    _pool_cycle(tmp_path, PerRunFeedCache(inner_collector),
                liquidation_feed=PerRunFeedCache(inner_feed))

    def per_symbol(calls, name):
        return [c[1] for c in calls if c[0] == name]

    assert len(per_symbol(inner_collector.calls, "funding_history")) == 2
    assert sorted(per_symbol(inner_collector.calls, "funding_history")) == ["BTCUSDT", "ETHUSDT"]
    assert len(per_symbol(inner_feed.calls, "liquidation_history")) == 2
    # Two per symbol, not one, and that is correct: `attach_feeds` reads the DAILY open-interest
    # series (memoized here, 4 -> 1) while `oi_store.record_intraday_oi` reads the HOURLY one
    # (`interval="1hour"`) into the store the runtime keeps for itself. Different intervals are
    # different questions, so the memo must not collapse them — and `oi_store` has always had its
    # own once-per-hour-per-symbol throttle for exactly the fan-out this memo now covers for the
    # other three feeds.
    #
    # The hourly leg is no longer scoped to the VISITED symbols: `accumulate_open_interest_cohort`
    # sweeps the declared cohort, because a retention store's scope cannot be a side effect of
    # routing (the rule the positioning sweep already follows — vendor retention is ~84 days
    # against a 500-day replay, so an hour not recorded is gone). So the two routed symbols are
    # asked twice each (daily + hourly) and the four cohort members this pool never routes are
    # asked once each, for the hourly store only. The throttle is visible in that split: BTC and
    # ETH are NOT asked a third time, because the sweep runs after `attach_feeds` in the same
    # hour and reads `skipped_fresh`.
    calls = sorted(per_symbol(inner_feed.calls, "open_interest_history"))
    assert len(calls) == 8
    assert calls == [
        "BNBUSDT", "BTCUSDT", "BTCUSDT", "DOGEUSDT", "ETHUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"
    ]


def test_the_candle_windows_a_fan_out_shares_are_fetched_once(tmp_path):
    """`attach_htf` fetches one step up the ladder, and that step is itself a routed context — so
    a pool routing 15m/1h/4h collects 1h and 4h twice per fire. Both windows end at the same last
    closed candle, so the deeper one answers both."""
    from runtime.mvp_runtime.crypto.market_data import PerRunFeedCache

    _install_pool(tmp_path, *[_always_spec(f"S_{tf}", "BTCUSDT", tf)
                              for tf in ("15m", "1h", "4h")])
    inner = _FeedCountingCollector()
    _pool_cycle(tmp_path, PerRunFeedCache(inner))

    fetched = [(c[1], c[2]) for c in inner.calls if c[0] == "collect"]
    assert len(fetched) == len(set(fetched)), f"the same window was fetched twice: {fetched}"


def test_the_memo_does_not_let_one_context_read_anothers_symbol(tmp_path):
    """The memo keys on the symbol, so this is arithmetic rather than an assumption — but it is
    the failure that would be silent and expensive, so it is pinned."""
    from runtime.mvp_runtime.crypto.market_data import PerRunFeedCache

    _install_pool(tmp_path, _always_spec("S_BTC", "BTCUSDT", "1d"),
                  _always_spec("S_ETH", "ETHUSDT", "1d"))
    inner = _FeedCountingCollector()
    summary = _pool_cycle(tmp_path, PerRunFeedCache(inner))
    assert {r["symbol"] for r in summary["cycles"]} == {"BTCUSDT", "ETHUSDT"}
    for record in summary["cycles"]:
        assert record["collection"]["symbol"] == record["symbol"]


# --- the fan-out's order is part of its behaviour (2026-07-29) ---------------------------------
#
# The fan-out runs sequentially and its scarce resources are consumed in order: two live slots,
# twenty paper ones, and a live incident that stops it outright. Sorting alphabetically made all
# three alphabetical — BNBUSDT took the live slot before BTCUSDT every fire, whatever either of
# them signalled, and a halt stranded whatever sorted late.

def _scored_pool(root, *entries):
    """Install a pool whose entries carry distinct champion scores."""
    pool.install_active_pool(
        {"active_strategies": [
            {"strategy_id": spec["strategy_id"], "status": "PAPER_ACTIVE",
             "champion_score": score, "strategy_spec": spec}
            for spec, score in entries
        ]},
        root=root,
    )


def test_contexts_are_visited_best_evidence_first_not_alphabetically(tmp_path):
    """The slot arbitration. A score is not a signal, so this does not promise the best entry
    wins — it replaces an ordering that correlates with nothing by one that correlates with the
    evidence the pool was promoted on."""
    _scored_pool(
        tmp_path,
        (_always_spec("S_AAA", "AAAUSDT", "1d"), 0.20),
        (_always_spec("S_BTC", "BTCUSDT", "1d"), 0.90),
        (_always_spec("S_ZZZ", "ZZZUSDT", "1d"), 0.55),
    )
    assert pool_cycle_contexts(tmp_path) == [
        ("BTCUSDT", "1d"), ("ZZZUSDT", "1d"), ("AAAUSDT", "1d"),
    ]


def test_a_context_holding_a_live_position_is_visited_before_any_new_entry(tmp_path):
    """Real money first. Settling or protecting an open live position is the most urgent thing a
    fire does, and a live incident halts the fan-out — so a live context that sorted late used to
    be the one a halt stranded."""
    _scored_pool(tmp_path, (_always_spec("S_BTC", "BTCUSDT", "1d"), 0.99))
    _book_live_position(tmp_path, "ZZZUSDT", timeframe="1d", max_holding_bars=12)

    contexts = pool_cycle_contexts(tmp_path)
    assert contexts[0] == ("ZZZUSDT", "1d"), f"the live position was not visited first: {contexts}"


def test_a_context_holding_a_paper_position_outranks_an_empty_one(tmp_path):
    """Same rule, lower stakes: a book that can settle goes before a book that can only open."""
    _scored_pool(
        tmp_path,
        (_always_spec("S_BTC", "BTCUSDT", "1d"), 0.99),
        (_always_spec("S_ETH", "ETHUSDT", "1d"), 0.10),
    )
    store = RealPaperStore(root=tmp_path, authorization=_AUTH)
    _pool_cycle(tmp_path, FakeExchangeCollector(), store)   # opens both books
    assert load_open_position(ETH_CTX, tmp_path) is not None

    # Now demote BTC's book away by emptying it, leaving ETH holding and BTC merely routable.
    paper.RealPaperStore(root=tmp_path, authorization=_AUTH).clear_position(CTX)
    contexts = pool_cycle_contexts(tmp_path)
    assert contexts[0] == ("ETHUSDT", "1d"), f"the open book was not visited first: {contexts}"


def test_the_order_is_deterministic_when_scores_tie(tmp_path):
    """A fan-out that reorders itself between fires would make every ledger comparison useless."""
    _scored_pool(
        tmp_path,
        (_always_spec("S_B", "BBBUSDT", "1d"), 0.5),
        (_always_spec("S_A", "AAAUSDT", "1d"), 0.5),
        (_always_spec("S_C", "CCCUSDT", "1d"), 0.5),
    )
    first = pool_cycle_contexts(tmp_path)
    assert first == [("AAAUSDT", "1d"), ("BBBUSDT", "1d"), ("CCCUSDT", "1d")]
    assert first == pool_cycle_contexts(tmp_path)


def test_a_scoreless_entry_is_visited_last_never_dropped(tmp_path):
    """Nothing to argue for going first is not a reason to be skipped."""
    _scored_pool(
        tmp_path,
        (_always_spec("S_NONE", "AAAUSDT", "1d"), None),
        (_always_spec("S_BTC", "BTCUSDT", "1d"), 0.4),
    )
    contexts = pool_cycle_contexts(tmp_path)
    assert ("AAAUSDT", "1d") in contexts
    assert contexts.index(("BTCUSDT", "1d")) < contexts.index(("AAAUSDT", "1d"))


def test_every_context_still_runs_whatever_the_order(tmp_path):
    """Ordering must never become filtering — a demoted strategy's book still has to settle."""
    _scored_pool(
        tmp_path,
        (_always_spec("S_A", "AAAUSDT", "1d"), 0.1),
        (_always_spec("S_B", "BBBUSDT", "1d"), 0.9),
    )
    _book_live_position(tmp_path, "ZZZUSDT", timeframe="4h")
    assert set(pool_cycle_contexts(tmp_path)) == {
        ("AAAUSDT", "1d"), ("BBBUSDT", "1d"), ("ZZZUSDT", "4h"),
    }


# --- the registered breaker limits (crypto_risk_limits.v0.1) -------------------

def test_no_registered_limits_judges_on_the_defaults(tmp_path):
    """The unconfigured machine, which is every machine until an operator registers a record."""
    from runtime.mvp_runtime.crypto import guards

    _install_pool(tmp_path, _always_spec())
    record = _cycle(tmp_path, FakeExchangeCollector())
    assert record["verdict_status"] == "ALLOW"
    assert record["risk_limits"]["source"] == guards.SOURCE_DEFAULT


def test_a_registered_limit_reaches_the_cycle_verdict(tmp_path):
    """The wiring under test: a record on disk is what the cycle's breaker actually judges on."""
    from runtime.mvp_runtime.crypto import risk_limits

    _install_pool(tmp_path, _always_spec())
    built = risk_limits.build_risk_limits_record(
        limits={"risk_per_trade": 0.01, "daily_max_loss_r": -1.0, "weekly_max_loss_r": -5.0,
                "max_consecutive_losses": 5, "max_drawdown_pct": -10.0},
        valid_from="2026-07-01T00:00:00Z", valid_until="2026-12-31T00:00:00Z",
        registered_by="thomas", registered_at="2026-07-01T00:00:00Z")
    risk_limits.write_registered_limits(built, root=tmp_path)

    record = _cycle(tmp_path, FakeExchangeCollector())
    limits = record["risk_limits"]
    assert limits["source"] == "registered" and limits["limits_id"] == built["limits_id"]
    assert limits["daily_max_loss_r"] == -1.0 and limits["max_consecutive_losses"] == 5


def test_a_tampered_limits_record_fails_the_cycle_guard_closed(tmp_path):
    """A record that cannot prove itself must not fall back to the defaults.

    The fallback is the fail-open direction: an operator who tightened a breaker would have it
    silently loosened back to the default by the very failure meant to be conservative."""
    from runtime.mvp_runtime.crypto import risk_limits
    from runtime.mvp_runtime.crypto.live_pnl import state_dir

    _install_pool(tmp_path, _always_spec())
    target = state_dir(tmp_path)
    target.mkdir(parents=True, exist_ok=True)
    (target / "crypto_risk_limits.json").write_text('{"limits":{"daily_max_loss_r":-6.0}}\n',
                                                    encoding="utf-8")
    record = _cycle(tmp_path, FakeExchangeCollector())
    assert record["verdict_status"] == "NO_NEW_POSITION"
    assert risk_limits.LIMITS_TAMPERED in record["reason_codes"]
    # Fail-closed where the record governs. `crypto_risk_limits` configures the LOSS breakers,
    # and after the verdict split those bind the live leg alone — so a record that cannot prove
    # itself refuses real money and leaves the paper leg, which never consulted it, alone. The
    # direction that matters is unchanged: the tampered record buys nothing it could not buy
    # before, and widens nothing.
    assert record["paper_verdict_status"] == "ALLOW"


def test_a_lapsed_limits_record_fails_the_cycle_guard_closed(tmp_path):
    """Relaxations expire; a lapsed one refuses rather than reverting to the defaults."""
    from runtime.mvp_runtime.crypto import risk_limits

    _install_pool(tmp_path, _always_spec())
    built = risk_limits.build_risk_limits_record(
        limits={"risk_per_trade": 0.01, "daily_max_loss_r": -1.0, "weekly_max_loss_r": -5.0,
                "max_consecutive_losses": 3, "max_drawdown_pct": -10.0},
        valid_from="2026-01-01T00:00:00Z", valid_until="2026-02-01T00:00:00Z",
        registered_by="thomas", registered_at="2026-01-01T00:00:00Z")
    risk_limits.write_registered_limits(built, root=tmp_path)

    record = _cycle(tmp_path, FakeExchangeCollector())
    assert record["verdict_status"] == "NO_NEW_POSITION"
    assert risk_limits.LIMITS_EXPIRED in record["reason_codes"]
    assert "risk_limits_unusable" in record["verdict_problems"]


# --- the drawdown baseline's routable re-check (#405) --------------------------

def test_an_unreadable_pool_leaves_the_drawdown_baseline_unverified(tmp_path, monkeypatch):
    """The fail-closed half of the rebase, at the level that actually reads the pool. A tampered
    pool degrades routing to "trade nothing" — and it must NOT arrive at the guard as an empty
    routable set, which would read as "every retired lineage is confirmed retired" and release
    the whole exclusion. A filesystem error must never clear a real-money brake."""
    from runtime.mvp_runtime.crypto import cycle as cycle_mod, pool as pool_mod

    seen: dict[str, object] = {"called": False}
    real_guard = cycle_mod.run_risk_guard

    def _capture(outcomes, **kw):
        seen["called"] = True
        seen["routable"] = kw.get("routable_strategy_ids")
        return real_guard(outcomes, **kw)

    def _explode(root=None):
        raise ToolError("STRATEGY_POOL_INVALID", "stubbed: unreadable pool")

    monkeypatch.setattr(cycle_mod, "run_risk_guard", _capture)
    monkeypatch.setattr(pool_mod, "load_active_pool", _explode)

    record = _cycle(tmp_path, FakeExchangeCollector())
    assert seen["called"], "the guard never ran"
    assert seen["routable"] is None, "an unreadable pool must be UNKNOWN, never the empty set"
    assert "STRATEGY_POOL_INVALID" in record["reason_codes"]


def test_a_readable_pool_hands_the_guard_its_routable_ids(tmp_path, monkeypatch):
    from runtime.mvp_runtime.crypto import cycle as cycle_mod

    seen: dict[str, object] = {}
    real_guard = cycle_mod.run_risk_guard

    def _capture(outcomes, **kw):
        seen["routable"] = kw.get("routable_strategy_ids")
        return real_guard(outcomes, **kw)

    monkeypatch.setattr(cycle_mod, "run_risk_guard", _capture)
    spec = _always_spec()
    _install_pool(tmp_path, spec)
    _cycle(tmp_path, FakeExchangeCollector())
    assert seen["routable"] == {spec["strategy_id"]}

# --- Gate 0 reaches the live leg ----------------------------------------------

def test_the_live_leg_is_handed_gate_0(tmp_path, monkeypatch):
    """The wiring, asserted at the cycle rather than only in `live_entry`: C6 has computed
    `live_candidate_eligible` every cycle since it shipped and no caller read it. It also pins
    the ORDERING — the report is built at step 5 and the live leg runs after it, so a report
    that had stayed below the live leg would arrive here as `None`."""
    from runtime.mvp_runtime.crypto import cycle as cycle_mod

    seen: dict[str, object] = {}

    def _capture(**kw):
        seen.update(kw)
        return {"live_route_status": "DISABLED", "live_opened": None, "live_settled": None,
                "live_reason_codes": [], "halt": False}

    monkeypatch.setattr(cycle_mod, "run_live_leg", _capture)
    _install_pool(tmp_path, _always_spec())
    record = _cycle(tmp_path, FakeExchangeCollector())

    # Gate 0 is no longer handed to the live leg (removed 2026-08-03). The assertion flips:
    # what this now guards is that nothing quietly re-wires it, and that the MEASUREMENT
    # survived the removal — the report is still built and still on the record, because "has
    # this pool shown an edge net of costs" is exactly what the operator checklist asks.
    assert "live_candidate" not in seen, "the live leg was handed a gate that no longer exists"
    assert record["report_status"] is not None
    assert "live_candidate_eligible" in record


# --- the LIVE PERMISSION phase runs before the entry (#615 §5) ------------------------------

def _install_live_armed_pool(root, spec, *, candidate_id="cand-1"):
    """The pool `_install_pool` builds, plus the one field that lets a strategy spend money."""
    pool.install_active_pool(
        {"active_strategies": [{
            "strategy_id": spec["strategy_id"], "status": "PAPER_ACTIVE", "champion_score": 0.5,
            "strategy_spec": spec, "candidate_id": candidate_id,
            "strategy_rule_hash": "h1", "generation_id": "GEN-1",
            pool.LIVE_TIER_FIELD: pool.LIVE_TIER_LIVE,
        }]},
        root=root,
    )


def _seed_live_losses(root, *, candidate_id="cand-1", count=2, risk=10.0):
    """`count` consecutive live losses on one lineage — enough to spend the allowance.

    Written through `build_live_outcome_record` so each row is self-hashed and shaped exactly as
    the exit path writes it: a hand-made row fails the verified read, which is also a refusal but
    never the one these tests mean."""
    from runtime.mvp_runtime.crypto.live_pnl import build_live_outcome_record
    from runtime.mvp_runtime.crypto.live_pnl import state_dir as live_state_dir

    target = live_state_dir(root)
    target.mkdir(parents=True, exist_ok=True)
    lines = []
    for i in range(count):
        record = build_live_outcome_record(
            realized_pnl_usdt=-1.5 * risk, symbol="BTCUSDT", side="SELL", quantity=0.001,
            position_id=f"live_p{i}", risk_usdt=risk, candidate_id=candidate_id,
            strategy_rule_hash="h1", strategy_generation_id="GEN-1",
            now=NOW,
        )
        lines.append(json.dumps(record, ensure_ascii=False) + "\n")
    (target / "live_outcomes.jsonl").write_text("".join(lines), encoding="utf-8")


def _leg_recorder(monkeypatch):
    """Capture what the live leg was handed, and let it do nothing."""
    from runtime.mvp_runtime.crypto import cycle as cycle_mod

    seen: dict[str, object] = {}

    def _capture(**kw):
        seen.update(kw)
        return {"live_route_status": "DISABLED", "live_opened": None, "live_settled": None,
                "live_reason_codes": [], "halt": False}

    monkeypatch.setattr(cycle_mod, "run_live_leg", _capture)
    return seen


def test_a_spent_allowance_is_taken_away_before_the_leg_can_spend_it(tmp_path, monkeypatch):
    """The ordering, asserted where it has an effect: on the set the leg is HANDED.

    This block used to run after the leg, which meant a lineage whose allowance was already
    spent still got one more real entry — the leg had run on the un-narrowed set, and the
    disarm only bound the cycle after. Pinned as "the leg cannot see the breached id" rather
    than as a line number, because the property is what must survive a reshuffle."""
    spec = _always_spec()
    _install_live_armed_pool(tmp_path, spec)
    _seed_live_losses(tmp_path)
    seen = _leg_recorder(monkeypatch)

    record = _cycle(tmp_path, FakeExchangeCollector(), RealPaperStore(root=tmp_path, authorization=_AUTH))

    assert seen, "the live leg never ran"
    assert seen["live_routable_strategy_ids"] == set(), (
        "the leg was handed a lineage whose allowance was already spent"
    )
    assert "LIVE_ALLOWANCE_SPENT" in record["reason_codes"]
    assert record["live_allowance"]["blocked_from_live_this_cycle"] == [spec["strategy_id"]]
    # ...and the durable tier move landed too, on a store that can write.
    assert record["live_allowance"]["disarmed"] == 1
    assert pool.live_routable_strategy_ids(pool.load_active_pool(tmp_path)) == set()


def test_an_unspent_allowance_leaves_the_leg_armed(tmp_path, monkeypatch):
    """The control for the test above: without it, a block that narrowed the set unconditionally
    would pass just as well and no lineage could ever trade live."""
    spec = _always_spec()
    _install_live_armed_pool(tmp_path, spec)
    seen = _leg_recorder(monkeypatch)

    record = _cycle(tmp_path, FakeExchangeCollector(), RealPaperStore(root=tmp_path, authorization=_AUTH))

    assert seen["live_routable_strategy_ids"] == {spec["strategy_id"]}
    assert "LIVE_ALLOWANCE_SPENT" not in record["reason_codes"]
    assert record["live_allowance"]["breached"] == []


def test_a_breach_that_cannot_be_persisted_still_refuses_the_entry(tmp_path, monkeypatch):
    """A dry-run store cannot write the tier move. The refusal must not depend on that write.

    The tempting shape — `if breached and store.filesystem_write: disarm(...)` — detects the
    breach, records the reason code, and hands the leg an un-narrowed set anyway, leaving the
    money path relying on some other gate happening to catch it. That is not a premise a live
    door may hold."""
    spec = _always_spec()
    _install_live_armed_pool(tmp_path, spec)
    _seed_live_losses(tmp_path)
    seen = _leg_recorder(monkeypatch)

    record = _cycle(tmp_path, FakeExchangeCollector(), DryRunPaperStore())

    assert seen["live_routable_strategy_ids"] == set(), (
        "a breach the runtime could not persist still armed the leg"
    )
    assert "LIVE_ALLOWANCE_SPENT" in record["reason_codes"]
    # Not attempted, and honestly reported as not having landed.
    assert record["live_allowance"]["disarmed"] is None
    assert pool.live_routable_strategy_ids(pool.load_active_pool(tmp_path)) == {spec["strategy_id"]}


def test_a_tier_write_that_refuses_still_refuses_the_entry(tmp_path, monkeypatch):
    """The other half of the same rule: the store CAN write and the write fails anyway — a
    locked pool, a raising writer. The in-cycle block is identical, and the reason code is what
    tells the two apart on the record."""
    from runtime.mvp_runtime.crypto import cycle as cycle_mod

    spec = _always_spec()
    _install_live_armed_pool(tmp_path, spec)
    _seed_live_losses(tmp_path)
    seen = _leg_recorder(monkeypatch)

    def _explode(ids, **kw):
        raise ToolError("STRATEGY_POOL_LOCKED", "stubbed: another writer holds the pool")

    monkeypatch.setattr(cycle_mod.pool, "disarm_live_tier", _explode)
    record = _cycle(tmp_path, FakeExchangeCollector(), RealPaperStore(root=tmp_path, authorization=_AUTH))

    assert seen["live_routable_strategy_ids"] == set()
    assert "LIVE_ALLOWANCE_SPENT" in record["reason_codes"]
    assert "STRATEGY_POOL_LOCKED" in record["reason_codes"]
    assert record["live_allowance"]["disarmed"] is None


def test_an_unreadable_live_history_disarms_every_lineage_before_the_leg(tmp_path, monkeypatch):
    """The conservative direction, pinned at the cycle rather than only at the evaluator: a
    history that cannot prove itself must not be able to argue an allowance is still unspent —
    and it must not do so a cycle late."""
    from runtime.mvp_runtime.crypto.live_pnl import state_dir as live_state_dir

    spec = _always_spec()
    _install_live_armed_pool(tmp_path, spec)
    target = live_state_dir(tmp_path)
    target.mkdir(parents=True, exist_ok=True)
    (target / "live_outcomes.jsonl").write_text("not json\n", encoding="utf-8")
    seen = _leg_recorder(monkeypatch)

    _cycle(tmp_path, FakeExchangeCollector(), RealPaperStore(root=tmp_path, authorization=_AUTH))
    assert seen["live_routable_strategy_ids"] == set()


# ---------------------------------------------------------------------------
# ⑧ Pipeline stall detection (dead-man switch)
# ---------------------------------------------------------------------------

class TestCycleIsStalled:
    """``cycle_is_stalled`` mirrors ``data_review.review_loop_is_stalled``:
    the first degraded fire stays quiet, the second consecutive one raises."""

    def test_one_degraded_cycle_is_not_stalled(self):
        from runtime.mvp_runtime.crypto.cycle import cycle_is_stalled
        record = {"degraded": True}
        assert cycle_is_stalled(record, None) is False
        assert cycle_is_stalled(record, "verdict=ALLOW route=matched") is False

    def test_two_consecutive_degraded_cycles_is_stalled(self):
        from runtime.mvp_runtime.crypto.cycle import cycle_is_stalled
        record = {"degraded": True}
        assert cycle_is_stalled(record, "degraded verdict=HOLD route=no_strategies") is True

    def test_stalled_status_is_recognised_on_the_next_fire(self):
        from runtime.mvp_runtime.crypto.cycle import PIPELINE_STALLED, cycle_is_stalled
        record = {"degraded": True}
        assert cycle_is_stalled(record, f"failed:{PIPELINE_STALLED}") is True

    def test_a_healthy_cycle_is_never_stalled(self):
        from runtime.mvp_runtime.crypto.cycle import cycle_is_stalled
        record = {"degraded": False}
        assert cycle_is_stalled(record, "degraded verdict=HOLD") is False


class TestPoolCycleIsStalled:
    """``pool_cycle_is_stalled`` checks that ALL contexts degraded for two
    consecutive fires — one healthy context keeps the pipeline productive."""

    def test_all_degraded_after_healthy_is_not_stalled(self):
        from runtime.mvp_runtime.crypto.cycle import pool_cycle_is_stalled
        summary = {"cycles": [{"degraded": True}, {"degraded": True}]}
        assert pool_cycle_is_stalled(summary, "pool_cycle contexts=2") is False

    def test_all_degraded_after_all_degraded_is_stalled(self):
        from runtime.mvp_runtime.crypto.cycle import pool_cycle_is_stalled
        summary = {"cycles": [{"degraded": True}, {"degraded": True}]}
        assert pool_cycle_is_stalled(summary, "pool_cycle contexts=2 all_degraded | ...") is True

    def test_a_bare_majority_is_stalled(self):
        """Thomas 2026-08-22 moved the rule from `all` to `majority`. Half counts."""
        from runtime.mvp_runtime.crypto.cycle import pool_cycle_is_stalled
        summary = {"cycles": [{"degraded": True}, {"degraded": False}]}
        assert pool_cycle_is_stalled(summary, "pool_cycle contexts=2 majority_degraded") is True

    def test_below_half_is_not_stalled(self):
        from runtime.mvp_runtime.crypto.cycle import pool_cycle_is_stalled
        summary = {"cycles": [{"degraded": True}] + [{"degraded": False}] * 2}
        assert pool_cycle_is_stalled(summary, "pool_cycle contexts=3 majority_degraded") is False

    def test_ten_of_eleven_dead_is_stalled(self):
        """The case the `all` rule left silent, and the reason it was widened."""
        from runtime.mvp_runtime.crypto.cycle import pool_cycle_is_stalled
        summary = {"cycles": [{"degraded": True}] * 10 + [{"degraded": False}]}
        assert pool_cycle_is_stalled(summary, "pool_cycle contexts=11 majority_degraded") is True

    def test_an_older_images_all_degraded_marker_still_arms_the_switch(self):
        """A stall straddling a deploy: the previous fire was recorded by the image that only
        wrote `all_degraded`, and must not silently restart the count."""
        from runtime.mvp_runtime.crypto.cycle import pool_cycle_is_stalled
        summary = {"cycles": [{"degraded": True}, {"degraded": True}]}
        assert pool_cycle_is_stalled(summary, "pool_cycle contexts=2 all_degraded | ...") is True

    def test_stalled_status_is_recognised(self):
        from runtime.mvp_runtime.crypto.cycle import PIPELINE_STALLED, pool_cycle_is_stalled
        summary = {"cycles": [{"degraded": True}]}
        assert pool_cycle_is_stalled(summary, f"failed:{PIPELINE_STALLED}") is True

    def test_empty_cycles_is_not_stalled(self):
        from runtime.mvp_runtime.crypto.cycle import pool_cycle_is_stalled
        assert pool_cycle_is_stalled({"cycles": []}, "pool_cycle all_degraded") is False


class TestPoolCycleStatusLineAllDegraded:
    """The markers on the status line are what make two consecutive degraded fires detectable
    from ``last_status`` alone. ``majority_degraded`` is the one the predicate arms on;
    ``all_degraded`` and ``degraded=N/M`` ride alongside so the operator can tell a total
    outage from a bare majority, which the arming token deliberately cannot."""

    def test_all_degraded_marker_appears_when_every_context_degraded(self):
        from runtime.mvp_runtime.crypto.cycle import pool_cycle_status_line
        summary = {
            "cycles": [
                {"degraded": True, "symbol": "BTCUSDT", "timeframe": "15m",
                 "verdict_status": "HOLD", "paper_verdict_status": "HOLD",
                 "route_status": "no_strategies",
                 "live_route_status": None, "reason_codes": []},
            ],
            "skipped": [], "unvisited": [],
        }
        status = pool_cycle_status_line(summary)
        assert "all_degraded" in status

    def test_marker_absent_when_some_contexts_healthy(self):
        from runtime.mvp_runtime.crypto.cycle import pool_cycle_status_line
        summary = {
            "cycles": [
                {"degraded": True, "symbol": "BTCUSDT", "timeframe": "15m",
                 "verdict_status": "HOLD", "paper_verdict_status": "HOLD",
                 "route_status": "no_strategies",
                 "live_route_status": None, "reason_codes": []},
                {"degraded": False, "symbol": "ETHUSDT", "timeframe": "15m",
                 "verdict_status": "ALLOW", "paper_verdict_status": "ALLOW",
                 "route_status": "matched",
                 "live_route_status": None, "reason_codes": []},
            ],
            "skipped": [], "unvisited": [],
        }
        status = pool_cycle_status_line(summary)
        assert "all_degraded" not in status
        # One of two IS a majority, so the arming token is present where `all_degraded` is not.
        assert "majority_degraded" in status
        assert "degraded=1/2" in status

    def test_below_half_carries_the_count_but_not_the_arming_token(self):
        from runtime.mvp_runtime.crypto.cycle import pool_cycle_status_line

        def ctx(symbol, degraded):
            return {"degraded": degraded, "symbol": symbol, "timeframe": "15m",
                    "verdict_status": "HOLD", "paper_verdict_status": "HOLD",
                    "route_status": "no_strategies",
                    "live_route_status": None, "reason_codes": []}

        status = pool_cycle_status_line({
            "cycles": [ctx("BTCUSDT", True), ctx("ETHUSDT", False), ctx("SOLUSDT", False)],
            "skipped": [], "unvisited": [],
        })
        assert "degraded=1/3" in status
        assert "majority_degraded" not in status
        assert "all_degraded" not in status

    def test_a_healthy_fire_carries_no_degraded_token_at_all(self):
        from runtime.mvp_runtime.crypto.cycle import pool_cycle_status_line
        status = pool_cycle_status_line({
            "cycles": [{"degraded": False, "symbol": "BTCUSDT", "timeframe": "15m",
                        "verdict_status": "ALLOW", "paper_verdict_status": "ALLOW",
                        "route_status": "matched",
                        "live_route_status": None, "reason_codes": []}],
            "skipped": [], "unvisited": [],
        })
        assert "degraded=" not in status
        assert "majority_degraded" not in status


# ---------------------------------------------------------------------------
# The wiring, end to end: does a degraded fire's status line reach the NEXT fire?
# ---------------------------------------------------------------------------

class TestStallDetectionWiring:
    """The classes above pin the two predicates against a hand-written ``previous_status``.
    Nothing pinned the mechanism that supplies it: that ``_execute`` returns the status line,
    that ``record_result`` stores it verbatim, and that the next fire reads it back as
    ``schedule.last_status``. A status line that lost its marker between two fires would leave
    the dead-man switch permanently silent, and every predicate test above would still pass.

    Measured on the live schedule while writing these: across 909 terminal fires over nine days
    no context degraded even once, so `any`, `majority` and `all` would each have fired exactly
    zero times. Production has never exercised this path — a test is the only thing behind it.
    """

    T0 = "2026-07-16T09:00:00Z"
    T1 = "2026-07-16T09:15:00Z"
    T2 = "2026-07-16T09:30:00Z"
    T3 = "2026-07-16T09:45:00Z"

    @staticmethod
    def _record(symbol="BTCUSDT", timeframe="1d", *, degraded=True):
        return {
            "cycle_id": f"cyc_{symbol}_{timeframe}", "symbol": symbol, "timeframe": timeframe,
            "degraded": degraded,
            "reason_codes": ["MARKET_DATA_DEGRADED"] if degraded else [],
            "verdict_status": "NO_ENTRY", "route_status": "NO_ROUTE",
        }

    @pytest.fixture
    def env(self, tmp_path, monkeypatch):
        from runtime.mvp_runtime.crypto import cycle as cycle_mod
        from runtime.mvp_runtime.crypto import market_data
        from runtime.mvp_runtime.crypto import paper as paper_mod
        from runtime.mvp_runtime.scheduler import KIND_CRYPTO, ScheduleStore, build_schedule
        from runtime.mvp_runtime.store import LedgerStore

        # The three Safety-Flag chokepoints the crypto branch selects through. Stubbed to inert
        # objects: this test is about the status string, and the cycle itself is stubbed too.
        monkeypatch.setattr(market_data, "select_market_data_collector", lambda **kw: object())
        monkeypatch.setattr(market_data, "select_liquidation_feed", lambda **kw: object())
        monkeypatch.setattr(paper_mod, "select_paper_store", lambda **kw: object())

        store = ScheduleStore(tmp_path)
        store.add(build_schedule(kind=KIND_CRYPTO, request="", interval_seconds=900,
                                 created_by="op", now=self.T0))
        return {"store": store, "cycle_mod": cycle_mod, "monkeypatch": monkeypatch,
                "ledger": LedgerStore(tmp_path / "ledger"), "control": ControlStore(tmp_path),
                "root": tmp_path}

    @staticmethod
    def _fire(env, now, notifier=None):
        # An explicit root, not None. The crypto branch refreshes the account snapshot on
        # its way past, and `root=None` resolves to the REPO's own state directory — which
        # on the live server is the volume the running containers own. This test is about
        # the status line; it has no business writing there.
        from runtime.mvp_runtime.scheduler import run_due
        return run_due(env["store"], now=now, ledger=env["ledger"],
                       control_store=env["control"], repo_root=env["root"], notifier=notifier)

    @staticmethod
    def _last_status(env):
        return env["store"].list()[0].last_status

    def test_two_all_degraded_fires_stall_and_stay_stalled(self, env):
        summary = {"cycles": [self._record("BTCUSDT"), self._record("ETHUSDT")],
                   "skipped": [], "contexts": 2}
        env["monkeypatch"].setattr(env["cycle_mod"], "run_pool_cycle", lambda **kw: summary)

        first = self._fire(env, self.T1)
        assert first["fired"] == 1 and first["failed"] == 0, "the FIRST degraded fire stays quiet"
        assert "all_degraded" in self._last_status(env), self._last_status(env)

        assert self._fire(env, self.T2)["failed"] == 1, "the SECOND consecutive one must block"
        assert self._last_status(env) == "failed:PIPELINE_STALLED"

        # And it STAYS blocked: fire 3 reads `failed:PIPELINE_STALLED`, not a status line.
        assert self._fire(env, self.T3)["failed"] == 1
        assert self._last_status(env) == "failed:PIPELINE_STALLED"

    def test_the_stall_reaches_the_operator(self, env):
        """Blocking is only useful if somebody is told."""
        summary = {"cycles": [self._record("BTCUSDT")], "skipped": [], "contexts": 1}
        env["monkeypatch"].setattr(env["cycle_mod"], "run_pool_cycle", lambda **kw: summary)
        sent: list[tuple] = []

        self._fire(env, self.T1, notifier=lambda *a: sent.append(a))
        assert sent == [], "a quiet first degraded fire must not page anyone"
        self._fire(env, self.T2, notifier=lambda *a: sent.append(a))
        assert len(sent) == 1
        assert "PIPELINE_STALLED" in " ".join(str(x) for x in sent[0])

    def test_a_healthy_fire_between_two_degraded_ones_resets_the_counter(self, env):
        """CONSECUTIVE degradation, not two degraded fires ever."""
        degraded = {"cycles": [self._record("BTCUSDT")], "skipped": [], "contexts": 1}
        healthy = {"cycles": [self._record("BTCUSDT", degraded=False)], "skipped": [], "contexts": 1}

        env["monkeypatch"].setattr(env["cycle_mod"], "run_pool_cycle", lambda **kw: degraded)
        assert self._fire(env, self.T1)["fired"] == 1
        env["monkeypatch"].setattr(env["cycle_mod"], "run_pool_cycle", lambda **kw: healthy)
        assert self._fire(env, self.T2)["fired"] == 1
        assert "all_degraded" not in self._last_status(env)
        env["monkeypatch"].setattr(env["cycle_mod"], "run_pool_cycle", lambda **kw: degraded)
        assert self._fire(env, self.T3)["fired"] == 1, "degraded after healthy is the FIRST again"

    def test_ten_of_eleven_contexts_dead_now_stalls(self, env):
        """The live pool runs eleven contexts. Under the original ``all()`` rule this exact
        shape stayed silent indefinitely; Thomas widened it to a majority on 2026-08-22."""
        mixed = {"cycles": [self._record(f"S{i}") for i in range(10)]
                           + [self._record("ALIVE", degraded=False)],
                 "skipped": [], "contexts": 11}
        env["monkeypatch"].setattr(env["cycle_mod"], "run_pool_cycle", lambda **kw: mixed)
        assert self._fire(env, self.T1)["fired"] == 1, "the first one still stays quiet"
        assert "degraded=10/11" in self._last_status(env), self._last_status(env)
        assert self._fire(env, self.T2)["failed"] == 1
        assert self._last_status(env) == "failed:PIPELINE_STALLED"

    def test_a_minority_of_degraded_contexts_still_never_stalls(self, env):
        """The other side of the new rule: below half is noise, however long it persists."""
        minority = {"cycles": [self._record("DEAD")]
                              + [self._record(f"S{i}", degraded=False) for i in range(10)],
                    "skipped": [], "contexts": 11}
        env["monkeypatch"].setattr(env["cycle_mod"], "run_pool_cycle", lambda **kw: minority)
        assert self._fire(env, self.T1)["fired"] == 1
        assert self._fire(env, self.T2)["fired"] == 1
        assert self._fire(env, self.T3)["fired"] == 1
        assert "degraded=1/11" in self._last_status(env)

    def test_single_symbol_override_stalls_on_two_degraded_fires(self, env, tmp_path):
        """The ``SYMBOL TIMEFRAME`` override runs a different branch with a different prefix rule
        (``startswith("degraded")`` rather than the pool's marker)."""
        from runtime.mvp_runtime.scheduler import KIND_CRYPTO, ScheduleStore, build_schedule

        store = ScheduleStore(tmp_path / "single")
        store.add(build_schedule(kind=KIND_CRYPTO, request="BTCUSDT 1d", interval_seconds=900,
                                 created_by="op", now=self.T0))
        env["monkeypatch"].setattr(env["cycle_mod"], "run_crypto_cycle",
                                   lambda **kw: self._record("BTCUSDT", "1d"))
        env["store"] = store

        assert self._fire(env, self.T1)["fired"] == 1
        assert self._last_status(env).startswith("degraded"), self._last_status(env)
        assert self._fire(env, self.T2)["failed"] == 1
        assert self._last_status(env) == "failed:PIPELINE_STALLED"


# --- the fan-out's one verified outcome read -------------------------------------

def test_a_handed_down_outcome_read_is_not_re_verified(tmp_path, monkeypatch):
    """`paper_outcomes` is the fan-out's single verified read of the outcome ledger; a
    context receiving it must not pay the per-row SHA256 of that file again."""
    from runtime.mvp_runtime.crypto import cycle as cycle_mod

    _install_pool(tmp_path, _always_spec())
    monkeypatch.setattr(
        cycle_mod, "read_outcomes",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("context re-read the ledger")),
    )
    record = _cycle(tmp_path, FakeExchangeCollector(), paper_outcomes=[])
    assert record["report_status"] is not None, "the report ran on the handed-down read"


def test_the_fanout_pays_one_verified_read_until_something_settles(tmp_path, monkeypatch):
    """Two settle-free contexts share one verified read; a settlement evicts the snapshot so
    every later context reads fresh — the exact freshness the per-context read provided,
    paid only when the file actually changed."""
    from runtime.mvp_runtime.crypto import cycle as cycle_mod

    _install_pool(tmp_path, _always_spec("S_BTC", "BTCUSDT"), _always_spec("S_ETH", "ETHUSDT"))
    store = RealPaperStore(root=tmp_path, authorization=_AUTH)
    calls = {"n": 0}
    real_read = cycle_mod.read_outcomes

    def _counting(*args, **kwargs):
        calls["n"] += 1
        return real_read(*args, **kwargs)

    monkeypatch.setattr(cycle_mod, "read_outcomes", _counting)

    summary = _pool_cycle(tmp_path, FakeExchangeCollector(), store)
    assert [c["symbol"] for c in summary["cycles"]] == ["BTCUSDT", "ETHUSDT"]
    assert all(c["settled"] is None for c in summary["cycles"])
    assert calls["n"] == 1, "a settle-free fan-out pays exactly one verified read"

    calls["n"] = 0
    sl_candle = {"high": 100.5, "low": 96.0, "close": 98.0}
    summary = _pool_cycle(tmp_path, FakeExchangeCollector(extra_candle=sl_candle), store,
                          now="2026-07-23T12:00:00Z")
    assert sum(1 for c in summary["cycles"] if c["settled"]) == 2
    assert calls["n"] == 2, (
        "the fan-out's snapshot plus one fresh read after the first settlement — "
        "the second settling context must not be served the stale snapshot"
    )