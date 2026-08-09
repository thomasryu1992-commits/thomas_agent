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
  **Three capabilities are exceptions and open on the environment alone** — `select_env_gated`,
  not `select_gated`, with no grant record anywhere: live trading (`MVP_LIVE_TRADING=real`,
  2026-07-28), the candle archive (2026-08-04) and the Naver research lane
  (`MVP_NAVER_RESEARCH=enabled`, 2026-08-09). All three were moved off grants because
  `activate_safety_flag.py` caps a grant at 30 days and expiry hurt more than it bought — for
  trading, an expiry could block the CLOSE path and trap an open position; for the other two, a
  renewal gap is a silent hole in a long-running collection. Egress still re-checks, but it
  re-reads the **env var**, so revoking any of them means unsetting the var and restarting the
  container — deleting a file revokes nothing here. Adding a fourth is a Thomas decision, not a
  pattern to follow: every new capability starts on a grant, and
  `test_the_env_only_gate_has_exactly_the_capabilities_thomas_named` fails until the new call
  site is listed with its reasoning. Any test that could inherit one of these vars from the
  operator's machine is isolated in `tests/conftest.py` (`_GATE_ENV_VARS`).
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
