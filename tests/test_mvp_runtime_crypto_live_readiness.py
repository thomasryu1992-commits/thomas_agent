"""LP6 tests — canary promotion evidence and the readiness board.

Under test: cleanliness is derived from the venue's answer and can never be asserted by a
caller; damaged evidence counts as zero rather than as the last good number; a promotion
minimum of zero is refused rather than trivially satisfied; and the readiness board reports
every gate honestly, never raises on an unreadable input, and cannot say READY while no order
path exists.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.crypto import live_promotion, live_readiness
from runtime.mvp_runtime.crypto.live_pnl import (
    LIVE_TRADING_ENV,
    LIVE_TRADING_FLAGS,
    LIVE_TRADING_PROVIDER_ID,
    REAL_LIVE_TRADING,
    state_dir,
)
from runtime.mvp_runtime.crypto.live_promotion import (
    DEFAULT_MIN_CLEAN_CANARY_ORDERS,
    DryRunCanaryRegistry,
    RealCanaryRegistry,
    build_canary_order_record,
    clean_canary_order_count,
    promotion_status,
    read_canary_orders,
    select_canary_registry,
)
from runtime.mvp_runtime.crypto.live_order import CONFIRMATION_ENV, LIVE_CONFIRMATION_PHRASE
from runtime.mvp_runtime.errors import SafetyGateBlocked, ToolError
from runtime.mvp_runtime.safety_gate import Authorization

NOW = "2026-07-23T12:00:00Z"

_LIVE_AUTH = Authorization(
    flags=LIVE_TRADING_FLAGS, provider_id=LIVE_TRADING_PROVIDER_ID, activation_sha256="sha256:test",
    expires_at="2999-01-01T00:00:00Z", evidence_ref=".runtime_governance_state/evidence.md",
)

_LIVE_ENVS = (
    LIVE_TRADING_ENV, CONFIRMATION_ENV, "MVP_LIVE_MANUAL_KILL_SWITCH",
    "MVP_LIVE_MAX_ORDER_NOTIONAL_USDT", "MVP_LIVE_ABSOLUTE_MAX_NOTIONAL_USDT",
    "MVP_LIVE_MAX_DAILY_ORDER_COUNT", "MVP_LIVE_MAX_OPEN_NOTIONAL_USDT",
    "MVP_LIVE_DAILY_LOSS_LIMIT_USDT", "MVP_LIVE_MIN_CLEAN_CANARY_ORDERS",
    "MVP_ACCOUNT_FEED", "BINANCE_ACCOUNT_API_KEY", "BINANCE_ACCOUNT_API_SECRET",
)


@pytest.fixture
def clean_env(monkeypatch):
    """A machine with nothing configured — the state every fresh checkout is in."""
    for name in _LIVE_ENVS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _canary(clean: bool = True, *, order_id: str = "o1", now: str = NOW):
    return build_canary_order_record(
        reconcile_status="RECONCILED" if clean else "UNRECONCILED",
        symbol="BTCUSDT", exchange_order_id=order_id, client_order_id=f"c_{order_id}",
        mismatches=None if clean else ["quantity"], notional_usdt=5.0, now=now,
    )


def _write(root, records):
    target = state_dir(root)
    target.mkdir(parents=True, exist_ok=True)
    path = target / live_promotion.CANARY_ORDERS_FILENAME
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


# === canary evidence ================================================================

def test_clean_is_derived_not_asserted():
    """A caller cannot declare its own order clean; the venue's answer decides."""
    assert _canary(clean=True)["clean"] is True
    assert _canary(clean=False)["clean"] is False
    # Reconciled but with a mismatch is NOT clean.
    record = build_canary_order_record(
        reconcile_status="RECONCILED", symbol="BTCUSDT", mismatches=["price"], now=NOW
    )
    assert record["clean"] is False


def test_missing_registry_is_honestly_empty(tmp_path):
    assert read_canary_orders(tmp_path) == []
    assert clean_canary_order_count(tmp_path) == (0, None)


def test_counts_only_clean_orders(tmp_path):
    _write(tmp_path, [_canary(True, order_id="a"), _canary(False, order_id="b"),
                      _canary(True, order_id="c")])
    assert clean_canary_order_count(tmp_path) == (2, None)


def test_tampered_registry_counts_zero_and_names_why(tmp_path):
    """Damaged evidence is no evidence — never the last good number."""
    record = dict(_canary(True))
    record["clean"] = True
    record["reconcile_status"] = "UNRECONCILED"  # edited after hashing
    _write(tmp_path, [record])
    count, error = clean_canary_order_count(tmp_path)
    assert count == 0
    assert error == live_promotion.CANARY_HISTORY_TAMPERED


def test_duplicate_canary_refuses(tmp_path):
    record = _canary(True)
    _write(tmp_path, [record, record])
    with pytest.raises(ToolError) as exc:
        read_canary_orders(tmp_path)
    assert exc.value.reason_code == live_promotion.CANARY_HISTORY_DUPLICATE


def test_unreadable_registry_counts_zero(tmp_path):
    target = state_dir(tmp_path)
    target.mkdir(parents=True, exist_ok=True)
    (target / live_promotion.CANARY_ORDERS_FILENAME).write_text("nope\n", encoding="utf-8")
    count, error = clean_canary_order_count(tmp_path)
    assert count == 0 and error == live_promotion.CANARY_HISTORY_UNREADABLE


# === the promotion gate =============================================================

def test_zero_minimum_is_refused_not_satisfied(tmp_path):
    """Requiring no evidence is the one setting that must never read as ready."""
    for minimum in (0, -1):
        status = promotion_status(min_orders=minimum, root=tmp_path)
        assert status["ready"] is False
        assert any("no evidence" in r for r in status["reasons"])


def test_below_threshold_is_not_ready(tmp_path):
    _write(tmp_path, [_canary(True, order_id="a")])
    status = promotion_status(min_orders=3, root=tmp_path)
    assert status["ready"] is False and status["clean_count"] == 1


def test_threshold_met_is_ready(tmp_path):
    _write(tmp_path, [_canary(True, order_id=x) for x in ("a", "b", "c")])
    status = promotion_status(min_orders=3, root=tmp_path)
    assert status["ready"] is True and status["clean_count"] == 3


def test_default_minimum_matches_the_source(tmp_path):
    assert DEFAULT_MIN_CLEAN_CANARY_ORDERS == 3
    assert promotion_status(root=tmp_path)["required"] == 3


def test_tampered_history_blocks_even_with_enough_records(tmp_path):
    """Three clean orders plus one corrupt row is not three clean orders."""
    good = [_canary(True, order_id=x) for x in ("a", "b", "c")]
    bad = dict(_canary(True, order_id="d"))
    bad["clean"] = True
    bad["symbol"] = "TAMPERED"
    _write(tmp_path, good + [bad])
    status = promotion_status(min_orders=3, root=tmp_path)
    assert status["ready"] is False and status["clean_count"] == 0
    assert status["history_error"] == live_promotion.CANARY_HISTORY_TAMPERED


# === the gate on writing evidence ===================================================

def test_registry_env_alone_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv(LIVE_TRADING_ENV, REAL_LIVE_TRADING)
    with pytest.raises(SafetyGateBlocked) as exc:
        select_canary_registry(now=NOW, root=tmp_path)
    assert exc.value.reason_code == "ACTIVATION_MISSING"


def test_registry_default_is_inert(tmp_path, monkeypatch):
    monkeypatch.delenv(LIVE_TRADING_ENV, raising=False)
    registry = select_canary_registry(now=NOW, root=tmp_path)
    assert isinstance(registry, DryRunCanaryRegistry)
    registry.append_canary_order(_canary(True))
    assert read_canary_orders(tmp_path) == []  # unbacked evidence never persists


def test_real_registry_refuses_without_authorization(tmp_path):
    with pytest.raises(SafetyGateBlocked):
        RealCanaryRegistry(root=tmp_path, authorization=None).append_canary_order(_canary(True))


def test_real_registry_roundtrips_under_the_grant(tmp_path):
    registry = RealCanaryRegistry(root=tmp_path, authorization=_LIVE_AUTH)
    registry.append_canary_order(_canary(True, order_id="a"))
    registry.append_canary_order(_canary(True, order_id="b"))
    assert clean_canary_order_count(tmp_path) == (2, None)


def test_canary_registry_shares_the_one_live_grant():
    assert RealCanaryRegistry.provider_id == LIVE_TRADING_PROVIDER_ID


# === the readiness board ============================================================

def test_fresh_machine_is_not_ready(tmp_path, clean_env):
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    assert status["ready"] is False
    failed = {c["check"] for c in status["checks"] if not c["ok"]}
    assert {"live_trading_grant", "confirmation_phrase", "risk_caps",
            "canary_evidence", "order_path_implemented"} <= failed


def test_board_reports_every_gate(tmp_path, clean_env):
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    assert {c["check"] for c in status["checks"]} == {
        "live_trading_grant", "confirmation_phrase", "risk_caps", "manual_kill_switch",
        "runtime_active", "daily_loss_breaker", "canary_evidence", "account_visibility",
        "order_path_implemented",
    }


def test_unconfigured_loss_limit_shows_as_breached(tmp_path, clean_env):
    """The board must not show a comfortable green for a missing risk limit."""
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    breaker = next(c for c in status["checks"] if c["check"] == "daily_loss_breaker")
    assert breaker["ok"] is False and "BREACHED" in breaker["detail"]


def test_configuring_caps_clears_that_row(tmp_path, clean_env):
    clean_env.setenv("MVP_LIVE_MAX_ORDER_NOTIONAL_USDT", "60")
    clean_env.setenv("MVP_LIVE_MAX_DAILY_ORDER_COUNT", "2")
    clean_env.setenv("MVP_LIVE_MAX_OPEN_NOTIONAL_USDT", "120")
    clean_env.setenv("MVP_LIVE_DAILY_LOSS_LIMIT_USDT", "20")
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    caps = next(c for c in status["checks"] if c["check"] == "risk_caps")
    assert caps["ok"] is True


def test_cap_above_the_ceiling_fails_the_caps_row(tmp_path, clean_env):
    clean_env.setenv("MVP_LIVE_MAX_ORDER_NOTIONAL_USDT", "500")
    clean_env.setenv("MVP_LIVE_MAX_DAILY_ORDER_COUNT", "2")
    clean_env.setenv("MVP_LIVE_MAX_OPEN_NOTIONAL_USDT", "120")
    clean_env.setenv("MVP_LIVE_DAILY_LOSS_LIMIT_USDT", "20")
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    caps = next(c for c in status["checks"] if c["check"] == "risk_caps")
    assert caps["ok"] is False and "ceiling" in caps["detail"]


def test_confirmation_phrase_row(tmp_path, clean_env):
    clean_env.setenv(CONFIRMATION_ENV, LIVE_CONFIRMATION_PHRASE)
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    phrase = next(c for c in status["checks"] if c["check"] == "confirmation_phrase")
    assert phrase["ok"] is True


def test_board_never_echoes_the_confirmation_phrase(tmp_path, clean_env):
    clean_env.setenv(CONFIRMATION_ENV, LIVE_CONFIRMATION_PHRASE)
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    assert LIVE_CONFIRMATION_PHRASE not in json.dumps(status)


def test_board_survives_an_unreadable_canary_registry(tmp_path, clean_env):
    """An unreadable input is a failed check with a reason, never a crashed board."""
    target = state_dir(tmp_path)
    target.mkdir(parents=True, exist_ok=True)
    (target / live_promotion.CANARY_ORDERS_FILENAME).write_text("garbage\n", encoding="utf-8")
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    evidence = next(c for c in status["checks"] if c["check"] == "canary_evidence")
    assert evidence["ok"] is False and "CANARY_HISTORY_UNREADABLE" in evidence["detail"]


def test_guard_dry_run_is_the_authoritative_answer(tmp_path, clean_env):
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    assert status["guard_dry_run"]["approved"] is False
    assert status["guard_dry_run"]["blocks"]


def test_order_path_is_not_implemented():
    """A canary for LP4: when an order adapter lands, this constant and this test change
    together, so the board can never claim a capability that does not exist."""
    assert live_readiness.ORDER_PATH_IMPLEMENTED is False


def test_board_cannot_report_ready_without_an_order_path(tmp_path, clean_env):
    clean_env.setenv(CONFIRMATION_ENV, LIVE_CONFIRMATION_PHRASE)
    clean_env.setenv("MVP_LIVE_MAX_ORDER_NOTIONAL_USDT", "60")
    clean_env.setenv("MVP_LIVE_MAX_DAILY_ORDER_COUNT", "2")
    clean_env.setenv("MVP_LIVE_MAX_OPEN_NOTIONAL_USDT", "120")
    clean_env.setenv("MVP_LIVE_DAILY_LOSS_LIMIT_USDT", "20")
    _write(tmp_path, [_canary(True, order_id=x) for x in ("a", "b", "c")])
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    assert status["ready"] is False
    assert not next(c for c in status["checks"] if c["check"] == "order_path_implemented")["ok"]


def test_render_is_ascii_and_states_the_verdict(tmp_path, clean_env):
    text = live_readiness.render_readiness_text(
        live_readiness.build_readiness(root=tmp_path, now=NOW)
    )
    text.encode("ascii")
    assert "NOT READY" in text and "no order path exists yet" in text
