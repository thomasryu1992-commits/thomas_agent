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
    """The same fixture that used to prove a guard block stops the paper entry and shadows it.

    It proves the opposite now, deliberately: six own losses trip the breaker, the live gate
    refuses on it, and paper goes on trading. What used to be recorded as a shadow is recorded
    as a settled outcome — the sample the ladder needs, which the breaker was suppressing."""
    from runtime.mvp_runtime.crypto import counterfactual

    _install_pool(tmp_path, _always_spec())
    from runtime.read_only_kernel import integrity

    store = RealPaperStore(root=tmp_path, authorization=_AUTH)
    # A tripped breaker: an outcome history the risk guard refuses to trade on. Written through
    # the same gated store the runtime uses, so the guard reads it exactly as it would in life.
    #
    # SELF-HASHED, and that is not decoration. `append_outcome` stores what it is handed, so an
    # unhashed row fails `read_outcomes`' verified read and the guard reports
    # `risk_history_unreadable` — a refusal, but not the one this test names. The old assertion
    # here was `verdict_status != "ALLOW"`, which both causes satisfy, so the fixture had never
    # tripped the breaker it claimed to and nothing said so.
    for i in range(6):
        body = {
            "position_id": f"p{i}", "strategy_id": "S_ALWAYS", "symbol": "BTCUSDT",
            "timeframe": "1d", "direction": "LONG", "result_R": -1.0, "outcome_closed": True,
            "outcome_id": f"o{i}", "close_reason": "stop_loss",
            "created_at_utc": "2026-07-21T00:00:00Z", "closed_at_utc": "2026-07-21T00:00:00Z",
            # The runtime's OWN provenance, not a literal: the risk guard reads only its own
            # trading (imported history is split off), so a hand-written marker here would make
            # this test pass while the breaker it claims to trip stayed clear.
            "provenance": paper.PAPER_PROVENANCE,
        }
        store.append_outcome({**body, "record_sha256": integrity.sha256_record(body)})
    record = _cycle(tmp_path, FakeExchangeCollector(), store)
    assert record["verdict_status"] != "ALLOW", "the fixture must actually trip a breaker"
    # The breaker binds the LIVE gate and nothing else. The paper leg trades through it, which is
    # the whole point: those six losses are six data points the ladder needs, and the sample that
    # would demote S_ALWAYS only exists if the entries that produce it are allowed to happen.
    assert "weekly_loss_limit_breached" in record["verdict_problems"]
    assert record["paper_verdict_status"] == "ALLOW"
    assert record["paper_verdict_problems"] == []
    assert record["opened"] is not None
    # And no shadow, because nothing on the paper leg was refused. The shadow path itself is
    # covered where a paper refusal still happens — see
    # `test_crypto_entry_economics.test_a_cost_refusal_is_shadowed_so_a_too_tight_gate_can_be_caught`.
    assert counterfactual.load_open_counterfactuals(tmp_path) == []


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
    assert len(per_symbol(inner_feed.calls, "open_interest_history")) == 4
    assert sorted(per_symbol(inner_feed.calls, "open_interest_history")) == [
        "BTCUSDT", "BTCUSDT", "ETHUSDT", "ETHUSDT"
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
