"""R4.3 Operator CLI tests. Dependencies are injected so the loop is exercised without a
network or env; the accepted path runs the pipeline and so needs a local Core."""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.mvp_runtime.binding import DEFAULT_POINTER_REL
from runtime.mvp_runtime.operator import InboundMessage, MockOperatorChannel, OperatorIdentity
from runtime.mvp_runtime.operator_cli import main
from runtime.mvp_runtime.worker import MockProvider

LOCAL_POINTER = Path(__file__).resolve().parents[1] / DEFAULT_POINTER_REL
requires_local_core = pytest.mark.skipif(not LOCAL_POINTER.is_file(), reason="no local Core activation")

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


@requires_local_core
def test_handles_registered_message_and_replies(capsys):
    ch = MockOperatorChannel(inbound=[_msg(), _msg(sender_id="tg-999")])
    rc = main([], channel=ch, registration=REG, provider=MockProvider())
    assert rc == 0
    assert "handled 1, dropped 1" in capsys.readouterr().out
    assert len(ch.sent) == 1 and ch.sent[0][0] == "chat-1"
    assert "Key findings" in ch.sent[0][1]
