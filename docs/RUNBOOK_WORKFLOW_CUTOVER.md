# Runbook — workflow cutover, rollback, and the workflow store's backup (sequence 2, P10)

**Scope:** the general-work entry points (the assistant's single dispatches first; the CLI, the
operator's general requests and the maintenance lane's analysis tasks afterwards, one at a time),
moving from the synchronous v2 dispatch to workflows (door API v3). **Not in scope:** the risk
scheduler's order state, the approval store, live trading — none of them move (plan §7, 이전 B).
**Rehearsed by:** `tests/test_mvp_runtime_workflow_migration.py` (A22, A25) and
`tests/test_mvp_runtime_workflow_backup.py` (A24), in isolated roots with no network and no keys.
**Authority:** none. This is procedure; the code and the tests named here are what holds.

A cutover is a **configuration change, not a data migration**: no legacy record is copied into
SQLite and no new record is written back in the legacy shape (V0.2 Q20). What changes is a flag
on the dispatch bridge and the assistant's tools.

## 1. Preconditions

- The runtime that carries P05–P10 is deployed by the candidate-tag procedure (`CLAUDE.md`), and
  `docker exec -i -u 10000 hermes …` against the dispatch door answers `capabilities` with
  `workflow_manager: true` — the compose command for `dispatch-bridge` carries
  `--workflow-manager`. Until that flag is on the door refuses every v3 command by name
  (`WORKFLOW_UNAVAILABLE`) and nothing below applies.
- The Hermes shims are installed at 2.9 or later (`scripts/ops/install_hermes_shims.sh --check`),
  so the assistant has `submit_workflow` and renders `V2_INTAKE_CLOSED` as a cutover, not an outage.
- A fresh backup with `workflow-snapshot=ok` (or `=absent`, if no workflow was ever accepted)
  in `backup.log` — §4 below.

## 2. Cutover of one entry point (이전 B)

Order of entry points: the assistant's single dispatches → the CLI's general requests → the
operator's general requests → the maintenance lane's `analysis_task` schedules. Each step is the
same three moves; do one entry point per day and watch a full cycle before the next.

1. **Close the entry point's new intake.** For the assistant's dispatches this is the door:
   add `--v2-intake closed` to the `dispatch-bridge` command in `docker-compose.yml` (or set
   `MVP_DISPATCH_V2_INTAKE=closed` in its environment) and `docker compose up -d dispatch-bridge`.
   From that moment a new `analyze` / `research` / `translate` / `draft_content` is refused as
   `V2_INTAKE_CLOSED` — nothing is started, nothing is claimed — while `submit_workflow`, the reads,
   and **the replay of any request_id accepted before the close** keep working. The door says which
   state it is in: `thomas_capabilities` → `v2_intake: closed`.
2. **Drain.** Wait until nothing is in flight on either path:
   ```bash
   docker exec thomas-dispatch-bridge python -m runtime.mvp_runtime.workflow_cli drain
   ```
   `drained: true` means no RUNNING legacy row (by origin) and no running workflow attempt. A
   legacy run closes itself when the worker answers (or is reconciled `RUN_ABANDONED` at the
   worker's next start); a workflow attempt lands or lapses at its lease (660 s). Do not proceed
   on `false`; do not "help" a run finish.
3. **Switch the client.** For the assistant: the SOUL/skill already say to use `submit_workflow`
   when `v2_intake` is closed (§7), so the switch is the flag itself; for the CLI and the operator
   the switch is the corresponding flag on their service (P11 names them per entry point). Then
   verify: one new request through the new path only, `/tasks` shows its `WORKFLOW` rows and no
   new `AGENT` row, and no id appears twice (`treg_…`, `task_…`, `wf_…` are disjoint namespaces).

**What "legacy writer 호출 0" means after the switch:** the door's forward to the worker is never
called for that entry point again (the rehearsal asserts the executor is never invoked and the
registry's AGENT row count does not move), and the only rows written are the manager's `WORKFLOW`
attempt rows.

## 3. Rollback

**A — before any new-format work was accepted** (`workflow_cli list` says `no workflows`, or
every workflow predates the cutover): remove `--v2-intake closed` (and `--workflow-manager` if the
rollback is of the manager itself), `docker compose up -d dispatch-bridge`, and point the shims
back at the previous revision with `install_hermes_shims.sh`. The v2 path works exactly as before;
no store is created by a door opened without the manager (the rehearsal asserts it).

**B — after new-format work exists:** stop new intake (§2 step 1 in reverse is NOT the move —
keep `--v2-intake closed` if the legacy path is what is being retired), let the manager drain
(`drain` → `true`) **or** keep a compatible release running that can read and finish what was
accepted. The store is never overwritten by a restore from before the cutover: that loses the
work in between, which is not a rollback (plan §7, 롤백 B). What is preserved, and the rehearsal
proves: with the flag off the door refuses v3 by name and serves v2; the store stays on disk and
readable (`workflow_cli list / inspect`, `WorkflowStore(readonly=True)`); a later manager start
recovers every RUNNING attempt from the worker's rows (P06) and finishes what was pending. Do not
assume an older image understands a newer schema: `workflow_cli verify --snapshot …` prints the
schema version a copy was written under; the runtime's own is `workflow_store.SCHEMA_VERSION`.

## 4. The workflow store's backup and restore (V0.2 Q28; A24)

`workflow.db` is a WAL-mode SQLite file and **is not tarred** — a live WAL database copied
mid-write is not a backup. `scripts/ops/harness_backup.sh core` (installed at
`/root/backups/backup-governance-state.sh`) makes a consistent copy first:

```
docker exec -u 10001 thomas-dispatch-bridge python -m runtime.mvp_runtime.workflow_cli snapshot \
    --dest /app/.runtime_governance_state/workflow/snapshots/<stamp>
```

then tars `.runtime_governance_state/` **with** `workflow/snapshots/<stamp>/` (the copy and its
manifest) and **without** `workflow/workflow.db*`, keeps only the newest snapshot directory, and
writes `workflow-snapshot=ok | absent | FAILED` on the log line (`absent` = the manager never
created the store here; not a failure). `backup_watch.sh` reads that marker daily and messages the
control chat on `FAILED` or on a line without it (an old backup script). Re-install both scripts
to `/root/backups/` when this runtime is deployed (P11) — until then the installed copies are the
pre-P10 ones and the watch's fifth check is not live.

**Restore** (an addition to `RUNBOOK_HARNESS_BACKUP_RESTORE.md` §2.1, run after its `tar xzf`):

```bash
SNAP=$(ls -1d /root/thomas_agent/.runtime_governance_state/workflow/snapshots/*/ | sort | tail -1)
docker run --rm --user 10001 -v /root/thomas_agent/.runtime_governance_state:/app/.runtime_governance_state \
    --entrypoint python thomas-agent-runtime:latest -m runtime.mvp_runtime.workflow_cli verify --snapshot "$SNAP"/workflow-*.db
cp "$SNAP"/workflow-*.db /root/thomas_agent/.runtime_governance_state/workflow/workflow.db
rm -f /root/thomas_agent/.runtime_governance_state/workflow/workflow.db-{wal,shm}
chown -R 10001:10001 /root/thomas_agent/.runtime_governance_state/workflow
```

`verify` opens the copy alone (never the live store) and prints its integrity verdict, schema
version, highest event cursor, row counts and whether the manifest beside it agrees; a non-zero exit
means do not restore that copy. A stale `-wal`/`-shm` beside a restored file corrupts it on first
open — the `rm` is not optional. After the restore the manager's startup reconciles any attempt the
copy shows RUNNING against the registry (P06), so nothing is re-run that the worker delivered.

**Rehearsal (isolated, no network, no keys):** `tests/test_mvp_runtime_workflow_backup.py` snapshots
under concurrent writes, verifies the copies, restores one into a fresh root and proves the results,
the cursor and the schema survived and that the restored store still runs a workflow to completion.
