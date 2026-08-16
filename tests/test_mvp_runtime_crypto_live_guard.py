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
    ORDER_NOTIONAL_PRICE_UNKNOWN,
    ORDER_NOTIONAL_UNDERSTATED,
    DEFAULT_ABSOLUTE_MAX_NOTIONAL_USDT,
    DEFAULT_MIN_CLEAN_CANARY_ORDERS,
    LIVE_CONFIRMATION_PHRASE,
    STATUS_BLOCKED,
    STATUS_READY,
    STATUS_REPAIR_REQUIRED,
    LiveOrderCounter,
    LiveOrderLimits,
    build_live_order_intent,
    check_declared_notional,
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
        budget_registered=True, allowed_symbols=["BTCUSDT"], limits=_ready_limits(),
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


# === LP2: the write side of that check — the append is idempotent ==================

def _authorized_ledger(root):
    return RealLiveLedger(root=root, authorization=Authorization(
        flags=LIVE_TRADING_FLAGS, provider_id=LIVE_TRADING_PROVIDER_ID,
        activation_sha256="sha256:test", expires_at="2999-01-01T00:00:00Z",
        evidence_ref=".runtime_governance_state/evidence.md",
    ))


def test_a_retried_settlement_appends_nothing_and_says_so(tmp_path):
    """The crash window the read-side alarm cannot repair: outcome appended, book-clear
    failed, and the re-settle rebuilds the SAME settlement_id (same position, same exit
    order) at a later `now`. Landing that row would not double-count — it would fail every
    verified read of the history. The ledger skips it and returns False."""
    ledger = _authorized_ledger(tmp_path)
    assert ledger.append_outcome(_outcome(-5.0)) is True
    retry = _outcome(-5.0, closed_at="2026-07-23T12:05:00Z")
    # outcome_id and the row hash move with `now`; the settlement identity does not.
    assert retry["outcome_id"] != _outcome(-5.0)["outcome_id"]
    assert retry["settlement_id"] == _outcome(-5.0)["settlement_id"]
    assert ledger.append_outcome(retry) is False
    rows = read_live_outcomes(tmp_path)   # still readable — the whole point of the skip
    assert len(rows) == 1 and rows[0]["realized_pnl_usdt"] == -5.0


def test_the_dup_check_never_reads_an_unverifiable_history_as_not_recorded(tmp_path):
    """Fail-closed: a history that cannot prove itself refuses the append rather than being
    appended past — unverifiable is never treated as not-yet-recorded."""
    target = state_dir(tmp_path)
    target.mkdir(parents=True, exist_ok=True)
    path = target / live_pnl.LIVE_OUTCOMES_FILENAME
    path.write_text("{not json\n", encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        _authorized_ledger(tmp_path).append_outcome(_outcome(-1.0))
    assert exc.value.reason_code == live_pnl.LIVE_HISTORY_UNREADABLE
    assert path.read_text(encoding="utf-8") == "{not json\n"   # nothing was appended


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

def test_ledger_env_alone_now_opens_the_gate(tmp_path, monkeypatch):
    """Inverted 2026-07-28 with the rest of the live surface. The ledger MUST move with the
    order adapter: a durable adapter over an inert ledger would trade real money with the
    daily loss breaker reading a permanent zero."""
    monkeypatch.setenv(LIVE_TRADING_ENV, REAL_LIVE_TRADING)
    ledger = select_live_ledger(now=NOW, root=tmp_path)
    assert isinstance(ledger, RealLiveLedger)
    ledger.append_outcome(_outcome(-1.0))
    assert len(read_live_outcomes(tmp_path)) == 1   # it really did persist


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
    assert any("live trading is not enabled" in b for b in verdict["blocks"])


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


# === step 6b: the guard requires a registered budget ===============================

def test_no_registered_budget_blocks():
    """autonomous_spend_without_registered_budget: '0' — no live order without one, even when
    everything else is ready."""
    verdict = evaluate_live_order_guard(_intent(), **_ready(budget_registered=False))
    assert verdict["approved"] is False
    assert any("registered live-trading budget" in b for b in verdict["blocks"])


def test_budget_default_is_fail_closed():
    """A caller that never resolves a budget cannot authorize an order on env-only caps: the
    fact defaults to False."""
    facts = _ready()
    facts.pop("budget_registered")
    verdict = evaluate_live_order_guard(_intent(), **facts)
    assert any("registered live-trading budget" in b for b in verdict["blocks"])


def test_resolve_limits_uses_the_registered_budget(tmp_path):
    from runtime.mvp_runtime.crypto import live_budget
    from runtime.mvp_runtime.crypto.live_order import resolve_live_order_limits

    caps = dict(max_order_notional_usdt=60.0, absolute_max_notional_usdt=200.0,
                max_daily_order_count=2, max_open_notional_usdt=120.0,
                daily_loss_limit_usdt=20.0, min_clean_canary_orders=3)
    rec = live_budget.build_live_trading_budget_record(
        caps=caps, symbol_allowlist=["BTCUSDT"], valid_from="2026-07-25T00:00:00Z",
        valid_until="2026-08-25T00:00:00Z", registered_by="thomas", registered_at="2026-07-25T00:00:00Z")
    live_budget.write_registered_budget(rec, root=tmp_path)
    limits, status = resolve_live_order_limits(tmp_path, now="2026-08-01T00:00:00Z")
    assert status["valid"] is True
    assert limits.max_order_notional_usdt == 60.0 and limits.max_daily_order_count == 2


def test_resolve_limits_without_a_budget_is_blocking(tmp_path):
    from runtime.mvp_runtime.crypto.live_order import resolve_live_order_limits

    limits, status = resolve_live_order_limits(tmp_path, now=NOW)
    assert status["valid"] is False
    # Blocking-default caps: a guard fed these refuses on every cap AND on the budget itself.
    assert limits.max_order_notional_usdt == 0.0 and limits.max_daily_order_count == 0
    guard = evaluate_live_order_guard(
        _intent(), gate_open=True, runtime_active=True, daily_loss_breached=False,
        clean_canary_orders=3, submitted_today=0, current_open_notional_usdt=0.0,
        budget_registered=status["valid"], limits=limits)
    assert guard["approved"] is False
    assert any("registered live-trading budget" in b for b in guard["blocks"])


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


# === increment 2b: the canary guard mode ===========================================

def test_canary_is_exempt_from_the_promotion_gate():
    """The chicken-and-egg: requiring >= 3 clean canaries before the FIRST canary can be placed
    is unsatisfiable. A canary is what earns that evidence, so the gate does not apply to it."""
    from runtime.mvp_runtime.crypto.live_order import CANARY_CONFIRMATION_PHRASE
    facts = _ready(clean_canary_orders=0,
                   limits=_ready_limits(canary_confirmation=CANARY_CONFIRMATION_PHRASE))
    verdict = evaluate_live_order_guard(_intent(), canary=True, **facts)
    assert verdict["approved"] is True and verdict["canary"] is True
    # ...while the autonomous path with the same zero evidence is still blocked.
    assert evaluate_live_order_guard(_intent(), **_ready(clean_canary_orders=0))["approved"] is False


def test_the_autonomous_phrase_cannot_authorize_a_canary():
    """One phrase per capability, in both directions."""
    from runtime.mvp_runtime.crypto.live_order import CANARY_CONFIRMATION_ENV
    # Only the autonomous phrase is set (that is what _ready_limits does) => a canary is refused.
    verdict = evaluate_live_order_guard(_intent(), canary=True, **_ready(clean_canary_orders=0))
    assert verdict["approved"] is False
    assert any(CANARY_CONFIRMATION_ENV in b for b in verdict["blocks"])


def test_the_canary_phrase_cannot_authorize_autonomous_trading():
    from runtime.mvp_runtime.crypto.live_order import CANARY_CONFIRMATION_PHRASE
    limits = _ready_limits(confirmation="", canary_confirmation=CANARY_CONFIRMATION_PHRASE)
    verdict = evaluate_live_order_guard(_intent(), **_ready(limits=limits))
    assert verdict["approved"] is False
    assert any(CONFIRMATION_ENV in b for b in verdict["blocks"])


@pytest.mark.parametrize("fact, needle", [
    (dict(gate_open=False), "live trading is not enabled"),
    (dict(runtime_active=False), "external_execution"),
    (dict(daily_loss_breached=True), "halted for today"),
    (dict(submitted_today=2), "daily order cap reached"),
    (dict(budget_registered=False), "registered live-trading budget"),
    (dict(current_open_notional_usdt=90.0), "open exposure"),
])
def test_a_canary_still_obeys_every_other_check(fact, needle):
    """Only the promotion gate and the phrase differ; a canary is not a bypass."""
    from runtime.mvp_runtime.crypto.live_order import CANARY_CONFIRMATION_PHRASE
    facts = _ready(clean_canary_orders=0,
                   limits=_ready_limits(canary_confirmation=CANARY_CONFIRMATION_PHRASE))
    facts.update(fact)
    verdict = evaluate_live_order_guard(_intent(), canary=True, **facts)
    assert verdict["approved"] is False
    assert any(needle in b for b in verdict["blocks"])


def test_canary_mode_defaults_off_so_the_promotion_gate_is_fail_closed():
    facts = _ready(clean_canary_orders=0)
    assert evaluate_live_order_guard(_intent(), **facts)["canary"] is False
    assert any("promotion not ready" in b for b in evaluate_live_order_guard(_intent(), **facts)["blocks"])


def test_a_canary_still_refuses_a_manual_kill_and_a_connectivity_probe():
    from runtime.mvp_runtime.crypto.live_order import CANARY_CONFIRMATION_PHRASE
    killed = _ready(clean_canary_orders=0, limits=_ready_limits(
        canary_confirmation=CANARY_CONFIRMATION_PHRASE, manual_kill_switch=True))
    assert evaluate_live_order_guard(_intent(), canary=True, **killed)["approved"] is False
    probe = _ready(clean_canary_orders=0, limits=_ready_limits(
        canary_confirmation=CANARY_CONFIRMATION_PHRASE))
    verdict = evaluate_live_order_guard(_intent(connectivity_test=True), canary=True, **probe)
    assert verdict["approved"] is False


# --- the seam: resolved limits, not hand-built ones -----------------------------
#
# Every canary test above builds its limits with `_ready_limits(canary_confirmation=...)`.
# The canary SCRIPT does not — it calls `resolve_live_order_limits`, which until 2026-07-26
# dropped the canary phrase on both branches. Both sides were tested; the join was not, so
# `place_canary_order.py` was permanently refused with "canary confirmation phrase not
# present" — the only live door there is, and the one that has to work before any autonomous
# path can. These test the join.

def _register_budget(root, **cap_overrides):
    from runtime.mvp_runtime.crypto import live_budget

    caps = dict(max_order_notional_usdt=60.0, absolute_max_notional_usdt=200.0,
                max_daily_order_count=2, max_open_notional_usdt=120.0,
                daily_loss_limit_usdt=20.0, min_clean_canary_orders=3)
    caps.update(cap_overrides)
    record = live_budget.build_live_trading_budget_record(
        caps=caps, symbol_allowlist=["BTCUSDT"], valid_from="2026-07-25T00:00:00Z",
        valid_until="2026-08-25T00:00:00Z", registered_by="thomas",
        registered_at="2026-07-25T00:00:00Z")
    live_budget.write_registered_budget(record, root=root)


@pytest.mark.parametrize("with_budget", [True, False])
def test_resolve_carries_both_operator_phrases_on_every_branch(tmp_path, monkeypatch, with_budget):
    """The caps come from the registered budget; the phrases and the manual kill stay env,
    and must survive BOTH branches — the branch taken when no budget is registered is the
    one an operator hits first."""
    from runtime.mvp_runtime.crypto.live_order import (
        CANARY_CONFIRMATION_PHRASE, LIVE_CONFIRMATION_PHRASE, resolve_live_order_limits,
    )

    monkeypatch.setenv("MVP_LIVE_CONFIRMATION", LIVE_CONFIRMATION_PHRASE)
    monkeypatch.setenv("MVP_LIVE_CANARY_CONFIRMATION", CANARY_CONFIRMATION_PHRASE)
    if with_budget:
        _register_budget(tmp_path)

    limits, status = resolve_live_order_limits(tmp_path, now="2026-08-01T00:00:00Z")
    assert status["valid"] is with_budget
    assert limits.confirmation_present() is True
    assert limits.canary_confirmation_present() is True


def test_the_canary_the_script_would_place_is_actually_approvable(tmp_path, monkeypatch):
    """The regression this section exists for: resolve + the canary guard, exactly as
    `scripts/place_canary_order.py` composes them, must be able to reach approved."""
    from runtime.mvp_runtime.crypto.live_order import (
        CANARY_CONFIRMATION_PHRASE, resolve_live_order_limits,
    )

    monkeypatch.setenv("MVP_LIVE_CANARY_CONFIRMATION", CANARY_CONFIRMATION_PHRASE)
    _register_budget(tmp_path)
    limits, status = resolve_live_order_limits(tmp_path, now="2026-08-01T00:00:00Z")

    verdict = evaluate_live_order_guard(
        _intent(), gate_open=True, runtime_active=True, daily_loss_breached=False,
        # 0 clean canaries is the real state before the first one; canary mode is what
        # exempts the promotion gate, and the whole point of this path.
        clean_canary_orders=0, submitted_today=0, current_open_notional_usdt=0.0,
        budget_registered=status["valid"],
        # Composed from the same resolved budget the script uses — the parity this test is
        # for. Reading the allowlist off `status` rather than restating it here is what keeps
        # the scope the script passes and the scope this test passes the same object.
        allowed_symbols=status.get("symbol_allowlist") or (),
        limits=limits, canary=True,
    )
    assert verdict["blocks"] == []
    assert verdict["approved"] is True


def test_the_autonomous_phrase_alone_still_cannot_authorize_a_canary(tmp_path, monkeypatch):
    """One phrase per capability survives the fix: setting only the autonomous phrase must
    not let a canary through the resolved-limits path either."""
    from runtime.mvp_runtime.crypto.live_order import (
        LIVE_CONFIRMATION_PHRASE, resolve_live_order_limits,
    )

    monkeypatch.setenv("MVP_LIVE_CONFIRMATION", LIVE_CONFIRMATION_PHRASE)
    monkeypatch.delenv("MVP_LIVE_CANARY_CONFIRMATION", raising=False)
    _register_budget(tmp_path)
    limits, status = resolve_live_order_limits(tmp_path, now="2026-08-01T00:00:00Z")

    verdict = evaluate_live_order_guard(
        _intent(), gate_open=True, runtime_active=True, daily_loss_breached=False,
        clean_canary_orders=0, submitted_today=0, current_open_notional_usdt=0.0,
        budget_registered=status["valid"], limits=limits, canary=True,
    )
    assert verdict["approved"] is False
    assert any("canary confirmation phrase not present" in b for b in verdict["blocks"])


def test_neither_guard_has_an_env_cap_fallback():
    """`limits` used to default to `LiveOrderLimits.from_env()` — a second source of caps,
    reachable by forgetting an argument, in a design whose whole point is that the registered
    budget is the only one. Omitting it is now a TypeError, not a quiet fallback."""
    facts = _ready()
    facts.pop("limits")
    with pytest.raises(TypeError):
        evaluate_live_order_guard(_intent(), **facts)
    with pytest.raises(TypeError):
        evaluate_live_close_guard(_intent(reduce_only=True), gate_open=True)


def test_from_env_cannot_produce_a_cap(monkeypatch):
    """The other half of the same rule, one level down.

    Making `limits` required closed the *call sites*, but `LiveOrderLimits.from_env()` kept
    parsing `MVP_LIVE_MAX_*`, so any caller could still reconstruct an env-derived cap set and
    hand it in — the second source was one method call away. `from_env` now reads only the
    operator-env fields, and every cap it returns is the blocking default however the
    environment is set.
    """
    for name, value in (
        ("MVP_LIVE_MAX_ORDER_NOTIONAL_USDT", "150"),
        ("MVP_LIVE_ABSOLUTE_MAX_NOTIONAL_USDT", "9999"),
        ("MVP_LIVE_MAX_DAILY_ORDER_COUNT", "99"),
        ("MVP_LIVE_MAX_OPEN_NOTIONAL_USDT", "9999"),
        ("MVP_LIVE_DAILY_LOSS_LIMIT_USDT", "500"),
        ("MVP_LIVE_MIN_CLEAN_CANARY_ORDERS", "1"),
    ):
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("MVP_LIVE_CONFIRMATION", LIVE_CONFIRMATION_PHRASE)

    limits = LiveOrderLimits.from_env()

    assert limits.max_order_notional_usdt == 0.0
    assert limits.max_daily_order_count == 0
    assert limits.max_open_notional_usdt == 0.0
    assert limits.daily_loss_limit_usdt == 0.0
    # Not zero, but the *source* default — the env's 9999 / 1 must not reach either. A raised
    # ceiling and a lowered promotion minimum are the two that widen authority quietly.
    assert limits.absolute_max_notional_usdt == DEFAULT_ABSOLUTE_MAX_NOTIONAL_USDT
    assert limits.min_clean_canary_orders == DEFAULT_MIN_CLEAN_CANARY_ORDERS
    # What it IS for still works.
    assert limits.confirmation_present() is True


def test_an_unconfigured_cap_names_the_registered_budget_not_an_env_var():
    """The refusal has to name the action that fixes it.

    Every "not configured" branch here is reachable only when no valid budget backs the caps
    (a registered budget's caps must all be > 0), so these fire alongside the budget block —
    and each one used to add `MVP_LIVE_MAX_ORDER_NOTIONAL_USDT must be > 0`, sending an
    operator holding real keys to a variable that has authorized nothing since step 6b. Same
    shape as #201: fail-closed, wrong next step.
    """
    verdict = evaluate_live_order_guard(
        _intent(), gate_open=True, runtime_active=True, daily_loss_breached=True,
        clean_canary_orders=0, submitted_today=0, current_open_notional_usdt=0.0,
        budget_registered=False, limits=LiveOrderLimits(),
    )
    unconfigured = [b for b in verdict["blocks"] if "not configured" in b]
    assert len(unconfigured) == 4, unconfigured    # loss, per-order, daily count, exposure
    for block in unconfigured:
        assert "scripts/register_live_trading_budget.py" in block, block
        assert "MVP_LIVE_" not in block, block

    # The fifth one needs its own setup: `min_clean_canary_orders` defaults to 3, so the
    # blocking-defaults instance above takes the "not ready yet" branch, not "not configured".
    promotion = evaluate_live_order_guard(
        _intent(), gate_open=True, runtime_active=True, daily_loss_breached=False,
        clean_canary_orders=0, submitted_today=0, current_open_notional_usdt=0.0,
        budget_registered=False, limits=LiveOrderLimits(min_clean_canary_orders=0),
    )
    minimum = [b for b in promotion["blocks"] if "promotion minimum is not configured" in b]
    assert len(minimum) == 1, promotion["blocks"]
    assert "scripts/register_live_trading_budget.py" in minimum[0]


# === the declared notional, and the price it is checked against =====================

class TestDeclaredNotional:
    """`check_declared_notional` — pure, so every branch is cheap to state exactly.

    It exists because `--quantity` (what reaches the venue) and `--notional` (what the caps
    measure) were independent operator inputs with nothing comparing them.
    """

    def test_a_matching_declaration_passes(self):
        result = check_declared_notional(
            quantity=0.001, declared_notional_usdt=64.51, reference_price=64_512.0)
        assert result["ok"] is True
        assert result["implied_notional_usdt"] == pytest.approx(64.512)

    def test_an_under_declaration_is_refused_and_names_the_right_number(self):
        result = check_declared_notional(
            quantity=0.001, declared_notional_usdt=60.0, reference_price=64_512.0)
        assert result["ok"] is False
        assert result["reason_code"] == ORDER_NOTIONAL_UNDERSTATED
        # An operator who is told "wrong" and not "use this" guesses again.
        assert "64.51" in result["message"]

    def test_over_declaring_passes(self):
        """It only makes every cap stricter; refusing it would block a careful operator."""
        result = check_declared_notional(
            quantity=0.001, declared_notional_usdt=500.0, reference_price=64_512.0)
        assert result["ok"] is True

    def test_a_tick_of_drift_is_tolerated(self):
        """The operator reads a price a moment before sending; exact equality is not realistic."""
        implied = 0.001 * 64_512.0
        assert check_declared_notional(
            quantity=0.001, declared_notional_usdt=implied * 0.995,
            reference_price=64_512.0)["ok"] is True

    def test_drift_beyond_the_tolerance_is_not(self):
        implied = 0.001 * 64_512.0
        assert check_declared_notional(
            quantity=0.001, declared_notional_usdt=implied * 0.98,
            reference_price=64_512.0)["ok"] is False

    @pytest.mark.parametrize("price", [None, 0.0, -1.0])
    def test_no_usable_price_is_a_refusal(self, price):
        """"Nothing to check" must never read as "approved"."""
        result = check_declared_notional(
            quantity=0.001, declared_notional_usdt=60.0, reference_price=price)
        assert result["ok"] is False
        assert result["reason_code"] == ORDER_NOTIONAL_PRICE_UNKNOWN
