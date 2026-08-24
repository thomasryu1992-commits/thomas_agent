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
    """One context per shadow now, so the cap can only be reached ACROSS contexts.

    This used to pile `MAX_OPEN_COUNTERFACTUALS + 5` plans into BTCUSDT 1d under different
    strategy ids — the exact shape the per-context rule exists to refuse, and one the runtime
    cannot produce anyway, since `route_entries` picks ONE strategy per context. Rewritten to
    the reachable scenario so it still tests the cap rather than testing the bug.

    The real fan-out is 5 symbols x 4 timeframes = 20 contexts, so the cap is now unreachable
    in production. It stays as the backstop it always was."""
    for i in range(counterfactual.MAX_OPEN_COUNTERFACTUALS + 5):
        counterfactual.run_counterfactual_update(
            blocked_plan={**_plan(), "symbol": f"SYM{i}USDT"}, block_reasons=["x"],
            last_candle=None, last_close=None, symbol=f"SYM{i}USDT", timeframe="1d",
            now=f"2026-07-22T{i % 24:02d}:00:00Z", root=tmp_path,
        )
    assert len(counterfactual.load_open_counterfactuals(tmp_path)) == counterfactual.MAX_OPEN_COUNTERFACTUALS


# --- supporting shadows (the fast-context cap's evidence half) -----------------

def _supporting_plan(strategy_id="S_bench", reason="ROUTED_BEHIND_PRIMARY"):
    return {**_plan(strategy_id=strategy_id), "shadow_reason": reason}


def test_supporting_shadows_open_settle_and_stay_out_of_gate_buckets(tmp_path):
    """A benched lineage's signal settles like any shadow, tagged with its routing reason —
    which is a bucket of its own, so gate calibration never reads routing evidence."""
    result = counterfactual.run_counterfactual_update(
        blocked_plan=None, block_reasons=[], last_candle=None, last_close=None,
        symbol="BTCUSDT", timeframe="1d", now=NOW, root=tmp_path,
        supporting_plans=[_supporting_plan()],
    )
    assert result["supporting_opened"] and result["open_count"] == 1
    open_rows = counterfactual.load_open_counterfactuals(tmp_path)
    assert open_rows[0]["shadow_kind"] == counterfactual.SHADOW_KIND_SUPPORTING

    sl_candle = {"high": 100.5, "low": 96.0, "close": 98.0, "close_time": "2026-07-23T00:00:00Z"}
    settled = counterfactual.run_counterfactual_update(
        blocked_plan=None, block_reasons=[], last_candle=sl_candle, last_close=98.0,
        symbol="BTCUSDT", timeframe="1d", now="2026-07-23T12:00:00Z", root=tmp_path,
    )
    assert settled["settled"][0]["result_R"] == -1.0 and settled["open_count"] == 0
    records = counterfactual.read_counterfactual_outcomes(tmp_path)
    assert records[0]["shadow_kind"] == counterfactual.SHADOW_KIND_SUPPORTING
    assert records[0]["strategy_id"] == "S_bench"
    assert set(counterfactual.r_values_by_reason(records)) == {"ROUTED_BEHIND_PRIMARY"}


def test_one_supporting_shadow_per_context_and_strategy(tmp_path):
    """The same lineage re-declined while its shadow is still open is the SAME measurement
    (the gate book's 16-copies lesson); a second benched lineage is a different one."""
    counterfactual.run_counterfactual_update(
        blocked_plan=None, block_reasons=[], last_candle=None, last_close=None,
        symbol="BTCUSDT", timeframe="1d", now=NOW, root=tmp_path,
        supporting_plans=[_supporting_plan()],
    )
    result = counterfactual.run_counterfactual_update(
        blocked_plan=None, block_reasons=[], last_candle=None, last_close=None,
        symbol="BTCUSDT", timeframe="1d", now="2026-07-22T16:00:00Z", root=tmp_path,
        supporting_plans=[_supporting_plan(), _supporting_plan(strategy_id="S_other")],
    )
    assert result["supporting_skipped"] == 1
    assert len(result["supporting_opened"]) == 1
    assert result["open_count"] == 2


def test_a_supporting_shadow_does_not_suppress_the_gate_shadow(tmp_path):
    """Two different measurements in one context: what the gate cost the book, and what a
    benched lineage would have done. Neither dedupe may reach across."""
    counterfactual.run_counterfactual_update(
        blocked_plan=None, block_reasons=[], last_candle=None, last_close=None,
        symbol="BTCUSDT", timeframe="1d", now=NOW, root=tmp_path,
        supporting_plans=[_supporting_plan()],
    )
    result = counterfactual.run_counterfactual_update(
        blocked_plan=_plan(), block_reasons=["daily_loss_limit_breached"],
        last_candle=None, last_close=None, symbol="BTCUSDT", timeframe="1d",
        now="2026-07-22T16:00:00Z", root=tmp_path,
    )
    assert result["opened"] is not None and result["duplicate_of"] is None
    assert result["open_count"] == 2


def test_a_second_shadow_in_one_context_is_refused_not_stacked(tmp_path):
    """The book being shadowed holds ONE position per (symbol, timeframe). A shadow book that
    can hold sixteen in a context where the real book holds at most one is not measuring the
    real book's counterfactual.

    Measured 2026-08-08: 16 of the 17 open shadows were ETHUSDT 1d LONG for strategy S1735,
    opened one per 15-minute cycle from 07-25T06:08 to 07-26T13:05 while the block persisted,
    all still holding 13-14 of 27 bars — the same trade sixteen times. `MAX_OPEN_COUNTERFACTUALS`
    knows the mechanism and bounds the BOOK; the damage is in the calibration, where
    `r_values_by_reason` counts each closed shadow as one observation and
    `dashboard.sample_verdict` gates a gate's verdict on an interval built over them.
    """
    first = counterfactual.run_counterfactual_update(
        blocked_plan=_plan(), block_reasons=["daily_loss_limit_breached"],
        last_candle=None, last_close=None, symbol="BTCUSDT", timeframe="1d", now=NOW,
        root=tmp_path,
    )
    again = counterfactual.run_counterfactual_update(
        blocked_plan=_plan(entry=101.0, strategy_id="S2"),
        block_reasons=["daily_loss_limit_breached"],
        last_candle=None, last_close=None, symbol="BTCUSDT", timeframe="1d",
        now="2026-07-22T12:15:00Z", root=tmp_path,
    )
    assert again["opened"] is None
    # Named, not counted: "the gate is still blocking the trade we are already measuring" and
    # "the gate stopped blocking" are opposite facts and `opened: None` says both.
    assert again["duplicate_of"] == first["opened"]
    assert again["open_count"] == 1

    # A different context is a different position in the real book, so it still opens.
    other = counterfactual.run_counterfactual_update(
        blocked_plan={**_plan(), "symbol": "ETHUSDT"}, block_reasons=["x"],
        last_candle=None, last_close=None, symbol="ETHUSDT", timeframe="1d",
        now="2026-07-22T12:30:00Z", root=tmp_path,
    )
    assert other["opened"] is not None and other["duplicate_of"] is None


def test_a_shadow_whose_clock_froze_expires_instead_of_holding_a_cap_slot(tmp_path):
    """A shadow only advances `holding_candles` in the cycle owning its context, so one whose
    context leaves the fan-out can never reach its time exit — it holds a cap slot forever.
    Measured 2026-08-04: 30 of 30 open shadows sat outside the five routed contexts, i.e. 30
    of the 50 slots were permanently spent."""
    counterfactual.run_counterfactual_update(
        blocked_plan={**_plan(), "max_holding_bars": 2}, block_reasons=["regime_excluded"],
        last_candle=None, last_close=None, symbol="BTCUSDT", timeframe="1d", now=NOW, root=tmp_path,
    )
    assert len(counterfactual.load_open_counterfactuals(tmp_path)) == 1

    # Another context's cycle, three days on: 2 bars x 1d of budget is spent.
    summary = counterfactual.run_counterfactual_update(
        blocked_plan=None, block_reasons=[], last_candle=None, last_close=None,
        symbol="ETHUSDT", timeframe="4h", now="2026-07-25T12:00:00Z", root=tmp_path,
    )
    assert [e["reason"] for e in summary["expired"]] == ["holding_budget_elapsed_unsettled"]
    assert summary["expired"][0]["block_reasons"] == ["regime_excluded"]
    assert summary["open_count"] == 0
    assert counterfactual.load_open_counterfactuals(tmp_path) == []
    # Never priced: an expiry has no result_R, so it must not reach per-reason expectancy.
    assert counterfactual.read_counterfactual_outcomes(tmp_path) == []


def test_a_shadow_inside_its_own_budget_is_left_alone(tmp_path):
    """Expiry is the shadow's own clock, not its context's membership. Contexts come back —
    1d re-entered the rotation on 2026-08-04 — and 17 of the 30 stranded shadows were still
    legitimately running, the ETHUSDT 1d block at 242h against a 648h budget."""
    counterfactual.run_counterfactual_update(
        blocked_plan={**_plan(), "max_holding_bars": 27}, block_reasons=["x"],
        last_candle=None, last_close=None, symbol="BTCUSDT", timeframe="1d", now=NOW, root=tmp_path,
    )
    summary = counterfactual.run_counterfactual_update(
        blocked_plan=None, block_reasons=[], last_candle=None, last_close=None,
        symbol="ETHUSDT", timeframe="4h", now="2026-07-25T12:00:00Z", root=tmp_path,
    )
    assert summary["expired"] == [] and summary["foreign_context_skipped"] == 1
    assert len(counterfactual.load_open_counterfactuals(tmp_path)) == 1


def test_an_overdue_shadow_in_its_own_context_settles_rather_than_expires(tmp_path):
    """Settling beats expiring wherever it is possible: a priced outcome is what the book
    exists for, and only a shadow this cycle cannot judge is a candidate for expiry."""
    counterfactual.run_counterfactual_update(
        blocked_plan={**_plan(), "max_holding_bars": 1}, block_reasons=["x"],
        last_candle=None, last_close=None, symbol="BTCUSDT", timeframe="1d", now=NOW, root=tmp_path,
    )
    calm = {"high": 101.0, "low": 99.5, "close": 100.5, "close_time": "2026-07-25T00:00:00Z"}
    summary = counterfactual.run_counterfactual_update(
        blocked_plan=None, block_reasons=[], last_candle=calm, last_close=100.5,
        symbol="BTCUSDT", timeframe="1d", now="2026-07-25T12:00:00Z", root=tmp_path,
    )
    assert summary["expired"] == [] and len(summary["settled"]) == 1
    assert counterfactual.read_counterfactual_outcomes(tmp_path)[0]["close_reason"] == "time_exit"


def test_a_shadow_that_cannot_state_its_budget_is_never_expired(tmp_path):
    """Fail-closed on unreadable inputs, the `settles_in_context` posture: an unknown
    timeframe cannot be converted to a span, so the row is left alone rather than expired on
    a guess."""
    counterfactual.run_counterfactual_update(
        blocked_plan={**_plan(), "timeframe": "3w", "max_holding_bars": 1},
        block_reasons=["x"], last_candle=None, last_close=None,
        symbol="BTCUSDT", timeframe="3w", now=NOW, root=tmp_path,
    )
    summary = counterfactual.run_counterfactual_update(
        blocked_plan=None, block_reasons=[], last_candle=None, last_close=None,
        symbol="ETHUSDT", timeframe="4h", now="2027-01-01T00:00:00Z", root=tmp_path,
    )
    assert summary["expired"] == []
    assert len(counterfactual.load_open_counterfactuals(tmp_path)) == 1


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


def test_a_corrupt_shadow_line_still_raises_this_readers_own_error_class(tmp_path):
    """The reader now streams through ``jsonl.iter_numbered`` instead of parsing by hand, and the
    thing that must NOT move is which class comes out: the tool chokepoints above catch ToolError,
    so adopting jsonl's default PersistenceError would fail past the caller instead of at it. The
    reason code and the file line number are the operator's two handles; both survive."""
    from runtime.mvp_runtime.errors import PersistenceError, ToolError

    path = paper.state_dir(tmp_path)
    path.mkdir(parents=True, exist_ok=True)
    (path / "counterfactual_outcomes.jsonl").write_text(
        '{"counterfactual_id": "cf1"}\n\n{not json\n', encoding="utf-8")

    with pytest.raises(ToolError) as exc:
        counterfactual.read_counterfactual_outcomes(tmp_path)
    assert exc.value.reason_code == "COUNTERFACTUAL_HISTORY_UNREADABLE"
    assert not isinstance(exc.value, PersistenceError)
    assert "line 3" in str(exc.value)  # the file line, not the second object


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


def test_a_loss_breaker_stops_the_live_gate_and_lets_paper_trade(tmp_path):
    """The verdict split, seen from outside the cycle.

    Twice rewritten, and the history is the point. It first proved that two PAPER losses stopped
    a PAPER entry and shadowed it. Then `paper_trade_verdict` took the paper leg off the
    breakers, and it proved those same paper losses stopped only the LIVE gate. Both versions
    metered real money against a simulation; the breakers now read LIVE outcomes, so the fixture
    seeds the only losses that can trip one.

    What survives unchanged is the invariant: the loss breaker binds the live gate and never the
    paper leg, and both verdicts ride on the record so neither reading is lost."""
    from tests.test_mvp_runtime_crypto_cycle import (
        FakeExchangeCollector, _install_pool, _live_loss, _write_live_outcomes,
    )

    _install_pool(tmp_path, _always_spec())
    _write_live_outcomes(tmp_path, [_live_loss(i, when=NOW) for i in range(1, 4)])

    record = run_crypto_cycle(
        collector=FakeExchangeCollector(), store=RealPaperStore(root=tmp_path, authorization=_AUTH),
        now=NOW, root=tmp_path, control_store=ControlStore(tmp_path),
    )
    assert record["verdict_status"] == "NO_NEW_POSITION"
    assert "max_consecutive_losses_breached" in record["verdict_problems"]
    # ...and the paper leg never saw it.
    assert record["paper_verdict_status"] == "ALLOW"
    assert record["paper_verdict_problems"] == []
    assert record["opened"] is not None, "the loss breaker must not stop a paper entry"
    # Nothing to shadow: a shadow stands in for an outcome that did not happen, and this one did.
    assert counterfactual.load_open_counterfactuals(tmp_path) == []


def test_dry_run_cycle_computes_shadow_without_persisting(tmp_path):
    """The refusal is now the economics gate rather than a loss breaker, because after the
    verdict split a loss breaker no longer refuses the paper leg and so no longer produces a
    shadow to test. The subject is unchanged: a dry run computes the shadow and writes nothing."""
    from tests.test_crypto_entry_economics import _NarrowRangeCollector, _spec_dict

    pool.install_active_pool(
        {"active_strategies": [{
            "strategy_id": "S1", "status": "PAPER_ACTIVE", "champion_score": 0.5,
            "strategy_spec": _spec_dict(entry_rules={
                "operator": "AND",
                "conditions": [{"feature": "close", "comparison": ">", "value": 0.0}],
            }),
        }]},
        root=tmp_path,
    )
    record = run_crypto_cycle(
        collector=_NarrowRangeCollector(), store=DryRunPaperStore(),
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


def test_no_paper_row_reaches_the_loss_breaker_own_or_imported(tmp_path):
    """What replaced the own/imported scoping question.

    That scoping existed because the guard read the paper store and imported crypto_AI_System
    rows could dilute it — measured 2026-07-25, 112 imported rows worth +266.8R sat inside the
    rolling week, so the weekly breaker could not trip however this runtime performed. The guard
    no longer reads that store at all, which answers the question by removing it: neither
    provenance can move a live breaker, in either direction.

    Seeded so that BOTH failure directions would show. The own losses would trip the daily
    breaker if they were still counted; the imported profit would clear it if the split had been
    dropped instead of the store. The verdict is ALLOW because neither is read."""
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
    assert record["verdict_status"] == "ALLOW"
    assert record["verdict_problems"] == []
    # The breaker judged nothing, and says so rather than reporting a comfortable clear.
    assert record["risk_limits"] is not None

def test_lifecycle_still_sees_the_full_history(tmp_path):
    """Deliberately NOT filtered: imported outcomes carry strategy lineage, and
    promotion/demotion is a performance judgement about a strategy, not a safety brake on this
    runtime. Scoping the guard must not silently change what lifecycle reads."""
    import inspect

    from runtime.mvp_runtime.crypto import cycle as cycle_mod

    source = inspect.getsource(cycle_mod.run_crypto_cycle)
    # Whitespace-insensitive: the guard call wrapped onto three lines when it grew the
    # drawdown baseline's routable set, and a test that pins formatting rather than the
    # invariant fails on the next reflow while a real inversion of the two would slip past it.
    flat = " ".join(source.split())
    assert "run_risk_guard( live_readable" in flat                  # guard: LIVE only
    assert "run_lifecycle(active_pool, outcomes" in flat            # lifecycle: full history


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


def test_the_board_explains_a_book_the_pool_itself_cannot_grow():
    """The other half of the lean line. That one says the book is one-way; this says whether the
    POOL is why — which became possible only once the routable set was capped at one strategy
    per context, fixing each context's direction at promotion time."""
    text = render_status_text(_board(pool_directional_capacity={
        "long_contexts": 20, "short_contexts": 0, "routable_contexts": 20,
        "reachable_book": 4, "skew_cap": 4, "cap_binds": True,
    }))
    assert "20개 컨텍스트 중 4개까지만 보유 가능" in text
    assert "롱 20 / 숏 0" in text


def test_a_pool_that_can_fill_its_own_slots_says_nothing():
    """A line that fired on every healthy pool would train the reader to skip the one above it."""
    text = render_status_text(_board(pool_directional_capacity={
        "long_contexts": 12, "short_contexts": 8, "routable_contexts": 20,
        "reachable_book": 20, "skew_cap": 4, "cap_binds": False,
    }))
    assert "보유 가능" not in text


def test_the_board_still_renders_a_status_written_before_the_lean_existed():
    """Records an operator opens the board to read predate this field. A renderer that assumed
    its own newest key would crash on exactly that history — and did, before this test."""
    text = render_status_text(_board(open_positions=[{}] * 3))
    assert "지금" in text and "편중" not in text


def test_the_grants_section_left_with_the_grants():
    """2026-08-10: Thomas retired per-machine grants, and the board's 권한 section went
    with them — a legacy status dict still carrying a `grants` key must render NO grant
    line, because surfacing leftover activation files' expiries would be the board
    claiming a bound nothing enforces."""
    legacy = [{"provider_id": "groq", "expires_at": "2026-07-28T01:56:34Z"}]
    text = render_status_text(_board(grants=legacy))
    assert "권한" not in text and "groq" not in text


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
