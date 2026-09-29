"""The risk lane's fire watchdog (`docs/proposals/RISK_LANE_WATCHDOG_V0.1.md`).

In-process cases inject ``exit`` so the watchdog can act without ending pytest; two cases run a
real child process, because "the process actually ends, with the code Docker restarts on" is
the property that matters and only a real process can show it.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from runtime.mvp_runtime import fire_watchdog, heartbeat
from runtime.mvp_runtime.fire_watchdog import EXIT_WATCHDOG, FireWatchdog

SERVICE = heartbeat.SCHEDULER_RISK_SERVICE
MARK = {"kind": "crypto_pipeline", "schedule_id": "schedule_x", "schedule_run_id": "srun_x",
        "started_at": "2026-09-29T05:00:00Z", "deadline_at": "2026-09-29T05:10:00Z"}
REPO_ROOT = Path(__file__).resolve().parents[1]


class _Exit:
    def __init__(self):
        self.codes = []
        self.called = threading.Event()

    def __call__(self, code):
        self.codes.append(code)
        self.called.set()


def _watchdog(tmp_path, exit, stderr=None):
    return FireWatchdog(SERVICE, root=tmp_path, exit=exit, stderr=stderr or io.StringIO(), backstop=False)


def test_a_fire_that_finishes_in_time_is_never_ended(tmp_path):
    exit = _Exit()
    with _watchdog(tmp_path, exit).armed(MARK, deadline_seconds=0.3):
        pass
    assert not exit.called.wait(0.6)
    assert not fire_watchdog.diagnostic_path(SERVICE, tmp_path).exists()


def test_a_fire_past_its_deadline_is_ended_with_evidence(tmp_path):
    exit, err = _Exit(), io.StringIO()
    with _watchdog(tmp_path, exit, err).armed(MARK, deadline_seconds=0.2):
        assert exit.called.wait(5)                     # the stuck fire: waits until the watchdog acts
    assert exit.codes == [EXIT_WATCHDOG]
    record = json.loads(fire_watchdog.diagnostic_path(SERVICE, tmp_path).read_text(encoding="utf-8"))
    assert record["schedule_run_id"] == "srun_x" and record["deadline_seconds"] == 0.2
    assert record["stacks"] and any("test_a_fire_past_its_deadline" in line for line in record["stacks"])
    assert "WATCHDOG — crypto_pipeline srun_x" in err.getvalue()


def test_a_fire_that_raises_is_disarmed(tmp_path):
    exit = _Exit()
    with pytest.raises(RuntimeError):
        with _watchdog(tmp_path, exit).armed(MARK, deadline_seconds=0.3):
            raise RuntimeError("boom")
    assert not exit.called.wait(0.6)


def test_evidence_that_cannot_be_written_does_not_stop_the_exit(tmp_path):
    (tmp_path / ".runtime_governance_state").mkdir()
    (tmp_path / ".runtime_governance_state" / "heartbeats").write_text("a file, not a directory")
    exit, err = _Exit(), io.StringIO()
    with _watchdog(tmp_path, exit, err).armed(MARK, deadline_seconds=0.2):
        assert exit.called.wait(5)
    assert exit.codes == [EXIT_WATCHDOG]
    assert "watchdog evidence not written" in err.getvalue()


def test_a_backstop_that_cannot_arm_never_stops_the_fire(tmp_path):
    # A stderr with no file descriptor cannot take faulthandler's dump. The fire must still run.
    err, ran = io.StringIO(), []
    watchdog = FireWatchdog(SERVICE, root=tmp_path, exit=_Exit(), stderr=err, backstop=True)
    with watchdog.armed(MARK, deadline_seconds=5):
        ran.append(True)
    assert ran == [True] and "backstop NOT armed" in err.getvalue()


def _diagnostic(tmp_path, content: str):
    path = fire_watchdog.diagnostic_path(SERVICE, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_the_next_start_alerts_once_and_moves_the_evidence_aside(tmp_path):
    path = _diagnostic(tmp_path, json.dumps(MARK))
    alerts = []
    now = "2026-09-29T05:11:00Z"
    record = fire_watchdog.report_previous_overrun(SERVICE, root=tmp_path, now=now,
                                                   alerter=lambda k, m: alerts.append((k, m)), stderr=io.StringIO())
    assert record["schedule_run_id"] == "srun_x"
    assert [k for k, _ in alerts] == [fire_watchdog.OVERRUN_ALERT_KEY] and "srun_x" in alerts[0][1]
    assert not path.exists() and (path.parent / f"{SERVICE}.watchdog.20260929T051100Z.json").is_file()
    assert fire_watchdog.report_previous_overrun(SERVICE, root=tmp_path, now=now,
                                                 alerter=lambda k, m: alerts.append((k, m))) is None
    assert len(alerts) == 1


def test_an_unreadable_diagnostic_is_still_reported(tmp_path):
    _diagnostic(tmp_path, "{not json")
    alerts = []
    record = fire_watchdog.report_previous_overrun(SERVICE, root=tmp_path, now="2026-09-29T05:11:00Z",
                                                   alerter=lambda k, m: alerts.append(k), stderr=io.StringIO())
    assert "unreadable" in record and alerts == [fire_watchdog.OVERRUN_ALERT_KEY]


# --- a real process: the property Docker's restart depends on ---------------------------------

def _child(tmp_path, body: str) -> subprocess.CompletedProcess:
    script = textwrap.dedent(f"""
        import sys, time
        from pathlib import Path
        from runtime.mvp_runtime import fire_watchdog
        root = Path({str(tmp_path)!r})
        mark = {MARK!r}
    """) + textwrap.dedent(body)
    return subprocess.run([sys.executable, "-c", script], cwd=REPO_ROOT, capture_output=True,
                          text=True, timeout=60)


def test_a_stuck_fire_ends_the_real_process_with_the_restart_code(tmp_path):
    started = time.monotonic()
    out = _child(tmp_path, """
        with fire_watchdog.FireWatchdog("scheduler-risk", root=root).armed(mark, deadline_seconds=0.5):
            time.sleep(30)
        print("fire returned")
    """)
    assert out.returncode == EXIT_WATCHDOG, out.stderr
    assert "fire returned" not in out.stdout and time.monotonic() - started < 20
    assert fire_watchdog.diagnostic_path(SERVICE, tmp_path).is_file()


def test_the_backstop_ends_the_process_when_the_python_stage_cannot(tmp_path):
    # The Python stage "fails" (its exit does nothing); faulthandler's C thread must end it.
    out = _child(tmp_path, """
        fire_watchdog.BACKSTOP_GRACE_SECONDS = 0.5
        wd = fire_watchdog.FireWatchdog("scheduler-risk", root=root, exit=lambda code: None)
        with wd.armed(mark, deadline_seconds=0.3):
            time.sleep(30)
        print("fire returned")
    """)
    assert out.returncode != 0 and "fire returned" not in out.stdout
    assert "Timeout" in out.stderr or "Thread" in out.stderr       # faulthandler's own dump
