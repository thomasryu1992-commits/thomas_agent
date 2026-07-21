"""R4.3 Operator CLI tests. Dependencies are injected so the loop is exercised without a
network or env; the accepted path runs the pipeline and so needs a local Core."""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.mvp_runtime.approval_store import ApprovalStore
from runtime.mvp_runtime.binding import DEFAULT_POINTER_REL
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.errors import OperatorBlocked
from runtime.mvp_runtime.operator import InboundMessage, MockOperatorChannel, OperatorIdentity
from runtime.mvp_runtime.operator_cli import main
from runtime.mvp_runtime.store import LedgerStore
from runtime.mvp_runtime.working_memory import WorkingMemoryStore
from runtime.mvp_runtime.worker import MockProvider

from tests._helpers import requires_local_core

REG = OperatorIdentity(operator_id="tg-1", chat_id="chat-1")


def _msg(**overrides):
    params = dict(
        text="이 사업 아이디어를 분석해줘: 구독형 반려동물 사료",
        sender_id="tg-1", chat_id="chat-1", chat_type="private",
        is_forwarded=False, channel="telegram_private",
    )
    params.update(overrides)
    return InboundMessage(**params)


def test_missing_registration_fails_closed(tmp_path):
    # No injected registration and no registration file under repo_root => fail closed.
    rc = main([], repo_root=tmp_path)
    assert rc == 2


def test_independent_validation_argument_mapping():
    """Bare flag = every request (R7 behavior unchanged); 'auto' = the R7.1 selective
    policy; absent = off."""
    from runtime.mvp_runtime.operator_cli import _parse_args, _validation_policy

    assert _validation_policy(_parse_args([]).independent_validation) is False
    assert _validation_policy(_parse_args(["--independent-validation"]).independent_validation) is True
    assert _validation_policy(_parse_args(["--independent-validation", "always"]).independent_validation) is True
    assert _validation_policy(_parse_args(["--independent-validation", "auto"]).independent_validation) == "auto"


def test_empty_channel_is_a_clean_noop(capsys):
    ch = MockOperatorChannel()
    rc = main([], channel=ch, registration=REG, provider=MockProvider())
    assert rc == 0
    assert "handled 0, dropped 0" in capsys.readouterr().out
    assert ch.sent == []


def test_multiple_batches_drain_and_stop():
    ch = MockOperatorChannel(inbound=[_msg(sender_id="tg-999")])  # one impostor
    rc = main(["--max-batches", "3", "--sleep-seconds", "0.01"], channel=ch, registration=REG,
              provider=MockProvider(), sleep=lambda _s: None)
    assert rc == 0
    assert ch.sent == []  # impostor dropped, never answered


def test_long_poll_flag_reaches_channel():
    ch = MockOperatorChannel()
    rc = main(["--long-poll-seconds", "25"], channel=ch, registration=REG, provider=MockProvider())
    assert rc == 0
    assert ch.last_long_poll_seconds == 25


@requires_local_core
def test_handles_registered_message_and_replies(capsys, tmp_path):
    ch = MockOperatorChannel(inbound=[_msg(), _msg(sender_id="tg-999")])
    rc = main([], channel=ch, registration=REG, provider=MockProvider(),
              store=LedgerStore(tmp_path / "ledger"),
              working_memory=WorkingMemoryStore(tmp_path / "wm"))
    assert rc == 0
    assert "handled 1, dropped 1" in capsys.readouterr().out
    # The received-working ack, then the answer — both on the verified chat.
    assert [c for c, _ in ch.sent] == ["chat-1", "chat-1"]
    assert "분석 중" in ch.sent[0][1]
    assert "Key findings" in ch.sent[1][1]


@requires_local_core
def test_cli_shares_working_memory(tmp_path):
    wm = WorkingMemoryStore(tmp_path / "wm")
    ch = MockOperatorChannel(inbound=[_msg()])
    rc = main([], channel=ch, registration=REG, provider=MockProvider(), working_memory=wm,
              store=LedgerStore(tmp_path / "ledger"))
    assert rc == 0 and wm.read_all()  # the operator CLI accumulates working memory


# --- loop resilience: transient transport errors must not kill the service ----

class _TransportFlakyChannel:
    """Fails the first N polls with CHANNEL_TRANSPORT, then returns empty batches."""

    def __init__(self, failures: int):
        self.failures = failures
        self.polls = 0
        self.sent: list[tuple[str, str]] = []

    def poll(self, *, long_poll_seconds: int = 0):
        self.polls += 1
        if self.failures > 0:
            self.failures -= 1
            raise OperatorBlocked("CHANNEL_TRANSPORT", "telegram getUpdates failed or timed out")
        return []

    def send(self, chat_id, text):
        self.sent.append((chat_id, text))


def test_transient_transport_error_retries_instead_of_crashing(capsys):
    ch = _TransportFlakyChannel(failures=1)
    sleeps: list[float] = []
    rc = main(["--max-batches", "2"], channel=ch, registration=REG, provider=MockProvider(),
              sleep=sleeps.append)
    assert rc == 0
    assert ch.polls == 3  # one failed poll, then two clean batches
    assert sleeps and sleeps[0] == 2.0  # backoff before the retry
    assert "transient channel error" in capsys.readouterr().err


def test_persistent_transport_error_exhausts_retries_in_finite_mode(capsys):
    ch = _TransportFlakyChannel(failures=99)
    rc = main(["--max-batches", "1"], channel=ch, registration=REG, provider=MockProvider(),
              sleep=lambda _s: None)
    assert rc == 2
    assert "retries exhausted" in capsys.readouterr().err


def test_non_transport_operator_block_still_fails_closed(capsys):
    class _BrokenChannel:
        def poll(self, *, long_poll_seconds: int = 0):
            raise OperatorBlocked("OFFSET_PERSIST_FAILED", "disk full")

        def send(self, chat_id, text):
            pass

    rc = main(["--max-batches", "0"], channel=_BrokenChannel(), registration=REG,
              provider=MockProvider(), sleep=lambda _s: None)
    assert rc == 2
    assert "OFFSET_PERSIST_FAILED" in capsys.readouterr().err


# --- R9 wiring: the production entrypoint passes the approval store -----------

def test_approve_over_the_loop_reaches_the_approval_path(tmp_path, monkeypatch):
    """The deployed loop must answer /approve from the approval path (unknown-id refusal
    here), not run it through the pipeline as a business idea."""
    import runtime.mvp_runtime.operator as operator_mod
    monkeypatch.setattr(operator_mod, "run_task", lambda *a, **k: pytest.fail("run_task must not run"))
    ch = MockOperatorChannel(inbound=[_msg(text="/approve approval_nope")])
    rc = main([], channel=ch, registration=REG, provider=MockProvider(),
              store=LedgerStore(tmp_path / "ledger"),
              working_memory=WorkingMemoryStore(tmp_path / "wm"),
              control_store=ControlStore(tmp_path / "control"),
              approval_store=ApprovalStore(tmp_path / "approvals"))
    assert rc == 0
    assert len(ch.sent) == 1
    assert "no approval with id" in ch.sent[0][1]
