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


def test_the_lane_services_are_probeable_and_answer_separately(tmp_path):
    """The lane split (`SCHEDULER_LANE_SPLIT_V0.1`) runs one tick process per lane, so each
    gets its own heartbeat file and its own probe name — a shared file would let a live
    maintenance loop keep a dead risk loop reading FRESH. One lane being fresh must say
    nothing about the other."""
    heartbeat.write_heartbeat(heartbeat.SCHEDULER_RISK_SERVICE,
                              interval_seconds=30, now=NOW, root=tmp_path)
    assert heartbeat_main(["scheduler-risk"], root=tmp_path, now=NOW) == 0
    assert heartbeat_main(["scheduler-maintenance"], root=tmp_path, now=NOW) != 0


# --- BUSY: a marked fire is judged by its deadline, not by the pass age -----------------------
# (`docs/proposals/RISK_LANE_WATCHDOG_V0.1.md` §3.2)

def _busy(started: str, deadline: str, kind: str = "crypto_pipeline") -> dict:
    return {"kind": kind, "schedule_id": "schedule_x", "schedule_run_id": "srun_x",
            "started_at": started, "deadline_at": deadline}


def test_a_long_fire_inside_its_deadline_is_fresh_although_no_pass_stamped(tmp_path):
    # 8 minutes since the stamp would be STALE by the pass-age rule (300 s); the fire's own
    # 10-minute deadline says it is a long valid fire, not a dead lane.
    heartbeat.write_heartbeat("scheduler-risk", interval_seconds=30, now=NOW, root=tmp_path,
                              busy=_busy(NOW, _at(10)))
    report = heartbeat.check_heartbeat("scheduler-risk", now=_at(8), root=tmp_path)
    assert report["status"] == heartbeat.FRESH and report["overrun"] is False
    assert "busy: crypto_pipeline srun_x" in report["detail"]


def test_a_fire_past_its_deadline_is_stale_although_the_stamp_is_recent(tmp_path):
    # A breaker fire with a 2-minute deadline, 3 minutes in: the pass-age rule would still call
    # this FRESH (180 s < 300 s). The deadline says it is stuck.
    heartbeat.write_heartbeat("scheduler-risk", interval_seconds=30, now=NOW, root=tmp_path,
                              busy=_busy(NOW, _at(2), kind="crypto_breaker_watch"))
    report = heartbeat.check_heartbeat("scheduler-risk", now=_at(3), root=tmp_path)
    assert report["status"] == heartbeat.STALE and report["overrun"] is True
    assert report["detail"].startswith("OVERRUN: crypto_breaker_watch")


def test_without_a_mark_the_pass_age_rule_is_unchanged(tmp_path):
    heartbeat.write_heartbeat("scheduler-risk", interval_seconds=30, now=NOW, root=tmp_path)
    report = heartbeat.check_heartbeat("scheduler-risk", now=_at(6), root=tmp_path)
    assert report["status"] == heartbeat.STALE and "busy" not in report


@pytest.mark.parametrize("mark", [{"kind": "crypto_pipeline"}, _busy("not-a-time", _at(10)), "busy"])
def test_a_broken_mark_is_reported_unreadable(tmp_path, mark):
    path = heartbeat.heartbeat_path("scheduler-risk", tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"service": "scheduler-risk", "heartbeat_at": NOW,
                                "interval_seconds": 30, "busy": mark}), encoding="utf-8")
    assert heartbeat.check_heartbeat("scheduler-risk", now=_at(1), root=tmp_path)["status"] == heartbeat.UNREADABLE


def _record(tmp_path):
    return json.loads(heartbeat.heartbeat_path("scheduler-risk", tmp_path).read_text(encoding="utf-8"))


def test_the_marker_marks_during_the_block_and_clears_after(tmp_path):
    with heartbeat.busy_marker("scheduler-risk", interval_seconds=30, kind="crypto_pipeline",
                               schedule_id="schedule_x", schedule_run_id="srun_x",
                               deadline_seconds=600, root=tmp_path) as mark:
        inside = _record(tmp_path)
    assert inside["busy"] == mark and inside["busy"]["schedule_run_id"] == "srun_x"
    span = heartbeat.timeutil.parse_iso(mark["deadline_at"]) - heartbeat.timeutil.parse_iso(mark["started_at"])
    assert span.total_seconds() == 600
    assert "busy" not in _record(tmp_path)


def test_a_fire_that_raises_still_clears_the_mark(tmp_path):
    with pytest.raises(RuntimeError, match="boom"):
        with heartbeat.busy_marker("scheduler-risk", interval_seconds=30, kind="crypto_pipeline",
                                   schedule_id="schedule_x", schedule_run_id="srun_x",
                                   deadline_seconds=600, root=tmp_path):
            raise RuntimeError("boom")
    assert "busy" not in _record(tmp_path)


def test_a_mark_that_cannot_be_written_never_stops_the_fire(tmp_path):
    (tmp_path / ".runtime_governance_state").mkdir()
    (tmp_path / ".runtime_governance_state" / "heartbeats").write_text("a file, not a directory")
    errors, ran = [], []
    with heartbeat.busy_marker("scheduler-risk", interval_seconds=30, kind="crypto_pipeline",
                               schedule_id="schedule_x", schedule_run_id="srun_x",
                               deadline_seconds=600, root=tmp_path, on_error=errors.append):
        ran.append(True)
    assert ran == [True]
    assert len(errors) == 2 and all(isinstance(e, OSError) for e in errors)   # the mark and the clear


def test_cli_exits_zero_for_a_busy_lane_and_one_for_an_overrun(tmp_path, capsys):
    heartbeat.write_heartbeat("scheduler-risk", interval_seconds=30, now=NOW, root=tmp_path,
                              busy=_busy(NOW, _at(10)))
    assert heartbeat_main(["scheduler-risk"], root=tmp_path, now=_at(8)) == 0
    assert heartbeat_main(["scheduler-risk"], root=tmp_path, now=_at(11)) == 1
    assert "OVERRUN" in capsys.readouterr().err
