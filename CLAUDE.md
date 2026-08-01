# CLAUDE.md — Thomas Agent

Rules for working in this repo. This file states **rules, not status** — for status see
the authority table below.

## What this project is

A governance-first autonomous agent. **Strong governance core, thin deterministic runtime**:
behavior is defined by contracts (YAML/Markdown + closed JSON Schemas); the runtime only
executes validated inputs in order. Nothing is active until an explicit, versioned, audited
approval turns it on.

## Guardrails (do not violate without explicit Thomas approval)

- **Reuse first.** No new Contract / Schema / Registry / Gate unless an existing owner truly
  can't express it. One concept = one authority = one source of truth. The canonical registry
  resolver is `runtime/registry_resolution.py`.
- **Fail-closed.** Missing / uncertain / hash-mismatch / authority-conflict → BLOCK, never
  guess. Every failure path raises a typed error with a stable `reason_code`.
- **Never modify `runtime/read_only_kernel/`.** Build in `runtime/mvp_runtime/`; import kernel
  modules (`integrity`, `schema_validation`, `audit`, …) as libraries.
- **Records must satisfy their closed schema** (`additionalProperties: false`). Validate every
  record you produce; the schema is authoritative.
- **Secrets are metadata-only.** Never store/log/audit secret values.
  `execution_budget.cost_currency` is a 3-letter code, never null.
- **Safety flags are OFF, enforced in code.** `model_invocation` / `network_access` need Thomas
  approval + a versioned governance update + audit before enabling. Selection goes through
  `safety_gate.select_gated(...)`, which constructs the capable implementation only after
  `authorize()` verifies a local integrity-checked grant (one per provider, gitignored,
  per-machine). An env var alone (`MVP_HOSTED_PROVIDER`) fails closed. A passing test is never
  an approval for the next capability. Mechanics: `runtime/mvp_runtime/safety_gate.py`.
- **Claude does not touch the live money path.** The crypto stack can place a real order.
  Claude does not run it, does not handle keys, does not enable live trading.
- **Never run state-writing CLIs on the host as root.** Services run as uid 10001 and mount
  `.runtime_governance_state/`; a root run leaves files the service can no longer write and
  fails later, in another process, with nothing pointing back at the cause. Use
  `docker exec thomas-scheduler python -m scripts.<script> …` — the **module** form, not
  `python scripts/<script>.py`, which puts `/app/scripts` on `sys.path` instead of `/app` and
  dies on `ModuleNotFoundError: No module named 'runtime'` for every script that does not patch
  `sys.path` itself. `state_guard` refuses the dangerous case at the door but does not
  self-heal — `chown -R 10001:10001` is the fix.
- **Tag the running image BEFORE `docker compose build`, never after.** The build reassigns
  `thomas-agent-runtime:latest`, and once it has, the image the containers are *still running
  on* is gone from the image store — not dangling, not tagged. `docker tag <old-id> …` then
  fails with `No such image` and there is no image left to roll back to. The tag convention
  already existed (`rollback-pre-366`); what was never written down is that it only works
  beforehand, while `latest` still points at the running image:
  `docker tag thomas-agent-runtime:latest thomas-agent-runtime:rollback-pre-<PR#>`.
  Measured 2026-08-01 on the #416 deploy, where it was skipped and the rollback point was lost.
  The fallback is `git checkout <commit> && docker compose build && docker compose up -d` —
  reproducible, and the reason this is a lost minute rather than a lost deploy, but it needs a
  clean tree and takes minutes where a tag takes seconds.
- **Never commit** `CURRENT_CORE_RELEASE.yaml`, `THOMAS_CORE/activations/`,
  `THOMAS_CORE/approvals/`, `.runtime_governance_state/**` — per-machine runtime state.
- **No direct `main` commits.** Branch → PR → gates → merge. Enforced by
  `.githooks/pre-commit`, which git runs **in the worktree being committed to** — enable
  it once per clone: `git config core.hooksPath .githooks`. A PreToolUse hook
  (`.claude/hooks/block-main-commits.sh`) is the coarse net for a bare `git commit` in the
  agent's own directory; it deliberately declines to judge commands that change directory
  first (`cd <worktree> && git commit …`), because it cannot see which tree those land in
  and guessing denied every legitimate worktree commit while the primary checkout rested
  on `main`.
- Match existing style: `from __future__ import annotations`, type hints, no import-time
  side effects.

## Commands

Development and deployment are on one **Linux Docker host**. Run from the repo root so the
`runtime`/`tests` namespace packages resolve.

```
.venv/bin/python -m pytest tests/ -q
.venv/bin/python scripts/run_repository_release_gate.py --full --check-only   # what CI runs = real acceptance
docker exec thomas-scheduler python -m runtime.mvp_runtime.cli "이 사업 아이디어를 분석해줘: ..."
```

CI is **Python 3.12**; the host venv is 3.14 and only the container has 3.12, so a green local
`pytest` is a fast signal, not CI parity — the release gate is the real acceptance check. The
CLI writes the ledger, so it goes through the container for the reason in the root-run rule
above; `pytest` cannot, because the image carries no `tests/` and no pytest.

Intake flags: `--independent-validation[=auto]`, `--important` (priority HIGH; under `auto`
adds the independent reviewer), `--revise` (one governed regeneration on a validation REVISE,
then deliver or BLOCK), `--write-output PATH`. Any unknown `--flag` → `EXIT_USAGE`, never
folded into the request text. The CLI takes **no** pointer argument — it reads
`.runtime_governance_state/CURRENT_CORE_RELEASE.yaml` by default.

First-time setup, local Core activation, and end-to-end verification: use the `verify` skill.

## Where authority lives

| Question | Owner |
|---|---|
| Design direction; expansion criteria + guardrails (§12–16) | `docs/THOMAS_AUTONOMOUS_ORGANIZATION_ARCHITECTURE.md` |
| Permission / authority / effect model (P0–P6, ALLOW…BLOCK) | `governance/GOVERNANCE_POLICY.yaml` (`runtime_effect.mode: REVIEW_ONLY`) |
| Source ownership, repo boundaries, canonical Gate entrypoints | `docs/ACTIVE_ARCHITECTURE.md` |
| Contracts + their closed schemas | `docs/runtime-contracts/`, `schemas/` |
| Roles (routable: `general.specialist` P3, `validation.independent` P2) | `03_ROLE_CONTRACTS/ROLE_REGISTRY.yaml` |
| Active core (schema v0.4, `thomas_approved`) | `THOMAS_CORE/MVP_ACTIVE_CORE.yaml` |
| **Why** an increment is shaped that way — read before "fixing" something odd | `docs/BUILD_HISTORY.md` |
| What is left to build | `docs/REMAINING_WORK.md` |
| What is actually live **on this machine** | `python -m runtime.mvp_runtime.crypto.live_readiness` |

## Locked decisions

MVP use case = "analyze this business idea"; MVP role = `general.specialist`; the MVP runtime
is a new module reusing kernel parts, not a kernel extension. Provider = free hosted APIs
behind the Safety-Flag Gate as an **ordered failover chain**
(`MVP_HOSTED_PROVIDER=google_ai_studio,groq`; Thomas 2026-07-20): each member needs its own
per-machine grant, a chain with an unknown/unauthorized member fails closed **entirely**
(never silently shrinks), and failover fires only on PROVIDER_UNAVAILABLE (503/429 after the
member's own retry) — never on timeout or 4xx.

**Determinism (MVP definition)** = pipeline determinism + recorded replay, not model-output
byte-equality. Deterministic ids come from `integrity.short_id` over a seed.
