# The assistant's container (Hermes) is retired; the control bot is the one window

- **Why:** Thomas decided on 2026-10-25 to remove Hermes because it costs too much to run. Operator control
  stays on Telegram through the control bot (`/status /approve /reject /pause /kill /resume /halt_trading
  /stop /feedback …`), which never depended on Hermes: approvals were always decided there, and the Hermes
  window only ever showed a copy.
- **What left:**
  - The `hermes` service in `docker-compose.yml`. `test_the_retired_assistant_is_not_a_service` now pins
    its absence; the three tests that pinned its boundary went with it.
  - `integrations/hermes/` (shims, SOUL, `thomas-ops` skill, templates, manifest) and `tests/hermes_shims/`.
  - `scripts/ops/install_hermes_shims.sh` and `scripts/ops/rotate_hermes_mcp_log.sh`.
  - `hermes` from the `health_watch.sh` roster.
  - `hermes-trial/data` from the core backup, along with the `hermes backup --quick` snapshot. An absent
    member fails the whole archive, so the backup would have broken the day that directory was deleted.
- **What stays, on purpose:**
  - **`HERMES_BOT_TOKEN`.** The two scheduler lanes and the operator's approval mirror send on it.
    `hermes-notify.sh` (the Mac routine, the weekly report, the `at` checks) uses it too. With no poller
    left it is a send-only notification window. Renaming it would touch every sender for no gain.
  - **The four doors and the workflow path.** `read` / `dispatch` / `switch` / `knowledge` and the workflow
    manager stay in this PR. They run with no client: they admit uid 10000, which nothing holds now. Their
    removal is the next PR, because the code is threaded into live modules:
    - `crypto/live_route.py` takes `stop_ref` from the switch door for the emergency-close binding.
    - The scheduler has a `workflow_plan` schedule kind.
    - The operator pushes workflow events.
    - The pipeline worker validates attempt frames.
  - **`claude-read-bridge`**, which shares `read_bridge.py` but is Claude's own door.
- **Host steps (Thomas, root):**
  1. Copy the newest core archive aside before reinstalling the backup script. The seven kept archives
     are the only copies of the Hermes data that will exist.
  2. Deploy this PR. `up -d` alone leaves the `hermes` container running as an orphan, so stop and
     remove it explicitly.
  3. Reinstall `harness_backup.sh` to `/root/backups/` and delete the `rotate-hermes-mcp-log` cron line.
  4. `HERMES_TELEGRAM_ALLOWED_USERS` in `.env` is read by nothing now and may go. `/root/hermes-trial`
     is Thomas's to keep or delete.
- **Not done here:** the history docs (`HERMES_ORCHESTRATOR_ARCHITECTURE_V0.1/V0.2`, the deployment plans,
  the policy drafts) keep their text and gain a retirement note where a reader starts.
  `GOVERNANCE_POLICY.yaml`'s assistant clauses change with the doors, in the next PR.
