# Runbook — harness backup and restore (five roots), health and restart budgets

**Status:** in force since 2026-09-04 (PR4 of the Hermes integration sequence; Thomas decisions Q10 and Q15, 2026-09-03).
**Scope:** everything on this host that is state and not code, for both runtimes — Thomas Agent (8 services, uid 10001) and the assistant Hermes (1 container, uid 10000). Code is in git; images are rebuilt from it.
**Owner of the scripts:** `scripts/ops/harness_backup.sh` and `scripts/ops/rotate_hermes_mcp_log.sh` in this repository, **installed** to `/root/backups/` (the crontab path `/root/backups/backup-governance-state.sh` did not change). Reinstall after editing:

```bash
install -m 700 scripts/ops/harness_backup.sh /root/backups/backup-governance-state.sh
install -m 700 scripts/ops/rotate_hermes_mcp_log.sh /root/backups/rotate-hermes-mcp-log.sh
install -m 700 scripts/ops/backup_watch.sh /root/backups/backup-watch.sh
install -m 700 scripts/ops/health_watch.sh /root/backups/health-watch.sh
```

## 1. What is backed up

One archive a day, `govstate-<UTC stamp>.tar.gz` under `/root/backups/governance-state/` (mode 0600, keep 7), plus the weekly candle archive (unchanged since 2026-08-31). Member paths are prefixed with the host directory they restore into.

| Root (member prefix) | Owner on disk | What it is | Excluded |
|---|---|---|---|
| `thomas_agent/.runtime_governance_state` | 10001:10001, 0700 | approvals and permission decisions, the hash-chained audit ledger, `records.jsonl` + archive, `schedules.jsonl`, task registry, control state, crypto state (pool, snapshots, budget and risk-limit records), working memory, knowledge corpus | `crypto/candle_archive` (weekly archive instead); the five unix sockets |
| `thomas_agent/THOMAS_CORE/activations`, `…/approvals` | root | the Core activation pointer's targets — mounted read-only into five services; **were in no backup before 2026-09-04** | — |
| `thomas_agent/workspace` | 10001 | content-lane deliverables (`POST.md`, `PASTE.txt`, …) | — |
| `thomas_agent/.env` | root, 0600 | the single secret source (see `DEPLOYMENT.md` → *Secret boundary*) | — |
| `hermes-trial/data` | 10000:10000, 0700 | `SOUL.md`, `config.yaml`, `mcp/` shims, `skills/`, `cron/jobs.json`, `memories/`, `sessions/`, `auth.json`, and `state-snapshots/<stamp>-daily/` — a **consistent** `state.db` copy made by `hermes backup --quick` (sqlite backup API) moments before the tar | the live `state.db*`, `kanban.db*`, `cron/executions.db*` (WAL-mode databases copied mid-write are not backups — the snapshot directory carries them); `cache/`, `lazy-packages/`, `home/`, `bin/`, `.local/` (installed packages, recreated on boot); `logs/`, `sandboxes/`, `image_cache/`, `audio_cache/`, `models_dev_cache.json` |

Measured 2026-09-04: **25 MB, 4.8 s** (the previous Thomas-only archive was 15 MB). Off-host: the Mac pull (`com.thomas.govstate-pull`, daily 18:00 KST, 90-day retention) globs `govstate-*.tar.gz`, so it picked the new shape up without reinstalling.

The snapshot step runs inside the container as uid 10000 (`docker exec -u 10000 hermes hermes backup --quick -l daily`). If the container is down the tar still runs and the log line ends in `hermes-snapshot=FAILED` — a visible hole rather than a silent one. Only the newest snapshot directory is kept on disk (33 MB each); older ones live in older archives.

**Check the backup ran.** `backup_watch.sh` does this daily at 08:00Z, fifteen minutes after the core
job, and messages the operator's registered control chat when a check fails — cron itself is silent
and there is no MTA on this host, which is why four consecutive failures went unseen in 2026-09-04..07.
It checks three things: the newest core archive is younger than 26 h, the last `mode=core` line in
`backup.log` reads OK, and the newest candle archive is younger than 8 days. It says nothing when all
three pass, and appends one line per run to `watch.log` so its own silence stays distinguishable from
its absence. `--dry-run` prints the message instead of sending it. The control-bot token comes from
`.env` through a curl config on stdin (never argv, never a log) and the chat id from
`operator_registration.json` rather than a second copy of it. By hand:

```bash
tail -3 /root/backups/governance-state/backup.log          # OK mode=core govstate-… 25M kept=7 hermes-snapshot=ok
tar tzf /root/backups/governance-state/govstate-$(date -u +%Y%m%d)-0745.tar.gz | awk -F/ '{print $1"/"$2}' | sort | uniq -c
# expect thomas_agent/.runtime_governance_state, thomas_agent/THOMAS_CORE, thomas_agent/workspace, thomas_agent/.env,
#        hermes-trial/data
```

A member that is not on disk makes `tar` exit 2, and this script deletes the half-written archive
rather than pass it off as a backup — so one absent path costs the whole day. The script now checks
the six members first and logs `FAILED … missing=<path>`; a bare `rc=2` in the log predates that
check. This is not hypothetical: PR5 retired `/root/hermes-trial/docker-compose.yml` on 2026-09-04
while it was still a member, and the daily archive failed silently until 2026-09-06.

## 2. Restore

Ownership is the part people get wrong: three uids, none of them the one running the restore. Restore as root, then `chown` exactly as below. Archives dated **before 2026-09-04** have no `thomas_agent/` prefix — their members start at `.runtime_governance_state/`; extract them with `-C /root/thomas_agent`.

### 2.1 Thomas Agent state (`.runtime_governance_state`)

```bash
cd /root && docker compose -p thomas_agent --env-file /root/thomas_agent/.env -f <clean-main-worktree>/docker-compose.yml stop
tar xzf /root/backups/governance-state/govstate-<stamp>.tar.gz -C /root thomas_agent/.runtime_governance_state
tar xzf /root/backups/governance-state/govstate-candles-<stamp>.tar.gz -C /root      # the weekly archive: candles are only here
chown -R 10001:10001 /root/thomas_agent/.runtime_governance_state
docker compose -p thomas_agent --env-file /root/thomas_agent/.env -f <clean-main-worktree>/docker-compose.yml up -d
```

Since PR5 there is **one** compose project (`thomas_agent`, nine services), so this `stop` stops
Hermes as well — expected here, because 2.3 restores its data next. Always pass `-p thomas_agent`
and `-f <clean-main-worktree>/docker-compose.yml`: without `-p` the directory name becomes the
project and the running containers are left alone, and a working tree that is not on `main` carries
a compose file that does not match what is deployed.

The sockets under `bridge/` and `internal/` are not in the archive; the door and worker processes recreate them on start. The candle loss window is up to 7 days by decision (2026-08-31).

### 2.2 Core activation, workspace, the secret file

```bash
tar xzf govstate-<stamp>.tar.gz -C /root thomas_agent/THOMAS_CORE/activations thomas_agent/THOMAS_CORE/approvals   # stays root-owned
tar xzf govstate-<stamp>.tar.gz -C /root thomas_agent/workspace && chown -R 10001:10001 /root/thomas_agent/workspace
tar xzf govstate-<stamp>.tar.gz -C /root thomas_agent/.env && chown root:root /root/thomas_agent/.env && chmod 600 /root/thomas_agent/.env
```

### 2.3 Hermes

The archive holds the data directory **without** the live databases, and a snapshot directory **with** consistent copies of them. Put the copies where the live files go, and delete any WAL/shared-memory files next to them — a stale `-wal` beside a restored `state.db` corrupts it on first open.

```bash
docker compose -p thomas_agent --env-file /root/thomas_agent/.env -f <clean-main-worktree>/docker-compose.yml stop hermes
tar xzf /root/backups/governance-state/govstate-<stamp>.tar.gz -C /root hermes-trial/data
SNAP=$(ls -1d /root/hermes-trial/data/state-snapshots/*/ | sort | tail -1)
cp "$SNAP/state.db" /root/hermes-trial/data/state.db
cp "$SNAP/kanban.db" /root/hermes-trial/data/kanban.db                   # if present
cp "$SNAP/cron/executions.db" /root/hermes-trial/data/cron/executions.db   # if present
rm -f /root/hermes-trial/data/{state.db,kanban.db}-{wal,shm} /root/hermes-trial/data/cron/executions.db-{wal,shm}
chown -R 10000:10000 /root/hermes-trial/data
docker compose -p thomas_agent --env-file /root/thomas_agent/.env -f <clean-main-worktree>/docker-compose.yml up -d --wait hermes
```

The assistant's compose definition is not in the archive and must not be restored from one: since
PR5 it is the `hermes` service of this repository's `docker-compose.yml`, and the retired
`/root/hermes-trial/docker-compose.yml` (kept as `docker-compose.retired-pr5.yml`) defines a second,
standalone gateway — bringing it back gives the same data directory two writers. `hermes.env` is
retired too (PR2); the compose file reads its three values from `/root/thomas_agent/.env`, so restore 2.2's `.env` first. Session continuity: `gateway_routing` keys sessions by the absolute `/opt/data/sessions` path, so keep the mount path.

### 2.4 After any restore

```bash
docker ps --format '{{.Names}} {{.Status}}'                                  # 9 containers, healthy
for c in read switch dispatch knowledge; do docker exec thomas-$c-bridge printenv MVP_BRIDGE_CLIENT_UID; done   # 10000 ×4
ls -ln /root/thomas_agent/.runtime_governance_state/bridge                   # srw-rw---- 10001:10000 ×4
docker exec -i -u 10000 hermes /opt/hermes/.venv/bin/python - <<'PY'
import socket, json; s = socket.socket(socket.AF_UNIX); s.connect('/opt/bridge/read.sock')
s.sendall(b'{"command": "runtime_status"}\n'); print(json.loads(s.recv(65536).split(b'\n')[0])['ok'])
PY
```

## 3. Health and restart budgets (Q15)

**Hermes healthcheck** — the container's CMD is `sleep infinity` (an s6 slot), so a dead gateway leaves the container `running`; `gateway_state.json` changes only on transitions and is not a liveness signal. The compose healthcheck reads the age of `/opt/data/state/gateway.heartbeat`, which the gateway rewrites every 30 s: unhealthy when older than 120 s (interval 60 s, 3 retries, 180 s start period). Like every Thomas healthcheck, it **reports** — nothing on this host restarts a container on `unhealthy`; that is a separate decision. Since 2026-09-07 something at least *reads* it: see *Container health watch* below.

**Stop budget** — s6-overlay's default `S6_KILL_GRACETIME` is 3000 ms, so the compose `stop_grace_period: 30s` was never reached and the gateway's own `stop()` was SIGKILLed mid-way: 19 of 19 boots since 07-30 recorded `prior_exit=unclean` (no data loss thanks to WAL recovery). Now `S6_KILL_GRACETIME=25000` and `S6_SERVICES_GRACETIME=25000` in the compose environment, and `agent.restart_drain_timeout: 20` in `config.yaml` for in-flight turns. First restart under the new budget, 2026-09-04 03:13Z: `prior_exit=clean`. Observed: `docker restart` takes ~25 s wall-clock even though the gateway drains in ~1.2 s — s6 waits the full grace for something that does not exit on TERM; within the 30 s compose grace, and not yet investigated.

**Log rotation** — Hermes rotates `agent.log` / `errors.log` / `gateway.log` itself; `mcp-stderr.log` (MCP server stderr, appended by `tools/mcp_tool.py`) it does not — 10.7 MB unrotated on 2026-09-04. `rotate_hermes_mcp_log.sh` copy-truncates it above 10 MB, keeps 3, daily 07:40Z from cron (`logrotate` would need a passwd entry for uid 10000, which the host does not have). Thomas services log to stderr only; Docker's `json-file` 10m×3 is their only store and is lost on recreation — `docker logs <c> > file` before a recreate if the tail matters.

### Container health watch

`health_watch.sh`, installed to `/root/backups/health-watch.sh`, every 10 minutes from cron. Until it
existed, the nine healthchecks reported to nobody: `restart: unless-stopped` does not act on
`unhealthy`, and a wedged poll was visible in `docker ps` and nowhere else.

| condition | how it is judged |
|---|---|
| missing / not running | against the roster in the script, which `tests/test_ops_health_watch.py` pins to `docker-compose.yml` |
| `unhealthy` | docker's own verdict — three failed probes at a 60 s interval, so ~3 minutes |
| stuck `starting` | `starting` is normal for the first 15–180 s after a deploy; past 15 minutes the probe has never passed |
| crash loop | `RestartCount` rose on the *same* container id. A manual `docker restart` does not touch that counter and `up -d` resets it with a new id |
| OOM kill | `.State.OOMKilled` — three services carry `mem_limit` and this host runs with ~1.5 GiB free |

Two rules keep it from becoming noise, which is how a watch dies. A state must be **seen twice**
before it is sent — counted per problem, and not required to be consecutive: an intermittent probe
reads healthy on half the samples, and demanding consecutive sightings never confirmed it at all. A
key survives two clean runs before it is forgotten, so a deploy (all nine recreated in about a
minute, rarely caught by even one ten-minute check) does not accumulate toward an alert. Crash loops
and OOM kills skip the wait: they are events, not states, and a deploy does not produce them.

And it speaks on the **edge**: once a problem set is reported it stays quiet until the set changes or
clears. Recovery is announced only when the problem has aged out of memory, so a flapping container
gets one ⚠️ and one ✅ rather than a pair every twenty minutes; an event key (a crash loop) has no
recovery at all, because "it has not crashed again in the last ten minutes" is not good news worth a
message.

The daemon is asked one question first (`docker version`), and every docker call has a deadline: a
wedged daemon — the case this watch exists for — must not be the case that hangs it. A lock file
means a stuck run is reported rather than joined by nine more.

```bash
/root/backups/health-watch.sh --dry-run          # what it would send, no state written
tail -3 /root/backups/health-watch.log           # OK services=9 · PENDING keys=1 confirmed=0 · send=200
touch /root/backups/health-watch.silence         # planned work: mutes sending for 6 h, then lapses
```

A suppressed alert is *held*, not swallowed — when the silence lapses the problem is still reported.
The same is true of a failed send: it is recorded with its HTTP code and retried on the next run,
because one network blip must not bury an outage until the problem set happens to change.

**What it does not see.** It reports docker's verdict, and docker's verdict is only as deep as each
service's probe. Four of the nine read a heartbeat (`heartbeat_cli` for operator and the two lanes;
the age of `gateway.heartbeat` for Hermes), so a lane that keeps stamping while dropping its cycles
still passes. The other five test that a socket file exists (`test -S`), which a wedged process
holding an open listener passes too. A green watch means the containers are up, not that the work is
moving — the ledger and `scheduler_events` answer that question, not this.

And nothing watches the watcher: if cron loses the line, `health-watch.log` simply stops growing.
Two operational notes: putting `DOCKER_BIN=` in the cron line is not needed and, if it points
anywhere but the real docker, the send is refused as a test; and the state file is a plain
`key=value` list — editing it by hand is fine, but a non-numeric counter is ignored rather than
obeyed.

**Verify the budgets are live:**

```bash
docker inspect hermes --format '{{.State.Health.Status}} {{json .Config.Healthcheck.Test}}'
docker exec hermes sh -c 'ps -o args | grep shutdownd' | grep -o '\-g [0-9]*'        # -g 25000
grep restart_drain_timeout /root/hermes-trial/data/config.yaml
tail -1 /root/hermes-trial/data/logs/container-boot.log | grep -o 'prior_exit=[a-z]*' # clean after any stop
crontab -l | grep backups/                            # 07:40 rotate, 07:45 core, 08:00 backup watch, */10 health, Sun 08:15 candles
/root/backups/backup-watch.sh --dry-run                        # OK — 백업 최신 …, or the message it would send
```

## 4. What is still not covered

- `docker logs` of the Thomas services (rotation only, no archive).
- Hermes `logs/` and `sandboxes/` by decision — not state.
- The Mac pull verifies the tar (`tar tzf`) but does not test-restore; a restore rehearsal on a scratch directory is the missing drill.
- Acting on `unhealthy`. `health_watch.sh` (§3) reports it now, but nothing restarts a container on it — still a separate decision.
- Work that stops inside a healthy container. The healthchecks read heartbeats, so a lane that keeps stamping while dropping cycles passes both the probe and the watch.
- The watchers' own liveness. Nothing checks that `watch.log` or `health-watch.log` is still growing, so a watch that dies is as quiet as the failure it was meant to catch. (The backup watch does not retry a failed send either — its next run is 24 h out; the health watch does, every 10 minutes.) Read them:
  ```bash
  tail -3 /root/backups/governance-state/watch.log      # one line per day: OK checks=3
  tail -3 /root/backups/health-watch.log                # one line per 10 min: OK services=9
  ```
