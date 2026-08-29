# Server Deployment (R4.5 / L3b)

**Status:** Active (MVP runtime). **Normative authority:** None — `governance/GOVERNANCE_POLICY.yaml`
and `runtime/mvp_runtime/` remain authoritative; this describes how to run the deployed services.

The MVP deploys as **eight services from one image**, sharing one mounted state volume
(`docker-compose.yml` at the repo root is the committed source of this topology). The three
loop services are described here; the **pipeline-worker** (the execution half of the dispatch
door, holding the model/search/Naver credentials) and the **four assistant doors**
(`read`/`switch`/`dispatch`/`knowledge` bridges, holding no credentials at all) have their own
sections further down:

- **operator** — the control-channel loop (`runtime/mvp_runtime/operator_cli.py`): poll
  Telegram → verify the registered operator → run the single-agent pipeline → reply. The same
  emergency console (`/pause` `/kill` `/resume` `/status`) governs the running service.
- **scheduler** — the RISK-lane tick loop (`scheduler_cli.py tick --lane risk`): fires the
  money path's kinds (crypto pipeline cycle, breaker watch, route watch) and nothing else.
  This is the service that holds the venue keys; the lane split kept it in place.
- **scheduler-maint** — the MAINTENANCE-lane tick loop (`tick --lane maintenance`): factory,
  null_control, candle archive, scheduled analysis, prune, rotate, report, propose, data
  review — the long fires, in a process the risk lane never waits on
  (`docs/proposals/SCHEDULER_LANE_SPLIT_V0.1.md`). Holds no venue, live, or model credential.

  **Without its lane's service nothing that lane schedules ever runs** — the operator loop
  does not tick schedules. Run at most ONE tick service PER LANE per state volume (the
  stores are cross-process locked and the lanes' kind sets are disjoint, but parallel
  crypto workers are out of scope).

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

This is what makes the Safety-Flag Gate meaningful in production: a freshly built image carries
no opt-ins, so the real Telegram transport and the hosted model provider stay **inert** until
the operator names them in the deploy `.env`. Since 2026-08-10 (Thomas) the environment IS the
gate — the per-machine grant records and their 30-day renewal are retired — so setting a
capability's opt-in var is the deliberate governance step, and revoking it means unsetting the
var and restarting the service. An unset or unrecognized value still selects the inert default.

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
- Capability opt-ins in the `.env` — since 2026-08-10 the environment is the gate, one var
  per capability: `MVP_OPERATOR_CHANNEL=telegram` for the real control-channel transport,
  `MVP_HOSTED_PROVIDER=...` for the model chain, `MVP_SEARCH_TOOL=tavily_search` (+
  `TAVILY_API_KEY`) for real search, `MVP_WORKSPACE_WRITER=real` for real writes, and the
  crypto opt-ins: `MVP_MARKET_DATA=binance_futures`,
  `MVP_ACCOUNT_FEED=binance_futures_account`, `MVP_LIQUIDATION_FEED=coinalyze_market_data`,
  `MVP_PAPER_TRADING=real`, `MVP_CANDLE_ARCHIVE=hyperliquid`, `MVP_LIVE_TRADING=real` (the
  live one needs its confirmation phrases too — see the live section). A search-backend failure at
  run time degrades the run (audited), never blocks it. Naming one capability never names
  another — a bare image stays inert on every capability you did not explicitly opt in.
  Leftover `safety_flag_activations/*.json` grant files from before 2026-08-10 are inert:
  they grant nothing and block nothing, and you can delete them.
- The `runtime_ledger/` and control state are created on first write.

Keep this state on a host directory (e.g. `/srv/thomas/state`) that maps to
`/app/.runtime_governance_state`. The Core activation/approval records additionally mount over
`/app/THOMAS_CORE/activations` and `/app/THOMAS_CORE/approvals`.

## Run (compose — the one deployment path)

**Compose is the only way this is deployed. Do not `docker run` the services and do not
build private `thomas-agent-operator:<tag>` images to deploy from.** The services own
the fixed names `thomas-operator`, `thomas-scheduler`, and `thomas-scheduler-maint`; a
hand-run container claiming
either name blocks the next `docker compose up` with a name conflict, and two people
deploying by different routes on one host means each redeploy silently replaces the
other's containers. One host, one deploy procedure — the candidate-tag procedure in the
root `CLAUDE.md` (its authority; this is the summary), run from a **clean `origin/main`
worktree**, never `--build` from the primary checkout. The primary checkout on a busy day
sits on another session's branch: measured 2026-08-29, a redeploy from a stale branch
checkout silently reverted the merged assurance flags (#769) for five hours — the drift
check in the assurance section below is what caught it.

```bash
docker tag thomas-agent-runtime:latest thomas-agent-runtime:rollback-pre-<PR#>   # BEFORE building
git -C /root/thomas_agent worktree add /root/thomas-deploy-<PR#> origin/main --detach
docker build -t thomas-agent-runtime:candidate-<PR#> /root/thomas-deploy-<PR#>
docker run --rm --entrypoint python thomas-agent-runtime:candidate-<PR#> -c "<assert the fix>"
docker tag thomas-agent-runtime:candidate-<PR#> thomas-agent-runtime:latest      # re-check running==latest first
docker compose -p thomas_agent --env-file /root/thomas_agent/.env \
  -f /root/thomas-deploy-<PR#>/docker-compose.yml up -d
```

The mounted state volume is untouched, so no history is lost. If a name conflict is reported, an out-of-band container exists —
`docker rm -f thomas-operator thomas-scheduler thomas-scheduler-maint` and re-run compose (their state is on the
bind mount, not in the container). Confirm the commit you are on is a superset of whatever
the running image carried before removing anything.

Secrets and per-host paths come from a gitignored `.env` next to `docker-compose.yml`
(compose reads it automatically). Typical `.env` for the current live host:

```text
MVP_OPERATOR_CHANNEL=telegram
TELEGRAM_BOT_TOKEN=...
# OPTIONAL. The scheduler's outbound notifications only, on a DIFFERENT bot from the one the
# operator loop polls. Set it when operator Telegram lives with the assistant (Hermes) and this
# runtime kept the original bot: without it the scheduler's notifications are delivered
# successfully into a conversation nobody reads any more. Safe only because the scheduler never
# calls getUpdates — never set this on the operator service, which does poll, or the two
# pollers steal each other's messages. Unset falls back to TELEGRAM_BOT_TOKEN.
SCHEDULER_TELEGRAM_BOT_TOKEN=...
MVP_HOSTED_PROVIDER=openrouter,google_ai_studio,groq
OPENROUTER_API_KEY=...
GOOGLE_AI_STUDIO_API_KEY=...
GROQ_API_KEY=...
MVP_VALIDATOR_PROVIDER=groq
MVP_MARKET_DATA=binance_futures
MVP_PAPER_TRADING=real
# THOMAS_STATE_DIR=/srv/thomas/state          # defaults to ./.runtime_governance_state
```

## Conversational mode (F2, optional)

Unset, the Telegram channel behaves exactly as it always has: plain text is a task
submission and `/verbs` are deterministic. Set, an **unmarked** plain-text message becomes a
conversation turn instead — the front desk asks clarifying questions, translates requests
into queued tasks, and answers "what's running?" with the same data `/tasks` prints.
`/verbs` and `!중요`-marked requests always stay deterministic.

```text
MVP_FRONTDESK_PROVIDER=groq
```

Three things must line up, and each fails closed loudly rather than degrading quietly:

1. the variable reaches the **operator** service (it is in that service's `environment:`
   block — the scheduler holds no conversation and must not get it);
2. every chain member it names is a known provider (an unknown or duplicate member fails the
   whole selection closed — the environment is the gate since 2026-08-10);
3. `conversation.frontdesk` is **active** in `03_ROLE_CONTRACTS/ROLE_REGISTRY.yaml`, with a
   matching definition hash — the env var is a request, the registry entry is the grant.

Miss (2) or (3) and the operator service refuses at startup (`FRONTDESK_ROLE_INACTIVE`,
`FRONTDESK_ROLE_HASH_MISMATCH`, or the gate's own refusal) instead of running a
conversation nobody authorized. `docker compose logs operator` prints `FRONTDESK:
conversational mode ON (model: …)` when it is genuinely on.

At run time a provider outage **degrades** rather than blocks: `FRONTDESK_DEGRADED` is
audited and the raw message continues to the task queue exactly as if the feature were off,
so no message is lost to a model failure.

`MVP_HOSTED_PROVIDER` also accepts `openrouter` — one OpenAI-compatible gateway to many
vendors' models — as a chain member (e.g. `MVP_HOSTED_PROVIDER=openrouter,groq`, OpenRouter
primary with Groq as the 503/429 failover). It needs its own `openrouter` mounted grant like
any provider, `OPENROUTER_API_KEY`, and `MVP_OPENROUTER_MODEL` naming the exact model slug (a
gateway fronts hundreds, so the runtime must be told which; an unset/stale slug is a 4xx):

```text
MVP_HOSTED_PROVIDER=openrouter,groq
OPENROUTER_API_KEY=...
MVP_OPENROUTER_MODEL=openai/gpt-oss-20b:free
```

```bash
# deploy: candidate-tag procedure above
docker compose ps            # all eight thomas-* services healthy
docker compose logs -f scheduler
```

With an empty `.env` the services run the network-free mock paths — a safe smoke test:
every env var alone fails closed without its mounted safety-flag grant, so a bare checkout
cannot open a network socket or write paper state. The crypto gates (`MVP_MARKET_DATA`,
`MVP_PAPER_TRADING`, `MVP_LIQUIDATION_FEED`) belong to the **scheduler** service; the
operator service never trades.

The compose operator runs with `--independent-validation auto` (review only
important/high-risk requests — the R7.1 policy). To change that, edit the operator
service's `command:` in `docker-compose.yml` — not a `docker run` flag.

`MVP_HOSTED_PROVIDER` also accepts an ordered failover chain (`openrouter,google_ai_studio,groq`
— put every member's API key in the `.env` too). The environment is the gate (2026-08-10):
naming the chain IS the authorization, and a chain with an unknown or duplicate member fails
closed at startup rather than silently shrinking. The next member is tried only when the previous
one answers 503/429 even after its own retry. Set `MVP_VALIDATOR_PROVIDER` (e.g. `groq`)
to run the R7.1 reviewer on its own gated provider/quota — same chain rules. **These env
vars belong to the scheduler service too** (both `environment:` blocks list them); a key
present for the operator but missing for the scheduler is the failure mode where scheduled
work silently degrades while interactive work is fine.

### Throwaway smoke test (NOT a deployment)

This is the *only* sanctioned `docker run`, and it exists solely to boot the image once and
watch it fail closed. It **must** use a throwaway name (`thomas-smoke` below) and its own
scratch volume so it can never collide with — or share state with — the deployed services.
It is never how the operator or scheduler is run for real; that is always compose, above.

```bash
docker run --rm --name thomas-smoke \
  -v thomas-smoke-state:/app/.runtime_governance_state \
  thomas-agent-runtime \
  python -m runtime.mvp_runtime.heartbeat_cli operator || echo "expected: fails closed with no state"
```

With no provisioned state and no secrets the image runs the network-free mock loop and fails
closed on every gated capability — which is the whole point of the smoke test. `--rm` removes
the container on exit; the named scratch volume is disposable (`docker volume rm
thomas-smoke-state`). Never point a `docker run` at the real state bind or the real service
names — that is the collision this section exists to prevent.

## Live trading env — forwarded to the scheduler (changed 2026-07-27)

**This section reversed on 2026-07-27 (Thomas decision).** The live-trading and account
variables are now forwarded from the compose `.env` to the **scheduler** service, so the
readiness board and the account read answer there without an operator exporting them per
session — in particular `daily_loss_breaker`, which needs the venue's realized figure because
the local outcome ledger cannot supply one.

The argument that used to sit here is kept, because it did not become wrong — it became a cost
that was accepted: a live order is supposed to happen when a human types the command, and
credentials in a long-running service outlive the intent that set them and return on every
restart (`restart: unless-stopped`), readable by anything that can inspect the container. It is
also **one Binance key** — the order credentials are derived from the account ones below — so
`account.py` being read-only *by construction* is a property of this code, not of a key that
carries futures-trading permission at the venue. A genuinely read-only venue key is what would
make the account read separable from the trading capability.

**The operator service still receives none of it**, and that half did not reverse: the operator
loop reads no market or account data, so a key that can trade buys it nothing.

> **This section said "Env alone opens nothing" until 2026-07-28. It no longer does.** Two
> changes landed that day and both cut the same way: cycle routing shipped, so a *scheduled*
> run reaches the order path (through exactly one module, `crypto/live_route.py`); and Thomas
> removed the per-machine `live_trading` grant, making `MVP_LIVE_TRADING=real` the entire gate.
> **The `.env` file described below is now, by itself, the difference between a scheduler that
> trades paper and one that trades real money.** Treat it accordingly.

What still stands between the scheduler and an autonomous order: only one module may reach the
order path (`test_the_cycle_reaches_the_live_order_path_through_exactly_one_module`), the
registered budget, the canary evidence, the confirmation phrase for the capability being
exercised, both kill switches, and the loss breaker.

So the variables below belong in the compose `.env` (the scheduler reads them from there), and
you can still export them in a shell for a one-off run against the host checkout.

**Both halves are enforced** (2026-07-27): `tests/test_deployment_env_passthrough.py` fails if
the **scheduler** stops receiving any of the eight, if the **operator** starts receiving any of
them, or if either service declares `env_file:` — which would forward the whole file and slip
past a per-variable check.

```bash
# --- live trading (canary session) ------------------------------------------
# ONE Binance API key: Futures trading ON, withdrawals and internal transfer OFF,
# IP-whitelisted. Withdrawal permission is the one that matters: every risk control
# in this runtime caps ORDER size (60/order, 120 open, 20 daily loss, 200 ceiling),
# and none of them governs a withdrawal, because no code path here calls one. Leaving
# it on converts a bounded loss into an unbounded one and buys no capability.
export BINANCE_ACCOUNT_API_KEY='...'
export BINANCE_ACCOUNT_API_SECRET='...'
export MVP_ACCOUNT_FEED=binance_futures_account

# The order-capable key. Kept as its OWN variables even when the same key fills both,
# so splitting them into two keys later is an edit here and nothing else.
export MVP_LIVE_ORDER_API_KEY="$BINANCE_ACCOUNT_API_KEY"
export MVP_LIVE_ORDER_API_SECRET="$BINANCE_ACCOUNT_API_SECRET"

# THE switch, and since 2026-07-28 the only one. This line alone selects the real
# order adapter, the real P&L ledger, the real position book, the real daily counter
# and the real canary registry. There is no second factor behind it any more.
export MVP_LIVE_TRADING=real

# One phrase per capability. Export ONLY the one you are exercising — the canary phrase
# cannot authorize autonomous trading and the autonomous phrase cannot authorize a canary.
export MVP_LIVE_CANARY_CONFIRMATION=I_UNDERSTAND_THIS_PLACES_A_REAL_LIVE_MAINNET_ORDER
# export MVP_LIVE_CONFIRMATION=I_UNDERSTAND_THIS_TRADES_LIVE_FUNDS_AUTONOMOUSLY

# Operator halt. Set it to refuse every live ENTRY; closes stay permitted.
# export MVP_LIVE_MANUAL_KILL_SWITCH=true
```

**The caps are not here.** `MVP_LIVE_MAX_*` no longer authorizes anything — the per-order,
daily-count, exposure and loss limits come from the registered `live_trading_budget.v0.1` record
(`scripts/register_live_trading_budget.py`). There is deliberately no cap an operator can set
outside that record.

**The C4 breaker limits are a second, separate record.** Daily/weekly R loss, consecutive losses,
drawdown and risk-per-trade are *not* budget caps — they gate paper and live alike — so they live
in `crypto_risk_limits.v0.1` (`scripts/register_crypto_risk_limits.py`). **Registering one is
optional and usually unnecessary:** with nothing registered the guard judges on the `guards.py`
defaults, which is the supported steady state. Two things to know before registering one:

- It carries a validity window, and **a lapsed record refuses new positions rather than reverting
  to the defaults** — reverting would silently loosen a breaker an operator had tightened. To go
  back to the defaults, delete the file; do not let it lapse.
- A limit outside the bounds in `guards.py` is refused, never clamped, and tightening is
  unbounded. Widening a breaker past those bounds is a code change and a Thomas decision.

```bash
python -m scripts.register_crypto_risk_limits --show   # read-only: what the guard judges on now
```

**No grant is required** — since 2026-08-10 (Thomas) the account feed, like every capability,
opens on its env opt-in alone: `MVP_ACCOUNT_FEED=binance_futures_account` plus the two key vars
above. Any old activation files (`live_trading` from before 2026-07-28,
`binance_futures_account` or others from before 2026-08-10) are inert: they grant nothing and
block nothing, and you can delete them.

Verify before spending anything — both commands are read-only and place no order:

```bash
python -m runtime.mvp_runtime.crypto.account          # the balance the caps are judged against
python -m runtime.mvp_runtime.crypto.live_readiness   # every gate, computed
```

`canary_evidence 0/3` staying FAIL on that board is **expected**: it is the one check a canary is
exempt from, and the canary is what earns it. Everything else must be PASS. The full operator
sequence is `docs/runtime-contracts/CRYPTO_LIVE_EXECUTION_V0.1.md`, Gates 2 and 3.

**When you are done trading:** remove `MVP_LIVE_TRADING` from the compose `.env` and restart the
scheduler. There is no grant file to delete any more, and that is the cost of the 2026-07-28
change: the gate no longer expires on its own, so nothing turns this off but you.

**To halt a scheduler that is trading right now, do not clear `MVP_LIVE_TRADING`.** It takes
effect only on the next start, and because the close guard also requires the opt-in it would
strand every open position. Use the runtime kill below — it writes control state, lands on the
running service at its next guard, and the close path is deliberately exempt from it.

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

## The four assistant doors — check the boundary, no test can

`read` / `switch` / `dispatch` / `knowledge` serve an assistant that lives in **another
repository** (`/root/hermes-trial`). Two properties of that boundary are enforced by
configuration rather than by code in this repo, so nothing here fails when they drift. Check them
whenever either side is redeployed.

**1. Each door admits one uid, and it is the assistant's gateway.**

```bash
for c in thomas-read-bridge thomas-switch-bridge thomas-dispatch-bridge thomas-knowledge-bridge; do
  printf '%-26s ' "$c"; docker logs "$c" 2>&1 | grep -o 'uids=[^)]*' | tail -1
done
```

Expect `uids=[10000]` on all four. `uids=unrestricted (group only)` means
`MVP_BRIDGE_CLIENT_UID` is unset, and the doors are back to checking the gid alone — every peer,
including a root shell on this host, is then recorded as actor `assistant_bridge` and the ledger
cannot tell them apart. uid 0 is deliberately **not** in the allowlist: `docker exec hermes`
defaults to root, so it cannot distinguish the host operator from the assistant's own container.
Host-side checks go through the scheduler CLI (above), not through these doors — expect
`PEER_NOT_PERMITTED` if you point a root shell at a door socket, and that is correct.

**2. The assistant's container reaches this host by more than the doors.**

```bash
docker inspect hermes --format '{{json .HostConfig.Binds}} {{json .HostConfig.GroupAdd}}'
```

Since 2026-07-31 that container mounts `/var/run/docker.sock` and carries the host `docker`
group, which makes it equivalent to host root — it can `docker exec` into `thomas-scheduler` and
read the live-order keys the door processes are kept away from. **This is retained by Thomas's
decision (2026-08-22), not an oversight**, and it is recorded here because the door modules'
guarantees (P3 ceiling, closed kind set, no money path) do not apply to that path. If the intent
ever changes, removing the **mount** is what closes it: the container's `stage2-hook.sh` re-adds
the group on every boot by inspecting the socket, so removing `group_add` alone leaves the path
open. That correction is recorded in the Hermes-side compose header.

## Assurance policies — both ON since 2026-08-24

Two policies cost a model call per task. They default OFF in the code and are turned on by a
line on a service's compose `command` — a deploy with a diff, not a behaviour that arrives with
a rebuild. **Both are now on, on both services**, and the committed compose says so; the table
records what each buys and what it costs, because turning one back off is the same one-line
deploy in reverse. That is also why the drift check below is not optional: on 2026-08-29 a
redeploy from a stale branch checkout reverted both flags for five hours with every container
healthy, and the check's `validation=automatic-only, revise=off` output was the only signal —
run it after every deploy.

| flag | service | what it adds | cost |
|---|---|---|---|
| `--independent-validation auto` | `operator`, `pipeline-worker` | R7's second reviewer; the stricter verdict decides delivery | one specialist call on ORANGE/RED-risk work |
| `--revise` | `operator`, `pipeline-worker` | M3's single bounded regeneration on a REVISE verdict (hard cap 1) | one specialist call, only on runs that draw a REVISE |

`auto` and not `always` on both: it is what the operator loop had been running since R7, and it
spends the extra call where the risk is rather than on every request. Raising either service to
`always` is the same one-line change.

**What this closed.** `pipeline-worker` had neither policy until this change, so every task the
dispatch door forwarded — fifteen on record, including every blog draft — ran with AUTOMATIC
checks only while the operator loop beside it had a second reviewer. And `--revise` had never
run in any deployed path: seven REVISE verdicts are on record and all seven ended
`FINAL_BLOCKED`, waiting for someone to ask again. A reviewer that can say "not good enough"
with nothing able to act on that verdict is half a loop.

**Reading the flags back off a running service** — the worker states both in its startup line,
so a service that quietly lost a flag is visible without reading compose:

```bash
docker logs thomas-pipeline-worker 2>&1 | grep -o 'validation=[^,]*, revise=[^,]*'
```

Expect `validation=auto, revise=on`. `validation=automatic-only, revise=off` means the compose
`command` in front of the running container is not this one.

## The memory prune schedule — registered 2026-08-24

`schedule_7c8b7423b5ceeac10b9e`, every 86400s, on the maintenance lane. Registered after the
review pass below, and only after the duplicate check shipped — before that, pruning was a
counter reset.

```bash
docker exec thomas-scheduler-maint python -m runtime.mvp_runtime.scheduler_cli \
    add --kind memory_prune --interval-seconds 86400
```

Module form and inside the container, per CLAUDE.md — a root run on the host leaves files the
service cannot rewrite. No restart: the tick loop picks the row up within 30s. `add` sets the
first fire to `now + interval`, so a freshly registered prune does **not** run immediately; there
is one full interval in which `disable` still costs nothing.

**Pruning is not reversible, and the audit does not preserve what it deleted.**
`build_retention_event` records `removed_count` and the `candidate_id` list — not the content. An
expired candidate is also still promotable right up until it is deleted (`promote_candidate`
checks status, not expiry). Snapshot the rows first if any of them might be wanted.

**The backlog was mostly one fact, written many times.** Reviewed 2026-08-24: 316 rows, **129
distinct texts**, five of them repeated **38 times each** for 170 of the rows — almost all of it
the same five-axis business-plan conclusion re-derived per analysis run. The store had no
duplicate check, so pruning it would have reset a counter and nothing else. The check now lives
in `WorkingMemoryStore.append` (against rows still live, on the `task_working_memory` /
`CANDIDATE` rung only), which is what makes this schedule worth registering: retention now
empties a store that stopped refilling.

**Two claims recorded here on 2026-08-23 were wrong, and checking them is why the schedule was
finally registered.** Both are corrected rather than deleted, because each is the kind of thing a
later reader would otherwise re-derive from the same misreading:

- **"A handful of the expired rows are real."** The Naver blog-SEO findings among them were
  fact-checked on 2026-08-24 and **none survived** — the reward figure described a programme that
  had already launched under different criteria, the dwell-time metrics were never published by
  Naver, and the search-volume band had no primary source at all. Nothing in the backlog needed
  promoting before the prune.
- **"The classifier files conversation as knowledge."** It does not. The front-desk session turns
  are stored with `candidate_type=frontdesk_session_context` and `scope=frontdesk_session`, which
  is correct and is exactly why the duplicate check leaves them alone. The earlier claim came from
  reading `promote_memory_candidate --list`, whose output does not show the type. Of the 308
  `reusable_knowledge` rows, one is non-analysis text; that is a model answering an input that
  was not a business idea, not a leak.

## Sending feedback on a completed run

`/feedback good|bad <note>` on the control channel binds to the last delivered run. `bad` with a
note also mints a correction candidate. Deployed since R-E1 and used **zero** times — the
mechanism is not the gap, the habit is. Nothing else in the runtime records that an answer was
wrong, so a run nobody comments on is a run the system will repeat.

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
- a typo'd provider chain refuses **outright** (`UNKNOWN_PROVIDER`) instead of silently
  shrinking — and a bare image with no opt-ins stays inert (the empty-tick step);
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
