# Server Deployment (R4.5 / L3b)

**Status:** Active (MVP runtime). **Normative authority:** None — `governance/GOVERNANCE_POLICY.yaml`
and `runtime/mvp_runtime/` remain authoritative; this describes how to run the deployed services.

The MVP deploys as **two services from one image**, sharing one mounted state volume
(`docker-compose.yml` at the repo root is the committed source of this topology):

- **operator** — the control-channel loop (`runtime/mvp_runtime/operator_cli.py`): poll
  Telegram → verify the registered operator → run the single-agent pipeline → reply. The same
  emergency console (`/pause` `/kill` `/resume` `/status`) governs the running service.
- **scheduler** — the tick loop (`runtime/mvp_runtime/scheduler_cli.py tick`): fires due
  schedules (scheduled analysis, the crypto pipeline cycle, the strategy factory, memory
  prune). **Without this service nothing scheduled ever runs** — the operator loop does not
  tick schedules. Run at most ONE scheduler per state volume (the deployment contract is a
  single tick process; the stores are cross-process locked, but parallel crypto workers are
  out of scope).

## What the image contains — and deliberately does not

The image carries **only committed, non-secret source** (runtime code, schemas, the approved
Core Release, role/registry contracts, the governance policy). It never contains:

- **Secrets** — the Telegram bot token and any model API key come from environment variables at
  runtime, read by name and never logged (see `providers.py` / `operator.py`).
- **Per-machine governance state** — the Core pointer, operator registration, safety-flag
  activation, control state, and the durable ledger live under `.runtime_governance_state/`,
  which is a **mounted volume**, and the local Core approval/activation records under
  `THOMAS_CORE/approvals|activations/`. The `.dockerignore` keeps all of these out of the build
  context.

This is what makes the Safety-Flag Gate meaningful in production: a freshly built image has no
activation record, so the real Telegram transport and the hosted model provider **fail closed**
(`ACTIVATION_MISSING`). Network capabilities turn on only when a valid, mounted activation
authorizes them — never because the image was built or an env var was set.

## Build

```bash
docker build -t thomas-agent-operator .
```

## Provision per-machine state (once per host)

The container starts without any state (it will idle, or fail closed the moment it needs Core
or a registration). To process real requests, provide the same local state a workstation uses
(see the "Core activation" and "Safety flags" sections of `CLAUDE.md`), then mount it:

- `operator_registration.json` — the single authorized operator (Telegram user id + private
  chat id). Without it the loop exits `REGISTRATION_MISSING`.
- `CURRENT_CORE_RELEASE.yaml` + the referenced `THOMAS_CORE/activations|approvals/` records —
  the active approved Core the pipeline binds each task to.
- `safety_flag_activations/<provider_id>.json` — **one grant per provider**, each required to
  enable that provider: `telegram` for the real control-channel transport, `google_ai_studio`
  for the hosted model, `tavily_search` (or `brave_search`) for real search, `workspace.writer`
  for real writes. Real search is selected with `MVP_SEARCH_TOOL=tavily_search` +
  `TAVILY_API_KEY`; a backend failure at run time degrades the run (audited), never blocks it.
  Build each with `scripts/activate_safety_flag.py --provider-id ...`; the referenced
  `safety_flag_evidence/` file must be mounted too, since the gate verifies it exists.
  Granting one provider never grants another — a bare image still fails closed on every
  capability you did not explicitly activate.
- The `runtime_ledger/` and control state are created on first write.

Keep this state on a host directory (e.g. `/srv/thomas/state`) that maps to
`/app/.runtime_governance_state`. The Core activation/approval records additionally mount over
`/app/THOMAS_CORE/activations` and `/app/THOMAS_CORE/approvals`.

## Run (compose — the deployed topology)

Secrets and per-host paths come from a gitignored `.env` next to `docker-compose.yml`
(compose reads it automatically). Typical `.env` for the current live host:

```text
MVP_OPERATOR_CHANNEL=telegram
TELEGRAM_BOT_TOKEN=...
MVP_HOSTED_PROVIDER=google_ai_studio,groq
GOOGLE_AI_STUDIO_API_KEY=...
GROQ_API_KEY=...
MVP_VALIDATOR_PROVIDER=groq
MVP_MARKET_DATA=binance_futures
MVP_PAPER_TRADING=real
# THOMAS_STATE_DIR=/srv/thomas/state          # defaults to ./.runtime_governance_state
```

```bash
docker compose up -d --build
docker compose ps            # both healthy: thomas-operator + thomas-scheduler
docker compose logs -f scheduler
```

With an empty `.env` both services run the network-free mock paths — a safe smoke test:
every env var alone fails closed without its mounted safety-flag grant, so a bare checkout
cannot open a network socket or write paper state. The crypto gates (`MVP_MARKET_DATA`,
`MVP_PAPER_TRADING`, `MVP_LIQUIDATION_FEED`) belong to the **scheduler** service; the
operator service never trades.

The compose operator runs with `--independent-validation auto` (review only
important/high-risk requests — the R7.1 policy).

### Run (plain docker — single service, e.g. a smoke test)

```bash
docker run -d --name thomas-operator \
  --restart unless-stopped \
  -e MVP_OPERATOR_CHANNEL=telegram \
  -e TELEGRAM_BOT_TOKEN="$TELEGRAM_BOT_TOKEN" \
  -e MVP_HOSTED_PROVIDER=google_ai_studio \
  -e GOOGLE_AI_STUDIO_API_KEY="$GOOGLE_AI_STUDIO_API_KEY" \
  -v /srv/thomas/state:/app/.runtime_governance_state \
  -v /srv/thomas/core/activations:/app/THOMAS_CORE/activations:ro \
  -v /srv/thomas/core/approvals:/app/THOMAS_CORE/approvals:ro \
  thomas-agent-runtime
```

Omit the `MVP_OPERATOR_CHANNEL` / `MVP_HOSTED_PROVIDER` env vars (and their secrets) to run the
network-free mock loop — a safe smoke test that touches no network and answers with the
deterministic mock analysis. Remember that a plain operator container runs NO schedules — the
scheduler service is what fires them.

`MVP_HOSTED_PROVIDER` also accepts an ordered failover chain
(`google_ai_studio,groq` — pass `GROQ_API_KEY` too). Every member needs its own
safety-flag activation on the mounted state volume; a chain with an unknown or
unauthorized member fails closed at startup rather than silently shrinking. The next
member is tried only when the previous one answers 503/429 even after its own retry.

R7.1: append `--independent-validation auto` to the container command to review only
important/high-risk requests (the operator marks one with a leading `!중요` /
`!important` token), and set `MVP_VALIDATOR_PROVIDER` (e.g. `groq`) to run the reviewer
on its own gated provider/quota — same grant rules as `MVP_HOSTED_PROVIDER`.

## Emergency controls on a running service

The operator console works two ways against the same mounted control state, so a `kill` from
either path halts the loop's next task immediately:

```bash
# From the host (works even if Telegram is unreachable):
docker exec thomas-operator python -m runtime.mvp_runtime.console_cli kill --reason "halt now"
docker exec thomas-operator python -m runtime.mvp_runtime.console_cli status
docker exec thomas-operator python -m runtime.mvp_runtime.console_cli resume --reason "cleared"

# Over Telegram: the registered operator texts /kill, /status, /resume, /pause, /stop <id>.
```

A `KILLED` state blocks all new/pending execution; only `/status` and audit reads remain, and
only the authenticated operator can `/resume`. A corrupt control file fails closed to `KILLED`.
`docker stop` halts the process; the mounted state (including any kill) survives a restart.

## Health, logs, shutdown

- **Healthcheck** (compose): each service's own **heartbeat**
  (`python -m runtime.mvp_runtime.heartbeat_cli operator|scheduler`). Each loop stamps
  `.runtime_governance_state/heartbeats/<service>.json` once per pass, and the probe fails
  when that stamp is older than the loop's own cadence allows — so a wedged poll or a tick
  hung on a provider call is finally visible. It replaced `console_cli status`, which only
  proved the control-state file parsed and therefore reported healthy through exactly those
  stalls. A KILLED runtime still passes: killed is *halted*, not *unhealthy*, its loop keeps
  turning, and resuming stays the operator's decision, never the orchestrator's.
  Check by hand with `docker compose exec scheduler python -m runtime.mvp_runtime.heartbeat_cli scheduler`.
- **Logs** rotate via the json-file driver (10 MB × 3 files per service).
- **Shutdown**: `docker compose stop` sends SIGTERM with a 30 s grace period — enough for an
  in-flight tick to finish its current fire; the claim-before-execute rule means a harder kill
  drops (never doubles) the in-flight occurrence, and since L3a a fire that fails inside a
  living process is recorded as a durable `failed` event.

## What CI enforces about this image

`.github/workflows/docker-image.yml` builds the image on ubuntu-latest — the same Linux
AMD64 target the service runs on — and smoke-tests the properties this document relies on,
all on a **bare image with no secrets and no provisioned state**:

- both compose services exist (an operator-only deploy runs no schedules);
- the scheduler ticks cleanly on an empty mounted volume, proving uid 10001 can write it;
- a provider env var **alone** refuses to open a network path (`ACTIVATION_MISSING`);
- the operator refuses to act with no registration (`REGISTRATION_MISSING`);
- a `kill` survives the container that issued it, and a corrupt control state reads as
  `KILLED`, never as "go";
- the scheduler service starts from its compose definition and answers the healthcheck.

So the fail-closed claims above are checked on every PR rather than trusted.

## Notes

- The base image is `python:3.12-slim` to match CI's Python 3.12.
- The services run as a non-root user (uid 10001); the mounted state directory must be writable
  by that uid.
- Production runtime dependencies are pinned in `requirements-runtime.txt` (YAML + JSON Schema
  only); regenerate it in lockstep with `requirements-validation.lock`.
