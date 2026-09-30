"""PROTECTION_UNKNOWN escalation (Thomas 2026-09-30, PROTECTION_UNKNOWN_ESCALATION_V0.1 D1–D4).

A live position whose protective legs cannot be read used to be held and recorded, and nothing else.
These tests pin the clock (U0 → U1 at 30 minutes, at once for a record with no bracket id → U2 at 60),
the edge-triggered message, the entry hold, the runtime's raise-only HARD halt, and the failure
directions. They also pin the one departure from the document: U1 holds entries rather than halting
the fan-out, because a fan-out halt would stop every other position from being managed."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from runtime.mvp_runtime.control import HALT_HARD, HALT_SOFT, ControlStore
from runtime.mvp_runtime.crypto import live_leg, live_route
from runtime.mvp_runtime.crypto import protection_watch as pw

from tests.test_mvp_runtime_crypto_live_route import (
    NOW, SYMBOL, _Adapter, _Ledger, _position, _settle, _snapshot, _Store,
)

T0 = datetime(2026, 7, 28, tzinfo=timezone.utc)


def _at(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _unknown_legs(*, ids_missing: bool = False) -> dict[str, Any]:
    error = live_leg.BRACKET_IDS_MISSING if ids_missing else "ORDER_TRANSPORT"
    return {"status": live_leg.PROTECTION_UNKNOWN,
            "legs": [{"leg": "stop_client_order_id", "error": error},
                     {"leg": "take_profit_client_order_id", "error": error}]}


def _observe(root, minutes, **kw):
    return pw.observe_unknown(_position(), _unknown_legs(**kw), now=_at(minutes), root=root,
                              ids_missing_code=live_leg.BRACKET_IDS_MISSING)


# --- the clock ----------------------------------------------------------------------------------

def test_the_clock_runs_on_wall_time_from_the_first_unknown_pass(tmp_path):
    assert _observe(tmp_path, 0)["level"] == pw.LEVEL_OBSERVE
    assert _observe(tmp_path, 15)["level"] == pw.LEVEL_OBSERVE
    assert _observe(tmp_path, 30)["level"] == pw.LEVEL_NOTIFY
    assert _observe(tmp_path, 60)["level"] == pw.LEVEL_HARD


def test_a_skipped_pass_does_not_reset_the_clock(tmp_path):
    """Wall time, not a pass count: two observations 70 minutes apart are at U2."""
    _observe(tmp_path, 0)
    assert _observe(tmp_path, 70)["level"] == pw.LEVEL_HARD


def test_a_record_with_no_bracket_id_is_u1_at_once(tmp_path):
    """No retry can fix a record that names no bracket id: someone has to look."""
    watch = _observe(tmp_path, 0, ids_missing=True)
    assert watch["level"] == pw.LEVEL_NOTIFY
    assert watch["notify"] is True
    assert pw.PERSISTING in watch["reason_codes"]


def test_the_message_is_edge_triggered_once_per_level(tmp_path):
    assert _observe(tmp_path, 0)["notify"] is False
    assert _observe(tmp_path, 30)["notify"] is True
    assert _observe(tmp_path, 45)["notify"] is False
    u2 = _observe(tmp_path, 60)
    assert (u2["notify"], u2["hard"]) == (True, True)
    assert _observe(tmp_path, 75)["notify"] is False


def test_the_hard_halt_is_owed_once_per_episode(tmp_path):
    _observe(tmp_path, 0)
    assert _observe(tmp_path, 60)["hard"] is True
    pw.mark_hard_applied(_position(), now=_at(60), root=tmp_path)
    assert _observe(tmp_path, 75)["hard"] is False


def test_a_definite_read_ends_the_episode(tmp_path):
    _observe(tmp_path, 0)
    _observe(tmp_path, 30)
    assert pw.clear(_position(), root=tmp_path) is True
    assert pw.read_watch(tmp_path) == {}
    assert _observe(tmp_path, 31)["level"] == pw.LEVEL_OBSERVE  # a new episode starts from zero


def test_a_position_that_left_the_book_is_pruned(tmp_path):
    _observe(tmp_path, 0)
    assert pw.prune({"another"}, root=tmp_path) == ["live-1"]
    assert pw.read_watch(tmp_path) == {}


def test_nothing_is_written_when_nothing_is_watched(tmp_path):
    """The live leg prunes and clears on every pass; with no episode that must touch no file."""
    assert pw.prune(set(), root=tmp_path) == []
    assert pw.clear(_position(), root=tmp_path) is False
    assert not (tmp_path / ".runtime_governance_state").exists()


# --- failure directions -------------------------------------------------------------------------

def test_an_unreadable_store_reads_every_unknown_position_as_u1_never_u2(tmp_path):
    _observe(tmp_path, 0)
    pw.watch_path(tmp_path).write_text("{not json", encoding="utf-8")
    watch = _observe(tmp_path, 90)
    assert watch["level"] == pw.LEVEL_NOTIFY
    assert watch["hard"] is False
    assert pw.WATCH_UNREADABLE in watch["reason_codes"]
    assert pw.entries_blocking(root=tmp_path, now=_at(90))["reason_code"] == pw.WATCH_UNREADABLE


def test_a_failed_write_still_holds_at_u1(tmp_path, monkeypatch):
    """A clock that cannot be written would restart at U0 every pass and never escalate."""
    def _refuse(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(pw, "_write", _refuse)
    watch = _observe(tmp_path, 0)
    assert watch["level"] == pw.LEVEL_NOTIFY
    assert pw.WATCH_UNRECORDED in watch["reason_codes"]


def test_only_u1_and_above_hold_entries(tmp_path):
    _observe(tmp_path, 0)
    assert pw.entries_blocking(root=tmp_path, now=_at(10)) is None
    _observe(tmp_path, 30)
    held = pw.entries_blocking(root=tmp_path, now=_at(31))
    assert held["reason_code"] == pw.PERSISTING
    assert held["positions"][0]["position_id"] == "live-1"


def test_the_hold_runs_on_the_wall_clock_not_the_last_reading(tmp_path):
    """A context that runs before the watched symbol's own context in a pass reads a level written
    a pass ago. The hold recomputes it, so it holds from minute 30 whatever the context order."""
    _observe(tmp_path, 0)  # stored at U0
    assert pw.read_watch(tmp_path)["live-1"]["level"] == pw.LEVEL_OBSERVE
    held = pw.entries_blocking(root=tmp_path, now=_at(40))
    assert held["positions"][0]["level"] == pw.LEVEL_NOTIFY


# --- the route ----------------------------------------------------------------------------------

def _seed(root, *, minutes_ago: float, kind: str = pw.KIND_READ_FAILED, **extra):
    since = (datetime.fromisoformat(NOW.replace("Z", "+00:00")) - timedelta(minutes=minutes_ago))
    entry = {"unknown_since": since.strftime("%Y-%m-%dT%H:%M:%SZ"), "kind": kind, "symbol": SYMBOL,
             "level": pw.LEVEL_OBSERVE, "passes": 1, **extra}
    path = pw.watch_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": pw.PROTECTION_WATCH_VERSION, "entries": {"live-1": entry}}))


def _sent(monkeypatch) -> list[list[str]]:
    sent: list[list[str]] = []
    monkeypatch.setattr(live_route, "_send_operator_text",
                        lambda record, lines, **kw: sent.append(list(lines)) or True)
    return sent


def test_an_unknown_read_is_held_and_starts_the_clock_without_a_halt(tmp_path, monkeypatch):
    sent = _sent(monkeypatch)
    record = _settle(_position(), adapter=_Adapter(fetch_raises="ORDER_TRANSPORT"), root=tmp_path)
    assert record["live_protection_watch"]["level"] == pw.LEVEL_OBSERVE
    assert record["halt"] is False and record["live_settled"] is None
    assert sent == []


def test_u1_messages_the_operator_and_does_not_halt_the_fan_out(tmp_path, monkeypatch):
    """The departure from the document, pinned: a fan-out halt would skip every other position's
    settlement and protection for as long as the read failed. U1 holds entries instead."""
    sent = _sent(monkeypatch)
    record = _settle(_position(stop_client_order_id=None, take_profit_client_order_id=None),
                     adapter=_Adapter(), root=tmp_path)
    assert record["live_protection_watch"]["level"] == pw.LEVEL_NOTIFY
    assert record["halt"] is False
    assert pw.PERSISTING in record["live_reason_codes"]
    assert len(sent) == 1 and "new live entries held" in sent[0][0]


def test_u2_tightens_the_control_state_to_hard_once(tmp_path, monkeypatch):
    sent = _sent(monkeypatch)
    _seed(tmp_path, minutes_ago=61, notified_level=pw.LEVEL_NOTIFY)
    record = _settle(_position(), adapter=_Adapter(fetch_raises="ORDER_TRANSPORT"), root=tmp_path)
    state = ControlStore.default(tmp_path).load()
    assert state.halt_level == HALT_HARD
    assert state.updated_by == pw.ACTOR
    assert pw.HARD_HALT in record["live_reason_codes"]
    assert record["live_protection_hard_halt"]["changed"] is True
    assert len(sent) == 1 and "HARD halt placed" in sent[0][0]
    # The episode is marked: the next pass neither re-applies nor messages again.
    calls: list[Any] = []
    monkeypatch.setattr(live_route, "apply_command", lambda *a, **k: calls.append(a) or {})
    _settle(_position(), adapter=_Adapter(fetch_raises="ORDER_TRANSPORT"), root=tmp_path)
    assert calls == [] and len(sent) == 1


def test_the_hard_halt_only_ever_tightens(tmp_path, monkeypatch):
    """Raise-only (decision 47): a SOFT halt in effect goes to HARD, and nothing here loosens it."""
    from runtime.mvp_runtime import control

    _sent(monkeypatch)
    store = ControlStore.default(tmp_path)
    control.apply_command(store, control.CMD_HALT_TRADING, actor="thomas", now=NOW, halt_level=HALT_SOFT)
    _seed(tmp_path, minutes_ago=61, notified_level=pw.LEVEL_NOTIFY)
    _settle(_position(), adapter=_Adapter(fetch_raises="ORDER_TRANSPORT"), root=tmp_path)
    assert store.load().halt_level == HALT_HARD


def test_a_definite_read_clears_the_watch(tmp_path, monkeypatch):
    _sent(monkeypatch)
    _seed(tmp_path, minutes_ago=40, level=pw.LEVEL_NOTIFY)
    _settle(_position(), adapter=_Adapter(orders={"sl-1": {"status": "NEW"}, "tp-1": {"status": "NEW"}}),
            root=tmp_path)
    assert pw.read_watch(tmp_path) == {}


def test_a_watched_position_at_u1_holds_every_new_entry_and_positions_are_still_managed(
    tmp_path, monkeypatch,
):
    _seed(tmp_path, minutes_ago=40, level=pw.LEVEL_NOTIFY)
    monkeypatch.setenv("MVP_LIVE_TRADING", "real")
    monkeypatch.setattr(live_route, "read_account", lambda **kw: (_snapshot(), {}))
    monkeypatch.setattr(live_route, "list_open_live_positions",
                        lambda root: [{"symbol": SYMBOL, "position_id": "live-1", "status": "OPEN"}])
    monkeypatch.setattr(live_route, "reconcile_positions",
                        lambda local, snapshot, now: {"status": "RECONCILED", "books": {}})
    managed: list[str] = []
    monkeypatch.setattr(live_route, "_settle_or_protect",
                        lambda record, position, **kw: managed.append(position["position_id"]))
    planned: list[Any] = []
    monkeypatch.setattr(live_route, "plan_live_entry", lambda plan, **kw: planned.append(plan) or {})
    out = live_route.run_live_leg(
        live_routable_strategy_ids={"S1"}, route=None, feature_row={"timestamp": NOW},
        verdict={"allow_new_position": True}, symbol=SYMBOL, collector=object(), now=NOW,
        root=tmp_path,
    )
    assert managed == ["live-1"]
    assert out["live_route_status"] == live_route.ROUTE_BLOCKED
    assert pw.PERSISTING in out["live_reason_codes"]
    assert planned == [], "no entry is planned while a watched position is at U1"
