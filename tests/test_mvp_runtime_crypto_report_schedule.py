"""C13 crypto_report schedule kind — daily dashboard pushed to the operator.

The report is pure reads plus one notify, so the rules under test are about what it
must NOT do: a failed/refused delivery is reported in the fire status and never
raised (a report that cannot be sent must not stop the schedules behind it), and the
kill switch still governs the fire like every other kind."""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime import control
from runtime.mvp_runtime.control import ControlState, ControlStore
from runtime.mvp_runtime.errors import MvpRuntimeError
from runtime.mvp_runtime.scheduler import (
    KIND_REPORT,
    KINDS,
    ScheduleStore,
    build_schedule,
    run_due,
)
from runtime.mvp_runtime.store import LEDGER_REL, LedgerStore

NOW = "2026-07-24T04:00:00Z"
LATER = "2026-07-25T04:00:00Z"


def _armed_store(tmp_path, *, interval=86400):
    schedule = build_schedule(kind=KIND_REPORT, request="", interval_seconds=interval,
                              created_by="op", now=NOW)
    store = ScheduleStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.add(schedule)
    return store


def _fire(tmp_path, store, monkeypatch, *, sent: list | None = None, raises=None):
    """Fire the due report with the operator channel stubbed."""
    from runtime.mvp_runtime import operator as operator_mod

    def fake_select(*, now=None, root=None):
        return object()

    def fake_notify(channel, text, *, repo_root=None):
        if raises is not None:
            raise raises
        if sent is not None:
            sent.append(text)

    monkeypatch.setattr(operator_mod, "select_operator_channel", fake_select)
    monkeypatch.setattr(operator_mod, "notify_operator", fake_notify)
    return run_due(store, now=LATER, control_store=ControlStore(tmp_path),
                   ledger=LedgerStore(tmp_path / LEDGER_REL), repo_root=tmp_path)


def test_report_kind_is_registered():
    assert KIND_REPORT == "crypto_report" and KIND_REPORT in KINDS


def test_report_needs_no_request_string(tmp_path):
    # Only analysis_task requires a request; a report has nothing to parameterize.
    schedule = build_schedule(kind=KIND_REPORT, request="", interval_seconds=86400,
                              created_by="op", now=NOW)
    assert schedule.kind == KIND_REPORT and schedule.request == ""


def test_fire_renders_and_sends_the_dashboard(tmp_path, monkeypatch):
    store = _armed_store(tmp_path)
    sent: list[str] = []
    summary = _fire(tmp_path, store, monkeypatch, sent=sent)
    assert summary["fired"] == 1
    assert summary["results"][0]["status"].startswith("report_sent")
    assert len(sent) == 1
    assert "crypto pipeline dashboard" in sent[0]


def test_delivery_failure_is_reported_never_raised(tmp_path, monkeypatch):
    store = _armed_store(tmp_path)
    summary = _fire(tmp_path, store, monkeypatch,
                    raises=MvpRuntimeError("CHANNEL_REFUSED", "no transport"))
    assert summary["fired"] == 1  # the tick completed; nothing propagated
    assert summary["results"][0]["status"] == "report_rendered_not_sent:CHANNEL_REFUSED"


def test_unexpected_transport_error_is_also_contained(tmp_path, monkeypatch):
    store = _armed_store(tmp_path)
    summary = _fire(tmp_path, store, monkeypatch, raises=RuntimeError("socket blew up"))
    assert summary["results"][0]["status"] == "report_rendered_not_sent:RuntimeError"


def test_kill_switch_skips_the_report(tmp_path, monkeypatch):
    store = _armed_store(tmp_path)
    control_store = ControlStore(tmp_path)
    control_store.path.parent.mkdir(parents=True, exist_ok=True)
    control_store.path.write_text(
        json.dumps(ControlState(mode=control.KILLED, updated_by="op",
                                updated_at=NOW, reason="t").as_record()),
        encoding="utf-8",
    )
    sent: list[str] = []
    from runtime.mvp_runtime import operator as operator_mod

    monkeypatch.setattr(operator_mod, "select_operator_channel", lambda **kw: object())
    monkeypatch.setattr(operator_mod, "notify_operator",
                        lambda *a, **kw: sent.append("should not happen"))
    summary = run_due(store, now=LATER, control_store=control_store,
                      ledger=LedgerStore(tmp_path / LEDGER_REL), repo_root=tmp_path)
    assert summary["fired"] == 0 and summary["skipped"] == 1
    assert sent == []  # a killed runtime tells nobody anything
