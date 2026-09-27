# Thomas Agent

A governance-first autonomous agent, run by one operator (Thomas) on one server. The core is
strict and the runtime is deliberately thin on policy: behaviour is defined by contracts
(YAML/Markdown with closed JSON Schemas), the runtime only executes validated inputs in order,
and nothing is active until an explicit, versioned, audited approval turns it on.

What runs today: a Telegram control channel and an analysis pipeline ("analyze this business
idea"), a scheduler that fires recurring work, a crypto research and paper-trading lane, a content
lane, and the doors an assistant (Hermes, a separate image) uses to reach them. Eight services
from one image — see [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

This is a personal system, published as-is. There is no license file.

## Safety boundary

- **Fail-closed.** Missing, uncertain, hash-mismatched or conflicting authority is a BLOCK with a
  stable `reason_code`, never a guess.
- **Capabilities are off in code.** Model calls and network access open only behind the
  Safety-Flag Gate, on an environment opt-in plus a versioned governance update
  ([`governance/GOVERNANCE_POLICY.yaml`](governance/GOVERNANCE_POLICY.yaml)). A passing test is
  never an approval.
- **Secrets are metadata-only.** They are never stored, logged or audited. The deployed secrets
  live in a host `.env` that is not in this repository.
- **Money movement is staged.** The crypto lane climbs
  `READ_ONLY → SHADOW → PAPER → SIGNED_TESTNET → LIVE_AUTONOMOUS → LIVE_SCALED`. Each step up is
  an approved, audited record bound to the running policy; stepping down is immediate. The entry
  guard refuses a new live entry below `LIVE_AUTONOMOUS`
  ([`docs/runtime-contracts/EXECUTION_STAGE_V0.1.md`](docs/runtime-contracts/EXECUTION_STAGE_V0.1.md)).

## Current stage

As of 2026-09-27 the deployed host is at **PAPER**: strategies are researched and traded on paper,
and no order reaches a venue. The repository cannot tell you this — the stage is a runtime record
on the machine. Read it there:

```bash
docker exec -u 10001 thomas-scheduler python -m scripts.register_execution_stage --show
docker exec -u 10001 thomas-scheduler python -m runtime.mvp_runtime.crypto.live_readiness
```

## Layout

| Path | What it is |
|---|---|
| `runtime/mvp_runtime/` | The runtime: pipeline, scheduler, stores, gates, and the domain lanes (`crypto/`, `knowledge/`) |
| `runtime/read_only_kernel/` | The read-only kernel — the runtime imports its modules (integrity, schema validation, audit) and never modifies it |
| `governance/` | The governance policy: permissions, effects, authority |
| `THOMAS_CORE/` | The active core (identity, values, operating policy) and its release flow |
| `docs/runtime-contracts/`, `schemas/` | Contracts and their closed schemas |
| `03_ROLE_CONTRACTS/`, `05_REGISTRIES/` | Role and registry definitions |
| `scripts/` | Gates, validators, and operator CLIs |
| `tests/` | The runtime test suite |
| `deferred/`, `historical/`, `generated/` | Frozen designs, retired evidence, and generated artifacts — not active authority |

## Running the tests

Python 3.12.

```bash
python -m pip install -r requirements-validation.lock pytest pytest-xdist
python scripts/ci_activate_core_for_tests.py
python -m pytest tests/ -q -n auto
python scripts/run_repository_release_gate.py --full --check-only
```

The Core activation step matters: without it about 200 tests skip silently. The release gate and
the pytest suite check different things, and both must pass.

## Where to read next

- [`docs/README.md`](docs/README.md) — the documentation index.
- [`docs/ACTIVE_ARCHITECTURE.md`](docs/ACTIVE_ARCHITECTURE.md) — source ownership and the canonical gates.
- [`docs/REMAINING_WORK.md`](docs/REMAINING_WORK.md) — what is left to build.
- [`docs/proposals/STATUS.md`](docs/proposals/STATUS.md) — which proposals wait on a decision.
- [`docs/history/`](docs/history/) — why each increment is shaped the way it is.
- [`CLAUDE.md`](CLAUDE.md) — the working rules for this repository.
