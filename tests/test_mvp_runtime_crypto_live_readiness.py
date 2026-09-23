"""LP6 tests — the readiness board.

Under test: the readiness board reports every gate honestly, never raises on an unreadable input,
and cannot say READY while no order path exists.

The canary promotion-evidence tests that opened this file went with the promotion gate
(2026-09-15, PR1r): the count, the minimum and the `canary_evidence` row no longer exist. The
verified reader of the frozen registry is tested in `test_mvp_runtime_canary_evidence.py`.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from runtime.mvp_runtime.crypto import execution_stage as es
from runtime.mvp_runtime.crypto import live_promotion, live_readiness, live_route
from runtime.mvp_runtime.crypto import pool as pool_store
from runtime.mvp_runtime.crypto.live_pnl import LIVE_TRADING_ENV, state_dir
from runtime.mvp_runtime.crypto.live_order import CONFIRMATION_ENV, LIVE_CONFIRMATION_PHRASE
from runtime.mvp_runtime.errors import ToolError

NOW = "2026-07-23T12:00:00Z"

_LIVE_ENVS = (
    LIVE_TRADING_ENV, CONFIRMATION_ENV, "MVP_LIVE_MANUAL_KILL_SWITCH",
    "MVP_LIVE_MAX_ORDER_NOTIONAL_USDT", "MVP_LIVE_ABSOLUTE_MAX_NOTIONAL_USDT",
    "MVP_LIVE_MAX_DAILY_ORDER_COUNT", "MVP_LIVE_MAX_OPEN_NOTIONAL_USDT",
    "MVP_LIVE_DAILY_LOSS_LIMIT_USDT",
    "MVP_ACCOUNT_FEED", "BINANCE_ACCOUNT_API_KEY", "BINANCE_ACCOUNT_API_SECRET",
    "MVP_MARKET_DATA",
)


@pytest.fixture
def clean_env(monkeypatch):
    """A machine with nothing configured — the state every fresh checkout is in."""
    for name in _LIVE_ENVS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


# === the readiness board ============================================================

def _register_budget(root, *, symbol_allowlist=("BTCUSDT",), **cap_overrides):
    """Register a valid live-trading budget under ``root`` (step 6b: the guard's cap source).
    Built today, so it carries no validity window and stands at any ``now``."""
    from runtime.mvp_runtime.crypto import live_budget
    caps = dict(max_order_notional_usdt=60.0, absolute_max_notional_usdt=200.0,
                max_daily_order_count=2, max_open_notional_usdt=120.0,
                daily_loss_limit_usdt=20.0)
    caps.update(cap_overrides)
    rec = live_budget.build_live_trading_budget_record(
        caps=caps, symbol_allowlist=list(symbol_allowlist), registered_by="thomas",
        registered_at="2026-07-01T00:00:00Z")
    live_budget.write_registered_budget(rec, root=root)
    return rec


def _write_legacy_budget(root, *, valid_from, valid_until):
    """A budget as registered before 2026-09-15 (PR1r): a validity window and the retired canary
    cap, both inside the self-hash. Hashed raw — nothing today can build this shape."""
    from runtime.read_only_kernel import integrity
    from runtime.mvp_runtime.crypto import live_budget
    body = {k: v for k, v in _register_budget(root).items() if k != "record_sha256"}
    body["caps"] = {**body["caps"], "min_clean_canary_orders": 4}
    body.update(valid_from=valid_from, valid_until=valid_until, registered_at=valid_from)
    body["record_sha256"] = integrity.sha256_record(body)
    live_budget.budget_path(root).write_text(json.dumps(body), encoding="utf-8")
    return body


def _allowlist_blocks(status):
    """The dry-run's symbol-scope blocks — the ones the board omitted its way into."""
    return [b for b in (status["guard_dry_run"].get("blocks") or []) if "allowlist" in b]


def test_fresh_machine_is_not_ready(tmp_path, clean_env):
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    assert status["ready"] is False
    failed = {c["check"] for c in status["checks"] if not c["ok"]}
    # order_path_implemented now passes (LP4 landed); every AUTHORITY row must still fail.
    assert {"live_trading_opt_in", "confirmation_phrase", "registered_budget"} <= failed


def test_board_reports_every_gate(tmp_path, clean_env):
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    assert {c["check"] for c in status["checks"]} == {
        "live_trading_opt_in", "confirmation_phrase", "registered_budget", "risk_limits_record",
        "manual_kill_switch", "runtime_active", "trading_armed", "live_armed_strategies",
        "daily_loss_breaker", "bracket_breaker", "api_breaker", "entry_marks", "pre_order_snapshots",
        "account_visibility", "market_data_visibility", "order_path_implemented",
        "autonomous_routing_wired", "execution_stage", "venue_contract",
    }


def test_unreadable_entry_marks_turn_the_board_red_and_say_not_to_delete(tmp_path, clean_env):
    """PR2a: a corrupt marks file refuses every live entry, so the board must say so — and must
    not send the operator to the one repair that re-opens bars already sent on."""
    from runtime.mvp_runtime.crypto.live_order import ENTRY_MARKS_FILENAME
    from runtime.mvp_runtime.crypto.state import venue_state_dir

    path = venue_state_dir(tmp_path) / ENTRY_MARKS_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{torn", encoding="utf-8")
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    row = next(c for c in status["checks"] if c["check"] == "entry_marks")
    assert row["ok"] is False and status["ready"] is False
    assert "LIVE_ENTRY_MARKS_UNREADABLE" in row["detail"] and "Do not just delete it" in row["detail"]


def test_the_entry_marks_row_names_the_entries_in_flight_and_the_ones_left_behind(tmp_path, clean_env):
    """PR2b-2: a claim is normal while its entry runs. One that expired without being given back
    was left by an entry that never finished, so the book and the venue need a look."""
    import json

    from runtime.mvp_runtime import timeutil
    from runtime.mvp_runtime.crypto.live_order import ENTRY_MARKS_FILENAME, ENTRY_MARKS_VERSION
    from runtime.mvp_runtime.crypto.state import venue_state_dir

    path = venue_state_dir(tmp_path) / ENTRY_MARKS_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "version": ENTRY_MARKS_VERSION, "entered": {}, "cooldown": {},
        "in_flight": {
            "BTCUSDT": {"claimed_at": timeutil.plus_minutes(NOW, -5), "door": "probe",
                        "client_order_id": "TAI_BTCUSDT_LONG_a"},
            "ETHUSDT": {"claimed_at": timeutil.plus_minutes(NOW, -120), "door": "autonomous",
                        "client_order_id": "TAI_ETHUSDT_LONG_b"},
            # Expired more than a day ago: it holds nothing and is no longer shown.
            "SOLUSDT": {"claimed_at": timeutil.plus_minutes(NOW, -(24 * 60 + 31)), "door": "probe",
                        "client_order_id": "TAI_SOLUSDT_LONG_c"},
        },
    }), encoding="utf-8")
    row = next(c for c in live_readiness.build_readiness(root=tmp_path, now=NOW)["checks"]
               if c["check"] == "entry_marks")
    assert row["ok"] is True
    assert f"BTCUSDT (probe since {timeutil.plus_minutes(NOW, -5)})" in row["detail"]
    assert "ETHUSDT (autonomous since" in row["detail"]
    assert row["detail"].count("EXPIRED unreleased") == 1
    assert "SOLUSDT" not in row["detail"]


def test_the_entry_marks_row_names_the_cooldowns_still_holding(tmp_path, clean_env):
    from runtime.mvp_runtime.crypto.live_order import LiveEntryMarks
    from runtime.mvp_runtime.crypto.live_pnl import LIVE_TRADING_FLAGS, LIVE_TRADING_PROVIDER_ID
    from tests._helpers import make_gate_authorization

    auth = make_gate_authorization(flags=LIVE_TRADING_FLAGS, provider_id=LIVE_TRADING_PROVIDER_ID)
    marks = LiveEntryMarks(root=tmp_path, authorization=auth)
    marks.claim_bar(symbol="BTCUSDT", timeframe="4h", bar_time="2026-07-25T00:00:00Z")
    # NOW's last closed 4h bar opens before this bound, so it still holds; the 1d one has passed.
    marks.record_stop_cooldown(symbol="BTCUSDT", timeframe="4h", until="2026-07-26T00:00:00Z")
    marks.record_stop_cooldown(symbol="ETHUSDT", timeframe="1d", until="2026-07-01T00:00:00Z")
    row = next(c for c in live_readiness.build_readiness(root=tmp_path, now=NOW)["checks"]
               if c["check"] == "entry_marks")
    assert row["ok"] is True
    assert "1 context(s) have sent an entry" in row["detail"]
    assert "BTCUSDT__4h until the 2026-07-26T00:00:00Z bar" in row["detail"]
    assert "ETHUSDT" not in row["detail"]


@pytest.mark.parametrize("now,holds", [
    ("2026-07-28T08:05:00Z", True),    # evaluates the 04:00 bar
    ("2026-07-28T12:05:00Z", True),    # evaluates the 08:00 bar — still before the bound
    ("2026-07-28T16:05:00Z", False),   # evaluates the 12:00 bar: the bound itself enters
])
def test_a_cooldown_is_judged_against_the_bar_the_context_evaluates(now, holds):
    assert live_readiness._cooldown_holds_now("BTCUSDT__4h", "2026-07-28T12:00:00Z", now) is holds


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
    # Built today: no window, and the row says so rather than leaving the end to be guessed.
    assert row["detail"].endswith("no expiry") and "valid until" not in row["detail"]


def test_an_expired_budget_fails_the_budget_row(tmp_path, clean_env):
    """A budget registered before 2026-09-15 still carries its validity window, and outside it
    the budget is still invalid (PR1r retired the window for new records, not for these) — the
    row names why and when, and the guard dry-run refuses (the caps fall back to blocking)."""
    _write_legacy_budget(tmp_path, valid_from="2026-06-01T00:00:00Z", valid_until="2026-06-30T00:00:00Z")
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)  # NOW is 2026-07-23, past valid_until
    row = next(c for c in status["checks"] if c["check"] == "registered_budget")
    assert row["ok"] is False and "invalid" in row["detail"]
    assert "OUTSIDE_VALIDITY_WINDOW" in row["detail"] and "2026-06-30T00:00:00Z" in row["detail"]
    assert status["guard_dry_run"]["approved"] is False


def test_a_legacy_budget_inside_its_window_still_names_its_end(tmp_path, clean_env):
    _write_legacy_budget(tmp_path, valid_from="2026-07-01T00:00:00Z", valid_until="2027-08-30T00:00:00Z")
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    row = next(c for c in status["checks"] if c["check"] == "registered_budget")
    assert row["ok"] is True and "order<=60.0" in row["detail"]
    assert row["detail"].endswith("valid until 2027-08-30T00:00:00Z")


# The C4 risk-limits row (3b) follows the same window rule as the budget row (PR1r): a record
# registered today reads "no expiry", a legacy one names its window and is still held to it.
_RISK_LIMITS = dict(risk_per_trade=0.01, daily_max_loss_r=-2.0, weekly_max_loss_r=-5.0,
                    max_consecutive_losses=4, max_drawdown_pct=-10.0)


def _write_legacy_risk_limits(root, *, valid_from, valid_until):
    """Risk limits as registered before 2026-09-15: a validity window inside the self-hash."""
    from runtime.read_only_kernel import integrity
    from runtime.mvp_runtime.crypto import risk_limits
    body = {
        "schema_version": risk_limits.RISK_LIMITS_SCHEMA_VERSION,
        "limits_id": "risklimits_0123456789abcdef0123", "limits": dict(_RISK_LIMITS),
        "valid_from": valid_from, "valid_until": valid_until,
        "registered_by": "thomas", "registered_at": valid_from,
    }
    body["record_sha256"] = integrity.sha256_record(body)
    risk_limits.write_registered_limits(body, root=root)


def _risk_row(status):
    return next(c for c in status["checks"] if c["check"] == "risk_limits_record")


def test_a_windowless_risk_limits_record_reads_no_expiry_and_names_its_rebase(tmp_path, clean_env):
    from runtime.mvp_runtime.crypto import risk_limits
    record = risk_limits.build_risk_limits_record(
        limits=_RISK_LIMITS, registered_by="thomas", registered_at="2026-07-01T00:00:00Z",
        drawdown_baseline_rebase={"excluded_strategy_ids": ["cand:c3", "cand:c9"], "reason": "retired"})
    risk_limits.write_registered_limits(record, root=tmp_path)
    row = _risk_row(live_readiness.build_readiness(root=tmp_path, now=NOW))
    assert row["ok"] is True and "consecutive 4" in row["detail"]
    assert f"registered {record['limits_id']}" in row["detail"]
    assert "registered_at 2026-07-01T00:00:00Z, no expiry" in row["detail"]
    assert row["detail"].endswith("drawdown baseline rebase excludes 2 lineage key(s)")
    assert "valid until" not in row["detail"]


def test_a_legacy_risk_limits_record_inside_its_window_names_its_end(tmp_path, clean_env):
    _write_legacy_risk_limits(tmp_path, valid_from="2026-07-01T00:00:00Z", valid_until="2027-08-30T00:00:00Z")
    row = _risk_row(live_readiness.build_readiness(root=tmp_path, now=NOW))
    assert row["ok"] is True
    assert row["detail"].endswith("valid until 2027-08-30T00:00:00Z") and "rebase" not in row["detail"]


def test_a_lapsed_legacy_risk_limits_record_fails_the_row_and_names_its_window(tmp_path, clean_env):
    from runtime.mvp_runtime.crypto import risk_limits
    _write_legacy_risk_limits(tmp_path, valid_from="2026-06-01T00:00:00Z", valid_until="2026-06-30T00:00:00Z")
    row = _risk_row(live_readiness.build_readiness(root=tmp_path, now=NOW))  # NOW is past valid_until
    assert row["ok"] is False and risk_limits.LIMITS_EXPIRED in row["detail"]
    assert "registered for 2026-06-01T00:00:00Z .. 2026-06-30T00:00:00Z" in row["detail"]


def test_a_tampered_budget_fails_the_budget_row(tmp_path, clean_env):
    from runtime.mvp_runtime.crypto import live_budget

    _register_budget(tmp_path)
    path = live_budget.budget_path(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["caps"]["max_order_notional_usdt"] = 199.0     # edited after hashing
    path.write_text(json.dumps(data), encoding="utf-8")
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    row = next(c for c in status["checks"] if c["check"] == "registered_budget")
    assert row["ok"] is False and row["detail"] == f"registered but invalid: {live_budget.BUDGET_TAMPERED}"
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


def test_the_board_no_longer_reads_the_canary_registry(tmp_path, clean_env):
    """Retired 2026-09-15 (PR1r): the `canary_evidence` row went with the promotion gate, so a
    damaged frozen registry is the history board's news, not a failed gate here."""
    target = state_dir(tmp_path)
    target.mkdir(parents=True, exist_ok=True)
    (target / live_promotion.CANARY_ORDERS_FILENAME).write_text("garbage\n", encoding="utf-8")
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    assert not any("CANARY_HISTORY" in c["detail"] or "canary" in c["check"] for c in status["checks"])
    assert "clean_canary_orders" not in status["guard_dry_run"]


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
    already registered. Both real doors (`live_route`, `run_slippage_probe`) read the scope off
    the same budget the caps come from."""
    before = live_readiness.build_readiness(root=tmp_path, now=NOW)
    assert _allowlist_blocks(before)          # no budget: the block is real

    _register_budget(tmp_path, symbol_allowlist=["BTCUSDT"])
    after = live_readiness.build_readiness(root=tmp_path, now=NOW)
    assert _allowlist_blocks(after) == []     # a registered scope is not an absent one
    # It still refuses — on the doors that ARE shut (opt-in, phrase). The point is that the
    # board no longer invents a thirteenth.
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
    *authority* — no grant, no phrase, no registered budget. Configuring the old env caps is
    not enough."""
    clean_env.setenv(CONFIRMATION_ENV, LIVE_CONFIRMATION_PHRASE)
    clean_env.setenv("MVP_LIVE_MAX_ORDER_NOTIONAL_USDT", "60")
    clean_env.setenv("MVP_LIVE_MAX_DAILY_ORDER_COUNT", "2")
    clean_env.setenv("MVP_LIVE_MAX_OPEN_NOTIONAL_USDT", "120")
    clean_env.setenv("MVP_LIVE_DAILY_LOSS_LIMIT_USDT", "20")
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    assert status["ready"] is False
    assert next(c for c in status["checks"] if c["check"] == "order_path_implemented")["ok"]
    failed = {c["check"] for c in status["checks"] if not c["ok"]}
    assert "live_trading_opt_in" in failed and "registered_budget" in failed
    assert status["guard_dry_run"]["approved"] is False


def test_a_machine_below_the_stage_a_live_entry_needs_fails_the_board(tmp_path, clean_env):
    """PR1b: the entry guard refuses below LIVE_AUTONOMOUS, so the board says so in the row that
    decides `ready`. A PASS beside a machine that reads READ_ONLY would be the all-PASS-with-
    nothing-armed board the audit started from."""
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    row = next(c for c in status["checks"] if c["check"] == "execution_stage")
    assert row["ok"] is False and status["ready"] is False
    assert "EXECUTION_STAGE_RECORD_MISSING" in row["detail"] and "LIVE_AUTONOMOUS" in row["detail"]
    assert "Closing is never gated" in row["detail"]
    assert status["execution_stage"]["enforced"] is True
    assert status["execution_stage"]["admits_entry"] is False
    assert live_readiness.readiness_data(status)["live_entry_possible"] is not True
    # The dry-run is the board's authoritative answer, so it must be judged against the stage the
    # row reports — a board that fails the row while its dry-run passes would be worse than silent.
    assert status["guard_dry_run"]["execution_stage"] == "READ_ONLY"
    assert any("execution stage" in block for block in status["guard_dry_run"]["blocks"])


def test_the_board_row_passes_at_the_rung_the_doors_admit(tmp_path, clean_env, monkeypatch):
    monkeypatch.setattr(live_readiness, "resolve_execution_stage", lambda root=None, **kw: es.StageStatus(
        stage="LIVE_AUTONOMOUS", valid=True, reason_code=None, recorded_stage="LIVE_AUTONOMOUS"))
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    row = next(c for c in status["checks"] if c["check"] == "execution_stage")
    assert row["ok"] is True and row["detail"] == "LIVE_AUTONOMOUS"


def test_the_testnet_evidence_line_names_an_unreadable_registry(tmp_path, clean_env):
    """Crypto PR1d-2: what the LIVE_AUTONOMOUS climb would find. A corrupt registry must read as
    UNREADABLE — rendering it as "none recorded" would tell the operator a machine that HAS earned
    its evidence never ran a cycle (review of #878)."""
    import json

    from runtime.mvp_runtime.crypto import testnet_evidence

    def line(status):
        return next(l for l in live_readiness.render_readiness_text(status).splitlines()
                    if "testnet_evidence" in l)

    empty = live_readiness.build_readiness(root=tmp_path, now=NOW)
    assert "none recorded" in line(empty) and empty["testnet_evidence"]["error"] is None

    def leg(name):
        return {"leg": name, "algo": name == "SL",
                "order_type": "STOP_MARKET" if name == "SL" else "LIMIT",
                "observed_status": "NEW", "withdrawn": True}

    complete = testnet_evidence.build_cycle_record(
        cycle_id="cyc_board", symbol="BTCUSDT",
        entry={"reconcile_status": "RECONCILED", "mismatches": []},
        protective_legs=[leg("SL"), leg("TP")],
        exit_result={"reconcile_status": "RECONCILED", "reduce_only": True},
        position_reconciliation={"status": "RECONCILED", "venue_positions": []},
        adapter_tool_id="t", base_url_host="testnet.binancefuture.com",
        started_at=NOW, completed_at=NOW,
    )
    testnet_evidence.append_cycle(complete, tmp_path)
    earned = live_readiness.build_readiness(root=tmp_path, now=NOW)
    assert "cyc_board" in line(earned) and earned["testnet_evidence"]["complete"] == ["cyc_board"]

    half = {**complete}
    half["protective_legs"] = [{**leg("SL"), "withdrawn": False}, leg("TP")]
    from runtime.read_only_kernel import integrity

    body = {k: v for k, v in half.items() if k != "record_sha256"}
    body["record_sha256"] = integrity.sha256_record(body)
    testnet_evidence.evidence_path(tmp_path).write_text(json.dumps(body) + "\n", encoding="utf-8")
    incomplete = live_readiness.build_readiness(root=tmp_path, now=NOW)
    assert "none complete" in line(incomplete)

    testnet_evidence.evidence_path(tmp_path).write_text('{"cycle_id": "x"}\n', encoding="utf-8")
    broken = live_readiness.build_readiness(root=tmp_path, now=NOW)
    assert "UNREADABLE" in line(broken)
    assert broken["testnet_evidence"]["error"] == testnet_evidence.EVIDENCE_TAMPERED
    # And the board still renders: a corrupt evidence file is not a broken board.
    assert live_readiness.render_readiness_text(broken)


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
    # thing: unwired, that the only door is the deliberate probe; wired, that a scheduled run
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
        assert "run_slippage_probe.py" in text


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


def test_market_data_is_reported_as_a_live_precondition(tmp_path, clean_env):
    """No market data means the collector is the synthetic mock, and nothing live runs on it.

    The slippage probe's reference price refuses a synthesised one, and data health will not
    trade on a synthetic feed. The row was first the canary door's precondition (its
    declared-notional check), which went with the door on 2026-09-15. A precondition only a
    docstring knows about is one the operator discovers at a terminal holding real keys (#201's
    lesson), so it is a row.
    """
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    row = next(c for c in status["checks"] if c["check"] == "market_data_visibility")
    assert row["ok"] is False
    assert "probe" in row["detail"], "say what the operator loses without it"


def test_market_data_env_alone_passes_the_row(tmp_path, clean_env, monkeypatch):
    """The environment is the gate (Thomas 2026-08-10): the opt-in alone passes the row.

    The safety the old grant check bought is not lost, just relocated: without the opt-in
    the selector returns the MOCK, whose synthesised price the probe's reference-price read
    rejects (`REFERENCE_PRICE_SYNTHETIC`) — the test below pins that failing direction."""
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
    """The local ledger never sees a venue-side or operator-side close (and was empty by
    construction while the entry-only canary door was the one door), so a venue figure must win
    outright rather than being averaged or cross-checked against a structural zero."""
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
    # The entry carries no artifact stamp and trades no labelled rule, so the gate refuses it
    # whatever approval it names; "armed" must not read as "can trade" (PR3a review).
    assert "1 of them cannot trade: S1 (spec)" in row["detail"]


def test_an_arm_that_can_trade_is_not_called_one_that_cannot(tmp_path, clean_env):
    from runtime.mvp_runtime.approval_store import ApprovalStore
    from runtime.mvp_runtime.crypto.strategy import StrategySpec
    from tests._helpers import live_arm_approval, stamped_pool_entry

    entry = _pool_entry("S1", live_tier=pool_store.LIVE_TIER_LIVE)
    entry["strategy_rule_hash"] = StrategySpec.from_dict(entry["strategy_spec"]).strategy_rule_hash
    entry["promoted_at"] = "2026-07-27T23:55:00Z"
    armed = stamped_pool_entry(entry)
    # The approval Thomas answered to arm it, which the gate verifies at order time (review of #906).
    approval = live_arm_approval(("c_S1",), (armed["strategy_rule_hash"],),
                                 artifacts=(armed[pool_store.ARTIFACT_SHA256_FIELD],))
    ApprovalStore.default(tmp_path).append([approval])
    armed[pool_store.LIVE_TIER_APPROVAL_FIELD] = approval["approval_id"]
    _write_pool(tmp_path, armed, _pool_entry("S2"))
    row = _armed_row(live_readiness.build_readiness(root=tmp_path, now=NOW))
    assert "1 armed of 2 occupying" in row["detail"] and "cannot trade" not in row["detail"]
    unbound = {k: v for k, v in armed.items() if k not in ("strategy_artifact_sha256", "strategy_artifact")}
    _write_pool(tmp_path, unbound, _pool_entry("S2"))
    row = _armed_row(live_readiness.build_readiness(root=tmp_path, now=NOW))
    assert "1 of them cannot trade: S1 (unbound)" in row["detail"]


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
    """The WIRED note promises REAL positions — "once every FAIL clears" until PR5b, the sentence a
    reader believed on 2026-08-10 while the armed set was empty; "while LIVE ENTRY POSSIBLE reads
    YES" since. The qualification must appear where the promise is made, not only in a PASS row
    further up."""
    _write_pool(tmp_path, _pool_entry("S1"))
    text = live_readiness.render_readiness_text(
        live_readiness.build_readiness(root=tmp_path, now=NOW))
    text.encode("ascii")
    if live_readiness.AUTONOMOUS_ROUTING_WIRED:
        # The promise is conditioned on the system's answer, never on this process's rows (PR5b).
        assert "opens and closes REAL positions while LIVE ENTRY POSSIBLE\n        reads YES" in text
        assert "once every FAIL clears" not in text
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

def _write_cycle(root, *, status: str, created_at: str, live_gate=None):
    """One crypto_cycle ledger row — the trading process's own record of its live gate, and of its
    two switches when ``live_gate`` is given (the leg stamps them since PR5a)."""
    from runtime.mvp_runtime.store import LEDGER_REL, RECORDS_FILE

    ledger = root / LEDGER_REL
    ledger.mkdir(parents=True, exist_ok=True)
    record = {"live_route_status": status, "created_at": created_at}
    if live_gate is not None:
        record["live_gate"] = live_gate
    row = {"kind": "crypto_cycle", "record": record}
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
    # No budget, so no limit: BREACHED, a fact about the limit that holds on every machine.
    assert own_detail.startswith("BREACHED")
    breaker = next(line for line in text.splitlines() if "daily_loss_breaker" in line)
    assert breaker.startswith("[FAIL]"), breaker
    assert own_detail in breaker      # its own finding, verbatim — not the scoped stand-in
    assert live_readiness.OUT_OF_SCOPE_DETAIL not in breaker
    assert live_readiness.OUT_OF_SCOPE_LOSS_DETAIL not in breaker


def test_a_loss_breaker_without_an_account_feed_describes_only_this_container(tmp_path, clean_env):
    """NO DATA SOURCE because this process reads no account said "the limit currently bounds
    nothing" — a claim about the system — on a console whose readiness state reads the trading
    process's snapshot for the same figure (review of #907). Scoped on the row, as the env rows are;
    `ready` and the check row are untouched, and where the rows speak for the system it still FAILs."""
    _register_budget(tmp_path)
    _write_cycle(tmp_path, status="HELD", created_at="2026-07-23T11:55:00Z")
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    row = next(c for c in status["checks"] if c["check"] == "daily_loss_breaker")
    assert row["ok"] is False and row["detail"].startswith("NO DATA SOURCE") and row["env_scoped"] is True
    text = live_readiness.render_readiness_text(status)
    breaker = next(line for line in text.splitlines() if line.startswith("[") and "daily_loss_breaker" in line)
    assert breaker == (f"[{live_readiness.OUT_OF_SCOPE_MARK}] {'daily_loss_breaker':24} "
                       f"{live_readiness.OUT_OF_SCOPE_LOSS_DETAIL}")
    assert "bounds nothing" not in text
    assert status["ready"] is False
    # A fresh record of a closed gate: the rows speak for the system, and this one says what it saw.
    _write_cycle(tmp_path, status="DISABLED", created_at="2026-07-23T11:58:00Z")
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    text = live_readiness.render_readiness_text(status)
    breaker = next(line for line in text.splitlines() if line.startswith("[") and "daily_loss_breaker" in line)
    assert breaker.startswith("[FAIL]") and "NO DATA SOURCE" in breaker, breaker


def test_a_loss_breaker_with_an_account_feed_is_never_scoped(tmp_path, clean_env, monkeypatch):
    """A process holding the account feed measured the loss itself: its NO DATA SOURCE (a read that
    failed) is its own finding about the account, and reads FAIL."""
    from runtime.mvp_runtime.crypto import account

    _register_budget(tmp_path)
    monkeypatch.setenv(account.ACCOUNT_FEED_ENV, account.BINANCE_ACCOUNT)
    monkeypatch.setenv(account.ACCOUNT_API_KEY_ENV, "k")
    monkeypatch.setenv(account.ACCOUNT_API_SECRET_ENV, "s")
    monkeypatch.setattr(
        live_readiness, "read_account",
        lambda **kw: (_ for _ in ()).throw(ToolError("ACCOUNT_DATA_DEGRADED", "down")),
    )
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    row = next(c for c in status["checks"] if c["check"] == "daily_loss_breaker")
    assert row["ok"] is False and row["env_scoped"] is False
    text = live_readiness.render_readiness_text(status)
    breaker = next(line for line in text.splitlines() if line.startswith("[") and "daily_loss_breaker" in line)
    assert breaker.startswith("[FAIL]"), breaker


def test_with_no_record_the_env_rows_still_describe_only_this_container(tmp_path, clean_env):
    """No record, and still no "live trading off" (crypto PR5b, FC-10). This test pinned the
    opposite until PR5b — "absence of evidence is not permission to soften the rows" — which was
    right while the rows were the board's conclusion. The conclusion is the readiness state now,
    at the top and in the last line, so the rows no longer carry it; what they did carry was the
    one sentence a summariser lifts. A process without the environment says what it knows: it
    cannot see the environment, and no record of the system's gate is readable here."""
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    text = live_readiness.render_readiness_text(status)

    opt_in = next(line for line in text.splitlines() if "live_trading_opt_in" in line)
    assert opt_in.startswith(f"[{live_readiness.OUT_OF_SCOPE_MARK}]"), opt_in
    assert "live trading off" not in text and "MVP_LIVE_TRADING is not" not in text
    assert "no record of the trading process's gate is readable here" in text
    assert "no record of the system's own gate is readable here" in text
    # Still not READY, and still no entry: this decides what the board says, never what it permits.
    assert status["ready"] is False
    assert text.splitlines()[-1].startswith("LIVE ENTRY POSSIBLE: NO - blocked by ")
    assert live_readiness.readiness_data(status)["env_out_of_scope"] is True


def test_a_recorded_disabled_gate_is_reported_as_off(tmp_path, clean_env):
    """Genuinely off must still read as off — the fix must not blur the real negative."""
    _write_cycle(tmp_path, status="DISABLED", created_at="2026-07-23T11:55:00Z")
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)

    assert status["recorded_gate"]["open"] is False
    assert live_readiness.contradicts_recorded_gate(status) is False
    # The one case the env rows speak for the system too: its own fresh record says closed.
    assert live_readiness.env_out_of_scope(status) is False
    text = live_readiness.render_readiness_text(status)
    assert "THIS PROCESS CANNOT SEE" not in text
    assert "NOT READY - every FAIL above must clear first" in text
    opt_in = next(line for line in text.splitlines() if line.startswith("[") and "live_trading_opt_in" in line)
    assert opt_in.startswith("[FAIL]"), opt_in
    assert "live_gate_open (GATE_DISABLED)" in text.splitlines()[-1]


def test_a_stale_record_is_not_evidence_about_now(tmp_path, clean_env):
    """An old open gate must not manufacture a warning: that trades one false claim for
    another, in the more alarming direction. Nor may the console's own empty environment speak
    instead (FC-10): a kill writes no more cycles, so this is the state a kill leaves, and the
    board used to print "live trading off" here off the console's env — right by accident, for the
    wrong reason."""
    _write_cycle(tmp_path, status="HELD", created_at="2026-07-20T00:00:00Z")
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)

    assert status["recorded_gate"]["stale"] is True
    assert live_readiness.contradicts_recorded_gate(status) is False
    text = live_readiness.render_readiness_text(status)
    assert "STALE" in text
    # The gate row keeps its dated, STALE-marked reading; no banner or verdict claims it as now.
    assert "   the trading process recorded the gate OPEN" not in text
    assert "was recorded OPEN" not in text
    assert "is over two hours old - not a statement about now" in text
    assert "live trading off" not in text
    for check in live_readiness.ENV_SCOPED_CHECKS:
        row = next(line for line in text.splitlines() if line.startswith("[") and check in line)
        assert row.startswith(f"[{live_readiness.OUT_OF_SCOPE_MARK}]"), row
    assert live_readiness.env_out_of_scope(status) is True


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
# the facts apart as fields, so a consumer never derives one from the prose of another.

def _status_for_view(*, ready=True, armed=0, armed_known=True, gate_known=True, gate_open=True,
                     gate_stale=False, stage_admits=True, contract_usable=True):
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
        "execution_stage": {"stage": "LIVE_AUTONOMOUS" if stage_admits else "READ_ONLY",
                            "valid": stage_admits, "reason_code": None if stage_admits else "X",
                            "enforced": True, "admits_entry": stage_admits},
        "venue_contract": {"error": None, "recorded": True, "status": "PASS" if contract_usable else "FAIL",
                           "usable": contract_usable, "symbols": ["BTCUSDT"]},
    }


# What `live_entry_possible` is made of, and each fact that decides it, is the readiness state's:
# tests/test_mvp_runtime_crypto_readiness_state.py (model v2, PR5a). The four-fact answer these
# tests pinned read True under a kill or a disarm (FO-10).


def test_live_entry_possible_is_what_the_read_shim_says_it_is(tmp_path, clean_env):
    """The assistant is told `live_entry_possible` is `true` only when every entry of
    `readiness.components` is ok, and to quote `readiness.blocking` or `readiness.unknown` otherwise
    (`trading_readiness` in integrations/hermes/mcp/read_bridge_mcp.py, shims 2.12). 2.11 listed the
    four fields the value was made of and said "nothing else", which stopped being true the day the
    readiness state added the kill, the disarm and the breakers (PR5a) — as a list of members had gone
    stale twice before. The description names the lists now, so a joining component needs no new
    sentence; this pins that the value IS those lists, on hand-made statuses and on the real board, and
    that the description says so."""
    four_fields_of_2_11 = {
        "live_armed_strategies": {"known": True, "armed": 1},
        "recorded_gate": {"known": True, "open": True, "stale": False},
        "execution_stage": {"admits_entry": True},
        "venue_contract": {"usable": True},
    }
    for status in ({}, four_fields_of_2_11, _status_for_view(),
                   live_readiness.build_readiness(root=tmp_path, now=NOW)):
        data = live_readiness.readiness_data(status)
        components = data["readiness"]["components"]
        assert list(components) == list(live_readiness.READINESS_COMPONENTS)
        values = [component["ok"] for component in components.values()]
        expected = False if False in values else (None if None in values else True)
        assert data["live_entry_possible"] is expected
        assert data["readiness"]["blocking"] == [
            f"{name}:{c['reason']}" for name, c in components.items() if c["ok"] is False]
        assert data["readiness"]["unknown"] == [
            f"{name}:{c['reason']}" for name, c in components.items() if c["ok"] is None]
    shim = Path(__file__).resolve().parents[1] / "integrations" / "hermes" / "mcp" / "read_bridge_mcp.py"
    tool = next(node for node in ast.parse(shim.read_text(encoding="utf-8")).body
                if isinstance(node, ast.FunctionDef) and node.name == "trading_readiness")
    description = ast.get_docstring(tool) or ""
    for field in ("live_entry_possible", "readiness.components", "readiness.blocking",
                  "readiness.unknown"):
        assert f"`{field}`" in description, field


def test_the_view_carries_the_boards_instant_and_is_json_safe():
    data = live_readiness.readiness_data(_status_for_view())
    assert data["as_of"] == NOW and data["env_scope"] == "this_process"
    assert data["checks"] == [{"check": "live_trading_opt_in", "ok": True}]
    json.dumps(data)


def test_the_real_board_on_a_fresh_machine_has_the_view(tmp_path, clean_env):
    """Built from the real board, not a hand-made status: a fresh machine is not ready, and no entry
    can open on it — it reads READ_ONLY and comes up disarmed (decision 10), and the doors refuse on
    both. Until the readiness state (PR5a) this read None, for want of a pool and a recorded gate."""
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    data = live_readiness.readiness_data(status)
    assert data["infrastructure_ready"] is False
    assert data["live_entry_possible"] is False
    assert {"execution_stage:EXECUTION_STAGE_RECORD_MISSING",
            "runtime_control:TRADING_DISARMED"} <= set(data["readiness"]["blocking"])
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


def test_the_arm_row_names_the_halt_level(tmp_path):
    """PR6: the row says which halt holds the arm down, and the readiness input carries it."""
    from runtime.mvp_runtime.control import ACTIVE, HALT_HARD, ControlState, ControlStore

    ControlStore(tmp_path).save(ControlState(mode=ACTIVE, updated_by="op", updated_at=NOW, reason="청산만",
                                             trading_armed=False, halt_level=HALT_HARD))
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    row = next(c for c in status["checks"] if c["check"] == "trading_armed")
    assert row["ok"] is False and "DISARMED (HARD halt: 청산만)" in row["detail"]
    assert "still managed" in row["detail"]
    assert status["readiness_inputs"]["runtime_control"]["halt_level"] == HALT_HARD


def test_the_board_judges_the_pre_order_snapshot_record(tmp_path, clean_env):
    """PR2b: the mainnet snapshot record is a check row. The first version reported it beside the
    checks; the review made the store refuse to append past a damaged line, which refuses every
    live entry, so it is on the board for the entry marks' reason."""
    from runtime.mvp_runtime.crypto import pre_order_gate

    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    assert status["pre_order_snapshots"] == {"readable": True, "appendable": True, "error": None, "count": 0,
                                             "last_created_at": None}
    row = {c["check"]: c for c in status["checks"]}["pre_order_snapshots"]
    assert row["ok"] is True and "none recorded" in row["detail"]
    assert "none recorded" in live_readiness.render_readiness_text(status)

    # A damaged line refuses every live entry (the store will not append past it), so the record is
    # a check, for the entry marks' reason (PR2b review).
    path = pre_order_gate.snapshot_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"pre_order_risk_snapshot_id": "x", "risk_snapshot_sha256": "sha256:0"\n',
                    encoding="utf-8")
    damaged = live_readiness.build_readiness(root=tmp_path, now=NOW)
    assert damaged["pre_order_snapshots"]["readable"] is False
    assert damaged["pre_order_snapshots"]["appendable"] is False
    row = {c["check"]: c for c in damaged["checks"]}["pre_order_snapshots"]
    assert row["ok"] is False and pre_order_gate.RISK_SNAPSHOT_STORE_TAMPERED in row["detail"]
    assert damaged["ready"] is False
    assert "UNREADABLE" in live_readiness.render_readiness_text(damaged)

    # A row that parses but fails its seal fails the row too.
    path.write_text('{"pre_order_risk_snapshot_id": "x", "risk_snapshot_sha256": "sha256:0"}\n',
                    encoding="utf-8")
    edited = live_readiness.build_readiness(root=tmp_path, now=NOW)
    assert {c["check"]: c for c in edited["checks"]}["pre_order_snapshots"]["ok"] is False
    # ...and refuses no entry: the append still takes rows, which the readiness state reads (review of #906).
    assert edited["pre_order_snapshots"]["appendable"] is True


# === the API error breaker row (PR2d-1) ============================================

def _api_row(root):
    return next(c for c in live_readiness.build_readiness(root=root, now=NOW)["checks"]
                if c["check"] == "api_breaker")


def test_the_api_breaker_row_counts_each_class_and_turns_red_once_tripped(tmp_path, clean_env):
    from tests._helpers import make_gate_authorization
    from runtime.mvp_runtime.crypto.live_order import LiveApiErrorBreaker, MAX_CONSECUTIVE_API_ERRORS
    from runtime.mvp_runtime.crypto.live_pnl import LIVE_TRADING_FLAGS, LIVE_TRADING_PROVIDER_ID

    limit = MAX_CONSECUTIVE_API_ERRORS
    row = _api_row(tmp_path)
    assert row["ok"] is True
    assert row["detail"] == f"write 0/{limit}, read 0/{limit} consecutive signed-call failures"

    breaker = LiveApiErrorBreaker(root=tmp_path, authorization=make_gate_authorization(
        flags=LIVE_TRADING_FLAGS, provider_id=LIVE_TRADING_PROVIDER_ID))
    breaker.record_failure(call_class="read", call="open_orders", at=NOW, reason_code="ORDER_TRANSPORT")
    row = _api_row(tmp_path)
    assert row["ok"] is True and f"read 1/{limit}" in row["detail"]

    for _ in range(limit):
        breaker.record_failure(call_class="write", call="submit", at=NOW, reason_code="ORDER_OUTCOME_UNKNOWN")
    row = _api_row(tmp_path)
    assert row["ok"] is False
    assert row["detail"].startswith(f"TRIPPED at {NOW} - write calls failed {limit} times in a row")
    assert "submit ORDER_OUTCOME_UNKNOWN" in row["detail"] and "scripts.clear_api_breaker" in row["detail"]
    assert "has NOT been told yet" in row["detail"]
    assert live_readiness.build_readiness(root=tmp_path, now=NOW)["ready"] is False

    claimed = breaker.claim_notice(at=NOW)
    breaker.mark_told(at="2026-07-23T12:01:00Z", tripped_at=claimed["tripped_at"])
    assert "The operator was told at 2026-07-23T12:01:00Z." in _api_row(tmp_path)["detail"]


def test_a_streak_at_the_limit_with_no_stamp_names_its_class(tmp_path, clean_env):
    import json as _json
    from runtime.mvp_runtime.crypto.live_order import API_BREAKER_FILENAME, MAX_CONSECUTIVE_API_ERRORS
    from runtime.mvp_runtime.crypto.state import venue_state_dir

    path = venue_state_dir(tmp_path) / API_BREAKER_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json.dumps({"write": {"consecutive": 0}, "read": {
        "consecutive": MAX_CONSECUTIVE_API_ERRORS, "last_call": "open_orders",
        "last_reason_code": "ORDER_TRANSPORT"}}), encoding="utf-8")
    row = _api_row(tmp_path)
    assert row["ok"] is False and "read calls failed" in row["detail"]


def test_an_unreadable_api_breaker_turns_the_board_red(tmp_path, clean_env):
    from runtime.mvp_runtime.crypto.live_order import API_BREAKER_FILENAME
    from runtime.mvp_runtime.crypto.state import venue_state_dir

    path = venue_state_dir(tmp_path) / API_BREAKER_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{torn", encoding="utf-8")
    row = _api_row(tmp_path)
    assert row["ok"] is False
    assert "UNREADABLE (LIVE_API_BREAKER_UNREADABLE)" in row["detail"]


# --- the venue contract row (PR4b, Thomas decision 46) -------------------------------------------

def _contract_row(root):
    status = live_readiness.build_readiness(root=root, now=NOW)
    return next(c for c in status["checks"] if c["check"] == "venue_contract"), status


def test_a_usable_pass_covering_the_budget_passes_the_row(tmp_path, clean_env):
    from tests._helpers import record_venue_contract

    _register_budget(tmp_path, symbol_allowlist=("BTCUSDT", "ETHUSDT"))
    record_venue_contract(tmp_path, ["BTCUSDT", "ETHUSDT"], verified_at=NOW)
    row, status = _contract_row(tmp_path)
    assert row["ok"] is True and "PASS at 2026-07-23T12:00:00Z, 0m old - usable" in row["detail"]
    assert status["venue_contract"]["usable"] is True


def test_a_budget_symbol_the_pass_did_not_cover_fails_the_row_and_names_it(tmp_path, clean_env):
    from tests._helpers import record_venue_contract

    _register_budget(tmp_path, symbol_allowlist=("BTCUSDT", "ETHUSDT"))
    record_venue_contract(tmp_path, ["BTCUSDT"], verified_at=NOW)
    row, _ = _contract_row(tmp_path)
    assert row["ok"] is False and "but not for ETHUSDT" in row["detail"]


@pytest.mark.parametrize("state,text", [
    ("fail", "FAIL (failed: position_mode)"),
    ("stale", "STALE"),
    ("damaged", "UNREADABLE (VENUE_CONTRACT_UNREADABLE)"),
    ("missing", "none recorded"),
], ids=["fail", "stale", "damaged", "missing"])
def test_a_contract_the_doors_refuse_on_fails_the_row_and_says_entries_are_refused(tmp_path, clean_env, state, text):
    from runtime.mvp_runtime.crypto import venue_contract as vc
    from tests._helpers import record_venue_contract

    _register_budget(tmp_path)
    if state == "fail":
        record_venue_contract(tmp_path, ["BTCUSDT"], verified_at=NOW, failed=("position_mode",))
    elif state == "stale":
        record_venue_contract(tmp_path, ["BTCUSDT"], verified_at="2026-07-23T05:59:59Z")
    elif state == "damaged":
        record_venue_contract(tmp_path, ["BTCUSDT"], verified_at=NOW)
        vc.contract_path(tmp_path).write_text("{", encoding="utf-8")
    row, status = _contract_row(tmp_path)
    assert row["ok"] is False and status["ready"] is False
    assert text in row["detail"] and "every mainnet entry is refused" in row["detail"]


def test_the_row_and_the_doors_agree_about_coverage_however_a_symbol_is_spelled(tmp_path, clean_env, monkeypatch):
    """One rule (`venue_contract.covers`): the schemas keep both lists upper case today, and the row must
    not start disagreeing with the doors the day one of them does not."""
    from runtime.mvp_runtime.crypto import venue_contract as vc
    from tests._helpers import record_venue_contract

    _register_budget(tmp_path, symbol_allowlist=("BTCUSDT",))
    record_venue_contract(tmp_path, ["BTCUSDT"], verified_at=NOW)
    real = vc.read_verification(tmp_path)
    monkeypatch.setattr(vc, "read_verification", lambda root=None: {**real, "symbols": [" btcusdt "]})
    assert vc.entry_refusal(vc.entry_fact(tmp_path), symbol="BTCUSDT", at=NOW) is None
    row, _ = _contract_row(tmp_path)
    assert row["ok"] is True, row["detail"]


def test_the_row_names_the_reason_the_doors_give_first(tmp_path, clean_env, monkeypatch):
    """Review of #903: a stale record under another contract version reads as the doors refuse it —
    the version first, in the judge's order, not STALE."""
    from runtime.mvp_runtime.crypto import venue_contract as vc
    from tests._helpers import record_venue_contract

    _register_budget(tmp_path)
    record_venue_contract(tmp_path, ["BTCUSDT"], verified_at="2026-07-23T05:59:59Z")
    monkeypatch.setattr(vc, "CONTRACT_VERSION", "binance_futures_contract.v9")     # the deploy that bumps it
    row, _ = _contract_row(tmp_path)
    assert row["ok"] is False and "- other contract version -" in row["detail"] and "STALE" not in row["detail"]
    assert vc.entry_refusal(vc.entry_fact(tmp_path), symbol=None, at=NOW)["reason_code"] == vc.ENTRY_CONTRACT_VERSION


def test_an_unexpected_reader_exception_fails_the_row_and_never_the_board(tmp_path, clean_env, monkeypatch):
    """Review of #903: the doors refuse on any exception the reader raises (UNREADABLE); the board names
    it the same way instead of raising."""
    from runtime.mvp_runtime.crypto import venue_contract as vc

    def boom(root=None):
        raise PermissionError("scripted: Path.is_file on 3.12")

    _register_budget(tmp_path)
    monkeypatch.setattr(vc, "read_verification", boom)
    row, status = _contract_row(tmp_path)
    assert row["ok"] is False and "UNREADABLE (PermissionError)" in row["detail"]
    assert status["ready"] is False
    assert vc.entry_refusal(vc.entry_fact(tmp_path), symbol="BTCUSDT", at=NOW)["reason_code"] \
        == vc.ENTRY_CONTRACT_UNREADABLE


# --- the manual kill switch row (decision 50) ---------------------------------------

def _kill_row(text):
    return next(line for line in text.splitlines() if line.startswith("[") and "manual_kill_switch" in line)


@pytest.mark.parametrize("engaged", [False, True])
def test_a_console_shows_the_manual_kill_switch_the_trading_process_recorded(tmp_path, clean_env, engaged):
    """Decision 50: the row read PASS "clear" off a console with no live-trading environment. It now
    shows the trading process's own record of the switch, and says it is the secondary control."""
    _write_cycle(tmp_path, status="HELD", created_at="2026-07-23T11:55:00Z",
                 live_gate={"confirmation_present": True, "manual_kill_switch": engaged})
    status = live_readiness.build_readiness(root=tmp_path, now=NOW)
    row = _kill_row(live_readiness.render_readiness_text(status))
    seen = "as the trading process recorded it at 2026-07-23T11:55:00Z"
    if engaged:
        assert row.startswith("[FAIL]") and "MVP_LIVE_MANUAL_KILL_SWITCH is engaged" in row, row
    else:
        assert row.startswith("[PASS]") and "clear, " in row, row
    assert seen in row and live_readiness.MANUAL_KILL_SECONDARY in row
    # Rendering only: the check record every machine consumer reads is this process's, untouched.
    check = next(c for c in status["checks"] if c["check"] == "manual_kill_switch")
    assert (check["ok"], check["detail"]) == (True, "clear")


@pytest.mark.parametrize("created_at,live_gate", [
    ("2026-07-23T11:55:00Z", None),                                                     # not stamped
    ("2026-07-23T08:00:00Z", {"confirmation_present": True, "manual_kill_switch": False}),  # stale
])
def test_a_console_without_a_usable_record_says_it_cannot_see_the_switch(tmp_path, clean_env, created_at,
                                                                      live_gate):
    _write_cycle(tmp_path, status="HELD", created_at=created_at, live_gate=live_gate)
    row = _kill_row(live_readiness.render_readiness_text(live_readiness.build_readiness(root=tmp_path, now=NOW)))
    assert row.startswith(f"[{live_readiness.OUT_OF_SCOPE_MARK}]") and live_readiness.OUT_OF_SCOPE_DETAIL in row
    assert "clear" not in row and live_readiness.MANUAL_KILL_SECONDARY in row


def test_a_console_whose_trading_process_recorded_a_closed_gate_points_at_no_missing_banner(tmp_path,
                                                                                         clean_env):
    """Review of PR6d: a fresh record of a CLOSED gate takes the banner down, and a gate that never
    opened stamped no switches, so the n/a row pointed at a banner that was not there."""
    _write_cycle(tmp_path, status=live_route.ROUTE_DISABLED, created_at="2026-07-23T11:55:00Z")
    text = live_readiness.render_readiness_text(live_readiness.build_readiness(root=tmp_path, now=NOW))
    row = _kill_row(text)
    assert "CANNOT SEE THE LIVE-TRADING ENVIRONMENT" not in text
    assert row.startswith(f"[{live_readiness.OUT_OF_SCOPE_MARK}]") and "banner" not in row
    assert "recorded its gate closed" in row and live_readiness.MANUAL_KILL_SECONDARY in row


def test_the_trading_process_shows_its_own_switch_as_the_secondary_control():
    opted = {"checks": [{"check": "live_trading_opt_in", "ok": True, "detail": "real"}]}
    for ok, detail, mark in ((True, "clear", "PASS"), (False, "MVP_LIVE_MANUAL_KILL_SWITCH is engaged", "FAIL")):
        got = live_readiness._manual_kill_row({"check": "manual_kill_switch", "ok": ok, "detail": detail}, opted)
        assert got == (mark, f"{detail} ({live_readiness.MANUAL_KILL_SECONDARY})")
