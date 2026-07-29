---
name: verify
description: How to build, run, and drive the Thomas Agent MVP runtime end-to-end to verify a change against its real surface (the intake CLI + the on-disk ledger).
---

# Verify a change in this repo

The runtime surface is the MVP intake CLI; evidence lands in the gitignored local ledger.

Commands here are bash on the Linux Docker host this project is developed and deployed on.
They were PowerShell until 2026-07-28 — the machine changed and the notes had not, which is
the failure mode this file exists to prevent for everything else.

## Two interpreters, and the split is not a preference

| | version | use it for |
|---|---|---|
| `.venv/bin/python` (host) | 3.14 | pytest, the release gate, anything read-only |
| `docker exec thomas-scheduler python` (container) | 3.12 | every CLI that WRITES `.runtime_governance_state/` |

Neither can do the other's job:

- The image carries `runtime/` and `scripts/` but **no `tests/` and no pytest**, so the suite
  only runs on the host.
- `.runtime_governance_state/` is owned by **uid 10001** — the account both services run as —
  and is bind-mounted rw into each container. A host-side root run leaves root-owned files the
  service can never write again, and it fails later, in a different process, with nothing
  pointing back at the command that caused it. `state_guard.assert_not_foreign_root_run`
  refuses that at the door on every operator script that writes — with one gap, below.

**CI is Python 3.12 and this host has no 3.12**, so a green local `pytest` is not CI parity
here. It never fully was: ~205 tests skip locally for want of an activated Core. Treat the
local suite as a fast signal and the release gate as the real check; reproduce a suspected
CI-only failure in a throwaway clone.

## Setup (fresh machine, once)

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-validation.lock pytest
```

No `PYTHONUTF8` anywhere: host and container both report UTF-8 for stdout, the filesystem
encoding and the preferred locale, and Korean round-trips unaided. It was a Windows-console
workaround, not a runtime requirement — `cli_common.force_utf8_io()` still handles the code's
side and stays, because the code should remain safe on a cp949 console even if nobody develops
on one.

Gotchas:
- If pytest errors with `PermissionError ... pytest-of-<user>`, pass `--basetemp` at a
  writable temp dir.
- The activation script fails on a leftover `THOMAS_CORE/approvals/core-approval-*.yaml` from
  a prior failed run — delete it and rerun. Do NOT let the ephemeral commit get checked out
  away (a branch switch deletes the tracked-then-ignored approval file the activation needs).

## Core activation (local, per-machine)

Every Task binds to an **active** approved Core Release. The approved Release itself
(`THOMAS_CORE/releases/thomas-core-v0.2.1-*/`) is committed and shared, but **activation is a
local runtime step, not shared source** — the approval, activation, and current-pointer
records are gitignored and live per-machine. That keeps the shared repo Core-neutral so the
deferred runtime-promotion-readiness gate stays green everywhere.

- The current pointer lives at **`.runtime_governance_state/CURRENT_CORE_RELEASE.yaml`** —
  outside `THOMAS_CORE/` so source validators don't treat the tree as activated.
- The MVP binding reads that path by default (`binding.DEFAULT_POINTER_REL`), so the intake
  CLI takes no pointer argument. `--current-pointer` belongs to
  `scripts/create_core_context_binding.py` and the release gate, not the MVP CLI.
- Never commit `CURRENT_CORE_RELEASE.yaml`, `THOMAS_CORE/activations/`,
  `THOMAS_CORE/approvals/`, or anything under `.runtime_governance_state/`.

### The shortcut, and the one chown it needs

```bash
git checkout -b tmp/core-activation      # the script makes an ephemeral commit
.venv/bin/python scripts/ci_activate_core_for_tests.py --allow-foreign-root-run
git reset HEAD~1                         # keep the gitignored state, drop the commit
chown -R 10001:10001 .runtime_governance_state
```

This is the one place the rule above is inverted, and the flag is why.
`ci_activate_core_for_tests.py` writes to **both** `THOMAS_CORE/approvals/` + `activations/`
(root-owned on the host, mounted **read-only** into the containers) and
`.runtime_governance_state/CURRENT_CORE_RELEASE.yaml` (uid 10001, rw). Neither side can run it
alone: in the container the `THOMAS_CORE` writes hit a read-only mount and there is no git
history to commit into, and on the host the pointer comes out root-owned.

So it is the one state writer whose refusal cannot say "re-run it under `docker exec`" — which
is why it refuses **locally** instead of through `assert_not_foreign_root_run`, and why the
deliberate case needs `--allow-foreign-root-run` rather than just working. Without the flag it
BLOCKs before writing anything.

The `chown` is not optional and the script will not pretend otherwise: an escaped run that
leaves a root-owned pointer prints `INCOMPLETE` and **exits non-zero**, because
`state_guard.assert_state_writable` would refuse to start either service against it. It does
not chown for you — nothing in this repo quietly widens its own access to governed state.

### The operator path (not the shortcut)

Record an operator-decision evidence file, run `scripts/approve_core_release.py` →
`scripts/activate_core_release.py` (source_type `operator_decision_intake`, verification
`verified_by_control_channel`), then move the generated
`THOMAS_CORE/CURRENT_CORE_RELEASE.yaml` into `.runtime_governance_state/`. Both produce the
same per-machine state.

## Drive the surface

```bash
.venv/bin/python -m runtime.mvp_runtime.cli "이 사업 아이디어를 분석해줘: <idea>"
```

Happy path: markdown analysis on stdout, `LEDGER: recorded to ...` on stderr, exit 0.
Evidence: `.runtime_governance_state/runtime_ledger/audit_events.jsonl` (5 hash-chained events
per run; a later run's first event carries the previous run's tip hash) and `records.jsonl`.

The CLI writes the ledger, so on this host it belongs in the container:

```bash
docker exec thomas-scheduler python -m runtime.mvp_runtime.cli "이 사업 아이디어를 분석해줘: <idea>"
```

Read the evidence back with Python — the lines are long and Korean-heavy, and `jq` is not
installed here:

```bash
.venv/bin/python -c "import json; [print(r['created_at'], r['event']['event_summary']) for r in map(json.loads, open('.runtime_governance_state/runtime_ledger/audit_events.jsonl'))]"
```

Fail-closed probes that work from the surface:
- Empty/whitespace request → `BLOCKED EMPTY_REQUEST`, exit 3.
- `MVP_HOSTED_PROVIDER=google_ai_studio` without an activation record →
  `BLOCKED ACTIVATION_MISSING`, exit 2 (safety gate).
- A hand-written `.runtime_governance_state/safety_flag_activations/<provider_id>.json` (one
  grant per provider) with a bad field (e.g. `authority_level: "P9"`) →
  `BLOCKED ACTIVATION_MALFORMED`. A BOM is itself rejected; a plain write from any Linux tool
  is BOM-less, so nothing special is needed. Delete the file after the probe, and write it as
  uid 10001 (`docker exec`) or chown it afterwards.

## Read-only status verbs

These take no state lock and write nothing, so they are safe to run at any time — including
during an observation window where only read-only verbs are permitted:

```bash
docker exec thomas-scheduler python -m runtime.mvp_runtime.crypto.live_readiness
docker exec thomas-scheduler python scripts/promote_strategy_candidates.py --list
```

`live_readiness` is the authority for what is actually live **on this machine**; the checklist
it prints is read per-run, never cached.

## Acceptance checks (the real one, plus its caveat)

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/python scripts/run_repository_release_gate.py --full --check-only
```

The gate is what CI runs and is the real acceptance signal. `--check-only` writes no Release
Gate evidence and grants no Release, Core, Runtime, or execution authority.
