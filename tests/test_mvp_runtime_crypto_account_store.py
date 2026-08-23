"""The funds snapshot: written by the lane with the keys, read by the door without them.

Thomas asked the assistant door for three things — position, funds, return. Two shipped in
#387; funds did not, because the balance is behind a signed venue call and the read door holds
no venue credential. Giving it one is not a small change on this host: there is one Binance key
and order authority derives from it, so the door the assistant talks to would hold a key that
can trade.

So the scheduler reads and writes; the door renders the file. What these pin is the part that
makes that safe to rely on — that the door never reaches the venue, that a bad minute at the
venue cannot destroy a good balance, and that an old number is shown as old rather than shown
as now.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.crypto import account_store
from runtime.mvp_runtime.crypto import live_readiness
from runtime.mvp_runtime.errors import ToolError

NOW = "2026-08-23T12:00:00Z"


def _record(**over):
    """A stored snapshot in the shape `account.snapshot_record` produces."""
    base = {
        "record_type": account_store.RECORD_TYPE,
        "tool_id": "crypto.account", "operation": "account_snapshot",
        "feed_id": "binance_futures_account", "configured": True,
        "read_only": True, "external_action": False, "network_egress": True,
        "asset": "USDT",
        "wallet_balance": 1234.5678, "margin_balance": 1230.0,
        "available_balance": 1100.25, "unrealized_pnl": -4.5678,
        "open_position_count": 3,
        "realized_windows": {"1d": 2.5, "7d": -11.0, "30d": None},
        "collected_at": "2026-08-23T11:58:00Z", "as_of": "2026-08-23T11:58:00Z",
        "written_at": "2026-08-23T11:58:01Z", "warnings": [],
    }
    base.update(over)
    return base


def _store(tmp_path, body):
    path = account_store.snapshot_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


# --- the door never reaches the venue ----------------------------------------

def test_the_board_reads_a_file_and_never_the_venue(tmp_path, monkeypatch):
    """The property the whole design exists for. If `load_funds_board` can ever call
    `read_account`, the door needs a credential and the credential-plane separation is over."""
    def _explode(*a, **k):
        raise AssertionError("the read door must never call the venue")

    monkeypatch.setattr("runtime.mvp_runtime.crypto.account.read_account", _explode)
    monkeypatch.setattr("runtime.mvp_runtime.crypto.account.select_account_feed", _explode)
    _store(tmp_path, _record())
    text = account_store.load_funds_board(now=NOW, root=tmp_path)
    assert "1234.5678 USDT" in text


def test_the_stored_snapshot_carries_no_credential(tmp_path):
    """`snapshot_record` is metadata-only by construction; this pins that the store does not
    add anything on the way through. The file is readable by the door's uid — a key in it
    would be a key in the assistant's reach."""
    _store(tmp_path, _record())
    raw = account_store.snapshot_path(tmp_path).read_text(encoding="utf-8").lower()
    for forbidden in ("secret", "api_key", "apikey", "signature", "token", "password"):
        assert forbidden not in raw


# --- refusing beats rendering zeros ------------------------------------------

def test_a_missing_snapshot_refuses_rather_than_rendering_zeros(tmp_path):
    """A board of zeros is indistinguishable from an emptied account. That is the one reading
    this must never produce by accident."""
    with pytest.raises(ToolError) as exc:
        account_store.load_funds_board(now=NOW, root=tmp_path)
    assert exc.value.reason_code == account_store.ACCOUNT_SNAPSHOT_MISSING
    assert "scheduler" in str(exc.value)


def test_a_damaged_snapshot_is_told_apart_from_a_missing_one(tmp_path):
    """Different causes, different operator actions: one waits for the next fire, the other
    means the writer is producing garbage."""
    path = account_store.snapshot_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        account_store.load_funds_board(now=NOW, root=tmp_path)
    assert exc.value.reason_code == account_store.ACCOUNT_SNAPSHOT_UNREADABLE


def test_an_unconfigured_feed_is_told_apart_from_a_degraded_one(tmp_path):
    """"never asked" and "asked and it failed" are different facts about the same blank."""
    _store(tmp_path, _record(configured=False))
    with pytest.raises(ToolError) as exc:
        account_store.load_funds_board(now=NOW, root=tmp_path)
    assert exc.value.reason_code == account_store.ACCOUNT_FEED_NOT_CONFIGURED

    _store(tmp_path, _record(degraded=True, error_reason_code="TOOL_TRANSPORT"))
    with pytest.raises(ToolError) as exc:
        account_store.load_funds_board(now=NOW, root=tmp_path)
    assert exc.value.reason_code == account_store.ACCOUNT_SNAPSHOT_DEGRADED
    assert "TOOL_TRANSPORT" in str(exc.value)


# --- old is shown as old, not hidden and not as now ---------------------------

def test_a_fresh_snapshot_carries_its_age_without_a_banner(tmp_path):
    _store(tmp_path, _record())
    text = account_store.load_funds_board(now=NOW, root=tmp_path)
    assert "as of 2026-08-23T11:58:00Z" in text
    assert "2m ago" in text
    assert "STALE" not in text


def test_a_stale_snapshot_still_shows_the_figure_and_says_it_is_stale(tmp_path):
    """§7 of the switch proposal asked for the `recorded_gate` treatment: render the value and
    say plainly it is not a statement about now. Hiding it would make a stale board and a
    broken one look the same, and only one of those is an emergency."""
    _store(tmp_path, _record(as_of="2026-08-23T05:00:00Z"))
    text = account_store.load_funds_board(now=NOW, root=tmp_path)
    assert "!! STALE" in text
    assert "1234.5678 USDT" in text          # the number is still there
    assert "420m ago" in text


def test_the_stale_window_is_the_one_the_readiness_board_already_uses(tmp_path):
    """An operator should not have to hold two different ideas of "too old" for two boards
    read in the same minute."""
    assert account_store.STALE_AFTER_SECONDS == live_readiness.RECORDED_GATE_STALE_AFTER_SECONDS


# --- what the board must not assert ------------------------------------------

def test_the_board_never_claims_there_are_no_positions(tmp_path):
    """The stored record carries `open_position_count`, not the positions. Reviving an
    `AccountSnapshot` from it and reusing `render_account_text` would print "positions: none"
    for an account holding three — a false statement about live exposure."""
    _store(tmp_path, _record(open_position_count=3))
    text = account_store.load_funds_board(now=NOW, root=tmp_path)
    assert "positions : 3 open" in text
    assert "crypto_status" in text           # where the detail actually lives


def test_a_withheld_income_window_is_not_rendered_as_zero(tmp_path):
    """`account` renders "n/a (income history withheld)" when the venue's income page filled.
    A bare 0.00 there asserts "no loss today" to the very breaker that measures the day."""
    _store(tmp_path, _record(realized_windows={"1d": None, "7d": -11.0}))
    text = account_store.load_funds_board(now=NOW, root=tmp_path)
    assert "realized 1d: n/a (income history withheld)" in text
    assert "realized 7d: -11.0 USDT" in text


def test_warnings_ride_along(tmp_path):
    _store(tmp_path, _record(warnings=["realized P&L unavailable (income page full)"]))
    assert "income page full" in account_store.load_funds_board(now=NOW, root=tmp_path)


# --- the writer: never raise, never destroy a good balance -------------------

def test_the_refresh_is_skipped_while_the_mark_is_fresh(tmp_path):
    assert account_store.is_due(None, NOW) is True
    assert account_store.is_due("2026-08-23T11:58:00Z", NOW) is False   # 2m
    assert account_store.is_due("2026-08-23T11:40:00Z", NOW) is True    # 20m
    assert account_store.is_due("not a timestamp", NOW) is True


def test_a_degraded_read_keeps_the_previous_snapshot(tmp_path, monkeypatch):
    """`orderbook_store`'s reason, and it matters more here: fold the data and the attempt into
    one file and a single timeout overwrites a good balance with a degraded record — the answer
    gets worse exactly when the venue is having a bad minute."""
    good = _record()
    _store(tmp_path, good)
    monkeypatch.setattr(
        "runtime.mvp_runtime.crypto.account.read_account",
        lambda **k: (None, {"configured": True, "degraded": True,
                            "error_reason_code": "TOOL_TRANSPORT"}),
    )
    line = account_store.refresh_snapshot(now=NOW, root=tmp_path)
    assert "degraded" in line and "kept the previous one" in line
    assert account_store.read_snapshot(tmp_path)["wallet_balance"] == good["wallet_balance"]
    # ...and the attempt was still recorded, or a refusing venue becomes one ask per fire.
    assert account_store.read_refresh_mark(tmp_path) == NOW


def test_a_raising_venue_never_takes_down_the_money_fire(tmp_path, monkeypatch):
    """This runs inside a RISK-kind fire. An exception here would stop the loop that manages
    real positions in order to fail at bookkeeping."""
    def _boom(**k):
        raise RuntimeError("venue on fire")

    monkeypatch.setattr("runtime.mvp_runtime.crypto.account.read_account", _boom)
    line = account_store.refresh_snapshot(now=NOW, root=tmp_path)
    assert "read failed" in line and "RuntimeError" in line


def test_a_successful_read_is_stored_with_its_as_of(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "runtime.mvp_runtime.crypto.account.read_account",
        lambda **k: (object(), _record(as_of=None, written_at=None)),
    )
    assert "refreshed" in account_store.refresh_snapshot(now=NOW, root=tmp_path)
    stored = account_store.read_snapshot(tmp_path)
    assert stored["as_of"] == "2026-08-23T11:58:00Z"       # the venue's instant, not ours
    assert stored["written_at"] == NOW
    assert stored["record_type"] == account_store.RECORD_TYPE


def test_the_refresh_asks_the_venue_with_a_bounded_timeout(tmp_path, monkeypatch):
    """The library default is 10s per call and this makes two, on a fire that is never
    deferred. Twenty seconds of venue latency pushed onto every other kind due in the same
    pass is the shape of delay `MAINTENANCE_PASS_BUDGET_SECONDS` exists to bound."""
    seen = {}

    def _capture(**kwargs):
        seen.update(kwargs)
        return None, {"configured": False}

    monkeypatch.setattr("runtime.mvp_runtime.crypto.account.read_account", _capture)
    account_store.refresh_snapshot(now=NOW, root=tmp_path)
    assert seen["timeout_seconds"] <= 5
