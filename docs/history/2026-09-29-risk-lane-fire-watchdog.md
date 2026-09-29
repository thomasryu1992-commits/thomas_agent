# A stuck risk fire ends the risk lane's process, and Docker restarts it

- **Why:** step 2 of `docs/proposals/RISK_LANE_WATCHDOG_V0.1.md` (Thomas, 2026-09-29: restart on
  timeout; deadlines approved as proposed). The risk lane runs the pipeline, the loss-breaker watch
  and the route watch one after another in one process. One fire that never returns silences all
  three, and `restart: unless-stopped` does not act on `unhealthy`.
- **What changed:**
  - `runtime/mvp_runtime/fire_watchdog.py`: `FireWatchdog.armed(mark, deadline_seconds)` starts a
    thread per fire. At the deadline it dumps all stacks to stderr, writes
    `heartbeats/scheduler-risk.watchdog.json` (tmp+replace), and calls `os._exit(70)`. The exit is
    in `finally`, so evidence that cannot be written never keeps a stuck lane up.
  - A `faulthandler.dump_traceback_later(exit=True)` backstop at deadline + 30 s covers a hang that
    holds the GIL. Disarm is in `finally`, so a fire that raises leaves no timer behind.
  - Arming is an observer's act: a watcher thread that cannot start, or a backstop that cannot arm
    (a stderr with no file descriptor), is said on stderr, and the fire runs unwatched as before.
    Found while testing: the backstop raised under captured stderr, and the fire was recorded as
    failed.
  - `scheduler_cli tick`: armed on `--lane risk` only, around the kinds with a deadline, stacked on
    step 1's BUSY mark. The maintenance lane and `--lane all` are never ended by it.
  - On startup the risk lane reports a previous watchdog exit once (`report_previous_overrun`:
    stderr, one operator alert, file moved aside). The alert comes from the healthy process.
  - Runbook §3 says the same for operators.
- **What it cannot do:** `os._exit` can land mid-write. JSONL shows a torn last line on read, the
  hash-chained ledger fails verification on one, and tmp+replace files keep their previous
  version, so the damage is visible, not silent. Exchange-resting orders are untouched, and
  closing is never stage-gated.
- **Tests:** in-process with an injected exit (finish in time, overrun with evidence and stacks,
  raise then disarm, evidence failure still exits, backstop that cannot arm), the startup report
  (once, unreadable file still reported), the lane wiring (risk lane only, risk kinds only), and
  two real child processes (exit code 70; the faulthandler backstop ends a process whose Python
  stage did not). Mutation: removing the exit, the disarm, exit-on-evidence-failure, the lane
  restriction, or the startup report each fails a test.
