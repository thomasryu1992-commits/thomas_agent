# Hermes integration — the assistant's side of the doors, under version control

Sequence 2, **P02** (2026-09-14). Decision record: [`docs/HERMES_ORCHESTRATOR_ARCHITECTURE_V0.2.md`](../../docs/HERMES_ORCHESTRATOR_ARCHITECTURE_V0.2.md) §5.2. Until this directory existed the four MCP shims, the door client, the operational prompt and the `thomas-ops` skill lived only on the deployment host (`/root/hermes-trial/data/`, uid 10000, backed up daily but reproducible from nothing), with a trail of `.bak-pre-*` copies as their only history.

**The rule from here on:** this directory is the source of truth; the host copy is what runs. Edit here, review in a PR, then install. A host edit that is not reflected here is drift, and `scripts/ops/install_hermes_shims.sh --check` says so.

## Layout

| Path | What it is | Runs where |
|---|---|---|
| `mcp/thomas_door_client.py` | the one socket client for the four doors (door API v2 frames, one receive loop) | inside the `hermes` container, imported by the four shims |
| `mcp/read_bridge_mcp.py`, `dispatch_bridge_mcp.py`, `switch_bridge_mcp.py`, `knowledge_bridge_mcp.py` | the stdio MCP shims the gateway spawns (`config.yaml` → `mcp_servers`) | inside the `hermes` container as `/opt/data/mcp/<shim>.py` |
| `config/SOUL.md` | the operational prompt (Korean) | Hermes system prompt |
| `config/skills/thomas-ops/` | the `thomas-ops` skill (`SKILL.md` 1.5.5) and its references | Hermes skill |
| `config/config.template.yaml` | `config.yaml` with the operator's Telegram ids replaced by placeholders; no secret was ever in that file | template — copy, fill the two ids |
| `config/cron-jobs.template.json` | the four cron jobs' configuration fields (schedule, prompt, model, toolsets) — the fourth, 워크플로 서술, is P08's polling narration (install it when the runtime runs the workflow manager); run state omitted; delivery target a placeholder | template |
| `MANIFEST.yaml` | which image, shim revision, prompt version and runtime were measured together | the record a deploy compares against |

Not here, on purpose: the container environment (three values from `.env`, see `docs/DEPLOYMENT.md`), sessions, memories, `state.db` and its snapshots, `auth.json`, logs and caches, and the `hermes-agent` build context (its own repository).

## What the read shim adds beside the console text

Five reads — `trading_readiness`, `current_funds`, `heartbeat`, `approval_status`, `runtime_status` — append the reply's structured `data` as one `[data]` JSON line (capped at 4,000 characters, named when truncated). For readiness the runtime's view keeps `infrastructure_ready`, `live_armed_strategies.armed`, `recorded_gate.stale` and `live_entry_possible` apart, and names what refuses an entry and what cannot be seen from the reader's container in `readiness.blocking` and `readiness.unknown` (2.12, crypto PR5a); for funds it carries `as_of`, `age_seconds` and `stale` with the figures. Reads whose data is large or already legible as text (`schedules`, `task_list`, boards) stay text-only. An older runtime answers `{action}` for the two crypto reads and the line still renders.

## Door API v3 in the dispatch shim (2.4)

`thomas_capabilities`, `submit_workflow`, `workflow_status`, `workflow_list`, `workflow_events`, `cancel_workflow` speak the workflow commands of P05 (#857). They need a runtime with P05 **and** a dispatch bridge started with `--workflow-manager`; any other door answers `WORKFLOW_UNAVAILABLE` and the shim says so instead of running anything. A submit whose reply is lost is retried under the **same** `request_id` (the door replays, never accepts twice). The procedure the model follows is `config/skills/thomas-ops/SKILL.md` §7.

## Check for drift, install

```bash
scripts/ops/install_hermes_shims.sh            # --check: same / DIFFERS / MISSING per file, exit 1 on drift, writes nothing
scripts/ops/install_hermes_shims.sh --install  # copies the five files over, backs up each changed one as <name>.bak-<stamp>, chown 10000:10000
docker restart hermes                          # the gateway spawns the MCP servers; a new shim is read at the next start
```

Run as root on the host (the destination is owned by uid 10000). The script never restarts anything and never touches the runtime image. Installing a shim that needs a newer door than the runtime speaks is the failure `MANIFEST.yaml` exists to prevent: read its `runtime_requirement` first, and deploy the runtime before the shim when a shim starts using a new command (v3, from P05).

The prompt and skill are installed by hand (`SOUL.md` → `/root/hermes-trial/data/SOUL.md`, the skill directory → `/root/hermes-trial/data/skills/thomas-ops/`), owner 10000:10000; a change there is a governance change the decision record names (§1.1), not a deploy detail.

## Tests

`tests/hermes_shims/` — the render branches of the four shims fed `Answer`s directly, and the door client's frame, receive loop and failure names against a one-shot AF_UNIX server. They run in the ordinary `python -m pytest tests/ -q` lane: a conftest puts `mcp/` on `sys.path` and stubs `mcp.server.fastmcp` when the package is absent (the Thomas venv and CI have no `mcp`). The three tests that open a real socket skip on Windows, counted in `tests/skip_ceiling.json`.

The host still carries its own copy of these tests under `/root/hermes-trial/data/mcp/tests/`; they are the same files and will be retired from the host once this directory has been installed at least once.
