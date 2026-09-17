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


@pytest.mark.parametrize("stored", [
    {DAY: -5},          # a negative count passed `current >= limit` for as many orders
    {DAY: "1"},         # never written by the counter
    {DAY: 1.5},
    {DAY: True},
    [],                 # not an object: used to be replaced by a fresh one
    "x",
    None,
])
def test_a_damaged_counter_refuses_and_is_left_as_evidence(tmp_path, stored):
    path = venue_state_dir(tmp_path) / live_order.COUNTER_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(stored)
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ToolError) as refused:
        _counter(tmp_path).reserve_submission(limit=2, day=DAY)
    assert _code(refused) == live_order.LIVE_COUNTER_UNREADABLE
    with pytest.raises(ToolError):
        _counter(tmp_path).record_submission(day=DAY)
    with pytest.raises(ToolError):
        live_order.count_today(tmp_path, day=DAY)
    assert path.read_text(encoding="utf-8") == text


def test_another_days_damage_does_not_close_today(tmp_path):
    """Only the day being counted is judged: an old malformed entry is not today's budget."""
    path = venue_state_dir(tmp_path) / live_order.COUNTER_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"2026-01-01": "junk", DAY: 1}), encoding="utf-8")
    assert _counter(tmp_path).reserve_submission(limit=2, day=DAY) == 2


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
        "version": live_order.ENTRY_MARKS_VERSION, "entered": {}, "cooldown": {}, "in_flight": {}}


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


@pytest.mark.parametrize("minutes,bars", [(0, 2), (-15, 2), (15, -1), (True, 2), (15.0, 2)])
def test_a_cooldown_bound_needs_a_bar_length(minutes, bars):
    with pytest.raises(ToolError) as refused:
        live_order.stop_cooldown_until(BAR, timeframe_minutes=minutes, bars=bars)
    assert _code(refused) == live_order.LIVE_ENTRY_COOLDOWN_UNCOMPUTABLE


@pytest.mark.parametrize("closed_at", [None, "", "2026-09-16", "2026-09-16T04:00:00+00:00", "soon"])
def test_a_cooldown_bound_needs_an_instant_it_can_read(closed_at):
    """Typed, not a bare ValueError from the parser."""
    with pytest.raises(ToolError) as refused:
        live_order.stop_cooldown_until(closed_at, timeframe_minutes=240, bars=2)
    assert _code(refused) == live_order.LIVE_ENTRY_COOLDOWN_UNCOMPUTABLE


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


def test_a_venue_store_judges_its_claims_on_its_own_marks(tmp_path):
    """The #876 bug class: a store that read another venue's baseline would refuse (or admit) on
    marks it does not own. A mainnet claim must not spend the testnet bar, a mainnet cooldown must
    not hold the testnet context — and each store still refuses its own second claim."""
    _marks(tmp_path).claim_bar(symbol="BTCUSDT", timeframe="4h", bar_time=BAR)
    _marks(tmp_path).record_stop_cooldown(symbol="ETHUSDT", timeframe="4h", until="2026-09-17T00:00:00Z")
    testnet = _marks(tmp_path, venue=VENUE_TESTNET)
    testnet.claim_bar(symbol="BTCUSDT", timeframe="4h", bar_time=BAR)
    testnet.claim_bar(symbol="ETHUSDT", timeframe="4h", bar_time=BAR)
    with pytest.raises(ToolError) as refused:
        testnet.claim_bar(symbol="BTCUSDT", timeframe="4h", bar_time=BAR)
    assert _code(refused) == live_order.LIVE_ENTRY_BAR_ALREADY_ENTERED
    testnet.record_stop_cooldown(symbol="SOLUSDT", timeframe="4h", until="2026-09-16T08:00:00Z")
    assert live_order.read_live_entry_marks(tmp_path)["cooldown"] == {"ETHUSDT__4h": "2026-09-17T00:00:00Z"}
    assert live_order.read_live_entry_marks(tmp_path, venue=VENUE_TESTNET)["cooldown"] == {
        "SOLUSDT__4h": "2026-09-16T08:00:00Z"}


def test_both_pre_send_stores_sync_their_file_before_returning(tmp_path, monkeypatch):
    """A reserved slot or a claimed bar that a crash forgets is an order the store no longer
    knows about. Not observable without a crash, so the call itself is pinned (review of #880)."""
    synced: list[int] = []
    real_fsync = live_order.os.fsync

    def _recording_fsync(fd):
        synced.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(live_order.os, "fsync", _recording_fsync)
    _counter(tmp_path).reserve_submission(limit=2, day=DAY)
    assert len(synced) == 1, "the counter returned a reservation it had not synced"
    _marks(tmp_path).claim_bar(symbol="BTCUSDT", timeframe="4h", bar_time=BAR)
    assert len(synced) == 2, "the marks store returned a claim it had not synced"


# --- the symbol's entry in flight (PR2b-2) -----------------------------------------------------

NOW = "2026-09-17T04:05:00Z"
ORDER = "TAI_BTCUSDT_LONG_aaaa"


# A flat account, a 60 USDT order and the approved 120 USDT exposure cap (PR2c-3).
FLAT = {"open_notional_usdt": 0.0, "symbols": [], "cap_usdt": 120.0}


def _claim(marks, *, symbol="BTCUSDT", door="autonomous", client_order_id=ORDER, now=NOW,
           notional_usdt=60.0, exposure=FLAT):
    return marks.claim_symbol(symbol=symbol, door=door, client_order_id=client_order_id, now=now,
                              notional_usdt=notional_usdt, exposure=exposure)


@pytest.fixture
def frozen_wall(monkeypatch):
    """The wall clock the claim stamps with (the later of it and `now`), set by the test."""
    clock = {"now": NOW}
    monkeypatch.setattr(live_order.timeutil, "utc_now_iso", lambda: clock["now"])
    return clock


def _open_position(tmp_path, symbol="BTCUSDT"):
    from runtime.mvp_runtime.crypto.live_position import RealLivePositionStore, build_live_position

    RealLivePositionStore(root=tmp_path, authorization=AUTH).save_position(build_live_position(
        symbol=symbol, direction="LONG", quantity=0.001, entry_price=60000.0, opened_at=NOW,
        entry_client_order_id="TAI_BTCUSDT_LONG_open", strategy_id="S001"))


def test_a_symbol_is_taken_once_and_given_back_by_its_own_order(tmp_path, frozen_wall):
    marks = _marks(tmp_path)
    _claim(marks)
    assert live_order.read_live_entry_marks(tmp_path)["in_flight"] == {
        "BTCUSDT": {"claimed_at": NOW, "door": "autonomous", "client_order_id": ORDER, "notional_usdt": 60.0}}
    with pytest.raises(ToolError) as refused:
        _claim(_marks(tmp_path), door="probe", client_order_id="TAI_BTCUSDT_LONG_bbbb")
    assert _code(refused) == live_order.LIVE_ENTRY_SYMBOL_IN_FLIGHT
    with pytest.raises(ToolError) as lost:
        _marks(tmp_path).release_symbol(symbol="BTCUSDT", client_order_id="TAI_BTCUSDT_LONG_bbbb")
    assert _code(lost) == live_order.LIVE_ENTRY_CLAIM_LOST
    assert "BTCUSDT" in live_order.read_live_entry_marks(tmp_path)["in_flight"], "not that order's claim"
    marks.release_symbol(symbol="BTCUSDT", client_order_id=ORDER)
    assert live_order.read_live_entry_marks(tmp_path)["in_flight"] == {}
    _claim(_marks(tmp_path), door="probe", client_order_id="TAI_BTCUSDT_LONG_bbbb")


def test_symbols_are_taken_on_their_own(tmp_path, frozen_wall):
    marks = _marks(tmp_path)
    _claim(marks)
    _claim(marks, symbol="ETHUSDT", client_order_id="TAI_ETHUSDT_LONG_cccc")
    assert set(live_order.read_live_entry_marks(tmp_path)["in_flight"]) == {"BTCUSDT", "ETHUSDT"}


def test_a_symbol_holding_a_position_cannot_be_taken(tmp_path, frozen_wall):
    """The book is re-read under the lock: an entry that booked its position and gave the symbol
    back after this door read the book is seen here."""
    _open_position(tmp_path)
    with pytest.raises(ToolError) as refused:
        _claim(_marks(tmp_path))
    assert _code(refused) == live_order.LIVE_ENTRY_SYMBOL_OCCUPIED
    assert live_order.read_live_entry_marks(tmp_path)["in_flight"] == {}


def test_a_book_that_cannot_be_read_refuses_the_claim(tmp_path, frozen_wall):
    from runtime.mvp_runtime.crypto.live_position import live_position_path

    path = live_position_path("BTCUSDT", tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ToolError):
        _claim(_marks(tmp_path))
    assert live_order.read_live_entry_marks(tmp_path)["in_flight"] == {}


def test_a_claim_expires_after_thirty_minutes(tmp_path, frozen_wall):
    """Decision 21: a process that died mid-entry does not hold the symbol for good."""
    _claim(_marks(tmp_path))
    frozen_wall["now"] = "2026-09-17T04:34:59Z"
    with pytest.raises(ToolError):
        _claim(_marks(tmp_path), client_order_id="TAI_BTCUSDT_LONG_late")
    frozen_wall["now"] = "2026-09-17T04:35:00Z"
    _claim(_marks(tmp_path), client_order_id="TAI_BTCUSDT_LONG_late")
    claim = live_order.read_live_entry_marks(tmp_path)["in_flight"]["BTCUSDT"]
    assert claim == {"claimed_at": "2026-09-17T04:35:00Z", "door": "autonomous",
                     "client_order_id": "TAI_BTCUSDT_LONG_late", "notional_usdt": 60.0}
    # The dead entry's late release cannot remove the claim that replaced it, and it is told so.
    with pytest.raises(ToolError) as lost:
        _marks(tmp_path).release_symbol(symbol="BTCUSDT", client_order_id=ORDER)
    assert _code(lost) == live_order.LIVE_ENTRY_CLAIM_LOST
    assert live_order.read_live_entry_marks(tmp_path)["in_flight"]["BTCUSDT"] == claim
    # A claim that is gone altogether is lost too.
    with pytest.raises(ToolError) as gone:
        _marks(tmp_path).release_symbol(symbol="ETHUSDT", client_order_id=ORDER)
    assert _code(gone) == live_order.LIVE_ENTRY_CLAIM_LOST


def test_a_claim_is_stamped_with_the_later_of_now_and_the_wall_clock(tmp_path, frozen_wall):
    """The cycle's `now` can be minutes old when its leg runs; an early stamp would expire early."""
    frozen_wall["now"] = "2026-09-17T04:20:00Z"
    _claim(_marks(tmp_path), now=NOW)
    assert live_order.read_live_entry_marks(tmp_path)["in_flight"]["BTCUSDT"]["claimed_at"] == "2026-09-17T04:20:00Z"
    frozen_wall["now"] = "2026-09-17T04:00:00Z"
    _claim(_marks(tmp_path), symbol="ETHUSDT", client_order_id="TAI_ETHUSDT_LONG_dddd", now=NOW)
    assert live_order.read_live_entry_marks(tmp_path)["in_flight"]["ETHUSDT"]["claimed_at"] == NOW


@pytest.mark.parametrize("kw", [
    {"symbol": ""}, {"symbol": None}, {"door": ""}, {"client_order_id": ""}, {"client_order_id": None},
    {"now": "2026-09-17 04:05"}, {"now": None},
])
def test_a_claim_that_cannot_be_named_is_refused(tmp_path, kw):
    with pytest.raises(ToolError) as refused:
        _claim(_marks(tmp_path), **kw)
    assert _code(refused) == live_order.LIVE_ENTRY_CLAIM_MALFORMED
    assert not (venue_state_dir(tmp_path) / live_order.ENTRY_MARKS_FILENAME).exists()


def test_concurrent_claims_on_one_symbol_have_one_winner(tmp_path, frozen_wall):
    outcomes: list[str] = []
    lock = threading.Lock()
    start = threading.Barrier(6)

    def contend(index):
        marks = _marks(tmp_path)
        start.wait()
        try:
            _claim(marks, client_order_id=f"TAI_BTCUSDT_LONG_{index}")
            outcome = "won"
        except ToolError as exc:
            outcome = exc.reason_code
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=contend, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert outcomes.count("won") == 1
    assert outcomes.count(live_order.LIVE_ENTRY_SYMBOL_IN_FLIGHT) == 5


def test_a_file_from_before_the_rule_reads_with_nothing_in_flight(tmp_path):
    _write_marks(tmp_path, {"version": live_order.ENTRY_MARKS_VERSION,
                            "entered": {"BTCUSDT__4h": BAR}, "cooldown": {}})
    assert live_order.read_live_entry_marks(tmp_path)["in_flight"] == {}


@pytest.mark.parametrize("in_flight", [
    [],
    {"BTCUSDT": "yes"},
    {"": {"claimed_at": NOW, "door": "probe", "client_order_id": ORDER}},
    {"BTCUSDT": {"claimed_at": "now", "door": "probe", "client_order_id": ORDER}},
    {"BTCUSDT": {"claimed_at": NOW, "door": "", "client_order_id": ORDER}},
    {"BTCUSDT": {"claimed_at": NOW, "door": "probe"}},
    {"BTCUSDT": {"claimed_at": NOW, "door": "probe", "client_order_id": ORDER, "extra": 1}},
    {"BTCUSDT": {"claimed_at": "2026-99-99T99:99:99Z", "door": "probe", "client_order_id": ORDER}},
    {"BTCUSDT": {"claimed_at": "9999-12-31T23:59:59Z", "door": "probe", "client_order_id": ORDER}},
], ids=["list", "not-a-claim", "no-symbol", "time-form", "no-door", "missing-field", "extra-field",
        "impossible-date", "no-expiry"])
def test_an_in_flight_map_that_cannot_be_trusted_refuses(tmp_path, in_flight):
    _write_marks(tmp_path, {"version": live_order.ENTRY_MARKS_VERSION, "entered": {}, "cooldown": {},
                            "in_flight": in_flight})
    with pytest.raises(ToolError) as refused:
        live_order.read_live_entry_marks(tmp_path)
    assert _code(refused) == live_order.LIVE_ENTRY_MARKS_UNREADABLE


def test_the_bar_rules_and_the_claims_share_one_file(tmp_path, frozen_wall):
    marks = _marks(tmp_path)
    _claim(marks)
    marks.claim_bar(symbol="BTCUSDT", timeframe="4h", bar_time=BAR)
    marks.record_stop_cooldown(symbol="ETHUSDT", timeframe="4h", until=BAR)
    stored = live_order.read_live_entry_marks(tmp_path)
    assert stored["entered"] == {"BTCUSDT__4h": BAR} and "BTCUSDT" in stored["in_flight"]
    assert stored["cooldown"] == {"ETHUSDT__4h": BAR}


@pytest.mark.parametrize("now,held", [
    ("2026-09-17T04:05:00Z", True), ("2026-09-17T04:34:59Z", True), ("2026-09-17T04:35:00Z", False),
    ("not a time", True),
])
def test_the_pure_read_of_a_claim(now, held):
    marks = {"in_flight": {"BTCUSDT": {"claimed_at": NOW, "door": "probe", "client_order_id": ORDER}}}
    assert (live_order.symbol_in_flight(marks, "BTCUSDT", now=now) is not None) is held
    assert live_order.symbol_in_flight(marks, "ETHUSDT", now=now) is None
    holds = live_order.live_entry_holds(dict(marks, entered={}, cooldown={}), symbol="BTCUSDT",
                                        timeframe="4h", bar_time=BAR, now=now)
    assert (live_order.LIVE_ENTRY_SYMBOL_IN_FLIGHT in holds) is held
    # Without a time, the bar rules alone: the claim is the lock's to judge.
    assert live_order.live_entry_holds(dict(marks, entered={}, cooldown={}), symbol="BTCUSDT",
                                       timeframe="4h", bar_time=BAR) == []


def test_a_claim_whose_expiry_cannot_be_computed_still_holds():
    """Reachable only through a damaged file the reader already refuses; the pure read must still
    fail towards holding, never raise into the leg."""
    marks = {"in_flight": {"BTCUSDT": {"claimed_at": "9999-12-31T23:59:59Z", "door": "probe",
                                       "client_order_id": ORDER}}}
    assert live_order.symbol_in_flight(marks, "BTCUSDT", now=NOW) is not None


def test_claims_need_the_live_authorization_and_are_inert_with_the_switch_off(tmp_path, monkeypatch):
    with pytest.raises(MvpRuntimeError):
        _claim(live_order.LiveEntryMarks(root=tmp_path, authorization=None))
    monkeypatch.delenv("MVP_LIVE_TRADING", raising=False)
    inert = live_order.select_live_entry_marks(root=tmp_path)
    _claim(inert)
    inert.release_symbol(symbol="BTCUSDT", client_order_id=ORDER)
    assert not (venue_state_dir(tmp_path) / live_order.ENTRY_MARKS_FILENAME).exists()


def test_a_testnet_claim_is_not_a_mainnet_claim(tmp_path, frozen_wall):
    _claim(_marks(tmp_path, venue=VENUE_TESTNET))
    assert live_order.read_live_entry_marks(tmp_path)["in_flight"] == {}
    _claim(_marks(tmp_path))


# --- the global caps, judged again at the claim (PR2c-3, decision 26) -----------------------------

def _book(tmp_path, symbol, *, notional=60.0):
    from runtime.mvp_runtime.crypto.live_position import RealLivePositionStore, build_live_position

    RealLivePositionStore(root=tmp_path, authorization=AUTH).save_position(build_live_position(
        symbol=symbol, direction="LONG", quantity=0.001, entry_price=notional * 1000, opened_at=NOW,
        entry_client_order_id=f"TAI_{symbol}_LONG_open", strategy_id="S001"))


def _order(symbol):
    return f"TAI_{symbol}_LONG_{symbol.lower()[:4]}"


def test_two_doors_on_two_symbols_cannot_both_take_the_last_position(tmp_path, frozen_wall):
    """The measured race: one position booked, and two entries on other symbols, each judged
    "booked + mine fits" before the other's order. At most two positions."""
    _book(tmp_path, "ETHUSDT")
    seen = {"open_notional_usdt": 60.0, "symbols": ["ETHUSDT"], "cap_usdt": 300.0}
    _claim(_marks(tmp_path), symbol="SOLUSDT", client_order_id=_order("SOLUSDT"), exposure=seen)
    with pytest.raises(ToolError) as refused:
        _claim(_marks(tmp_path), symbol="BTCUSDT", door="probe", exposure=seen)
    assert _code(refused) == live_order.LIVE_ENTRY_CAPACITY_TAKEN
    assert set(live_order.read_live_entry_marks(tmp_path)["in_flight"]) == {"SOLUSDT"}


def test_two_doors_on_two_symbols_cannot_both_spend_the_last_exposure(tmp_path, frozen_wall):
    """A flat book: each order fits alone, both together exceed the cap."""
    seen = {"open_notional_usdt": 0.0, "symbols": [], "cap_usdt": 100.0}
    _claim(_marks(tmp_path), symbol="SOLUSDT", client_order_id=_order("SOLUSDT"), exposure=seen)
    with pytest.raises(ToolError) as refused:
        _claim(_marks(tmp_path), symbol="BTCUSDT", exposure=seen)
    assert _code(refused) == live_order.LIVE_ENTRY_EXPOSURE_TAKEN
    # Room for the second one once the first is only 40.
    _marks(tmp_path).release_symbol(symbol="SOLUSDT", client_order_id=_order("SOLUSDT"))
    _claim(_marks(tmp_path), symbol="SOLUSDT", client_order_id=_order("SOLUSDT"), notional_usdt=40.0,
           exposure=seen)
    _claim(_marks(tmp_path), symbol="BTCUSDT", exposure=seen)


@pytest.mark.parametrize("seen,notional,fits", [
    # The door's venue read already held ETH: its 60 is in the seen figure and counted once.
    ({"open_notional_usdt": 60.0, "symbols": ["ETHUSDT"]}, 60.0, True),
    # Booked after the read: added to the seen 0, up to the cap and not a cent past it.
    ({"open_notional_usdt": 0.0, "symbols": []}, 60.0, True),
    ({"open_notional_usdt": 0.0, "symbols": []}, 60.01, False),
    # A read that named no symbols: every booked position is added again.
    ({"open_notional_usdt": 60.0, "symbols": None}, 0.01, False),
], ids=["seen", "since-fits", "since-over", "unknown"])
def test_a_booked_position_is_counted_once_against_the_exposure(tmp_path, frozen_wall, seen, notional, fits):
    _book(tmp_path, "ETHUSDT", notional=60.0)
    exposure = {**seen, "cap_usdt": 120.0}
    if fits:
        _claim(_marks(tmp_path), notional_usdt=notional, exposure=exposure)
        return
    with pytest.raises(ToolError) as refused:
        _claim(_marks(tmp_path), notional_usdt=notional, exposure=exposure)
    assert _code(refused) == live_order.LIVE_ENTRY_EXPOSURE_TAKEN


def test_an_expired_claim_takes_no_room(tmp_path, frozen_wall):
    seen = {"open_notional_usdt": 0.0, "symbols": [], "cap_usdt": 100.0}
    _claim(_marks(tmp_path), symbol="SOLUSDT", client_order_id=_order("SOLUSDT"), exposure=seen)
    frozen_wall["now"] = "2026-09-17T04:35:00Z"
    _claim(_marks(tmp_path), symbol="BTCUSDT", now="2026-09-17T04:35:00Z", exposure=seen)


def test_a_claim_whose_position_is_booked_is_counted_once(tmp_path, frozen_wall):
    """A door books its position before it gives the symbol back: in between, the book counts it."""
    seen = {"open_notional_usdt": 0.0, "symbols": [], "cap_usdt": 130.0}
    _claim(_marks(tmp_path), symbol="SOLUSDT", client_order_id=_order("SOLUSDT"), exposure=seen)
    _book(tmp_path, "SOLUSDT", notional=60.0)
    _claim(_marks(tmp_path), symbol="BTCUSDT", exposure=seen)      # 60 booked + 60 = 120 <= 130


def test_a_claim_written_before_its_notional_was_recorded_counts_as_the_whole_cap(tmp_path, frozen_wall):
    path = live_order.venue_state_dir(tmp_path) / live_order.ENTRY_MARKS_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": live_order.ENTRY_MARKS_VERSION, "entered": {}, "cooldown": {},
                                "in_flight": {"SOLUSDT": {"claimed_at": NOW, "door": "probe",
                                                          "client_order_id": _order("SOLUSDT")}}}),
                    encoding="utf-8")
    assert "notional_usdt" not in live_order.read_live_entry_marks(tmp_path)["in_flight"]["SOLUSDT"]
    with pytest.raises(ToolError) as refused:
        _claim(_marks(tmp_path), notional_usdt=0.01)
    assert _code(refused) == live_order.LIVE_ENTRY_EXPOSURE_TAKEN


def test_a_booked_record_whose_notional_cannot_be_read_counts_as_the_whole_cap(tmp_path, frozen_wall):
    marks = live_order.read_live_entry_marks(tmp_path)
    booked = [{"symbol": "ETHUSDT", "notional_usdt": None}]
    problem = live_order.claim_caps_problem(
        marks, booked, symbol="BTCUSDT", now=NOW, notional_usdt=0.01, max_positions=2,
        exposure={"open_notional_usdt": 0.0, "symbols": [], "cap_usdt": 120.0})
    assert problem is not None and problem[0] == live_order.LIVE_ENTRY_EXPOSURE_TAKEN


@pytest.mark.parametrize("notional,exposure", [
    (None, FLAT), (0.0, FLAT), (-1.0, FLAT), (float("nan"), FLAT), (True, FLAT), ("60", FLAT),
    (60.0, None), (60.0, {}),
    (60.0, {**FLAT, "cap_usdt": 0.0}), (60.0, {**FLAT, "cap_usdt": None}),
    (60.0, {**FLAT, "open_notional_usdt": -1.0}), (60.0, {**FLAT, "open_notional_usdt": None}),
    (60.0, {**FLAT, "symbols": "BTCUSDT"}), (60.0, {**FLAT, "symbols": [1]}),
], ids=["no-notional", "zero", "negative", "nan", "bool", "string", "no-exposure", "empty-exposure",
        "zero-cap", "no-cap", "negative-open", "no-open", "symbols-string", "symbols-not-strings"])
def test_a_claim_that_cannot_say_what_it_adds_is_refused(tmp_path, frozen_wall, notional, exposure):
    with pytest.raises(ToolError) as refused:
        _claim(_marks(tmp_path), notional_usdt=notional, exposure=exposure)
    assert _code(refused) == live_order.LIVE_ENTRY_CLAIM_MALFORMED
    assert live_order.read_live_entry_marks(tmp_path)["in_flight"] == {}


@pytest.mark.parametrize("notional", [0, -1.0, "60", True, None, float("inf")])
def test_a_recorded_claim_with_a_bad_notional_makes_the_marks_unreadable(tmp_path, notional):
    path = live_order.venue_state_dir(tmp_path) / live_order.ENTRY_MARKS_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    claim = {"claimed_at": NOW, "door": "probe", "client_order_id": ORDER, "notional_usdt": notional}
    path.write_text(json.dumps({"version": live_order.ENTRY_MARKS_VERSION, "entered": {}, "cooldown": {},
                                "in_flight": {"BTCUSDT": claim}}), encoding="utf-8")
    with pytest.raises(ToolError) as refused:
        live_order.read_live_entry_marks(tmp_path)
    assert _code(refused) == live_order.LIVE_ENTRY_MARKS_UNREADABLE


def test_the_caps_count_other_symbols_only_whatever_the_marks_hold_for_this_one():
    """`claim_caps_problem` on its own: an entry's own symbol is never one of the others, even when
    the marks still show a claim there (the claim itself refuses that case first)."""
    marks = {"version": live_order.ENTRY_MARKS_VERSION, "entered": {}, "cooldown": {},
             "in_flight": {"BTCUSDT": {"claimed_at": NOW, "door": "probe", "client_order_id": ORDER,
                                       "notional_usdt": 60.0},
                           "ETHUSDT": {"claimed_at": NOW, "door": "probe",
                                       "client_order_id": "TAI_ETHUSDT_LONG_eeee", "notional_usdt": 60.0}}}
    fits = live_order.claim_caps_problem(
        marks, [], symbol="BTCUSDT", now=NOW, notional_usdt=60.0, max_positions=2,
        exposure={"open_notional_usdt": 0.0, "symbols": [], "cap_usdt": 120.0})
    assert fits is None, fits
