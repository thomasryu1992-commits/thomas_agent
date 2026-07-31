"""The read-only door — what the assistant can see, and what it provably cannot do.

The halt door's tests are mostly about one verb that must never get through. These are
about a whole category: ``registry_console`` and ``memory_console`` each implement a
mutating verb next to their reads, and the claim this module makes is that neither is
reachable from here. That claim is worth more than any single read working.

None of these need a local Core — the door renders state and runs no task.
"""

from __future__ import annotations

import json
import socket
import threading

import pytest

from runtime.mvp_runtime import control, read_bridge, socket_door
from runtime.mvp_runtime.control import ACTIVE, KILLED, ControlStore
from runtime.mvp_runtime.errors import ControlBlocked, MvpRuntimeError

NOW = "2026-07-30T09:00:00Z"


class FakeLedger:
    def __init__(self):
        self.control: list[dict] = []
        self.blocks: list[dict] = []

    def append_control(self, entry):
        self.control.append(entry)

    def append_block(self, entry):
        self.blocks.append(entry)

    def last_audit_hash(self):
        return "sha256:" + "ab" * 32


def _apply(request, store, ledger=None, **kw):
    return read_bridge.apply_read(
        request, control_store=store, ledger=ledger or FakeLedger(), now=NOW, **kw
    )


# --- what cannot be reached ---------------------------------------------------

def test_the_table_names_no_mutating_action():
    """Structural. `registry_console` also implements CANCEL and `memory_console` also
    implements PROMOTE; this door reaches neither because no entry produces them. Filtering
    them out later would be a weaker guarantee than never naming them."""
    actions = {spec for _family, spec in read_bridge._READS.values()}
    assert "CANCEL" not in actions
    assert "PROMOTE" not in actions


def test_the_table_names_no_control_verb_that_changes_state():
    """`control` is dispatched to for `status` only. kill/pause/resume/stop belong to the
    halt door and the authenticated channel, and must not have a second way in."""
    control_specs = {spec for family, spec in read_bridge._READS.values() if family == "control"}
    assert control_specs == {control.CMD_STATUS}
    for changing in (control.CMD_KILL, control.CMD_PAUSE, control.CMD_RESUME, control.CMD_STOP):
        assert changing not in control_specs


@pytest.mark.parametrize("verb", [
    "cancel", "promote", "kill", "pause", "resume", "stop", "approve", "reject",
    "feedback", "crypto_statuss", "",
])
def test_mutating_and_unknown_verbs_are_refused(tmp_path, verb):
    store = ControlStore(tmp_path)
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": verb}, store)
    assert exc.value.reason_code in {"VERB_NOT_PERMITTED", "MALFORMED_REQUEST"}
    assert store.load().mode == ACTIVE


def test_a_read_changes_no_control_state_and_writes_no_event(tmp_path):
    store = ControlStore(tmp_path)
    ledger = FakeLedger()
    out = _apply({"command": "runtime_status"}, store, ledger)
    assert out["ok"] is True
    assert store.load().mode == ACTIVE
    assert ledger.control == [], "a read must leave no control event"


# --- the reads themselves -----------------------------------------------------

def test_runtime_status_renders_the_mode(tmp_path):
    store = ControlStore(tmp_path)
    out = _apply({"command": "runtime_status"}, store)
    assert out["command"] == "runtime_status"
    assert ACTIVE in out["reply"]


def test_reads_keep_answering_while_the_runtime_is_killed(tmp_path):
    """`kill_switch.kill_allows: read_only_status` — a halted runtime is exactly when a board
    is most worth reading, so a halt must not blind the assistant."""
    store = ControlStore(tmp_path)
    control.apply_command(store, control.CMD_KILL, actor="test", now=NOW)
    assert store.load().mode == KILLED

    out = _apply({"command": "runtime_status"}, store)
    assert out["ok"] is True
    assert KILLED in out["reply"]


def test_every_table_entry_dispatches_without_an_unhandled_error(tmp_path):
    """Each verb must reach its applier and come back as either a rendered reply or a typed
    refusal. An unwired store is a refusal; a TypeError would mean the dispatch is wrong."""
    store = ControlStore(tmp_path)
    for command in read_bridge._READS:
        argument = "x" if command == "result" else None
        request = {"command": command}
        if argument:
            request["argument"] = argument
        try:
            out = _apply(request, store, repo_root=tmp_path)
            assert isinstance(out["reply"], str)
        except MvpRuntimeError:
            pass  # a typed refusal is a correct outcome for an unwired store


# --- arguments ----------------------------------------------------------------

@pytest.mark.parametrize("command", ["runtime_status", "crypto_status", "tasks", "memory"])
def test_an_argument_is_refused_where_it_would_be_ignored(tmp_path, command):
    """Accepting an argument that changes nothing is how a caller comes to believe it asked
    a narrower question than it did."""
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": command, "argument": "btc"}, ControlStore(tmp_path))
    assert exc.value.reason_code == "ARGUMENT_NOT_ACCEPTED"


def test_the_two_verbs_that_take_an_argument_accept_one(tmp_path):
    store = ControlStore(tmp_path)
    for command in sorted(read_bridge._TAKES_ARGUMENT):
        try:
            _apply({"command": command, "argument": "5"}, store, repo_root=tmp_path)
        except MvpRuntimeError as exc:
            assert exc.reason_code != "ARGUMENT_NOT_ACCEPTED"


def test_a_non_string_argument_is_refused(tmp_path):
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": "history", "argument": 5}, ControlStore(tmp_path))
    assert exc.value.reason_code == "MALFORMED_REQUEST"


# --- fail-closed framing ------------------------------------------------------

@pytest.mark.parametrize("request_obj", [
    "runtime_status", 42, None, [], {}, {"command": ""}, {"command": "   "}, {"argument": "x"},
])
def test_malformed_requests_are_refused(tmp_path, request_obj):
    with pytest.raises(ControlBlocked) as exc:
        _apply(request_obj, ControlStore(tmp_path))
    assert exc.value.reason_code == "MALFORMED_REQUEST"


# --- the socket ---------------------------------------------------------------

unix_only = pytest.mark.skipif(
    not socket_door.UNIX_SOCKETS_AVAILABLE, reason="the read door listens on AF_UNIX"
)


def _ask(path, payload):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(5)
        client.connect(str(path))
        client.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        return json.loads(client.recv(65536).decode("utf-8").strip())


@unix_only
def test_end_to_end_over_the_socket(tmp_path):
    store = ControlStore(tmp_path)
    sock = tmp_path / "r.sock"
    server = read_bridge.open_door(sock, control_store=store, ledger=FakeLedger())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        out = _ask(sock, {"command": "runtime_status"})
        assert out["ok"] is True
        assert ACTIVE in out["reply"]

        refused = _ask(sock, {"command": "cancel", "argument": "t-1"})
        assert refused["ok"] is False
        assert refused["reason_code"] == "VERB_NOT_PERMITTED"
        assert store.load().mode == ACTIVE
    finally:
        server.shutdown()
        server.server_close()


@unix_only
def test_the_doors_do_not_share_a_socket_path(tmp_path):
    """Separate authorities, separate sockets — so a deployment cannot accidentally serve
    switch actions from the read door's file permissions or the reverse."""
    from runtime.mvp_runtime import dispatch_bridge, switch_bridge

    rels = {read_bridge.SOCKET_REL, switch_bridge.SOCKET_REL, dispatch_bridge.SOCKET_REL}
    assert len(rels) == 3
    paths = {
        read_bridge.socket_path(tmp_path),
        switch_bridge.socket_path(tmp_path),
        dispatch_bridge.socket_path(tmp_path),
    }
    assert len(paths) == 3


@unix_only
def test_the_socket_is_not_world_accessible(tmp_path):
    server = read_bridge.open_door(
        tmp_path / "r.sock", control_store=ControlStore(tmp_path), ledger=FakeLedger(),
    )
    try:
        import stat as stat_mod

        mode = (tmp_path / "r.sock").stat().st_mode
        assert not mode & stat_mod.S_IROTH
        assert not mode & stat_mod.S_IWOTH
    finally:
        server.server_close()
