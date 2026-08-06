"""The watch that would have spoken on 2026-08-06.

`live_route` reaches `ROUTE_INCIDENT` for one reason — *"real money is in a state this runtime
cannot account for"* — and until `route_watch` existed, reaching it told nobody. Measured on the
live host: ETHUSDT 4h held INCIDENT for 44 consecutive cycles across ten and a quarter hours
while the live outcome ledger stayed empty, and it surfaced because a person happened to read a
readiness row during an unrelated deploy.

What is pinned here is the edge behaviour, because that is what separates a watch from a timer:
a standing incident must announce once and then go quiet, and clearing must announce too.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.crypto import route_watch


def _record(at, symbol, timeframe, status, *, codes=None, reconcile=None):
    return {
        "created_at": at,
        "symbol": symbol,
        "timeframe": timeframe,
        "live_route_status": status,
        "live_reason_codes": list(codes or []),
        "live_reconcile_status": reconcile,
    }


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """Feed `evaluate` a cycle ledger without writing 56MB of JSONL."""
    rows: list[dict] = []
    monkeypatch.setattr(
        route_watch, "_read_cycle_records", lambda root, limit: (rows[-limit:], None)
    )
    return rows


def _state(ledger, *, now="2026-08-06T04:00:00Z", root=None):
    return route_watch.evaluate(root, now=now)


# --- what "in incident" means -------------------------------------------------

def test_the_latest_record_for_a_context_decides(ledger, tmp_path):
    ledger += [
        _record("2026-08-06T01:00:00Z", "ETHUSDT", "4h", "INCIDENT"),
        _record("2026-08-06T02:00:00Z", "ETHUSDT", "4h", "HELD"),
    ]
    assert _state(ledger, root=tmp_path)["in_incident"] == []


def test_contexts_fail_independently(ledger, tmp_path):
    """ETHUSDT 4h was in incident while ETHUSDT 1d was HELD in the same batch. Reporting at
    symbol granularity would have to pick one of those to be true."""
    ledger += [
        _record("2026-08-06T02:00:00Z", "ETHUSDT", "4h", "INCIDENT"),
        _record("2026-08-06T02:00:00Z", "ETHUSDT", "1d", "HELD"),
    ]
    assert _state(ledger, root=tmp_path)["in_incident"] == ["ETHUSDT 4h"]


def test_a_disabled_gate_does_not_clear_an_incident(ledger, tmp_path):
    """The gate being shut is not evidence a book disagreement was resolved. Letting DISABLED
    count as a recovery would make a halted runtime look healthy."""
    ledger += [
        _record("2026-08-06T01:00:00Z", "ETHUSDT", "4h", "INCIDENT"),
        _record("2026-08-06T02:00:00Z", "ETHUSDT", "4h", "DISABLED"),
    ]
    assert _state(ledger, root=tmp_path)["in_incident"] == ["ETHUSDT 4h"]


def test_the_run_length_counts_the_current_run_only(ledger, tmp_path):
    """A context that recovered and relapsed reports the current run, not a total."""
    ledger += [
        _record("2026-08-06T00:00:00Z", "ETHUSDT", "4h", "INCIDENT"),
        _record("2026-08-06T01:00:00Z", "ETHUSDT", "4h", "SETTLED"),
        _record("2026-08-06T02:00:00Z", "ETHUSDT", "4h", "INCIDENT"),
        _record("2026-08-06T03:00:00Z", "ETHUSDT", "4h", "INCIDENT"),
    ]
    detail = _state(ledger, root=tmp_path)["contexts"]["ETHUSDT 4h"]
    assert detail["cycles"] == 2
    assert detail["since"] == "2026-08-06T02:00:00Z"
    assert detail["capped"] is False


def test_a_run_filling_the_window_says_so(ledger, tmp_path):
    """`capped` is the difference between "44 cycles" and "at least 44" — an operator sizing an
    incident must not read a lookback limit as its age."""
    ledger += [_record(f"2026-08-06T0{i}:00:00Z", "ETHUSDT", "4h", "INCIDENT") for i in range(4)]
    assert _state(ledger, root=tmp_path)["contexts"]["ETHUSDT 4h"]["capped"] is True


# --- the edge trigger ---------------------------------------------------------

def test_the_first_fire_always_announces(ledger, tmp_path):
    """With no marker there is no previous to differ from, and staying silent would make a
    freshly deployed watch indistinguishable from one that is not running."""
    ledger += [_record("2026-08-06T02:00:00Z", "ETHUSDT", "4h", "HELD")]
    result = route_watch.run_route_watch(tmp_path, now="2026-08-06T04:00:00Z", persist=False)
    assert result["changed"] is True


def test_the_first_fire_on_a_healthy_runtime_is_not_a_clearing(ledger, tmp_path):
    """Shipped wrong and caught in production: the watch's first fire ever, 2026-08-06 09:15Z,
    told an operator "live route incident CLEARED" about an incident he had never been told of.
    A watch whose opening message describes an event that did not happen is one an operator
    learns to discount, which is the only asset this module has."""
    ledger += [_record("2026-08-06T02:00:00Z", "ETHUSDT", "4h", "HELD")]
    text = route_watch.run_route_watch(tmp_path, now="2026-08-06T04:00:00Z", persist=False)["text"]
    assert "CLEARED" not in text
    assert "watch is running" in text
    assert "not a change" in text


def test_a_real_clearing_still_says_cleared(ledger, tmp_path):
    """The other half of the same fix: the first-fire wording must not swallow a genuine
    recovery, which is the message an operator is actually waiting for."""
    ledger += [_record("2026-08-06T01:00:00Z", "ETHUSDT", "4h", "INCIDENT")]
    route_watch.run_route_watch(tmp_path, now="2026-08-06T02:00:00Z")
    ledger += [_record("2026-08-06T02:00:00Z", "ETHUSDT", "4h", "SETTLED")]
    text = route_watch.run_route_watch(tmp_path, now="2026-08-06T03:00:00Z")["text"]
    assert "CLEARED" in text
    assert "watch is running" not in text


def test_a_standing_incident_announces_once(ledger, tmp_path):
    """The failure mode this watch must not become: a timer. A standing incident lengthens by
    one every cycle and that is not news."""
    ledger += [_record("2026-08-06T01:00:00Z", "ETHUSDT", "4h", "INCIDENT")]
    first = route_watch.run_route_watch(tmp_path, now="2026-08-06T02:00:00Z")
    assert first["changed"] is True

    ledger += [_record("2026-08-06T02:00:00Z", "ETHUSDT", "4h", "INCIDENT")]
    second = route_watch.run_route_watch(tmp_path, now="2026-08-06T03:00:00Z")
    assert second["changed"] is False
    assert second["text"] == ""
    assert route_watch.status_line(second) == "route_watch:unchanged:1:ETHUSDT 4h"


def test_clearing_announces_too(ledger, tmp_path):
    """An operator told about the incident and not about its resolution is left believing the
    worse state."""
    ledger += [_record("2026-08-06T01:00:00Z", "ETHUSDT", "4h", "INCIDENT")]
    route_watch.run_route_watch(tmp_path, now="2026-08-06T02:00:00Z")

    ledger += [_record("2026-08-06T02:00:00Z", "ETHUSDT", "4h", "SETTLED")]
    cleared = route_watch.run_route_watch(tmp_path, now="2026-08-06T03:00:00Z")
    assert cleared["changed"] is True
    assert "CLEARED" in cleared["text"]
    assert "DONE ETHUSDT 4h" in cleared["text"]


def test_a_second_context_entering_announces(ledger, tmp_path):
    ledger += [_record("2026-08-06T01:00:00Z", "ETHUSDT", "4h", "INCIDENT")]
    route_watch.run_route_watch(tmp_path, now="2026-08-06T02:00:00Z")

    ledger += [
        _record("2026-08-06T02:00:00Z", "ETHUSDT", "4h", "INCIDENT"),
        _record("2026-08-06T02:00:00Z", "BTCUSDT", "1d", "INCIDENT"),
    ]
    result = route_watch.run_route_watch(tmp_path, now="2026-08-06T03:00:00Z")
    assert result["changed"] is True
    assert "NEW  BTCUSDT 1d" in result["text"]
    assert "still standing: ETHUSDT 4h" in result["text"]


# --- the marker ---------------------------------------------------------------

def test_not_persisting_leaves_the_transition_unannounced(ledger, tmp_path):
    """The scheduler withholds the marker until delivery succeeds, so an undelivered
    announcement is retried rather than recorded as told."""
    ledger += [_record("2026-08-06T01:00:00Z", "ETHUSDT", "4h", "INCIDENT")]
    route_watch.run_route_watch(tmp_path, now="2026-08-06T02:00:00Z", persist=False)
    again = route_watch.run_route_watch(tmp_path, now="2026-08-06T03:00:00Z", persist=False)
    assert again["changed"] is True


def test_a_corrupt_marker_reads_as_never_announced(ledger, tmp_path):
    """Fail-open, deliberately: the worst case is one redundant announcement, while refusing to
    watch on an unreadable marker silences the thing whose whole job is to speak."""
    path = route_watch.mark_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")
    assert route_watch.read_mark(tmp_path) is None


def test_the_marker_round_trips(ledger, tmp_path):
    ledger += [_record("2026-08-06T01:00:00Z", "ETHUSDT", "4h", "INCIDENT")]
    result = route_watch.run_route_watch(tmp_path, now="2026-08-06T02:00:00Z")
    stored = json.loads(route_watch.mark_path(tmp_path).read_text(encoding="utf-8"))
    assert stored["in_incident"] == ["ETHUSDT 4h"]
    assert result["state"]["watch_version"] == route_watch.WATCH_VERSION


# --- what the operator reads --------------------------------------------------

def test_the_message_carries_the_reason_and_what_to_do(ledger, tmp_path):
    ledger += [
        _record("2026-08-06T01:00:00Z", "ETHUSDT", "4h", "INCIDENT",
                codes=["LIVE_VENUE_CLOSE_UNSETTLEABLE"], reconcile="DRIFT"),
    ]
    text = route_watch.run_route_watch(tmp_path, now="2026-08-06T02:00:00Z")["text"]
    assert "LIVE ROUTE INCIDENT" in text
    assert "LIVE_VENUE_CLOSE_UNSETTLEABLE" in text
    assert "DRIFT" in text
    # A status without an instruction is a status an operator has to translate.
    assert "live_readiness" in text
    assert "closes stay allowed" in text
    assert text.isascii()
