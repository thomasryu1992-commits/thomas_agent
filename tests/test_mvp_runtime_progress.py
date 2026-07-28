"""Progress delivery — the live status line between the queue receipt and the result.

The silence this closes: the receipt was the last thing Thomas heard until a finished
analysis arrived, which on a validated run is two model calls later. A run that was working
and a run that was wedged looked identical from the channel.

What must hold, and every one of these is a direction chosen once:

- ONE extra message per run, edited in place — never one notification per stage.
- Stage NAMES, never a percentage: a model call has no denominator, and a bar that stops at
  80% is a lie told slowly.
- The reported stages are the ones that actually RAN, so the line describes this run rather
  than a plan of it.
- Every failure direction is "the run wins": a notice that cannot be sent, cannot be edited,
  or has no message id to edit costs nothing but the display.
- The line is CLOSED before the deliverable is sent, so a finished answer never sits under a
  status still claiming to be analyzing.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime import operator, pipeline
from runtime.mvp_runtime.errors import OperatorBlocked
from runtime.mvp_runtime.operator import (
    InboundMessage,
    MockOperatorChannel,
    OperatorIdentity,
    ProgressNotice,
    run_operator_once,
)
from runtime.mvp_runtime.task_registry import TaskRegistryStore
from tests._helpers import requires_local_core

NOW = "2026-07-26T09:00:00Z"
REG = OperatorIdentity(operator_id="tg-12345", chat_id="chat-777")


def _msg(text="이 아이디어를 분석해줘"):
    return InboundMessage(text=text, sender_id="tg-12345", chat_id="chat-777")


def _stub_run(monkeypatch, stages=(), status="COMPLETED", reason=None):
    """Stand in for the pipeline, reporting `stages` through the real callback.

    The BLOCKED shape is the pipeline's real one — `result["block"]["reason_code"]`, which is
    where `render_result_reply` reads it from. A stub that invented a top-level `reason_code`
    passed its own assertion while the production path rendered a bare "BLOCKED"."""
    def _run(request, **kwargs):
        for stage in stages:
            kwargs["on_progress"](stage)
        result = {"status": status, "final_response": f"분석: {request}",
                  "records": {"received_task": {"identity": {"task_id": "task_1",
                                                             "trace_id": "trace_1"}}}}
        if reason is not None:
            result["block"] = {"stage": "validation", "reason_code": reason, "message": ""}
        return result

    monkeypatch.setattr("runtime.mvp_runtime.operator.run_task", _run)
    monkeypatch.setattr("runtime.mvp_runtime.operator_feedback.record_delivery",
                        lambda *a, **k: None)


# --- the vocabulary ----------------------------------------------------------

def test_every_reported_stage_has_a_label_and_every_label_a_stage():
    """Two lists, one meaning. A stage the pipeline reports and the channel cannot name would
    print a bare `analysis_worker` at Thomas; a label for a stage nothing emits is a line that
    can never appear. Both are drift, and drift here is invisible in production."""
    assert set(operator.PROGRESS_LABELS) == set(pipeline.PROGRESS_STAGES)


def test_reported_stages_are_real_pipeline_steps():
    """The progress vocabulary is a SUBSET of the run's own step names, not a second one
    invented for the channel — so renaming a step moves both consumers or neither."""
    steps = {
        pipeline.STEP_INTAKE, pipeline.STEP_CORE_BINDING, pipeline.STEP_PRIME_PLANNING,
        pipeline.STEP_SEARCH, pipeline.STEP_MEMORY_RETRIEVAL, pipeline.STEP_ANALYSIS_WORKER,
        pipeline.STEP_AUTOMATIC_VALIDATION, pipeline.STEP_INDEPENDENT_VALIDATION,
        pipeline.STEP_CONTROLLED_WRITE, pipeline.STEP_REVISION,
    }
    assert set(pipeline.PROGRESS_STAGES) <= steps


def test_no_label_promises_a_percentage():
    """A run has no measurable denominator — a model call takes as long as it takes. The
    design decision is stage names only, and this is where it is enforced rather than
    remembered."""
    for label in list(operator.PROGRESS_LABELS.values()) + [operator.PROGRESS_STARTED_LABEL]:
        assert "%" not in label


# --- one message, edited -----------------------------------------------------

def test_the_status_line_is_edited_not_resent(tmp_path, monkeypatch):
    _stub_run(monkeypatch, stages=["readonly_search", "analysis_worker", "automatic_validation"])
    channel = MockOperatorChannel(inbound=[_msg()])

    run_operator_once(channel, REG, registry=TaskRegistryStore(tmp_path), repo_root=tmp_path)

    # Three sends total (receipt, status line, deliverable) however many stages ran...
    assert len(channel.sent) == 3
    # ...and the stages arrive as edits to the ONE status message.
    assert len(channel.edited) == 4          # 3 stages + the terminal close
    assert {mid for _chat, mid, _text in channel.edited} == {"mock-msg-2"}
    labels = [text.rsplit("상태: ", 1)[1] for _chat, _mid, text in channel.edited]
    assert labels == ["자료 검색 중", "분석 중", "결과 검증 중", "완료 — 결과 전송 중"]


def test_the_line_reports_the_stages_that_actually_ran(tmp_path, monkeypatch):
    """A run that skipped the independent review must not claim one. The callback fires from
    inside each branch, so the line is a description of this run, not of the plan."""
    _stub_run(monkeypatch, stages=["analysis_worker", "automatic_validation"])
    channel = MockOperatorChannel(inbound=[_msg()])

    run_operator_once(channel, REG, registry=TaskRegistryStore(tmp_path), repo_root=tmp_path)

    edits = " ".join(text for _chat, _mid, text in channel.edited)
    assert "독립 검증 중" not in edits


def test_the_line_carries_the_entry_id_and_the_request(tmp_path, monkeypatch):
    """Two tasks in one chat produce two status lines; each has to say which one it is."""
    _stub_run(monkeypatch, stages=["analysis_worker"])
    store = TaskRegistryStore(tmp_path)
    channel = MockOperatorChannel(inbound=[_msg("이 아이디어를 분석해줘")])

    run_operator_once(channel, REG, registry=store, repo_root=tmp_path)

    entry_id = store.latest()[0].registry_entry_id
    line = channel.sent[1][1]
    assert entry_id[:12] in line
    assert "이 아이디어를 분석해줘" in line


def test_the_line_is_closed_before_the_deliverable_is_sent(tmp_path, monkeypatch):
    """A finished answer must never arrive under a status still reading 분석 중."""
    _stub_run(monkeypatch, stages=["analysis_worker"])
    channel = MockOperatorChannel(inbound=[_msg()])

    run_operator_once(channel, REG, registry=TaskRegistryStore(tmp_path), repo_root=tmp_path)

    assert channel.edited[-1][2].endswith("완료 — 결과 전송 중")
    assert "분석: " in channel.sent[-1][1]      # the deliverable came after


def test_a_withheld_run_says_so_on_the_line(tmp_path, monkeypatch):
    _stub_run(monkeypatch, stages=["analysis_worker"], status="BLOCKED", reason="VALIDATION_FAILED")
    channel = MockOperatorChannel(inbound=[_msg()])

    run_operator_once(channel, REG, registry=TaskRegistryStore(tmp_path), repo_root=tmp_path)

    assert "중단됨 (VALIDATION_FAILED)" in channel.edited[-1][2]


# --- failure directions: the run always wins ---------------------------------

class _NoEditChannel(MockOperatorChannel):
    """A transport with no edit support — the pre-progress channel, still valid."""
    edit = None


def test_a_channel_that_cannot_edit_gets_no_status_line(tmp_path, monkeypatch):
    """Degrading to the old behaviour, not to six notifications: without editing, a per-stage
    message is worse than silence."""
    _stub_run(monkeypatch, stages=["analysis_worker", "automatic_validation"])
    channel = _NoEditChannel(inbound=[_msg()])

    run_operator_once(channel, REG, registry=TaskRegistryStore(tmp_path), repo_root=tmp_path)

    assert len(channel.sent) == 2               # receipt + deliverable, exactly as before
    assert "분석: " in channel.sent[-1][1]


def test_a_send_failure_on_the_line_does_not_cost_the_run(tmp_path, monkeypatch):
    class _FailsFirstSend(MockOperatorChannel):
        def send(self, chat_id, text):
            if "상태:" in text:
                raise OperatorBlocked("CHANNEL_TRANSPORT", "no")
            return super().send(chat_id, text)

    _stub_run(monkeypatch, stages=["analysis_worker"])
    channel = _FailsFirstSend(inbound=[_msg()])

    run_operator_once(channel, REG, registry=TaskRegistryStore(tmp_path), repo_root=tmp_path)

    assert channel.edited == []
    assert "분석: " in channel.sent[-1][1]      # the analysis still reached Thomas


def test_an_edit_failure_switches_the_line_off_rather_than_retrying(tmp_path, monkeypatch):
    """A channel that just failed an edit will most likely fail the next five too; spending
    five more failing round-trips per run buys nothing, and the delivery at the end is the
    message that matters."""
    class _FailsEdits(MockOperatorChannel):
        def edit(self, chat_id, message_id, text):
            self.edited.append((chat_id, message_id, text))
            raise OperatorBlocked("CHANNEL_TRANSPORT", "no")

    _stub_run(monkeypatch, stages=["readonly_search", "analysis_worker", "automatic_validation"])
    channel = _FailsEdits(inbound=[_msg()])

    run_operator_once(channel, REG, registry=TaskRegistryStore(tmp_path), repo_root=tmp_path)

    assert len(channel.edited) == 1             # tried once, then stopped
    assert "분석: " in channel.sent[-1][1]


def test_a_transport_with_no_message_id_gets_no_line(tmp_path, monkeypatch):
    """Sent but unidentifiable is the same as unusable: with nothing to edit, the honest
    outcome is one notice, not a new message per stage."""
    class _NoIds(MockOperatorChannel):
        def send(self, chat_id, text):
            super().send(chat_id, text)
            return None

    _stub_run(monkeypatch, stages=["analysis_worker"])
    channel = _NoIds(inbound=[_msg()])

    run_operator_once(channel, REG, registry=TaskRegistryStore(tmp_path), repo_root=tmp_path)

    assert channel.edited == []
    assert "분석: " in channel.sent[-1][1]


def test_a_raising_callback_cannot_kill_a_run():
    """`_progress` swallows everything, not just typed errors: the callback reaches a network
    channel, and the analysis is the point of the run."""
    def _boom(_stage):
        raise RuntimeError("channel exploded")

    pipeline._progress(_boom, pipeline.STEP_ANALYSIS_WORKER)     # must not raise


def test_a_repeated_label_is_not_re_sent():
    """Telegram rejects an edit that changes nothing, and that rejection would switch the
    line off for the rest of the run over a non-event."""
    channel = MockOperatorChannel()
    notice = ProgressNotice(channel, "chat-777", entry_id="treg_abc123def456", request_text="x")
    notice.start()
    notice.stage("analysis_worker")
    notice.stage("analysis_worker")

    assert len(channel.edited) == 1


def test_an_unknown_stage_is_dropped_not_printed_raw():
    channel = MockOperatorChannel()
    notice = ProgressNotice(channel, "chat-777", entry_id="treg_abc123def456", request_text="x")
    notice.start()
    notice.stage("some_future_stage")

    assert channel.edited == []


def test_a_long_request_is_clipped_out_of_the_line():
    """The status line is a header, not a transcript: a pasted business plan must not become
    a 4000-character message that is then edited five more times."""
    channel = MockOperatorChannel()
    notice = ProgressNotice(channel, "chat-777", entry_id="treg_abc123def456",
                            request_text="가" * 3000)
    notice.start()

    assert len(channel.sent[0][1]) < 200


# --- the seam: the REAL pipeline reports ------------------------------------
#
# Everything above stubs `run_task`, which proves the channel half and would stay green if
# the pipeline reported nothing at all.
#
# The end-to-end versions need an activated Core (every stage is downstream of binding, so a
# core-neutral checkout BLOCKs before the first one), which means they skip on CI and on any
# machine without a local activation — see `tests/_helpers.requires_local_core`. A seam test
# that only runs somewhere else is not much of a guard, so the structural test below never
# skips: it asserts the emit sites exist for exactly the stages that are declared.

def test_every_declared_stage_is_actually_emitted_by_run_task():
    """No activation needed, so this runs everywhere. It catches the two ways the declared
    list and the code drift: a stage added to `PROGRESS_STAGES` that nothing ever reports
    (a label that can never appear), and an emit site deleted while its stage stays declared.
    Reading the source is the point — a stage is only real if `run_task` calls it."""
    import inspect

    body = inspect.getsource(pipeline.run_task)
    constants = {value: name for name, value in vars(pipeline).items()
                 if name.startswith("STEP_") and isinstance(value, str)}
    for stage in pipeline.PROGRESS_STAGES:
        assert f"_progress(on_progress, {constants[stage]})" in body, stage
    # ...and nothing is emitted that was never declared.
    emitted = {constants[s] for s in pipeline.PROGRESS_STAGES}
    for name in constants.values():
        if f"_progress(on_progress, {name})" in body:
            assert name in emitted, name


@requires_local_core
def test_the_real_pipeline_reports_its_stages_in_order():
    from runtime.mvp_runtime.worker import MockProvider

    seen: list[str] = []
    result = pipeline.run_task(
        "이 사업 아이디어를 분석해줘: 구독형 세차",
        provider=MockProvider(), now=NOW, on_progress=seen.append,
    )

    assert result["status"] == "COMPLETED"
    assert seen == [pipeline.STEP_SEARCH, pipeline.STEP_ANALYSIS_WORKER,
                    pipeline.STEP_AUTOMATIC_VALIDATION]
    assert set(seen) <= set(pipeline.PROGRESS_STAGES)


@requires_local_core
def test_the_real_pipeline_runs_unchanged_without_a_callback():
    """`on_progress` is opt-in: the CLI and every library caller pass nothing, and a run
    without it must be exactly the run that existed before."""
    from runtime.mvp_runtime.worker import MockProvider

    with_cb = pipeline.run_task("이 사업 아이디어를 분석해줘: 구독형 세차",
                                provider=MockProvider(), now=NOW, on_progress=lambda _s: None)
    without = pipeline.run_task("이 사업 아이디어를 분석해줘: 구독형 세차",
                                provider=MockProvider(), now=NOW)

    assert with_cb["status"] == without["status"]
    assert with_cb["final_response"] == without["final_response"]


@requires_local_core
def test_the_whole_seam_carries_a_stage_to_the_channel(tmp_path, monkeypatch):
    """Front door to status line, with NOTHING stubbed between them: a real queued run, the
    real pipeline, the real reporter. A callback renamed or unwired on either side would
    leave both halves' own tests green — the #201 lesson, applied to this feature."""
    from runtime.mvp_runtime.worker import MockProvider

    monkeypatch.setattr("runtime.mvp_runtime.operator_feedback.record_delivery",
                        lambda *a, **k: None)
    channel = MockOperatorChannel(inbound=[_msg("이 사업 아이디어를 분석해줘: 구독형 세차")])

    run_operator_once(channel, REG, registry=TaskRegistryStore(tmp_path),
                      provider=MockProvider(), repo_root=None)

    labels = [text.rsplit("상태: ", 1)[1] for _chat, _mid, text in channel.edited]
    assert labels == ["자료 검색 중", "분석 중", "결과 검증 중", "완료 — 결과 전송 중"]


# --- the deliverable is never edited -----------------------------------------

def test_editing_is_wired_only_to_the_status_line(tmp_path, monkeypatch):
    """An analysis Thomas has already read must not change under him — the same reason the
    ledger is append-only. Only the status line is ever an edit target."""
    _stub_run(monkeypatch, stages=["analysis_worker"])
    channel = MockOperatorChannel(inbound=[_msg()])

    run_operator_once(channel, REG, registry=TaskRegistryStore(tmp_path), repo_root=tmp_path)

    status_message_id = "mock-msg-2"
    assert {mid for _chat, mid, _text in channel.edited} == {status_message_id}
    assert all("상태: " in text for _chat, _mid, text in channel.edited)
