"""The venue axis (PR1d-0, 2026-09-16).

Until now "live" meant one venue, so the execution state — the daily order counter, the position
book, the outcome ledger, the bracket breaker, the registered budget — sat in one unqualified
directory. The signed testnet path (PR1d-1, Thomas decision 2) has to place, reconcile and settle
an order without any of it being read back as live. What is pinned here: mainnet keeps every path
it had (so nothing on the machine moves), another venue gets its own subtree, and an unknown venue
refuses rather than falling back to the live one."""

from __future__ import annotations

import inspect
import json

import pytest

from runtime.mvp_runtime.crypto import live_budget, live_order, live_pnl, live_position
from runtime.mvp_runtime.crypto.state import (
    VENUE_MAINNET,
    VENUE_TESTNET,
    VENUE_UNKNOWN,
    state_dir,
    venue_state_dir,
)
from runtime.mvp_runtime.errors import ToolError

# Every path a live order's state reaches, with the helper that resolves it.
_PATHS = {
    "counter": lambda root, venue: venue_state_dir(root, venue=venue) / live_order.COUNTER_FILENAME,
    "positions": lambda root, venue: live_position.live_positions_dir(root, venue=venue),
    "position_file": lambda root, venue: live_position.live_position_path("BTCUSDT", root, venue=venue),
    "outcomes": lambda root, venue: venue_state_dir(root, venue=venue) / live_pnl.LIVE_OUTCOMES_FILENAME,
    "breaker": lambda root, venue: venue_state_dir(root, venue=venue) / live_order.BRACKET_BREAKER_FILENAME,
    "budget": lambda root, venue: live_budget.budget_path(root, venue=venue),
}


def _authorized(monkeypatch):
    """The live-trading authorization these stores re-check at every write. The opt-in has to be
    set while the store runs, because `assert_authorization` re-reads it (that is the point)."""
    from runtime.mvp_runtime import safety_gate

    monkeypatch.setenv(live_pnl.LIVE_TRADING_ENV, live_pnl.REAL_LIVE_TRADING)
    return safety_gate.env_only_authorization(
        flags=live_pnl.LIVE_TRADING_FLAGS, provider_id=live_pnl.LIVE_TRADING_PROVIDER_ID,
        env_var=live_pnl.LIVE_TRADING_ENV, opt_in_value=live_pnl.REAL_LIVE_TRADING,
    )


@pytest.mark.parametrize("name", sorted(_PATHS))
def test_mainnet_keeps_the_path_it_always_had(tmp_path, name):
    """No migration: a record written before the venue axis existed is read from the same place
    by the same code, and a rollback to the previous image finds it there."""
    resolved = _PATHS[name](tmp_path, VENUE_MAINNET)
    assert resolved.is_relative_to(state_dir(tmp_path))
    assert "venues" not in resolved.relative_to(state_dir(tmp_path)).parts


@pytest.mark.parametrize("name", sorted(_PATHS))
def test_another_venue_gets_its_own_subtree(tmp_path, name):
    mainnet, testnet = _PATHS[name](tmp_path, VENUE_MAINNET), _PATHS[name](tmp_path, VENUE_TESTNET)
    assert mainnet != testnet
    assert testnet.is_relative_to(state_dir(tmp_path) / "venues" / VENUE_TESTNET)


def test_an_unknown_venue_refuses_rather_than_falling_back_to_live(tmp_path):
    """A typo that resolved to mainnet would write testnet state into the live book — the one
    outcome this axis exists to prevent."""
    with pytest.raises(ToolError) as exc:
        venue_state_dir(tmp_path, venue="binance_futuros")
    assert exc.value.reason_code == VENUE_UNKNOWN
    with pytest.raises(ToolError):
        live_position.live_position_path("BTCUSDT", tmp_path, venue="")


def test_the_default_is_mainnet_everywhere_so_an_existing_caller_moves_nothing():
    """Every venue argument defaults to mainnet: the callers written before this change keep
    reading and writing exactly what they did."""
    defaults = {
        "count_today": inspect.signature(live_order.count_today).parameters["venue"],
        "read_bracket_failures": inspect.signature(live_order.read_bracket_failures).parameters["venue"],
        "live_positions_dir": inspect.signature(live_position.live_positions_dir).parameters["venue"],
        "live_position_path": inspect.signature(live_position.live_position_path).parameters["venue"],
        "load_open_live_position": inspect.signature(live_position.load_open_live_position).parameters["venue"],
        "list_open_live_positions": inspect.signature(live_position.list_open_live_positions).parameters["venue"],
        "read_live_outcomes_raw": inspect.signature(live_pnl.read_live_outcomes_raw).parameters["venue"],
        "budget_path": inspect.signature(live_budget.budget_path).parameters["venue"],
    }
    for name, param in defaults.items():
        assert param.default == VENUE_MAINNET, name
        assert param.kind is inspect.Parameter.KEYWORD_ONLY, name
    for store in (live_order.LiveOrderCounter, live_order.LiveBracketFailureBreaker,
                  live_position.RealLivePositionStore, live_pnl.RealLiveLedger):
        assert inspect.signature(store.__init__).parameters["venue"].default == VENUE_MAINNET, store.__name__


def test_two_venues_count_their_own_orders(tmp_path, monkeypatch):
    """The failure this axis exists to prevent, at the counter: a testnet order must not spend
    the live daily cap, and a live order must not be hidden by one."""
    auth = _authorized(monkeypatch)
    live = live_order.LiveOrderCounter(root=tmp_path, authorization=auth)
    testnet = live_order.LiveOrderCounter(root=tmp_path, authorization=auth, venue=VENUE_TESTNET)
    for _ in range(3):
        testnet.record_submission(day="2026-09-16")
    assert live_order.count_today(tmp_path, day="2026-09-16") == 0
    assert live_order.count_today(tmp_path, day="2026-09-16", venue=VENUE_TESTNET) == 3
    live.record_submission(day="2026-09-16")
    assert live_order.count_today(tmp_path, day="2026-09-16") == 1
    assert live_order.count_today(tmp_path, day="2026-09-16", venue=VENUE_TESTNET) == 3


def test_two_venues_keep_their_own_books_and_breakers(tmp_path, monkeypatch):
    auth = _authorized(monkeypatch)
    book = live_position.RealLivePositionStore(root=tmp_path, authorization=auth, venue=VENUE_TESTNET)
    record = {"symbol": "BTCUSDT", "position_id": "p1", "status": "OPEN", "stage": live_position.LIVE_STAGE}
    book.save_position(record)
    assert live_position.list_open_live_positions(tmp_path) == []
    assert [p["position_id"] for p in live_position.list_open_live_positions(tmp_path, venue=VENUE_TESTNET)] == ["p1"]

    # The live breaker already carries a streak: a venue that reads it as its own baseline would
    # inherit it — and when the live one is clean, would reset its own count on every failure, so
    # it could never trip. Review of #876 found exactly that (the write was per-venue, the read
    # was not), which an empty-mainnet test cannot see.
    live_breaker = live_order.LiveBracketFailureBreaker(root=tmp_path, authorization=auth)
    for i in range(2):
        live_breaker.record_failure(symbol="BTCUSDT", status="REJECTED", reason_codes=["X"],
                                    at=f"2026-09-16T00:0{i}:00Z")
    breaker = live_order.LiveBracketFailureBreaker(root=tmp_path, authorization=auth, venue=VENUE_TESTNET)
    breaker.record_failure(symbol="BTCUSDT", status="REJECTED", reason_codes=["X"], at="2026-09-16T00:10:00Z")
    assert live_order.read_bracket_failures(tmp_path)["consecutive"] == 2, "the live streak moved"
    assert live_order.read_bracket_failures(tmp_path, venue=VENUE_TESTNET)["consecutive"] == 1
    assert live_order.bracket_breaker_status(tmp_path, venue=VENUE_TESTNET)["consecutive"] == 1
    # And the other way: a second live failure counts 3, not 1.
    live_breaker.record_failure(symbol="BTCUSDT", status="REJECTED", reason_codes=["X"],
                                at="2026-09-16T00:20:00Z")
    assert live_order.read_bracket_failures(tmp_path)["consecutive"] == 3


def test_two_venues_keep_their_own_outcome_ledgers(tmp_path, monkeypatch):
    """The dedupe rides the verified read, so a ledger that reads another venue's file both
    misses its own duplicates — one duplicate makes EVERY verified read of that history raise —
    and silently drops a settlement whose id the other venue happens to hold. Review of #876."""
    auth = _authorized(monkeypatch)
    from runtime.read_only_kernel import integrity

    body = {"settlement_id": "s1", "outcome_id": "o1", "symbol": "BTCUSDT", "stage": "live",
            "provenance": "mvp_live_kernel", "net_r": 0.0, "closed_at": "2026-09-16T00:00:00Z"}
    row = {**body, "record_sha256": integrity.sha256_record(body)}
    testnet = live_pnl.RealLiveLedger(root=tmp_path, authorization=auth, venue=VENUE_TESTNET)
    assert testnet.append_outcome(row) is True
    assert testnet.append_outcome(row) is False, "the second write was not seen as a duplicate"
    assert len(live_pnl.read_live_outcomes_raw(tmp_path, venue=VENUE_TESTNET)) == 1
    assert live_pnl.read_live_outcomes_raw(tmp_path) == [], "a testnet settlement reached the live ledger"
    # The same settlement id on the live ledger is a different history, not a duplicate of this one.
    live = live_pnl.RealLiveLedger(root=tmp_path, authorization=auth)
    assert live.append_outcome(row) is True
    assert len(live_pnl.read_live_outcomes_raw(tmp_path)) == 1


def test_a_venue_budget_is_read_only_by_that_venue(tmp_path):
    record = live_budget.build_live_trading_budget_record(
        caps=dict(max_order_notional_usdt=60.0, absolute_max_notional_usdt=200.0,
                  max_daily_order_count=2, max_open_notional_usdt=120.0, daily_loss_limit_usdt=20.0),
        symbol_allowlist=["BTCUSDT"], registered_by="thomas", registered_at="2026-09-16T00:00:00Z",
    )
    path = live_budget.budget_path(tmp_path, venue=VENUE_TESTNET)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")
    assert live_budget.read_registered_budget(tmp_path) is None, "a testnet budget backed a live order"
