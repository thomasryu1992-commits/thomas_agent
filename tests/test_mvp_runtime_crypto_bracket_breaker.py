"""The bracket-failure breaker: the count that stops the naked-entry loop.

Why this exists at all is worth stating, because every individual event it counts is *handled*
and looks fine. On 2026-08-02 the runtime placed its first two autonomous live entries. Both
filled, both protective `STOP_MARKET closePosition` legs came back `ORDER_REJECTED`, and
`live_leg` closed each position again — correctly, safely, costing 0.12 USDT in fees. Then the
next signal arrived and was treated exactly like the first, because nothing counted the first
two. The loop *signal -> fill -> refuse -> close* was bounded only by the daily order budget,
which refills every UTC midnight.

So the tests that matter here are the ones about what does NOT reset the count.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.crypto import live_leg, live_route
from runtime.mvp_runtime.crypto.live_order import (
    BRACKET_BREAKER_FILENAME,
    MAX_CONSECUTIVE_BRACKET_FAILURES,
    DryRunLiveBracketFailureBreaker,
    LiveBracketFailureBreaker,
    bracket_breaker_status,
    read_bracket_failures,
    select_live_bracket_breaker,
)
from runtime.mvp_runtime.crypto.live_pnl import (
    LIVE_TRADING_ENV,
    LIVE_TRADING_FLAGS,
    LIVE_TRADING_PROVIDER_ID,
    state_dir,
)
from runtime.mvp_runtime.errors import SafetyGateBlocked, ToolError
from runtime.mvp_runtime.safety_gate import Authorization

NOW = "2026-08-02T08:20:44Z"

_LIVE_AUTH = Authorization(
    flags=LIVE_TRADING_FLAGS, provider_id=LIVE_TRADING_PROVIDER_ID, activation_sha256="sha256:test",
    expires_at="2999-01-01T00:00:00Z", evidence_ref=".runtime_governance_state/evidence.md",
)


def _breaker(root):
    return LiveBracketFailureBreaker(root=root, authorization=_LIVE_AUTH)


def _fail(breaker, *, at=NOW, symbol="ETHUSDT"):
    return breaker.record_failure(
        symbol=symbol, status=live_leg.ENTRY_NAKED_CLOSED, at=at,
        reason_codes=[live_leg.BRACKET_FAILED, live_leg.NAKED_POSITION_CLOSED],
        error_detail=[{"leg": "stop", "error": "ORDER_REJECTED", "error_detail": "-2021 ..."}],
    )


# === the store =====================================================================

def test_starts_empty(tmp_path):
    assert read_bracket_failures(tmp_path)["consecutive"] == 0
    assert bracket_breaker_status(tmp_path)["tripped"] is False


def test_failures_accumulate_and_carry_what_the_venue_said(tmp_path):
    record = _fail(_breaker(tmp_path))
    assert record["consecutive"] == 1
    assert record["last_symbol"] == "ETHUSDT"
    assert record["last_status"] == live_leg.ENTRY_NAKED_CLOSED
    assert live_leg.BRACKET_FAILED in record["last_reason_codes"]
    # The venue's own numeric code, kept because the container holding the logs is the first
    # thing to disappear — which is exactly what made the original two uninvestigable.
    assert record["last_error_detail"][0]["error_detail"] == "-2021 ..."


def test_trips_at_the_limit(tmp_path):
    breaker = _breaker(tmp_path)
    for _ in range(MAX_CONSECUTIVE_BRACKET_FAILURES):
        _fail(breaker)
    status = bracket_breaker_status(tmp_path)
    assert status["consecutive"] == MAX_CONSECUTIVE_BRACKET_FAILURES
    assert status["tripped"] is True


def test_a_resting_bracket_ends_the_streak(tmp_path):
    breaker = _breaker(tmp_path)
    _fail(breaker)
    record = breaker.record_success()
    assert record["consecutive"] == 0
    assert record["total"] == 1  # the history is not rewritten, only the streak


def test_the_streak_does_not_reset_at_midnight(tmp_path):
    """The point of the whole increment. The daily order budget refills at 00:00 UTC and that
    refill is what let the loop resume; a breaker expiring on the same clock as the budget it
    bounds would bound nothing."""
    breaker = _breaker(tmp_path)
    # The streak is built to the limit ACROSS the boundary, never to a hardcoded count: the fact
    # under test is that midnight does not reset it, not what the limit happens to be today.
    for _ in range(MAX_CONSECUTIVE_BRACKET_FAILURES - 1):
        _fail(breaker, at="2026-08-02T23:59:00Z")
    _fail(breaker, at="2026-08-03T00:01:00Z")
    assert bracket_breaker_status(tmp_path)["tripped"] is True


def test_the_operator_clear_records_who_and_why(tmp_path):
    breaker = _breaker(tmp_path)
    _fail(breaker)
    _fail(breaker)
    record = breaker.clear(actor="thomas", reason="venue -2021, stop distance fixed", at=NOW)
    assert record["consecutive"] == 0
    assert record["cleared_by"] == "thomas"
    assert record["cleared_reason"] == "venue -2021, stop distance fixed"
    assert bracket_breaker_status(tmp_path)["tripped"] is False


def test_the_record_survives_a_reread(tmp_path):
    _fail(_breaker(tmp_path))
    stored = json.loads((state_dir(tmp_path) / BRACKET_BREAKER_FILENAME).read_text(encoding="utf-8"))
    assert stored["consecutive"] == 1
    assert read_bracket_failures(tmp_path)["consecutive"] == 1


def test_an_unreadable_record_fails_closed(tmp_path):
    """`count_today`'s rule pointed the other way: reading zero from a corrupt file would
    reopen the door this exists to hold shut."""
    target = state_dir(tmp_path)
    target.mkdir(parents=True, exist_ok=True)
    (target / BRACKET_BREAKER_FILENAME).write_text("nope", encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        read_bracket_failures(tmp_path)
    assert exc.value.reason_code == "LIVE_BRACKET_BREAKER_UNREADABLE"


def test_a_non_integer_count_fails_closed(tmp_path):
    target = state_dir(tmp_path)
    target.mkdir(parents=True, exist_ok=True)
    (target / BRACKET_BREAKER_FILENAME).write_text('{"consecutive": "two"}', encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        read_bracket_failures(tmp_path)
    assert exc.value.reason_code == "LIVE_BRACKET_BREAKER_UNREADABLE"


def test_writing_refuses_without_authorization(tmp_path):
    with pytest.raises(SafetyGateBlocked):
        LiveBracketFailureBreaker(root=tmp_path, authorization=None).record_success()


def test_selection_is_inert_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv(LIVE_TRADING_ENV, raising=False)
    breaker = select_live_bracket_breaker(now=NOW, root=tmp_path)
    assert isinstance(breaker, DryRunLiveBracketFailureBreaker)
    breaker.record_failure(symbol="ETHUSDT", status=live_leg.ENTRY_NAKED_CLOSED, at=NOW)
    assert read_bracket_failures(tmp_path)["consecutive"] == 0


# === what the leg tells it =========================================================

class _Recorder:
    """Stands in for the durable breaker: tests can hold no real grant."""

    def __init__(self, raises: Exception | None = None):
        self.calls: list[str] = []
        self._raises = raises

    def record_failure(self, **kwargs):
        if self._raises:
            raise self._raises
        self.calls.append("failure")
        self.kwargs = kwargs

    def record_success(self):
        if self._raises:
            raise self._raises
        self.calls.append("success")


def _outcome(monkeypatch, entry, *, raises=None):
    recorder = _Recorder(raises)
    monkeypatch.setattr(live_route, "select_live_bracket_breaker", lambda **kw: recorder)
    record: dict = {"live_reason_codes": []}
    live_route._record_bracket_outcome(record, entry, symbol="ETHUSDT", now=NOW, root=None)
    return recorder, record


def test_a_naked_close_is_counted(monkeypatch):
    recorder, _ = _outcome(monkeypatch, {
        "status": live_leg.ENTRY_NAKED_CLOSED,
        "reason_codes": [live_leg.BRACKET_FAILED],
        "bracket": [{"leg": "stop", "error": "ORDER_REJECTED", "error_detail": "-2021"}],
    })
    assert recorder.calls == ["failure"]
    assert recorder.kwargs["error_detail"] == [{
        "leg": "stop", "order_type": None, "placed": None, "status": None,
        "error": "ORDER_REJECTED", "error_detail": "-2021",
    }]


def test_a_leg_that_never_rested_is_described_even_though_it_said_nothing(monkeypatch):
    """The real 2026-08-03T04:28:58Z shape, and the failure this function had.

    A rejection talks (`error_detail`, #426). A leg that simply never came to rest does not:
    no error, no exchange id, just a status that is not NEW. Reading only `error` recorded
    `last_error_detail: null` for that entry — counted, and mute about why."""
    recorder, _ = _outcome(monkeypatch, {
        "status": live_leg.ENTRY_NAKED_CLOSED,
        "reason_codes": [live_leg.BRACKET_FAILED],
        "bracket": [
            {"leg": "BUY", "order_type": "STOP_MARKET", "placed": False, "status": "NOT_FOUND",
             "error": None, "error_detail": None, "exchange_order_id": None},
            {"leg": "BUY", "order_type": "LIMIT", "placed": True, "status": "NEW",
             "error": None, "error_detail": None, "exchange_order_id": "8389766247673980036"},
        ],
    })
    detail = recorder.kwargs["error_detail"]
    assert detail is not None, "a silent failure must still be described"
    # Only the leg that failed. The take-profit rested, so it is not part of the finding.
    assert len(detail) == 1
    assert detail[0]["order_type"] == "STOP_MARKET"
    assert detail[0]["status"] == "NOT_FOUND"
    assert detail[0]["placed"] is False


def test_a_fully_rested_bracket_describes_nothing(monkeypatch):
    """The inverse pin: membership is decided by the status, so a bracket whose legs all rested
    must not start appearing in the record as though something had gone wrong."""
    recorder, _ = _outcome(monkeypatch, {
        "status": live_leg.ENTRY_NAKED_CLOSED,
        "reason_codes": [live_leg.BRACKET_FAILED],
        "bracket": [
            {"leg": "BUY", "order_type": "STOP_MARKET", "status": "NEW", "error": None},
            {"leg": "BUY", "order_type": "LIMIT", "status": "NEW", "error": None},
        ],
    })
    assert recorder.kwargs["error_detail"] is None


def test_a_naked_open_is_counted(monkeypatch):
    """The close failed too. Already an incident and a halt — but the halt is per-cycle and
    this count is what persists past it."""
    recorder, _ = _outcome(monkeypatch, {
        "status": live_leg.ENTRY_NAKED_OPEN, "reason_codes": [live_leg.NAKED_CLOSE_FAILED],
    })
    assert recorder.calls == ["failure"]


def test_a_bracketed_entry_clears_the_streak(monkeypatch):
    recorder, _ = _outcome(monkeypatch, {"status": live_leg.ENTRY_OPENED, "reason_codes": []})
    assert recorder.calls == ["success"]


@pytest.mark.parametrize("status", [live_leg.ENTRY_REFUSED, live_leg.ENTRY_NOT_CONFIRMED])
def test_an_entry_that_never_reached_the_bracket_says_nothing(monkeypatch, status):
    """The one that would quietly disarm the breaker. No bracket was attempted, so neither
    outcome tested the protective path — and a refusal must not clear a streak it never met."""
    recorder, _ = _outcome(monkeypatch, {"status": status, "reason_codes": []})
    assert recorder.calls == []


def test_an_unreadable_record_blocks_the_entry_path(tmp_path):
    """`ToolError` is an `MvpRuntimeError`, so `run_live_leg` reports BLOCKED and sends nothing.
    Pinned because the value of failing closed here is entirely in which except clause catches
    it: the broad one below would mark the cycle an INCIDENT and halt the whole fan-out."""
    from runtime.mvp_runtime.errors import MvpRuntimeError

    target = state_dir(tmp_path)
    target.mkdir(parents=True, exist_ok=True)
    (target / BRACKET_BREAKER_FILENAME).write_text("nope", encoding="utf-8")
    with pytest.raises(MvpRuntimeError) as exc:
        bracket_breaker_status(tmp_path)
    assert exc.value.reason_code == "LIVE_BRACKET_BREAKER_UNREADABLE"


def test_the_readiness_board_reports_an_unreadable_record_as_failed(tmp_path):
    """It must not crash the board, and it must not read as clear."""
    from runtime.mvp_runtime.crypto import live_readiness

    target = state_dir(tmp_path)
    target.mkdir(parents=True, exist_ok=True)
    (target / BRACKET_BREAKER_FILENAME).write_text("nope", encoding="utf-8")
    board = live_readiness.build_readiness(root=tmp_path, now=NOW)
    row = next(c for c in board["checks"] if c["check"] == "bracket_breaker")
    assert row["ok"] is False
    assert "UNREADABLE" in row["detail"]


def test_a_breaker_write_failure_is_reported_never_raised(monkeypatch):
    """By this point the order is at the venue. Raising here would abandon the audit report and
    the operator notice that come after it."""
    _, record = _outcome(
        monkeypatch,
        {"status": live_leg.ENTRY_NAKED_CLOSED, "reason_codes": []},
        raises=OSError("disk full"),
    )
    assert live_route.BRACKET_BREAKER_UNRECORDED in record["live_reason_codes"]
    assert "UNEXPECTED_OSError" in record["live_reason_codes"]
