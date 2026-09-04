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
and has already priced and declined restructuring, so do not "fix" the growth). What keeps the
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
  Since **Thomas 2026-08-10**, every gated capability opens on its **environment opt-in
  alone**: selection goes through `safety_gate.select_env_gated(...)` (or its `_chain` /
  `_optional` variants), which constructs the capable implementation only behind the opt-in
  and hands it its `Authorization`; every egress re-check re-reads the env var. The
  per-machine grant records and their 30-day renewal are **retired** — on capabilities meant
  to run indefinitely the renewal bought too little: live trading left grants first
  (2026-07-28; an expiry could block the CLOSE path and trap an open position), the candle
  archive (2026-08-04) and the Naver lane (2026-08-09) followed (a renewal gap is a silent
  hole in a long-running collection), and 2026-08-10 retired the rest on the same ledger.
  **What that gives up, on the record:** the second factor, the expiry, the per-machine
  audited scope/authority record, and file-deletion revocation — revoking a capability now
  means unsetting the var and **restarting the container**. What it does not give up: an
  unset/unknown value still selects the inert default, and an unknown or duplicate chain
  member still fails the whole chain closed.
  `test_the_env_only_gate_has_exactly_the_capabilities_thomas_named` enforces both
  directions — zero callers of the retired grant selectors, and an exact enumeration of the
  env-gated call sites, so a new capability still cannot ship ungoverned. Every opt-in var is
  stripped per-test in `tests/conftest.py` (`_GATE_ENV_VARS`), floor-checked by
  `test_the_suite_isolates_every_gate_opt_in_env_var`. Leftover
  `safety_flag_activations/*.json` files are inert (delete freely, as uid 10001). Mechanics:
  `runtime/mvp_runtime/safety_gate.py`.
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
  **`latest` is what you can tag; the RUNNING image is what you need — check they are the same
  rather than assuming it.** `docker inspect thomas-scheduler --format '{{.Image}}'` against
  `docker images thomas-agent-runtime`. A concurrent session that builds without deploying
  leaves `latest` ahead of what is running, and tagging it then names the **new** image as the
  rollback point — a tag that reads like a safety net and is not one. Hit 2026-08-02; it cost
  nothing only because the previous deploy's tag still held the real image. When they differ,
  tag **from that existing tag** (`docker tag …:rollback-pre-<prev> …:rollback-pre-<PR#>`),
  never from the raw image id, which is exactly the reference that stops resolving.
  The fallback is `git checkout <commit> && docker compose build && docker compose up -d` —
  reproducible, and the reason this is a lost minute rather than a lost deploy, but it needs a
  clean tree and takes minutes where a tag takes seconds.
- **Build to a candidate tag, verify it, then promote `latest`.** Everything above is about
  surviving a build that overwrites `latest`; this avoids the overwrite. `docker compose build`
  and a bare `docker build -t …:latest` both reassign the tag the running containers were
  started from, which is what opens the window the rule above closes by hand. Building to a
  name of its own does not:

  ```
  docker tag thomas-agent-runtime:latest thomas-agent-runtime:rollback-pre-<PR#>   # still first
  docker build -t thomas-agent-runtime:candidate-<PR#> <clean-worktree>
  docker run --rm --entrypoint python thomas-agent-runtime:candidate-<PR#> -c "<assert the fix>"
  docker tag thomas-agent-runtime:candidate-<PR#> thomas-agent-runtime:latest
  docker compose up -d
  ```

  `latest` keeps pointing at the running image for the whole build, the candidate is provable
  before anything restarts, and promotion is one atomic retag. Used for every deploy on
  2026-08-08/09. Never assert by the commit the worktree was on: what a build actually
  contains is the question, and a `git log` that says the merge landed does not answer it.
- **Assert what the fix DOES, not how it is spelled — and a failed assertion is where the
  investigation starts, never where it ends.** The obvious check is an identifier: `hasattr`,
  a constant's value, a substring of `inspect.getsource`. Identifiers move. Measured twice in
  one audit on 2026-08-09, both false alarms on merged-and-deployed work:

  - a check for `attach_cross_section` in `scheduler.py` reported the #601 fix missing, after
    #621 extracted the five legs into `cycle.attach_mining_legs`;
  - `PROMOTION_HASH_VERSION == "strategy_promotion.v3"` reported #649 missing, after #648
    bumped it to v4 while keeping #649's field.

  Prefer calling the thing over matching a name — *"the reactivation set changes the hash"*,
  *"this column is supplied on the frame"*, *"the helper attaches all five legs"* — because a
  property survives the rename that breaks a grep. Where only a name will do, expect it to rot.

  **The rule that matters is the second half.** Both checks above were wrong in the direction
  that produces a confident false report, and reporting either as a regression would have been
  the only damage done that day. Read the code before calling a deploy incomplete: a MISS means
  *the check and the tree disagree*, and the check is the newer of the two.
- **Build from a clean `origin/main` worktree, and never `compose up --build`.** The compose
  build context is `/root/thomas_agent`, the primary checkout — which on a busy day is on
  another session's branch with uncommitted work in it. `--build` ships that. Measured
  repeatedly on 2026-08-08/09: the primary checkout sat on a foreign feature branch with
  modified `live_leg.py` for most of the day. `git worktree add <tmp> origin/main --detach`,
  build from there, remove it after. Cheap, and it is the only way to say what the image
  contains.
- **You are not the only session deploying. Re-read the host, never your own notes.** A
  concurrent session redeploys everything, not just its own slice. Measured 2026-08-09: a deploy
  finished at 07:56 and by 07:59 the containers had been recreated by someone else — a different
  running image, and two `rollback-pre-*` tags this session had not created. A state recorded
  minutes ago is not evidence. So the running-vs-`latest` check above is not a formality at the
  start of a deploy — it is a **re-read immediately before the promote**, because the window
  between build and promote is exactly where the other session lands. A `rollback-pre-<N>` tag
  you did not create is the signal that it already has.
- **A one-off script does not need a deploy.** To run newly merged code against live state
  without restarting anything, build to a throwaway tag and run it with the scheduler's own
  mounts and user — `latest` untouched, so no other session's `compose up -d` picks it up:

  ```
  docker run --rm --user thomas -w /app \
    -v /root/thomas_agent/.runtime_governance_state:/app/.runtime_governance_state:rw \
    --entrypoint python thomas-agent-runtime:tool-<PR#> -m scripts.<script> --list
  ```

  Read the user and mounts off `docker inspect thomas-scheduler` rather than copying them from
  here, and back up any file the script rewrites first. Used 2026-08-09 to dedupe the shadow
  book while the live window kept trading.
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
- **Five required checks on `main` since 2026-09-04** (PR3 of the Hermes sequence, Thomas
  decision Q14): the two Active Architecture Gates, `MVP runtime pytest (ubuntu-latest)`,
  `MVP runtime pytest (windows-latest)`, and `Docker build + fail-closed smoke`. Until then only
  the two one-minute gates were required, so auto-merge could land a branch with a red pytest or
  a compose file the smoke rejects. Auto-merge now waits for all five (~7–8 minutes); `strict`
  stays on, so a branch that falls behind `main` re-runs after `update-branch`. Push follow-up
  commits BEFORE enabling auto-merge, or disable it first — a green head merges as soon as the
  five are green, and a later push arrives on a merged PR.
- **The assistant is the ninth compose service, not part of the runtime image** (PR5, 2026-09-04).
  `hermes` in `docker-compose.yml` is image-only (`hermes-agent`, built from `/root/hermes-trial/hermes-agent`
  out of band); the candidate-tag flow above never rebuilds or retags it, and `up -d` recreates it only
  when its own service block changed. Its boundary — bridge-only mount, three `.env` values, no
  `depends_on` — is pinned in `tests/test_deployment_env_passthrough.py`; changing that block is a
  governance change, not a deploy detail (`docs/HERMES_ORCHESTRATOR_ARCHITECTURE_V0.1.md`).
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
(`MVP_HOSTED_PROVIDER=openrouter,google_ai_studio,groq`; Thomas 2026-07-20; openrouter
prepended Thomas 2026-07-24 — 8bde1f9, 4da8118; grants retired Thomas 2026-08-10, the env
names the chain): a chain with an unknown or duplicate member fails closed **entirely**
(never silently shrinks), and failover fires only on PROVIDER_UNAVAILABLE (503/429 after the
member's own retry) — never on timeout or 4xx.

**Determinism (MVP definition)** = pipeline determinism + recorded replay, not model-output
byte-equality. Deterministic ids come from `integrity.short_id` over a seed.
