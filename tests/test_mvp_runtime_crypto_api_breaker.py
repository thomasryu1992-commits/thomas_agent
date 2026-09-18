"""The API error breaker (PR2d-1, Thomas decisions 18 and 27): the count that stops new live entries
when the venue will not answer this machine's signed calls.

What it bounds is the failure nothing else did. Reads answer and sends do not, so each entry that
leaves has an outcome nobody knows; the symbol's claim holds it for thirty minutes, and the next
fire tries again — up to the daily order cap, with no message to the operator.

So the tests that matter are the ones about what does NOT reset it: a success of the other class,
any success once it has tripped, and a refusal the venue answered (decision 27 ends a streak on a
success only). The leg's own use of it is tested with the leg, in
``test_mvp_runtime_crypto_live_route.py``.
"""

from __future__ import annotations

import http.client
import io
import json
import urllib.error

import pytest
from tests._helpers import make_gate_authorization

from runtime.mvp_runtime.crypto import account as account_mod
from runtime.mvp_runtime.crypto import live_execution as lx
from runtime.mvp_runtime.crypto.live_order import (
    API_ADAPTER_CALLS,
    API_BREAKER_FILENAME,
    API_BREAKER_NOTICE_RETRY_SECONDS,
    API_CALL_CLASSES,
    API_CALL_READ,
    API_CALL_WRITE,
    MAX_CONSECUTIVE_API_ERRORS,
    ApiErrorRecordingAdapter,
    DryRunLiveApiErrorBreaker,
    LiveApiErrorBreaker,
    api_breaker_status,
    api_breaker_trip_lines,
    api_error_counts,
    read_api_errors,
    record_account_read,
    recorded_like,
    select_live_api_breaker,
)
from runtime.mvp_runtime.crypto.live_pnl import (
    LIVE_TRADING_ENV,
    LIVE_TRADING_FLAGS,
    LIVE_TRADING_PROVIDER_ID,
)
from runtime.mvp_runtime.crypto.state import venue_state_dir
from runtime.mvp_runtime.errors import SafetyGateBlocked, ToolError

NOW = "2026-09-18T08:00:00Z"

_LIVE_AUTH = make_gate_authorization(flags=LIVE_TRADING_FLAGS, provider_id=LIVE_TRADING_PROVIDER_ID)


def _breaker(root):
    return LiveApiErrorBreaker(root=root, authorization=_LIVE_AUTH)


def _fail(breaker, call_class=API_CALL_WRITE, *, call="submit", at=NOW,
          reason_code="ORDER_TRANSPORT", venue_code=None):
    return breaker.record_failure(call_class=call_class, call=call, at=at,
                                  reason_code=reason_code, venue_code=venue_code)


def _path(root):
    return venue_state_dir(root) / API_BREAKER_FILENAME


# === the store =====================================================================

def test_starts_empty_and_clear(tmp_path):
    status = api_breaker_status(tmp_path)
    assert status["tripped"] is False and status["tripped_class"] is None
    assert status["consecutive"] == 0 and status["limit"] == MAX_CONSECUTIVE_API_ERRORS
    assert {name: status[name]["consecutive"] for name in API_CALL_CLASSES} == {"write": 0, "read": 0}


def test_a_failure_is_counted_in_its_class_with_what_the_venue_said(tmp_path):
    stored = _fail(_breaker(tmp_path), API_CALL_READ, call="fetch_order",
                   reason_code="ORDER_REJECTED", venue_code=-1003)
    assert stored[API_CALL_READ] == {
        "consecutive": 1, "total": 1, "last_failure_at": NOW, "last_call": "fetch_order",
        "last_reason_code": "ORDER_REJECTED", "last_venue_code": -1003,
    }
    assert stored[API_CALL_WRITE]["consecutive"] == 0
    assert stored["just_tripped"] is False and stored["limit"] == MAX_CONSECUTIVE_API_ERRORS
    assert read_api_errors(tmp_path)[API_CALL_READ]["consecutive"] == 1


@pytest.mark.parametrize("venue_code", ["-1003", True, 1.5], ids=["text", "bool", "float"])
def test_only_an_integer_venue_code_is_kept(tmp_path, venue_code):
    stored = _fail(_breaker(tmp_path), venue_code=venue_code)
    assert stored[API_CALL_WRITE]["last_venue_code"] is None


def test_it_trips_at_the_limit_and_says_so_exactly_once(tmp_path):
    breaker = _breaker(tmp_path)
    latched = [_fail(breaker)["just_tripped"] for _ in range(MAX_CONSECUTIVE_API_ERRORS + 2)]
    assert latched == [False] * (MAX_CONSECUTIVE_API_ERRORS - 1) + [True, False, False]
    status = api_breaker_status(tmp_path)
    assert status["tripped"] is True and status["tripped_class"] == API_CALL_WRITE
    assert status["tripped_at"] == NOW
    assert status["consecutive"] == MAX_CONSECUTIVE_API_ERRORS + 2


def test_the_two_classes_are_counted_apart(tmp_path):
    """Every send is preceded by reads, so one streak would let a read that works hide a send
    that does not — and failures of both kinds, interleaved, are two streaks, not one."""
    breaker = _breaker(tmp_path)
    for _ in range(MAX_CONSECUTIVE_API_ERRORS - 1):
        _fail(breaker, API_CALL_WRITE)
        _fail(breaker, API_CALL_READ, call="open_orders")
    status = api_breaker_status(tmp_path)
    assert status["tripped"] is False
    assert status["consecutive"] == MAX_CONSECUTIVE_API_ERRORS - 1


def test_a_read_that_works_does_not_end_a_failing_send_streak(tmp_path):
    breaker = _breaker(tmp_path)
    for _ in range(MAX_CONSECUTIVE_API_ERRORS - 1):
        _fail(breaker, API_CALL_WRITE)
        breaker.record_success(call_class=API_CALL_READ)
    assert _fail(breaker, API_CALL_WRITE)["just_tripped"] is True


def test_a_success_of_the_same_class_ends_the_streak_and_keeps_the_history(tmp_path):
    breaker = _breaker(tmp_path)
    _fail(breaker)
    _fail(breaker)
    stored = breaker.record_success(call_class=API_CALL_WRITE)
    assert stored[API_CALL_WRITE]["consecutive"] == 0
    assert stored[API_CALL_WRITE]["total"] == 2
    assert stored[API_CALL_WRITE]["last_reason_code"] == "ORDER_TRANSPORT"


def test_once_tripped_no_success_clears_it(tmp_path):
    """Closes, settlement and the account keep calling the venue while entries are shut. A
    breaker any success could clear would clear itself on the next pass."""
    breaker = _breaker(tmp_path)
    for _ in range(MAX_CONSECUTIVE_API_ERRORS):
        _fail(breaker)
    for call_class in (API_CALL_WRITE, API_CALL_READ, API_CALL_WRITE):
        breaker.record_success(call_class=call_class)
    status = api_breaker_status(tmp_path)
    assert status["tripped"] is True
    assert status[API_CALL_WRITE]["consecutive"] == MAX_CONSECUTIVE_API_ERRORS


def test_while_tripped_the_record_is_frozen_as_evidence(tmp_path):
    """Once latched, a success of either class changes nothing — not even the class that did not
    trip. What else was failing when it tripped is what the operator reads before clearing."""
    breaker = _breaker(tmp_path)
    for _ in range(2):
        _fail(breaker, API_CALL_READ, call="open_orders")
    for _ in range(MAX_CONSECUTIVE_API_ERRORS):
        _fail(breaker, API_CALL_WRITE)
    before = read_api_errors(tmp_path)
    breaker.record_success(call_class=API_CALL_READ)
    assert read_api_errors(tmp_path) == before


def test_a_success_with_nothing_counted_writes_nothing(tmp_path):
    """The normal path — every signed call of every pass — costs a read of the record, never a
    write of it."""
    _breaker(tmp_path).record_success(call_class=API_CALL_READ)
    assert not _path(tmp_path).exists()


def test_the_operator_clear_resets_both_classes_and_records_who_and_why(tmp_path):
    breaker = _breaker(tmp_path)
    for _ in range(MAX_CONSECUTIVE_API_ERRORS):
        _fail(breaker)
    _fail(breaker, API_CALL_READ, call="fetch_order")
    stored = breaker.clear(actor="thomas", reason="venue maintenance; answering again",
                           at="2026-09-18T09:00:00Z")
    assert {name: stored[name]["consecutive"] for name in API_CALL_CLASSES} == {"write": 0, "read": 0}
    assert stored[API_CALL_WRITE]["total"] == MAX_CONSECUTIVE_API_ERRORS   # history is kept
    assert (stored["cleared_by"], stored["cleared_reason"], stored["cleared_at"]) == (
        "thomas", "venue maintenance; answering again", "2026-09-18T09:00:00Z")
    status = api_breaker_status(tmp_path)
    assert status["tripped"] is False and status["tripped_class"] is None
    # A reset, not an exemption: it trips again, and says so again.
    latched = [_fail(breaker)["just_tripped"] for _ in range(MAX_CONSECUTIVE_API_ERRORS)]
    assert latched[-1] is True and not any(latched[:-1])


def test_the_record_survives_a_reread_and_stores_no_verdict(tmp_path):
    _fail(_breaker(tmp_path), API_CALL_READ)
    stored = json.loads(_path(tmp_path).read_text(encoding="utf-8"))
    assert stored[API_CALL_READ]["consecutive"] == 1
    assert "just_tripped" not in stored and "limit" not in stored and "tripped" not in stored


@pytest.mark.parametrize("content", [
    "{torn",
    "[]",
    '{"write": {"consecutive": 0}}',
    '{"write": {"consecutive": "two"}, "read": {}}',
    '{"write": [], "read": {}}',
    '{"write": {"consecutive": Infinity}, "read": {}}',
    '{"write": {"consecutive": true}, "read": {}}',
    '{"write": {"consecutive": -1}, "read": {}}',
    '{"write": {"consecutive": 2.0}, "read": {}}',
    '{"write": {}, "read": {"total": "3"}}',
], ids=["not-json", "not-an-object", "a-class-missing", "non-integer", "class-not-an-object",
        "non-finite", "bool", "negative", "float", "text-total"])
def test_an_unreadable_record_fails_closed_and_is_never_overwritten(tmp_path, content):
    """A breaker whose state reads as zero because the file is corrupt reopens the door it
    exists to hold shut; one that overwrote it would erase the evidence as well."""
    path = _path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        api_breaker_status(tmp_path)
    assert exc.value.reason_code == "LIVE_API_BREAKER_UNREADABLE"
    with pytest.raises(ToolError):
        _fail(_breaker(tmp_path))
    with pytest.raises(ToolError):
        _breaker(tmp_path).clear(actor="thomas", reason="x", at=NOW)
    assert path.read_text(encoding="utf-8") == content


def test_an_unknown_call_class_is_refused(tmp_path):
    with pytest.raises(ToolError) as exc:
        _fail(_breaker(tmp_path), "order")
    assert exc.value.reason_code == "LIVE_API_CALL_CLASS_UNKNOWN"
    with pytest.raises(ToolError):
        _breaker(tmp_path).record_success(call_class="order")
    assert not _path(tmp_path).exists()


def test_a_success_ends_only_its_own_class_s_streak(tmp_path):
    """A read that works says nothing about sends: its success must leave a send streak alone,
    even while the read class has a streak of its own to end."""
    breaker = _breaker(tmp_path)
    for _ in range(MAX_CONSECUTIVE_API_ERRORS - 1):
        _fail(breaker, API_CALL_WRITE)
    _fail(breaker, API_CALL_READ, call="fetch_order")
    stored = breaker.record_success(call_class=API_CALL_READ)
    assert stored[API_CALL_READ]["consecutive"] == 0
    assert stored[API_CALL_WRITE]["consecutive"] == MAX_CONSECUTIVE_API_ERRORS - 1
    assert _fail(breaker, API_CALL_WRITE)["just_tripped"] is True


def test_the_status_counts_the_longer_streak_of_either_class(tmp_path):
    breaker = _breaker(tmp_path)
    _fail(breaker, API_CALL_WRITE)
    for _ in range(3):
        _fail(breaker, API_CALL_READ, call="open_orders")
    assert api_breaker_status(tmp_path)["consecutive"] == 3


def test_raising_the_limit_does_not_reopen_a_tripped_breaker(tmp_path):
    """The latch is the record's stamp. A limit raised after the trip leaves it tripped until an
    operator clears it."""
    breaker = _breaker(tmp_path)
    for _ in range(MAX_CONSECUTIVE_API_ERRORS):
        _fail(breaker)
    assert api_breaker_status(tmp_path, limit=MAX_CONSECUTIVE_API_ERRORS + 2)["tripped"] is True


def test_a_streak_at_the_limit_with_no_stamp_is_not_clear(tmp_path):
    """A limit lowered since, or a record repaired by hand: the count says the door would refuse,
    so it reads tripped — and a success does not quietly erase the streak."""
    breaker = _breaker(tmp_path)
    for _ in range(3):
        _fail(breaker, API_CALL_READ, call="open_orders")
    assert api_breaker_status(tmp_path, limit=3)["tripped"] is True
    lowered = LiveApiErrorBreaker(root=tmp_path, authorization=_LIVE_AUTH, limit=3)
    lowered.record_success(call_class=API_CALL_READ)
    assert api_breaker_status(tmp_path)[API_CALL_READ]["consecutive"] == 3
    # The next failure stamps it, and says so.
    assert _fail(lowered, API_CALL_READ, call="open_orders")["just_tripped"] is True


# === the notice (review of #889: told until a message gets through) =====================

def _tripped(tmp_path):
    breaker = _breaker(tmp_path)
    for _ in range(MAX_CONSECUTIVE_API_ERRORS):
        _fail(breaker)
    return breaker


def _later(seconds):
    from runtime.mvp_runtime import timeutil

    return timeutil.plus_seconds(NOW, seconds)


def test_nothing_to_tell_while_the_breaker_is_clear(tmp_path):
    breaker = _breaker(tmp_path)
    _fail(breaker)
    assert breaker.claim_notice(at=NOW) is None


def test_the_first_claim_after_a_latch_tells_and_a_concurrent_one_does_not(tmp_path):
    breaker = _tripped(tmp_path)
    claimed = breaker.claim_notice(at=NOW)
    assert claimed["tripped_class"] == API_CALL_WRITE and claimed["limit"] == MAX_CONSECUTIVE_API_ERRORS
    assert "class    : write" in "\n".join(api_breaker_trip_lines(claimed))
    # Another pass, or the probe, a moment later: the attempt is in flight.
    assert breaker.claim_notice(at=_later(API_BREAKER_NOTICE_RETRY_SECONDS - 1)) is None


def test_a_notice_that_did_not_get_through_is_tried_again_later(tmp_path):
    breaker = _tripped(tmp_path)
    assert breaker.claim_notice(at=NOW) is not None
    retried = breaker.claim_notice(at=_later(API_BREAKER_NOTICE_RETRY_SECONDS))
    assert retried is not None
    breaker.mark_told(at=_later(API_BREAKER_NOTICE_RETRY_SECONDS + 1), tripped_at=retried["tripped_at"])
    assert api_breaker_status(tmp_path)["told_at"] == _later(API_BREAKER_NOTICE_RETRY_SECONDS + 1)
    # Told: never again for this latch, however long it stays tripped.
    assert breaker.claim_notice(at=_later(10 * API_BREAKER_NOTICE_RETRY_SECONDS)) is None


def test_an_unreadable_attempt_time_errs_toward_telling(tmp_path):
    breaker = _tripped(tmp_path)
    record = json.loads(_path(tmp_path).read_text(encoding="utf-8"))
    record["notice_attempted_at"] = "not a time"
    _path(tmp_path).write_text(json.dumps(record), encoding="utf-8")
    assert breaker.claim_notice(at=NOW) is not None


def test_a_notice_for_a_latch_cleared_since_is_not_recorded(tmp_path):
    breaker = _tripped(tmp_path)
    claimed = breaker.claim_notice(at=NOW)
    breaker.clear(actor="thomas", reason="venue answered again", at=_later(5))
    breaker.mark_told(at=_later(6), tripped_at=claimed["tripped_at"])
    status = api_breaker_status(tmp_path)
    assert status["told_at"] is None and status["notice_attempted_at"] is None
    # The next latch is told on its own.
    for _ in range(MAX_CONSECUTIVE_API_ERRORS):
        _fail(breaker, at=_later(60))
    assert breaker.claim_notice(at=_later(61)) is not None


def test_the_inert_breaker_has_nothing_to_tell_and_nothing_to_check():
    inert = DryRunLiveApiErrorBreaker()
    assert inert.claim_notice(at=NOW) is None
    assert inert.check_writable() is None
    assert inert.mark_told(at=NOW, tripped_at=NOW)["told_at"] is None


# === a breaker that cannot count ======================================================

# What writing over a directory raises: IsADirectoryError on POSIX, PermissionError on Windows.
_WRITE_REFUSED = {"IsADirectoryError", "PermissionError"}


def _unwritable(tmp_path):
    """The record stays readable; only its replacement cannot be written (a full disk, a file the
    service cannot write)."""
    breaker = _breaker(tmp_path)
    _fail(breaker)
    _path(tmp_path).with_suffix(".tmp").mkdir()
    return breaker


def test_an_unwritable_record_is_found_before_anything_is_sent(tmp_path):
    breaker = _unwritable(tmp_path)
    with pytest.raises(OSError):
        breaker.check_writable()
    heard: list = []
    wrapped = ApiErrorRecordingAdapter(_Adapter(), breaker, on_unrecorded=heard.append)
    assert wrapped.breaker_unwritable() is True
    assert len(heard) == 1 and heard[0] in _WRITE_REFUSED and wrapped.unrecorded == heard


def test_a_writable_record_is_rewritten_as_it_is(tmp_path):
    breaker = _breaker(tmp_path)
    _fail(breaker, API_CALL_READ)
    before = read_api_errors(tmp_path)
    wrapped = ApiErrorRecordingAdapter(_Adapter(), breaker)
    assert wrapped.breaker_unwritable() is False
    assert read_api_errors(tmp_path) == before


def test_a_failed_write_earlier_in_the_pass_keeps_it_unwritable(tmp_path):
    wrapped = ApiErrorRecordingAdapter(_Adapter(), _breaker(tmp_path))
    wrapped.unrecorded.append("LIVE_API_BREAKER_LOCKED")
    assert wrapped.breaker_unwritable() is True


def test_writing_refuses_without_authorization(tmp_path):
    with pytest.raises(SafetyGateBlocked):
        LiveApiErrorBreaker(root=tmp_path, authorization=None).record_success(call_class=API_CALL_READ)


def test_selection_is_inert_by_default_and_durable_behind_the_switch(tmp_path, monkeypatch):
    monkeypatch.delenv(LIVE_TRADING_ENV, raising=False)
    inert = select_live_api_breaker(now=NOW, root=tmp_path)
    assert isinstance(inert, DryRunLiveApiErrorBreaker)
    for _ in range(MAX_CONSECUTIVE_API_ERRORS):
        stored = inert.record_failure(call_class=API_CALL_WRITE, call="submit", at=NOW,
                                      reason_code="ORDER_TRANSPORT")
        assert not stored.get("just_tripped")
    assert api_breaker_status(tmp_path)["consecutive"] == 0 and not _path(tmp_path).exists()

    monkeypatch.setenv(LIVE_TRADING_ENV, "real")
    assert isinstance(select_live_api_breaker(now=NOW, root=tmp_path), LiveApiErrorBreaker)


# === what counts (decision 27) =====================================================

@pytest.mark.parametrize("error,counts", [
    (ToolError("ORDER_TRANSPORT", "timed out"), True),
    (ToolError("ORDER_TRANSPORT", "HTTP 503", data={"http_status": 503}), True),
    (ToolError("ORDER_MALFORMED_RESULT", "unparseable"), True),
    (ToolError("ORDER_OUTCOME_UNKNOWN", "unknown", data={"venue_code": -1007}), True),
    (ToolError("TOOL_TRANSPORT", "account timed out"), True),
    (ToolError("TOOL_RATE_LIMITED", "account rate limited"), True),
    (ToolError("MALFORMED_RESULT", "account unparseable"), True),
    (ToolError("NO_ORDER_API_KEY", "no key"), True),
    (ToolError("NO_API_KEY", "no account key"), True),
    (ToolError("ORDER_REJECTED", "rate limited", data={"venue_code": -1003}), True),
    (ToolError("ORDER_REJECTED", "overloaded", data={"venue_code": -1008}), True),
    (ToolError("ORDER_REJECTED", "clock", data={"venue_code": -1021}), True),
    (ToolError("ORDER_REJECTED", "signature", data={"venue_code": -1022}), True),
    (ToolError("ORDER_REJECTED", "key rejected", data={"venue_code": -2015}), True),
    (ToolError("ORDER_REJECTED", "would trigger", data={"venue_code": -2021}), False),
    (ToolError("ORDER_REJECTED", "margin", data={"venue_code": -2019}), False),
    (ToolError("ORDER_REJECTED", "duplicate id", data={"venue_code": -4116}), False),
    (ToolError("ORDER_REJECTED", "no code"), False),
    (ToolError("ORDER_REJECTED", "a bool", data={"venue_code": True}), False),
    (ToolError("MALFORMED_INTENT", "nothing sent"), False),
    (ToolError("LIVE_ORDER_SNAPSHOT_MISSING", "the venue door refused"), False),
    (RuntimeError("this process's own"), False),
], ids=lambda value: getattr(value, "reason_code", None) or (
    str(value) if isinstance(value, bool) else type(value).__name__))
def test_what_counts(error, counts):
    assert api_error_counts(error) is counts


@pytest.mark.parametrize("data,counts", [
    ({"venue_code": -1015}, True),                          # too many new orders, by its code alone
    ({"venue_code": -1016}, True),                          # the service is going down
    ({"venue_code": -1002, "http_status": 401}, True),     # not authorized
    ({"venue_code": -1011}, True),                          # this IP may not
    ({"venue_code": -1099}, True),                          # not authenticated
    ({"venue_code": -2008}, True),                          # no such key
    ({"venue_code": -2017}, True),                          # the key is locked
    ({"venue_code": -9999, "http_status": 418}, True),     # banned, whatever the code
    ({"venue_code": -9999, "http_status": 429}, True),     # rate limited, whatever the code
    ({"venue_code": -9999, "http_status": 503}, True),     # the venue's own failure
    ({"venue_code": -2021, "http_status": 400}, False),    # a business refusal stays one
    ({"venue_code": -2021, "http_status": True}, False),   # a bool is no status
], ids=["-1015", "-1016", "-1002", "-1011", "-1099", "-2008", "-2017", "418", "429", "503",
        "business-400", "bool-status"])
def test_the_status_and_the_venue_code_decide_a_refusal(data, counts):
    assert api_error_counts(ToolError("ORDER_REJECTED", "scripted", data=data)) is counts


@pytest.mark.parametrize("data,counts", [
    ({"venue_code": -1102, "http_status": 400}, False),    # a business refusal: the venue's word
    ({"venue_code": -1021, "http_status": 400}, True),     # the clock
    ({"venue_code": -2015, "http_status": 401}, True),     # the key
    ({"http_status": 502}, True),                           # a code-less gateway failure
    (None, True),                                           # could not reach the venue at all
], ids=["business", "clock", "key", "gateway", "unreachable"])
def test_an_account_transport_error_is_judged_by_what_the_venue_said(data, counts):
    """The account feed reports every HTTP failure as TOOL_TRANSPORT; the venue's own code, where
    it answered with one, says whether that was the venue failing or the request refused."""
    assert api_error_counts(ToolError("TOOL_TRANSPORT", "scripted", data=data)) is counts


@pytest.mark.parametrize("error", [
    http.client.RemoteDisconnected("Remote end closed connection without response"),
    ConnectionResetError(104, "Connection reset by peer"),
    http.client.IncompleteRead(b"par", 10),
], ids=["disconnected", "reset", "cut-short"])
def test_a_transport_failure_that_escaped_untyped_counts(error):
    assert api_error_counts(error) is True


# === the recording adapter ============================================================

class _Recorder:
    """Stands in for the durable breaker and remembers what it was told."""

    def __init__(self, *, raises=None, trip_on=None):
        self.calls: list = []
        self._raises = raises
        self._trip_on = trip_on

    def record_failure(self, **kw):
        if self._raises is not None:
            raise self._raises
        self.calls.append(("failure", kw))
        failures = sum(1 for kind, _ in self.calls if kind == "failure")
        return {"just_tripped": failures == self._trip_on, "tripped_class": kw["call_class"]}

    def record_success(self, *, call_class):
        if self._raises is not None:
            raise self._raises
        self.calls.append(("success", call_class))
        return {}


class _Adapter:
    tool_id = "fake.adapter"
    network_egress = True

    def __init__(self, error=None):
        self.error = error

    def _answer(self, name, *args):
        if self.error is not None:
            raise self.error
        return {"call": name, "args": list(args)}

    def submit(self, order_request, *, timeout_seconds=10):
        return self._answer("submit", order_request)

    def cancel_order(self, symbol, client_order_id, *, timeout_seconds=10, algo=False):
        return self._answer("cancel_order", symbol, client_order_id)

    def fetch_order(self, symbol, client_order_id, *, timeout_seconds=10, algo=False):
        return self._answer("fetch_order", symbol, client_order_id)

    def open_orders(self, symbol=None, *, timeout_seconds=10):
        return self._answer("open_orders", symbol)

    def algo_open_orders(self, symbol=None, *, timeout_seconds=10):
        return self._answer("algo_open_orders", symbol)

    def validate_order(self, order_request, *, timeout_seconds=10):
        return self._answer("validate_order", order_request)


def test_the_roster_names_every_signed_call_the_leg_makes():
    """A call the roster does not name is not counted at all. The five the leg and the probe make
    are, each in its class — so the day a new one is added, this is where it is decided."""
    assert API_ADAPTER_CALLS == {
        "submit": API_CALL_WRITE, "cancel_order": API_CALL_WRITE,
        "fetch_order": API_CALL_READ, "open_orders": API_CALL_READ, "algo_open_orders": API_CALL_READ,
    }


def test_every_signed_call_the_doors_make_is_on_the_roster():
    """The roster counts only what it names, so a door that starts making a new signed call must
    put it here (or say why it does not count). Read off the doors' own code, not the constant:
    the constant alone cannot notice a call it never heard of (review of #889)."""
    import ast
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    doors = ("runtime/mvp_runtime/crypto/live_route.py", "runtime/mvp_runtime/crypto/live_leg.py",
             "runtime/mvp_runtime/crypto/live_execution.py", "scripts/run_slippage_probe.py")
    called: set[str] = set()
    for rel in doors:
        for node in ast.walk(ast.parse((repo / rel).read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "adapter"):
                called.add(node.func.attr)
    wrapper_own = {"record_account", "recording", "breaker_unwritable"}
    assert called - wrapper_own == set(API_ADAPTER_CALLS), sorted(called ^ set(API_ADAPTER_CALLS))
    assert wrapper_own <= set(vars(ApiErrorRecordingAdapter))


def test_the_wrapper_reads_everything_through_and_counts_only_the_roster():
    recorder = _Recorder()
    wrapped = ApiErrorRecordingAdapter(_Adapter(), recorder)
    assert wrapped.tool_id == "fake.adapter"
    assert getattr(wrapped, "network_egress", False) is True
    assert wrapped.validate_order({"symbol": "BTCUSDT"}) == {"call": "validate_order",
                                                            "args": [{"symbol": "BTCUSDT"}]}
    assert recorder.calls == []
    with pytest.raises(AttributeError):
        wrapped.place_anything  # noqa: B018 — nothing the adapter lacks appears on the wrapper


@pytest.mark.parametrize("call,args,call_class", [
    ("submit", ({"symbol": "BTCUSDT"},), API_CALL_WRITE),
    ("cancel_order", ("BTCUSDT", "cid"), API_CALL_WRITE),
    ("fetch_order", ("BTCUSDT", "cid"), API_CALL_READ),
    ("open_orders", ("BTCUSDT",), API_CALL_READ),
    ("algo_open_orders", ("BTCUSDT",), API_CALL_READ),
])
def test_each_signed_call_is_a_success_of_its_class(call, args, call_class):
    recorder = _Recorder()
    wrapped = ApiErrorRecordingAdapter(_Adapter(), recorder)
    assert getattr(wrapped, call)(*args, timeout_seconds=3) == {"call": call, "args": list(args)}
    assert recorder.calls == [("success", call_class)]


def test_a_failure_that_counts_is_recorded_and_raised_unchanged():
    error = ToolError("ORDER_REJECTED", "venue rejected the order (code -1003)", data={"venue_code": -1003})
    recorder = _Recorder()
    wrapped = ApiErrorRecordingAdapter(_Adapter(error), recorder, now=lambda: NOW)
    with pytest.raises(ToolError) as exc:
        wrapped.cancel_order("BTCUSDT", "cid")
    assert exc.value is error
    assert recorder.calls == [("failure", {
        "call_class": API_CALL_WRITE, "call": "cancel_order", "at": NOW,
        "reason_code": "ORDER_REJECTED", "venue_code": -1003,
    })]


@pytest.mark.parametrize("error", [
    ToolError("ORDER_REJECTED", "would trigger", data={"venue_code": -2021}),
    ToolError("MALFORMED_INTENT", "nothing sent"),
    RuntimeError("this process's own"),
], ids=["business-rejection", "nothing-sent", "own-error"])
def test_a_failure_that_does_not_count_is_neither_a_failure_nor_a_success(error):
    """Decision 27 ends a streak on a success only. A refusal the venue answered is not one."""
    recorder = _Recorder()
    wrapped = ApiErrorRecordingAdapter(_Adapter(error), recorder)
    with pytest.raises(type(error)) as exc:
        wrapped.fetch_order("BTCUSDT", "cid")
    assert exc.value is error
    assert recorder.calls == []


@pytest.mark.parametrize("error", [
    ToolError("LIVE_API_BREAKER_LOCKED", "held by another process"),
    OSError(28, "No space left on device"),
], ids=["locked", "disk-full"])
def test_a_breaker_that_cannot_be_written_never_changes_the_calls_answer(error):
    heard: list = []
    code = getattr(error, "reason_code", type(error).__name__)
    answered = ApiErrorRecordingAdapter(_Adapter(), _Recorder(raises=error), on_unrecorded=heard.append)
    assert answered.submit({"symbol": "BTCUSDT"}) == {"call": "submit", "args": [{"symbol": "BTCUSDT"}]}
    refused = ApiErrorRecordingAdapter(_Adapter(ToolError("ORDER_TRANSPORT", "timed out")),
                                       _Recorder(raises=error), on_unrecorded=heard.append)
    with pytest.raises(ToolError) as exc:
        refused.submit({"symbol": "BTCUSDT"})
    assert exc.value.reason_code == "ORDER_TRANSPORT"
    assert answered.unrecorded == [code] and refused.unrecorded == [code]
    assert heard == [code, code]


def test_the_call_that_latches_it_is_reported_once():
    tripped: list = []
    wrapped = ApiErrorRecordingAdapter(_Adapter(ToolError("ORDER_TRANSPORT", "timed out")),
                                       _Recorder(trip_on=2), on_trip=tripped.append)
    for _ in range(3):
        with pytest.raises(ToolError):
            wrapped.submit({})
    assert wrapped.tripped == {"just_tripped": True, "tripped_class": API_CALL_WRITE}
    assert tripped == [wrapped.tripped]


def test_the_wrapper_tells_once_even_if_the_store_says_it_twice():
    """Once per wrapper is the wrapper's own promise, not only the store's."""
    class _Twice(_Recorder):
        def record_failure(self, **kw):
            super().record_failure(**kw)
            return {"just_tripped": True, "tripped_class": kw["call_class"], "seen": len(self.calls)}

    told: list = []
    wrapped = ApiErrorRecordingAdapter(_Adapter(ToolError("ORDER_TRANSPORT", "timed out")), _Twice(),
                                       on_trip=told.append)
    for _ in range(2):
        with pytest.raises(ToolError):
            wrapped.submit({})
    assert len(told) == 1 and wrapped.tripped["seen"] == 1


def test_a_report_that_fails_does_not_become_the_calls_failure():
    def _broken(_value):
        raise RuntimeError("the report broke")

    wrapped = ApiErrorRecordingAdapter(_Adapter(ToolError("ORDER_TRANSPORT", "timed out")),
                                       _Recorder(trip_on=1), on_trip=_broken, on_unrecorded=_broken)
    with pytest.raises(ToolError) as exc:
        wrapped.submit({})
    assert exc.value.reason_code == "ORDER_TRANSPORT"
    assert wrapped.tripped is not None


def test_the_wrapper_counts_on_the_real_breaker(tmp_path):
    wrapped = ApiErrorRecordingAdapter(_Adapter(ToolError("ORDER_OUTCOME_UNKNOWN", "unknown",
                                                          data={"venue_code": -1007})),
                                       _breaker(tmp_path), now=lambda: NOW)
    for _ in range(MAX_CONSECUTIVE_API_ERRORS):
        with pytest.raises(ToolError):
            wrapped.submit({})
    assert wrapped.tripped["tripped_class"] == API_CALL_WRITE
    assert wrapped.tripped[API_CALL_WRITE]["last_venue_code"] == -1007
    assert api_breaker_status(tmp_path)["tripped"] is True


def test_a_copied_wrapper_neither_recurses_nor_loses_its_record(tmp_path):
    import copy

    wrapped = ApiErrorRecordingAdapter(_Adapter(ToolError("ORDER_TRANSPORT", "timed out")), _breaker(tmp_path))
    twin = copy.copy(wrapped)
    with pytest.raises(ToolError):
        twin.submit({})
    assert api_breaker_status(tmp_path)[API_CALL_WRITE]["consecutive"] == 1


# === the account feed's fill history ===================================================

class _Feed:
    """A capable account feed: the fill history a settlement falls back to is a signed read."""

    feed_id = "binance"
    network_egress = True

    def __init__(self, error=None):
        self.error = error

    def fill_history(self, symbol, *, start_ms, timeout_seconds):
        if self.error is not None:
            raise self.error
        return [{"symbol": symbol}]

    def account_snapshot(self, *, timeout_seconds):
        return "snapshot"


def test_the_fill_history_is_a_read_of_the_same_record():
    recorder = _Recorder(trip_on=1)
    wrapped = ApiErrorRecordingAdapter(_Adapter(), recorder, now=lambda: NOW)
    feed = wrapped.recording(_Feed())
    assert feed.feed_id == "binance" and feed.network_egress is True
    assert feed.fill_history("BTCUSDT", start_ms=0, timeout_seconds=3) == [{"symbol": "BTCUSDT"}]
    # Not on the roster: the account snapshot is counted by its caller, off the feed's own record.
    assert feed.account_snapshot(timeout_seconds=3) == "snapshot"
    assert recorder.calls == [("success", API_CALL_READ)]

    failing = wrapped.recording(_Feed(ToolError("TOOL_TRANSPORT", "timed out")))
    with pytest.raises(ToolError):
        failing.fill_history("BTCUSDT", start_ms=0, timeout_seconds=3)
    assert recorder.calls[-1] == ("failure", {"call_class": API_CALL_READ, "call": "fill_history",
                                              "at": NOW, "reason_code": "TOOL_TRANSPORT", "venue_code": None})
    # One record: the latch the feed's call caused is the wrapper's.
    assert wrapped.tripped == {"just_tripped": True, "tripped_class": API_CALL_READ}


def test_an_inert_feed_is_handed_back_as_it_is():
    """It asks the venue nothing, so its answer must not end a streak."""
    inert = account_mod.NoAccountFeed()
    recorder = _Recorder()
    assert ApiErrorRecordingAdapter(_Adapter(), recorder).recording(inert) is inert
    assert ApiErrorRecordingAdapter(_Adapter(), recorder).recording(None) is None
    assert recorder.calls == []


def test_a_bare_adapter_records_no_feed():
    feed = _Feed()
    assert recorded_like(_Adapter(), feed) is feed
    wrapped = ApiErrorRecordingAdapter(_Adapter(), _Recorder())
    assert recorded_like(wrapped, feed) is not feed


# === the account read ================================================================

@pytest.mark.parametrize("readable,reason_code,expected", [
    (True, None, [("success", API_CALL_READ)]),
    (False, "TOOL_TRANSPORT", [("failure", "TOOL_TRANSPORT")]),
    (False, "TOOL_RATE_LIMITED", [("failure", "TOOL_RATE_LIMITED")]),
    (False, "MALFORMED_RESULT", [("failure", "MALFORMED_RESULT")]),
    (False, "NO_API_KEY", [("failure", "NO_API_KEY")]),
    (False, None, []),
    (False, account_mod.ACCOUNT_DATA_DEGRADED, []),
], ids=["readable", "transport", "rate-limited", "malformed", "no-key", "no-feed", "summary-word"])
def test_the_account_read_is_counted_by_the_feeds_own_code(readable, reason_code, expected):
    """`read_account` degrades rather than raising, so its record is the evidence. No feed at all
    asked nothing; ACCOUNT_DATA_DEGRADED is the record's summary of every failure, not a reason."""
    recorder = _Recorder()
    record_account_read(recorder, readable=readable, reason_code=reason_code, at=NOW)
    assert [(kind, detail if kind == "success" else detail["reason_code"])
            for kind, detail in recorder.calls] == expected
    if expected and expected[0][0] == "failure":
        assert recorder.calls[0][1]["call"] == "read_account"
        assert recorder.calls[0][1]["call_class"] == API_CALL_READ


def test_the_wrapper_counts_the_account_read_with_its_own_calls(tmp_path):
    wrapped = ApiErrorRecordingAdapter(_Adapter(), _breaker(tmp_path), now=lambda: NOW)
    for _ in range(MAX_CONSECUTIVE_API_ERRORS):
        wrapped.record_account(readable=False, reason_code="TOOL_TRANSPORT")
    assert wrapped.tripped["tripped_class"] == API_CALL_READ
    assert wrapped.tripped[API_CALL_READ]["last_call"] == "read_account"


def test_a_rate_limited_account_read_says_so(monkeypatch):
    """"Come back later" and "could not reach the venue" both count, but the operator reads them
    differently — the account read now tells them apart, as the market-data reads already did."""
    monkeypatch.setenv(account_mod.ACCOUNT_API_KEY_ENV, "test-key")
    monkeypatch.setenv(account_mod.ACCOUNT_API_SECRET_ENV, "test-secret")

    def _limited(request, timeout):
        raise urllib.error.HTTPError("https://redacted?signature=deadbeef", 429, "Too Many Requests",
                                     {"Retry-After": "120"}, io.BytesIO(b""))

    monkeypatch.setattr(account_mod.urllib.request, "urlopen", _limited)
    feed = account_mod.BinanceFuturesAccountFeed(authorization=make_gate_authorization(
        flags=account_mod._NETWORK_FLAGS, provider_id=account_mod.BINANCE_ACCOUNT))
    with pytest.raises(ToolError) as exc:
        feed.account_snapshot(timeout_seconds=1)
    assert exc.value.reason_code == "TOOL_RATE_LIMITED"
    assert "120" in exc.value.reason and "deadbeef" not in exc.value.reason
    assert exc.value.data == {"http_status": 429}
    assert api_error_counts(exc.value) is True


# === what the order adapter carries (PR2d-1) ============================================

def _venue_error(code, msg="scripted", *, status=400):
    body = json.dumps({"code": code, "msg": msg}).encode("utf-8")
    return urllib.error.HTTPError("https://redacted", status, msg, {}, io.BytesIO(body))


@pytest.fixture
def order_adapter(monkeypatch):
    monkeypatch.setenv(lx.ORDER_API_KEY_ENV, "test-key")
    monkeypatch.setenv(lx.ORDER_API_SECRET_ENV, "test-secret")
    return lx.BinanceFuturesOrderAdapter(authorization=make_gate_authorization(
        flags=LIVE_TRADING_FLAGS, provider_id=LIVE_TRADING_PROVIDER_ID))


def _raising(monkeypatch, error):
    def _urlopen(*_a, **_k):
        raise error

    monkeypatch.setattr(lx.urllib.request, "urlopen", _urlopen)


@pytest.mark.parametrize("call,args", [
    ("submit", ({"symbol": "BTCUSDT", "side": "BUY", "type": "MARKET", "quantity": "0.001",
                 "newClientOrderId": "TAI_X"},)),
    ("fetch_order", ("BTCUSDT", "TAI_X")),
    ("open_orders", ("BTCUSDT",)),
    ("algo_open_orders", ("BTCUSDT",)),
    ("cancel_order", ("BTCUSDT", "TAI_X")),
])
@pytest.mark.parametrize("code,counts", [(-1003, True), (-2015, True), (-2019, False)],
                         ids=["rate-limited", "key-rejected", "business"])
def test_every_venue_refusal_carries_the_venues_own_code(monkeypatch, order_adapter, call, args,
                                                         code, counts):
    """The breaker reads the venue's code off `data` rather than parsing a message, so every
    refusal the adapter raises has to carry it."""
    _raising(monkeypatch, _venue_error(code))
    with pytest.raises(ToolError) as exc:
        getattr(order_adapter, call)(*args)
    assert exc.value.data == {"venue_code": code, "http_status": 400}
    assert api_error_counts(exc.value) is counts


def test_an_unknown_outcome_carries_its_code_and_counts(monkeypatch, order_adapter):
    _raising(monkeypatch, _venue_error(-1007, "Timeout waiting for response from backend server."))
    with pytest.raises(ToolError) as exc:
        order_adapter.submit({"symbol": "BTCUSDT", "side": "BUY", "type": "MARKET",
                              "quantity": "0.001", "newClientOrderId": "TAI_X"})
    assert exc.value.reason_code == lx.ORDER_OUTCOME_UNKNOWN
    assert exc.value.data == {"venue_code": -1007, "http_status": 400} and api_error_counts(exc.value) is True


def test_a_duplicate_id_carries_its_code_and_does_not_count(monkeypatch, order_adapter):
    """Idempotency working as designed: the venue is answering, the original landed."""
    _raising(monkeypatch, _venue_error(-4116, "ClientOrderId is duplicated."))
    with pytest.raises(ToolError) as exc:
        order_adapter.submit({"symbol": "BTCUSDT", "side": "BUY", "type": "MARKET",
                              "quantity": "0.001", "newClientOrderId": "TAI_X"})
    assert exc.value.data == {"venue_code": -4116, "http_status": 400} and api_error_counts(exc.value) is False


def test_a_rate_limited_send_counts_whatever_code_rides_with_it(monkeypatch, order_adapter):
    """A 429 with -1015 (too many new orders) read as a business rejection before the review of
    #889. The status travels with every refusal now."""
    _raising(monkeypatch, _venue_error(-1015, "Too many new orders.", status=429))
    with pytest.raises(ToolError) as exc:
        order_adapter.submit({"symbol": "BTCUSDT", "side": "BUY", "type": "MARKET",
                              "quantity": "0.001", "newClientOrderId": "TAI_X"})
    assert exc.value.data == {"venue_code": -1015, "http_status": 429}
    assert api_error_counts(exc.value) is True


class _CutShort:
    """A response whose body the venue cut off mid-read."""

    def __init__(self, error):
        self._error = error

    def read(self):
        raise self._error

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.mark.parametrize("error,at_read", [
    (http.client.RemoteDisconnected("Remote end closed connection without response"), False),
    (ConnectionResetError(104, "Connection reset by peer"), False),
    (http.client.BadStatusLine("garbage"), False),
    (http.client.IncompleteRead(b"par", 10), True),
    (ConnectionResetError(104, "Connection reset by peer"), True),
], ids=["disconnected", "reset", "bad-status", "cut-short", "reset-mid-read"])
def test_a_dropped_connection_is_a_typed_transport_failure(monkeypatch, order_adapter, error, at_read):
    """These leave `urlopen` unwrapped: the venue took the request and dropped the answer, so the
    outcome is unknown — the same fact as a timeout (review of #889)."""
    if at_read:
        monkeypatch.setattr(lx.urllib.request, "urlopen", lambda *a, **k: _CutShort(error))
    else:
        _raising(monkeypatch, error)
    with pytest.raises(ToolError) as exc:
        order_adapter.submit({"symbol": "BTCUSDT", "side": "BUY", "type": "MARKET",
                              "quantity": "0.001", "newClientOrderId": "TAI_X"})
    assert exc.value.reason_code == lx.ORDER_TRANSPORT and api_error_counts(exc.value) is True


def test_an_undecodable_answer_is_a_malformed_one(monkeypatch, order_adapter):
    class _Latin:
        def read(self):
            return b"\xff\xfe not utf-8"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(lx.urllib.request, "urlopen", lambda *a, **k: _Latin())
    with pytest.raises(ToolError) as exc:
        order_adapter.fetch_order("BTCUSDT", "TAI_X")
    assert exc.value.reason_code == lx.ORDER_MALFORMED_RESULT and api_error_counts(exc.value) is True


def _account_feed(monkeypatch):
    monkeypatch.setenv(account_mod.ACCOUNT_API_KEY_ENV, "test-key")
    monkeypatch.setenv(account_mod.ACCOUNT_API_SECRET_ENV, "test-secret")
    return account_mod.BinanceFuturesAccountFeed(authorization=make_gate_authorization(
        flags=account_mod._NETWORK_FLAGS, provider_id=account_mod.BINANCE_ACCOUNT))


@pytest.mark.parametrize("error", [
    http.client.RemoteDisconnected("Remote end closed connection without response"),
    ConnectionResetError(104, "Connection reset by peer"),
], ids=["disconnected", "reset"])
def test_a_dropped_account_read_degrades_instead_of_escaping(monkeypatch, error):
    """Before the review of #889 these escaped `read_account`, which catches only a ToolError, and
    took the whole pass down before settle and protect."""
    feed = _account_feed(monkeypatch)
    _raising_account(monkeypatch, error)
    monkeypatch.setattr(account_mod, "select_account_feed", lambda **kw: feed)
    snapshot, record = account_mod.read_account(timeout_seconds=1)
    assert snapshot is None and record["error_reason_code"] == "TOOL_TRANSPORT"


def _raising_account(monkeypatch, error):
    def _urlopen(*_a, **_k):
        raise error

    monkeypatch.setattr(account_mod.urllib.request, "urlopen", _urlopen)


@pytest.mark.parametrize("status,code,counts", [
    (400, -1102, False),     # a business refusal (a parameter): the venue answered
    (400, -1021, True),      # the clock
    (401, -2015, True),      # the key
], ids=["business", "clock", "key"])
def test_an_account_refusal_carries_what_the_venue_said(monkeypatch, status, code, counts):
    feed = _account_feed(monkeypatch)
    _raising_account(monkeypatch, _venue_error(code, status=status))
    with pytest.raises(ToolError) as exc:
        feed.fill_history("BTCUSDT", start_ms=0, timeout_seconds=1)
    assert exc.value.reason_code == "TOOL_TRANSPORT"
    assert exc.value.data == {"http_status": status, "venue_code": code}
    assert api_error_counts(exc.value) is counts


def test_a_code_less_http_failure_carries_its_status_and_counts(monkeypatch, order_adapter):
    _raising(monkeypatch, urllib.error.HTTPError("https://redacted", 502, "Bad Gateway", {},
                                                 io.BytesIO(b"<html>")))
    with pytest.raises(ToolError) as exc:
        order_adapter.fetch_order("BTCUSDT", "TAI_X")
    assert exc.value.reason_code == lx.ORDER_TRANSPORT
    assert exc.value.data == {"http_status": 502} and api_error_counts(exc.value) is True


def test_an_order_that_does_not_exist_is_an_answer_not_a_failure(monkeypatch, order_adapter):
    """The venue's truthful "no such order" is a normal answer: a success of the read class."""
    _raising(monkeypatch, _venue_error(-2013, "Order does not exist."))
    recorder = _Recorder()
    assert ApiErrorRecordingAdapter(order_adapter, recorder).fetch_order("BTCUSDT", "TAI_X") is None
    assert recorder.calls == [("success", API_CALL_READ)]


# === the operator's message =========================================================

def test_the_trip_message_names_the_class_the_call_and_the_way_back(tmp_path):
    breaker = _breaker(tmp_path)
    for _ in range(MAX_CONSECUTIVE_API_ERRORS - 1):
        _fail(breaker, API_CALL_READ, call="open_orders", reason_code="TOOL_RATE_LIMITED")
    stored = _fail(breaker, API_CALL_READ, call="fetch_order", reason_code="ORDER_REJECTED",
                   venue_code=-1003)
    text = "\n".join(api_breaker_trip_lines(stored))
    assert f"class    : read ({MAX_CONSECUTIVE_API_ERRORS}/{MAX_CONSECUTIVE_API_ERRORS} in a row)" in text
    assert "last     : fetch_order ORDER_REJECTED (venue -1003)" in text
    assert f"at       : {NOW}" in text
    assert "Closing, settling and" in text and "scripts.clear_api_breaker" in text
