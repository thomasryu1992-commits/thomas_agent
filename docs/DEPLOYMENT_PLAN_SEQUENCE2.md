# Deployment plan — sequence 2 (Hermes orchestrator, workflows) — for Thomas's decision

**Status:** PREPARED, NOT EXECUTED (2026-09-14). Nothing below has been run on the host; the running
image is still the pre-sequence-2 build (checkout `d5de6fa`), the Hermes shims on the host are 2.2,
`/root/backups/*.sh` are the pre-P10 copies, and the compose command for `dispatch-bridge` carries no
flag. **Executing this plan is a separate, explicit decision** (plan §8 P11; `CLAUDE.md`: a passing
test is never an approval for the next capability, and Claude does not deploy).
**Owner:** Thomas. **Authority:** none — `CLAUDE.md`'s candidate-tag procedure, `docs/DEPLOYMENT.md`
and the runbooks named here are the authority for how.

## 1. What is being deployed, and what is not

| Component | From | To | Carried by |
|---|---|---|---|
| Thomas runtime image | checkout `d5de6fa` (running) | `origin/main` after #865 (P00–P10 + the independent-review fixes) | candidate-tag build (§3) |
| `dispatch-bridge` compose volumes | state only | + the two read-only Core mounts (#865 — without them no gated-step ask can be minted) | comes with the compose file of §4 |
| `dispatch-bridge` compose command | no flag | `--workflow-manager` | a one-line PR, then `up -d` (§5) |
| Hermes shims (`/root/hermes-trial/data/mcp/`) | 2.2 | 2.10 (`integrations/hermes/MANIFEST.yaml`) | `scripts/ops/install_hermes_shims.sh --install` + `docker restart hermes` (§6) |
| Hermes prompt `SOUL.md` and skill `thomas-ops` | skill 1.4.1 on the host | skill 1.5.5 | **by hand** — the installer copies the five shims only (§6) |
| Hermes cron jobs | 3 jobs | 4 (adds 워크플로 서술, every 30 min) | by hand from `config/cron-jobs.template.json` (§6) |
| Backup scripts (`/root/backups/`) | pre-P10 | `scripts/ops/harness_backup.sh`, `backup_watch.sh` | copy (§6) |

**Not in this deploy, by decision:** policy 1.6.0 (`POLICY_1_6_0_DRAFT.md` — schedule delegation stays
dormant until Thomas applies it; the door refuses every change by name), `--v2-intake closed` (the
cutover of the assistant's single dispatches comes after the pilot, §7), any change to the money path
(no crypto kind is scheduled by the assistant, no financial setting moves, Risk Scheduler untouched),
and the read door's verb set (unchanged since 1.5.0).

## 2. Compatible combination (the record a deploy compares against)

`integrations/hermes/MANIFEST.yaml` at `origin/main`: door API proto 2 + v3 workflow commands; shims
2.10; skill 1.5.5; workflow store schema 3; task registry v0.4; policy 1.5.0. A v2-only shim against
this runtime keeps working (A18); a 2.10 shim against the running (old) image renders
`WORKFLOW_UNAVAILABLE` / `PROTO` refusals by name and falls back to nothing (A18) — so the order is
**runtime first, then the manager flag, then everything on the Hermes side** (V0.2 §4.2 "서버 선배포,
shim 후전환"). Nothing on the Hermes side is touched until §4 has promoted the runtime.

## 3. Build and verify the candidate (no restart yet)

```bash
docker inspect thomas-scheduler --format '{{.Image}}'; docker images thomas-agent-runtime   # running == latest? re-read the host
docker tag thomas-agent-runtime:latest thomas-agent-runtime:rollback-pre-863                  # BEFORE building
git -C /root/thomas_agent fetch origin main
git -C /root/thomas_agent worktree add /root/thomas-deploy-863 origin/main --detach
docker build -t thomas-agent-runtime:candidate-863 /root/thomas-deploy-863
# assert what the fix DOES: the door serves v3 and refuses v3 without the flag; the store has schema 3.
# `-i` is load-bearing: without it the heredoc never reaches python, which exits 0 having checked nothing.
docker run -i --rm --entrypoint python thomas-agent-runtime:candidate-863 - <<'PY'
from runtime.mvp_runtime import dispatch_bridge, workflow_store
assert {"workflow.submit", "schedule.propose_change", "workflow.report_usage"} <= dispatch_bridge.V3_COMMANDS
assert workflow_store.SCHEMA_VERSION == 3
from runtime.mvp_runtime.control import ControlStore
import tempfile, pathlib
root = pathlib.Path(tempfile.mkdtemp())
try:
    dispatch_bridge.apply_dispatch({"command": "capabilities"}, control_store=ControlStore(root), workflow_store=None)
except Exception as exc:
    raise SystemExit(f"capabilities must answer without the manager: {exc}")
from runtime.mvp_runtime.workflow_store import WorkflowStore
from runtime.mvp_runtime.errors import WorkflowBlocked
store = WorkflowStore(pathlib.Path(tempfile.mkdtemp()))                  # the review fixes are in (#865): a gate is one-way
gated = {"schema_version": "workflow_plan.v0.1", "goal": "g", "budget": {"max_model_calls": 2},
         "steps": [{"id": "a", "capability": "analysis", "request": "x", "reason": "r", "requires_approval": True}]}
wid = store.submit(principal="p", request_id="r", plan=gated, now="2026-09-14T00:00:00Z").workflow_id
ungated = {**gated, "steps": [{"id": "a", "capability": "analysis", "request": "x", "reason": "r"}]}
try:
    store.propose_update(wid, expected_version=store.status_view(wid, now="2026-09-14T00:00:00Z")["row_version"],
                         plan=ungated, reason="r", now="2026-09-14T00:00:00Z")
    raise SystemExit("a gate could be removed: this image predates #865")
except WorkflowBlocked as exc:
    assert exc.reason_code == "PLAN_CONFLICT"
print("candidate ok")
PY
# the command must print "candidate ok"; no output is a failed check, not a pass
```

## 4. Promote and restart (the one atomic step)

```bash
docker inspect thomas-scheduler --format '{{.Image}}'; docker images thomas-agent-runtime   # re-read: another session may have deployed
docker tag thomas-agent-runtime:candidate-863 thomas-agent-runtime:latest
docker compose -p thomas_agent --env-file /root/thomas_agent/.env -f /root/thomas-deploy-863/docker-compose.yml up -d
```

If the re-read shows another session's deploy, stop here: nothing on the host has been changed yet
except the candidate and rollback tags.

## 5. The manager flag

A one-line PR adding `--workflow-manager` to the `dispatch-bridge` command in `docker-compose.yml` (CI
already proves that combination serves v3 — the override in `.github/ci-compose.workflow.yml` — and
that the risk lane survives the door's stop and restart), merged and applied with the same `up -d`
from a fresh worktree. Until that line lands the deployed door refuses every v3 command by name.

## 6. The Hermes side and the host scripts (only after §4 and §5)

```bash
cd /root/thomas-deploy-863
scripts/ops/install_hermes_shims.sh --check     # the five MCP shims only
scripts/ops/install_hermes_shims.sh --install   # copies with .bak-<stamp> backups; never restarts
# prompt and skill: NOT handled by the installer (integrations/hermes/README.md) — copy by hand, keep a backup
cp -p /root/hermes-trial/data/SOUL.md /root/hermes-trial/data/SOUL.md.bak-$(date -u +%Y%m%d-%H%M)
cp -rp /root/hermes-trial/data/skills/thomas-ops /root/hermes-trial/data/skills/thomas-ops.bak-$(date -u +%Y%m%d-%H%M)
cp integrations/hermes/config/SOUL.md /root/hermes-trial/data/SOUL.md
cp -r integrations/hermes/config/skills/thomas-ops/. /root/hermes-trial/data/skills/thomas-ops/
chown -R 10000:10000 /root/hermes-trial/data/SOUL.md /root/hermes-trial/data/skills/thomas-ops
# the fourth cron job: add the 워크플로 서술 entry from integrations/hermes/config/cron-jobs.template.json
# to /root/hermes-trial/data/cron/jobs.json (uid 10000), deliver target = the existing one
docker restart hermes                           # the gateway reads shims, prompt and skill at start
cp scripts/ops/harness_backup.sh /root/backups/backup-governance-state.sh
cp scripts/ops/backup_watch.sh   /root/backups/backup-watch.sh
```

Done means: `install_hermes_shims.sh --check` reports no drift **and** `diff -r integrations/hermes/config/skills/thomas-ops
/root/hermes-trial/data/skills/thomas-ops` and `diff integrations/hermes/config/SOUL.md /root/hermes-trial/data/SOUL.md`
are empty (the installer's check covers neither) **and** hermes restarted healthy. The prompt and skill
are a governance change the decision record names (V0.2 §1.1), not a deploy detail — they go in with
Thomas's decision to deploy, not by themselves. Rolling this side back is the `.bak-<stamp>` copies,
the previous `jobs.json` and another `docker restart hermes`.

## 7. Post-deploy checks, the pilot, and the cutover

```bash
docker ps --format '{{.Names}} {{.Status}}'                                               # 9 healthy
for c in read switch dispatch knowledge; do docker exec thomas-$c-bridge printenv MVP_BRIDGE_CLIENT_UID; done
docker exec -i -u 10000 hermes /opt/hermes/.venv/bin/python - <<'PY'                     # v3 served? (-i: the heredoc reaches python)
import socket, json; s = socket.socket(socket.AF_UNIX); s.connect('/opt/bridge/dispatch.sock')
s.sendall(b'{"command": "capabilities", "proto": 2}\n'); print(json.loads(s.recv(65536).split(b'\n')[0])['data'])
PY
docker exec thomas-dispatch-bridge python -m runtime.mvp_runtime.workflow_cli list          # "no workflows" on day one
tail -2 /root/backups/governance-state/backup.log                                           # next morning: workflow-snapshot=absent|ok
```
plus the V0.2 §7 revalidation commands (A31) on the deployed tree.

**Pilot (plan §"운영 전환"):** limited general requests only — the assistant submits one-step and
small multi-step plans (analysis/research/translation/content) on Thomas's ask; the operator pushes
their endings to the control chat; `report_workflow_usage` at the end of each. Measure for a week:
accepted / completed / failed, WAITING_REPLAN decisions taken, push deliveries CONFIRMED vs
UNCERTAIN, the backup marker. No schedule delegation (policy 1.6.0 not applied), no financial path.

**Cutover of the assistant's single dispatches** (`RUNBOOK_WORKFLOW_CUTOVER.md` §2) only after the
pilot: `--v2-intake closed`, drain, done. Rollback at any point is §3 of that runbook.

## 8. Rollback evidence

- Image: `rollback-pre-863` tag + `docker compose up -d` from the previous worktree (`CLAUDE.md`).
- Shims, prompt, skill: the `.bak-<stamp>` copies of §6; the 2.2 shims against the new runtime keep working (A18).
- Workflow data after new work: preserved and readable with the flag off; a later manager finishes it
  (A25, `tests/test_mvp_runtime_workflow_migration.py`); never restore over it.
- Backup: the copy in `workflow/snapshots/<stamp>/`, verified by `workflow_cli verify` (A24).

## 9. Acceptance evidence at preparation time

Full suite under an ephemeral Core: 6290 passed / 2 skipped (P10 tree), 6329 / 2 after the independent-review
fixes (#865); `run_repository_release_gate.py --full --check-only`: PASS (P09 tree). An independent review of
P07–P11 (15 reviewers) raised 69 findings; the confirmed ones are fixed in #865 and on this PR. In-process load (A27): 100 accepts, 0 lost, 0 duplicated, overload
refused by name (`tests/test_mvp_runtime_workflow_load.py`). CI compose step (A26/A28): the door serves v3
with the manager, the risk lane survives its stop, the door answers again within 120 s. Acceptance
matrix: `/root/thomas_refactor_plan_2026-09-14/acceptance-matrix.csv`.
