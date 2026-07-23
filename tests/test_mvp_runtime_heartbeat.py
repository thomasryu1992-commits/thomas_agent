"""Service heartbeat tests — liveness must mean the LOOP turned, not that a file parses.

The healthcheck this replaces (`console_cli status`) passed while a tick loop was wedged,
because it only proved the control state was readable. These pin the properties that make
the heartbeat a real liveness signal: it is stamped per pass, judged against the writer's
own cadence, and every failure mode is a reported status rather than a crash in the probe.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime import heartbeat
from runtime.mvp_runtime.heartbeat_cli import main as heartbeat_main

NOW = "2026-07-23T12:00:00Z"


def _at(minutes: int) -> str:
    return f"2026-07-23T{12 + minutes // 60:02d}:{minutes % 60:02d}:00Z"


def test_a_fresh_heartbeat_reports_fresh(tmp_path):
    heartbeat.write_heartbeat("scheduler", interval_seconds=30, now=NOW, root=tmp_path)
    report = heartbeat.check_heartbeat("scheduler", now=_at(1), root=tmp_path)
    assert report["status"] == heartbeat.FRESH
    assert report["age_seconds"] == 60.0


def test_a_quiet_loop_goes_stale(tmp_path):
    heartbeat.write_heartbeat("scheduler", interval_seconds=30, now=NOW, root=tmp_path)
    # 30s cadence -> the 300s floor governs, not 3x30s: one slow pipeline run is normal.
    assert heartbeat.check_heartbeat("scheduler", now=_at(4), root=tmp_path)["status"] == heartbeat.FRESH
    assert heartbeat.check_heartbeat("scheduler", now=_at(6), root=tmp_path)["status"] == heartbeat.STALE


def test_a_slow_cadence_widens_its_own_threshold(tmp_path):
    # The threshold is derived from the writer, so a deliberately slow loop is not
    # called stalled just for being slow.
    heartbeat.write_heartbeat("operator", interval_seconds=600, now=NOW, root=tmp_path)
    assert heartbeat.stale_after_seconds(600) == 1800
    assert heartbeat.check_heartbeat("operator", now=_at(25), root=tmp_path)["status"] == heartbeat.FRESH
    assert heartbeat.check_heartbeat("operator", now=_at(31), root=tmp_path)["status"] == heartbeat.STALE


def test_a_service_that_never_started_is_missing(tmp_path):
    report = heartbeat.check_heartbeat("scheduler", now=NOW, root=tmp_path)
    assert report["status"] == heartbeat.MISSING and report["age_seconds"] is None


@pytest.mark.parametrize("content", ["{not json", '{"service": "scheduler"}', "[]"])
def test_a_broken_record_is_reported_not_raised(tmp_path, content):
    # The probe exists to report trouble; it must never become the trouble.
    path = heartbeat.heartbeat_path("scheduler", tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    assert heartbeat.check_heartbeat("scheduler", now=NOW, root=tmp_path)["status"] == heartbeat.UNREADABLE


def test_a_rewrite_replaces_rather_than_accumulates(tmp_path):
    heartbeat.write_heartbeat("scheduler", interval_seconds=30, now=NOW, root=tmp_path)
    heartbeat.write_heartbeat("scheduler", interval_seconds=30, now=_at(1), root=tmp_path)
    record = json.loads(heartbeat.heartbeat_path("scheduler", tmp_path).read_text(encoding="utf-8"))
    assert record["heartbeat_at"] == _at(1)
    assert record["service"] == "scheduler" and isinstance(record["pid"], int)


# --- the probe CLI (this is what the container healthcheck runs) ---------------

def test_cli_exits_zero_only_while_fresh(tmp_path, capsys):
    heartbeat.write_heartbeat("scheduler", interval_seconds=30, now=NOW, root=tmp_path)
    assert heartbeat_main(["scheduler"], root=tmp_path, now=_at(1)) == 0
    assert "FRESH" in capsys.readouterr().out

    assert heartbeat_main(["scheduler"], root=tmp_path, now=_at(30)) == 1
    assert "STALE" in capsys.readouterr().err


def test_cli_reports_a_service_that_never_started(tmp_path, capsys):
    assert heartbeat_main(["operator"], root=tmp_path, now=NOW) == 1
    assert "MISSING" in capsys.readouterr().err
