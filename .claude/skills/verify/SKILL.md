---
name: verify
description: How to build, run, and drive the Thomas Agent MVP runtime end-to-end to verify a change against its real surface (the intake CLI + the on-disk ledger).
---

# Verify a change in this repo

The runtime surface is the MVP intake CLI; evidence lands in the gitignored
local ledger. Windows notes assume PowerShell 5.1.

## Setup (fresh machine, once)

```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-validation.lock pytest
# Local Core activation (required for the CLI happy path and ~52 pipeline tests):
git checkout -b tmp/core-activation   # script makes an ephemeral commit
.venv\Scripts\python scripts/ci_activate_core_for_tests.py
git reset HEAD~1                      # keep the gitignored state, drop the commit
```

Gotchas:
- Always set `$env:PYTHONUTF8='1'` — Korean I/O breaks without it.
- If pytest errors with `PermissionError ... pytest-of-<user>`, pass
  `--basetemp` pointing at a writable temp dir.
- The activation script fails on a leftover
  `THOMAS_CORE/approvals/core-approval-*.yaml` from a prior failed run — delete
  it and rerun. Do NOT let the ephemeral commit get checked out away (a branch
  switch deletes the tracked-then-ignored approval file the activation needs).

## Core activation (local, per-machine)

`ci_activate_core_for_tests.py` above is the shortcut for verification. The real
operator path is below; both produce the same per-machine state.

Every Task binds to an **active** approved Core Release. The approved Release
itself (`THOMAS_CORE/releases/thomas-core-v0.2.1-*/`) is committed and shared, but
**activation is a local runtime step, not shared source** — the approval,
activation, and current-pointer records are gitignored and live per-machine. That
keeps the shared repo Core-neutral so the deferred runtime-promotion-readiness
gate stays green everywhere.

- The current pointer lives at
  **`.runtime_governance_state/CURRENT_CORE_RELEASE.yaml`** — outside `THOMAS_CORE/`
  so source validators don't treat the tree as activated.
- The MVP binding reads that path by default (`binding.DEFAULT_POINTER_REL`), so the
  intake CLI takes no pointer argument. `--current-pointer` belongs to
  `scripts/create_core_context_binding.py` and the release gate, not the MVP CLI.
- Operator path on a fresh machine (once): record an operator-decision evidence
  file, run `scripts/approve_core_release.py` → `scripts/activate_core_release.py`
  (source_type `operator_decision_intake`, verification
  `verified_by_control_channel`), then move the generated
  `THOMAS_CORE/CURRENT_CORE_RELEASE.yaml` into `.runtime_governance_state/`. The
  gitignored `THOMAS_CORE/approvals/` and `THOMAS_CORE/activations/` records stay
  local.
- Never commit `CURRENT_CORE_RELEASE.yaml`, `THOMAS_CORE/activations/`,
  `THOMAS_CORE/approvals/`, or anything under `.runtime_governance_state/`.
- **Run state-writing CLIs through the container, never on the host as root.**
  Services run as uid 10001 and mount `.runtime_governance_state/`; a host-side root
  run leaves root-owned files the service can no longer write, and it fails later,
  in a different process, with nothing pointing back at the command that caused it
  (this happened twice on 2026-07-25/26 — a safety-flag activation and an operator
  notify pointer). Use `docker exec thomas-scheduler python scripts/<script>.py …`.
  `state_guard.assert_not_foreign_root_run` refuses the dangerous case at the door
  and `assert_state_writable` refuses to start a service whose state is already
  broken; neither self-heals — if you are told to `chown -R 10001:10001`, that is
  the fix.

## Drive the surface

```powershell
$env:PYTHONUTF8='1'
.venv\Scripts\python -m runtime.mvp_runtime.cli "이 사업 아이디어를 분석해줘: <idea>"
```

Happy path: markdown analysis on stdout, `LEDGER: recorded to ...` on stderr,
exit 0. Evidence: `.runtime_governance_state/runtime_ledger/audit_events.jsonl`
(5 hash-chained events per run; a later run's first event carries the previous
run's tip hash) and `records.jsonl`. Read them with Python or
`Get-Content -Encoding UTF8` (PS5.1 default encoding mojibakes the Korean and
`ConvertFrom-Json` chokes on the longest lines — parse with Python if it matters).

Fail-closed probes that work from the surface:
- Empty/whitespace request → `BLOCKED EMPTY_REQUEST`, exit 3.
- `$env:MVP_HOSTED_PROVIDER='google_ai_studio'` without an activation record →
  `BLOCKED ACTIVATION_MISSING`, exit 2 (safety gate).
- A hand-written
  `.runtime_governance_state/safety_flag_activations/<provider_id>.json` (one grant
  per provider) with a bad field (e.g. `authority_level: "P9"`) →
  `BLOCKED ACTIVATION_MALFORMED`.
  Write it WITHOUT a BOM (`[IO.File]::WriteAllText(..., UTF8Encoding($false))`);
  a BOM is itself rejected. Delete the file after the probe.

## Acceptance checks (CI parity, not verification)

```powershell
.venv\Scripts\python -m pytest tests/ -q
.venv\Scripts\python scripts/run_repository_release_gate.py --full --check-only
```
