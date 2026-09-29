# Risk-lane watchdog: the design comes before the code

- **Why a design first:** Thomas chose restart on a watchdog timeout (2026-09-29). Ending the
  process that runs the loss breaker and the route watch is a production-operations change, and
  `os._exit` can land mid-write, so the deadline values, the evidence path and the torn-write
  exposure are written down for review before any code.
- **What the record says:** `docs/proposals/RISK_LANE_WATCHDOG_V0.1.md` — measured fire durations
  (crypto_pipeline max 122.5 s over 851 fires; breaker 3.2 s; route 0.8 s), per-kind deadlines
  600/120/120 s, a BUSY field on the lane heartbeat so a long valid fire never reads like a dead
  lane, a Python watchdog thread with a faulthandler backstop that dumps stacks, writes a
  diagnostic file and exits 70 for Docker's `restart: unless-stopped` to relaunch, and recovery
  through the existing `abandoned_mid_run` reconciliation plus one operator alert from the fresh
  process. No new governance record or schema.
- **Found while measuring:** `restart: unless-stopped` never acts on `unhealthy` (runbook §3), so
  today a stuck risk fire is recovered only by a person after the host health watch reports it.
- **Not in this change:** code. Two implementation PRs follow review (BUSY heartbeat, then the
  watchdog).
