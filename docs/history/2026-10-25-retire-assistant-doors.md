# The assistant's four doors, the workflow manager and the knowledge lane are retired; policy 1.7.0

- **Why:** Thomas retired Hermes on 2026-10-25 (`2026-10-25-retire-hermes-container.md`) and chose to remove
  what it used in the same round: the four doors (`read` / `switch` / `dispatch` / `knowledge`) and the workflow
  path. With the client gone, those were code and services that nothing calls. Operator control stays on the
  control bot.
- **What left:**
  - **Services:** `read-bridge`, `dispatch-bridge`, `switch-bridge`, `knowledge-bridge`. Five remain:
    operator, the two scheduler lanes, pipeline-worker and claude-read-bridge.
  - **Modules:** `switch_bridge`, `dispatch_bridge`, `knowledge_bridge` (with their CLIs), `workflow`,
    `workflow_store`, `workflow_manager`, `workflow_console`, `workflow_cli`, `bridge_idempotency`,
    `dispatch_spend`. Also the `workflow_plan.v0.1` / `workflow_event.v0.1` schemas. The CI smoke step that
    drove the dispatch door leaves in its own branch (`ci/drop-dispatch-door-smoke`), merged first: the agent's
    token cannot write workflow files.
  - **The knowledge lane, whole** (`runtime/mvp_runtime/knowledge/`). Its door was its only entry, so it goes
    the way `predmarket/` went. Its PDF dependencies left the image too: `poppler-utils` and `pypdf`.
  - **Scheduler kinds:** `workflow_plan` and `dispatch_spend_watch`. A stored row of either is refused by name at
    its fire (`UNKNOWN_KIND`), the way the `pm_scan` rows were.
  - **Operator:** the approval mirror (the operator no longer holds `HERMES_BOT_TOKEN`) and the workflow event
    push. The announcer keeps the execution-stage and emergency-close asks.
  - **Asks:** the switch door's two (`trading_switch:` / `runtime_resume:`) and the workflow step's, along with
    their permission builders and their render branches in `approval.py`.
  - **Pipeline worker:** the assistant profile, the attempt frame and the registry rows it opened. The
    scheduler is the one caller now, and a frame naming no profile is refused (`ACTOR_PROFILE_NOT_PERMITTED`).
    The four request kinds moved into the worker.
  - **Policy 1.7.0** (`docs/runtime-contracts/POLICY_1_7_0_DRAFT.md`): the dispatch gate, the dispatch,
    switch, mirror and schedule clauses.
- **What stays, on purpose:**
  - **`control_channel.assistant_read`.** Claude's read door serves its policy-gated reads under it.
  - **`stop_ref`**, moved to `control.py`. The emergency-close ask binds it, and the ids are unchanged:
    `test_stop_ref_kept_its_ids_when_it_moved_out_of_the_switch_door` pins two that the door's copy computed.
    So an ask minted before the deploy spends after it.
  - **The emergency-close request path.** `scripts.emergency_close --request` (Thomas's terminal) never used
    the switch door.
  - **`schedule_delegation`.** The container CLI's own changes go through `apply_change(bounded=False)`. Its
    bounded half (the door's delegation) has no caller now; trimming it is a separate cleanup.
  - **`task_registry`'s `AGENT` / `WORKFLOW` origins and the `assistant_bridge` actor name**, so the rows
    already written still read.
- **Checked:**
  - Both policy validators pass on the applied tree.
  - The 1.7.0 bump's `--check` was READY before `--apply`, but against an empty approval store: it says
    nothing about the host's store.
  - Full suite and release gate: see the PR.
- **Host steps (Thomas, root), in this order:**
  1. **Before the deploy.**
     - Zero outstanding approvals: run the predicate in `POLICY_1_7_0_DRAFT.md` §3.
     - `scheduler_cli list`, then `remove` any `workflow_plan` or `dispatch_spend_watch` row.
  2. **Deploy** (the deploy skill), then remove the four orphaned door containers. `up -d` leaves them
     running: use `--remove-orphans`, or `docker rm -f` by name.
  3. **Reinstall** `health_watch.sh`, `harness_backup.sh` and `backup_watch.sh` to `/root/backups/` together.
     The new backup no longer writes the workflow marker, and the old watch would flag its absence.
  4. **REBIND** the execution stage: the policy version and the safety fingerprint both moved.
  5. **Update the C6a deploy runner's tree.** `RUNTIME_CONTAINERS` names the four remaining runtime containers
     now.
  6. **State on disk is Thomas's** to keep or delete: `.runtime_governance_state/bridge/`,
     `.runtime_governance_state/workflow/`, and the knowledge store.
