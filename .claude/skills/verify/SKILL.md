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
- A hand-written `.runtime_governance_state/safety_flag_activation.json` with a
  bad field (e.g. `authority_level: "P9"`) → `BLOCKED ACTIVATION_MALFORMED`.
  Write it WITHOUT a BOM (`[IO.File]::WriteAllText(..., UTF8Encoding($false))`);
  a BOM is itself rejected. Delete the file after the probe.

## Acceptance checks (CI parity, not verification)

```powershell
.venv\Scripts\python -m pytest tests/ -q
.venv\Scripts\python scripts/run_repository_release_gate.py --full --check-only
```
