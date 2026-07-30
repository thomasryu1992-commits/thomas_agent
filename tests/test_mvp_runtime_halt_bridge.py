"""The halt-only door — permission surface, attributability, and the socket.

The point of this module is a negative: an external assistant can stop this runtime and can
never start it. Most of what follows tests that ``resume`` does not get through, from more
than one direction, because a single assertion guarding a money path is a single assertion
away from being deleted by someone who thinks it is redundant.

None of these need a local Core — the door short-circuits before any task runs.
"""

from __future__ import annotations

import json
import socket
import threading

import pytest

from runtime.mvp_runtime import control, halt_bridge
from runtime.mvp_runtime.control import ACTIVE, KILLED, PAUSED, ControlStore
from runtime.mvp_runtime.errors import ControlBlocked

NOW = "2026-07-30T09:00:00Z"


class FakeLedger:
    def __init__(self):
        self.control: list[dict] = []

    def append_control(self, entry):
        self.control.append(entry)

    def last_audit_hash(self):
        return "sha256:" + "ab" * 32


def _apply(request, store, ledger=None):
    return halt_bridge.apply_halt(
        request, control_store=store, ledger=ledger or FakeLedger(), now=NOW,
    )


# --- the two verbs it carries -------------------------------------------------

def test_kill_halts_the_runtime(tmp_path):
    store = ControlStore(tmp_path)
    out = _apply({"command": "kill", "reason": "assistant: stop trading now"}, store)
    assert out["ok"] is True
    assert store.load().mode == KILLED
    assert store.load().execution_allowed is False


def test_pause_halts_the_runtime(tmp_path):
    store = ControlStore(tmp_path)
    out = _apply({"command": "pause", "reason": "assistant: hold"}, store)
    assert out["ok"] is True
    assert store.load().mode == PAUSED


def test_kill_is_idempotent(tmp_path):
    """Halting twice is the same runtime — the property that makes this verb safe to expose
    to a caller whose messages may be replayed or duplicated."""
    store = ControlStore(tmp_path)
    _apply({"command": "kill", "reason": "first"}, store)
    _apply({"command": "kill", "reason": "second"}, store)
    assert store.load().mode == KILLED


# --- the verb it must never carry ---------------------------------------------

def test_resume_is_refused_by_name(tmp_path):
    store = ControlStore(tmp_path)
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": "resume", "reason": "assistant: go"}, store)
    assert exc.value.reason_code == "RESUME_NEVER_PERMITTED"


def test_resume_cannot_lift_a_halt(tmp_path):
    """The assertion that actually matters: after a halt, a resume through this door leaves
    the runtime halted. A refusal that still moved the state would be worse than no door."""
    store = ControlStore(tmp_path)
    _apply({"command": "kill", "reason": "halt"}, store)
    with pytest.raises(ControlBlocked):
        _apply({"command": "resume", "reason": "assistant: go"}, store)
    assert store.load().mode == KILLED
    assert store.load().execution_allowed is False


def test_resume_is_absent_from_the_permission_set():
    """Structural, not behavioural: even if the named refusal above were deleted, `resume`
    would still fall through to VERB_NOT_PERMITTED because it is not in `_ALLOWED`."""
    assert control.CMD_RESUME not in halt_bridge._ALLOWED


def test_the_permission_set_is_exactly_the_two_halt_verbs():
    """Drift gate. Widening this door is a decision, so it must break a test rather than
    ride along in a change about something else."""
    assert halt_bridge._ALLOWED == frozenset({control.CMD_KILL, control.CMD_PAUSE})


@pytest.mark.parametrize("verb", ["status", "stop", "audit", "recovery", "approve", "killl"])
def test_every_other_verb_is_refused(tmp_path, verb):
    store = ControlStore(tmp_path)
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": verb, "reason": "x"}, store)
    assert exc.value.reason_code == "VERB_NOT_PERMITTED"
    assert store.load().mode == ACTIVE


# --- attributability ----------------------------------------------------------

def test_the_actor_names_the_assistant_not_the_local_console(tmp_path):
    """An operator reading the ledger must be able to tell a halt that came from SSH from
    one that came from the assistant — that distinction is the reason the actor is its own
    constant rather than a reuse of console_cli's."""
    from runtime.mvp_runtime import console_cli

    store = ControlStore(tmp_path)
    ledger = FakeLedger()
    out = _apply({"command": "kill", "reason": "assistant: stop"}, store, ledger)

    assert out["actor"] == halt_bridge.ASSISTANT_ACTOR
    assert halt_bridge.ASSISTANT_ACTOR != console_cli.LOCAL_ACTOR
    assert store.load().updated_by == halt_bridge.ASSISTANT_ACTOR
    assert ledger.control, "a halt must leave a durable control event"


def test_the_stated_reason_is_recorded(tmp_path):
    store = ControlStore(tmp_path)
    _apply({"command": "kill", "reason": "assistant: funding rate spike"}, store)
    assert "funding rate spike" in json.dumps(store.load().__dict__, default=str, ensure_ascii=False)


# --- fail-closed framing ------------------------------------------------------

@pytest.mark.parametrize("request_obj", [
    "kill", 42, None, [], {}, {"command": ""}, {"command": "   "}, {"reason": "no verb"},
])
def test_malformed_requests_are_refused(tmp_path, request_obj):
    store = ControlStore(tmp_path)
    with pytest.raises(ControlBlocked) as exc:
        _apply(request_obj, store)
    assert exc.value.reason_code == "MALFORMED_REQUEST"
    assert store.load().mode == ACTIVE


@pytest.mark.parametrize("reason", [None, "", "   ", 7])
def test_a_halt_without_a_reason_is_refused(tmp_path, reason):
    store = ControlStore(tmp_path)
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": "kill", "reason": reason}, store)
    assert exc.value.reason_code == "REASON_REQUIRED"
    assert store.load().mode == ACTIVE


# --- policy drift gate --------------------------------------------------------

def test_the_doors_verbs_stay_within_the_policy_grant():
    """Same mechanical gate the console carries: this door adds no verb the Governance
    Policy has not already granted under local_operator_console."""
    import yaml

    from runtime.mvp_runtime.paths import repo_root

    policy = yaml.safe_load(
        (repo_root() / "governance" / "GOVERNANCE_POLICY.yaml").read_text(encoding="utf-8")
    )
    allowed = set(policy["control_channel"]["local_operator_console"]["emergency_controls_allowed"])
    for verb in halt_bridge._ALLOWED:
        assert verb in allowed, (
            f"halt-bridge verb {verb!r} is not granted by the Governance Policy - "
            "either drop the verb or extend emergency_controls_allowed explicitly"
        )


# --- the socket ---------------------------------------------------------------

def _serve(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def _ask(path, payload):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(5)
        client.connect(str(path))
        client.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        return json.loads(client.recv(8192).decode("utf-8").strip())


def test_end_to_end_over_the_socket(tmp_path):
    store = ControlStore(tmp_path)
    sock = tmp_path / "h.sock"
    server = halt_bridge.HaltBridgeServer(sock, control_store=store, ledger=FakeLedger())
    _serve(server)
    try:
        out = _ask(sock, {"command": "kill", "reason": "assistant: stop"})
        assert out["ok"] is True
        assert store.load().mode == KILLED

        refused = _ask(sock, {"command": "resume", "reason": "assistant: go"})
        assert refused["ok"] is False
        assert refused["reason_code"] == "RESUME_NEVER_PERMITTED"
        assert store.load().mode == KILLED
    finally:
        server.shutdown()
        server.server_close()


def test_the_socket_is_not_world_accessible(tmp_path):
    """The shared group is the access control. World-writable would make "any process on
    this host" the authorization, which is wider than the host-console precedent."""
    import stat as stat_mod

    server = halt_bridge.HaltBridgeServer(
        tmp_path / "h.sock", control_store=ControlStore(tmp_path), ledger=FakeLedger(),
    )
    try:
        mode = (tmp_path / "h.sock").stat().st_mode
        assert not mode & stat_mod.S_IROTH
        assert not mode & stat_mod.S_IWOTH
    finally:
        server.server_close()


def test_a_malformed_frame_gets_an_answer_and_the_door_stays_up(tmp_path):
    """A door that dies on a bad frame is a denial-of-service on the halt path."""
    store = ControlStore(tmp_path)
    sock = tmp_path / "h.sock"
    server = halt_bridge.HaltBridgeServer(sock, control_store=store, ledger=FakeLedger())
    _serve(server)
    try:
        bad = _ask(sock, "not-an-object")
        assert bad["ok"] is False
        assert bad["reason_code"] == "MALFORMED_REQUEST"

        good = _ask(sock, {"command": "pause", "reason": "still answering"})
        assert good["ok"] is True
    finally:
        server.shutdown()
        server.server_close()
