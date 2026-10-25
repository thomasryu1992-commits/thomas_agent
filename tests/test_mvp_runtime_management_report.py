"""The weekly management report (WMR-1): four sections, read on their own, sent on the control bot.

What is under test is mostly what the report must NOT do: let one unreadable section cost the other
three, put a failure's exception text or an approval's snapshot or a feedback comment into the
message, poll the control bot, or lose a failed send quietly."""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime import management_report as mr
from runtime.mvp_runtime import operator as operator_mod
from runtime.mvp_runtime.control import ControlState, ControlStore
from runtime.mvp_runtime.errors import MvpRuntimeError, OperatorBlocked, PersistenceError
from runtime.mvp_runtime.scheduler import (
    KIND_MANAGEMENT_REPORT,
    KINDS,
    MAINTENANCE_KINDS,
    Schedule,
    ScheduleStore,
    build_schedule,
    run_due,
)
from runtime.mvp_runtime.store import ARCHIVE_DIR, FEEDBACK_FILE, LEDGER_REL, SCHEDULER_FILE, LedgerStore

NOW = "2026-10-26T00:00:00Z"            # Monday 09:00 KST
IN_WINDOW = "2026-10-22T10:00:00Z"
BEFORE_WINDOW = "2026-10-10T10:00:00Z"
LEAK = "/app/.runtime_governance_state/approvals/thomas_only.jsonl"


class FakeLedger:
    def __init__(self, *, records=(), scheduler=(), feedback=()):
        self.records = list(records)
        self.events = {SCHEDULER_FILE: list(scheduler), FEEDBACK_FILE: list(feedback)}

    def iter_records_with_archive(self, *, appended_since=None, kinds=None):
        return iter([r for r in self.records if kinds is None or r["kind"] in kinds])

    def iter_events_with_archive(self, filename, *, appended_since=None):
        return iter(self.events[filename])


class FakeSchedules:
    def __init__(self, rows):
        self._rows = rows

    def list(self):
        return self._rows


class FakeApprovals:
    def __init__(self, pending):
        self._pending = pending

    def pending(self):
        return self._pending


def _run(trace, role, *, received=IN_WINDOW, result="PASS"):
    return [
        {"kind": "task", "trace_id": trace, "record": {"request": {"received_at": received}}},
        {"kind": "role_assignment", "trace_id": trace, "record": {"role_id": role}},
        {"kind": "validation_result", "trace_id": trace, "record": {"validation": {"result": result, "checks": []}}},
    ]


def _schedule(sid, kind, last_status, *, enabled=True):
    return Schedule(schedule_id=sid, kind=kind, request="", interval_seconds=3600, enabled=enabled,
                    created_by="op", created_at=BEFORE_WINDOW, next_run_at=NOW, reason="",
                    last_run_at=IN_WINDOW, last_status=last_status)


def _ask(approval_id, target, expires_at):
    return {"approval_id": approval_id, "status": "PENDING", "action_fingerprint": "sha256:" + "f" * 64,
            "approved_action_snapshot": {"target_ref": target, "normalized_parameters": {"secret": LEAK}},
            "validity": {"issued_at": IN_WINDOW, "expires_at": expires_at}}


def _report(**over):
    ledger = over.pop("ledger", FakeLedger(
        records=_run("trace_a", "research.general") + _run("trace_b", "content.general", result="BLOCK"),
        scheduler=[
            {"action": "failed", "kind": "crypto_factory", "schedule_id": "schedule_f", "created_at": IN_WINDOW,
             "status": f"failed:FACTORY_CHILD:ValueError: {LEAK}"},
            {"action": "abandoned", "kind": "crypto_factory", "schedule_id": "schedule_f", "created_at": IN_WINDOW},
            {"action": "fired", "kind": "analysis_task", "schedule_id": "schedule_t", "created_at": IN_WINDOW},
            {"action": "failed", "kind": "crypto_factory", "schedule_id": "schedule_f", "created_at": BEFORE_WINDOW},
        ],
        feedback=[
            {"trace_id": "trace_a", "verdict": "GOOD", "comment": f"코멘트 {LEAK}", "created_at": IN_WINDOW},
            {"trace_id": "trace_b", "verdict": "BAD", "comment": "별로", "created_at": IN_WINDOW},
            {"trace_id": "trace_old", "verdict": "BAD", "comment": "", "created_at": BEFORE_WINDOW},
        ],
    ))
    args = dict(
        now=NOW, ledger=ledger,
        schedules=FakeSchedules([
            _schedule("schedule_f", "crypto_factory", f"failed:FACTORY_CHILD:ValueError: {LEAK}"),
            _schedule("schedule_off", "holdings_refresh", "failed:TOSS_DOWN", enabled=False),
            _schedule("schedule_ok", "crypto_report", "report_sent cycles=12 warnings=0"),
        ]),
        approval_store=FakeApprovals([
            _ask("approval_live", "emergency_close:crypto", "2026-10-26T00:20:00Z"),
            _ask("approval_dead", "execution_stage:binance_futures:PAPER", "2026-10-25T00:00:00Z"),
        ]),
        control_state=ControlState(mode="ACTIVE"),
        crypto_status=lambda: {"cycles_seen": 12, "warnings": ["w"], "open_positions": [{}, {}],
                               "last_cycle": {"at": IN_WINDOW, "verdict": "NO_SIGNAL"}},
    )
    args.update(over)
    return mr.collect(**args)


def test_the_kind_is_registered_on_the_maintenance_lane():
    assert KIND_MANAGEMENT_REPORT == "management_report"
    assert KIND_MANAGEMENT_REPORT in KINDS and KIND_MANAGEMENT_REPORT in MAINTENANCE_KINDS
    schedule = build_schedule(kind=KIND_MANAGEMENT_REPORT, request="", interval_seconds=604800,
                              created_by="op", now=NOW)
    assert schedule.next_run_at == "2026-11-02T00:00:00Z"      # first fire one interval after registering


def test_the_four_sections_say_what_happened_in_the_window():
    text = mr.render(_report())
    assert text.startswith("주간 경영 보고 (10-19 09:00 → 10-26 09:00 KST)")
    assert "런타임: ACTIVE" in text
    assert "[research.general] 실행 1 · 전달 1" in text and "[content.general] 실행 1 · 전달 0" in text
    assert "BLOCK 1" in text
    assert "[crypto] 최근 사이클 12 · 마지막 10-22 19:00 NO_SIGNAL · 열린 포지션 2 · 경고 1" in text
    # Approvals: the live ask, by id, kind and expiry; the expired one is not offered.
    assert "approval_live · emergency_close · 만료 10-26 09:20" in text and "approval_dead" not in text
    # Schedules: the enabled failing one by code; a disabled one is not "failing"; counts in window only.
    assert "crypto_factory (schedule_f) — FACTORY_CHILD" in text and "TOSS_DOWN" not in text
    assert "crypto_factory (schedule_f) 실패 1 · 중단 1" in text
    assert "스케줄 분석 실행 1건" in text
    assert "이벤트 기록이" not in text          # a row from before the window: the week is covered
    # Feedback: in-window verdicts, per lane by trace, against the runs delivered.
    assert "/feedback 2건 (전달 1건 대비): GOOD 1 · BAD 1 · NOTE 0" in text
    assert "[content.general] BAD 1" in text and "[research.general] GOOD 1" in text


def test_no_exception_text_snapshot_fingerprint_or_comment_leaves():
    text = mr.render(_report())
    assert LEAK not in text and "ValueError" not in text
    assert "sha256:" not in text and "코멘트" not in text and "별로" not in text


def test_one_unreadable_section_costs_only_itself():
    class Broken(FakeLedger):
        def iter_events_with_archive(self, filename, *, appended_since=None):
            raise PersistenceError("LEDGER_UNREADABLE", "torn line")

    def boom():
        raise RuntimeError(LEAK)

    report = _report(ledger=Broken(records=_run("trace_a", "research.general")), crypto_status=boom)
    text = mr.render(report)
    assert "■ 실패 스케줄\n  읽지 못함: LEDGER_UNREADABLE" in text
    assert "■ 피드백\n  읽지 못함: LEDGER_UNREADABLE" in text
    assert "[crypto] 읽지 못함: RuntimeError" in text and LEAK not in text
    assert "[research.general] 실행 1" in text and "approval_live" in text


def test_a_short_event_history_says_how_far_back_it_counted():
    late = "2026-10-24T00:00:00Z"
    ledger = FakeLedger(scheduler=[{"action": "failed", "kind": "crypto", "schedule_id": "s", "created_at": late,
                                    "status": "failed:X_Y"}])
    text = mr.render(_report(ledger=ledger))
    assert "(이벤트 기록이 10-24 09:00부터만 있어 그 이후만 셌습니다)" in text


def test_an_empty_week_renders_plainly():
    text = mr.render(_report(ledger=FakeLedger(), schedules=FakeSchedules([]), approval_store=FakeApprovals([])))
    assert "비크립토 레인: 기간 안에 실행된 요청이 없습니다." in text
    assert "(기록된 스케줄러 이벤트가 없습니다)" in text
    assert "■ 대기 승인\n  없음" in text and "지금 실패 중: 없음" in text and "/feedback 0건" in text


# --- the ledger reads a week across rotation ---------------------------------------------------------

def test_the_event_reader_walks_archives_and_refuses_other_files(tmp_path):
    ledger = LedgerStore(tmp_path)
    archive = tmp_path / ARCHIVE_DIR
    archive.mkdir(parents=True)
    (archive / "scheduler_events.2026-10-20T000000Z.jsonl").write_text(
        json.dumps({"action": "failed", "created_at": "2026-10-19T12:00:00Z"}) + "\n", encoding="utf-8")
    (archive / "scheduler_events.2026-10-01T000000Z.jsonl").write_text(
        json.dumps({"action": "failed", "created_at": "2026-09-30T12:00:00Z"}) + "\n", encoding="utf-8")
    ledger.append_scheduler_event({"action": "fired", "created_at": "2026-10-25T12:00:00Z"})
    rows = list(ledger.iter_events_with_archive(SCHEDULER_FILE, appended_since="2026-10-19T00:00:00Z"))
    assert [r["created_at"] for r in rows] == ["2026-10-19T12:00:00Z", "2026-10-25T12:00:00Z"]
    assert list(ledger.iter_events_with_archive(FEEDBACK_FILE)) == []          # absent is empty, not an error
    with pytest.raises(PersistenceError) as exc:
        list(ledger.iter_events_with_archive("audit_events.jsonl"))
    assert exc.value.reason_code == "LEDGER_NOT_WINDOW_READABLE"


# --- the control bot, send-only ----------------------------------------------------------------------

def test_the_control_channel_never_polls(monkeypatch):
    monkeypatch.delenv(operator_mod.OPERATOR_CHANNEL_ENV, raising=False)
    channel = operator_mod.select_control_channel()
    for read in (channel.poll, channel.peek):
        with pytest.raises(OperatorBlocked) as exc:
            read()
        assert exc.value.reason_code == operator_mod.CONTROL_SEND_ONLY_REASON


def test_the_control_channel_reads_the_control_token_by_name_behind_the_same_gate(monkeypatch):
    monkeypatch.setenv(operator_mod.OPERATOR_CHANNEL_ENV, operator_mod.TELEGRAM)
    channel = operator_mod.select_control_channel()
    assert isinstance(channel._inner, operator_mod.TelegramChannel)
    assert channel._inner._token_env == operator_mod.CONTROL_BOT_TOKEN_ENV == "MVP_CONTROL_BOT_TOKEN"
    assert channel.network_egress is True


# --- the fire ---------------------------------------------------------------------------------------

def _fire(tmp_path, monkeypatch, *, sent=None, raises=None):
    store = ScheduleStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.add(build_schedule(kind=KIND_MANAGEMENT_REPORT, request="", interval_seconds=604800,
                             created_by="op", now="2026-10-19T00:00:00Z"))
    seen = []

    def fake_notify(channel, text, *, repo_root=None):
        seen.append(channel)
        if raises is not None:
            raise raises
        if sent is not None:
            sent.append(text)

    monkeypatch.setattr(operator_mod, "select_control_channel", lambda: "control-bot")
    monkeypatch.setattr(operator_mod, "select_operator_channel",
                        lambda **_: pytest.fail("the report must not use the notification channel"))
    monkeypatch.setattr(operator_mod, "notify_operator", fake_notify)
    ledger = LedgerStore(tmp_path / LEDGER_REL)
    run_due(store, now=NOW, control_store=ControlStore(tmp_path), ledger=ledger, repo_root=tmp_path)
    return seen, ledger.read_scheduler_events()


def test_the_fire_sends_once_on_the_control_bot(tmp_path, monkeypatch):
    sent: list[str] = []
    seen, events = _fire(tmp_path, monkeypatch, sent=sent)
    assert seen == ["control-bot"] and len(sent) == 1 and sent[0].startswith("주간 경영 보고")
    terminal = [e for e in events if e["action"] in ("fired", "failed")]
    assert terminal[-1]["action"] == "fired" and terminal[-1]["status"].startswith("management_report_sent")


def test_a_send_that_fails_fails_the_fire(tmp_path, monkeypatch):
    _, events = _fire(tmp_path, monkeypatch, raises=OperatorBlocked("NO_BOT_TOKEN", "unset"))
    terminal = [e for e in events if e["action"] in ("fired", "failed")]
    assert terminal[-1]["action"] == "failed"
    assert terminal[-1]["status"].startswith("failed:MANAGEMENT_REPORT_NOT_SENT")


def test_failure_code_keeps_the_code_and_drops_the_rest():
    assert mr.failure_code(f"failed:FACTORY_CHILD:ValueError: {LEAK}") == "FACTORY_CHILD"
    assert mr.failure_code("failed:x") is None and mr.failure_code("report_sent") is None
    assert issubclass(OperatorBlocked, MvpRuntimeError)
