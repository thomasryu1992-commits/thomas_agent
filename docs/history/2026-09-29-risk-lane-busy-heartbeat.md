# The risk lane marks each fire BUSY on its heartbeat

- **Why:** step 1 of `docs/proposals/RISK_LANE_WATCHDOG_V0.1.md` (Thomas approved the deadlines,
  2026-09-29). The heartbeat was stamped once per pass, so a long valid fire and a dead loop looked
  the same: both stop stamping. And a stuck breaker or route watch read FRESH for five minutes,
  because the pass-age floor is 300 s and those fires normally take under four seconds.
- **What changed:**
  - `scheduler.RISK_FIRE_DEADLINE_SECONDS`: crypto_pipeline 600 s, breaker_watch 120 s,
    route_watch 120 s.
  - `run_due(fire_guard=…)`: a context manager entered around each executed fire. Its exit runs
    when the fire returns and when it raises.
  - The tick loop's guard is `heartbeat.busy_marker`. It writes `busy` (kind, schedule, run id,
    start, deadline) into the lane's heartbeat before the fire and clears it in `finally`.
    Best-effort both ways, like every heartbeat write.
  - `check_heartbeat`: with a mark, the deadline decides. Inside it: FRESH, however old the last
    pass. Past it: STALE (`OVERRUN`), however recent. A malformed mark is UNREADABLE. Without a
    mark, the old pass-age rule is unchanged.
- **Not marked:** maintenance kinds. They have no deadline, and the pass-age rule already notices a
  maintenance hang at 300 s; a generous deadline there would have noticed it later, not sooner.
- **Behavior change:** only what the healthcheck reports. Nothing is killed yet. The watchdog that
  ends a stuck fire is the next PR.
- **Tests:** marker lifecycle (clears after a raise, a write failure never stops the fire), the
  verdict both ways, `run_due` entering the guard around executed fires only, and the tick loop
  marking a risk fire on its own lane's heartbeat while a maintenance fire runs unmarked.
  Mutation: removing the `finally`, the verdict, the guard wrap, or the write-failure catch each
  fails a test.
