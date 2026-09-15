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

from runtime.read_only_kernel import integrity
from runtime.mvp_runtime.crypto import live_promotion, live_readiness
from runtime.mvp_runtime.crypto import pool as pool_store
from runtime.mvp_runtime.crypto.live_pnl import LIVE_TRADING_ENV, state_dir
from runtime.mvp_runtime.crypto.live_promotion import (
    DEFAULT_MIN_CLEAN_CANARY_ORDERS,
    clean_canary_order_count,
    promotion_status,
)
from runtime.mvp_runtime.crypto.live_order import CONFIRMATION_ENV, LIVE_CONFIRMATION_PHRASE
from runtime.mvp_runtime.errors import ToolError

NOW = "2026-07-23T12:00:00Z"

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


def _canary(clean: bool = True, *, order_id: str = "o1", now: str = NOW, **extra):
    """A registry row as it sits on disk, hashed the way the reader verifies it.

    Raw rather than built: the builder went with the canary door on 2026-09-15 (PR1r), and the
    rows these tests stand for were written by it long before. ``clean`` is stated here because
    it is stored on the row; nothing in the read path derives it."""
    body = {
        "reconcile_status": "RECONCILED" if clean else "UNRECONCILED", "clean": clean,
        "symbol": "BTCUSDT", "exchange_order_id": order_id, "client_order_id": f"c_{order_id}",
        "mismatches": [] if clean else ["quantity"], "notional_usdt": 5.0,
        "recorded_at_utc": now, "stage": "live_canary",
        "provenance": live_promotion.CANARY_PROVENANCE, "canary_order_id": f"canary_{order_id}",
        **extra,
    }
    body["record_sha256"] = integrity.sha256_record(body)
    return body


def _write(root, records):
    target = state_dir(root)
    target.mkdir(parents=True, exist_ok=True)
    path = target / live_promotion.CANARY_ORDERS_FILENAME
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


# === canary evidence ================================================================

def test_missing_registry_is_honestly_empty(tmp_path):
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


# === the readiness board ============================================================

def _register_budget(root, *, valid_from="2026-07-01T00:00:00Z", valid_until="2026-12-31T00:00:00Z",
                     symbol_allowlist=("BTCUSDT",), **cap_overrides):
    """Register a valid live-trading budget under ``root`` (step 6b: the guard's cap source)."""
    from runtime.mvp_runtime.crypto import live_budget
    caps = dict(max_order_notional_usdt=60.0, absolute_max_notional_usdt=200.0,
                max_daily_order_count=2, max_open_notional_usdt=120.0,
                daily_loss_limit_usdt=20.0, min_clean_canary_orders=3)
    caps.update(cap_overrides)
    rec = live_budget.build_live_trading_budget_record(
        caps=caps, symbol_allowlist=list(symbol_allowlist), valid_from=valid_from,
        valid_until=valid_until, registered_by="thomas", registered_at=valid_from)
    live_budget.write_registered_budget(rec, root=root)
    return rec


def _allowlist_blocks(status):
    """The dry-run's symbol-scope blocks — the ones the board omitted its way into."""
    return [b for b in (status["guard_dry_run"].get("blocks") or []) if "allowlist" in b]


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
        "live_trading_opt_in", "confirmation_phrase", "registered_budget", "risk_limits_record",
        "manual_kill_switch", "runtime_active", "trading_armed", "live_armed_strategies",
        "daily_loss_breaker", "bracket_breaker", "canary_evidence",
        "account_visibility", "market_data_visibility", "order_path_implemented",
        "autonomous_routing_wired",
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


def test_an_unbudgeted_machine_blocks_on_the_absent_allowlist(tmp_path, clean_env):
    """With no budget there IS no scope, and the block naming that is the true answer.

    Pinned beside the tests below because it is the case the old code reported for *every*
    machine: correct here, and correct nowhere else."""
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    assert _allowlist_blocks(status) == [
        b for b in status["guard_dry_run"]["blocks"] if "no symbol allowlist" in b
    ]
    assert _allowlist_blocks(status)
    assert status["guard_dry_run_symbol"] == live_readiness.DEFAULT_PROBE_SYMBOL


def test_the_dry_run_reads_the_registered_allowlist(tmp_path, clean_env):
    """The defect this test exists for: the board called the guard without `allowed_symbols`,
    whose default is EMPTY and blocks every symbol — so the one line the module calls the
    authoritative answer reported "no symbol allowlist backs this order" on every machine,
    however the budget was registered, and told the operator to register the budget they had
    already registered. Both real doors (`live_route`, `place_canary_order`) read the scope off
    the same budget the caps come from."""
    before = live_readiness.build_readiness(root=tmp_path, now=NOW)
    assert _allowlist_blocks(before)          # no budget: the block is real

    _register_budget(tmp_path, symbol_allowlist=["BTCUSDT"])
    after = live_readiness.build_readiness(root=tmp_path, now=NOW)
    assert _allowlist_blocks(after) == []     # a registered scope is not an absent one
    # It still refuses — on the doors that ARE shut (opt-in, phrase, canaries). The point is
    # that the board no longer invents a thirteenth.
    assert after["guard_dry_run"]["approved"] is False
    assert after["guard_dry_run"]["blocks"]


def test_the_probe_symbol_comes_from_the_budget(tmp_path, clean_env):
    """A machine budgeted for ETHUSDT alone is correctly configured. Probing a hardcoded
    BTCUSDT would answer a question nobody asked and report a scope block on a machine whose
    scope is intact — the same false alarm one layer down."""
    _register_budget(tmp_path, symbol_allowlist=["ETHUSDT", "SOLUSDT"])
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    assert status["guard_dry_run_symbol"] == "ETHUSDT"
    assert _allowlist_blocks(status) == []


def test_the_board_names_the_symbol_it_probed(tmp_path, clean_env):
    """A symbol-scoped verdict is unreadable without its symbol: the operator cannot tell
    "my budget excludes this one" from "this one is blocked"."""
    _register_budget(tmp_path, symbol_allowlist=["SOLUSDT"])
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    assert "guard dry-run (SOLUSDT at the configured cap):" in (
        live_readiness.render_readiness_text(status)
    )


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


def test_the_execution_stage_is_an_informational_line_never_a_pass(tmp_path, clean_env):
    """Crypto PR1a, review of #872: a `[PASS]` beside a machine that reads READ_ONLY would repeat the
    all-PASS-with-nothing-armed board the audit started from. The stage is data and a `[----]` line."""
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    assert "execution_stage" not in {c["check"] for c in status["checks"]}
    assert status["execution_stage"]["stage"] == "READ_ONLY" and status["execution_stage"]["enforced"] is False
    line = next(l for l in live_readiness.render_readiness_text(status).splitlines() if "execution_stage" in l)
    assert line.startswith("[----]") and "EXECUTION_STAGE_RECORD_MISSING" in line and "not enforced yet" in line


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
        # Which halt the board names depends on the committed policy's grant (review of H2): the
        # soft halt only once it acts, otherwise kill with its caveat. Either way the caveat about
        # position management is on the board.
        from runtime.mvp_runtime.control import CMD_HALT_TRADING, granted_emergency_controls
        if CMD_HALT_TRADING in granted_emergency_controls():
            assert "console_cli halt_trading" in text
        else:
            assert "console_cli kill" in text and "halt_trading, acts once" in text
        assert "position management" in text
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


def test_market_data_env_alone_passes_the_row(tmp_path, clean_env, monkeypatch):
    """The environment is the gate (Thomas 2026-08-10): the opt-in alone passes the row.

    The safety the old grant check bought is not lost, just relocated: without the opt-in
    the selector returns the MOCK, whose synthesised price `place_canary_order`'s own
    declared-notional check rejects — the test below pins that failing direction."""
    monkeypatch.setenv("MVP_MARKET_DATA", "binance_futures")
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    row = next(c for c in status["checks"] if c["check"] == "market_data_visibility")
    assert row["ok"] is True


def test_market_data_row_fails_when_the_opt_in_is_absent(tmp_path, clean_env):
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    row = next(c for c in status["checks"] if c["check"] == "market_data_visibility")
    assert row["ok"] is False
    assert "MVP_MARKET_DATA" in row["detail"]


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


# --- a caller about to open a position does not fall back to the local ledger (2026-09-15) ---

def test_an_entry_caller_reads_a_missing_venue_figure_as_tripped(tmp_path):
    """The fail-open the execution-authority audit verified: the account read succeeded, its
    income call did not, and the local ledger — which cannot see a venue-side close — answered
    "nothing closed today" as 0.0, so the entry went ahead with the daily cap bounding nothing.
    For a caller that is about to open a position the missing figure is the trip."""
    from runtime.mvp_runtime.crypto.live_pnl import (
        LIVE_PNL_VENUE_FIGURE_MISSING, PNL_SOURCE_LOCAL_LEDGER, live_risk_snapshot,
    )

    snap = live_risk_snapshot(limit_usdt=20.0, root=tmp_path, now=NOW,
                              venue_realized_pnl_usdt=None, venue_required=True)
    assert snap["daily_loss_limit_breached"] is True
    assert snap["history_error"] == LIVE_PNL_VENUE_FIGURE_MISSING
    assert snap["pnl_source"] == PNL_SOURCE_LOCAL_LEDGER


def test_a_venue_figure_still_decides_for_an_entry_caller(tmp_path):
    """The rule only replaces the fallback; a real figure is judged exactly as before."""
    from runtime.mvp_runtime.crypto.live_pnl import PNL_SOURCE_VENUE, live_risk_snapshot

    clear = live_risk_snapshot(limit_usdt=20.0, root=tmp_path, now=NOW,
                               venue_realized_pnl_usdt=-5.0, venue_required=True)
    assert (clear["daily_loss_limit_breached"], clear["history_error"], clear["pnl_source"]) == (
        False, None, PNL_SOURCE_VENUE)
    hit = live_risk_snapshot(limit_usdt=20.0, root=tmp_path, now=NOW,
                             venue_realized_pnl_usdt=-20.0, venue_required=True)
    assert hit["daily_loss_limit_breached"] is True


def test_a_reporting_caller_keeps_the_local_branch(tmp_path):
    """Pins the default: a caller that opens nothing (a fresh machine's board, the dashboard)
    still gets the local ledger's reading and its NO_SOURCE marker, unchanged."""
    from runtime.mvp_runtime.crypto.live_pnl import LIVE_PNL_NO_SOURCE, live_risk_snapshot

    snap = live_risk_snapshot(limit_usdt=20.0, root=tmp_path, now=NOW, venue_realized_pnl_usdt=None)
    assert snap["daily_loss_limit_breached"] is False
    assert snap["history_error"] == LIVE_PNL_NO_SOURCE


def test_a_board_that_read_the_account_trips_on_a_missing_figure(tmp_path, monkeypatch):
    """The board and the entry paths give one answer: once the board has read the account for
    the breaker, a snapshot carrying no realized figure fails the row with the entry paths' own
    reason, rather than a comfortable local-ledger reading."""
    from runtime.mvp_runtime.crypto import account, live_readiness
    from runtime.mvp_runtime.crypto.account import AccountSnapshot
    from runtime.mvp_runtime.crypto.live_pnl import LIVE_PNL_VENUE_FIGURE_MISSING

    monkeypatch.setenv(account.ACCOUNT_FEED_ENV, account.BINANCE_ACCOUNT)
    monkeypatch.setenv(account.ACCOUNT_API_KEY_ENV, "k")
    monkeypatch.setenv(account.ACCOUNT_API_SECRET_ENV, "s")
    snapshot = AccountSnapshot(
        asset="USDT", wallet_balance=500.0, margin_balance=500.0, available_balance=400.0,
        unrealized_pnl=0.0, positions=[], realized_windows={}, source="fake", collected_at=NOW,
    )
    monkeypatch.setattr(live_readiness, "read_account", lambda **kw: (snapshot, {}))

    board = live_readiness.build_readiness(root=tmp_path, now=NOW)

    row = next(c for c in board["checks"] if c["check"] == "daily_loss_breaker")
    assert row["ok"] is False
    assert LIVE_PNL_VENUE_FIGURE_MISSING in row["detail"]


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

def test_the_board_says_when_the_evidence_cannot_prove_its_own_size(tmp_path):
    """The other half of the repair. The subtraction was recorded and nothing read it, so an
    operator could only find it by opening the JSONL — which is not a surface."""
    import json

    from runtime.read_only_kernel import integrity
    from runtime.mvp_runtime.crypto import live_promotion

    body = {
        "reconcile_status": "RECONCILED", "clean": True, "symbol": "BTCUSDT",
        "exchange_order_id": 1, "client_order_id": "canary-1", "mismatches": [],
        "notional_usdt": 65.0, "recorded_at_utc": "2026-07-26T14:05:23Z",
        "stage": "live_canary", "provenance": live_promotion.CANARY_PROVENANCE,
        "canary_order_id": "canary_abc",
    }
    body["record_sha256"] = integrity.sha256_record(body)
    store = live_promotion.state_dir(tmp_path) / live_promotion.CANARY_ORDERS_FILENAME
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps(body) + "\n", encoding="utf-8")

    status = live_promotion.promotion_status(min_orders=1, root=tmp_path)
    assert status["ready"] is True, "an unprovable size must not change what the count means"
    assert status["size_unproven"] == 1
    assert status["largest_size_gap_usdt"] is None


def test_a_recorded_size_gap_is_surfaced_rather_than_judged(tmp_path):
    # The shape a post-#268 row carries: declared 65.0, the venue's filled 64.512.
    record = _canary(True, notional_usdt=65.0, quantity=0.001, fill_avg_price=64512.0,
                     fill_executed_qty=0.001, filled_notional_usdt=64.512,
                     notional_declared_vs_filled_usdt=0.488)
    _write(tmp_path, [record])

    status = live_promotion.promotion_status(min_orders=1, root=tmp_path)
    assert status["size_unproven"] == 0
    assert status["largest_size_gap_usdt"] == pytest.approx(0.488, abs=1e-6)
    assert status["ready"] is True          # surfaced, not judged


# === the armed set (#648: occupying and allowed-to-spend are two facts) =============
# `live_tier` absent reads as OBSERVATION, so #648's migration disarmed every pre-existing
# entry on purpose — and the board kept answering READY: on 2026-08-10 every row was green
# while `pool.live_routable_strategy_ids` was empty, no strategy could open a position, and
# the operator found out from the positions that never appeared. These pin the row that says
# it, and the split it inherits from the entry door: zero armed is the expected steady state
# (informational), an unreadable pool is a fault (FAIL, reported UNREADABLE — never 0).

def _strategy_spec(sid):
    """The minimum the pool file's fail-closed loader accepts."""
    return {
        "schema_version": "strategy_spec.v1",
        "strategy_id": sid, "strategy_version": "1.0", "strategy_family": "breakout",
        "symbol_scope": ["BTCUSDT"], "timeframe": "1d", "direction": "long",
        "entry_rules": {"operator": "AND",
                        "conditions": [{"feature": "close", "comparison": ">", "value": 0.0}]},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 2.0,
                       "max_holding_bars": 10},
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    }


def _pool_entry(sid, *, live_tier=None, status="PAPER_ACTIVE"):
    entry = {"strategy_id": sid, "candidate_id": f"c_{sid}", "status": status,
             "strategy_spec": _strategy_spec(sid)}
    if live_tier is not None:
        entry[pool_store.LIVE_TIER_FIELD] = live_tier
    return entry


def _write_pool(root, *entries):
    path = pool_store.pool_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"active_strategies": list(entries)}), encoding="utf-8")
    return path


def _armed_row(status):
    return next(c for c in status["checks"] if c["check"] == "live_armed_strategies")


def test_the_armed_set_is_a_row(tmp_path, clean_env):
    _write_pool(tmp_path,
                _pool_entry("S1", live_tier=pool_store.LIVE_TIER_LIVE),
                _pool_entry("S2"))
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    row = _armed_row(status)
    assert row["ok"] is True
    assert "1 armed of 2 occupying" in row["detail"]
    # As data too: the render's WIRED note reads the count, and a JSON consumer must not have
    # to parse prose.
    assert status["live_armed_strategies"] == {
        "known": True, "armed": 1, "occupying": 2, "error": None,
    }


def test_zero_armed_is_said_loudly_but_cannot_move_the_ready_verdict(tmp_path, clean_env):
    """Zero armed is the deliberate steady state (#648's migration default), not a runtime
    fault: `ready` keeps meaning "may this RUNTIME trade". But the detail must say what zero
    means, because READY above an empty armed set is exactly the 2026-08-10 misread."""
    _write_pool(tmp_path, _pool_entry("S1"), _pool_entry("S2", status="WARNING"))
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    row = _armed_row(status)
    assert row["ok"] is True
    assert "0 armed of 2 occupying" in row["detail"]
    assert "NO strategy may open a real position" in row["detail"]
    # Arming one strategy is a strategy-level change; the runtime-level verdict must not move
    # in either direction.
    _write_pool(tmp_path,
                _pool_entry("S1", live_tier=pool_store.LIVE_TIER_LIVE),
                _pool_entry("S2", status="WARNING"))
    rearmed = live_readiness.build_readiness(root=tmp_path, now=NOW)
    assert rearmed["ready"] == status["ready"]


def test_a_missing_pool_is_an_empty_pool_not_a_fault(tmp_path, clean_env):
    """`load_active_pool` documents missing = empty: a fresh machine honestly has zero of
    zero, and must not read as UNREADABLE."""
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    row = _armed_row(status)
    assert row["ok"] is True
    assert "0 armed of 0 occupying" in row["detail"]


@pytest.mark.parametrize("content, code", [
    ("not json{", "STRATEGY_POOL_UNREADABLE"),
    (json.dumps({"active_strategies": [{"strategy_id": "S1"}]}), "STRATEGY_POOL_INVALID"),
])
def test_an_unreadable_pool_fails_the_row_and_never_reads_as_zero(
    tmp_path, clean_env, content, code
):
    """The entry door keeps "no strategy is armed" and "the pool could not be read" apart with
    two reason codes, and the board must not collapse them into a comfortable 0: an unreadable
    pool refuses every live entry at the door, which is a fault with a repair — a FAIL row."""
    path = pool_store.pool_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    row = _armed_row(status)
    assert row["ok"] is False
    assert "UNREADABLE" in row["detail"] and code in row["detail"]
    assert "0 armed" not in row["detail"]
    assert status["live_armed_strategies"] == {
        "known": False, "armed": None, "occupying": None, "error": code,
    }
    assert status["ready"] is False


def test_the_render_qualifies_the_wired_note_when_nothing_is_armed(tmp_path, clean_env):
    """The WIRED note promises "REAL positions once every FAIL clears" — the sentence a reader
    believed on 2026-08-10 while the armed set was empty. The qualification must appear where
    the promise is made, not only in a PASS row further up."""
    _write_pool(tmp_path, _pool_entry("S1"))
    text = live_readiness.render_readiness_text(
        live_readiness.build_readiness(root=tmp_path, now=NOW))
    text.encode("ascii")
    if live_readiness.AUTONOMOUS_ROUTING_WIRED:
        assert "0 strategies are armed" in text
        assert "no autonomous entry will OPEN" in text
        assert "--live-tier LIVE" in text


def test_the_render_does_not_cry_zero_when_a_strategy_is_armed(tmp_path, clean_env):
    _write_pool(tmp_path, _pool_entry("S1", live_tier=pool_store.LIVE_TIER_LIVE))
    text = live_readiness.render_readiness_text(
        live_readiness.build_readiness(root=tmp_path, now=NOW))
    assert "1 armed of 1 occupying" in text
    assert "0 strategies are armed" not in text


# === whose environment is this board describing? (#382) ==============================
# The board computes most rows from `os.environ`, so it answers for the process running it.
# Once the operator console and the assistant read door started serving it from containers
# that deliberately carry no MVP_LIVE_*, "live trading off" became a false statement about a
# system whose scheduler held an open gate. These lock the fix: the board reports what the
# trading process recorded, and refuses a bare "off" its own env cannot support.

def _write_cycle(root, *, status: str, created_at: str):
    """One crypto_cycle ledger row — the trading process's own record of its live gate."""
    from runtime.mvp_runtime.store import LEDGER_REL, RECORDS_FILE

    ledger = root / LEDGER_REL
    ledger.mkdir(parents=True, exist_ok=True)
    row = {"kind": "crypto_cycle",
           "record": {"live_route_status": status, "created_at": created_at}}
    with (ledger / RECORDS_FILE).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def test_recorded_gate_is_unknown_without_a_cycle(tmp_path, clean_env):
    """No record is not evidence of "off" — it is absence, and it says so."""
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    assert status["recorded_gate"]["known"] is False
    assert live_readiness.contradicts_recorded_gate(status) is False
    assert "UNKNOWN" in live_readiness.render_readiness_text(status)


def test_a_blind_process_will_not_claim_live_trading_is_off(tmp_path, clean_env):
    """The bug itself: no live env here, but the process that CAN trade was gated open."""
    _write_cycle(tmp_path, status="HELD", created_at="2026-07-23T11:55:00Z")
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)

    assert status["recorded_gate"]["open"] is True
    assert live_readiness.contradicts_recorded_gate(status) is True
    text = live_readiness.render_readiness_text(status)
    assert "THIS PROCESS CANNOT SEE THE LIVE-TRADING ENVIRONMENT" in text
    # The one line a hurried reader keeps must not be an unqualified system claim.
    assert "NOT READY (THIS PROCESS ONLY)" in text
    assert "NOT READY - every FAIL above must clear first" not in text


def test_env_rows_carry_their_scope_on_the_row_while_the_process_is_blind(tmp_path, clean_env):
    """The banner was not enough (2026-08-10): a reader that summarises the board carries the
    ROW out of it, so `[FAIL] live_trading_opt_in ... (live trading off)` became "live trading
    is disabled" three times in one hour while the scheduler held an OPEN gate. The scope now
    rides the mark, which no summary can drop and keep the row."""
    _write_cycle(tmp_path, status="HELD", created_at="2026-07-23T11:55:00Z")
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    text = live_readiness.render_readiness_text(status)

    for check in live_readiness.ENV_SCOPED_CHECKS:
        row = next(line for line in text.splitlines() if check in line)
        assert row.startswith(f"[{live_readiness.OUT_OF_SCOPE_MARK}]"), row
        assert live_readiness.OUT_OF_SCOPE_DETAIL in row, row
    # The sentence that actually reached the operator as a system claim is GONE, not annotated.
    assert "live trading off" not in text
    assert "MVP_LIVE_TRADING is not" not in text
    # The guard dry-run refuses for the same absent env; its blocks are withheld, not echoed.
    assert "SCOPE  : this container cannot run the guard for real" in text
    assert "BLOCK  :" not in text
    # The kept line states what IS known rather than only what it is not evidence of.
    assert f"gate was recorded OPEN at {status['recorded_gate']['recorded_at']}" in text
    # Rendering only. The record every machine consumer reads is untouched, and so is `ready`.
    assert status["ready"] is False
    assert all(c["ok"] is False for c in status["checks"]
               if c["check"] in live_readiness.ENV_SCOPED_CHECKS)


def test_a_blind_process_still_says_FAIL_for_a_failure_it_can_see(tmp_path, clean_env):
    """The downgrade is scoped to the env rows. A check that fails for a reason this container
    CAN observe is a real finding, and blurring it would trade one false report for another."""
    _write_cycle(tmp_path, status="HELD", created_at="2026-07-23T11:55:00Z")
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    text = live_readiness.render_readiness_text(status)

    own_detail = next(c["detail"] for c in status["checks"]
                      if c["check"] == "daily_loss_breaker" and not c["ok"])
    breaker = next(line for line in text.splitlines() if "daily_loss_breaker" in line)
    assert breaker.startswith("[FAIL]"), breaker
    assert own_detail in breaker      # its own finding, verbatim — not the scoped stand-in
    assert live_readiness.OUT_OF_SCOPE_DETAIL not in breaker


def test_env_rows_read_FAIL_when_nothing_says_the_system_is_trading(tmp_path, clean_env):
    """No record, no downgrade. Absence of evidence about the gate is not permission to soften
    the rows — the ordinary machine with no live env must still read a plain FAIL."""
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    text = live_readiness.render_readiness_text(status)

    opt_in = next(line for line in text.splitlines() if "live_trading_opt_in" in line)
    assert opt_in.startswith("[FAIL]"), opt_in
    assert live_readiness.OUT_OF_SCOPE_MARK not in text
    assert "SCOPE  : dry-run" not in text


def test_a_recorded_disabled_gate_is_reported_as_off(tmp_path, clean_env):
    """Genuinely off must still read as off — the fix must not blur the real negative."""
    _write_cycle(tmp_path, status="DISABLED", created_at="2026-07-23T11:55:00Z")
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)

    assert status["recorded_gate"]["open"] is False
    assert live_readiness.contradicts_recorded_gate(status) is False
    text = live_readiness.render_readiness_text(status)
    assert "THIS PROCESS CANNOT SEE" not in text
    assert "NOT READY - every FAIL above must clear first" in text


def test_a_stale_record_is_not_evidence_about_now(tmp_path, clean_env):
    """An old open gate must not manufacture a warning: that trades one false claim for
    another, in the more alarming direction."""
    _write_cycle(tmp_path, status="HELD", created_at="2026-07-20T00:00:00Z")
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)

    assert status["recorded_gate"]["stale"] is True
    assert live_readiness.contradicts_recorded_gate(status) is False
    assert "STALE" in live_readiness.render_readiness_text(status)


def test_the_newest_cycle_decides(tmp_path, clean_env):
    """A gate that closed and reopened reads as open; the board follows the last record."""
    _write_cycle(tmp_path, status="DISABLED", created_at="2026-07-23T11:00:00Z")
    _write_cycle(tmp_path, status="OPENED", created_at="2026-07-23T11:58:00Z")
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    assert status["recorded_gate"]["status"] == "OPENED"
    assert live_readiness.contradicts_recorded_gate(status) is True


def test_the_recorded_gate_cannot_move_the_ready_verdict(tmp_path, clean_env):
    """`ready` stays "can THIS process trade" — the CLI exit code is a script precondition,
    so an observability row must never be able to flip it."""
    before = live_readiness.build_readiness(root=tmp_path, now=NOW)
    _write_cycle(tmp_path, status="HELD", created_at="2026-07-23T11:55:00Z")
    after = live_readiness.build_readiness(root=tmp_path, now=NOW)

    assert before["ready"] == after["ready"]
    assert [c["check"] for c in before["checks"]] == [c["check"] for c in after["checks"]]
    assert "live_gate_recorded" not in {c["check"] for c in after["checks"]}


def test_an_unreadable_ledger_reports_unknown_rather_than_off(tmp_path, clean_env):
    """Fail-closed: a corrupt ledger must not become a confident answer in either direction."""
    from runtime.mvp_runtime.store import LEDGER_REL, RECORDS_FILE

    ledger = tmp_path / LEDGER_REL
    ledger.mkdir(parents=True, exist_ok=True)
    (ledger / RECORDS_FILE).write_text('{"kind": "crypto_cycle" broken\n', encoding="utf-8")

    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    assert status["recorded_gate"]["known"] is False
    assert live_readiness.contradicts_recorded_gate(status) is False

# --- the structured view (sequence 2, P02) -----------------------------------
#
# One word cannot carry what this board says. The 2026-09-13 review found `ready: true` beside
# zero armed strategies read through the assistant door as "live trading is on". The view keeps
# the four facts apart as fields, so a consumer never derives one from the prose of another.

def _status_for_view(*, ready=True, armed=0, armed_known=True, gate_known=True, gate_open=True,
                     gate_stale=False):
    return {
        "created_at": NOW,
        "ready": ready,
        "checks": [{"check": "live_trading_opt_in", "ok": ready, "detail": ""}],
        "recorded_gate": {
            "known": gate_known, "open": gate_open if gate_known else None,
            "status": "HELD" if gate_known else None, "recorded_at": NOW if gate_known else None,
            "age_seconds": 60.0 if gate_known else None, "stale": gate_stale, "error": None,
        },
        "live_armed_strategies": {
            "known": armed_known, "armed": armed if armed_known else None,
            "occupying": 15 if armed_known else None,
            "error": None if armed_known else "POOL_UNREADABLE",
        },
        "guard_dry_run": {"status": "BLOCKED", "blocks": [], "repairs": []},
        "guard_dry_run_symbol": "BTCUSDT",
        "submitted_today": 0, "counter_error": None,
        "order_path_implemented": True, "autonomous_routing_wired": True,
    }


def test_ready_infrastructure_with_no_armed_strategy_is_not_live_entry_possible():
    data = live_readiness.readiness_data(_status_for_view(ready=True, armed=0))
    assert data["infrastructure_ready"] is True
    assert data["live_armed_strategies"] == {"known": True, "armed": 0, "occupying": 15, "error": None}
    assert data["live_entry_possible"] is False


def test_an_armed_strategy_behind_an_open_fresh_gate_is_live_entry_possible():
    data = live_readiness.readiness_data(_status_for_view(armed=1))
    assert data["live_entry_possible"] is True
    assert data["recorded_gate"]["open"] is True and data["recorded_gate"]["stale"] is False


def test_a_stale_or_closed_recorded_gate_means_no_entry_and_says_which():
    stale = live_readiness.readiness_data(_status_for_view(armed=1, gate_stale=True))
    assert stale["live_entry_possible"] is False and stale["recorded_gate"]["stale"] is True
    closed = live_readiness.readiness_data(_status_for_view(armed=1, gate_open=False))
    assert closed["live_entry_possible"] is False and closed["recorded_gate"]["open"] is False


def test_an_unknown_fact_answers_none_never_a_guess():
    pool_unreadable = live_readiness.readiness_data(_status_for_view(armed_known=False))
    assert pool_unreadable["live_entry_possible"] is None
    assert pool_unreadable["live_armed_strategies"]["error"] == "POOL_UNREADABLE"
    no_cycle = live_readiness.readiness_data(_status_for_view(armed=1, gate_known=False))
    assert no_cycle["live_entry_possible"] is None and no_cycle["recorded_gate"]["known"] is False


def test_the_view_carries_the_boards_instant_and_is_json_safe():
    data = live_readiness.readiness_data(_status_for_view())
    assert data["as_of"] == NOW and data["env_scope"] == "this_process"
    assert data["checks"] == [{"check": "live_trading_opt_in", "ok": True}]
    json.dumps(data)


def test_the_real_board_on_a_fresh_machine_has_the_view(tmp_path, clean_env):
    """Built from the real board, not a hand-made status: a fresh machine is not ready and
    knows neither its pool nor a recorded gate, so entry is unknown — not False."""
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    data = live_readiness.readiness_data(status)
    assert data["infrastructure_ready"] is False
    assert data["live_entry_possible"] is None
    assert {c["check"] for c in data["checks"]} == {c["check"] for c in status["checks"]}
    json.dumps(data)


def test_the_arm_row_says_why_and_whether_positions_are_still_managed(tmp_path):
    """A disarm has two situations with different consequences: a soft halt (ACTIVE — positions
    managed) and a stop (PAUSED/KILLED — management stopped too). The row names the reason and
    which one it is, instead of assuming a runtime-only resume."""
    from runtime.mvp_runtime.control import ACTIVE, KILLED, ControlState, ControlStore

    store = ControlStore(tmp_path)
    store.save(ControlState(mode=ACTIVE, updated_by="op", updated_at=NOW, reason="변동성 soft halt",
                            trading_armed=False))
    row = next(c for c in live_readiness.build_readiness(root=tmp_path, now=NOW)["checks"]
               if c["check"] == "trading_armed")
    assert row["ok"] is False and "변동성 soft halt" in row["detail"] and "still managed" in row["detail"]

    store.save(ControlState(mode=KILLED, updated_by="op", updated_at=NOW, reason="kill", trading_armed=False))
    row = next(c for c in live_readiness.build_readiness(root=tmp_path, now=NOW)["checks"]
               if c["check"] == "trading_armed")
    assert "management is stopped too" in row["detail"]
