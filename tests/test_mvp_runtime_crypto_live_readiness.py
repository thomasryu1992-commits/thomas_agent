"""LP6 tests — canary promotion evidence and the readiness board.

Under test: cleanliness is derived from the venue's answer and can never be asserted by a
caller; damaged evidence counts as zero rather than as the last good number; a promotion
minimum of zero is refused rather than trivially satisfied; and the readiness board reports
every gate honestly, never raises on an unreadable input, and cannot say READY while no order
path exists.
"""

from __future__ import annotations

import json
from pathlib import Path

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
    "MVP_MARKET_DATA",
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
    """The source value, and the single authority that carries it.

    The name used to be a promise this test did not keep: it compared `live_promotion`'s
    constant against the literal 3 and stopped there, while `live_order` declared its own
    `DEFAULT_MIN_CLEAN_CANARY_ORDERS = 3` for `LiveOrderLimits` to default to. Two authorities
    for one safety threshold, and nothing comparing them — editing either alone left the suite
    green with the guard and the promotion board requiring different numbers of canaries.

    `live_order` now re-exports this one, and what pins that is the **source** check below,
    not a comparison of values. Comparing the two constants cannot work: they would only
    differ if someone edited one, and an `is` check is worse than useless here — CPython
    caches small integers, so `live_order.DEFAULT_... is DEFAULT_...` is True for two
    independent `= 3` declarations. That check was written first and passed against a
    deliberately reintroduced duplicate. Counting declarations is the thing that holds.
    """
    import re

    from runtime.mvp_runtime.crypto.live_order import LiveOrderLimits

    assert DEFAULT_MIN_CLEAN_CANARY_ORDERS == 3
    assert promotion_status(root=tmp_path)["required"] == 3
    assert LiveOrderLimits().min_clean_canary_orders == DEFAULT_MIN_CLEAN_CANARY_ORDERS

    crypto_dir = Path(live_readiness.__file__).parent
    declaring = sorted(
        path.name for path in crypto_dir.glob("*.py")
        if re.search(r"^DEFAULT_MIN_CLEAN_CANARY_ORDERS\s*=", path.read_text(encoding="utf-8"), re.M)
    )
    assert declaring == ["live_promotion.py"], (
        f"{len(declaring)} modules declare the clean-canary minimum: {declaring}. It is a "
        f"safety threshold and must have one authority; import it rather than restating it."
    )


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

def test_registry_env_alone_now_opens_the_gate(tmp_path, monkeypatch):
    """Inverted 2026-07-28 with the rest of the live surface. The registry MUST move with the
    order adapter: evidence has to be exactly as hard to produce as the order it evidences, or
    a machine places real canaries and records none of them."""
    monkeypatch.setenv(LIVE_TRADING_ENV, REAL_LIVE_TRADING)
    registry = select_canary_registry(now=NOW, root=tmp_path)
    assert isinstance(registry, RealCanaryRegistry)
    registry.append_canary_order(_canary(True))
    assert len(read_canary_orders(tmp_path)) == 1   # it really did persist


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
    assert {"live_trading_opt_in", "confirmation_phrase", "registered_budget",
            "canary_evidence"} <= failed


def test_board_reports_every_gate(tmp_path, clean_env):
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    assert {c["check"] for c in status["checks"]} == {
        "live_trading_opt_in", "confirmation_phrase", "registered_budget", "manual_kill_switch",
        "runtime_active", "daily_loss_breaker", "canary_evidence", "account_visibility",
        "market_data_visibility", "order_path_implemented", "autonomous_routing_wired",
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
    assert "live_trading_opt_in" in failed and "registered_budget" in failed
    assert status["guard_dry_run"]["approved"] is False


def test_render_is_ascii_and_says_what_ready_now_means(tmp_path, clean_env):
    text = live_readiness.render_readiness_text(
        live_readiness.build_readiness(root=tmp_path, now=NOW)
    )
    text.encode("ascii")
    assert "NOT READY" in text
    # The board must not let green ticks read as harmless now that a path exists.
    assert "an order path EXISTS" in text and "a real order can be placed" in text
    # Which autonomous note is correct depends on the wiring, so assert the one that matches
    # rather than pinning the board to one era of the build. Both must say the consequential
    # thing: unwired, that the only door is the deliberate canary; wired, that a scheduled run
    # moves real money and how to stop it.
    if live_readiness.AUTONOMOUS_ROUTING_WIRED:
        assert "WIRED" in text and "REAL positions" in text
        # The halt instruction must name the runtime kill, and must warn against the obvious
        # wrong move. Clearing MVP_LIVE_TRADING looks like the way to stop a runtime whose gate
        # IS that variable; it needs a restart to take effect and it strands every open position,
        # because the close guard still requires the opt-in. Pinned in both directions because
        # an operator reads this line in a hurry.
        assert "console_cli kill" in text
        assert "Do NOT clear MVP_LIVE_TRADING" in text
    else:
        assert "LP5" in text and "place_canary_order.py" in text


def test_autonomous_routing_is_reported_and_never_fails_the_board(tmp_path, clean_env):
    """B1: whether an autonomous path exists is the fact most likely to go stale in prose, so it
    is a computed row. It is deliberately informational — an UNWIRED runtime is the safe state,
    and failing the board on it would invert the meaning of every other row."""
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    row = next(c for c in status["checks"] if c["check"] == "autonomous_routing_wired")
    assert row["ok"] is True
    assert ("not wired" in row["detail"]) is (not live_readiness.AUTONOMOUS_ROUTING_WIRED)
    assert status["autonomous_routing_wired"] is live_readiness.AUTONOMOUS_ROUTING_WIRED


def test_the_board_prose_makes_no_build_claims(tmp_path, clean_env):
    """The module whose purpose is anti-drift must not assert a build state in prose — that is
    exactly what went stale. Status belongs in the rows."""
    import runtime.mvp_runtime.crypto.live_readiness as module

    text = (module.__doc__ or "") + live_readiness.render_readiness_text(
        live_readiness.build_readiness(root=tmp_path, now=NOW)
    )
    assert "unbuilt" not in text.lower()


def test_market_data_is_reported_as_a_canary_precondition(tmp_path, clean_env):
    """Since the declared-notional check landed, no market data means no canary.

    `place_canary_order` verifies `--notional` against the venue's last close and refuses when
    there is no usable price — so a machine without this feed cannot place the canaries that
    the `canary_evidence` row is counting. A precondition only a docstring knows about is one
    the operator discovers at a terminal holding real keys (#201's lesson), so it is a row.
    """
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    row = next(c for c in status["checks"] if c["check"] == "market_data_visibility")
    assert row["ok"] is False
    assert "canary" in row["detail"], "say what the operator loses without it"


def test_market_data_env_alone_does_not_pass_the_row(tmp_path, clean_env, monkeypatch):
    """The env var alone selects the MOCK, whose synthesised price the check rejects.

    Reporting green here would tell an operator the canary can run when it cannot — the same
    env-var-is-not-a-grant confusion the safety gate exists to prevent.
    """
    monkeypatch.setenv("MVP_MARKET_DATA", "binance_futures")
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    row = next(c for c in status["checks"] if c["check"] == "market_data_visibility")
    assert row["ok"] is False
    assert "grant" in row["detail"]


# === the daily-loss breaker has a source ==========================================

def test_the_two_windows_are_different_statistics(monkeypatch):
    """Not a labelling nit. These are NET sums, so a wider window also picks up the wider
    window's profits — the rolling figure can be LESS negative than the calendar day, which
    is today's loss hidden behind yesterday's profit on the one measure meant to stop the day."""
    from datetime import datetime, timezone

    from runtime.mvp_runtime.crypto.account import bucket_income

    now_ms = int(datetime(2026, 7, 27, 1, 0, tzinfo=timezone.utc).timestamp() * 1000)
    rows = [
        {"incomeType": "REALIZED_PNL", "asset": "USDT", "income": "50",
         "time": now_ms - 2 * 3600 * 1000},        # yesterday 23:00
        {"incomeType": "REALIZED_PNL", "asset": "USDT", "income": "-30",
         "time": now_ms - 30 * 60 * 1000},         # today 00:30
    ]
    windows = bucket_income(rows, now_ms=now_ms)

    assert windows["1d"]["net"] == 20.0        # rolling: looks like a profit
    assert windows["today"]["net"] == -30.0    # calendar day: the actual loss


def test_the_breaker_takes_the_stricter_window():
    """So the limit cannot be escaped by which 24 hours it is measured over."""
    from runtime.mvp_runtime.crypto.live_pnl import venue_daily_realized_net

    assert venue_daily_realized_net({"today": {"net": -30.0}, "1d": {"net": 20.0}}) == -30.0
    assert venue_daily_realized_net({"today": {"net": 5.0}, "1d": {"net": -12.0}}) == -12.0


def test_an_unreadable_window_is_skipped_not_read_as_zero():
    """Zero is a claim about money. A missing or malformed bucket must not make one."""
    from runtime.mvp_runtime.crypto.live_pnl import venue_daily_realized_net

    assert venue_daily_realized_net({"today": {"net": "x"}, "1d": {"net": -4.0}}) == -4.0
    assert venue_daily_realized_net({"today": {"net": True}}) is None   # bool is not a figure
    for empty in ({}, None, {"today": "nope"}, {"1d": {}}):
        assert venue_daily_realized_net(empty) is None


def test_the_venue_figure_is_the_authority_when_given():
    """The local ledger is empty by construction on the canary path, so a venue figure must
    win outright rather than being averaged or cross-checked against a structural zero."""
    from runtime.mvp_runtime.crypto.live_pnl import PNL_SOURCE_VENUE, live_risk_snapshot

    snap = live_risk_snapshot(limit_usdt=20.0, root=Path("/nonexistent"), now=NOW,
                              venue_realized_pnl_usdt=-25.0)
    assert snap["pnl_source"] == PNL_SOURCE_VENUE
    assert snap["daily_loss_limit_breached"] is True
    assert snap["history_error"] is None       # a real figure is not a missing one


def test_an_unconfigured_board_opens_no_socket_and_still_fails_the_row(tmp_path, monkeypatch):
    """The property that keeps the board usable as a diagnostic: no feed configured means no
    outbound call, and the breaker row fails for want of a source rather than passing."""
    from runtime.mvp_runtime.crypto import account, live_readiness

    for var in (account.ACCOUNT_FEED_ENV, account.ACCOUNT_API_KEY_ENV,
                account.ACCOUNT_API_SECRET_ENV):
        monkeypatch.delenv(var, raising=False)

    def _explode(**kwargs):                      # pragma: no cover - must not be reached
        raise AssertionError("the board read the account without a configured feed")

    monkeypatch.setattr(live_readiness, "read_account", _explode)
    board = live_readiness.build_readiness(root=tmp_path, now=NOW)

    row = next(c for c in board["checks"] if c["check"] == "daily_loss_breaker")
    assert row["ok"] is False


def test_a_failing_account_read_leaves_the_row_failing(tmp_path, monkeypatch):
    """Degrade, never block — but a loss breaker is the one place where "could not measure"
    must not soften into "nothing to report"."""
    from runtime.mvp_runtime.crypto import account, live_readiness
    from runtime.mvp_runtime.errors import ToolError

    monkeypatch.setenv(account.ACCOUNT_FEED_ENV, account.BINANCE_ACCOUNT)
    monkeypatch.setenv(account.ACCOUNT_API_KEY_ENV, "k")
    monkeypatch.setenv(account.ACCOUNT_API_SECRET_ENV, "s")
    monkeypatch.setattr(
        live_readiness, "read_account",
        lambda **kw: (_ for _ in ()).throw(ToolError("ACCOUNT_DATA_DEGRADED", "down")),
    )

    board = live_readiness.build_readiness(root=tmp_path, now=NOW)

    row = next(c for c in board["checks"] if c["check"] == "daily_loss_breaker")
    assert row["ok"] is False


# --- the evidence has to prove its own size -------------------------------------

def test_a_canary_record_carries_the_venues_own_numbers():
    """The four canaries standing as evidence on 2026-07-28 carried a declared notional, no
    quantity and no fill — so there was no way left to ask whether 65.0 described the order.
    A promotion gate whose records cannot be re-derived is an assertion with a hash on it."""
    from runtime.mvp_runtime.crypto import live_promotion

    record = live_promotion.build_canary_order_record(
        reconcile_status="RECONCILED", symbol="BTCUSDT",
        exchange_order_id=1, client_order_id="c1", mismatches=[],
        notional_usdt=65.0, quantity=0.001,
        fill={"avg_price": 64512.0, "executed_qty": 0.001, "cum_quote": 64.512},
        now="2026-07-29T00:00:00Z",
    )
    assert record["quantity"] == 0.001
    assert record["fill_avg_price"] == 64512.0
    assert record["filled_notional_usdt"] == 64.512
    # Declared minus filled: a subtraction, not a memory. 65.0 declared for a 64.51 order.
    assert record["notional_declared_vs_filled_usdt"] == pytest.approx(0.488, abs=1e-6)
    assert record["clean"] is True          # stated, not judged — see the next test


def test_a_declared_notional_that_disagrees_is_recorded_not_judged():
    """Making a disagreement a mismatch would change what `clean` means, and therefore what
    the promotion gate counts. That is a separate decision and is deliberately not taken here:
    the record states the gap so an operator can see it."""
    from runtime.mvp_runtime.crypto import live_promotion

    understated = live_promotion.build_canary_order_record(
        reconcile_status="RECONCILED", symbol="BTCUSDT",
        exchange_order_id=1, client_order_id="c1", mismatches=[],
        notional_usdt=60.0, quantity=0.001,
        fill={"avg_price": 70000.0, "executed_qty": 0.001, "cum_quote": 70.0},
        now="2026-07-29T00:00:00Z",
    )
    assert understated["notional_declared_vs_filled_usdt"] == pytest.approx(-10.0)
    assert understated["clean"] is True and understated["mismatches"] == []


@pytest.mark.parametrize("fill", [None, {}, {"cum_quote": None}, {"cum_quote": "n/a"}])
def test_an_unknown_fill_leaves_the_comparison_unknown_never_zero(fill):
    """`0.0` would read as 'declared and filled agreed'. They did not agree; nobody knows."""
    from runtime.mvp_runtime.crypto import live_promotion

    record = live_promotion.build_canary_order_record(
        reconcile_status="RECONCILED", symbol="BTCUSDT",
        exchange_order_id=1, client_order_id="c1", mismatches=[],
        notional_usdt=65.0, quantity=0.001, fill=fill,
        now="2026-07-29T00:00:00Z",
    )
    assert record["notional_declared_vs_filled_usdt"] is None
    assert record["filled_notional_usdt"] in (None, "n/a")


def test_records_written_before_this_existed_still_read():
    """Every canary already in the registry predates these fields. They must keep verifying —
    the store is self-hashed, and a field added to the builder must not invalidate history."""
    from runtime.mvp_runtime.crypto import live_promotion

    old = live_promotion.build_canary_order_record(
        reconcile_status="RECONCILED", symbol="BTCUSDT",
        exchange_order_id=1, client_order_id="c1", mismatches=[],
        notional_usdt=65.0, now="2026-07-29T00:00:00Z",
    )
    assert old["quantity"] is None and old["filled_notional_usdt"] is None
    assert old["notional_declared_vs_filled_usdt"] is None
    assert old["clean"] is True
