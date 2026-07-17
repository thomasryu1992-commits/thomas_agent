# Server Deployment (R4.5)

**Status:** Active (MVP runtime). **Normative authority:** None — `governance/GOVERNANCE_POLICY.yaml`
and `runtime/mvp_runtime/` remain authoritative; this describes how to run the operator service.

The MVP ships as a container that runs the operator control-channel loop
(`runtime/mvp_runtime/operator_cli.py`) as a long-lived service: poll Telegram → verify the
registered operator → run the single-agent pipeline → reply. The same emergency console
(`/pause` `/kill` `/resume` `/status`) governs the running service.

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
  for the hosted model, `brave_search` for real search, `workspace.writer` for real writes.
  Build each with `scripts/activate_safety_flag.py --provider-id ...`; the referenced
  `safety_flag_evidence/` file must be mounted too, since the gate verifies it exists.
  Granting one provider never grants another — a bare image still fails closed on every
  capability you did not explicitly activate.
- The `runtime_ledger/` and control state are created on first write.

Keep this state on a host directory (e.g. `/srv/thomas/state`) that maps to
`/app/.runtime_governance_state`. The Core activation/approval records additionally mount over
`/app/THOMAS_CORE/activations` and `/app/THOMAS_CORE/approvals`.

## Run

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
  thomas-agent-operator
```

Omit the `MVP_OPERATOR_CHANNEL` / `MVP_HOSTED_PROVIDER` env vars (and their secrets) to run the
network-free mock loop — a safe smoke test that touches no network and answers with the
deterministic mock analysis.

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

## Notes

- The base image is `python:3.12-slim` to match CI's Python 3.12.
- The service runs as a non-root user (uid 10001); the mounted state directory must be writable
  by that uid.
- Production runtime dependencies are pinned in `requirements-runtime.txt` (YAML + JSON Schema
  only); regenerate it in lockstep with `requirements-validation.lock`.
