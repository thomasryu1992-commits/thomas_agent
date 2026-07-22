"""C11 analytics tests — digest trends, counterfactual shadows, dashboard reads.

Under test: the digest only compares COMPLETE periods and refuses thin samples; a
blocked-but-actionable signal opens a shadow that settles with the paper exit math
into its own registry (hypothetical, never the risk guard's input); the shadow book
is capped and persists only through the real store; the dashboard is pure reads and
degrades unreadable inputs to warnings."""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.crypto import counterfactual, paper, pool
from runtime.mvp_runtime.crypto.cycle import run_crypto_cycle
from runtime.mvp_runtime.crypto.dashboard import build_status, render_status_text
from runtime.mvp_runtime.crypto.digest import (
    DEGRADING,
    INSUFFICIENT_SAMPLE,
    STABLE,
    build_performance_digest,
    render_digest_text,
)
from runtime.mvp_runtime.crypto.guards import run_risk_guard
from runtime.mvp_runtime.crypto.paper import DryRunPaperStore, RealPaperStore
from runtime.mvp_runtime.safety_gate import FILESYSTEM_WRITE, Authorization

NOW = "2026-07-22T12:00:00Z"

_AUTH = Authorization(
    flags=(FILESYSTEM_WRITE,), provider_id="paper_trading", activation_sha256="sha256:test",
    expires_at="2999-01-01T00:00:00Z", evidence_ref=".runtime_governance_state/evidence.md",
)


def _outcome(result_r, closed_at):
    return {"result_R": result_r, "outcome_closed": True, "created_at_utc": closed_at}


# --- digest -------------------------------------------------------------------

def test_digest_compares_only_complete_periods():
    # Week 28 (Jul 6-12) and week 29 (Jul 13-19) are complete at NOW (Wed of week 30);
    # week 30 is still accumulating and must not be judged.
    rows = (
        [_outcome(1.0, f"2026-07-{d:02d}T10:00:00Z") for d in (6, 7, 8, 9, 10)]      # W28: +1.0
        + [_outcome(0.5, f"2026-07-{d:02d}T10:00:00Z") for d in (13, 14, 15, 16, 17)]  # W29: +0.5
        + [_outcome(-5.0, "2026-07-21T10:00:00Z")]                                     # W30: open period
    )
    result = build_performance_digest(rows, now=NOW)
    trend = result["weekly_trend"]
    assert trend["latest_period"] == "2026-W29" and trend["previous_period"] == "2026-W28"
    assert trend["verdict"] == DEGRADING  # 0.5 - 1.0 = -0.5 < -0.1
    assert trend["expectancy_delta_R"] == -0.5


def test_digest_refuses_thin_buckets():
    rows = [_outcome(1.0, "2026-07-06T10:00:00Z"), _outcome(1.0, "2026-07-13T10:00:00Z")]
    trend = build_performance_digest(rows, now=NOW)["weekly_trend"]
    assert trend["verdict"] == INSUFFICIENT_SAMPLE
    assert "below_min_sample" in trend["reason"]


def test_digest_stable_within_threshold():
    rows = (
        [_outcome(0.50, f"2026-07-{d:02d}T10:00:00Z") for d in (6, 7, 8, 9, 10)]
        + [_outcome(0.55, f"2026-07-{d:02d}T10:00:00Z") for d in (13, 14, 15, 16, 17)]
    )
    assert build_performance_digest(rows, now=NOW)["weekly_trend"]["verdict"] == STABLE


def test_digest_counts_unbucketed_rows():
    rows = [_outcome(1.0, "not-a-timestamp")]
    result = build_performance_digest(rows, now=NOW)
    assert result["unbucketed_count"] == 1
    assert "unbucketed" in render_digest_text(result)


# --- counterfactual -----------------------------------------------------------

def _plan(entry=100.0, stop=97.0, target=104.0, strategy_id="S1"):
    return {"symbol": "BTCUSDT", "timeframe": "1d", "direction": "LONG",
            "entry_price": entry, "stop_loss": stop, "take_profit": target,
            "risk": entry - stop, "strategy_id": strategy_id, "strategy_rule_hash": "aaa"}


def test_shadow_opens_and_settles_into_own_registry(tmp_path):
    opened = counterfactual.run_counterfactual_update(
        blocked_plan=_plan(), block_reasons=["daily_loss_limit_breached"],
        last_candle=None, last_close=None, timeframe="1d", now=NOW, root=tmp_path,
    )
    assert opened["opened"] is not None and opened["open_count"] == 1

    sl_candle = {"high": 100.5, "low": 96.0, "close": 98.0, "close_time": "2026-07-23T00:00:00Z"}
    settled = counterfactual.run_counterfactual_update(
        blocked_plan=None, block_reasons=[], last_candle=sl_candle, last_close=98.0,
        timeframe="1d", now="2026-07-23T12:00:00Z", root=tmp_path,
    )
    assert settled["settled"][0]["classification"] == "AVOIDED_LOSS"
    assert settled["settled"][0]["result_R"] == -1.0
    assert settled["open_count"] == 0

    records = counterfactual.read_counterfactual_outcomes(tmp_path)
    assert len(records) == 1 and records[0]["hypothetical"] is True
    assert records[0]["block_reasons"] == ["daily_loss_limit_breached"]
    # Structural separation: the risk guard never reads this file.
    assert paper.read_outcomes(tmp_path) == []
    verdict = run_risk_guard(paper.read_outcomes(tmp_path), now=NOW)
    assert verdict["allow_new_position"] is True


def test_shadow_book_is_capped(tmp_path):
    for i in range(counterfactual.MAX_OPEN_COUNTERFACTUALS + 5):
        counterfactual.run_counterfactual_update(
            blocked_plan=_plan(entry=100.0 + i, strategy_id=f"S{i}"), block_reasons=["x"],
            last_candle=None, last_close=None, timeframe="1d",
            now=f"2026-07-22T{i % 24:02d}:00:00Z", root=tmp_path,
        )
    assert len(counterfactual.load_open_counterfactuals(tmp_path)) == counterfactual.MAX_OPEN_COUNTERFACTUALS


def test_dry_run_persists_nothing(tmp_path):
    summary = counterfactual.run_counterfactual_update(
        blocked_plan=_plan(), block_reasons=["x"], last_candle=None, last_close=None,
        timeframe="1d", now=NOW, root=tmp_path, persist=False,
    )
    assert summary["opened"] is not None  # computed...
    assert counterfactual.load_open_counterfactuals(tmp_path) == []  # ...not persisted


def test_summarize_by_block_reason():
    records = [
        {"outcome_closed": True, "result_R": 2.0, "block_reasons": ["synthetic_data_source_blocks_trading"]},
        {"outcome_closed": True, "result_R": -1.0, "block_reasons": ["synthetic_data_source_blocks_trading"]},
        {"outcome_closed": True, "result_R": -1.0, "block_reasons": ["daily_loss_limit_breached"]},
    ]
    summary = counterfactual.summarize_counterfactuals(records)
    synthetic = summary["synthetic_data_source_blocks_trading"]
    assert synthetic["closed_count"] == 2 and synthetic["expectancy_R"] == 0.5
    assert synthetic["missed_opportunity"] == 1 and synthetic["avoided_loss"] == 1
    assert summary["daily_loss_limit_breached"]["expectancy_R"] == -1.0


# --- cycle wiring -------------------------------------------------------------

def _always_spec():
    from tests.test_mvp_runtime_crypto_cycle import _always_spec as base

    return base()


def test_blocked_cycle_opens_shadow_and_allowed_cycle_does_not(tmp_path):
    from tests.test_mvp_runtime_crypto_cycle import FakeExchangeCollector, _install_pool

    _install_pool(tmp_path, _always_spec())
    # Poison the risk history so the verdict blocks while the route still matches.
    state = paper.state_dir(tmp_path)
    state.mkdir(parents=True, exist_ok=True)
    losses = [{"result_R": -1.2, "outcome_closed": True, "outcome_id": f"o{i}",
               "created_at_utc": f"2026-07-22T0{i}:00:00Z"} for i in range(2)]
    with open(state / "paper_outcomes.jsonl", "w", encoding="utf-8") as handle:
        for o in losses:
            handle.write(json.dumps(o) + "\n")

    record = run_crypto_cycle(
        collector=FakeExchangeCollector(), store=RealPaperStore(root=tmp_path, authorization=_AUTH),
        now=NOW, root=tmp_path, control_store=ControlStore(tmp_path),
    )
    assert record["verdict_status"] == "NO_NEW_POSITION"
    assert record["counterfactual"]["opened"] is not None
    shadows = counterfactual.load_open_counterfactuals(tmp_path)
    assert shadows and "daily_loss_limit_breached" in shadows[0]["block_reasons"]


def test_dry_run_cycle_computes_shadow_without_persisting(tmp_path):
    from tests.test_mvp_runtime_crypto_cycle import FakeExchangeCollector, _install_pool

    _install_pool(tmp_path, _always_spec())
    state = paper.state_dir(tmp_path)
    state.mkdir(parents=True, exist_ok=True)
    with open(state / "paper_outcomes.jsonl", "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"result_R": -3.0, "outcome_closed": True, "outcome_id": "o1",
                                 "created_at_utc": "2026-07-22T01:00:00Z"}) + "\n")
    record = run_crypto_cycle(
        collector=FakeExchangeCollector(), store=DryRunPaperStore(),
        now=NOW, root=tmp_path, control_store=ControlStore(tmp_path),
    )
    assert record["counterfactual"]["opened"] is not None
    assert counterfactual.load_open_counterfactuals(tmp_path) == []


# --- dashboard ----------------------------------------------------------------

def test_dashboard_reads_and_renders(tmp_path):
    state = paper.state_dir(tmp_path)
    state.mkdir(parents=True, exist_ok=True)
    with open(state / "paper_outcomes.jsonl", "w", encoding="utf-8") as handle:
        for o in [_outcome(1.0, "2026-07-20T10:00:00Z"), _outcome(-0.5, "2026-07-21T10:00:00Z")]:
            handle.write(json.dumps({**o, "outcome_id": o["created_at_utc"]}) + "\n")
    pool.install_active_pool({"active_strategies": []}, root=tmp_path)

    status = build_status(tmp_path, now=NOW)
    assert status["performance"]["closed_count"] == 2
    assert status["warnings"] == []
    text = render_status_text(status)
    assert "crypto pipeline dashboard" in text and "performance" in text


def test_dashboard_degrades_unreadable_inputs_to_warnings(tmp_path):
    state = paper.state_dir(tmp_path)
    state.mkdir(parents=True, exist_ok=True)
    (state / "paper_outcomes.jsonl").write_text("{broken\n", encoding="utf-8")
    status = build_status(tmp_path, now=NOW)
    assert any("outcome store unreadable" in w for w in status["warnings"])
    assert "WARNING" in render_status_text(status)
