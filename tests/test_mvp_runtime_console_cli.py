"""R4 Local operator emergency-console CLI tests."""

from __future__ import annotations

import json

from runtime.mvp_runtime.console_cli import main
from runtime.mvp_runtime.control import ACTIVE, KILLED, PAUSED, ControlStore
from runtime.mvp_runtime.store import LedgerStore

NOW = "2026-07-16T09:00:00Z"


def _stores(tmp_path):
    return ControlStore(tmp_path), LedgerStore(tmp_path / "ledger")


def test_status_on_fresh_state_is_active(tmp_path, capsys):
    control_store, ledger = _stores(tmp_path)
    rc = main(["status"], control_store=control_store, ledger=ledger, now=NOW)
    assert rc == 0
    assert "mode: ACTIVE" in capsys.readouterr().out


def test_kill_sets_state_and_records_event(tmp_path, capsys):
    control_store, ledger = _stores(tmp_path)
    rc = main(["kill", "--reason", "halt now"], control_store=control_store, ledger=ledger, now=NOW)
    assert rc == 0
    assert control_store.load().mode == KILLED
    rows = (ledger.root / "control_events.jsonl").read_text(encoding="utf-8").strip().splitlines()
    event = json.loads(rows[0])
    assert event["action"] == "kill" and event["resulting_mode"] == KILLED
    assert event["actor"] == "local_console"


def test_pause_then_resume(tmp_path):
    control_store, ledger = _stores(tmp_path)
    main(["pause"], control_store=control_store, ledger=ledger, now=NOW)
    assert control_store.load().mode == PAUSED
    main(["resume"], control_store=control_store, ledger=ledger, now=NOW)
    assert control_store.load().mode == ACTIVE


def test_stop_without_task_id_fails_closed(tmp_path, capsys):
    control_store, ledger = _stores(tmp_path)
    rc = main(["stop"], control_store=control_store, ledger=ledger, now=NOW)
    assert rc == 2
    assert "MISSING_TASK_ID" in capsys.readouterr().err


def test_stop_with_task_id_records(tmp_path):
    control_store, ledger = _stores(tmp_path)
    rc = main(["stop", "task-42"], control_store=control_store, ledger=ledger, now=NOW)
    assert rc == 0
    assert "task-42" in control_store.load().stop_requested_task_ids


def test_kill_persisted_across_new_store_instances(tmp_path):
    control_store, ledger = _stores(tmp_path)
    main(["kill"], control_store=control_store, ledger=ledger, now=NOW)
    # A fresh store over the same directory sees the killed state (durable, per-machine).
    assert ControlStore(tmp_path).load().mode == KILLED
