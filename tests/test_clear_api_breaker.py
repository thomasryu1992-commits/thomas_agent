"""The operator's reset for the API error breaker (PR2d-1, Thomas decision 27).

The breaker holds the live entry door shut and no success reopens it once it has tripped, so this
script is the only way back. It asks for a written reason because what it follows is a venue that
stopped answering — something to look at before the next entry leaves, not a button.
"""

from __future__ import annotations

import pytest
from tests._helpers import make_gate_authorization

from runtime.mvp_runtime.crypto.live_order import (
    MAX_CONSECUTIVE_API_ERRORS,
    DryRunLiveApiErrorBreaker,
    LiveApiErrorBreaker,
    api_breaker_status,
)
from runtime.mvp_runtime.crypto.live_pnl import LIVE_TRADING_FLAGS, LIVE_TRADING_PROVIDER_ID
from scripts import clear_api_breaker as cab

NOW = "2026-09-18T08:00:00Z"

_LIVE_AUTH = make_gate_authorization(flags=LIVE_TRADING_FLAGS, provider_id=LIVE_TRADING_PROVIDER_ID)


def _durable(root):
    return LiveApiErrorBreaker(root=root, authorization=_LIVE_AUTH)


@pytest.fixture
def tripped(tmp_path, monkeypatch):
    """A breaker tripped on the write class, with the durable implementation wired in — tests
    hold no live switch, so the script's own selection would hand back the inert one."""
    breaker = _durable(tmp_path)
    for _ in range(MAX_CONSECUTIVE_API_ERRORS):
        breaker.record_failure(call_class="write", call="submit", at=NOW,
                               reason_code="ORDER_REJECTED", venue_code=-1003)
    breaker.record_failure(call_class="read", call="open_orders", at=NOW, reason_code="ORDER_TRANSPORT")
    monkeypatch.setattr(cab, "select_live_api_breaker", lambda **kw: breaker)
    return tmp_path


def test_show_reports_both_classes_and_writes_nothing(tripped, capsys):
    before = api_breaker_status(tripped)
    assert cab.main(["--show", "--root", str(tripped)]) == cab.EXIT_OK
    out = capsys.readouterr().out
    assert f"TRIPPED at {NOW}" in out
    assert f"write: {MAX_CONSECUTIVE_API_ERRORS} in a row" in out and "(venue -1003)" in out
    assert "read : 1 in a row" in out and "open_orders ORDER_TRANSPORT" in out
    assert "tripped by the write class" in out and "live entries are REFUSED" in out
    assert api_breaker_status(tripped) == before


def test_show_runs_before_the_root_guard(tripped, capsys, monkeypatch):
    """The look-before-you-sign command must not refuse on a host shell."""
    def _refuse(root=None):
        raise AssertionError("--show must not reach the root guard")

    monkeypatch.setattr(cab, "assert_not_foreign_root_run", _refuse)
    assert cab.main(["--show", "--root", str(tripped)]) == cab.EXIT_OK


def test_clearing_requires_a_reason_and_an_operator(tripped, capsys):
    assert cab.main(["--root", str(tripped)]) == cab.EXIT_USAGE
    assert cab.main(["--cleared-by", "thomas", "--root", str(tripped)]) == cab.EXIT_USAGE
    assert cab.main(["--reason", "looked at it", "--root", str(tripped)]) == cab.EXIT_USAGE
    # Stored verbatim, so a blank answer is no answer (review of #889).
    assert cab.main(["--cleared-by", "  ", "--reason", "looked at it", "--root", str(tripped)]) == cab.EXIT_USAGE
    assert cab.main(["--cleared-by", "thomas", "--reason", " \t", "--root", str(tripped)]) == cab.EXIT_USAGE
    assert api_breaker_status(tripped)["tripped"] is True


def test_the_reason_is_stored_without_its_padding(tripped, capsys):
    assert cab.main(["--cleared-by", " thomas ", "--reason", "  venue fine  ", "--root", str(tripped)]) == cab.EXIT_OK
    status = api_breaker_status(tripped)
    assert (status["cleared_by"], status["cleared_reason"]) == ("thomas", "venue fine")


def test_a_failure_counted_right_after_the_reset_is_not_a_failed_reset(tripped, capsys, monkeypatch):
    """Judged by what the clear wrote, not by the counts read back (review of #889)."""
    breaker = _durable(tripped)
    real_clear = breaker.clear

    def _clear_then_the_venue_fails(**kw):
        stored = real_clear(**kw)
        breaker.record_failure(call_class="read", call="open_orders", at=NOW, reason_code="ORDER_TRANSPORT")
        return stored

    breaker.clear = _clear_then_the_venue_fails
    monkeypatch.setattr(cab, "select_live_api_breaker", lambda **kw: breaker)
    assert cab.main(["--cleared-by", "thomas", "--reason", "venue fine", "--root", str(tripped)]) == cab.EXIT_OK
    captured = capsys.readouterr()
    assert "may open a position again" in captured.out and "NOT cleared" not in captured.err


def test_a_reset_the_venue_trips_again_at_once_says_so(tripped, capsys, monkeypatch):
    breaker = _durable(tripped)
    real_clear = breaker.clear

    def _clear_then_it_trips(**kw):
        stored = real_clear(**kw)
        for _ in range(MAX_CONSECUTIVE_API_ERRORS):
            breaker.record_failure(call_class="write", call="submit", at=NOW, reason_code="ORDER_TRANSPORT")
        return stored

    breaker.clear = _clear_then_it_trips
    monkeypatch.setattr(cab, "select_live_api_breaker", lambda **kw: breaker)
    assert cab.main(["--cleared-by", "thomas", "--reason", "venue fine", "--root", str(tripped)]) == cab.EXIT_BLOCKED
    assert "tripped again since" in capsys.readouterr().err


def test_a_reasoned_clear_reopens_the_door_and_records_why(tripped, capsys):
    code = cab.main(["--cleared-by", "thomas", "--reason", "venue maintenance 03:00-03:40Z; answering",
                     "--root", str(tripped)])
    assert code == cab.EXIT_OK
    out = capsys.readouterr().out
    assert "may open a position again" in out and "TRIPPED" not in out
    status = api_breaker_status(tripped)
    assert status["tripped"] is False and status["consecutive"] == 0
    assert (status["cleared_by"], status["cleared_reason"]) == (
        "thomas", "venue maintenance 03:00-03:40Z; answering")
    assert "last cleared" in out


def test_a_streak_below_the_limit_can_be_cleared_too(tmp_path, monkeypatch, capsys):
    breaker = _durable(tmp_path)
    breaker.record_failure(call_class="read", call="fetch_order", at=NOW, reason_code="ORDER_TRANSPORT")
    monkeypatch.setattr(cab, "select_live_api_breaker", lambda **kw: breaker)
    assert cab.main(["--cleared-by", "thomas", "--reason", "one timeout, venue fine",
                     "--root", str(tmp_path)]) == cab.EXIT_OK
    assert api_breaker_status(tmp_path)["consecutive"] == 0


def test_clearing_an_already_clear_breaker_says_so(tmp_path, capsys):
    code = cab.main(["--cleared-by", "thomas", "--reason", "nothing to do", "--root", str(tmp_path)])
    assert code == cab.EXIT_OK
    assert "nothing to clear" in capsys.readouterr().out


def test_a_clear_that_went_nowhere_says_so(tripped, capsys, monkeypatch):
    """Run where the live switch is off, the selection hands back the inert breaker and the reset
    writes nothing. Printing "cleared" over a record that did not change would send the operator
    away believing the door is open."""
    monkeypatch.setattr(cab, "select_live_api_breaker", lambda **kw: DryRunLiveApiErrorBreaker())
    code = cab.main(["--cleared-by", "thomas", "--reason", "venue fine", "--root", str(tripped)])
    assert code == cab.EXIT_BLOCKED
    captured = capsys.readouterr()
    assert "NOT cleared" in captured.err and "docker exec thomas-scheduler" in captured.err
    assert "may open a position again" not in captured.out
    assert api_breaker_status(tripped)["tripped"] is True


def test_a_fail_closed_refusal_is_blocked_not_usage(tripped, capsys, monkeypatch):
    from runtime.mvp_runtime.errors import ToolBlocked

    def _refuse(**_kw):
        raise ToolBlocked("LIVE_API_BREAKER_LOCKED", "another process holds the breaker")

    monkeypatch.setattr(cab, "select_live_api_breaker", _refuse)
    code = cab.main(["--cleared-by", "thomas", "--reason", "venue fine", "--root", str(tripped)])
    assert code == cab.EXIT_BLOCKED
    assert "BLOCKED: LIVE_API_BREAKER_LOCKED" in capsys.readouterr().err


def test_an_unreadable_record_is_blocked_even_for_show(tmp_path, capsys):
    from runtime.mvp_runtime.crypto.live_order import API_BREAKER_FILENAME
    from runtime.mvp_runtime.crypto.state import venue_state_dir

    path = venue_state_dir(tmp_path) / API_BREAKER_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{torn", encoding="utf-8")
    assert cab.main(["--show", "--root", str(tmp_path)]) == cab.EXIT_BLOCKED
    assert "LIVE_API_BREAKER_UNREADABLE" in capsys.readouterr().err
    assert path.read_text(encoding="utf-8") == "{torn"


def test_a_root_run_is_refused_before_anything_is_written(tripped, capsys, monkeypatch):
    from runtime.mvp_runtime.errors import ToolBlocked

    def _foreign(root=None):
        raise ToolBlocked("STATE_FOREIGN_ROOT_RUN", "scripted root run")

    monkeypatch.setattr(cab, "assert_not_foreign_root_run", _foreign)
    code = cab.main(["--cleared-by", "thomas", "--reason", "venue fine", "--root", str(tripped)])
    assert code == cab.EXIT_BLOCKED
    assert api_breaker_status(tripped)["tripped"] is True
