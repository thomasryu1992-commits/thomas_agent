"""PR2a — the durable facts a live entry spends before it is sent.

Two stores, both behind the live-trading switch:

- **the day's order slot** (``LiveOrderCounter.reserve_submission``): the guard judges a count read
  earlier in the leg, and the scheduler's live leg and an operator's probe spend the same cap from
  two processes. The reservation is the locked check-and-increment, so at most the cap succeeds;
- **the entry marks** (``LiveEntryMarks``): one entry per context per bar, and paper's post-stop-loss
  cooldown. A corrupt file refuses — the opposite of paper's marks, on purpose.

The leg-level and route-level behaviour is pinned beside the leg and the route; this file pins the
stores themselves, against the real files under ``tmp_path``.
"""

from __future__ import annotations

import json
import threading

import pytest
from tests._helpers import make_gate_authorization

from runtime.mvp_runtime.crypto import live_order
from runtime.mvp_runtime.crypto.live_pnl import LIVE_TRADING_FLAGS, LIVE_TRADING_PROVIDER_ID
from runtime.mvp_runtime.crypto.state import VENUE_TESTNET, venue_state_dir
from runtime.mvp_runtime.errors import MvpRuntimeError, ToolError

DAY = "2026-09-16"
BAR = "2026-09-16T04:00:00Z"
AUTH = make_gate_authorization(flags=LIVE_TRADING_FLAGS, provider_id=LIVE_TRADING_PROVIDER_ID)


def _counter(tmp_path, **kw):
    return live_order.LiveOrderCounter(root=tmp_path, authorization=AUTH, **kw)


def _marks(tmp_path, **kw):
    return live_order.LiveEntryMarks(root=tmp_path, authorization=AUTH, **kw)


def _code(excinfo):
    return excinfo.value.reason_code


# --- the day's order slot ------------------------------------------------------------------

def test_a_reservation_takes_a_slot_and_the_cap_refuses_the_next(tmp_path):
    counter = _counter(tmp_path)
    assert counter.reserve_submission(limit=2, day=DAY) == 1
    assert counter.reserve_submission(limit=2, day=DAY) == 2
    with pytest.raises(ToolError) as refused:
        counter.reserve_submission(limit=2, day=DAY)
    assert _code(refused) == live_order.LIVE_DAILY_ORDER_CAP_REACHED
    assert live_order.count_today(tmp_path, day=DAY) == 2, "a refused reservation must not count"


def test_an_order_counted_after_the_send_fills_the_same_cap(tmp_path):
    """`record_submission` (the testnet cycle's rule) and a reservation spend one counter."""
    counter = _counter(tmp_path)
    counter.record_submission(day=DAY)
    with pytest.raises(ToolError):
        counter.reserve_submission(limit=1, day=DAY)


@pytest.mark.parametrize("limit", [0, -1])
def test_an_unconfigured_cap_reserves_nothing(tmp_path, limit):
    with pytest.raises(ToolError) as refused:
        _counter(tmp_path).reserve_submission(limit=limit, day=DAY)
    assert _code(refused) == live_order.LIVE_DAILY_ORDER_CAP_REACHED
    assert live_order.count_today(tmp_path, day=DAY) == 0


def test_the_cap_is_per_day(tmp_path):
    counter = _counter(tmp_path)
    counter.reserve_submission(limit=1, day=DAY)
    assert counter.reserve_submission(limit=1, day="2026-09-17") == 1


def test_concurrent_reservations_never_exceed_the_cap(tmp_path):
    """The race the reservation closes: every contender read a count under the cap. Separate
    store objects, so each acquires the lock through its own handle — as two processes do."""
    results: list[object] = []
    lock = threading.Lock()
    start = threading.Barrier(8)

    def contend():
        counter = _counter(tmp_path)
        start.wait()
        try:
            outcome: object = counter.reserve_submission(limit=3, day=DAY)
        except ToolError as exc:
            outcome = exc.reason_code
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=contend) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert sorted(r for r in results if isinstance(r, int)) == [1, 2, 3]
    assert results.count(live_order.LIVE_DAILY_ORDER_CAP_REACHED) == 5
    assert live_order.count_today(tmp_path, day=DAY) == 3


def test_an_unreadable_counter_reserves_nothing(tmp_path):
    path = venue_state_dir(tmp_path) / live_order.COUNTER_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ToolError) as refused:
        _counter(tmp_path).reserve_submission(limit=5, day=DAY)
    assert _code(refused) == live_order.LIVE_COUNTER_UNREADABLE


def test_a_counter_with_no_authorization_reserves_nothing(tmp_path):
    with pytest.raises(MvpRuntimeError):
        live_order.LiveOrderCounter(root=tmp_path, authorization=None).reserve_submission(limit=5, day=DAY)
    assert live_order.count_today(tmp_path, day=DAY) == 0


def test_the_inert_counter_reserves_without_writing(tmp_path, monkeypatch):
    monkeypatch.delenv("MVP_LIVE_TRADING", raising=False)
    counter = live_order.select_live_order_counter(root=tmp_path)
    assert counter.filesystem_write is False
    assert counter.reserve_submission(limit=1) == 0
    assert not (venue_state_dir(tmp_path) / live_order.COUNTER_FILENAME).exists()


# --- the entry marks: reading ----------------------------------------------------------------

def _write_marks(tmp_path, payload):
    path = venue_state_dir(tmp_path) / live_order.ENTRY_MARKS_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")


def test_a_machine_with_no_marks_reads_empty(tmp_path):
    assert live_order.read_live_entry_marks(tmp_path) == {
        "version": live_order.ENTRY_MARKS_VERSION, "entered": {}, "cooldown": {}}


@pytest.mark.parametrize("payload", [
    "{not json",
    "[]",
    {"entered": {}, "cooldown": {}},                                          # no version
    {"version": "live_entry_marks.v0", "entered": {}, "cooldown": {}},        # another version
    {"version": live_order.ENTRY_MARKS_VERSION, "entered": {}},               # a map missing
    {"version": live_order.ENTRY_MARKS_VERSION, "entered": [], "cooldown": {}},
    {"version": live_order.ENTRY_MARKS_VERSION, "entered": {"BTCUSDT__4h": "yesterday"}, "cooldown": {}},
    {"version": live_order.ENTRY_MARKS_VERSION, "entered": {}, "cooldown": {"BTCUSDT__4h": 1}},
])
def test_marks_that_cannot_be_trusted_refuse_rather_than_read_empty(tmp_path, payload):
    """Paper's marks read a corrupt file as "no mark", which costs one redundant evaluation. Here
    "no mark" is a real order on a bar that may already have had one."""
    _write_marks(tmp_path, payload)
    with pytest.raises(ToolError) as refused:
        live_order.read_live_entry_marks(tmp_path)
    assert _code(refused) == live_order.LIVE_ENTRY_MARKS_UNREADABLE


# --- the entry marks: claiming a bar -----------------------------------------------------------

def test_a_bar_is_claimed_once(tmp_path):
    marks = _marks(tmp_path)
    marks.claim_bar(symbol="BTCUSDT", timeframe="4h", bar_time=BAR)
    assert live_order.read_live_entry_marks(tmp_path)["entered"] == {"BTCUSDT__4h": BAR}
    with pytest.raises(ToolError) as refused:
        _marks(tmp_path).claim_bar(symbol="BTCUSDT", timeframe="4h", bar_time=BAR)
    assert _code(refused) == live_order.LIVE_ENTRY_BAR_ALREADY_ENTERED


def test_an_older_bar_cannot_be_claimed_and_a_newer_one_moves_the_mark(tmp_path):
    marks = _marks(tmp_path)
    marks.claim_bar(symbol="BTCUSDT", timeframe="4h", bar_time=BAR)
    with pytest.raises(ToolError):
        marks.claim_bar(symbol="BTCUSDT", timeframe="4h", bar_time="2026-09-16T00:00:00Z")
    marks.claim_bar(symbol="BTCUSDT", timeframe="4h", bar_time="2026-09-16T08:00:00Z")
    assert live_order.read_live_entry_marks(tmp_path)["entered"] == {"BTCUSDT__4h": "2026-09-16T08:00:00Z"}


def test_contexts_claim_their_own_bars(tmp_path):
    marks = _marks(tmp_path)
    for symbol, timeframe in (("BTCUSDT", "4h"), ("BTCUSDT", "1d"), ("ETHUSDT", "4h")):
        marks.claim_bar(symbol=symbol, timeframe=timeframe, bar_time=BAR)
    assert set(live_order.read_live_entry_marks(tmp_path)["entered"]) == {
        "BTCUSDT__4h", "BTCUSDT__1d", "ETHUSDT__4h"}


def test_concurrent_claims_on_one_bar_have_one_winner(tmp_path):
    outcomes: list[str] = []
    lock = threading.Lock()
    start = threading.Barrier(6)

    def contend():
        marks = _marks(tmp_path)
        start.wait()
        try:
            marks.claim_bar(symbol="BTCUSDT", timeframe="4h", bar_time=BAR)
            outcome = "won"
        except ToolError as exc:
            outcome = exc.reason_code
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=contend) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert outcomes.count("won") == 1
    assert outcomes.count(live_order.LIVE_ENTRY_BAR_ALREADY_ENTERED) == 5


def test_a_claim_inside_a_cooldown_is_refused_under_the_lock(tmp_path):
    """The decision read the cooldown; the claim re-checks it, so a hold that landed between the
    two still holds."""
    marks = _marks(tmp_path)
    marks.record_stop_cooldown(symbol="BTCUSDT", timeframe="4h", until="2026-09-16T12:00:00Z")
    with pytest.raises(ToolError) as refused:
        marks.claim_bar(symbol="BTCUSDT", timeframe="4h", bar_time="2026-09-16T08:00:00Z")
    assert _code(refused) == live_order.LIVE_ENTRY_STOP_LOSS_COOLDOWN
    assert live_order.read_live_entry_marks(tmp_path)["entered"] == {}
    marks.claim_bar(symbol="BTCUSDT", timeframe="4h", bar_time="2026-09-16T12:00:00Z")


@pytest.mark.parametrize("kw", [
    {"symbol": "BTCUSDT", "timeframe": "4h", "bar_time": None},
    {"symbol": "BTCUSDT", "timeframe": "4h", "bar_time": "2026-09-16 04:00"},
    {"symbol": "BTCUSDT", "timeframe": None, "bar_time": BAR},
    {"symbol": "", "timeframe": "4h", "bar_time": BAR},
])
def test_a_bar_that_cannot_be_named_cannot_be_claimed(tmp_path, kw):
    with pytest.raises(ToolError) as refused:
        _marks(tmp_path).claim_bar(**kw)
    assert _code(refused) == live_order.LIVE_ENTRY_BAR_UNKNOWN
    assert not (venue_state_dir(tmp_path) / live_order.ENTRY_MARKS_FILENAME).exists()


def test_a_corrupt_file_refuses_the_claim_and_is_left_for_the_operator(tmp_path):
    _write_marks(tmp_path, "{broken")
    with pytest.raises(ToolError) as refused:
        _marks(tmp_path).claim_bar(symbol="BTCUSDT", timeframe="4h", bar_time=BAR)
    assert _code(refused) == live_order.LIVE_ENTRY_MARKS_UNREADABLE
    path = venue_state_dir(tmp_path) / live_order.ENTRY_MARKS_FILENAME
    assert path.read_text(encoding="utf-8") == "{broken", "a refusal must not overwrite the evidence"


# --- the entry marks: the cooldown -----------------------------------------------------------

def test_a_cooldown_never_shortens(tmp_path):
    marks = _marks(tmp_path)
    marks.record_stop_cooldown(symbol="BTCUSDT", timeframe="4h", until="2026-09-16T12:00:00Z")
    marks.record_stop_cooldown(symbol="BTCUSDT", timeframe="4h", until="2026-09-16T08:00:00Z")
    assert live_order.read_live_entry_marks(tmp_path)["cooldown"] == {"BTCUSDT__4h": "2026-09-16T12:00:00Z"}
    marks.record_stop_cooldown(symbol="BTCUSDT", timeframe="4h", until="2026-09-16T16:00:00Z")
    assert live_order.read_live_entry_marks(tmp_path)["cooldown"] == {"BTCUSDT__4h": "2026-09-16T16:00:00Z"}


def test_a_cooldown_keeps_the_bars_already_claimed(tmp_path):
    marks = _marks(tmp_path)
    marks.claim_bar(symbol="BTCUSDT", timeframe="4h", bar_time=BAR)
    marks.record_stop_cooldown(symbol="BTCUSDT", timeframe="4h", until="2026-09-16T16:00:00Z")
    stored = live_order.read_live_entry_marks(tmp_path)
    assert stored["entered"] == {"BTCUSDT__4h": BAR}
    assert stored["cooldown"] == {"BTCUSDT__4h": "2026-09-16T16:00:00Z"}


@pytest.mark.parametrize("kw", [
    {"symbol": "BTCUSDT", "timeframe": "4h", "until": "later"},
    {"symbol": "BTCUSDT", "timeframe": "", "until": BAR},
])
def test_a_cooldown_that_cannot_be_named_is_refused(tmp_path, kw):
    with pytest.raises(ToolError) as refused:
        _marks(tmp_path).record_stop_cooldown(**kw)
    assert _code(refused) == live_order.LIVE_ENTRY_BAR_UNKNOWN


@pytest.mark.parametrize("closed_at,minutes,expected", [
    ("2026-09-16T10:07:00Z", 15, "2026-09-16T10:30:00Z"),
    ("2026-09-16T10:59:59Z", 60, "2026-09-16T12:00:00Z"),
    ("2026-09-16T03:58:00Z", 240, "2026-09-16T08:00:00Z"),
    ("2026-09-16T04:00:00Z", 240, "2026-09-16T12:00:00Z"),   # a settle ON the boundary is the new bar
    ("2026-09-16T23:59:59Z", 1440, "2026-09-18T00:00:00Z"),
])
def test_the_cooldown_bound_is_two_bars_after_the_stop_bar(closed_at, minutes, expected):
    """Paper's window on the open-time basis: the stop bar and the one after it are held, and
    the bar after that is the first that may enter."""
    assert live_order.stop_cooldown_until(closed_at, timeframe_minutes=minutes, bars=2) == expected


@pytest.mark.parametrize("minutes,bars", [(0, 2), (-15, 2), (15, -1)])
def test_a_cooldown_bound_needs_a_bar_length(minutes, bars):
    with pytest.raises(ToolError):
        live_order.stop_cooldown_until(BAR, timeframe_minutes=minutes, bars=bars)


# --- the switch and the venue axis -----------------------------------------------------------

def test_a_mark_store_with_no_authorization_writes_nothing(tmp_path):
    with pytest.raises(MvpRuntimeError):
        live_order.LiveEntryMarks(root=tmp_path, authorization=None).claim_bar(
            symbol="BTCUSDT", timeframe="4h", bar_time=BAR)
    assert not (venue_state_dir(tmp_path) / live_order.ENTRY_MARKS_FILENAME).exists()


def test_with_the_switch_off_the_marks_are_inert(tmp_path, monkeypatch):
    monkeypatch.delenv("MVP_LIVE_TRADING", raising=False)
    marks = live_order.select_live_entry_marks(root=tmp_path)
    assert marks.filesystem_write is False
    marks.claim_bar(symbol="BTCUSDT", timeframe="4h", bar_time=BAR)
    marks.record_stop_cooldown(symbol="BTCUSDT", timeframe="4h", until=BAR)
    assert not (venue_state_dir(tmp_path) / live_order.ENTRY_MARKS_FILENAME).exists()


def test_with_the_switch_on_the_marks_are_durable(tmp_path, monkeypatch):
    monkeypatch.setenv("MVP_LIVE_TRADING", "real")
    marks = live_order.select_live_entry_marks(root=tmp_path)
    assert marks.filesystem_write is True
    marks.claim_bar(symbol="BTCUSDT", timeframe="4h", bar_time=BAR)
    assert live_order.read_live_entry_marks(tmp_path)["entered"] == {"BTCUSDT__4h": BAR}


def test_each_venue_keeps_its_own_marks(tmp_path):
    _marks(tmp_path, venue=VENUE_TESTNET).claim_bar(symbol="BTCUSDT", timeframe="4h", bar_time=BAR)
    assert live_order.read_live_entry_marks(tmp_path)["entered"] == {}
    assert live_order.read_live_entry_marks(tmp_path, venue=VENUE_TESTNET)["entered"] == {"BTCUSDT__4h": BAR}
    _marks(tmp_path).claim_bar(symbol="BTCUSDT", timeframe="4h", bar_time=BAR)
