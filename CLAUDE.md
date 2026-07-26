# CLAUDE.md — Thomas Agent

Guidance for Claude Code working in this repo. Read this first. Applies on every machine.

## What this project is

A governance-first autonomous agent ("Thomas Agent"). Design principle: **Strong Core / Governance center, thin deterministic Runtime**. Behavior is defined by contracts (YAML/Markdown + closed JSON Schemas); the runtime only executes validated inputs in order. Nothing is "active" until an explicit, versioned, audited approval turns it on.

Current reality: the committed system is contracts + a **read-only replay kernel** (no LLM call yet). The first live agent capability is being built now under Phase R2 (see Status).

## Repository layout

- `docs/THOMAS_AUTONOMOUS_ORGANIZATION_ARCHITECTURE.md` — **top-level design-direction (Goal) document** (promoted from historical v0.1 by Thomas decision 2026-07-24). New features, milestones, and roadmaps must be designed against its three layers (Target / MVP / Dynamic Task Team) and its expansion criteria + guardrails (§12–§16). Design authority only — Permission/effects stay with the Governance Policy, current-implementation truth with `docs/ACTIVE_ARCHITECTURE.md`.
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
Options are `--independent-validation[=auto]` (R7/R7.1), `--important` (R7.1: priority
HIGH — under `auto`, adds the independent reviewer to this run), `--revise` (M3: opt-in
one-shot revision loop — a validation REVISE earns exactly one governed regeneration,
pre-allocated + audited, then delivers or BLOCKs), and `--write-output PATH`
(R8); any other `--flag` is rejected with `EXIT_USAGE` rather than folded into the request
text.
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
- The MVP binding reads that path by default (`binding.DEFAULT_POINTER_REL`) — the intake
  CLI takes no pointer argument, so pass nothing. (`--current-pointer` belongs to
  `scripts/create_core_context_binding.py` and the release gate, not to the MVP CLI.)
- To activate on a fresh machine (once): record an operator-decision evidence file,
  then run `scripts/approve_core_release.py` → `scripts/activate_core_release.py`
  (source_type `operator_decision_intake`, verification `verified_by_control_channel`),
  then move the generated `THOMAS_CORE/CURRENT_CORE_RELEASE.yaml` into
  `.runtime_governance_state/`. The gitignored `THOMAS_CORE/approvals/` and
  `THOMAS_CORE/activations/` records stay local.
- Never commit `CURRENT_CORE_RELEASE.yaml`, `THOMAS_CORE/activations/`, or
  `THOMAS_CORE/approvals/` — they are local runtime state.
- **Run state-writing CLIs through the container, never on the host as root.** The
  services run as uid 10001 and mount `.runtime_governance_state/`; a host-side root run
  leaves root-owned files there that the service can no longer write, and it fails later,
  in a different process, with nothing pointing back at the command that caused it (this
  happened twice on 2026-07-25/26 — a safety-flag activation and an operator notify
  pointer). Use `docker exec thomas-scheduler python scripts/<script>.py …`.
  `state_guard.assert_not_foreign_root_run` refuses the dangerous case at the door, and
  `assert_state_writable` refuses to start a service whose state is already broken;
  neither self-heals — if you are told to `chown -R 10001:10001`, that is the fix.

## Conventions & guardrails (do not violate without explicit Thomas approval)

- **Reuse first.** No new Contract / Schema / Registry / Gate unless an existing owner truly can't express it. One concept = one authority = one source of truth.
- **Fail-closed.** Missing / uncertain / hash-mismatch / authority-conflict → BLOCK, never guess. Every failure path raises a typed error with a stable `reason_code`.
- **Do not modify the read-only kernel.** Build in `runtime/mvp_runtime/`; import kernel modules as libraries.
- **Records must satisfy their closed schema.** Validate every produced record; the schema is authoritative.
- **Secrets are metadata-only.** Never store/log/audit secret values. `execution_budget.cost_currency` must be a 3-letter code (not null).
- **No direct `main` commits.** Branch → PR → gates → merge. All prior work landed via PRs.
- **Safety flags are gated (enforced, not just documented).** `model_invocation` and `network_access` are OFF and require explicit Thomas approval + versioned governance update + audit before enabling (the "Safety-Flag Gate"). A good test result is never an auto-approval for the next capability. Enforcement lives in `runtime/mvp_runtime/safety_gate.py`: a capable implementation is only returned/used after `authorize()` verifies a **local, integrity-checked activation record** — present, self-hash-consistent, unexpired, evidence-backed, and explicitly enabling the requested flags/provider. An env var alone (`MVP_HOSTED_PROVIDER`) fails closed. **One grant per provider**, at `.runtime_governance_state/safety_flag_activations/<provider_id>.json` (gitignored, per-machine, like the Core pointer): each is scoped/expired/evidenced on its own, so authorizing a second provider cannot widen or refresh the first and a corrupt grant fails only its own provider closed. The filename is just an index — the record's own `provider_id` is the authority (self-hashed), so copying a grant under another provider's name grants nothing. A `provider_id` is a path segment, so it is pattern-checked + containment-checked (`activation_path`). Activate locally with `scripts/activate_safety_flag.py --provider-id ...` (it mints the tamper-evident `content_sha256` + the evidence file); never commit either. **Selection goes through `safety_gate.select_gated(...)`** — `gated_factory` receives the `Authorization`, so the capable implementation cannot be constructed before the gate opens.
- **Determinism (MVP definition):** pipeline-determinism + recorded-replay, not model-output byte-equality. Deterministic ids come from `integrity.short_id` over a seed.
- Match existing code style: `from __future__ import annotations`, type hints, no side effects at import.

## Status & roadmap
- **Everything on the R-series roadmap (R0.5–R10) has shipped**, plus the crypto pipeline (C-series),
  the LLM orchestration track (M-series), candidate role trials, and programization through registry
  registration. What each increment does and **why it is shaped that way** is in
  `docs/BUILD_HISTORY.md` — read that before changing something that looks arbitrary; most of it is a
  recorded decision with a failure behind it.
- **What is left to build** lives in `docs/REMAINING_WORK.md`, and only there. This file states rules;
  it deliberately no longer duplicates status, because four places claiming status is how the
  readiness board came to describe a shipped module as missing.
- **The one status fact worth carrying in a rules file**, because it changes what "be careful" means:
  the crypto live-execution stack can now place a **real order**. `financial_transaction_execution_implemented`
  is `true`, LP4's signed adapter and LP5's executing leg both exist, and the money path builds a P5
  PermissionDecision + audit event. What still prevents autonomous trading is structural, not missing
  code: no autonomous entry point may import the order path (a test enforces it), `financial_executor_enabled`
  is `false`, zero of the required three clean canary orders have been placed, and every egress needs the
  operator's per-machine `live_trading` grant, order key, confirmation phrase, and registered budget.
  **Claude does not run that path, does not handle keys, and does not enable live trading.**
  For this machine's actual state, ask the board rather than any document:
  `python -m runtime.mvp_runtime.crypto.live_readiness`.


Key locked decisions: MVP use case = "analyze this business idea"; MVP role = `general.specialist`; provider = free hosted APIs behind the Safety-Flag Gate — originally a single API (Google AI Studio), widened by explicit Thomas decision (2026-07-20, after a live Gemini 503 outage) to an **ordered failover chain** (`MVP_HOSTED_PROVIDER=google_ai_studio,groq`): each member needs its own per-machine grant, a chain with an unknown/unauthorized member fails closed entirely (never silently shrinks), and failover happens only on PROVIDER_UNAVAILABLE (503/429 after the member's own retry), never on timeout/4xx; MVP runtime is a new module reusing kernel parts (not a kernel extension).
