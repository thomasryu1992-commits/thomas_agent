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

## Conventions & guardrails (do not violate without explicit Thomas approval)

- **Reuse first.** No new Contract / Schema / Registry / Gate unless an existing owner truly can't express it. One concept = one authority = one source of truth.
- **Fail-closed.** Missing / uncertain / hash-mismatch / authority-conflict → BLOCK, never guess. Every failure path raises a typed error with a stable `reason_code`.
- **Do not modify the read-only kernel.** Build in `runtime/mvp_runtime/`; import kernel modules as libraries.
- **Records must satisfy their closed schema.** Validate every produced record; the schema is authoritative.
- **Secrets are metadata-only.** Never store/log/audit secret values. `execution_budget.cost_currency` must be a 3-letter code (not null).
- **No direct `main` commits.** Branch → PR → gates → merge. All prior work landed via PRs.
- **Safety flags are gated.** `model_invocation` and `network_access` are OFF and require explicit Thomas approval + versioned governance update + audit before enabling (the "Safety-Flag Gate"). A good test result is never an auto-approval for the next capability.
- **Determinism (MVP definition):** pipeline-determinism + recorded-replay, not model-output byte-equality. Deterministic ids come from `integrity.short_id` over a seed.
- Match existing code style: `from __future__ import annotations`, type hints, no side effects at import.

## Status & roadmap

- Done: R0.5 (repo sync/cleanup), R1 (MVP spec), **R2.1 Task Intake** (`runtime/mvp_runtime/intake.py` + `cli.py`, tests green).
- Next: **R2.2 Thomas Prime Planner** → R2.3 Role Routing → R2.4 LLM invocation (behind mock until the Safety-Flag Gate) → R2.5 Validation → R2.6 Audit → R2.7 E2E.
- Then: R3 Read-only Tool (web search) → R4 Operator/Telegram → R4.5 Server Deploy (Dockerfile + prod requirements live here) → R5 Memory → R6 Scheduler → R7 Multi-Agent (last) → R8 Controlled Write.

Key locked decisions: MVP use case = "analyze this business idea"; MVP role = `general.specialist`; provider = a single free hosted API (default Google AI Studio; alt Groq/Cerebras); MVP runtime is a new module reusing kernel parts (not a kernel extension).
