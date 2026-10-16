"""The read door opened for a narrower reader — the Claude door (why: ``docs/history/2026-10-16-claude-read-door-profile.md``).

D-11 a1, c1, d2 and f2 were confirmed by Thomas on 2026-10-16 for the repository implementation only.
The single profile and the audit of refused peers follow Thomas's 2026-10-15 instruction (review R-01
alternative a, R-02). None of it grants access: these tests passing is not an approval.

Without a profile the door is the assistant's, pinned unchanged by ``test_mvp_runtime_read_bridge.py``
(X-10). Tests carry the ids of the design's test plan (X-0..X-12). Every secret-looking string below is
a fake marker; nothing reads real state.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import threading
from pathlib import Path

import pytest

from runtime.mvp_runtime import read_bridge, read_bridge_cli, schedule_delegation, scheduler, socket_door, store_reads
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.errors import ControlBlocked, MvpRuntimeError
from runtime.mvp_runtime.scheduler import ScheduleStore
from runtime.mvp_runtime.store import SCHEDULER_FILE, LedgerStore

NOW = "2026-10-13T03:00:00Z"
FAKE = "FAKE-SECRET-7f3a"
FAKE_PATH = "/root/fake-state/holdings/x.json"
SID = "schedule_" + "a" * 20
RID = "srun_" + "b" * 20
CLAUDE_READS = frozenset({"schedules", "scheduler_events"})
HELD = frozenset({"runtime_status", "heartbeat", "crypto_readiness", "holdings_status"})
CLAUDE = read_bridge.PROFILES[read_bridge.CLAUDE_PROFILE]
REPO = Path(__file__).resolve().parents[1]

_REASON = re.compile(r"^[A-Z][A-Z0-9_]{2,63}\Z")
unix_only = pytest.mark.skipif(not socket_door.UNIX_SOCKETS_AVAILABLE, reason="the doors listen on AF_UNIX")


def _event(**over):
    event = {"record_type": "scheduler_event.v0", "action": "failed", "schedule_id": SID,
             "kind": "crypto_factory", "status": "failed:X", "created_at": NOW, "schedule_run_id": RID,
             "duration_ms": 5, "integrity": {"event_sha256": "c" * 64}}
    event.update(over)
    return event


def _events_reply(tmp_path, events, argument=None):
    ledger = LedgerStore(tmp_path)
    for e in events:
        ledger.append_scheduler_event(e)
    request = {"command": "scheduler_events"} | ({"argument": argument} if argument else {})
    return read_bridge.apply_read(request, control_store=ControlStore(tmp_path), ledger=ledger, now=NOW,
                                  repo_root=tmp_path, profile=CLAUDE)


def _blob(reply):
    return json.dumps(reply, ensure_ascii=False)


# --- X-0: the contract's closed lists are the scheduler's ------------------------------------------

def test_x0_the_closed_lists_match_the_scheduler_and_the_delegation():
    run_actions = {"fired", "failed", "skipped", "gap_detected"}
    declared = {getattr(scheduler, n) for n in dir(scheduler) if n.startswith("ACTION_")
                and isinstance(getattr(scheduler, n), str)}
    assert store_reads.EVENT_ACTIONS == declared | run_actions
    assert store_reads.CHANGE_ACTIONS == schedule_delegation.ACTIONS
    assert set(scheduler._MUTATION_STATUS.values()) == {"enabled", "disabled", "removed"}


# --- X-1 .. X-4, X-7 .. X-9b, X-12: the scheduler_events projection --------------------------------

def test_x1_an_exception_message_reaches_neither_the_reply_nor_the_data(tmp_path):
    out = _events_reply(tmp_path, [_event(status=f"failed:FACTORY_CHILD:FileNotFoundError: {FAKE_PATH} token={FAKE}")])
    assert out["data"]["events"][0]["outcome"] == "failed:FACTORY_CHILD"
    blob = _blob(out)
    assert FAKE not in blob and FAKE_PATH not in blob and "FileNotFoundError" not in blob


def test_x2_a_field_outside_the_contract_never_leaves(tmp_path):
    out = _events_reply(tmp_path, [_event(request=f"분석 {FAKE}", reason=FAKE, api_key=FAKE, proposed_by="x",
                                          last_status=f"failed:X:{FAKE}", foo="bar")])
    row = out["data"]["events"][0]
    assert not {"request", "reason", "api_key", "last_status", "proposed_by", "foo", "status",
                "integrity", "record_type"} & set(row)
    assert FAKE not in _blob(out)


def test_x3_an_unknown_kind_or_action_is_unknown_and_partial(tmp_path):
    out = _events_reply(tmp_path, [_event(kind="not_a_kind"), _event(action="teleported")])
    first, second = out["data"]["events"]
    assert (first["kind"], first["outcome"], first["projection"]) == ("UNKNOWN", "UNKNOWN", "v1-partial")
    assert (second["action"], second["outcome"], second["projection"]) == ("UNKNOWN", "UNKNOWN", "v1-partial")
    assert out["data"]["projection"] == "v1-partial"


def test_x4_a_malformed_field_is_dropped_and_marked(tmp_path):
    out = _events_reply(tmp_path, [
        _event(created_at="yesterday", duration_ms="5s", schedule_id="../../etc"),
        _event(action="proposed", status="proposed", change_action="explode",
               out_of_scope=["MIN_INTERVAL", "lower case", FAKE]),
    ])
    first, second = out["data"]["events"]
    assert not {"created_at", "duration_ms", "schedule_id"} & set(first) and first["projection"] == "v1-partial"
    assert second["out_of_scope"] == ["MIN_INTERVAL"] and "change_action" not in second
    assert FAKE not in _blob(out)


def test_x4_a_row_that_is_not_an_object_becomes_an_unknown_row():
    assert store_reads.project_scheduler_event("not an object") == {
        "action": "UNKNOWN", "kind": "UNKNOWN", "outcome": "UNKNOWN", "projection": "v1-partial"}


def test_x7_a_projection_defect_is_refused_whole(tmp_path, monkeypatch):
    real = store_reads.project_scheduler_event
    monkeypatch.setattr(store_reads, "project_scheduler_event",
                        lambda raw: real(raw) | {"status": raw.get("status")})
    with pytest.raises(ControlBlocked) as exc:
        _events_reply(tmp_path, [_event(status=f"failed:X:{FAKE}")])
    assert exc.value.reason_code == "REPLY_SCHEMA_INVALID" and FAKE not in str(exc.value)


def test_x8b_a_removal_event_does_not_carry_last_status_back(tmp_path):
    out = _events_reply(tmp_path, [_event(action="removed", status="removed", request=FAKE, reason=FAKE,
                                          last_run_at=NOW, last_status=f"failed:FACTORY_CHILD:ValueError: {FAKE}")])
    assert out["data"]["events"][0]["outcome"] == "removed" and FAKE not in _blob(out)


def test_x9_a_success_summary_is_reduced_to_fired(tmp_path):
    out = _events_reply(tmp_path, [_event(action="fired", status=f"verdict=HOLD opened=long:{FAKE} settled=tp(1.2R)")])
    assert out["data"]["events"][0]["outcome"] == "fired" and FAKE not in _blob(out)


def test_x9b_every_known_status_shape_normalises_without_loss(tmp_path):
    out = _events_reply(tmp_path, [
        _event(action="started", status="running"),
        _event(action="abandoned", status="abandoned_mid_run", started_at=NOW),
        _event(action="skipped", status="skipped_not_active"),
        _event(action="deferred", status="deferred_pass_budget", pass_elapsed_ms=10, pass_budget_ms=60000),
        _event(action="deferred", status="deferred_factory_child_running"),
        _event(action="gap_detected", status="overdue_seconds=901"),
        _event(action="failed", status="failed:UNEXPECTED:ValueError"),
        _event(action="failed", status="failed:FACTORY_CHILD_DIED:exit=1"),
        _event(action="expired", status="disabled", previously_enabled=True),
    ])
    rows = out["data"]["events"]
    assert [r["outcome"] for r in rows] == [
        "started", "abandoned", "skipped:not_active", "deferred:pass_budget", "deferred:factory_child_running",
        "gap_detected", "failed:UNEXPECTED", "failed:FACTORY_CHILD_DIED", "expired"]
    assert out["data"]["projection"] == "v1" and rows[5]["overdue_seconds"] == 901


def test_x12_the_count_limit_is_the_unprojected_one(tmp_path):
    events = [_event(created_at=f"2026-10-13T03:{i // 60:02d}:{i % 60:02d}Z") for i in range(150)]
    two = _events_reply(tmp_path, events, "2")
    assert two["data"]["count"] == 2 and two["data"]["active_file_total"] == 150
    noted = _events_reply(tmp_path / "b", events, "abc")
    assert noted["data"]["count"] == store_reads.DEFAULT_EVENTS and "기본값" in noted["reply"]
    capped = _events_reply(tmp_path / "c", events, "500")
    assert capped["data"]["count"] == store_reads.MAX_EVENTS


# --- X-5a / X-5b: the refusals stay typed and say nothing of the row --------------------------------

def test_x5a_a_door_without_its_ledger_refuses_typed(tmp_path):
    with pytest.raises(ControlBlocked) as exc:
        read_bridge.apply_read({"command": "scheduler_events"}, control_store=ControlStore(tmp_path),
                               ledger=None, now=NOW, repo_root=tmp_path, profile=CLAUDE)
    assert exc.value.reason_code == "SCHEDULER_LEDGER_UNAVAILABLE"


def test_x5b_a_corrupt_line_is_refused_without_its_content(tmp_path):
    ledger = LedgerStore(tmp_path)
    ledger.append_scheduler_event(_event())
    with (Path(ledger.root) / SCHEDULER_FILE).open("a", encoding="utf-8") as fh:
        fh.write('{"action": "failed", "status": "failed:X:' + FAKE + '", BROKEN\n')
    with pytest.raises(Exception) as exc:
        read_bridge.apply_read({"command": "scheduler_events"}, control_store=ControlStore(tmp_path),
                               ledger=ledger, now=NOW, repo_root=tmp_path, profile=CLAUDE)
    assert FAKE not in str(exc.value)


# --- X-8: the schedules projection -------------------------------------------------------------------

def test_x8_schedules_answer_last_outcome_never_last_status(tmp_path):
    store = ScheduleStore(tmp_path)
    failing = scheduler.build_schedule(kind=scheduler.KIND_FACTORY, request=f"합성 {FAKE}", interval_seconds=86400,
                                       created_by="test", now="2026-10-13T00:00:00Z")
    firing = scheduler.build_schedule(kind=scheduler.KIND_TASK, request="합성", interval_seconds=3600,
                                      created_by="test", now="2026-10-13T00:00:01Z")
    store.add(failing)
    store.add(firing)
    store.record_result(failing.schedule_id, last_run_at=NOW, last_status=f"failed:FACTORY_CHILD:ValueError: {FAKE}")
    store.record_result(firing.schedule_id, last_run_at=NOW, last_status=f"verdict=HOLD opened=long:{FAKE}")
    out = read_bridge.apply_read({"command": "schedules"}, control_store=ControlStore(tmp_path), ledger=None,
                                 now=NOW, repo_root=tmp_path, schedules=store, profile=CLAUDE)
    outcomes = {r["kind"]: r["last_outcome"] for r in out["data"]["enabled"]}
    assert outcomes == {scheduler.KIND_FACTORY: "failed:FACTORY_CHILD", scheduler.KIND_TASK: "fired"}
    assert FAKE not in _blob(out) and "합성" not in _blob(out)
    assert not {"overdue_seconds", "overdue_count"} & (set(out["data"]) | set(out["data"]["enabled"][0]))


def test_r06_a_value_with_a_trailing_newline_is_not_a_well_formed_value():
    sid = SID + "\n"
    row = store_reads.project_scheduler_event({"action": "fired", "kind": scheduler.KIND_TASK, "schedule_id": sid,
                                               "created_at": NOW + "\n", "schedule_run_id": RID + "\n"})
    assert not {"schedule_id", "created_at", "schedule_run_id"} & set(row) and row["projection"] == "v1-partial"
    smuggled = {"action": "fired", "kind": scheduler.KIND_TASK, "outcome": "fired", "projection": "v1", "schedule_id": sid}
    with pytest.raises(ControlBlocked) as exc:
        store_reads._checked({"events": [smuggled], "count": 1, "active_file_total": 1, "as_of": NOW,
                              "projection": "v1"}, store_reads._EVENTS_DATA_SCHEMA)
    assert exc.value.reason_code == "REPLY_SCHEMA_INVALID"
    with pytest.raises(ControlBlocked):
        store_reads._checked({"enabled": [], "disabled_count": 0, "as_of": f"{FAKE}", "projection": "v1"},
                             store_reads._SCHEDULES_DATA_SCHEMA)


# --- X-11: the profile's read subset ---------------------------------------------------------------------

def test_r03_the_four_raw_reads_are_held_out_of_every_profile():
    # f2 names them; each was traced and answers raw. Admitting one needs a reply contract first.
    assert read_bridge._PROFILE_HELD_READS == HELD and HELD <= set(read_bridge._READS)
    assert not HELD & read_bridge._PROFILE_ADMISSIBLE_READS
    assert all(not HELD & p.reads for p in read_bridge.PROFILES.values())


def test_x11_the_claude_profile_serves_its_reads_and_refuses_the_rest(tmp_path):
    (tmp_path / "governance").mkdir()
    shutil.copy(REPO / "governance" / "GOVERNANCE_POLICY.yaml", tmp_path / "governance" / "GOVERNANCE_POLICY.yaml")
    store = ScheduleStore(tmp_path)
    ledger = LedgerStore(tmp_path)
    assert CLAUDE.reads == CLAUDE_READS and CLAUDE.projection == store_reads.PROJECTION_V1
    for verb in sorted(read_bridge._READS) + ["not_a_read"]:
        call = lambda: read_bridge.apply_read(  # noqa: E731
            {"command": verb}, control_store=ControlStore(tmp_path), ledger=ledger, now=NOW, repo_root=tmp_path,
            schedules=store, profile=CLAUDE)
        if verb in CLAUDE_READS:
            assert call()["ok"] is True, verb
        else:
            with pytest.raises(ControlBlocked) as exc:
                call()
            assert exc.value.reason_code == "VERB_NOT_PERMITTED", verb
            # R-05: the refusal names what THIS door serves, never the rest of the table
            assert "tasks" not in str(exc.value).split("carries", 1)[1], verb


# --- R-01: one profile, nothing to leave out ---------------------------------------------------------------

def test_r01_a_look_alike_profile_is_refused_at_the_applier(tmp_path):
    wide = read_bridge.DoorProfile(name=read_bridge.CLAUDE_PROFILE, reads=frozenset(read_bridge._READS),
                                   projection=store_reads.PROJECTION_V1)
    twin = read_bridge.DoorProfile(name=CLAUDE.name, reads=CLAUDE.reads, projection=CLAUDE.projection)
    assert twin == CLAUDE and twin is not CLAUDE      # equal values, not the registered object
    for fake in (twin, wide, "claude", {"name": "claude"}):
        with pytest.raises(ControlBlocked) as exc:
            read_bridge.apply_read({"command": "heartbeat"}, control_store=ControlStore(tmp_path), ledger=None,
                                   now=NOW, repo_root=tmp_path, profile=fake)
        assert exc.value.reason_code == "DOOR_PROFILE_INVALID"


@pytest.mark.parametrize("broken", [
    {"projection": None},                                        # no reply contract: raw last_status would leave
    {"projection": "v9"},
    {"reads": frozenset()},
    {"reads": CLAUDE_READS | {"heartbeat"}},                       # a read that answers raw
    {"reads": CLAUDE_READS | {"tasks"}},
    {"reads": set(CLAUDE_READS)},                                  # mutable: not the structure it claims to be
])
def test_r01_an_incoherent_registered_profile_refuses_to_listen(tmp_path, monkeypatch, broken):
    monkeypatch.setenv(socket_door.CLIENT_UID_ENV, str(os.getuid()))
    bad = read_bridge.DoorProfile(**({"name": "claude", "reads": CLAUDE_READS, "projection": "v1"} | broken))
    monkeypatch.setattr(read_bridge, "PROFILES", {"claude": bad})
    with pytest.raises(ControlBlocked) as exc:
        read_bridge.open_door(tmp_path / "c.sock", control_store=ControlStore(tmp_path), ledger=LedgerStore(tmp_path),
                              profile="claude", request_log=tmp_path / "requests.jsonl")
    assert exc.value.reason_code == "DOOR_PROFILE_INVALID"
    assert not (tmp_path / "c.sock").exists() and not (tmp_path / "requests.jsonl").exists()


@pytest.mark.parametrize("profile, log, uids, code", [
    ("nope", "requests.jsonl", "1", "DOOR_PROFILE_UNKNOWN"),
    ("Claude", "requests.jsonl", "1", "DOOR_PROFILE_UNKNOWN"),      # no case folding into a profile
    ("claude", None, "1", "REQUEST_LOG_REQUIRED"),
    ("claude", "requests.jsonl", None, "READ_UID_ALLOWLIST_REQUIRED"),
    (None, "requests.jsonl", "1", "DOOR_PROFILE_REQUIRED"),          # a log alone does not make a profiled door
])
def test_r01_a_partly_configured_door_refuses_to_listen(tmp_path, monkeypatch, profile, log, uids, code):
    if uids is None:
        monkeypatch.delenv(socket_door.CLIENT_UID_ENV, raising=False)
    else:
        monkeypatch.setenv(socket_door.CLIENT_UID_ENV, uids)
    with pytest.raises(ControlBlocked) as exc:
        read_bridge.open_door(tmp_path / "c.sock", control_store=ControlStore(tmp_path), ledger=LedgerStore(tmp_path),
                              profile=profile, request_log=tmp_path / log if log else None)
    assert exc.value.reason_code == code
    assert not (tmp_path / "c.sock").exists() and not (tmp_path / "requests.jsonl").exists()


def test_r01_the_old_per_protection_flags_no_longer_exist():
    for flag in ("--reads", "--projection", "--require-uid-allowlist"):
        with pytest.raises(SystemExit):
            read_bridge_cli._parse_args([flag, "x"] if flag != "--require-uid-allowlist" else [flag])


@pytest.mark.parametrize("argv, code", [
    (["--request-log", "LOG"], "DOOR_PROFILE_REQUIRED"),
    (["--profile", "claude"], "REQUEST_LOG_REQUIRED"),
    (["--profile", "nope", "--request-log", "LOG"], "DOOR_PROFILE_UNKNOWN"),
])
def test_r01_the_cli_refuses_a_partial_profile(tmp_path, monkeypatch, capsys, argv, code):
    monkeypatch.setenv(socket_door.CLIENT_UID_ENV, str(os.getuid()))
    real = read_bridge.open_door
    monkeypatch.setattr(read_bridge, "open_door", lambda path, **kw: real(
        path, control_store=ControlStore(tmp_path), ledger=LedgerStore(tmp_path),
        profile=kw["profile"], request_log=kw["request_log"]))
    sock = tmp_path / "c.sock"
    argv = [a.replace("LOG", str(tmp_path / "requests.jsonl")) for a in argv]
    assert read_bridge_cli.main(["--socket", str(sock), *argv]) != 0
    assert code in capsys.readouterr().err
    assert not sock.exists() and not (tmp_path / "requests.jsonl").exists()


@unix_only
def test_r01_without_a_profile_the_door_is_the_assistants(tmp_path, monkeypatch):
    monkeypatch.delenv(socket_door.CLIENT_UID_ENV, raising=False)
    door = read_bridge.open_door(tmp_path / "a.sock", control_store=ControlStore(tmp_path), ledger=LedgerStore(tmp_path))
    try:
        assert type(door) is socket_door.SocketDoor and door.RequestHandlerClass is socket_door._Handler
        assert getattr(door, "profile", None) is None
    finally:
        door.server_close()


# --- R-02 and X-5c / X-6: the request log, over a real socket ------------------------------------------

def _ask_raw(path, raw: bytes):
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(5.0)
    try:
        client.connect(str(path))
        client.sendall(raw)
        return json.loads(client.recv(1 << 20).decode("utf-8"))
    finally:
        client.close()


def _ask(path, payload):
    return _ask_raw(path, (json.dumps(payload) + "\n").encode("utf-8"))


def _serve(tmp_path, monkeypatch, log, *, uid=None):
    monkeypatch.setenv(socket_door.CLIENT_UID_ENV, str(os.getuid() if uid is None else uid))
    door = read_bridge.open_door(
        tmp_path / "door" / "c.sock", control_store=ControlStore(tmp_path), ledger=LedgerStore(tmp_path),
        profile=read_bridge.CLAUDE_PROFILE, request_log=log)
    threading.Thread(target=door.serve_forever, daemon=True).start()
    return door


def _rows(log):
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def _answered(rows):
    return [(r["peer_uid"], r["read"], r["outcome"]) for r in rows if r["outcome"] != "DOOR_OPENED"]


@unix_only
def test_x5c_an_unpermitted_peer_is_refused_and_logged_with_its_uid(tmp_path, monkeypatch):
    log = tmp_path / "requests.jsonl"
    door = _serve(tmp_path, monkeypatch, log, uid=os.getuid() + 7)
    try:
        reply = _ask(tmp_path / "door" / "c.sock", {"command": "scheduler_events", "argument": FAKE})
    finally:
        door.shutdown()
        door.server_close()
    assert reply["ok"] is False and reply["reason_code"] == "PEER_NOT_PERMITTED"
    # the kernel's uid; no read name, because nothing a refused peer sent is interpreted
    assert _answered(_rows(log)) == [(os.getuid(), None, "PEER_NOT_PERMITTED")]
    assert FAKE not in log.read_text(encoding="utf-8")


@unix_only
def test_r02_a_peer_whose_credentials_cannot_be_read_is_logged_without_a_guessed_uid(tmp_path, monkeypatch):
    log = tmp_path / "requests.jsonl"
    door = _serve(tmp_path, monkeypatch, log)
    monkeypatch.setattr(socket_door, "peer_credentials", lambda sock: None)
    try:
        reply = _ask(tmp_path / "door" / "c.sock", {"command": "heartbeat"})
    finally:
        door.shutdown()
        door.server_close()
    assert reply["reason_code"] == "PEER_CREDENTIALS_UNAVAILABLE"
    assert _answered(_rows(log)) == [(None, None, "PEER_CREDENTIALS_UNAVAILABLE")]


@unix_only
def test_every_answer_the_transport_sends_is_logged(tmp_path, monkeypatch):
    # One worker, so every request below runs on the SAME pool thread: a read name left over from
    # one request would show up on the next row (the thread-local must not carry across).
    monkeypatch.setattr(read_bridge, "MAX_CONCURRENT_REQUESTS", 1)
    LedgerStore(tmp_path).append_scheduler_event(_event(status=f"failed:FACTORY_CHILD:{FAKE}"))
    log = tmp_path / "requests.jsonl"
    door = _serve(tmp_path, monkeypatch, log)
    path = tmp_path / "door" / "c.sock"
    try:
        ok = _ask(path, {"command": "scheduler_events", "argument": "3"})
        garbled = _ask_raw(path, b"{not json " + FAKE.encode() + b"\n")
        outside = _ask(path, {"command": "tasks"})
        unknown = _ask(path, {"command": f"x {FAKE} " + "y" * 100})
        no_command = _ask(path, {"argument": FAKE})
    finally:
        door.shutdown()
        door.server_close()
    assert ok["ok"] is True and FAKE not in _blob(ok)
    assert garbled["reason_code"] == "MALFORMED_REQUEST" and outside["reason_code"] == "VERB_NOT_PERMITTED"
    me = os.getuid()
    assert _answered(_rows(log)) == [
        (me, "scheduler_events", "ok"),
        (me, None, "MALFORMED_REQUEST"),                # a frame that would not decode: no read to name
        (me, "tasks", "VERB_NOT_PERMITTED"),             # a real read name, outside the profile
        (me, "<not_a_read>", "VERB_NOT_PERMITTED"),      # R-04: free text never lands in the log
        (me, None, "MALFORMED_REQUEST"),
    ]
    assert unknown["reason_code"] == "VERB_NOT_PERMITTED" and no_command["reason_code"] == "MALFORMED_REQUEST"
    text = log.read_text(encoding="utf-8")
    assert FAKE not in text and FAKE.lower() not in text
    assert all(set(r) == {"at", "peer_uid", "read", "outcome"} for r in _rows(log))
    assert _rows(log)[0]["outcome"] == "DOOR_OPENED" and _rows(log)[0]["peer_uid"] is None


@unix_only
def test_r02_a_busy_refusal_is_logged_with_the_kernels_uid(tmp_path, monkeypatch):
    log = tmp_path / "requests.jsonl"
    door = _serve(tmp_path, monkeypatch, log)
    ours, theirs = socket.socketpair()
    try:
        door._refuse_busy(ours)
        reply = json.loads(theirs.recv(1 << 16).decode("utf-8"))
    finally:
        ours.close()
        theirs.close()
        door.shutdown()
        door.server_close()
    assert reply["reason_code"] == "BRIDGE_BUSY"
    assert _answered(_rows(log)) == [(os.getuid(), None, "BRIDGE_BUSY")]


@unix_only
def test_x6_a_door_whose_log_cannot_be_written_does_not_listen(tmp_path, monkeypatch):
    log_dir_in_the_way = tmp_path / "requests.jsonl"
    log_dir_in_the_way.mkdir()                     # the log path is a directory: every append fails
    monkeypatch.setenv(socket_door.CLIENT_UID_ENV, str(os.getuid()))
    with pytest.raises(ControlBlocked) as exc:
        read_bridge.open_door(tmp_path / "c.sock", control_store=ControlStore(tmp_path), ledger=LedgerStore(tmp_path),
                              profile=read_bridge.CLAUDE_PROFILE, request_log=log_dir_in_the_way)
    assert exc.value.reason_code == "REQUEST_LOG_UNAVAILABLE"
    assert str(tmp_path) not in str(exc.value)
    assert not (tmp_path / "c.sock").exists(), "a door that refused after its bind leaves no socket behind"


@unix_only
def test_x6_a_request_that_cannot_be_logged_is_answered_with_nothing(tmp_path, monkeypatch):
    LedgerStore(tmp_path).append_scheduler_event(_event(status=f"failed:FACTORY_CHILD:{FAKE}"))
    log = tmp_path / "requests.jsonl"
    door = _serve(tmp_path, monkeypatch, log)
    log.unlink()
    log.mkdir()                                    # the log breaks after the door opened
    try:
        reply = _ask(tmp_path / "door" / "c.sock", {"command": "scheduler_events"})
    finally:
        door.shutdown()
        door.server_close()
    assert reply == {"ok": False, "reason_code": "REQUEST_LOG_UNAVAILABLE", "reason": reply["reason"]}
    assert "data" not in reply and FAKE not in _blob(reply) and str(tmp_path) not in reply["reason"]


@unix_only
def test_a_permitted_peer_is_answered_projected_and_logged_with_its_uid(tmp_path, monkeypatch):
    LedgerStore(tmp_path).append_scheduler_event(_event(status=f"failed:FACTORY_CHILD:{FAKE}"))
    store = ScheduleStore(tmp_path)
    failing = scheduler.build_schedule(kind=scheduler.KIND_FACTORY, request="합성", interval_seconds=86400,
                                       created_by="test", now="2026-10-13T00:00:00Z")
    store.add(failing)
    store.record_result(failing.schedule_id, last_run_at=NOW, last_status=f"failed:FACTORY_CHILD:ValueError: {FAKE}")
    log = tmp_path / "requests.jsonl"
    monkeypatch.setenv(socket_door.CLIENT_UID_ENV, str(os.getuid()))
    door = read_bridge.open_door(
        tmp_path / "door" / "c.sock", control_store=ControlStore(tmp_path), ledger=LedgerStore(tmp_path),
        schedules=store, profile=read_bridge.CLAUDE_PROFILE, request_log=log)
    threading.Thread(target=door.serve_forever, daemon=True).start()
    try:
        events = _ask(tmp_path / "door" / "c.sock", {"command": "scheduler_events"})
        schedules = _ask(tmp_path / "door" / "c.sock", {"command": "schedules"})
    finally:
        door.shutdown()
        door.server_close()
    assert events["ok"] is True and events["data"]["events"][0]["outcome"] == "failed:FACTORY_CHILD"
    assert schedules["ok"] is True and schedules["data"]["enabled"][0]["last_outcome"] == "failed:FACTORY_CHILD"
    assert FAKE not in _blob(events) and FAKE not in _blob(schedules)
    assert _answered(_rows(log)) == [(os.getuid(), "scheduler_events", "ok"), (os.getuid(), "schedules", "ok")]


# --- R-07: the request log is opened without following a link ----------------------------------------
# Every path below is under tmp_path. "Refused" means REQUEST_LOG_UNAVAILABLE and nothing written
# anywhere: the log is either provably the log, or the answer is withheld.

posix_log = pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW") or read_bridge.fcntl is None,
                               reason="the request log needs O_NOFOLLOW and fcntl (POSIX)")


def _write_row(log):
    read_bridge._log_request(log, peer_uid=7, read="schedules", outcome="ok", now=NOW)


def _refused(log):
    with pytest.raises(ControlBlocked) as exc:
        _write_row(log)
    assert exc.value.reason_code == "REQUEST_LOG_UNAVAILABLE"
    assert str(log) not in str(exc.value) and str(log.parent) not in str(exc.value)


@posix_log
def test_r07_a_new_log_is_created_private_and_appended_without_a_sidecar(tmp_path):
    log = tmp_path / "requests.jsonl"
    neighbour = tmp_path / "other.txt"
    neighbour.write_text("untouched\n", encoding="utf-8")
    _write_row(log)
    _write_row(log)
    assert [json.loads(line)["peer_uid"] for line in log.read_text(encoding="utf-8").splitlines()] == [7, 7]
    assert not (os.stat(log).st_mode & 0o022)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["other.txt", "requests.jsonl"]   # no .lock sidecar
    assert neighbour.read_text(encoding="utf-8") == "untouched\n"


@posix_log
def test_r07_a_symlink_at_the_log_path_is_refused_and_its_target_is_untouched(tmp_path):
    target = tmp_path / "elsewhere.txt"
    target.write_bytes(b"precious\n")
    log = tmp_path / "requests.jsonl"
    log.symlink_to(target)
    _refused(log)
    assert target.read_bytes() == b"precious\n" and log.is_symlink()


@posix_log
def test_r07_a_dangling_symlink_is_refused_and_creates_nothing_through_it(tmp_path):
    log = tmp_path / "requests.jsonl"
    log.symlink_to(tmp_path / "would_be_created.txt")
    _refused(log)
    assert not (tmp_path / "would_be_created.txt").exists()


@posix_log
@pytest.mark.parametrize("plant", ["hardlink", "group_writable", "other_writable", "directory", "fifo",
                                   "fifo_with_reader"])
def test_r07_a_log_path_that_is_not_a_private_regular_file_is_refused(tmp_path, plant):
    log = tmp_path / "requests.jsonl"
    original = tmp_path / "original.txt"
    if plant == "hardlink":
        original.write_bytes(b"precious\n")
        os.link(original, log)
    elif plant in ("group_writable", "other_writable"):
        log.write_bytes(b"precious\n")
        os.chmod(log, 0o660 if plant == "group_writable" else 0o646)
    elif plant == "directory":
        log.mkdir()
    else:
        os.mkfifo(log)                         # O_NONBLOCK: refused, not a hung handler thread
    reader = os.open(log, os.O_RDONLY | os.O_NONBLOCK) if plant == "fifo_with_reader" else None
    try:
        _refused(log)                          # with a reader the open succeeds: the descriptor check refuses it
    finally:
        if reader is not None:
            os.close(reader)
    if plant == "hardlink":
        assert original.read_bytes() == b"precious\n"
    elif plant in ("group_writable", "other_writable"):
        assert log.read_bytes() == b"precious\n"


@posix_log
def test_r07_a_log_owned_by_another_user_is_refused(tmp_path, monkeypatch):
    log = tmp_path / "requests.jsonl"
    log.write_bytes(b"")
    os.chmod(log, 0o640)
    monkeypatch.setattr(read_bridge.os, "geteuid", lambda: os.stat(log).st_uid + 1)
    _refused(log)
    assert log.read_bytes() == b""


@posix_log
def test_r07_a_missing_log_directory_is_refused_and_not_created(tmp_path):
    log = tmp_path / "no_such_dir" / "requests.jsonl"
    _refused(log)
    assert not (tmp_path / "no_such_dir").exists()


def test_r07_a_platform_without_nofollow_or_locking_refuses(tmp_path, monkeypatch):
    log = tmp_path / "requests.jsonl"
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    _refused(log)
    monkeypatch.undo()
    monkeypatch.setattr(read_bridge, "fcntl", None)
    _refused(log)
    assert not log.exists()


@unix_only
@posix_log
def test_r07_a_door_whose_log_path_is_a_symlink_does_not_listen(tmp_path, monkeypatch):
    target = tmp_path / "elsewhere.txt"
    target.write_bytes(b"precious\n")
    (tmp_path / "requests.jsonl").symlink_to(target)
    monkeypatch.setenv(socket_door.CLIENT_UID_ENV, str(os.getuid()))
    with pytest.raises(ControlBlocked) as exc:
        read_bridge.open_door(tmp_path / "c.sock", control_store=ControlStore(tmp_path), ledger=LedgerStore(tmp_path),
                              profile=read_bridge.CLAUDE_PROFILE, request_log=tmp_path / "requests.jsonl")
    assert exc.value.reason_code == "REQUEST_LOG_UNAVAILABLE"
    assert target.read_bytes() == b"precious\n" and not (tmp_path / "c.sock").exists()


# --- R3-5: a profiled door's refusals are rebuilt from their code -------------------------------------------
# Whatever an exception says — a path, a secret-looking value, several lines, a parser's message — a
# profiled door answers with a code from PROFILE_ERROR_TEXT and that code's fixed sentence.

MULTILINE = f"could not read {FAKE_PATH}: {FAKE}\nTraceback (most recent call last):\n  File \"/app/x.py\""


def _fixed(reply, code):
    assert reply["ok"] is False and reply["reason_code"] == code
    assert reply["reason"] == read_bridge.PROFILE_ERROR_TEXT[code]
    assert set(reply) <= {"ok", "reason_code", "reason", "proto", "client_id", "request_id"}
    assert FAKE not in _blob(reply) and FAKE_PATH not in _blob(reply) and "Traceback" not in _blob(reply)


def _served_once(tmp_path, monkeypatch, payload=None, raw=None, uid=None):
    log = tmp_path / "requests.jsonl"
    door = _serve(tmp_path, monkeypatch, log, uid=uid)
    try:
        path = tmp_path / "door" / "c.sock"
        reply = _ask_raw(path, raw) if raw is not None else _ask(path, payload)
    finally:
        door.shutdown()
        door.server_close()
    text = log.read_text(encoding="utf-8")
    assert FAKE not in text and FAKE_PATH not in text and "Traceback" not in text
    return reply, _answered(_rows(log))


@unix_only
def test_r35_a_typed_refusal_carrying_a_path_a_secret_and_data_answers_its_code_only(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        exc = ControlBlocked("LEDGER_UNREADABLE", MULTILINE)
        exc.data = {"path": FAKE_PATH, "token": FAKE}
        raise exc
    monkeypatch.setattr(store_reads, "read_scheduler_events", boom)
    reply, rows = _served_once(tmp_path, monkeypatch, {"command": "scheduler_events", "request_id": "r-1"})
    _fixed(reply, "READ_UNAVAILABLE")
    assert reply["request_id"] == "r-1"                      # the peer's own well-formed token is echoed
    assert rows == [(os.getuid(), "scheduler_events", "LEDGER_UNREADABLE")]   # the log keeps the real code


@unix_only
def test_r35_a_real_ledger_read_error_with_an_absolute_path_does_not_reach_the_peer(tmp_path, monkeypatch):
    ledger_file = Path(LedgerStore(tmp_path).root) / SCHEDULER_FILE
    ledger_file.parent.mkdir(parents=True, exist_ok=True)
    ledger_file.mkdir()                                      # reading it raises IsADirectoryError(path)
    with pytest.raises(MvpRuntimeError) as raw:
        LedgerStore(tmp_path).read_scheduler_events_tail(3)
    assert str(tmp_path) in str(raw.value), "the transport's text WOULD have carried the absolute path"
    reply, rows = _served_once(tmp_path, monkeypatch, {"command": "scheduler_events"})
    _fixed(reply, "READ_UNAVAILABLE")
    assert str(tmp_path) not in _blob(reply) and rows[0][2] == raw.value.reason_code


@unix_only
def test_r35_an_unexpected_exception_is_bridge_error_without_message_or_pid(tmp_path, monkeypatch):
    monkeypatch.setattr(store_reads, "read_schedules", lambda *a, **k: (_ for _ in ()).throw(RuntimeError(MULTILINE)))
    reply, rows = _served_once(tmp_path, monkeypatch, {"command": "schedules"})
    _fixed(reply, "BRIDGE_ERROR")
    assert "error_ref" not in reply and str(os.getpid()) not in _blob(reply)
    assert rows == [(os.getuid(), "schedules", "BRIDGE_ERROR")]


@unix_only
def test_r35_a_projection_that_fails_or_breaks_its_schema_answers_its_code(tmp_path, monkeypatch):
    LedgerStore(tmp_path).append_scheduler_event(_event(status=f"failed:X:{FAKE}"))
    real = store_reads.project_scheduler_event
    monkeypatch.setattr(store_reads, "project_scheduler_event", lambda raw: (_ for _ in ()).throw(ValueError(MULTILINE)))
    reply, _ = _served_once(tmp_path, monkeypatch, {"command": "scheduler_events"})
    _fixed(reply, "BRIDGE_ERROR")
    monkeypatch.setattr(store_reads, "project_scheduler_event", lambda raw: real(raw) | {"status": raw.get("status")})
    LedgerStore(tmp_path / "b").append_scheduler_event(_event(status=f"failed:X:{FAKE}"))
    reply, rows = _served_once(tmp_path / "b", monkeypatch, {"command": "scheduler_events"})
    _fixed(reply, "REPLY_SCHEMA_INVALID")


@unix_only
@pytest.mark.parametrize("raw, code", [
    (b"{not json " + FAKE.encode() + b"\n", "MALFORMED_REQUEST"),
    (json.dumps({"command": "scheduler_events", "proto": FAKE}).encode() + b"\n", "PROTO_UNSUPPORTED"),
    (json.dumps({"command": f"x {FAKE}"}).encode() + b"\n", "VERB_NOT_PERMITTED"),
    (json.dumps({"command": "tasks"}).encode() + b"\n", "VERB_NOT_PERMITTED"),
    (json.dumps({"command": "schedules", "argument": FAKE}).encode() + b"\n", "ARGUMENT_NOT_ACCEPTED"),
    (json.dumps({"command": "tasks", "request_id": f"has space {FAKE}"}).encode() + b"\n", "VERB_NOT_PERMITTED"),
])
def test_r35_bad_requests_answer_fixed_text_and_echo_nothing_malformed(tmp_path, monkeypatch, raw, code):
    reply, _ = _served_once(tmp_path, monkeypatch, raw=raw)
    _fixed(reply, code)
    assert "tasks" not in reply["reason"] and "schedules" not in reply["reason"]
    assert "request_id" not in reply and "proto" not in reply      # a malformed token the peer sent is not echoed


@unix_only
def test_r35_a_refused_peer_is_not_told_its_uid(tmp_path, monkeypatch):
    reply, rows = _served_once(tmp_path, monkeypatch, {"command": "schedules"}, uid=os.getuid() + 7)
    _fixed(reply, "PEER_NOT_PERMITTED")
    assert str(os.getuid()) not in reply["reason"] and rows == [(os.getuid(), None, "PEER_NOT_PERMITTED")]


@unix_only
def test_r35_busy_and_log_failure_answer_their_fixed_text(tmp_path, monkeypatch):
    log = tmp_path / "requests.jsonl"
    door = _serve(tmp_path, monkeypatch, log)
    ours, theirs = socket.socketpair()
    try:
        door._refuse_busy(ours)
        busy = json.loads(theirs.recv(1 << 16).decode("utf-8"))
        log.unlink()
        log.mkdir()
        unlogged = _ask(tmp_path / "door" / "c.sock", {"command": "schedules", "request_id": "r-2"})
    finally:
        ours.close()
        theirs.close()
        door.shutdown()
        door.server_close()
    _fixed(busy, "BRIDGE_BUSY")
    _fixed(unlogged, "REQUEST_LOG_UNAVAILABLE")
    assert unlogged["request_id"] == "r-2"


@unix_only
def test_r35_a_refusal_that_cannot_be_rebuilt_is_answered_by_the_constant(tmp_path, monkeypatch):
    monkeypatch.setattr(read_bridge, "profile_error_payload", lambda *a, **k: (_ for _ in ()).throw(KeyError(FAKE)))
    reply, _ = _served_once(tmp_path, monkeypatch, {"command": "tasks"})
    assert reply == {"ok": False, "reason_code": "BRIDGE_ERROR", "reason": read_bridge.PROFILE_ERROR_TEXT["BRIDGE_ERROR"]}


def test_r35_every_code_outside_the_set_becomes_read_unavailable():
    for code in ("LEDGER_UNREADABLE", "DOOR_PROFILE_INVALID", FAKE, None, 7, "verb_not_permitted"):
        out = read_bridge.profile_error_payload(code)
        assert out == {"ok": False, "reason_code": "READ_UNAVAILABLE",
                       "reason": read_bridge.PROFILE_ERROR_TEXT["READ_UNAVAILABLE"]}
    assert all(_REASON.match(c) and "{" not in t for c, t in read_bridge.PROFILE_ERROR_TEXT.items())


@unix_only
def test_r35_the_assistants_door_still_answers_the_transports_own_text(tmp_path, monkeypatch):
    monkeypatch.delenv(socket_door.CLIENT_UID_ENV, raising=False)
    monkeypatch.setattr(store_reads, "read_schedules",
                        lambda *a, **k: (_ for _ in ()).throw(ControlBlocked("SCHEDULES_UNAVAILABLE", "authored text")))
    door = read_bridge.open_door(tmp_path / "a.sock", control_store=ControlStore(tmp_path), ledger=LedgerStore(tmp_path),
                                 schedules=ScheduleStore(tmp_path))
    threading.Thread(target=door.serve_forever, daemon=True).start()
    try:
        reply = _ask(tmp_path / "a.sock", {"command": "schedules"})
    finally:
        door.shutdown()
        door.server_close()
    # unchanged: the transport's `str(exc)`, which for a typed refusal is "<code>: <text>"
    assert reply == {"ok": False, "reason_code": "SCHEDULES_UNAVAILABLE", "reason": "SCHEDULES_UNAVAILABLE: authored text"}


@posix_log
@pytest.mark.parametrize("failure", ["short", "error"])
def test_r07_a_torn_write_is_cut_back_and_refused(tmp_path, monkeypatch, failure):
    log = tmp_path / "requests.jsonl"
    _write_row(log)
    before = log.read_bytes()
    real_write = os.write

    def torn(fd, data):
        if isinstance(data, (bytes, bytearray)) and data.startswith(b'{"at"'):
            if failure == "error":
                real_write(fd, data[: len(data) // 2])
                raise OSError("disk full")
            return real_write(fd, data[: len(data) // 2])
        return real_write(fd, data)
    monkeypatch.setattr(read_bridge.os, "write", torn)
    _refused(log)
    monkeypatch.undo()
    assert log.read_bytes() == before                    # no torn bytes left behind
    _write_row(log)
    assert [json.loads(line)["read"] for line in log.read_text(encoding="utf-8").splitlines()] == ["schedules"] * 2
