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


def _own_outcome(result_r, closed_at, **extra):
    """An outcome as THIS runtime's paper kernel writes it: stamped with the paper provenance
    and self-hashed, so it survives ``read_outcomes``' verified read and lands on the
    dashboard's own-performance line rather than the imported one."""
    from runtime.read_only_kernel import integrity

    body = {**_outcome(result_r, closed_at), "provenance": paper.PAPER_PROVENANCE,
            "outcome_id": closed_at, **extra}
    return {**body, "record_sha256": integrity.sha256_record(body)}


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
        last_candle=None, last_close=None, symbol="BTCUSDT", timeframe="1d", now=NOW, root=tmp_path,
    )
    assert opened["opened"] is not None and opened["open_count"] == 1

    sl_candle = {"high": 100.5, "low": 96.0, "close": 98.0, "close_time": "2026-07-23T00:00:00Z"}
    settled = counterfactual.run_counterfactual_update(
        blocked_plan=None, block_reasons=[], last_candle=sl_candle, last_close=98.0,
        symbol="BTCUSDT", timeframe="1d", now="2026-07-23T12:00:00Z", root=tmp_path,
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


def test_shadow_settles_on_the_plan_time_exit_not_the_table(tmp_path):
    # Exit parity for shadows: the blocked plan carries the spec's max_holding_bars=1,
    # so one calm bar time-exits it — under the old 1d table default (48) the shadow
    # would have measured a hold the real trade could never have had.
    counterfactual.run_counterfactual_update(
        blocked_plan={**_plan(), "max_holding_bars": 1}, block_reasons=["x"],
        last_candle=None, last_close=None, symbol="BTCUSDT", timeframe="1d", now=NOW, root=tmp_path,
    )
    calm = {"high": 101.0, "low": 99.5, "close": 100.5, "close_time": "2026-07-23T00:00:00Z"}
    settled = counterfactual.run_counterfactual_update(
        blocked_plan=None, block_reasons=[], last_candle=calm, last_close=100.5,
        symbol="BTCUSDT", timeframe="1d", now="2026-07-23T12:00:00Z", root=tmp_path,
    )
    assert settled["open_count"] == 0
    records = counterfactual.read_counterfactual_outcomes(tmp_path)
    assert len(records) == 1 and records[0]["close_reason"] == "time_exit"


def test_shadow_book_is_capped(tmp_path):
    for i in range(counterfactual.MAX_OPEN_COUNTERFACTUALS + 5):
        counterfactual.run_counterfactual_update(
            blocked_plan=_plan(entry=100.0 + i, strategy_id=f"S{i}"), block_reasons=["x"],
            last_candle=None, last_close=None, symbol="BTCUSDT", timeframe="1d",
            now=f"2026-07-22T{i % 24:02d}:00:00Z", root=tmp_path,
        )
    assert len(counterfactual.load_open_counterfactuals(tmp_path)) == counterfactual.MAX_OPEN_COUNTERFACTUALS


def test_a_foreign_context_shadow_is_never_settled_or_advanced(tmp_path):
    """The L1a guard the shadow book reintroduced: an ETH cycle used to settle a BTC
    shadow against ETH candles, fabricating a hypothetical that feeds gate calibration."""
    counterfactual.run_counterfactual_update(
        blocked_plan=_plan(), block_reasons=["x"], last_candle=None, last_close=None,
        symbol="BTCUSDT", timeframe="1d", now=NOW, root=tmp_path,
    )
    before = counterfactual.load_open_counterfactuals(tmp_path)[0]

    sweep = {"high": 100.5, "low": 96.0, "close": 98.0, "close_time": "2026-07-23T00:00:00Z"}
    for symbol, timeframe in (("ETHUSDT", "1d"), ("BTCUSDT", "1h")):
        summary = counterfactual.run_counterfactual_update(
            blocked_plan=None, block_reasons=[], last_candle=sweep, last_close=98.0,
            symbol=symbol, timeframe=timeframe, now="2026-07-23T12:00:00Z", root=tmp_path,
        )
        assert summary["settled"] == [] and summary["foreign_context_skipped"] == 1
    assert counterfactual.read_counterfactual_outcomes(tmp_path) == []
    after = counterfactual.load_open_counterfactuals(tmp_path)[0]
    assert after["holding_candles"] == before["holding_candles"]   # not advanced either

    # Its own context still settles it.
    summary = counterfactual.run_counterfactual_update(
        blocked_plan=None, block_reasons=[], last_candle=sweep, last_close=98.0,
        symbol="BTCUSDT", timeframe="1d", now="2026-07-23T12:00:00Z", root=tmp_path,
    )
    assert summary["settled"][0]["classification"] == "AVOIDED_LOSS"


def test_an_unreadable_book_degrades_and_is_preserved(tmp_path):
    # It used to be read as "no shadows" and then overwritten, destroying the data
    # and hiding the loss.
    path = paper.state_dir(tmp_path)
    path.mkdir(parents=True, exist_ok=True)
    (path / "counterfactual_positions.json").write_text("{not json", encoding="utf-8")

    summary = counterfactual.run_counterfactual_update(
        blocked_plan=_plan(), block_reasons=["x"], last_candle=None, last_close=None,
        symbol="BTCUSDT", timeframe="1d", now=NOW, root=tmp_path,
    )
    assert summary["degraded"] == "COUNTERFACTUAL_BOOK_UNVERIFIABLE"
    assert summary["opened"] is None
    # The damaged file survives for inspection rather than being silently rewritten.
    assert (path / "counterfactual_positions.json").read_text(encoding="utf-8") == "{not json"


def test_a_resettled_shadow_is_not_doubled(tmp_path):
    counterfactual.run_counterfactual_update(
        blocked_plan=_plan(), block_reasons=["x"], last_candle=None, last_close=None,
        symbol="BTCUSDT", timeframe="1d", now=NOW, root=tmp_path,
    )
    shadow = counterfactual.load_open_counterfactuals(tmp_path)[0]
    record = counterfactual.build_counterfactual_outcome_record(
        shadow, close_reason="stop_loss", exit_price=97.0, result_r=-1.0, now=NOW)
    counterfactual._append_outcomes([record], root=tmp_path)
    counterfactual._append_outcomes([record], root=tmp_path)      # a retried settlement
    assert len(counterfactual.read_counterfactual_outcomes(tmp_path)) == 1


def test_a_tampered_native_shadow_outcome_is_refused(tmp_path):
    path = paper.state_dir(tmp_path)
    path.mkdir(parents=True, exist_ok=True)
    record = counterfactual.build_counterfactual_outcome_record(
        {**_plan(), "counterfactual_id": "cf1", "holding_candles": 1},
        close_reason="stop_loss", exit_price=97.0, result_r=-1.0, now=NOW)
    (path / "counterfactual_outcomes.jsonl").write_text(
        json.dumps({**record, "result_R": 9.0}) + "\n", encoding="utf-8")
    with pytest.raises(Exception) as exc:
        counterfactual.read_counterfactual_outcomes(tmp_path)
    assert exc.value.reason_code == "COUNTERFACTUAL_HISTORY_TAMPERED"


def test_imported_shadow_rows_skip_the_hash_recompute(tmp_path):
    # The live store holds 53 imported counterfactuals hashed by the SOURCE scheme.
    path = paper.state_dir(tmp_path)
    path.mkdir(parents=True, exist_ok=True)
    imported = {"counterfactual_id": "cf_import", "outcome_closed": True, "result_R": 1.0,
                "provenance": "crypto_ai_system_import", "record_sha256": "sha256:source-scheme"}
    (path / "counterfactual_outcomes.jsonl").write_text(json.dumps(imported) + "\n", encoding="utf-8")
    assert counterfactual.read_counterfactual_outcomes(tmp_path) == [imported]


def test_dry_run_persists_nothing(tmp_path):
    summary = counterfactual.run_counterfactual_update(
        blocked_plan=_plan(), block_reasons=["x"], last_candle=None, last_close=None,
        symbol="BTCUSDT", timeframe="1d", now=NOW, root=tmp_path, persist=False,
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
    # This runtime's OWN losses — the risk guard counts only these (imported history cannot
    # answer "is this system losing right now"), so the fixture carries the paper provenance.
    losses = [_own_outcome(-1.2, f"2026-07-22T0{i}:00:00Z", outcome_id=f"o{i}") for i in range(2)]
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
        handle.write(json.dumps(_own_outcome(-3.0, "2026-07-22T01:00:00Z", outcome_id="o1")) + "\n")
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
        for o in [_own_outcome(1.0, "2026-07-20T10:00:00Z"), _own_outcome(-0.5, "2026-07-21T10:00:00Z")]:
            handle.write(json.dumps(o) + "\n")
    pool.install_active_pool({"active_strategies": []}, root=tmp_path)

    status = build_status(tmp_path, now=NOW)
    assert status["performance"]["closed_count"] == 2
    assert status["warnings"] == []
    text = render_status_text(status)
    assert "crypto dashboard" in text and "성과" in text


def test_dashboard_never_blends_imported_history_into_this_runtimes_performance(tmp_path):
    """The store holds both; the headline must answer "how is THIS runtime doing".

    Blended, the imported crypto_AI_System set dominates by count — that is exactly how a
    +2.24R headline was read as this runtime's result when its own record was 5 trades."""
    state = paper.state_dir(tmp_path)
    state.mkdir(parents=True, exist_ok=True)
    rows = [_own_outcome(-1.0, "2026-07-24T10:00:00Z"), _own_outcome(-1.0, "2026-07-25T10:00:00Z")]
    # Imported rows carry the other provenance and are NOT self-hash-verified on read.
    rows += [{**_outcome(3.0, f"2026-07-2{d}T10:00:00Z"), "provenance": paper.IMPORTED_PROVENANCE,
              "outcome_id": f"imported-{d}"} for d in (0, 1, 2)]
    with open(state / "paper_outcomes.jsonl", "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    pool.install_active_pool({"active_strategies": []}, root=tmp_path)

    status = build_status(tmp_path, now=NOW)
    assert status["performance"]["closed_count"] == 2            # own only
    assert status["performance"]["expectancy"] < 0               # and honestly negative
    assert status["imported_performance"]["closed_count"] == 3   # reported, separately
    assert status["imported_performance"]["expectancy"] > 0
    text = render_status_text(status)
    own_line = next(l for l in text.splitlines() if l.startswith("성과"))
    assert "자체 페이퍼" in own_line and "-" in own_line      # own, and honestly negative
    imported_line = next(l for l in text.splitlines() if "imported" in l)
    # Demoted: indented, parenthesised, and disclaimed — a positive predecessor number
    # sitting flush under a negative own one is exactly how it got misread before.
    assert imported_line.strip().startswith("(참고:")
    assert "이 런타임 아님" in imported_line
    assert text.index(own_line) < text.index(imported_line)


def test_dashboard_degrades_unreadable_inputs_to_warnings(tmp_path):
    state = paper.state_dir(tmp_path)
    state.mkdir(parents=True, exist_ok=True)
    (state / "paper_outcomes.jsonl").write_text("{broken\n", encoding="utf-8")
    status = build_status(tmp_path, now=NOW)
    assert any("outcome store unreadable" in w for w in status["warnings"])
    # Warnings now ride in the headline block, where a reader sees them before the data
    # they undermine — not appended after everything.
    rendered = render_status_text(status)
    assert "⚠ outcome store unreadable" in rendered.split("지금")[0]


def test_imported_history_cannot_offset_the_risk_breaker(tmp_path):
    """The reason the guard was scoped to own outcomes.

    Imported crypto_AI_System history is real but was produced by different code, so it cannot
    answer "is THIS system losing right now". Measured 2026-07-25: 112 imported rows worth
    +266.8R sat inside the rolling week, so the weekly-loss breaker could not trip however this
    runtime performed. Here a big imported profit sits alongside this runtime's own losses — the
    breaker must still trip.
    """
    from tests.test_mvp_runtime_crypto_cycle import FakeExchangeCollector, _install_pool

    _install_pool(tmp_path, _always_spec())
    state = paper.state_dir(tmp_path)
    state.mkdir(parents=True, exist_ok=True)
    rows = [_own_outcome(-1.2, f"2026-07-22T0{i}:00:00Z", outcome_id=f"own{i}") for i in range(2)]
    rows += [{"result_R": 200.0, "outcome_closed": True, "outcome_id": "imported-1",
              "created_at_utc": "2026-07-22T05:00:00Z",
              "provenance": paper.IMPORTED_PROVENANCE}]
    with open(state / "paper_outcomes.jsonl", "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")

    record = run_crypto_cycle(
        collector=FakeExchangeCollector(), store=RealPaperStore(root=tmp_path, authorization=_AUTH),
        now=NOW, root=tmp_path, control_store=ControlStore(tmp_path),
    )
    # +200R of someone else's history does not clear this runtime's own daily loss.
    assert record["verdict_status"] == "NO_NEW_POSITION"
    shadows = counterfactual.load_open_counterfactuals(tmp_path)
    assert shadows and "daily_loss_limit_breached" in shadows[0]["block_reasons"]


def test_lifecycle_still_sees_the_full_history(tmp_path):
    """Deliberately NOT filtered: imported outcomes carry strategy lineage, and
    promotion/demotion is a performance judgement about a strategy, not a safety brake on this
    runtime. Scoping the guard must not silently change what lifecycle reads."""
    import inspect

    from runtime.mvp_runtime.crypto import cycle as cycle_mod

    source = inspect.getsource(cycle_mod.run_crypto_cycle)
    assert "run_risk_guard(own_outcomes" in source      # guard: own only
    assert "run_lifecycle(active_pool, outcomes" in source   # lifecycle: full history


# --- dashboard readability (the operator could not judge from the old dump) ---------
#
# The board reported state and left every judgement to the reader: the headline number sat
# above its own INSUFFICIENT_SAMPLE caveat, a positive imported figure sat flush under a
# negative own one, and "which gate should I change" required doing missed/avoided
# arithmetic across five lines. Same data; the reading order is what these pin.

def _board(**overrides):
    status = {
        "created_at": "2026-07-26T03:55:33Z",
        "last_cycle": {"at": "2026-07-26T03:55:33Z", "verdict": "ALLOW", "route": "NO_ENTRY",
                       "feeds": {"funding": "ok", "liquidations": "ok", "open_interest": "ok"},
                       "degraded": False, "reason_codes": []},
        "open_position": {"direction": "LONG", "strategy_id": "S003", "entry_price": 570.36},
        "open_positions": [{}],
        "pool_size": 71, "pool_status_counts": {"PAPER_ACTIVE": 55, "SUSPENDED": 16},
        "performance": {"closed_count": 11, "expectancy": -0.30682667,
                        "max_drawdown": 5.67135024, "recommendation": "DROP_CANDIDATE_PROFILE"},
        "imported_performance": {"closed_count": 114, "expectancy": 2.3638822, "max_drawdown": 1.0},
        "digest": None, "counterfactual_closed": 118, "counterfactual_by_reason": {},
        "grants": [], "warnings": [],
    }
    status.update(overrides)
    return status


_GATES = {
    "MAX_CONSECUTIVE_LOSS_GATE_BLOCKED": {"closed_count": 2, "expectancy_R": 2.0,
                                          "missed_opportunity": 2, "avoided_loss": 0},
    "daily_loss_limit_breached": {"closed_count": 65, "expectancy_R": -0.42631077,
                                  "missed_opportunity": 11, "avoided_loss": 53},
    "POSITION_LIMIT_GATE_BLOCKED": {"closed_count": 30, "expectancy_R": 0.14859566,
                                    "missed_opportunity": 15, "avoided_loss": 15},
}


def test_the_verdict_comes_before_the_evidence():
    text = render_status_text(_board())
    assert text.splitlines()[2].startswith("판단:")
    # 11 closed is noise, and the board must say so where the number is decided, not only
    # in a trends section further down.
    assert "표본 부족" in text


def test_a_thin_sample_never_reads_as_a_green_light():
    """A positive expectancy off 4 trades is still not evidence."""
    text = render_status_text(_board(performance={
        "closed_count": 4, "expectancy": 1.9, "max_drawdown": 0.2, "recommendation": "KEEP"}))
    assert "표본 부족" in text
    assert "확대 근거 없음" in text


def test_a_real_sample_that_loses_says_so_plainly():
    text = render_status_text(_board(performance={
        "closed_count": 80, "expectancy": -0.4, "max_drawdown": 3.0, "recommendation": "DROP"}))
    assert "손실 중" in text and "확대 근거 없음" in text


def test_costing_gates_are_named_in_the_headline_and_sorted_first():
    text = render_status_text(_board(counterfactual_by_reason=_GATES))
    assert "게이트 2개가 손해 중" in text
    gate_lines = [l for l in text.splitlines() if l.startswith("  🔴") or l.startswith("  🟢")]
    # Costing first, worst first: a gate that blocked only winners is the one to look at.
    assert gate_lines[0].startswith("  🔴") and "MAX_CONSECUTIVE_LOSS" in gate_lines[0]
    assert gate_lines[-1].startswith("  🟢")


def test_a_gate_that_blocks_losers_is_marked_earning():
    text = render_status_text(_board(counterfactual_by_reason={
        "daily_loss_limit_breached": _GATES["daily_loss_limit_breached"]}))
    assert "🟢 daily_loss_limit_breached" in text
    assert "손해 중" not in text          # nothing to flag, so the headline stays quiet


def test_imported_history_cannot_be_read_as_this_runtimes_result():
    text = render_status_text(_board())
    imported = next(l for l in text.splitlines() if "imported" in l)
    assert imported.strip().startswith("(참고:")     # parenthesised and indented
    assert "이 런타임 아님" in imported


def test_numbers_are_scannable_not_precise_to_eight_places():
    text = render_status_text(_board())
    assert "-0.31R" in text and "0.30682667" not in text
    assert "dd 5.67R" in text          # a magnitude carries no + sign


def test_the_board_shows_the_books_directional_shape():
    """The gate that reads the book's lean declines on STANDING state, so the lean belongs on the
    board and not only on the one status line that happened to print a refusal."""
    text = render_status_text(_board(
        open_positions=[{}] * 7,
        directional_lean={"long": 5, "short": 2, "unattributed": 0,
                          "lean": 3, "limit": 4, "at_limit": False},
    ))
    assert "방향 롱 5 / 숏 2 · 편중 +3 (한도 ±4)" in text
    assert "한 방향 한도" not in text, "not at the limit, so no warning"


def test_the_board_warns_when_the_book_is_at_the_directional_limit():
    text = render_status_text(_board(
        open_positions=[{}] * 4,
        directional_lean={"long": 4, "short": 0, "unattributed": 0,
                          "lean": 4, "limit": 4, "at_limit": True},
    ))
    assert "편중 +4 (한도 ±4)" in text and "⚠ 한 방향 한도" in text


def test_a_position_with_no_readable_direction_is_counted_not_dropped():
    """The gate treats it as aligned with whatever is proposed, so a board that silently omitted
    it would show a book more balanced than the one the gate is judging."""
    text = render_status_text(_board(
        open_positions=[{}] * 3,
        directional_lean={"long": 2, "short": 0, "unattributed": 1,
                          "lean": 2, "limit": 4, "at_limit": False},
    ))
    assert "방향불명 1" in text


def test_the_board_still_renders_a_status_written_before_the_lean_existed():
    """Records an operator opens the board to read predate this field. A renderer that assumed
    its own newest key would crash on exactly that history — and did, before this test."""
    text = render_status_text(_board(open_positions=[{}] * 3))
    assert "지금" in text and "편중" not in text


def test_grants_collapse_to_one_line_until_one_is_near_expiry():
    far = [{"provider_id": "groq", "expires_at": "2026-08-20T01:56:34Z"},
           {"provider_id": "telegram", "expires_at": "2026-08-18T10:13:20Z"}]
    text = render_status_text(_board(grants=far))
    assert len([l for l in text.splitlines() if "권한" in l or "groq" in l]) == 1

    soon = [{"provider_id": "groq", "expires_at": "2026-07-28T01:56:34Z"}]
    urgent = render_status_text(_board(grants=soon))
    assert "⚠" in urgent and "groq" in urgent


def test_warnings_surface_in_the_headline():
    text = render_status_text(_board(warnings=["outcome store unreadable (LEDGER_UNREADABLE)"]))
    assert "⚠ outcome store unreadable" in text.split("지금")[0]


def test_an_empty_board_still_renders():
    text = render_status_text({"created_at": "2026-07-26T03:55:33Z", "performance": {},
                               "pool_size": 0, "pool_status_counts": {}})
    assert "판단:" in text and "근거 없음" in text


# --- the dashboard must not hold the ledger in memory --------------------------------
#
# It used to read_text() the whole record ledger, splitlines() it, and parse every row into
# a list — to keep the last twelve. On the live host that ledger is 56MB / 3881 rows, and
# parsed JSON is several times its text size: the read OOM-killed on a host with 400MB
# free, and would have taken the scheduler down with it at the next daily report.

def _ledger_with_cycles(root, *, cycles: int, noise: int, filler: int = 0):
    from runtime.mvp_runtime.store import LEDGER_REL, RECORDS_FILE
    path = root / LEDGER_REL / RECORDS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for index in range(noise):
            handle.write(json.dumps({"kind": "agent_output", "trace_id": f"t{index}",
                                     "record": {"pad": "x" * filler}}) + "\n")
        for index in range(cycles):
            handle.write(json.dumps({"kind": "crypto_cycle", "trace_id": f"c{index}",
                                     "record": {"created_at": f"2026-07-26T00:{index:02d}:00Z",
                                                "verdict_status": "ALLOW",
                                                "pad": "x" * filler}}) + "\n")
    return path


def test_cycle_read_keeps_only_the_window_it_needs(tmp_path):
    import tracemalloc
    from runtime.mvp_runtime.crypto.dashboard import _read_cycle_records

    # ~8MB of ledger; the old implementation's peak scaled with this, the new one does not.
    _ledger_with_cycles(tmp_path, cycles=400, noise=400, filler=10_000)
    tracemalloc.start()
    rows, warning = _read_cycle_records(tmp_path, 12)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()

    assert warning is None
    assert len(rows) == 12
    assert rows[-1]["created_at"] == "2026-07-26T00:399:00Z"
    # A bounded window: nowhere near the ~8MB the file occupies, let alone its parsed size.
    assert peak < 2 * 1024 * 1024, f"peak {peak} suggests the ledger is being held in memory"


def test_the_window_is_the_most_recent_cycles(tmp_path):
    from runtime.mvp_runtime.crypto.dashboard import _read_cycle_records

    _ledger_with_cycles(tmp_path, cycles=30, noise=5)
    rows, _ = _read_cycle_records(tmp_path, 3)
    assert [r["created_at"][-6:-1] for r in rows] == ["27:00", "28:00", "29:00"]


def test_a_corrupt_row_elsewhere_no_longer_blinds_the_board(tmp_path):
    """One bad line anywhere used to fail the whole read. A row that is not a cycle is not
    this reader's business."""
    from runtime.mvp_runtime.crypto.dashboard import _read_cycle_records
    from runtime.mvp_runtime.store import LEDGER_REL, RECORDS_FILE

    path = _ledger_with_cycles(tmp_path, cycles=2, noise=0)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not json at all\n")
    rows, warning = _read_cycle_records(tmp_path, 12)
    assert len(rows) == 2 and warning is None


def test_a_corrupt_cycle_row_is_reported(tmp_path):
    """A row that looks like a cycle and will not parse IS this reader's business."""
    from runtime.mvp_runtime.crypto.dashboard import _read_cycle_records

    path = _ledger_with_cycles(tmp_path, cycles=2, noise=0)
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"kind": "crypto_cycle", broken\n')
    rows, warning = _read_cycle_records(tmp_path, 12)
    assert len(rows) == 2
    assert "unparsable" in warning


def test_a_missing_ledger_is_not_an_error(tmp_path):
    from runtime.mvp_runtime.crypto.dashboard import _read_cycle_records
    assert _read_cycle_records(tmp_path, 12) == ([], None)
