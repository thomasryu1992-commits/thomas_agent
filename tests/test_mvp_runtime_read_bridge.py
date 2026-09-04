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

from runtime.mvp_runtime import control, domain_console, read_bridge, socket_door
from runtime.mvp_runtime.control import ACTIVE, KILLED, ControlStore
from runtime.mvp_runtime.errors import ControlBlocked, MvpRuntimeError, OperatorBlocked

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


def test_every_bridge_domain_read_resolves_to_a_live_handler():
    """Static, and deliberately so — the test above cannot catch this.

    `_READS` names domain verbs as a `(VERB, subcommand)` tuple handed straight to
    `domain_console.apply_domain_command`, **bypassing `parse_domain_command`**. So a verb the
    domain console no longer has is not a syntax error and not a crash; it is a typed
    `OperatorBlocked`, which the dispatch test above swallows by design ("a typed refusal is a
    correct outcome for an unwired store"). That is how `pred_report` survived #434 deleting
    the PRED lane: the assistant kept a verb whose only possible answer was a refusal naming
    `/pred`, an operator command that had been deleted in the same PR.

    Checking the tables against each other needs no store, so nothing here can be absorbed.
    """
    for command, (family, spec) in read_bridge._READS.items():
        if family != "domain":
            continue
        verb, subcommand = spec
        assert verb.lower() in domain_console.COMMANDS, (
            f"{command!r} points at domain verb {verb!r}, which the domain console no longer has"
        )
        known = domain_console._SUBCOMMANDS[verb.lower()]
        assert subcommand in known, (
            f"{command!r} asks for {verb}/{subcommand!r}; the verb has {sorted(known)}"
        )


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


# --- the funds verb ----------------------------------------------------------
#
# The third of Thomas's original three (position, funds, return). It is the one that could not
# be built the obvious way: the balance is behind a signed venue call and this door holds no
# venue credential. It reads a snapshot the scheduler wrote instead, and the two tests below
# are what keep it that way — the second one is the design, expressed as a test.


def test_the_funds_verb_is_a_read_that_takes_no_argument():
    assert "crypto_funds" in read_bridge._READS
    assert read_bridge._READS["crypto_funds"] == (read_bridge._DOMAIN, ("CRYPTO", "funds"))
    assert "crypto_funds" not in read_bridge._TAKES_ARGUMENT


def test_the_funds_read_never_opens_a_socket_to_the_venue(monkeypatch, tmp_path):
    """Adding a venue credential to this door would put an order-capable key in the process the
    assistant talks to — there is one Binance key on this host and order authority derives from
    it. So the door must be structurally unable to ask, not merely disinclined to."""
    def _explode(*a, **k):
        raise AssertionError("the read door reached the venue")

    monkeypatch.setattr("runtime.mvp_runtime.crypto.account.read_account", _explode)
    monkeypatch.setattr("runtime.mvp_runtime.crypto.account.select_account_feed", _explode)

    # No snapshot in a fresh tmp root, so it refuses — and refusing is the point: the patch
    # above proves the refusal did not come from a failed network call. The door surfaces a
    # console refusal as OperatorBlocked, exactly as it does for every other domain read.
    with pytest.raises(OperatorBlocked) as exc:
        read_bridge.apply_read(
            {"command": "crypto_funds"},
            control_store=ControlStore(tmp_path), ledger=None, repo_root=tmp_path,
        )
    assert exc.value.reason_code == "ACCOUNT_SNAPSHOT_MISSING"


# --- the frame envelope (door API v2) ------------------------------------------

def test_a_v1_frame_gets_the_v1_reply_plus_data(tmp_path):
    out = _apply({"command": "runtime_status"}, ControlStore(tmp_path), repo_root=tmp_path)
    assert "proto" not in out and "client_id" not in out
    assert isinstance(out["reply"], str)
    assert isinstance(out["data"], dict) and "reply" not in out["data"]
    assert out["data"]["action"] == out["action"]


def test_a_v2_frame_is_echoed_and_its_data_is_the_consoles_structured_view(tmp_path):
    out = _apply(
        {"command": "runtime_status", "proto": 2, "client_id": "hermes:dm"},
        ControlStore(tmp_path), repo_root=tmp_path,
    )
    assert out["proto"] == 2 and out["client_id"] == "hermes:dm"
    assert out["data"]["mode"] == ACTIVE


def test_an_unsupported_proto_is_refused_before_any_verb_is_read(tmp_path):
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": "no_such_verb", "proto": 3}, ControlStore(tmp_path), repo_root=tmp_path)
    assert exc.value.reason_code == "PROTO_UNSUPPORTED"


def test_a_malformed_client_id_is_refused_even_though_this_door_ignores_unknown_keys(tmp_path):
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": "runtime_status", "client_id": "not a name"}, ControlStore(tmp_path), repo_root=tmp_path)
    assert exc.value.reason_code == "MALFORMED_REQUEST"


# --- the fourth family: stores the console never rendered (door API v2) ---------------

from runtime.mvp_runtime import heartbeat, scheduler, store_reads
from runtime.mvp_runtime.approval_store import ApprovalStore
from runtime.mvp_runtime.scheduler import ScheduleStore
from runtime.mvp_runtime.store import LedgerStore


def test_every_read_names_the_clause_that_admits_it_and_nothing_else_is_named():
    """The operator channel's rule, applied here: a verb cannot join `_READS` without an
    authority string, and an authority string cannot outlive its verb."""
    assert set(read_bridge.READ_VERB_AUTHORITY) == set(read_bridge._READS)
    for verb, authority in read_bridge.READ_VERB_AUTHORITY.items():
        assert authority.startswith("policy:"), verb


def test_the_store_family_names_only_reads():
    verbs = {v for v, (family, _spec) in read_bridge._READS.items() if family == read_bridge._STORES}
    assert verbs == {"schedules", "scheduler_events", "heartbeat", "approval_status"}
    for verb in verbs:
        assert not any(word in verb for word in ("enable", "disable", "remove", "add", "approve", "reject"))


def test_schedules_lists_rows_with_their_lane_and_overdue_state(tmp_path):
    store = ScheduleStore(tmp_path)
    store.add(scheduler.build_schedule(
        kind=scheduler.KIND_TASK, request="시장 요약", interval_seconds=3600,
        created_by="test", now="2026-07-30T00:00:00Z",
    ))
    out = _apply({"command": "schedules", "proto": 2}, ControlStore(tmp_path), repo_root=tmp_path, schedules=store)
    assert out["ok"] is True and out["proto"] == 2 and isinstance(out["reply"], str)
    (row,) = out["data"]["enabled"]
    assert row["kind"] == scheduler.KIND_TASK and row["lane"] == scheduler.LANE_MAINTENANCE
    assert row["overdue_seconds"] is not None and out["data"]["overdue_count"] == 1   # 9 hours past a 1 h cadence
    assert out["data"]["disabled_count"] == 0


def test_schedules_refuses_typed_without_its_store(tmp_path):
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": "schedules"}, ControlStore(tmp_path), repo_root=tmp_path)
    assert exc.value.reason_code == "SCHEDULES_UNAVAILABLE"


def test_scheduler_events_returns_the_newest_n_and_states_a_clamped_count(tmp_path):
    ledger = LedgerStore(tmp_path)
    for i in range(3):
        ledger.append_scheduler_event({"record_type": "scheduler_event.v0", "action": "fired", "schedule_id": f"s{i}",
                                       "kind": "crypto_pipeline", "status": f"fired {i}", "created_at": f"2026-07-30T09:0{i}:00Z"})
    out = _apply({"command": "scheduler_events", "argument": "2"}, ControlStore(tmp_path), ledger=ledger, repo_root=tmp_path)
    assert out["data"]["count"] == 2 and out["data"]["active_file_total"] == 3
    assert [e["schedule_id"] for e in out["data"]["events"]] == ["s1", "s2"]
    noted = _apply({"command": "scheduler_events", "argument": "abc"}, ControlStore(tmp_path), ledger=ledger, repo_root=tmp_path)
    assert noted["data"]["count"] == 3 and "기본값" in noted["reply"]
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": "scheduler_events"}, ControlStore(tmp_path), repo_root=tmp_path)   # FakeLedger has no reader
    assert exc.value.reason_code == "SCHEDULER_LEDGER_UNAVAILABLE"


def test_heartbeat_reports_each_loop_from_its_file(tmp_path):
    heartbeat.write_heartbeat(heartbeat.OPERATOR_SERVICE, interval_seconds=25, now=NOW, root=tmp_path)
    out = _apply({"command": "heartbeat"}, ControlStore(tmp_path), repo_root=tmp_path)
    by = {c["service"]: c for c in out["data"]["services"]}
    assert by[heartbeat.OPERATOR_SERVICE]["status"] == heartbeat.FRESH
    assert by[heartbeat.SCHEDULER_RISK_SERVICE]["status"] == heartbeat.MISSING
    assert out["data"]["all_fresh"] is False
    assert "operator: FRESH" in out["reply"]


def _approval(approval_id="approval_ok", *, status="PENDING", expires_at="2026-07-30T09:15:00Z"):
    return {"approval_id": approval_id, "status": status,
            "validity": {"issued_at": NOW, "expires_at": expires_at},
            "approved_action_snapshot": {"target_ref": "trading_switch:crypto", "permission_scope": "RUNTIME_GOVERNANCE",
                                         "action_type": "RESUME"},
            "action_fingerprint": "sha256:secret", "decision": {}, "consumption": {}}


def test_approval_status_applies_the_clock_and_never_exposes_the_record(tmp_path):
    store = ApprovalStore(tmp_path)
    store.append([_approval()])
    fresh = _apply({"command": "approval_status", "argument": "approval_ok"}, ControlStore(tmp_path), repo_root=tmp_path, approval_store=store)
    assert fresh["data"]["status_recorded"] == "PENDING" and fresh["data"]["status_effective"] == "PENDING"
    assert fresh["data"]["target_prefix"] == "trading_switch" and fresh["data"]["permission_scope"] == "RUNTIME_GOVERNANCE"
    assert "action_fingerprint" not in fresh["data"] and "approved_action_snapshot" not in fresh["data"]
    assert "sha256:secret" not in fresh["reply"]
    lapsed = read_bridge.apply_read({"command": "approval_status", "argument": "approval_ok"}, control_store=ControlStore(tmp_path),
                                    ledger=FakeLedger(), now="2026-07-30T09:20:00Z", repo_root=tmp_path, approval_store=store)
    assert lapsed["data"]["status_effective"] == "EXPIRED" and lapsed["data"]["status_recorded"] == "PENDING"
    assert store.get("approval_ok")["status"] == "PENDING"   # a read wrote nothing back


@pytest.mark.parametrize("argument, code", [(None, "USAGE"), ("approval_nope", "APPROVAL_NOT_FOUND")])
def test_approval_status_refuses_typed(tmp_path, argument, code):
    store = ApprovalStore(tmp_path)
    store.append([_approval()])
    request = {"command": "approval_status"}
    if argument is not None:
        request["argument"] = argument
    with pytest.raises(ControlBlocked) as exc:
        _apply(request, ControlStore(tmp_path), repo_root=tmp_path, approval_store=store)
    assert exc.value.reason_code == code


def test_approval_status_refuses_typed_without_its_store(tmp_path):
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": "approval_status", "argument": "approval_ok"}, ControlStore(tmp_path), repo_root=tmp_path)
    assert exc.value.reason_code == "APPROVALS_UNAVAILABLE"


def test_approval_status_data_keys_are_exactly_the_named_fields(tmp_path):
    """`APPROVAL_STATUS_FIELDS` is the name the policy draft pins to; it must not drift from
    what the read actually renders, in either direction."""
    from runtime.mvp_runtime import store_reads
    from runtime.mvp_runtime.approval_store import ApprovalStore
    store = ApprovalStore(tmp_path)
    store.append([{"approval_id": "approval_fields01", "status": "PENDING",
                   "validity": {"issued_at": "2026-09-04T00:00:00Z", "expires_at": "2026-09-04T00:15:00Z"},
                   "approved_action_snapshot": {"target_ref": "trading_switch:crypto", "permission_scope": "RUNTIME_GOVERNANCE"},
                   "action_fingerprint": "sha256:x"}])
    outcome = store_reads.read_approval_status(store, "approval_fields01", now="2026-09-04T00:05:00Z")
    assert set(outcome["data"]) == set(store_reads.APPROVAL_STATUS_FIELDS)
    assert not ({"approved_action_snapshot", "action_fingerprint", "permission_decision_id"} & set(outcome["data"]))
