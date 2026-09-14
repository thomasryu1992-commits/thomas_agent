"""Entry-point cutover and rollback rehearsals (sequence 2, P10; plan §7 이전 B / 롤백 A·B;
acceptance A22, A25).

Cutover is a configuration change, not a data migration: the target entry point's NEW intake is
closed (`--v2-intake closed` on the dispatch bridge), what is in flight drains, and the client is
switched (shim v3 tools). Rollback before any new work is the flag off and the shim back on v2;
rollback after new work keeps the store, keeps it readable, and lets a later manager finish what
was accepted — never a restore over it. Every rehearsal runs in an isolated root.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime import dispatch_bridge, task_registry, workflow as wf, workflow_cli
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.errors import ControlBlocked
from runtime.mvp_runtime.socket_door import ASSISTANT_ACTOR
from runtime.mvp_runtime.store import LedgerStore
from runtime.mvp_runtime.task_registry import TaskRegistryStore
from runtime.mvp_runtime.workflow_manager import WorkflowManager
from runtime.mvp_runtime.workflow_store import WorkflowStore

NOW = "2026-09-14T14:00:00Z"
LATER = "2026-09-14T14:05:00Z"


def _plan(goal="목표"):
    return {"schema_version": "workflow_plan.v0.1", "goal": goal,
            "steps": [{"id": "a", "capability": "analysis", "request": "x", "reason": "r"}],
            "budget": {"max_model_calls": 2}}


def _v2(**over):
    req = {"request": "analyze this idea", "kind": "analysis", "reason": "operator asked", "proto": 2}
    req.update(over)
    return req


@pytest.fixture
def executor():
    calls: list[dict] = []

    def _execute(text, kind, reason, naver_keywords, client_id=None):
        calls.append({"request": text, "kind": kind})
        return {"ok": True, "kind": kind, "task_id": "task_1", "registry_entry_id": "treg_1",
                "final_response": "ok", "actor": ASSISTANT_ACTOR}

    _execute.calls = calls
    return _execute


def _door(request, root, *, execute, store, ledger=None, registry=None, v2_intake=True, manager_enabled=True):
    return dispatch_bridge.apply_dispatch(
        request, control_store=ControlStore(root), ledger=ledger, execute=execute, registry=registry,
        workflow_store=store, manager_enabled=manager_enabled, v2_intake=v2_intake, now=NOW,
    )


# --- A22: close the legacy intake, drain, switch ---------------------------------------------------------

def test_closing_the_v2_intake_refuses_new_dispatches_and_nothing_else(tmp_path, executor):
    store = WorkflowStore(tmp_path)
    with pytest.raises(ControlBlocked) as exc:
        _door(_v2(), tmp_path, execute=executor, store=store, v2_intake=False)
    assert exc.value.reason_code == "V2_INTAKE_CLOSED" and executor.calls == []
    # the new path and the reads are untouched by the closed intake
    out = _door({"command": "workflow.submit", "request_id": "hermes-1", "plan": _plan(), "proto": 2},
                tmp_path, execute=executor, store=store, v2_intake=False)
    assert out["ok"] and out["data"]["status"] == wf.W_VALIDATED
    caps = _door({"command": "capabilities"}, tmp_path, execute=executor, store=store, v2_intake=False)["data"]
    assert caps["v2_intake"] == "closed" and caps["workflow_manager"] is True
    assert _door({"command": "capabilities"}, tmp_path, execute=executor, store=store)["data"]["v2_intake"] == "open"
    # a closed intake is refused BEFORE the kill switch or the forwarder could matter: nothing ran
    assert executor.calls == []


def test_a_retry_of_a_dispatch_accepted_before_the_close_still_replays(tmp_path, executor):
    ledger = LedgerStore(tmp_path / "ledger")
    store = WorkflowStore(tmp_path)
    first = _door(_v2(request_id="hermes-before"), tmp_path, execute=executor, store=store, ledger=ledger)
    assert first["ok"] and len(executor.calls) == 1
    # intake closes; the client that lost the reply retries with the same id and gets the run back
    again = _door(_v2(request_id="hermes-before"), tmp_path, execute=executor, store=store, ledger=ledger, v2_intake=False)
    assert again["replayed"] is True and again["data"]["task_id"] == "task_1" and len(executor.calls) == 1
    with pytest.raises(ControlBlocked) as exc:
        _door(_v2(request_id="hermes-after"), tmp_path, execute=executor, store=store, ledger=ledger, v2_intake=False)
    assert exc.value.reason_code == "V2_INTAKE_CLOSED" and len(executor.calls) == 1


def test_drain_reports_what_is_in_flight_on_both_paths_and_says_when_nothing_is(tmp_path, capsys):
    registry = TaskRegistryStore(tmp_path)
    store = WorkflowStore(tmp_path)
    # a legacy run still going (the worker opened it, has not closed it)
    legacy = task_registry.record_submission(registry, request_text="old path", origin=task_registry.AGENT_ORIGIN,
                                             requester_id=ASSISTANT_ACTOR, now=NOW, request_kind="analysis")
    # a workflow attempt in flight
    wid = store.submit(principal="hermes", request_id="hermes-1", plan=_plan(), now=NOW).workflow_id
    (attempt,) = store.claim_ready(now=NOW)
    report = workflow_cli.drain_status(tmp_path, now=NOW)
    assert report["drained"] is False and report["workflow_store"] == "present"
    assert report["legacy_running"] == {"AGENT": [legacy.registry_entry_id]}
    assert [a["attempt_id"] for a in report["workflow_attempts_running"]] == [attempt.attempt_id]
    # both settle: the legacy row closes, the attempt lands
    task_registry.close_entry(registry, legacy, status=task_registry.DELIVERED, now=LATER, task_id="task_1",
                              trace_id="trace_1", result_ref="ledger:trace_1")
    store.record_result(attempt.attempt_id, now=LATER, succeeded=True, result_ref="ledger:trace_2", model_calls=1)
    assert workflow_cli.main(["drain"], repo_root=tmp_path, now=LATER) == 0
    import json
    report = json.loads(capsys.readouterr().out)
    assert report["drained"] is True and report["legacy_running"] == {} and report["workflow_attempts_running"] == []
    assert store.status_view(wid, now=LATER)["status"] == wf.W_COMPLETED


def test_after_the_switch_no_legacy_writer_runs_and_the_id_namespaces_never_collide(tmp_path, executor):
    registry = TaskRegistryStore(tmp_path)
    store = WorkflowStore(tmp_path)
    before = len(registry.latest())
    for i in range(3):
        _door({"command": "workflow.submit", "request_id": f"hermes-{i}", "plan": _plan(f"작업 {i}"), "proto": 2},
              tmp_path, execute=executor, store=store, registry=registry, v2_intake=False)
    assert executor.calls == [] and len(registry.latest()) == before               # the door wrote no legacy row
    # the manager runs them through the worker path, whose rows are origin WORKFLOW, never AGENT
    frames: list[dict] = []

    def call(path, frame, *, deadline_seconds):
        frames.append(frame)
        n = len(frames)
        entry = task_registry.record_submission(registry, request_text=frame["request"], origin=task_registry.WORKFLOW_ORIGIN,
                                                requester_id=ASSISTANT_ACTOR, now=NOW, request_kind=frame["kind"],
                                                attempt_id=frame["attempt_id"])
        task_registry.close_entry(registry, entry, status=task_registry.DELIVERED, now=NOW, task_id=f"task_{n}",
                                  trace_id=f"trace_{n}", result_ref=f"ledger:trace_{n}")
        return {"ok": True, "kind": frame["kind"], "task_id": f"task_{n}", "trace_id": f"trace_{n}",
                "registry_entry_id": entry.registry_entry_id, "final_response": "done", "actor": ASSISTANT_ACTOR,
                "attempt_id": frame["attempt_id"], "workflow_id": frame["workflow_id"]}

    manager = WorkflowManager(store, control_store=ControlStore(tmp_path), worker_socket=tmp_path / "internal" / "pipeline.sock",
                              call=call, door_is_live=lambda p: True, clock=lambda: NOW, synchronous=True, registry=registry,
                              concurrency=3, log=lambda line: None)
    manager.tick()
    rows = registry.latest()
    assert len(rows) == 3 and {r.origin for r in rows} == {task_registry.WORKFLOW_ORIGIN}
    ids = {r.registry_entry_id for r in rows} | {r.task_id for r in rows} | {w["workflow_id"] for w in store.list_workflows()}
    assert len(ids) == 9                                                                # treg_, task_, wf_: disjoint
    assert all(w["status"] == wf.W_COMPLETED for w in store.list_workflows())


# --- A25: rollback before and after new work --------------------------------------------------------------

def test_rollback_before_any_new_work_is_the_flag_off_and_the_v2_path_as_before(tmp_path, executor):
    # flag off: the door is opened without the store; v3 is refused by name, v2 works
    with pytest.raises(ControlBlocked) as exc:
        _door({"command": "workflow.submit", "request_id": "x", "plan": _plan(), "proto": 2}, tmp_path,
              execute=executor, store=None, manager_enabled=False)
    assert exc.value.reason_code == "WORKFLOW_UNAVAILABLE"
    out = _door(_v2(), tmp_path, execute=executor, store=None, manager_enabled=False)
    assert out["ok"] and len(executor.calls) == 1
    assert not WorkflowStore.exists(tmp_path)                                          # nothing was created


def test_rollback_after_new_work_keeps_the_store_readable_and_a_later_manager_finishes_it(tmp_path, executor):
    store = WorkflowStore(tmp_path)
    done = store.submit(principal="hermes", request_id="hermes-1", plan=_plan("끝난 것"), now=NOW).workflow_id
    (att,) = store.claim_ready(now=NOW)
    store.record_result(att.attempt_id, now=NOW, succeeded=True, result_ref="ledger:trace_1", model_calls=1)
    pending = store.submit(principal="hermes", request_id="hermes-2", plan=_plan("남은 것"), now=NOW).workflow_id
    # rollback: the manager flag goes off and the shim goes back to v2 — the door refuses v3 by name,
    # the v2 path works, and the store is untouched and still readable by a compatible service
    with pytest.raises(ControlBlocked) as exc:
        _door({"command": "workflow.status", "workflow_id": done, "proto": 2}, tmp_path, execute=executor,
              store=None, manager_enabled=False)
    assert exc.value.reason_code == "WORKFLOW_UNAVAILABLE"
    assert _door(_v2(), tmp_path, execute=executor, store=None, manager_enabled=False)["ok"]
    reader = WorkflowStore(tmp_path, readonly=True)
    assert reader.status_view(done, now=LATER)["status"] == wf.W_COMPLETED
    assert reader.status_view(pending, now=LATER)["status"] == wf.W_VALIDATED
    assert reader.status_view(done, now=LATER)["steps"][0]["result_ref"] == "ledger:trace_1"
    # forward again: a later manager (a compatible release) picks the pending one up and finishes it
    manager = WorkflowManager(WorkflowStore(tmp_path), control_store=ControlStore(tmp_path),
                              worker_socket=tmp_path / "internal" / "pipeline.sock",
                              call=lambda p, f, *, deadline_seconds: {"ok": True, "kind": f["kind"], "task_id": "task_2",
                                                                       "trace_id": "trace_2", "registry_entry_id": "treg_2",
                                                                       "final_response": "done", "actor": ASSISTANT_ACTOR,
                                                                       "attempt_id": f["attempt_id"], "workflow_id": f["workflow_id"]},
                              door_is_live=lambda p: True, clock=lambda: LATER, synchronous=True, log=lambda line: None)
    manager.startup_report()                                     # nothing was in flight; nothing to recover
    assert manager.tick()["claimed"] == 1
    assert WorkflowStore(tmp_path, readonly=True).status_view(pending, now=LATER)["status"] == wf.W_COMPLETED


def test_the_bridge_reads_its_intake_mode_from_the_command_line_and_the_environment(monkeypatch):
    from runtime.mvp_runtime import dispatch_bridge_cli as cli

    monkeypatch.delenv(cli.V2_INTAKE_ENV, raising=False)
    assert cli._parse_args([]).v2_intake == "open"
    assert cli._parse_args(["--v2-intake", "closed"]).v2_intake == "closed"
    monkeypatch.setenv(cli.V2_INTAKE_ENV, "closed")
    assert cli._parse_args([]).v2_intake == "closed"
    monkeypatch.setenv(cli.V2_INTAKE_ENV, "sideways")
    with pytest.raises(SystemExit):
        cli._parse_args([])



def test_a_closed_intake_takes_no_new_run_even_when_the_old_claim_lapses_between_lookup_and_claim(tmp_path, executor, monkeypatch):
    from runtime.mvp_runtime import bridge_idempotency

    ledger = LedgerStore(tmp_path / "ledger")
    store = WorkflowStore(tmp_path)
    assert _door(_v2(request_id="hermes-edge"), tmp_path, execute=executor, store=store, ledger=ledger)["ok"]
    real_claim = bridge_idempotency.claim
    monkeypatch.setattr(bridge_idempotency, "claim", lambda ledger, **kw: real_claim(ledger, **{**kw, "now": "2026-09-16T00:00:00Z"}))
    with pytest.raises(ControlBlocked) as exc:                                 # the lookup saw the row; the claim did not
        _door(_v2(request_id="hermes-edge"), tmp_path, execute=executor, store=store, ledger=ledger, v2_intake=False)
    assert exc.value.reason_code == "V2_INTAKE_CLOSED" and len(executor.calls) == 1



def test_the_intake_flag_reaches_the_door_the_bridge_serves_and_no_flag_means_no_store(monkeypatch):
    """Review of P10 (2026-09-14): the rehearsals called `apply_dispatch` directly, so a bridge
    that dropped `v2_intake` on the way to `open_door` — or created the workflow store without the
    manager flag — passed every test. This runs the CLI's own wiring with the socket stubbed."""
    from runtime.mvp_runtime import dispatch_bridge_cli as cli

    served = []
    monkeypatch.setattr(cli, "serve_door_forever", lambda **kw: served.append(kw) or 0)
    opened = []
    monkeypatch.setattr(cli.dispatch_bridge, "open_door", lambda path, **kw: opened.append(kw) or object())
    monkeypatch.setattr(cli.WorkflowManager, "start", lambda self: None)
    monkeypatch.setattr(cli.WorkflowManager, "stop", lambda self: None)
    monkeypatch.delenv(cli.V2_INTAKE_ENV, raising=False)

    assert cli.main(["--v2-intake", "closed"]) == 0
    served[-1]["open_server"]()
    assert opened[-1]["v2_intake"] is False and opened[-1]["workflow_store"] is None
    assert opened[-1]["manager_enabled"] is False and not WorkflowStore.exists()   # no flag, no store

    assert cli.main(["--workflow-manager"]) == 0
    served[-1]["open_server"]()
    assert opened[-1]["v2_intake"] is True and opened[-1]["workflow_store"] is not None and opened[-1]["manager_enabled"]

    monkeypatch.setenv(cli.V2_INTAKE_ENV, "")
    assert cli._parse_args([]).v2_intake == "open"                                  # a blank variable never stops the door


def test_drain_counts_a_queued_legacy_request_as_in_flight(tmp_path):
    registry = TaskRegistryStore(tmp_path)
    queued, _position = task_registry.enqueue(registry, request_text="아직 대기", origin="TELEGRAM",
                                              requester_id="thomas", now=NOW)
    report = workflow_cli.drain_status(tmp_path, now=NOW)
    assert report["drained"] is False and queued.registry_entry_id in sum(report["legacy_running"].values(), [])
