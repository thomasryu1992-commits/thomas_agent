"""The risk lane's fire watchdog — a stuck risk fire ends in a process restart.

`docs/proposals/RISK_LANE_WATCHDOG_V0.1.md` (Thomas 2026-09-29: restart on timeout; deadlines in
`scheduler.RISK_FIRE_DEADLINE_SECONDS`). The risk lane runs the pipeline, the loss-breaker watch and
the route watch one after another in one process, so one fire that never returns silences all
three, and `restart: unless-stopped` does not act on `unhealthy`. This module is what acts.

Two stages, because the thing that has to notice a hang cannot be the hung thing:

1. **A Python thread** armed per fire. At the deadline it dumps every thread's stack to stderr,
   writes a diagnostic file next to the lane heartbeat, and ends the process with exit code
   ``EXIT_WATCHDOG`` for Docker to relaunch. A diagnostic that cannot be written does not stop
   the exit: a failed record must never be the reason a stuck lane stays up.
2. **A C-level backstop**, ``faulthandler.dump_traceback_later(exit=True)``, a little after the
   deadline — for a hang that holds the GIL, where no Python thread can run at all.

The diagnostic file is not a governance record: the fire's ledger ending is the existing
``abandoned_mid_run``, written by the next process's startup scan. The next process also reads the
diagnostic file, alerts the operator once, and moves it aside (``report_previous_overrun``) — the
alert is sent by a healthy process, never by the stuck one.

What this cannot do, stated rather than hidden: ``os._exit`` can land mid-write. Append-only JSONL
reveals a torn last line on read, the hash-chained ledger fails verification on one, and
tmp+replace files keep their previous version — so a torn write is visible, not silent. Resting
exchange orders are untouched by a restart, and closing is never gated by the execution stage.
"""

from __future__ import annotations

import faulthandler
import json
import os
import sys
import threading
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, TextIO

from . import heartbeat, timeutil

EXIT_WATCHDOG = 70           # EX_SOFTWARE: the process ended itself on purpose
BACKSTOP_GRACE_SECONDS = 30.0
STACK_LINES_KEPT = 60
OVERRUN_ALERT_KEY = "risk_watchdog_overrun"


def diagnostic_path(service: str, root: Path | None = None) -> Path:
    return heartbeat.heartbeats_dir(root) / f"{service}.watchdog.json"


def _stacks() -> list[str]:
    """Every thread's stack, newest frame last, as text — the part of the dump worth keeping."""
    names = {t.ident: t.name for t in threading.enumerate()}
    lines: list[str] = []
    for ident, frame in sys._current_frames().items():  # noqa: SLF001 — the documented way to read them
        lines.append(f"thread {names.get(ident, ident)}:")
        lines.extend(line.rstrip("\n") for line in traceback.format_stack(frame))
    return lines[-STACK_LINES_KEPT:]


class FireWatchdog:
    """Arms per fire. ``exit`` and ``backstop`` are injectable so tests can watch it act without
    ending the test process; production uses ``os._exit`` and ``faulthandler``."""

    def __init__(
        self, service: str, *, root: Path | None = None,
        exit: Callable[[int], Any] = os._exit,
        stderr: TextIO | None = None,
        backstop: bool = True,
    ) -> None:
        self.service = service
        self.root = root
        self._exit = exit
        self._stderr = stderr
        self._backstop = backstop

    @property
    def stderr(self) -> TextIO:
        return self._stderr if self._stderr is not None else sys.stderr

    @contextmanager
    def armed(self, mark: dict[str, Any], *, deadline_seconds: float) -> Iterator[None]:
        """Watch one fire. ``mark`` is the BUSY mark (kind, schedule, run id, start, deadline).
        Disarmed in ``finally``: a fire that raises must not leave a timer that later kills a
        healthy loop."""
        # Arming is an observer's act, and an observer must never stop the fire it watches: a
        # watcher that cannot start or a backstop that cannot arm is said on stderr and the fire
        # runs unwatched, exactly as it did before this module existed.
        done = threading.Event()
        try:
            threading.Thread(
                target=self._watch, args=(done, dict(mark), deadline_seconds),
                name=f"fire-watchdog-{mark.get('schedule_run_id')}", daemon=True,
            ).start()
        except Exception as exc:  # noqa: BLE001
            self._say(f"SCHEDULER: watchdog NOT armed for {mark.get('kind')} ({type(exc).__name__})\n")
        backstop = False
        if self._backstop:
            try:
                faulthandler.dump_traceback_later(deadline_seconds + BACKSTOP_GRACE_SECONDS,
                                                  exit=True, file=self.stderr)
                backstop = True
            except Exception as exc:  # noqa: BLE001 — e.g. a stderr with no file descriptor
                self._say(f"SCHEDULER: watchdog backstop NOT armed ({type(exc).__name__})\n")
        try:
            yield
        finally:
            done.set()
            if backstop:
                faulthandler.cancel_dump_traceback_later()

    def _say(self, text: str) -> None:
        try:
            self.stderr.write(text)
        except Exception:  # noqa: BLE001
            pass

    def _watch(self, done: threading.Event, mark: dict[str, Any], deadline_seconds: float) -> None:
        if done.wait(deadline_seconds):
            return
        try:
            self.stderr.write(
                f"SCHEDULER: WATCHDOG — {mark.get('kind')} {mark.get('schedule_run_id')} passed its "
                f"{deadline_seconds:g}s deadline; dumping stacks and exiting {EXIT_WATCHDOG} for a restart\n")
            faulthandler.dump_traceback(file=self.stderr, all_threads=True)
            self.stderr.flush()
        except Exception:  # noqa: BLE001 — the exit below must happen regardless
            pass
        try:
            record = {**mark, "service": self.service, "pid": os.getpid(),
                      "deadline_seconds": deadline_seconds, "overrun_at": timeutil.utc_now_iso(),
                      "stacks": _stacks()}
            path = diagnostic_path(self.service, self.root)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(path)
        except Exception as exc:  # noqa: BLE001 — evidence is best-effort; the exit is not
            try:
                self.stderr.write(f"SCHEDULER: watchdog evidence not written ({type(exc).__name__}); exiting anyway\n")
            except Exception:  # noqa: BLE001
                pass
        finally:
            self._exit(EXIT_WATCHDOG)


def report_previous_overrun(
    service: str, *, root: Path | None, now: str,
    alerter: Callable[[str, str], None] | None, stderr: TextIO | None = None,
) -> dict[str, Any] | None:
    """At startup: if the previous process of ``service`` was ended by the watchdog, say so once.

    Moves the diagnostic aside (``*.watchdog.<stamp>.json``) after reading it, so the next start is
    quiet. Never raises — like the other startup diagnoses, this must not stop the loop starting."""
    err = stderr if stderr is not None else sys.stderr
    path = diagnostic_path(service, root)
    if not path.is_file():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        record = {"unreadable": f"{type(exc).__name__}: {exc}"}
    stamp = now.replace(":", "").replace("-", "")
    try:
        path.replace(path.with_name(f"{service}.watchdog.{stamp}.json"))
    except OSError as exc:
        err.write(f"SCHEDULER: watchdog diagnostic not moved aside ({type(exc).__name__})\n")
    err.write(f"SCHEDULER: previous process ended by the watchdog — {record.get('kind')} "
              f"{record.get('schedule_run_id')} started {record.get('started_at')}, "
              f"deadline {record.get('deadline_at')}\n")
    if alerter is not None:
        alerter(OVERRUN_ALERT_KEY,
                "[리스크 레인 재시작] 워치독이 멈춘 fire를 끝내고 프로세스를 재시작했습니다.\n"
                f"- 종류: {record.get('kind')} ({record.get('schedule_id')})\n"
                f"- 회차: {record.get('schedule_run_id')}\n"
                f"- 시작: {record.get('started_at')} / 마감: {record.get('deadline_at')}\n"
                f"확인 시각: {now}\n"
                "그 회차는 완료되지 않았고 재시도하지 않습니다(at-most-once). 스택은 컨테이너 로그와 "
                f".runtime_governance_state/heartbeats/{service}.watchdog.{stamp}.json 에 있습니다.")
    return record


__all__ = [
    "BACKSTOP_GRACE_SECONDS", "EXIT_WATCHDOG", "FireWatchdog", "OVERRUN_ALERT_KEY",
    "diagnostic_path", "report_previous_overrun",
]
