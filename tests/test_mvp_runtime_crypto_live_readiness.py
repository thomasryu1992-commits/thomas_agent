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

def _register_budget(root, *, valid_from="2026-07-01T00:00:00Z", valid_until="2026-12-31T00:00:00Z", **cap_overrides):
    """Register a valid live-trading budget under ``root`` (step 6b: the guard's cap source)."""
    from runtime.mvp_runtime.crypto import live_budget
    caps = dict(max_order_notional_usdt=60.0, absolute_max_notional_usdt=200.0,
                max_daily_order_count=2, max_open_notional_usdt=120.0,
                daily_loss_limit_usdt=20.0, min_clean_canary_orders=3)
    caps.update(cap_overrides)
    rec = live_budget.build_live_trading_budget_record(
        caps=caps, symbol_allowlist=["BTCUSDT"], valid_from=valid_from, valid_until=valid_until,
        registered_by="thomas", registered_at=valid_from)
    live_budget.write_registered_budget(rec, root=root)
    return rec


def test_fresh_machine_is_not_ready(tmp_path, clean_env):
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    assert status["ready"] is False
    failed = {c["check"] for c in status["checks"] if not c["ok"]}
    # order_path_implemented now passes (LP4 landed); every AUTHORITY row must still fail.
    assert {"live_trading_grant", "confirmation_phrase", "registered_budget",
            "canary_evidence"} <= failed


def test_board_reports_every_gate(tmp_path, clean_env):
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    assert {c["check"] for c in status["checks"]} == {
        "live_trading_grant", "confirmation_phrase", "registered_budget", "manual_kill_switch",
        "runtime_active", "daily_loss_breaker", "canary_evidence", "account_visibility",
        "order_path_implemented",
    }


def test_unconfigured_loss_limit_shows_as_breached(tmp_path, clean_env):
    """The board must not show a comfortable green for a missing risk limit."""
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    breaker = next(c for c in status["checks"] if c["check"] == "daily_loss_breaker")
    assert breaker["ok"] is False and "BREACHED" in breaker["detail"]


def test_registering_a_budget_clears_the_budget_row(tmp_path, clean_env):
    """Step 6b: the caps come from the registered budget now, not env — env caps no longer
    clear the row (that is the whole point: an auditable record, not a mutable env)."""
    # Env caps set, but NO budget registered => the row stays red.
    clean_env.setenv("MVP_LIVE_MAX_ORDER_NOTIONAL_USDT", "60")
    before = live_readiness.build_readiness(root=tmp_path, now=NOW)
    assert next(c for c in before["checks"] if c["check"] == "registered_budget")["ok"] is False
    # Register a valid budget => the row clears, and the caps in the detail come from the record.
    _register_budget(tmp_path)
    after = live_readiness.build_readiness(root=tmp_path, now=NOW)
    row = next(c for c in after["checks"] if c["check"] == "registered_budget")
    assert row["ok"] is True and "order<=60.0" in row["detail"]


def test_an_expired_budget_fails_the_budget_row(tmp_path, clean_env):
    """A registered budget outside its validity window is invalid — the row names why, and the
    guard dry-run refuses (the caps fall back to blocking)."""
    _register_budget(tmp_path, valid_from="2026-06-01T00:00:00Z", valid_until="2026-06-30T00:00:00Z")
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)  # NOW is 2026-07-23, past valid_until
    row = next(c for c in status["checks"] if c["check"] == "registered_budget")
    assert row["ok"] is False and "invalid" in row["detail"]
    assert status["guard_dry_run"]["approved"] is False


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


def test_order_path_flag_matches_the_governance_flag():
    """The lockstep rule: the board's constant and the policy's
    `financial_transaction_execution_implemented` state the SAME fact ("the code that can place a
    live order exists"), so a drift between them — one claiming a capability the other denies —
    is the dangerous state this asserts against."""
    import yaml

    from runtime.mvp_runtime.paths import repo_root

    policy = yaml.safe_load((repo_root() / "governance" / "GOVERNANCE_POLICY.yaml").read_text(encoding="utf-8"))
    flag = policy["financial"]["financial_transaction_execution_implemented"]
    assert live_readiness.ORDER_PATH_IMPLEMENTED is bool(flag)


def test_the_executor_handoff_flag_is_still_off():
    """LP4 made the runtime act directly; it did NOT enable the deferred Executor handoff. That
    flag is gate-asserted elsewhere too — this states the boundary next to the flag that moved."""
    import yaml

    from runtime.mvp_runtime.paths import repo_root

    policy = yaml.safe_load((repo_root() / "governance" / "GOVERNANCE_POLICY.yaml").read_text(encoding="utf-8"))
    assert policy["financial"]["financial_executor_enabled"] is False


def test_board_still_refuses_without_the_grant_even_though_the_path_exists(tmp_path, clean_env):
    """Since LP4 the order-path row passes, so what must keep the board from READY is the
    *authority* — no grant, no phrase, no registered budget. Configuring the old env caps and
    three clean canaries is not enough."""
    clean_env.setenv(CONFIRMATION_ENV, LIVE_CONFIRMATION_PHRASE)
    clean_env.setenv("MVP_LIVE_MAX_ORDER_NOTIONAL_USDT", "60")
    clean_env.setenv("MVP_LIVE_MAX_DAILY_ORDER_COUNT", "2")
    clean_env.setenv("MVP_LIVE_MAX_OPEN_NOTIONAL_USDT", "120")
    clean_env.setenv("MVP_LIVE_DAILY_LOSS_LIMIT_USDT", "20")
    _write(tmp_path, [_canary(True, order_id=x) for x in ("a", "b", "c")])
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    assert status["ready"] is False
    assert next(c for c in status["checks"] if c["check"] == "order_path_implemented")["ok"]
    failed = {c["check"] for c in status["checks"] if not c["ok"]}
    assert "live_trading_grant" in failed and "registered_budget" in failed
    assert status["guard_dry_run"]["approved"] is False


def test_render_is_ascii_and_says_what_ready_now_means(tmp_path, clean_env):
    text = live_readiness.render_readiness_text(
        live_readiness.build_readiness(root=tmp_path, now=NOW)
    )
    text.encode("ascii")
    assert "NOT READY" in text
    # The board must not let green ticks read as harmless now that a path exists.
    assert "an order path EXISTS" in text and "a real order can be placed" in text
    assert "LP5" in text and "place_canary_order.py" in text
