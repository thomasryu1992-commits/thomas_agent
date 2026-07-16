# CLAUDE.md — Thomas Agent

Guidance for Claude Code working in this repo. Read this first. Applies on every machine.

## What this project is

A governance-first autonomous agent ("Thomas Agent"). Design principle: **Strong Core / Governance center, thin deterministic Runtime**. Behavior is defined by contracts (YAML/Markdown + closed JSON Schemas); the runtime only executes validated inputs in order. Nothing is "active" until an explicit, versioned, audited approval turns it on.

Current reality: the committed system is contracts + a **read-only replay kernel** (no LLM call yet). The first live agent capability is being built now under Phase R2 (see Status).

## Repository layout

- `THOMAS_CORE/` — identity, values, goals, active core rules. `MVP_ACTIVE_CORE.yaml` is the only active core (schema v0.4, `thomas_approved`).
- `governance/GOVERNANCE_POLICY.yaml` — authoritative permission/authority/effect model (P0–P6, ALLOW/EXECUTE_AND_REPORT/APPROVAL_REQUIRED/BLOCK). `runtime_effect.mode: REVIEW_ONLY` — execution capabilities are OFF.
- `03_ROLE_CONTRACTS/` — roles + `ROLE_REGISTRY.yaml`. Active routable roles: `general.specialist` (P3), `validation.independent` (P2). Others are non-routable candidates.
- `docs/runtime-contracts/` + `schemas/` — the contracts and their **closed** JSON Schemas (`additionalProperties:false`). Reuse these; do not invent new ones.
- `runtime/read_only_kernel/` — deterministic read-only replay kernel. **Do not modify.** Reuse its modules (`integrity`, `schema_validation`, `audit`, etc.) as libraries.
- `runtime/mvp_runtime/` — the new live MVP runtime (Phase R2). New agent code goes here.
- `runtime/registry_resolution.py` — canonical registry resolver.
- `scripts/` — validators + the release gate. `tests/` — pytest suite.

## Dev environment & commands

CI uses **Python 3.12** (`.github/workflows/`). Match that locally.
- On Windows, `python` may be a Microsoft Store stub; use the `py` launcher: `py -3 -m venv .venv`.
- `.venv/` is gitignored — keep the virtualenv out of the repo.

Setup:
```
py -3 -m venv .venv                                  # Windows;  python3 -m venv .venv on Linux/mac
.venv/Scripts/python -m pip install -r requirements-validation.lock pytest   # *nix: .venv/bin/python
```

Run tests (from repo root so the `runtime`/`tests` namespace packages resolve):
```
.venv/Scripts/python -m pytest tests/ -q
```

Run the full repository release gate (what CI runs — the real acceptance check):
```
.venv/Scripts/python scripts/run_repository_release_gate.py --full --check-only
```

Run the MVP intake CLI (R2.1):
```
.venv/Scripts/python -m runtime.mvp_runtime.cli "이 사업 아이디어를 분석해줘: ..."
```
On Windows set `PYTHONUTF8=1` for non-ASCII I/O.

## Core activation (local, per-environment)

The MVP binds each Task to an **active** approved Core Release. The approved
Release itself (`THOMAS_CORE/releases/thomas-core-v0.2.1-*/`) is committed and
shared. **Activation is a local runtime step, not shared source**: the approval,
activation, and current-pointer records are gitignored and live per-machine. This
keeps the shared repo Core-neutral so the deferred runtime-promotion-readiness gate
stays green everywhere.

- The current pointer lives at **`.runtime_governance_state/CURRENT_CORE_RELEASE.yaml`**
  (outside `THOMAS_CORE/` so source validators don't treat the tree as activated).
- The MVP binding must be pointed at it: `--current-pointer .runtime_governance_state/CURRENT_CORE_RELEASE.yaml`.
- To activate on a fresh machine (once): record an operator-decision evidence file,
  then run `scripts/approve_core_release.py` → `scripts/activate_core_release.py`
  (source_type `operator_decision_intake`, verification `verified_by_control_channel`),
  then move the generated `THOMAS_CORE/CURRENT_CORE_RELEASE.yaml` into
  `.runtime_governance_state/`. The gitignored `THOMAS_CORE/approvals/` and
  `THOMAS_CORE/activations/` records stay local.
- Never commit `CURRENT_CORE_RELEASE.yaml`, `THOMAS_CORE/activations/`, or
  `THOMAS_CORE/approvals/` — they are local runtime state.

## Conventions & guardrails (do not violate without explicit Thomas approval)

- **Reuse first.** No new Contract / Schema / Registry / Gate unless an existing owner truly can't express it. One concept = one authority = one source of truth.
- **Fail-closed.** Missing / uncertain / hash-mismatch / authority-conflict → BLOCK, never guess. Every failure path raises a typed error with a stable `reason_code`.
- **Do not modify the read-only kernel.** Build in `runtime/mvp_runtime/`; import kernel modules as libraries.
- **Records must satisfy their closed schema.** Validate every produced record; the schema is authoritative.
- **Secrets are metadata-only.** Never store/log/audit secret values. `execution_budget.cost_currency` must be a 3-letter code (not null).
- **No direct `main` commits.** Branch → PR → gates → merge. All prior work landed via PRs.
- **Safety flags are gated (enforced, not just documented).** `model_invocation` and `network_access` are OFF and require explicit Thomas approval + versioned governance update + audit before enabling (the "Safety-Flag Gate"). A good test result is never an auto-approval for the next capability. Enforcement lives in `runtime/mvp_runtime/safety_gate.py`: a network-capable provider is only returned/used after `authorize()` verifies a **local, integrity-checked activation record** at `.runtime_governance_state/safety_flag_activation.json` (gitignored, per-machine, like the Core pointer) — present, self-hash-consistent, unexpired, evidence-backed, and explicitly enabling the requested flags/provider. An env var alone (`MVP_HOSTED_PROVIDER`) fails closed. To run the real hosted provider locally, build the record with `safety_gate.build_activation_record(...)` (it computes the tamper-evident `content_sha256`) referencing an operator-decision evidence file, and write it to that path; never commit it.
- **Determinism (MVP definition):** pipeline-determinism + recorded-replay, not model-output byte-equality. Deterministic ids come from `integrity.short_id` over a seed.
- Match existing code style: `from __future__ import annotations`, type hints, no side effects at import.

## Status & roadmap

- Done: R0.5 (repo sync/cleanup), R1 (MVP spec), **R2 full single-agent pipeline** (R2.1 Intake → R2.7 E2E: intake, Thomas Prime planner, role routing, model invocation behind the Safety-Flag Gate, output validation, hash-chained audit, durable append-only ledger), **R3 Read-only web-search tool** (mock path complete — search is an `INTERNAL_READ` ALLOW action whose hits become evidence and whose use is audited; the real Brave backend is gated, activate locally with `scripts/activate_safety_flag.py`), **R4 Operator/Telegram control channel** (`runtime/mvp_runtime/operator.py` + `operator_cli.py`: canonical identity gate — Telegram private 1:1, registered user+chat, unverified silently dropped — the real Telegram adapter behind the Safety-Flag Gate, and the poll→handle→send loop entrypoint; see `docs/runtime-contracts/OPERATOR_CONTROL_CHANNEL_V0.1.md`), and **R5 Memory** (R5.1–R5.4: working-memory candidate creation (ALLOW), durable per-machine store + scoped retrieval feeding back as context, operator-only promotion of candidates to VALIDATED, each memory event audited; `runtime/mvp_runtime/memory.py` + `working_memory.py` + `scripts/promote_memory_candidate.py`; **R5 retention** (§12.4): candidates carry `expires_at`, retrieval never serves an expired candidate, and `runtime/mvp_runtime/memory_cli.py prune` deletes expired candidates with an audited memory-retention event — VALIDATED memory untouched). Architecture-review remediations A (enforced Safety-Flag Gate), B (durable ledger + audit-every-outcome), and D (single authority for levels/invariant/effect in `authority.py`) also merged; C parked (see the handoff doc).
- Also done: **R4 emergency operator console** (`runtime/mvp_runtime/control.py` + `console_cli.py`): pause/kill/resume/status/stop over the verified Telegram channel or a local host CLI (SSH emergency stop), enforced by the loop off a local gitignored control state (`.runtime_governance_state/operator_control_state.json`) with fail-closed semantics (missing=ACTIVE, corrupt=KILLED) and durable tamper-evident control events. Honors `kill_switch.agent_can_disable_or_bypass: false` — only the authenticated operator can resume.
- Remaining R4 (deferred, not blocking): control-channel approval handling (MVP is ALLOW-only, so not needed yet) and `audit`/`recovery` console verbs beyond `/status` + the control-event log.
- Also done: **R4.5 Server Deploy** — `Dockerfile` (python:3.12-slim, non-root, runs the continuous operator loop) + `requirements-runtime.txt` (pinned prod deps: YAML + JSON Schema only) + `.dockerignore` + `docs/DEPLOYMENT.md`. The image carries only committed non-secret source; secrets (bot token, API key) come from env and per-machine state (Core pointer, registration, safety-flag activation, control state, ledger) is a mounted volume — never baked in, so a bare image fails closed on network capabilities. Emergency stop on a running service via `docker exec ... console_cli kill`.
- Also done: **R6 Scheduler** (`runtime/mvp_runtime/scheduler.py` + `scheduler_cli.py`): recurring schedules (interval cadence) run a stored task **template** — `analysis_task` (through the full pipeline) or `memory_prune` (the periodic driver for R5 retention), never a shell command. **Kill-switch bound** (`kill_blocks: scheduler_execution` — PAUSED/KILLED skips a due fire and drops the occurrence), overlap-safe (single-process sequential), every fire/skip audited; schedules in `.runtime_governance_state/schedules.jsonl` (local, gitignored). Thin live scheduler — the deferred `scheduler_plan_review.v0.1` review-only schema stays inactive. See `docs/runtime-contracts/SCHEDULER_V0.1.md`.
- Next: R7 Multi-Agent (last) → R8 Controlled Write.

Key locked decisions: MVP use case = "analyze this business idea"; MVP role = `general.specialist`; provider = a single free hosted API (default Google AI Studio; alt Groq/Cerebras); MVP runtime is a new module reusing kernel parts (not a kernel extension).
