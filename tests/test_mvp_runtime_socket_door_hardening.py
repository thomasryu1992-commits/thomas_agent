"""How a bridge door stands: one per path, bounded, peer-checked, and quiet about its insides.

These are the four properties a door has independently of which verbs it carries, so they are
tested here once rather than three times over. Each one replaces an assumption the transport
used to make:

- binding removed a socket file it found, on the reasoning that a live server holds no lock on
  the path — which is true, and is exactly why removing it produced two live doors;
- a thread per connection with no ceiling, on a socket whose client is a model that retries;
- file permissions as the whole of authentication, which authorize a group and not a process;
- ``str(exc)`` in the reply for any exception a door did not anticipate.

None of these need a local Core: the transport does not read one.
"""

from __future__ import annotations

import json
import os
import socket
import threading

import pytest

from runtime.mvp_runtime import socket_door
from runtime.mvp_runtime.errors import ControlBlocked

unix_only = pytest.mark.skipif(
    not socket_door.UNIX_SOCKETS_AVAILABLE, reason="the bridge doors listen on AF_UNIX"
)


def _serving(path, apply, **kwargs):
    """A door on ``path``, actually accepting. Caller closes."""
    server = socket_door.SocketDoor(path, apply, **kwargs)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _ask(path, payload, *, timeout=5.0):
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(path))
        client.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        return json.loads(client.recv(65536).decode("utf-8"))
    finally:
        client.close()


def _quiet_ask(path, payload, into=None):
    """``_ask`` for the concurrency tests: a client that is *meant* to be refused, timed out or
    reset must not raise out of its thread and fail the run for the reason under test."""
    try:
        reply = _ask(path, payload, timeout=10.0)
    except (OSError, ValueError):
        return
    if into is not None:
        into.append(reply)


# --- one door per path ---------------------------------------------------------

@unix_only
def test_a_stale_socket_is_still_cleared(tmp_path):
    """The case the old unconditional unlink was actually for must keep working.

    An unclean shutdown leaves the pathname behind with nothing listening on it. That file
    would make bind() fail, so a restart has to remove it — the probe only distinguishes this
    from the case where a door is still there.
    """
    sock = tmp_path / "bridge" / "d.sock"
    sock.parent.mkdir(parents=True)
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(sock))
    stale.close()                       # closes the server, leaves the pathname
    assert sock.is_socket()

    server = socket_door.SocketDoor(sock, lambda request: {"ok": True})
    try:
        assert sock.is_socket()
    finally:
        server.server_close()


@unix_only
def test_a_live_door_is_not_replaced_by_a_second_one(tmp_path):
    """Unlinking a live socket's pathname does not close its server — it forks the door.

    The old process keeps its inode and its established connections; every new connection
    reaches whoever bound second. Both then write the same state under the same locks, so
    nothing corrupts and nothing tells the operator which process answered.
    """
    sock = tmp_path / "bridge" / "d.sock"
    first = _serving(sock, lambda request: {"ok": True, "door": "first"})
    try:
        with pytest.raises(ControlBlocked) as exc:
            socket_door.SocketDoor(sock, lambda request: {"ok": True, "door": "second"})
        assert exc.value.reason_code == "BRIDGE_ALREADY_RUNNING"
        # The refusal left the running door untouched — it is still the one answering.
        assert _ask(sock, {"any": "frame"})["door"] == "first"
    finally:
        first.shutdown()
        first.server_close()


@unix_only
def test_the_probe_reads_a_stale_path_as_dead_and_a_served_one_as_live(tmp_path):
    sock = tmp_path / "bridge" / "d.sock"
    sock.parent.mkdir(parents=True)
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(sock))
    stale.close()
    assert socket_door.door_is_live(sock) is False

    sock.unlink()
    server = _serving(sock, lambda request: {"ok": True})
    try:
        assert socket_door.door_is_live(sock) is True
    finally:
        server.shutdown()
        server.server_close()


@unix_only
def test_an_absent_path_is_not_live(tmp_path):
    assert socket_door.door_is_live(tmp_path / "nothing.sock") is False


# --- a bounded number of handlers ----------------------------------------------

@unix_only
def test_no_more_than_the_ceiling_run_at_once(tmp_path):
    """The bound that matters is how many handlers are inside the apply function together —
    not how many sockets are open, which is what a queue length measures."""
    sock = tmp_path / "bridge" / "d.sock"
    ceiling = 2
    lock = threading.Lock()
    live = {"now": 0, "peak": 0}
    saturated = threading.Event()
    release = threading.Event()

    def _apply(request):
        with lock:
            live["now"] += 1
            live["peak"] = max(live["peak"], live["now"])
            if live["now"] >= ceiling:
                saturated.set()
        release.wait(timeout=5)
        with lock:
            live["now"] -= 1
        return {"ok": True}

    server = _serving(sock, _apply, max_concurrent_requests=ceiling)
    clients = [threading.Thread(target=_quiet_ask, args=(sock, {"n": n}), daemon=True)
               for n in range(8)]
    try:
        for client in clients:
            client.start()
        # Reaching the ceiling is what stops this being vacuous: a door that ran ONE at a time
        # would also never exceed two.
        assert saturated.wait(timeout=5), "the door never reached its own ceiling"
        assert live["peak"] <= ceiling
    finally:
        release.set()
        for client in clients:
            client.join(timeout=5)
        server.shutdown()
        server.server_close()


@unix_only
def test_past_admission_a_connection_is_answered_and_closed(tmp_path):
    """Refused rather than left hanging: a client that reads a closed socket cannot tell a busy
    door from a dead one, and the retry it makes is the one that was already too many."""
    sock = tmp_path / "bridge" / "d.sock"
    release = threading.Event()
    replies: list[dict] = []

    def _apply(request):
        release.wait(timeout=5)
        return {"ok": True}

    # One running, ADMISSION_SLACK queued; everything past that is refused.
    server = _serving(sock, _apply, max_concurrent_requests=1)
    flood = 4 * (1 + socket_door.ADMISSION_SLACK)
    clients = [threading.Thread(target=_quiet_ask, args=(sock, {"n": n}, replies), daemon=True)
               for n in range(flood)]
    try:
        for client in clients:
            client.start()
        for client in clients:
            client.join(timeout=1)
        busy = [r for r in replies if r.get("reason_code") == "BRIDGE_BUSY"]
        assert busy, f"{flood} simultaneous connections produced no refusal"
        assert "retry" in busy[0]["reason"]
    finally:
        release.set()
        for client in clients:
            client.join(timeout=5)
        server.shutdown()
        server.server_close()


@unix_only
def test_a_sequential_client_is_never_refused(tmp_path):
    """The regression ADMISSION_SLACK exists for. A handler releases its slot *after* the reply
    is sent, so a client that reads its answer and immediately reconnects can arrive while the
    slot is still held. At a ceiling of 1 that made a strictly sequential caller — the only kind
    the doors actually have — collide with itself."""
    sock = tmp_path / "bridge" / "d.sock"
    server = _serving(sock, lambda request: {"ok": True}, max_concurrent_requests=1)
    try:
        for _ in range(40):
            assert _ask(sock, {"command": "read"})["ok"] is True
    finally:
        server.shutdown()
        server.server_close()


@unix_only
def test_a_door_survives_more_connections_than_it_has_threads(tmp_path):
    """A pool rather than a thread per connection: the refused ones must not leave the door
    unable to serve the next legitimate request."""
    sock = tmp_path / "bridge" / "d.sock"
    server = _serving(sock, lambda request: {"ok": True}, max_concurrent_requests=2)
    replies: list[dict] = []
    clients = [threading.Thread(target=_quiet_ask, args=(sock, {"n": n}, replies), daemon=True)
               for n in range(30)]
    try:
        for client in clients:
            client.start()
        for client in clients:
            client.join(timeout=10)
        assert _ask(sock, {"command": "read"})["ok"] is True
    finally:
        server.shutdown()
        server.server_close()


@unix_only
def test_a_door_must_serve_at_least_one_request(tmp_path):
    """`unix_only` because the ceiling check is not the FIRST refusal in the constructor and
    should not be: on a platform with no AF_UNIX the door cannot exist at all, so
    `UNIX_SOCKETS_UNAVAILABLE` is the more fundamental answer and comes first. Reaching this
    one therefore requires a platform where a door is possible. (Caught by Windows CI, which
    is the only place that ordering is observable.)"""
    with pytest.raises(ControlBlocked) as exc:
        socket_door.SocketDoor(tmp_path / "d.sock", lambda request: {"ok": True},
                               max_concurrent_requests=0)
    assert exc.value.reason_code == "BRIDGE_CONCURRENCY_INVALID"


def test_the_stop_door_is_not_ceilinged_at_one():
    """`switch_bridge` carries the halt and the start, and the start is the slow one. A ceiling
    of 1 would let an in-flight `enable` refuse the `disable` behind it — the failure that made
    this door its own service, reproduced inside it."""
    from runtime.mvp_runtime import dispatch_bridge, read_bridge, switch_bridge

    assert switch_bridge.MAX_CONCURRENT_REQUESTS > 1
    # Ordering is the claim: reads are cheapest, a dispatch holds its slot for a pipeline run.
    assert read_bridge.MAX_CONCURRENT_REQUESTS > switch_bridge.MAX_CONCURRENT_REQUESTS
    assert dispatch_bridge.MAX_CONCURRENT_REQUESTS < switch_bridge.MAX_CONCURRENT_REQUESTS


# --- errors that do not describe the runtime -----------------------------------

def test_an_untyped_failure_does_not_reach_the_caller_as_text(capsys):
    """The assistant is the least trusted end of this socket. An unanticipated exception's text
    is the runtime describing its own internals to it — store paths, config, dependency errors."""
    secret = "/app/.runtime_governance_state/approvals/thomas_only.jsonl"
    payload = socket_door.internal_error_payload(RuntimeError(secret), "switch")

    assert payload["ok"] is False
    assert payload["reason_code"] == "BRIDGE_ERROR"
    assert secret not in json.dumps(payload)
    assert payload["error_ref"]
    # The detail is moved, not lost: the same ref appears in the service log.
    logged = capsys.readouterr().err
    assert secret in logged
    assert payload["error_ref"] in logged
    assert "RuntimeError" in logged


def test_two_failures_do_not_share_a_reference(capsys):
    first = socket_door.internal_error_payload(RuntimeError("a"), "switch")
    second = socket_door.internal_error_payload(RuntimeError("b"), "switch")
    capsys.readouterr()
    assert first["error_ref"] != second["error_ref"]


@unix_only
def test_a_typed_refusal_still_says_what_it_refused(tmp_path, capsys):
    """The redaction is for exceptions nobody anticipated. A `ControlBlocked` message is
    authored, operator-facing text — that is the whole point of a typed refusal — and going
    back verbatim is what makes `APPROVAL_REQUIRED` carry the id to approve."""
    sock = tmp_path / "bridge" / "d.sock"

    def _apply(request):
        raise ControlBlocked("VERB_NOT_PERMITTED", "'resume' is not permitted here")

    server = _serving(sock, _apply)
    try:
        reply = _ask(sock, {"command": "resume"})
        assert reply["reason_code"] == "VERB_NOT_PERMITTED"
        assert "resume" in reply["reason"]
    finally:
        server.shutdown()
        server.server_close()


@unix_only
def test_an_untyped_failure_over_a_real_socket_keeps_the_door_standing(tmp_path):
    sock = tmp_path / "bridge" / "d.sock"

    def _apply(request):
        if request.get("command") == "boom":
            raise RuntimeError("/app/.runtime_governance_state/secret_path")
        return {"ok": True}

    server = _serving(sock, _apply)
    try:
        broken = _ask(sock, {"command": "boom"})
        assert broken["reason_code"] == "BRIDGE_ERROR"
        assert "secret_path" not in json.dumps(broken)
        # Then keeps standing, which is why the handler catches at all.
        assert _ask(sock, {"command": "fine"})["ok"] is True
    finally:
        server.shutdown()
        server.server_close()


# --- peer identity --------------------------------------------------------------

def test_no_configured_uid_means_no_uid_check(monkeypatch):
    monkeypatch.delenv(socket_door.CLIENT_UID_ENV, raising=False)
    assert socket_door.resolve_client_uids() == frozenset()


@pytest.mark.parametrize("raw,expected", [
    ("10000", {10000}),
    (" 10000 ", {10000}),
    ("10000,10001", {10000, 10001}),
    ("10000, 10001,", {10000, 10001}),
])
def test_configured_uids_are_read(monkeypatch, raw, expected):
    """Comma-separated because the operator's shell and the assistant's container are two
    legitimate peers, and naming one must not exclude the other."""
    monkeypatch.setenv(socket_door.CLIENT_UID_ENV, raw)
    assert socket_door.resolve_client_uids() == frozenset(expected)


@pytest.mark.parametrize("raw", ["hermes", "10000,hermes", "-1", ","])
def test_an_unusable_uid_refuses_rather_than_being_dropped(monkeypatch, raw):
    """An allowlist that silently lost a member is the shape of grant this door got wrong once
    already: the configuration said one thing and the running system did another."""
    monkeypatch.setenv(socket_door.CLIENT_UID_ENV, raw)
    with pytest.raises(ControlBlocked) as exc:
        socket_door.resolve_client_uids()
    assert exc.value.reason_code == "BRIDGE_CLIENT_UID_INVALID"


@unix_only
def test_a_permitted_uid_is_served(tmp_path, monkeypatch):
    monkeypatch.setenv(socket_door.CLIENT_UID_ENV, str(os.getuid()))
    sock = tmp_path / "bridge" / "d.sock"
    server = _serving(sock, lambda request: {"ok": True})
    try:
        assert _ask(sock, {"command": "read"})["ok"] is True
    finally:
        server.shutdown()
        server.server_close()


@unix_only
def test_an_unpermitted_uid_is_refused(tmp_path, monkeypatch):
    """`SO_PEERCRED` is what the kernel recorded at connect() time, so unlike anything in the
    frame the caller cannot choose it.

    The refusal is *answered*, which is the part that constrains the implementation: the frame
    is drained before the check so the reply is not a reset on a socket the peer is still
    writing to. What must not happen is the frame being acted on, and that is what is asserted.
    """
    monkeypatch.setenv(socket_door.CLIENT_UID_ENV, str(os.getuid() + 7))
    sock = tmp_path / "bridge" / "d.sock"
    reached = []
    server = _serving(sock, lambda request: reached.append(request) or {"ok": True})
    try:
        reply = _ask(sock, {"command": "read"})
        assert reply["ok"] is False
        assert reply["reason_code"] == "PEER_NOT_PERMITTED"
        assert reached == [], "an unpermitted peer's frame must never reach the apply function"
        # The refusal does not quote the allowlist back to whoever tripped it.
        assert str(os.getuid() + 7) not in reply["reason"]
    finally:
        server.shutdown()
        server.server_close()


@unix_only
def test_a_configured_check_that_cannot_run_refuses(tmp_path, monkeypatch):
    """A check the deployment asked for and the platform cannot perform is not a check that
    passes. `peer_credentials` returns None only where SO_PEERCRED does not exist."""
    monkeypatch.setenv(socket_door.CLIENT_UID_ENV, str(os.getuid()))
    monkeypatch.setattr(socket_door, "peer_credentials", lambda sock: None)
    sock = tmp_path / "bridge" / "d.sock"
    server = _serving(sock, lambda request: {"ok": True})
    try:
        reply = _ask(sock, {"command": "read"})
        assert reply["reason_code"] == "PEER_CREDENTIALS_UNAVAILABLE"
    finally:
        server.shutdown()
        server.server_close()


@unix_only
def test_peer_credentials_report_this_process(tmp_path):
    """The gid here is the peer's *effective* gid, not its supplementary groups — which is why
    it must never be compared against MVP_BRIDGE_CLIENT_GID, the group the assistant reaches
    these sockets through."""
    sock = tmp_path / "bridge" / "d.sock"
    seen = {}

    def _apply(request):
        return {"ok": True}

    server = socket_door.SocketDoor(sock, _apply)
    original = socket_door.peer_credentials

    def _capture(conn):
        seen["creds"] = original(conn)
        return seen["creds"]

    server.allowed_client_uids = frozenset({os.getuid()})
    socket_door.peer_credentials = _capture
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _ask(sock, {"command": "read"})
        pid, uid, _gid = seen["creds"]
        assert uid == os.getuid()
        assert pid == os.getpid()
    finally:
        socket_door.peer_credentials = original
        server.shutdown()
        server.server_close()


# --- the frame envelope (door API v2) ------------------------------------------
#
# Three optional keys every door reads the same way, defined beside the transport. What is
# pinned: a frame that names no version speaks v1 and gets no `proto` back; a version the door
# does not speak is refused by name, never served as v1; `client_id` is a name, not a payload;
# a typed refusal raised inside `apply` still carries the envelope back over the socket; and the
# transport's own refusals — BUSY, a frame that is not JSON — carry none, because nothing was read.

def test_a_frame_that_names_no_proto_speaks_v1():
    assert socket_door.negotiate_proto({"command": "status"}) == socket_door.DEFAULT_PROTO
    assert socket_door.negotiate_proto({"proto": 2}) == 2
    assert socket_door.negotiate_proto("not a dict") == socket_door.DEFAULT_PROTO


@pytest.mark.parametrize("proto", [3, 0, -1, "2", 2.0, True, [2], {}])
def test_an_unsupported_proto_is_refused_by_name_not_served_as_v1(proto):
    with pytest.raises(ControlBlocked) as exc:
        socket_door.negotiate_proto({"proto": proto})
    assert exc.value.reason_code == "PROTO_UNSUPPORTED"


def test_client_id_is_a_name_not_a_payload():
    assert socket_door.client_id_of({}) is None
    assert socket_door.client_id_of({"client_id": " hermes:cron:silent-watch "}) == "hermes:cron:silent-watch"
    for bad in ("", "   ", 42, "x" * (socket_door.MAX_CLIENT_ID_LENGTH + 1), "has space", "semi;colon", "quote'"):
        with pytest.raises(ControlBlocked) as exc:
            socket_door.client_id_of({"client_id": bad})
        assert exc.value.reason_code == "MALFORMED_REQUEST", bad


def test_the_envelope_echoes_only_what_the_frame_named_and_always_carries_data():
    v1 = socket_door.envelope({"ok": True, "reply": "x"}, request={"command": "status"})
    assert "proto" not in v1 and "client_id" not in v1
    assert v1["data"] is None
    v2 = socket_door.envelope(
        {"ok": True, "reply": "x"}, request={"command": "status", "proto": 2, "client_id": "hermes:dm"},
        data={"mode": "ACTIVE"},
    )
    assert v2["proto"] == 2 and v2["client_id"] == "hermes:dm"
    assert v2["data"] == {"mode": "ACTIVE"}
    assert v2["reply"] == "x"  # the text a v1 client reads is untouched


def test_a_refusal_payload_echoes_a_well_formed_envelope_and_nothing_malformed():
    exc = ControlBlocked("VERB_NOT_PERMITTED", "no such verb")
    echoed = socket_door.refusal_payload(exc, {"command": "x", "proto": 2, "client_id": "hermes:dm", "request_id": "req-1"})
    assert echoed["ok"] is False and echoed["reason_code"] == "VERB_NOT_PERMITTED"
    assert echoed["proto"] == 2 and echoed["client_id"] == "hermes:dm" and echoed["request_id"] == "req-1"
    assert "data" not in echoed
    bare = socket_door.refusal_payload(exc, {"command": "x", "proto": 9, "client_id": "bad id", "request_id": 42})
    assert "proto" not in bare and "client_id" not in bare and "request_id" not in bare
    assert socket_door.refusal_payload(exc, None) == {"ok": False, "reason_code": "VERB_NOT_PERMITTED", "reason": str(exc)}


def test_plain_keeps_a_consoles_structured_keys_and_drops_its_text():
    outcome = {"reply": "rendered", "action": "TASKS_LISTED", "count": 2, "entries": object(), "nested": {"a": 1}}
    assert socket_door.plain(outcome) == {"action": "TASKS_LISTED", "count": 2, "nested": {"a": 1}}
    assert socket_door.plain("not a dict") == {}


@unix_only
def test_a_typed_refusal_over_the_socket_carries_the_envelope_back(tmp_path):
    def apply(request):
        raise ControlBlocked("VERB_NOT_PERMITTED", "this door serves nothing")

    server = _serving(tmp_path / "refusing.sock", apply)
    try:
        reply = _ask(tmp_path / "refusing.sock", {"command": "x", "proto": 2, "client_id": "hermes:dm"})
    finally:
        server.shutdown()
    assert reply["ok"] is False and reply["reason_code"] == "VERB_NOT_PERMITTED"
    assert reply["proto"] == 2 and reply["client_id"] == "hermes:dm"


@unix_only
def test_a_frame_that_is_not_json_gets_a_bare_refusal_with_no_envelope(tmp_path):
    server = _serving(tmp_path / "bare.sock", lambda request: {"ok": True})
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(5.0)
    try:
        client.connect(str(tmp_path / "bare.sock"))
        client.sendall(b'{"proto": 2, not json\n')
        reply = json.loads(client.recv(65536).decode("utf-8"))
    finally:
        client.close()
        server.shutdown()
    assert reply["reason_code"] == "MALFORMED_REQUEST"
    assert "proto" not in reply and "data" not in reply and "client_id" not in reply


@unix_only
def test_a_refusal_with_detail_carries_it_as_data_over_the_socket(tmp_path):
    def apply(request):
        raise ControlBlocked("REQUEST_IN_FLIGHT", "still running", data={"status": "RUNNING"})

    server = _serving(tmp_path / "detail.sock", apply)
    try:
        reply = _ask(tmp_path / "detail.sock", {"command": "x", "proto": 2})
    finally:
        server.shutdown()
    assert reply["reason_code"] == "REQUEST_IN_FLIGHT" and reply["data"] == {"status": "RUNNING"} and reply["proto"] == 2
