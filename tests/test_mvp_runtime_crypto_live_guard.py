"""LP2/LP3 tests — the live P&L ledger, the daily-loss breaker, and the final order guard.

Under test: an unconfigured risk limit reads as HALTED and never as unlimited; the live
history is a verified read so a tampered or duplicated outcome cannot argue the breaker
clear; every guard check accumulates rather than short-circuiting; a cap above the absolute
ceiling is refused rather than clamped; a missing notional is never back-filled from the cap;
and the reduceOnly close path stays open when the entry path is halted.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.crypto import live_pnl
from runtime.mvp_runtime.crypto.live_order import (
    CONFIRMATION_ENV,
    LIVE_CONFIRMATION_PHRASE,
    STATUS_BLOCKED,
    STATUS_READY,
    STATUS_REPAIR_REQUIRED,
    LiveOrderCounter,
    LiveOrderLimits,
    build_live_order_intent,
    count_today,
    enrich_order_identity,
    evaluate_live_close_guard,
    evaluate_live_order_guard,
    make_client_order_id,
    make_idempotency_key,
    render_guard_text,
    select_live_order_counter,
)
from runtime.mvp_runtime.crypto.live_pnl import (
    LIVE_TRADING_ENV,
    LIVE_TRADING_FLAGS,
    LIVE_TRADING_PROVIDER_ID,
    REAL_LIVE_TRADING,
    DryRunLiveLedger,
    RealLiveLedger,
    build_live_outcome_record,
    daily_loss_limit_breached,
    daily_realized_pnl,
    live_risk_snapshot,
    read_live_outcomes,
    select_live_ledger,
    state_dir,
)
from runtime.mvp_runtime.errors import SafetyGateBlocked, ToolError
from runtime.mvp_runtime.safety_gate import Authorization

NOW = "2026-07-23T12:00:00Z"
TODAY = "2026-07-23"

_LIVE_AUTH = Authorization(
    flags=LIVE_TRADING_FLAGS, provider_id=LIVE_TRADING_PROVIDER_ID, activation_sha256="sha256:test",
    expires_at="2999-01-01T00:00:00Z", evidence_ref=".runtime_governance_state/evidence.md",
)


def _outcome(pnl: float, *, closed_at: str = NOW, position_id: str = "pos-1", **kw):
    return build_live_outcome_record(
        realized_pnl_usdt=pnl, symbol="BTCUSDT", side="SELL", quantity=0.01,
        position_id=position_id, now=closed_at, **kw,
    )


def _write_outcomes(root, records):
    target = state_dir(root)
    target.mkdir(parents=True, exist_ok=True)
    path = target / live_pnl.LIVE_OUTCOMES_FILENAME
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


def _ready_limits(**overrides) -> LiveOrderLimits:
    """A fully configured, conservative budget — the only shape that can reach READY."""
    base = dict(
        max_order_notional_usdt=60.0,
        absolute_max_notional_usdt=200.0,
        max_daily_order_count=2,
        max_open_notional_usdt=120.0,
        daily_loss_limit_usdt=20.0,
        min_clean_canary_orders=3,
        confirmation=LIVE_CONFIRMATION_PHRASE,
        manual_kill_switch=False,
    )
    base.update(overrides)
    return LiveOrderLimits(**base)


def _intent(**overrides):
    intent = {
        "status": "ORDER_INTENT_CREATED",
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "quantity": 0.001,
        "order_notional_usdt": 55.0,
        "reduce_only": False,
        "connectivity_test": False,
    }
    intent.update(overrides)
    return intent


def _ready(**kw):
    facts = dict(
        gate_open=True, runtime_active=True, daily_loss_breached=False,
        clean_canary_orders=3, submitted_today=0, current_open_notional_usdt=0.0,
        limits=_ready_limits(),
    )
    facts.update(kw)
    return facts


# === LP2: the breaker ==============================================================

@pytest.mark.parametrize("limit", [None, 0, 0.0, -5, "", "abc"])
def test_unconfigured_loss_limit_reads_as_breached(limit):
    """The single most important line in the module: no limit means HALTED, not unlimited."""
    assert daily_loss_limit_breached(limit, outcomes=[]) is True


def test_configured_limit_not_reached():
    assert daily_loss_limit_breached(20.0, outcomes=[_outcome(-5.0)], day=TODAY) is False


def test_configured_limit_reached_exactly():
    assert daily_loss_limit_breached(20.0, outcomes=[_outcome(-20.0)], day=TODAY) is True


def test_limit_sign_is_ignored():
    """A limit typed as -20 means the same 20 USDT of loss."""
    assert daily_loss_limit_breached(-20.0, outcomes=[_outcome(-25.0)], day=TODAY) is True


def test_breaker_resets_at_utc_midnight():
    yesterday = _outcome(-50.0, closed_at="2026-07-22T23:59:59Z", position_id="pos-y")
    assert daily_loss_limit_breached(20.0, outcomes=[yesterday], day=TODAY) is False


def test_daily_pnl_sums_only_the_target_day():
    rows = [
        _outcome(10.0, position_id="a"),
        _outcome(-4.0, position_id="b"),
        _outcome(-99.0, closed_at="2026-07-20T00:00:00Z", position_id="c"),
    ]
    assert daily_realized_pnl(rows, day=TODAY) == pytest.approx(6.0)


def test_non_numeric_pnl_raises_instead_of_counting_zero():
    """Reading a malformed loss as zero would understate the day and clear the breaker."""
    bad = dict(_outcome(-5.0))
    bad["realized_pnl_usdt"] = "oops"
    with pytest.raises(ToolError) as exc:
        daily_realized_pnl([bad], day=TODAY)
    assert exc.value.reason_code == live_pnl.LIVE_HISTORY_TAMPERED


# === LP2: the verified read ========================================================

def test_missing_store_is_honestly_empty(tmp_path):
    assert read_live_outcomes(tmp_path) == []


def test_roundtrip(tmp_path):
    _write_outcomes(tmp_path, [_outcome(1.0, position_id="a"), _outcome(2.0, position_id="b")])
    assert [r["realized_pnl_usdt"] for r in read_live_outcomes(tmp_path)] == [1.0, 2.0]


def test_tampered_record_refuses(tmp_path):
    record = dict(_outcome(-1.0))
    record["realized_pnl_usdt"] = 999.0  # edited after hashing
    _write_outcomes(tmp_path, [record])
    with pytest.raises(ToolError) as exc:
        read_live_outcomes(tmp_path)
    assert exc.value.reason_code == live_pnl.LIVE_HISTORY_TAMPERED


def test_duplicate_settlement_refuses(tmp_path):
    """A duplicated settlement is the double-count signature; it must not reach the breaker."""
    record = _outcome(-10.0)
    _write_outcomes(tmp_path, [record, record])
    with pytest.raises(ToolError) as exc:
        read_live_outcomes(tmp_path)
    assert exc.value.reason_code == live_pnl.LIVE_HISTORY_DUPLICATE


def test_unparseable_line_refuses(tmp_path):
    target = state_dir(tmp_path)
    target.mkdir(parents=True, exist_ok=True)
    (target / live_pnl.LIVE_OUTCOMES_FILENAME).write_text("{not json\n", encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        read_live_outcomes(tmp_path)
    assert exc.value.reason_code == live_pnl.LIVE_HISTORY_UNREADABLE


def test_risk_snapshot_fails_closed_on_unreadable_history(tmp_path):
    target = state_dir(tmp_path)
    target.mkdir(parents=True, exist_ok=True)
    (target / live_pnl.LIVE_OUTCOMES_FILENAME).write_text("garbage\n", encoding="utf-8")
    snapshot = live_risk_snapshot(limit_usdt=20.0, root=tmp_path, now=NOW)
    assert snapshot["daily_loss_limit_breached"] is True
    assert snapshot["history_error"] == live_pnl.LIVE_HISTORY_UNREADABLE
    assert snapshot["daily_realized_pnl_usdt"] is None


def test_risk_snapshot_reports_the_day(tmp_path):
    _write_outcomes(tmp_path, [_outcome(-3.0)])
    snapshot = live_risk_snapshot(limit_usdt=20.0, root=tmp_path, now=NOW)
    assert snapshot["daily_realized_pnl_usdt"] == pytest.approx(-3.0)
    assert snapshot["daily_loss_limit_configured"] is True
    assert snapshot["daily_loss_limit_breached"] is False
    assert snapshot["closed_trade_count"] == 1


# === LP2: the one switch ===========================================================

def test_ledger_env_alone_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv(LIVE_TRADING_ENV, REAL_LIVE_TRADING)
    with pytest.raises(SafetyGateBlocked) as exc:
        select_live_ledger(now=NOW, root=tmp_path)
    assert exc.value.reason_code == "ACTIVATION_MISSING"


def test_ledger_default_is_inert(tmp_path, monkeypatch):
    monkeypatch.delenv(LIVE_TRADING_ENV, raising=False)
    ledger = select_live_ledger(now=NOW, root=tmp_path)
    assert isinstance(ledger, DryRunLiveLedger)
    ledger.append_outcome(_outcome(-1.0))
    assert read_live_outcomes(tmp_path) == []  # wrote nothing


def test_real_ledger_refuses_without_authorization(tmp_path):
    with pytest.raises(SafetyGateBlocked) as exc:
        RealLiveLedger(root=tmp_path, authorization=None).append_outcome(_outcome(-1.0))
    assert exc.value.reason_code == "NOT_AUTHORIZED"


def test_the_same_grant_covers_ledger_and_counter():
    """One switch: both live-side writers demand the identical provider and flag set."""
    assert RealLiveLedger.provider_id == LiveOrderCounter.provider_id == LIVE_TRADING_PROVIDER_ID
    assert "network_access" in LIVE_TRADING_FLAGS and "filesystem_write" in LIVE_TRADING_FLAGS


# === LP3: the final guard ==========================================================

def test_fully_configured_intent_is_ready():
    verdict = evaluate_live_order_guard(_intent(), **_ready())
    assert verdict["status"] == STATUS_READY and verdict["approved"] is True
    assert verdict["blocks"] == [] and verdict["repairs"] == []


def test_closed_gate_blocks():
    verdict = evaluate_live_order_guard(_intent(), **_ready(gate_open=False))
    assert verdict["approved"] is False
    assert any("grant is not active" in b for b in verdict["blocks"])


def test_missing_confirmation_blocks():
    verdict = evaluate_live_order_guard(
        _intent(), **_ready(limits=_ready_limits(confirmation=""))
    )
    assert any(CONFIRMATION_ENV in b for b in verdict["blocks"])


def test_wrong_confirmation_phrase_blocks():
    """The canary phrase must not authorize autonomous trading."""
    verdict = evaluate_live_order_guard(
        _intent(),
        **_ready(limits=_ready_limits(confirmation="I_UNDERSTAND_THIS_PLACES_A_REAL_LIVE_MAINNET_ORDER")),
    )
    assert verdict["approved"] is False


def test_manual_kill_switch_blocks():
    verdict = evaluate_live_order_guard(
        _intent(), **_ready(limits=_ready_limits(manual_kill_switch=True))
    )
    assert any("manual kill switch" in b for b in verdict["blocks"])


def test_paused_runtime_blocks_a_live_entry():
    """kill_blocks: external_execution finally has a door."""
    verdict = evaluate_live_order_guard(_intent(), **_ready(runtime_active=False))
    assert any("external_execution" in b for b in verdict["blocks"])


def test_tripped_breaker_blocks():
    verdict = evaluate_live_order_guard(_intent(), **_ready(daily_loss_breached=True))
    assert any("halted for today" in b for b in verdict["blocks"])


def test_insufficient_canary_evidence_blocks():
    verdict = evaluate_live_order_guard(_intent(), **_ready(clean_canary_orders=1))
    assert any("promotion not ready" in b for b in verdict["blocks"])


def test_connectivity_probe_cannot_use_the_live_path():
    verdict = evaluate_live_order_guard(_intent(connectivity_test=True), **_ready())
    assert any("connectivity_test" in b for b in verdict["blocks"])


def test_unconfigured_caps_block():
    verdict = evaluate_live_order_guard(
        _intent(),
        **_ready(limits=_ready_limits(
            max_order_notional_usdt=0.0, max_daily_order_count=0, max_open_notional_usdt=0.0
        )),
    )
    assert len([b for b in verdict["blocks"] if "not configured" in b]) == 3


def test_cap_above_the_absolute_ceiling_is_refused_not_clamped():
    verdict = evaluate_live_order_guard(
        _intent(order_notional_usdt=50.0),
        **_ready(limits=_ready_limits(max_order_notional_usdt=500.0, absolute_max_notional_usdt=200.0)),
    )
    assert any("exceeds the absolute ceiling" in b for b in verdict["blocks"])


def test_oversized_order_blocks():
    verdict = evaluate_live_order_guard(_intent(order_notional_usdt=100.0), **_ready())
    assert any("exceeds the effective cap" in b for b in verdict["blocks"])


def test_daily_count_reached_blocks():
    verdict = evaluate_live_order_guard(_intent(), **_ready(submitted_today=2))
    assert any("daily order cap reached" in b for b in verdict["blocks"])


def test_exposure_cap_counts_the_pending_order():
    """90 open + 55 new = 145 > 120, even though neither alone exceeds the cap."""
    verdict = evaluate_live_order_guard(_intent(), **_ready(current_open_notional_usdt=90.0))
    assert any("open exposure" in b for b in verdict["blocks"])


def test_malformed_intent_is_a_repair_not_a_block():
    verdict = evaluate_live_order_guard(_intent(quantity=0), **_ready())
    assert verdict["status"] == STATUS_REPAIR_REQUIRED
    assert verdict["blocks"] == []
    assert any("quantity" in r for r in verdict["repairs"])


def test_blocks_outrank_repairs():
    verdict = evaluate_live_order_guard(_intent(quantity=0), **_ready(gate_open=False))
    assert verdict["status"] == STATUS_BLOCKED


def test_checks_accumulate_rather_than_short_circuiting():
    """The operator must see every reason at once, not just the first."""
    verdict = evaluate_live_order_guard(
        _intent(),
        **_ready(gate_open=False, runtime_active=False, daily_loss_breached=True,
                 clean_canary_orders=0, limits=_ready_limits(confirmation="", manual_kill_switch=True)),
    )
    assert len(verdict["blocks"]) >= 6


def test_guard_text_is_ascii():
    render_guard_text(evaluate_live_order_guard(_intent(), **_ready(gate_open=False))).encode("ascii")


# === LP3: the close guard ==========================================================

def test_close_is_allowed_while_the_entry_path_is_halted():
    """A halt that traps you in a losing position is worse than the halt prevents."""
    intent = _intent(reduce_only=True, direction="LONG")
    verdict = evaluate_live_close_guard(intent, gate_open=True, limits=_ready_limits())
    assert verdict["approved"] is True and verdict["close_guard"] is True


def test_close_requires_reduce_only():
    verdict = evaluate_live_close_guard(_intent(reduce_only=False), gate_open=True, limits=_ready_limits())
    assert any("reduceOnly" in b for b in verdict["blocks"])


def test_close_still_needs_the_grant_and_the_phrase():
    intent = _intent(reduce_only=True)
    assert evaluate_live_close_guard(intent, gate_open=False, limits=_ready_limits())["approved"] is False
    assert evaluate_live_close_guard(
        intent, gate_open=True, limits=_ready_limits(confirmation="")
    )["approved"] is False


# === LP3: intent + idempotency =====================================================

def test_intent_refuses_a_missing_direction():
    with pytest.raises(ToolError) as exc:
        build_live_order_intent({}, symbol="BTCUSDT", quantity=0.001, notional_usdt=55.0, now=NOW)
    assert exc.value.reason_code == "MALFORMED_DIRECTION"


def test_intent_never_backfills_notional_from_the_cap(monkeypatch):
    monkeypatch.setenv("MVP_LIVE_MAX_ORDER_NOTIONAL_USDT", "60")
    with pytest.raises(ToolError) as exc:
        build_live_order_intent(
            {"direction": "LONG"}, symbol="BTCUSDT", quantity=0.001, notional_usdt=0.0, now=NOW
        )
    assert exc.value.reason_code == "MISSING_ORDER_NOTIONAL"


def test_intent_sides_and_identity():
    intent = build_live_order_intent(
        {"direction": "SHORT", "entry_price": 60000.0}, symbol="BTCUSDT",
        quantity=0.001, notional_usdt=60.0, now=NOW,
    )
    assert intent["side"] == "SELL" and intent["execution_stage"] == "live"
    assert intent["status"] == "ORDER_INTENT_CREATED"
    assert len(intent["client_order_id"]) <= 36


def test_reduce_only_intent_flips_the_side():
    """Closing a LONG sells; closing a SHORT buys."""
    intent = build_live_order_intent(
        {"direction": "LONG"}, symbol="BTCUSDT", quantity=0.001,
        notional_usdt=60.0, now=NOW, reduce_only=True, close_reason="stop_loss",
    )
    assert intent["side"] == "SELL" and intent["reduce_only"] is True


def test_idempotency_key_is_stable_and_retry_safe():
    payload = {"symbol": "BTCUSDT", "direction": "LONG", "position_id": "p1"}
    assert make_idempotency_key(payload) == make_idempotency_key(dict(payload))
    assert make_idempotency_key(payload) != make_idempotency_key({**payload, "position_id": "p2"})


def test_client_order_id_fits_the_venue_limit():
    key = make_idempotency_key({"a": "b"})
    assert len(make_client_order_id("BTCUSDT", "LONG", key)) <= 36


def test_enrich_is_deterministic():
    a = enrich_order_identity({"symbol": "BTCUSDT", "direction": "LONG", "created_at": NOW})
    b = enrich_order_identity({"symbol": "BTCUSDT", "direction": "LONG", "created_at": NOW})
    assert a["client_order_id"] == b["client_order_id"]


# === LP3: the counter ==============================================================

def test_counter_starts_at_zero(tmp_path):
    assert count_today(tmp_path, day=TODAY) == 0


def test_counter_increments_under_the_grant(tmp_path):
    counter = LiveOrderCounter(root=tmp_path, authorization=_LIVE_AUTH)
    assert counter.record_submission(day=TODAY) == 1
    assert counter.record_submission(day=TODAY) == 2
    assert count_today(tmp_path, day=TODAY) == 2
    assert count_today(tmp_path, day="2026-07-24") == 0  # per-day budget


def test_counter_refuses_without_authorization(tmp_path):
    with pytest.raises(SafetyGateBlocked):
        LiveOrderCounter(root=tmp_path, authorization=None).record_submission(day=TODAY)


def test_unreadable_counter_fails_closed(tmp_path):
    """Reading zero would hand back the entire daily budget."""
    target = state_dir(tmp_path)
    target.mkdir(parents=True, exist_ok=True)
    (target / "live_order_counter.json").write_text("nope", encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        count_today(tmp_path, day=TODAY)
    assert exc.value.reason_code == "LIVE_COUNTER_UNREADABLE"


def test_counter_selection_is_inert_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv(LIVE_TRADING_ENV, raising=False)
    counter = select_live_order_counter(now=NOW, root=tmp_path)
    assert counter.record_submission(day=TODAY) == 0
    assert count_today(tmp_path, day=TODAY) == 0
