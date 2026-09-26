# CLAUDE.md — Thomas Agent

Rules for working in this repo. This file states **rules, not status** — for status see
the authority table below.

## What this project is

A governance-first autonomous agent. **Strong governance core, policy-thin deterministic
runtime**: behavior is defined by contracts (YAML/Markdown + closed JSON Schemas); the runtime
only executes validated inputs in order. Nothing is active until an explicit, versioned,
audited approval turns it on.

"Thin" is a claim about **policy, not size**. The domain packages have outgrown the core and
keep growing while the kernel does not move (§G of `docs/REMAINING_WORK.md` re-measures this —
and has already priced and declined restructuring, so do not "fix" the growth; the one exception is
crypto PR7, directive work since 2026-09-21, split step by step under a layer test and a record
comparison). What keeps the
core thin while lanes grow, stated as rules:

- A domain package (`crypto/`, `knowledge/`) is an **application of the core's chokepoints**
  (PermissionDecision, Safety-Flag Gate, audit chain — `docs/ACTIVE_ARCHITECTURE.md`), never a
  parallel runtime.
- The core import graph loads **zero** domain modules. A domain package appears at module
  level only in its own door modules (today `knowledge_bridge*.py`); everywhere else the core
  dispatches into a lane with function-local imports at the dispatch sites (`scheduler.py`,
  `domain_console.py`). `tests/test_mvp_runtime_domain_isolation.py` pins both properties;
  widening the door list is a decision to record there, not a convenience.
- A lane earns its size with evidence or is removed **whole** (`predmarket/`, 2026-08-02, is
  the precedent). Lanes are removable units, never core accretion.

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
- **Safety flags are OFF, enforced in code — and the environment is the gate.** Enabling a
  `model_invocation` / `network_access` capability needs Thomas approval + a versioned
  governance update + audit; a passing test is never an approval for the next capability.
  Every gated capability opens on its **environment opt-in alone** (Thomas 2026-08-10):
  selection goes through `safety_gate.select_env_gated(...)` (or its `_chain` / `_optional`
  variants), which constructs the capable implementation only behind the opt-in and hands it
  its `Authorization`; every egress re-check re-reads the env var. Revoking a capability means
  unsetting the var and **restarting the container**. An unset/unknown value selects the inert
  default, and an unknown or duplicate chain member fails the whole chain closed.
  `test_the_env_only_gate_has_exactly_the_capabilities_thomas_named` pins both directions — zero
  callers of the retired grant selectors, and an exact enumeration of the env-gated call sites —
  so a new capability cannot ship ungoverned. Every opt-in var is stripped per-test in
  `tests/conftest.py` (`_GATE_ENV_VARS`), floor-checked by
  `test_the_suite_isolates_every_gate_opt_in_env_var`. Leftover `safety_flag_activations/*.json`
  files are inert (delete freely, as uid 10001). Mechanics: `runtime/mvp_runtime/safety_gate.py`;
  why the per-machine grants were retired and what that gave up: `docs/BUILD_HISTORY.md`.
- **Claude does not touch the live money path.** The crypto stack can place a real order.
  Claude does not run it, does not handle keys, does not enable live trading.
- **Crypto research machinery is paused until the first forward-cohort verdict** (Thomas
  2026-09-26, review D3). No new hypothesis, trial or display machinery in `crypto/`. Exempt: bug
  fixes, the slippage measurement (review C2), 1h pooling (C3), and the PR7 split work. Judgement
  rules loosen only at a research-epoch boundary (`RESEARCH_EPOCH_V0.1.md` Q3). Record:
  `docs/proposals/SYSTEM_REVIEW_IMPROVEMENT_PLAN_V0.1.md`, "결정 (Thomas 2026-09-26)".
- **Never run state-writing CLIs on the host as root.** Services run as uid 10001 and mount
  `.runtime_governance_state/`; a root run leaves files the service can no longer write and
  fails later, in another process, with nothing pointing back at the cause. Use
  `docker exec thomas-scheduler python -m scripts.<script> …` — the **module** form, not
  `python scripts/<script>.py`, which puts `/app/scripts` on `sys.path` instead of `/app` and
  dies on `ModuleNotFoundError: No module named 'runtime'` for every script that does not patch
  `sys.path` itself. `state_guard` refuses the dangerous case at the door but does not
  self-heal — `chown -R 10001:10001` is the fix.
- **Assert what the fix DOES, not how it is spelled — and a failed assertion is where the
  investigation starts, never where it ends.** A check by identifier (`hasattr`, a constant's
  value, a substring of `inspect.getsource`) rots when the code is refactored: a check for
  `attach_cross_section` in `scheduler.py` once reported a merged fix missing after the legs had
  moved into `cycle.attach_mining_legs`. Prefer calling the thing — *"the helper attaches all
  five legs"* — because a property survives the rename that breaks a grep. Read the code before
  calling a deploy incomplete: a MISS means *the check and the tree disagree*, and the check is
  the newer of the two.
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
- **Five required checks on `main`** (Thomas decision Q14): the two Active Architecture Gates,
  `MVP runtime pytest (ubuntu-latest)`, `MVP runtime pytest (windows-latest)`, and
  `Docker build + fail-closed smoke`. Auto-merge waits for all five (~7–8 minutes); `strict` is
  on, so a branch that falls behind `main` re-runs after `update-branch`. Push follow-up commits
  BEFORE enabling auto-merge, or disable it first — a green head merges as soon as the five are
  green, and a later push arrives on a merged PR.
- **The assistant is the ninth compose service, not part of the runtime image.**
  `hermes` in `docker-compose.yml` is image-only (`hermes-agent`, built from
  `/root/hermes-trial/hermes-agent` out of band); the deploy procedure below never rebuilds or
  retags it, and `up -d` recreates it only when its own service block changed. Its boundary —
  bridge-only mount, three `.env` values, no `depends_on` — is pinned in
  `tests/test_deployment_env_passthrough.py`; changing that block is a governance change, not a
  deploy detail (`docs/HERMES_ORCHESTRATOR_ARCHITECTURE_V0.2.md`).
- Match existing style: `from __future__ import annotations`, type hints, no import-time
  side effects.

## Deploying

Several sessions build and deploy from this host at once, and the compose build context is the
primary checkout (`/root/thomas_agent`), which is often on another session's branch with
uncommitted work in it. The procedure exists so that every deploy ships exactly `origin/main` and
leaves a rollback point that really is the image that was running. These rules carry the same
standing as the guardrails above:

```
docker inspect thomas-scheduler --format '{{.Image}}'    # the RUNNING image …
docker images thomas-agent-runtime                        # … must be what `latest` points at
docker tag thomas-agent-runtime:latest thomas-agent-runtime:rollback-pre-<PR#>
git worktree add <tmp> origin/main --detach
docker build -t thomas-agent-runtime:candidate-<PR#> <tmp>
docker run --rm --entrypoint python thomas-agent-runtime:candidate-<PR#> -c "<assert the fix>"
#   … repeat the running-vs-latest check here, immediately before the promote …
docker tag thomas-agent-runtime:candidate-<PR#> thomas-agent-runtime:latest
docker compose up -d
git worktree remove <tmp>
```

- **Tag the rollback point before any build.** A build that targets `latest`
  (`docker compose build`, `docker build -t …:latest`) removes the running image from the image
  store — not dangling, gone — and then there is nothing left to tag. Building to
  `candidate-<PR#>` keeps `latest` on the running image until the one-step promote.
- **Tag the running image, not whatever `latest` happens to be.** A concurrent session that built
  without deploying leaves `latest` ahead of what is running, and tagging it names the *new* image
  as the rollback point — a tag that reads like a safety net and is not one. When the two differ,
  tag from the previous rollback tag (`docker tag …:rollback-pre-<prev> …:rollback-pre-<PR#>`),
  never from the raw image id, which is exactly the reference that stops resolving.
- **Re-read the host immediately before the promote, never your own notes.** A concurrent session
  redeploys everything, not just its own slice, and the window between build and promote is where
  it lands. A `rollback-pre-<N>` tag you did not create is the signal that it already has.
- **Never `compose up --build`** — it builds the primary checkout, whatever is in it. Judge what an
  image contains by asserting against the image, never by the commit a worktree was on.
- If the rollback point is lost anyway, rebuild the previous commit to a candidate tag from a clean
  worktree and promote that: minutes instead of seconds, but reproducible.

**A one-off script does not need a deploy.** To run newly merged code against live state without
restarting anything, build to a throwaway tag and run it with the scheduler's own mounts and
user — `latest` untouched, so no other session's `compose up -d` picks it up:

```
docker run --rm --user thomas -w /app \
  -v /root/thomas_agent/.runtime_governance_state:/app/.runtime_governance_state:rw \
  --entrypoint python thomas-agent-runtime:tool-<PR#> -m scripts.<script> --list
```

Read the user and mounts off `docker inspect thomas-scheduler` rather than copying them from here,
and back up any file the script rewrites first.

## Commands

Development and deployment are on one **Linux Docker host**. Run from the repo root so the
`runtime`/`tests` namespace packages resolve.

```
.venv/bin/python -m pytest tests/ -q
.venv/bin/python scripts/run_repository_release_gate.py --full --check-only   # governance validators; runs no pytest
docker exec thomas-scheduler python -m runtime.mvp_runtime.cli "이 사업 아이디어를 분석해줘: ..."
```

CI is **Python 3.12**; the host venv is 3.14 and only the container has 3.12, so a green local
`pytest` is a fast signal, not CI parity. The real acceptance check is the five required checks on
the PR, pytest on 3.12 among them. The release gate runs the governance validators only (the Active
Architecture Gate checks run its active scope on every PR, the full gate on gate/CI changes and
nightly) and no tests, so a green gate says nothing about the suite. The
CLI writes the ledger, so it goes through the container for the reason in the root-run rule
above; `pytest` cannot, because the image carries no `tests/` and no pytest.

Intake flags: `--independent-validation[=auto]`, `--important` (priority HIGH; under `auto`
adds the independent reviewer), `--revise` (one governed regeneration on a validation REVISE,
then deliver or BLOCK), `--write-output PATH`, `--naver-keywords "SEED[, SEED...]"` (runs the
gated Naver keyword brief; rows become [K#] evidence). Any unknown `--flag` → `EXIT_USAGE`, never
folded into the request text. The CLI takes **no** pointer argument — it reads
`.runtime_governance_state/CURRENT_CORE_RELEASE.yaml` by default.
**A `--naver-keywords` run execs in `thomas-pipeline-worker`, not the scheduler** — since the
plane separation that container is the only one holding the Naver env, and in any other the
brief silently degrades to Mock rows (`docker exec thomas-pipeline-worker python -m
runtime.mvp_runtime.cli "…" --naver-keywords "…"`). Keyword-less CLI runs are unaffected.

First-time setup, local Core activation, and end-to-end verification: use the `verify` skill.

## Where authority lives

| Question | Owner |
|---|---|
| Design direction; expansion criteria + guardrails (§12–16) | `docs/THOMAS_AUTONOMOUS_ORGANIZATION_ARCHITECTURE.md` |
| Permission / authority / effect model (P0–P6, ALLOW…BLOCK) | `governance/GOVERNANCE_POLICY.yaml` (`runtime_effect.mode: REVIEW_ONLY` — what the policy auto-grants, i.e. nothing; **not** whether live trading is on, which is `live_readiness` below) |
| Source ownership, repo boundaries, canonical Gate entrypoints | `docs/ACTIVE_ARCHITECTURE.md` |
| Contracts + their closed schemas | `docs/runtime-contracts/`, `schemas/` |
| Roles (routable: `general.specialist` P3, `validation.independent` P2) | `03_ROLE_CONTRACTS/ROLE_REGISTRY.yaml` |
| Active core (schema v0.4, `thomas_approved`) | `THOMAS_CORE/MVP_ACTIVE_CORE.yaml` |
| **Why** an increment is shaped that way — read before "fixing" something odd | `docs/history/` (one file per increment, since 2026-09-25; add yours in the PR) and `docs/BUILD_HISTORY.md` (the archive before that) |
| What is left to build | `docs/REMAINING_WORK.md` |
| Which proposals wait on a Thomas decision, and which decisions wait on a build | `docs/proposals/STATUS.md` — generated from each proposal's `**상태:**` line by `scripts/build_proposal_status.py`; change the line, never the page |
| What is actually live **on this machine** | `python -m runtime.mvp_runtime.crypto.live_readiness` |
| What execution stage this machine is at (crypto) | `python -m scripts.register_execution_stage --show`, run in `thomas-scheduler` (`docs/runtime-contracts/EXECUTION_STAGE_V0.1.md`) |

## Locked decisions

MVP use case = "analyze this business idea"; MVP role = `general.specialist`; the MVP runtime
is a new module reusing kernel parts, not a kernel extension. Provider = free hosted APIs
behind the Safety-Flag Gate as an **ordered failover chain**
(`MVP_HOSTED_PROVIDER=openrouter,google_ai_studio,groq`; Thomas 2026-07-20; openrouter
prepended Thomas 2026-07-24; grants retired Thomas 2026-08-10, the env
names the chain): a chain with an unknown or duplicate member fails closed **entirely**
(never silently shrinks), and failover fires only on PROVIDER_UNAVAILABLE (503/429 after the
member's own retry) — never on timeout or 4xx.

**Determinism (MVP definition)** = pipeline determinism + recorded replay, not model-output
byte-equality. Deterministic ids come from `integrity.short_id` over a seed.
