"""Tests for the shared door client: the frame, the receive loop, the failure names.

No `mcp` import — this module has none. The shim directory is on `sys.path` via this
directory's conftest. The three tests that open a real AF_UNIX socket skip where the platform
has none (CI's windows-latest lane), the same way the door tests do."""

from __future__ import annotations

import json
import os
import socket
import threading

import pytest

import thomas_door_client as c

unix_only = pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="the door client speaks AF_UNIX")


def _serve_once(path: Path, reply: bytes | None, *, hold: bool = False):
    """A one-shot AF_UNIX server: read a line, answer `reply` (or hold and close nothing)."""
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(path)); srv.listen(1)
    got: dict = {}
    ready = threading.Event()

    def run():
        ready.set()
        conn, _ = srv.accept()
        with conn:
            buf = b""
            while b"\n" not in buf:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
            got["frame"] = json.loads(buf.split(b"\n")[0])
            if hold:
                threading.Event().wait(1.5)
            elif reply is not None:
                conn.sendall(reply)
        srv.close()

    t = threading.Thread(target=run, daemon=True); t.start(); ready.wait()
    return got, t


def test_the_frame_carries_proto_2_the_client_id_and_a_request_id_only_when_asked(monkeypatch):
    monkeypatch.delenv(c.CLIENT_ID_ENV, raising=False)
    plain = c.frame({"command": "runtime_status"})
    assert plain == {"command": "runtime_status", "proto": 2, "client_id": "hermes:telegram"}
    keyed = c.frame({"request": "x"}, request_id="hermes-abc")
    assert keyed["request_id"] == "hermes-abc" and "request_id" not in plain
    assert c.encode({"request": "한글"}) == b'{"request": "\xed\x95\x9c\xea\xb8\x80"}\n'.replace(b"\xed\x95\x9c\xea\xb8\x80", "한글".encode())


def test_an_invalid_client_id_falls_back_instead_of_being_sent(monkeypatch):
    monkeypatch.setenv(c.CLIENT_ID_ENV, "hermes:cron:아침")       # outside the door's charset
    assert c.client_id() == c.DEFAULT_CLIENT_ID
    monkeypatch.setenv(c.CLIENT_ID_ENV, "hermes:cron:morning")
    assert c.client_id() == "hermes:cron:morning"
    monkeypatch.setenv(c.CLIENT_ID_ENV, "x" * 65)
    assert c.client_id() == c.DEFAULT_CLIENT_ID


def test_new_request_ids_are_unique_and_within_the_doors_charset():
    a, b = c.new_request_id(), c.new_request_id()
    assert a != b and c._CLIENT_ID_RE.match(a) and len(a) <= 64


@unix_only
def test_ask_sends_the_v2_frame_and_parses_the_first_line(tmp_path, monkeypatch):
    sock = tmp_path / "read.sock"
    monkeypatch.setenv("THOMAS_READ_SOCKET", str(sock))
    got, t = _serve_once(sock, b'{"ok": true, "proto": 2, "reply": "ACTIVE", "data": {"mode": "ACTIVE"}}\n')
    answer = c.ask("read", {"command": "runtime_status"})
    t.join(2)
    assert got["frame"] == {"command": "runtime_status", "proto": 2, "client_id": "hermes:telegram"}
    assert answer.ok and answer.sent and answer.failure is None
    assert answer.reply == "ACTIVE" and answer.data == {"mode": "ACTIVE"} and not answer.replayed


def test_ask_names_a_missing_socket_without_sending(tmp_path, monkeypatch):
    monkeypatch.setenv("THOMAS_SWITCH_SOCKET", str(tmp_path / "absent.sock"))
    answer = c.ask("switch", {"command": "status"})
    assert answer.failure == c.NO_SOCKET and not answer.sent and not answer.ok
    text = answer.failure_text()
    assert text.startswith("UNAVAILABLE: no switch door at") and "control channel" in text


@unix_only
def test_a_timeout_after_the_frame_was_sent_is_named_as_such(tmp_path, monkeypatch):
    sock = tmp_path / "dispatch.sock"
    monkeypatch.setenv("THOMAS_DISPATCH_SOCKET", str(sock))
    got, t = _serve_once(sock, None, hold=True)
    answer = c.ask("dispatch", {"request": "x"}, request_id="hermes-1", timeout_seconds=0.3)
    assert answer.failure == c.TIMEOUT_AFTER_SEND and answer.sent
    assert got["frame"]["request_id"] == "hermes-1"
    t.join(3)


@unix_only
def test_unparseable_and_empty_replies_are_unclear_not_refusals(tmp_path, monkeypatch):
    sock = tmp_path / "read.sock"
    monkeypatch.setenv("THOMAS_READ_SOCKET", str(sock))
    _, t = _serve_once(sock, b"not json\n")
    answer = c.ask("read", {"command": "tasks"}); t.join(2)
    assert answer.failure == c.UNPARSEABLE and answer.failure_text().startswith("UNCLEAR")
    sock2 = tmp_path / "read2.sock"
    monkeypatch.setenv("THOMAS_READ_SOCKET", str(sock2))
    _, t = _serve_once(sock2, b"")
    answer = c.ask("read", {"command": "tasks"}); t.join(2)
    assert answer.failure == c.EMPTY_REPLY


def test_a_frame_over_the_cap_is_refused_here_not_dropped_mid_wire(tmp_path, monkeypatch):
    sock = tmp_path / "knowledge.sock"
    sock.touch()
    monkeypatch.setenv("THOMAS_KNOWLEDGE_SOCKET", str(sock))
    answer = c.ask("knowledge", {"text": "x" * 100}, max_frame_bytes=50)
    assert answer.failure == c.FRAME_TOO_LARGE and not answer.sent
    assert answer.failure_text().startswith("REFUSED: the request is")


def test_refusals_render_with_their_code_and_the_two_v2_codes_get_a_sentence():
    door = c.DOORS["read"]
    plain = c.Answer(door=door, frame={"ok": False, "reason_code": "VERB_NOT_PERMITTED", "reason": "no"})
    assert plain.refused_text() == "REFUSED [VERB_NOT_PERMITTED]: no."
    proto = c.Answer(door=door, frame={"ok": False, "reason_code": "PROTO_UNSUPPORTED", "reason": "proto 3"})
    assert proto.refused_text().startswith("UNAVAILABLE: the Thomas read door on this machine does not speak door API v2")
    peer = c.Answer(door=door, frame={"ok": False, "reason_code": "PEER_NOT_PERMITTED", "reason": "uid 0"})
    assert "not a request problem" in peer.refused_text()
    running = c.Answer(door=door, frame={"ok": False, "reason_code": "REQUEST_IN_FLIGHT", "data": {"status": "RUNNING"}})
    assert running.data["status"] == "RUNNING" and not running.ok


def test_stamp_puts_an_absolute_instant_in_front():
    text = c.stamp("board")
    assert text.startswith("[SNAPSHOT 20") and text.endswith("\n\nboard")
