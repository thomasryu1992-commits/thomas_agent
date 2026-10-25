"""Claude's read door as deployed: the ``claude-read-bridge`` compose service and ``scripts/claude_read.py``.

The door's behaviour is pinned in ``test_mvp_runtime_read_bridge_projection.py``. This file pins the decision
about how it runs (Option C C6b prerequisite 1, RD-1..RD-4, Thomas 2026-10-19). Each property below is one
the design names as what keeps the door Claude's alone:
- the literal uid and gid;
- the socket outside ``bridge/``;
- the read-only state root;
- the request log.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest
import yaml

from runtime.mvp_runtime import read_bridge, socket_door
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.store import LedgerStore
from scripts import claude_read

COMPOSE_PATH = Path(__file__).resolve().parents[1] / "docker-compose.yml"
SERVICE = "claude-read-bridge"
CLAUDE_UID = "2101"
DOOR_DIR = "${THOMAS_CLAUDE_DOOR_DIR:-/var/lib/thomas-claude-door}"
unix_only = pytest.mark.skipif(not socket_door.UNIX_SOCKETS_AVAILABLE, reason="the doors listen on AF_UNIX")


def _services() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))["services"]


def _service() -> dict:
    return _services()[SERVICE]


# --- the compose service ---------------------------------------------------------------------------------

def test_the_door_runs_the_claude_profile_with_its_socket_and_request_log():
    command = _service()["command"]
    assert command[:3] == ["python", "-m", "runtime.mvp_runtime.read_bridge_cli"]
    flags = dict(zip(command[3::2], command[4::2]))
    assert flags == {"--profile": read_bridge.CLAUDE_PROFILE, "--socket": "/claude-door/read.sock",
                     "--request-log": "/claude-door-log/requests.jsonl"}
    assert _service()["healthcheck"]["test"] == ["CMD", "test", "-S", flags["--socket"]]


def test_uid_and_gid_are_literals_naming_the_claude_account_only():
    """Never the ${MVP_BRIDGE_CLIENT_*} the other doors share: a value set for them must not reach this one."""
    service = _service()
    assert service["environment"] == {"MVP_BRIDGE_CLIENT_GID": CLAUDE_UID, "MVP_BRIDGE_CLIENT_UID": CLAUDE_UID}
    assert service["group_add"] == [CLAUDE_UID]
    raw = COMPOSE_PATH.read_text(encoding="utf-8")
    block = raw[raw.index(f"  {SERVICE}:\n"):]
    block = block[:block.index("\n\n")]
    assert "${MVP_BRIDGE_CLIENT" not in block


def test_the_claude_uid_reaches_no_other_door():
    for name, spec in _services().items():
        if name == SERVICE:
            continue
        env = spec.get("environment") or {}
        for key in ("MVP_BRIDGE_CLIENT_UID", "MVP_BRIDGE_CLIENT_GID"):
            assert CLAUDE_UID not in str(env.get(key, "")).split(","), (name, key)
        assert CLAUDE_UID not in [str(g) for g in spec.get("group_add") or []], name


def test_state_is_read_only_and_the_socket_and_log_live_outside_bridge():
    volumes = _service()["volumes"]
    assert volumes == ["${THOMAS_STATE_DIR:-./.runtime_governance_state}:/app/.runtime_governance_state:ro",
                       f"{DOOR_DIR}/sock:/claude-door",
                       f"{DOOR_DIR}/log:/claude-door-log"]
    assert "bridge" not in DOOR_DIR and "runtime_governance_state" not in DOOR_DIR


def test_no_other_service_mounts_the_claude_socket_or_its_log():
    """Written for the assistant's container (retired 2026-10-25); it holds for every other service."""
    for name, spec in _services().items():
        if name == "claude-read-bridge":
            continue
        mounts = " ".join(str(v) for v in spec.get("volumes") or [])
        assert "thomas-claude-door" not in mounts and "/claude-door" not in mounts, name


def test_the_door_carries_no_secret_and_waits_on_nothing():
    service = _service()
    assert "env_file" not in service and "secrets" not in service and "depends_on" not in service


# --- the client --------------------------------------------------------------------------------------------

def test_the_client_sends_exactly_the_profiles_reads():
    assert set(claude_read.READS) == set(read_bridge.PROFILES[read_bridge.CLAUDE_PROFILE].reads)


def test_the_client_targets_the_composed_socket():
    default = DOOR_DIR.split(":-")[1].rstrip("}")
    assert claude_read.SOCKET == Path(default) / "sock" / Path(
        _service()["command"][_service()["command"].index("--socket") + 1]).name


@pytest.mark.parametrize("read", ["holdings_status", "crypto_readiness", "SCHEDULES", "nope"])
def test_a_read_outside_the_profile_never_leaves_the_client(read, monkeypatch, capsys):
    monkeypatch.setattr(claude_read.socket_door, "call_door", lambda *a, **k: pytest.fail("reached the door"))
    assert claude_read.main([read]) == claude_read.EXIT_USAGE


def test_no_door_answering_is_a_typed_refusal(tmp_path, capsys):
    assert claude_read.main(["schedules"], socket_path=tmp_path / "absent.sock") == claude_read.EXIT_BLOCKED
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["reason_code"] in {"DOOR_UNREACHABLE", "UNIX_SOCKETS_UNAVAILABLE"}


def _serve(tmp_path, monkeypatch, *, uid):
    monkeypatch.setenv(socket_door.CLIENT_UID_ENV, str(uid))
    door = read_bridge.open_door(
        tmp_path / "door" / "read.sock", control_store=ControlStore(tmp_path), ledger=LedgerStore(tmp_path),
        profile=read_bridge.CLAUDE_PROFILE, request_log=tmp_path / "requests.jsonl")
    threading.Thread(target=door.serve_forever, daemon=True).start()
    return door


@unix_only
def test_a_reply_is_printed_as_the_door_sent_it(tmp_path, monkeypatch, capsys):
    door = _serve(tmp_path, monkeypatch, uid=os.getuid())
    try:
        code = claude_read.main(["scheduler_events"], socket_path=tmp_path / "door" / "read.sock")
    finally:
        door.shutdown()
        door.server_close()
    reply = json.loads(capsys.readouterr().out)
    assert code == claude_read.EXIT_OK and reply["ok"] is True
    rows = [json.loads(line) for line in (tmp_path / "requests.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(r["peer_uid"], r["read"], r["outcome"]) for r in rows if r["outcome"] != "DOOR_OPENED"] == [
        (os.getuid(), "scheduler_events", "ok")]


@unix_only
def test_a_peer_the_door_does_not_admit_gets_its_refusal_printed(tmp_path, monkeypatch, capsys):
    door = _serve(tmp_path, monkeypatch, uid=os.getuid() + 7)
    try:
        code = claude_read.main(["schedules"], socket_path=tmp_path / "door" / "read.sock")
    finally:
        door.shutdown()
        door.server_close()
    reply = json.loads(capsys.readouterr().out)
    assert code == claude_read.EXIT_BLOCKED and reply["reason_code"] == "PEER_NOT_PERMITTED"
