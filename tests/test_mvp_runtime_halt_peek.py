"""Option E — a halt reaches the runtime before the analysis it interrupted ends.

The gap, from `docs/proposals/CONTROL_LANE_SEPARATION_V0.1.md` §1.3: while one analysis runs,
`/kill` exists **nowhere in the runtime**. The operator loop is the only process that receives
Telegram and it does not poll while it drains — so the control state file, which the crypto
cycle's `live_route` re-reads immediately before a live entry, could not change for the length
of an analysis. That made this loop's responsiveness a dependency of the money path running in
another container.

What must hold, and each is a direction chosen once:

- The peek **does not claim** what it reads. The cursor is the whole risk of this design:
  advancing it would destroy an ordinary request that arrived in the same batch as a `/kill`.
- Because it does not claim, everything it acts on is seen again — so it acts **only** on
  verbs that are safe to apply twice, and it writes **state only**: no ledger event, no reply.
- `resume` is not peekable, and that is a safety property rather than an omission.
- It does **not** stop the running analysis. That is decision K4, not this.
- Every failure direction is "the run wins".
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime import control, operator
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.errors import OperatorBlocked
from runtime.mvp_runtime.operator import (
    InboundMessage,
    MockOperatorChannel,
    OperatorIdentity,
    peek_for_halt,
    run_operator_once,
)
from runtime.mvp_runtime.store import LedgerStore
from runtime.mvp_runtime.task_registry import TaskRegistryStore

NOW = "2026-07-29T09:00:00Z"
REG = OperatorIdentity(operator_id="tg-12345", chat_id="chat-777")


def _msg(text, sender_id="tg-12345"):
    return InboundMessage(text=text, sender_id=sender_id, chat_id="chat-777")


def _control(tmp_path):
    return ControlStore(tmp_path / "control")


# --- the verb set IS the safety argument -------------------------------------

def test_only_halt_verbs_are_peekable():
    """The peek does not claim what it reads, so everything here is seen twice. `kill`/`pause`
    are the same runtime applied twice; `resume`, `approve`, `status` are not."""
    assert operator.PEEKABLE_HALT_VERBS == {control.CMD_KILL, control.CMD_PAUSE}


def test_resume_is_not_peekable_and_that_is_the_point(tmp_path):
    """A peek that could resume would let a halt be undone by a message sent BEFORE it, re-read
    out of order. The absence is the property."""
    assert control.CMD_RESUME not in operator.PEEKABLE_HALT_VERBS

    store = _control(tmp_path)
    store.save(control.ControlState(mode=control.KILLED, updated_by="tg-12345",
                                    updated_at=NOW, reason="halted"))
    channel = MockOperatorChannel(inbound=[_msg("/resume")])

    assert peek_for_halt(channel, registration=REG, control_store=store, now=NOW) is None
    assert store.load().mode == control.KILLED         # still halted


@pytest.mark.parametrize("verb, mode", [("/kill", control.KILLED), ("/pause", control.PAUSED)])
def test_a_halt_is_applied_to_the_state_immediately(tmp_path, verb, mode):
    """The one thing that has to happen: the state file changes, because that is what the
    crypto cycle in the other container reads before a live entry."""
    store = _control(tmp_path)
    channel = MockOperatorChannel(inbound=[_msg(verb)])

    applied = peek_for_halt(channel, registration=REG, control_store=store, now=NOW)

    assert applied == verb.lstrip("/")
    assert store.load().mode == mode
    assert store.load().execution_allowed is False


# --- the cursor: the whole risk of this design -------------------------------

def test_the_peek_does_not_claim_what_it_reads(tmp_path):
    """Advancing the cursor here would claim the whole batch while handling only the halt verb
    in it — silently destroying an ordinary request sent in the same second. The messages stay
    queued for the next poll."""
    store = _control(tmp_path)
    channel = MockOperatorChannel(inbound=[_msg("이 아이디어를 분석해줘"), _msg("/kill")])

    peek_for_halt(channel, registration=REG, control_store=store, now=NOW)

    assert len(channel.inbound) == 2            # nothing drained
    assert channel.peeks == 1


def test_the_real_transport_peek_leaves_the_cursor_where_it_found_it(tmp_path, monkeypatch):
    """The property above, on the transport that actually has a cursor — the mock cannot fail
    the way the real one would. `getUpdates` is intercepted; no socket is opened.

    This is the test that matters: an offset advanced by a peek is a message destroyed, and
    the only place that can happen is here."""
    from runtime.mvp_runtime.crypto import live_pnl  # noqa: F401  (import parity with suite)
    from runtime.mvp_runtime.operator import TelegramChannel
    from runtime.mvp_runtime.safety_gate import Authorization

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    state_path = tmp_path / "offset.json"
    channel = TelegramChannel(
        authorization=Authorization(
            flags=operator._NETWORK_FLAGS, provider_id="telegram",
            activation_sha256="sha256:test", expires_at="2999-01-01T00:00:00Z",
            evidence_ref=".runtime_governance_state/evidence.md",
        ),
        state_path=state_path,
    )
    calls: list[dict] = []

    def _fake_call(_self, _token, method, params, *, timeout):
        calls.append({"method": method, **params})
        return {"ok": True, "result": [{
            "update_id": 4242,
            "message": {"text": "/kill", "chat": {"id": "chat-777", "type": "private"},
                        "from": {"id": "tg-12345"}},
        }]}

    monkeypatch.setattr(TelegramChannel, "_call", _fake_call)

    messages = channel.peek()

    assert [m.text for m in messages] == ["/kill"]
    assert calls[0]["offset"] == 0 and calls[0]["timeout"] == 0   # non-blocking, same offset
    assert not state_path.exists(), "the peek persisted a cursor it had no right to claim"
    # And the very next peek asks for the SAME update again — nothing was consumed.
    channel.peek()
    assert calls[1]["offset"] == 0


def test_the_peek_writes_no_ledger_event_and_sends_no_reply(tmp_path):
    """Both belong to the normal handling that follows, because the same message will be
    delivered again. A peek that replied would reply twice per halt, and a peek that audited
    would put two control events in the ledger for one `/kill`."""
    from runtime.mvp_runtime.store import CONTROL_FILE

    store = _control(tmp_path)
    channel = MockOperatorChannel(inbound=[_msg("/kill")])
    control_log = tmp_path / "ledger" / CONTROL_FILE

    peek_for_halt(channel, registration=REG, control_store=store, now=NOW)

    assert channel.sent == []
    assert not control_log.exists(), "the peek wrote a control event the normal path will write"
    # ...and the normal applier DOES write one, so the assertion above is about the peek
    # rather than about control events being unwritable here.
    ledger = LedgerStore(tmp_path / "ledger")
    control.apply_command(store, control.CMD_KILL, actor="tg-12345", now=NOW, ledger=ledger)
    assert control_log.exists() and control_log.read_text(encoding="utf-8").strip()


def test_applying_the_same_halt_twice_is_the_same_runtime(tmp_path):
    """The peek applies it, then the next poll applies it again through the normal path. That
    is only acceptable because the transition is idempotent — pinned here rather than assumed."""
    store = _control(tmp_path)
    channel = MockOperatorChannel(inbound=[_msg("/kill")])

    peek_for_halt(channel, registration=REG, control_store=store, now=NOW)
    first = store.load().mode
    control.apply_command(store, control.CMD_KILL, actor="tg-12345", now=NOW)

    assert first == store.load().mode == control.KILLED


# --- identity, and what the peek refuses -------------------------------------

def test_an_unregistered_sender_cannot_halt_the_runtime(tmp_path):
    """The peek runs the same identity gate as the normal path — an unverified `/kill` is not
    a shortcut into the control state."""
    store = _control(tmp_path)
    channel = MockOperatorChannel(inbound=[_msg("/kill", sender_id="tg-99999")])

    assert peek_for_halt(channel, registration=REG, control_store=store, now=NOW) is None
    assert store.load().execution_allowed is True


def test_ordinary_messages_are_ignored_by_the_peek(tmp_path):
    store = _control(tmp_path)
    channel = MockOperatorChannel(inbound=[_msg("분석해줘"), _msg("/status"), _msg("/tasks")])

    assert peek_for_halt(channel, registration=REG, control_store=store, now=NOW) is None
    assert store.load().execution_allowed is True


# --- failure directions: the run wins ----------------------------------------

def test_a_transport_failure_costs_nothing(tmp_path):
    class _PeekExplodes(MockOperatorChannel):
        def peek(self):
            raise OperatorBlocked("CHANNEL_TRANSPORT", "no")

    store = _control(tmp_path)
    assert peek_for_halt(_PeekExplodes(), registration=REG, control_store=store, now=NOW) is None
    assert store.load().execution_allowed is True      # unchanged, and no exception escaped


def test_a_transport_that_cannot_peek_degrades_to_the_old_behaviour(tmp_path):
    class _NoPeek(MockOperatorChannel):
        peek = None

    store = _control(tmp_path)
    assert peek_for_halt(_NoPeek(), registration=REG, control_store=store, now=NOW) is None


def test_without_a_control_store_the_peek_does_nothing():
    """The library calling mode has no console; there is nothing to halt."""
    channel = MockOperatorChannel(inbound=[_msg("/kill")])
    assert peek_for_halt(channel, registration=REG, control_store=None, now=NOW) is None
    assert channel.peeks == 0                          # not even read


# --- on the real drain --------------------------------------------------------

def _stub_run(monkeypatch, stages=("analysis_worker",)):
    def _run(request, **kwargs):
        for stage in stages:
            kwargs["on_progress"](stage)
        return {"status": "COMPLETED", "final_response": f"분석: {request}",
                "records": {"received_task": {"identity": {"task_id": "t", "trace_id": "tr"}}}}

    monkeypatch.setattr("runtime.mvp_runtime.operator.run_task", _run)
    monkeypatch.setattr("runtime.mvp_runtime.operator_feedback.record_delivery",
                        lambda *a, **k: None)


def test_a_kill_sent_during_an_analysis_lands_before_it_ends(tmp_path, monkeypatch):
    """The seam, end to end: the request is queued and drained, `/kill` arrives while the
    pipeline is mid-stage, and the control state is halted BEFORE the run returns — which is
    what the crypto cycle in the other container reads."""
    seen: list[bool] = []
    store = _control(tmp_path)

    def _run(request, **kwargs):
        # `/kill` shows up mid-run, exactly as Telegram would deliver it.
        channel.inbound.append(_msg("/kill"))
        kwargs["on_progress"]("analysis_worker")
        seen.append(store.load().execution_allowed)     # observed INSIDE the run
        return {"status": "COMPLETED", "final_response": "분석 결과",
                "records": {"received_task": {"identity": {"task_id": "t", "trace_id": "tr"}}}}

    monkeypatch.setattr("runtime.mvp_runtime.operator.run_task", _run)
    monkeypatch.setattr("runtime.mvp_runtime.operator_feedback.record_delivery",
                        lambda *a, **k: None)
    channel = MockOperatorChannel(inbound=[_msg("이 아이디어를 분석해줘")])

    run_operator_once(channel, REG, registry=TaskRegistryStore(tmp_path),
                      control_store=store, repo_root=tmp_path)

    assert seen == [False], "the halt did not reach the state while the analysis was running"
    assert store.load().mode == control.KILLED


def test_the_running_analysis_still_finishes(tmp_path, monkeypatch):
    """Option E receives a halt; it does not abort. Stopping the running analysis needs an
    abort path and a registry terminal for an interrupted RUNNING entry — decision K4."""
    store = _control(tmp_path)

    def _run(request, **kwargs):
        channel.inbound.append(_msg("/kill"))
        kwargs["on_progress"]("analysis_worker")
        return {"status": "COMPLETED", "final_response": "분석 결과",
                "records": {"received_task": {"identity": {"task_id": "t", "trace_id": "tr"}}}}

    monkeypatch.setattr("runtime.mvp_runtime.operator.run_task", _run)
    monkeypatch.setattr("runtime.mvp_runtime.operator_feedback.record_delivery",
                        lambda *a, **k: None)
    channel = MockOperatorChannel(inbound=[_msg("분석해줘")])

    run_operator_once(channel, REG, registry=TaskRegistryStore(tmp_path),
                      control_store=store, repo_root=tmp_path)

    assert any("분석 결과" in text for _chat, text in channel.sent)


def test_the_kill_is_still_handled_normally_afterwards(tmp_path, monkeypatch):
    """Because the peek claimed nothing, the next poll delivers the same `/kill` and answers it
    the usual way — one reply, from one place."""
    store = _control(tmp_path)
    _stub_run(monkeypatch)
    channel = MockOperatorChannel(inbound=[_msg("분석해줘")])

    run_operator_once(channel, REG, registry=TaskRegistryStore(tmp_path),
                      control_store=store, repo_root=tmp_path)
    channel.inbound.append(_msg("/kill"))
    run_operator_once(channel, REG, registry=TaskRegistryStore(tmp_path),
                      control_store=store, repo_root=tmp_path)

    assert store.load().mode == control.KILLED
    assert any("KILLED" in text or "정지" in text or "kill" in text.lower()
               for _chat, text in channel.sent)
