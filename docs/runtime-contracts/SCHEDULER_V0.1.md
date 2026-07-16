# Scheduler (R6) — v0.1

**Status:** Active (MVP runtime). **Normative authority:** None — `governance/GOVERNANCE_POLICY.yaml`
and `runtime/mvp_runtime/` remain authoritative; this describes the runtime behavior. This is the
thin **live** MVP scheduler and is distinct from the deferred, review-only
[`DISABLED_SCHEDULER_INTERFACE_V0.1`](DISABLED_SCHEDULER_INTERFACE_V0.1.md) (`scheduler_plan_review.v0.1`),
which is not activated here.

The scheduler runs a stored task **template** on a fixed interval cadence. A template is a request
string (`analysis_task`) or a maintenance action (`memory_prune`) — **never a shell command**.

## Kinds

| Kind | Effect |
|---|---|
| `analysis_task` | Run `request` through the full pipeline (`run_task`) as a scheduler-initiated task (`channel=scheduler`, `requester_type=scheduler`) — same intake, planning, permission, budget, and audit as an operator request. |
| `memory_prune` | Run working-memory retention (delete expired candidates, audited) — the periodic driver for R5 §12.4 retention. |

## Safety and governance

- **Kill-switch bound** (`kill_switch.kill_blocks: scheduler_execution`): before each fire the
  runtime control state (R4) is checked. While **PAUSED or KILLED**, a due schedule is **skipped,
  never run**, recorded as a skip, and its `next_run_at` advances — a kill **drops** the occurrence
  rather than queueing a burst for resume.
- **Overlap-safe:** the MVP is single-process and `run_due` executes due schedules sequentially,
  each at most once per tick, so a schedule cannot overlap itself. Cross-process concurrency
  control is a later increment.
- The scheduler **grants no new authority** — each scheduled task passes the full pipeline gates.
- Creating a schedule is `EXECUTE_AND_REPORT` (autonomous internal task creation, §4.2) and is
  recorded to the ledger; every fire and every kill-skip is recorded too (`scheduler_events.jsonl`).
- **Interval cadence only** (min 60s) in v0.1; cron-style calendaring is future.

## State (local, per-machine, gitignored)

Schedules live in `.runtime_governance_state/schedules.jsonl` (like the ledger, control state, and
working memory). A corrupt store fails closed (`SCHEDULES_UNREADABLE`).

## Running it

```bash
# Manage:
python -m runtime.mvp_runtime.scheduler_cli add --kind analysis_task \
    --request "이 사업 아이디어를 분석해줘: ..." --interval-seconds 3600
python -m runtime.mvp_runtime.scheduler_cli add --kind memory_prune --interval-seconds 86400
python -m runtime.mvp_runtime.scheduler_cli list
python -m runtime.mvp_runtime.scheduler_cli disable <schedule_id>
python -m runtime.mvp_runtime.scheduler_cli remove <schedule_id>

# Tick loop (respects the kill switch; one tick by default, or continuous):
python -m runtime.mvp_runtime.scheduler_cli tick --max-ticks 0 --interval-seconds 60
```

The tick loop selects a provider/search tool through the Safety-Flag Gate exactly like the
operator loop, so a scheduled `analysis_task` uses the deterministic mock provider unless a real
provider is locally activated.

## Key modules

- `runtime/mvp_runtime/scheduler.py` — `Schedule` / `ScheduleStore`, `build_schedule`, `run_due`
  (kill-switch-bound, overlap-safe, audited).
- `runtime/mvp_runtime/scheduler_cli.py` — manage + tick loop entrypoint.

## Not yet implemented

Cron-style cadence, cross-process concurrency/overlap locks, catch-up policy for missed runs, and
per-schedule budgets are later increments.
