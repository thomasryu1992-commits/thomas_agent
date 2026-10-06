# The deploy preflight stops while a scheduled fire is running, and the promote waits for a factory fire about to start

- **What happened (2026-10-05):** a concurrent session deployed #1123 at about 08:12 UTC. `compose up`
  recreated the scheduler lanes while the DOGEUSDT 1h `crypto_factory` fire (`e15f3982`) was 15 s in.
  The next process's startup scan wrote `abandoned_mid_run` at 08:12:59. The schedule's `next_run_at` had
  already advanced at claim, so that day's generation for it was never minted. Nothing else was lost,
  but any fire, including a risk-lane cycle, dies the same way.
- **The check (`scripts/ops/deploy_preflight.py`, `fires`):**
  - **A fire in flight stops both runs.** This means a `started` scheduler event from the last 15
    minutes with no `fired`/`failed`/`abandoned` under its `schedule_run_id`, which is the scheduler's
    own pairing (`scheduler.find_abandoned_runs`). The 15 minutes is the factory child's timeout. An
    older unpaired start is a dead run the next restart closes, not one a deploy can kill.
  - **A factory fire about to start.** An enabled `crypto_factory` schedule whose `next_run_at` is
    within 10 minutes, or overdue and waiting its turn, stops the `--promote` run and warns on the
    first one.
  - **Unreadable state files** warn rather than pass.
- **Replayed against the live ledger:** events up to 2026-10-05T08:12:50Z give `STOP` naming
  `crypto_factory e15f3982 since 08:12:44Z`. Runs at 08:12:30 and 08:13:30 catch the fires running
  then, and 08:40 passes. The schedule half cannot be replayed, because `schedules.jsonl` keeps only
  current state.
- **Also changed:** the deploy skill's step 6 says what a `fires` STOP means and to wait for the
  fire's terminal event.
- **Not changed:** the scheduler. Retrying an abandoned factory occurrence the same day would be a
  scheduler behaviour change. This PR only keeps a deploy from causing one.
