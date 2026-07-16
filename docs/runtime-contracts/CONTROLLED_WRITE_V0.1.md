# Controlled Write v0.1 (R8)

**Status:** Active runtime capability, OFF by default
**Owner:** Thomas
**Authority:** None. This document describes an implementation; the canonical Governance
Policy (`governance/GOVERNANCE_POLICY.yaml`) owns the rules it obeys.

The first capability that leaves a durable artifact of the agent's own choosing, and the
runtime's **first EXECUTE_AND_REPORT action** — everything before it (analysis, search,
validation) is ALLOW-tier. Implemented in `runtime/mvp_runtime/workspace.py`.

## Zero new governance surface

The governance already modelled this end to end. R8 adds **no** contract, schema,
registry, or gate:

| Need | Already existed |
|---|---|
| Action scope | `WORKSPACE_REVERSIBLE_WRITE` in the `permission_decision.v0.3` enum |
| Its disposition | `policy_dispositions.EXECUTE_AND_REPORT` in the Governance Policy |
| Kill-switch binding | `kill_switch.kill_blocks: tool_write` |
| Audit shape | `audit_event.v0.1` `OTHER` + subtype in `reason_codes` (the I0.5.4 precedent) |
| Authority ladder | `authority.levels` P3 CREATE / P4 INTERNAL_MODIFY |

The only code change outside the new module is one narrow widening of the disposition
gate in `permission.py` (see *The permission-tier expansion*).

## What it does

`--write-output PATH` on the CLI (or `write_path=` on `run_task`) creates the rendered
response as a file at a **workspace-relative** path. The write is planned as its own
governed action with its own PermissionDecision, performed only if validation passes, and
reported.

```
python -m runtime.mvp_runtime.cli --write-output reports/idea.md "이 사업 아이디어를 분석해줘: ..."
```

## Safety model

**Create-only.** A write that would replace existing bytes fails closed (`TARGET_EXISTS`),
enforced both by an `exists()` check and by opening with mode `"x"` — so a file appearing
between the check and the write cannot be clobbered either. Nothing is ever destroyed, so
the write is reversible by deleting what it created: the reversibility that
`WORKSPACE_REVERSIBLE_WRITE` is named for is **structural**, not a promise backed by a
backup mechanism.

**Why create-only, and why P3.** The Governance Policy's ladder is `P3: CREATE` /
`P4: INTERNAL_MODIFY`. Creating a new file is CREATE — P3, within `general.specialist`'s
P3 ceiling. Modifying an existing file is INTERNAL_MODIFY — P4, **above** that ceiling, so
the authority invariant refuses it outright. P4 is reachable only through Thomas Prime's
conditional authority, which additionally requires "a registered Program or Tool"
(`THOMAS_PRIME_CHARTER` §10) — and the active gate
(`scripts/validate_slimming_package.py::validate_active_kernel`... `must remain disabled`)
pins every registry entry to `enabled: false`, while `DISABLED_RESOURCE_EXECUTION` is a
BLOCK scope. A P4 write is therefore **not reachable** without an architecture change far
larger than R8. Create-only at P3 is the capability that actually fits the governance.

**Confined.** The target must resolve inside `workspace/` (repo root, gitignored).
Rejected, each with its own reason code: `ABSOLUTE_PATH` (including drive letters and UNC
paths — checked with `PureWindowsPath` semantics on every platform, so a POSIX-looking
`/etc/x` is refused on Windows too), `PATH_ESCAPE` (`..` traversal **and** symlink escape),
`INVALID_PATH` (control characters), `PATH_TOO_LONG`, `EMPTY_PATH`. Containment is verified
on the **resolved** path against the **resolved** base, so a symlinked parent cannot smuggle
a write out and a workspace behind a symlink does not false-negative.

**Kill-switch bound** (`kill_blocks: tool_write`). `run_write` — the chokepoint both
writers pass through — refuses while PAUSED or KILLED. A corrupt control state reads as
KILLED, so uncertainty refuses too.

**Gated, OFF by default.** `select_writer` returns the `DryRunWriter` — which computes the
write, its size, and its content hash while touching nothing — unless the caller opts in
via `MVP_WORKSPACE_WRITER=real` **and** the Safety-Flag Gate authorizes the new
`filesystem_write` flag against a local, integrity-checked activation record. The env var
alone fails closed. `RealWorkspaceWriter` re-asserts its authorization at the moment it
touches disk, so a directly-constructed writer cannot bypass the gate.

To enable locally (per-machine, gitignored, never committed):

```
python scripts/activate_safety_flag.py --provider-id workspace.writer \
  --flags filesystem_write --authority-level P3 --reason "..."
```

**Validation gates the write.** Only a PASSING result is written — under the same stricter
automatic/independent outcome that gates delivery. A rejected analysis leaves no artifact.

**Content is metadata-only in records.** The write record and audit event carry the target,
byte count, and `content_sha256` — never the content. Nothing a model wrote can leak into
the audit trail.

**`workspace/` is gitignored**, so a controlled write can never reach a commit.

## The permission-tier expansion

R8 is the first action whose disposition is not ALLOW. `permission.py` previously refused
everything else outright. It now admits `EXECUTE_AND_REPORT`, but narrowly:

- Only dispositions with an implementation **and a reporting path** (`_EXECUTABLE_DISPOSITIONS`).
  `APPROVAL_REQUIRED` and `BLOCK` stay refused — the MVP has no approval flow, so an
  APPROVAL_REQUIRED action could never become authorized and must not proceed.
- Only the EXECUTE_AND_REPORT **scopes the runtime implements** (`_EXECUTE_AND_REPORT_SCOPES`
  = `{WORKSPACE_REVERSIBLE_WRITE}`). The other scopes governance prices at
  EXECUTE_AND_REPORT (`GIT_AGENT_BRANCH_CHANGE`, `LOCAL_BUILD_TEST`, …) do **not** ride in
  on the widening.

The disposition is read from the canonical Governance Policy, never chosen locally, and the
record still carries `runtime_effect: REVIEW_ONLY` with every grant flag false — acting is
not granting.

This crossing was approved by Thomas explicitly, as a permission-tier expansion. Per
CLAUDE.md's Safety-Flag Gate philosophy, a good test result is never an auto-approval for
the next capability.

## The "report" half

EXECUTE_AND_REPORT obligates reporting. The write is never silent:

- A `WORKSPACE_WRITE` audit event (durable, hash-chained), referencing the
  WORKSPACE_REVERSIBLE_WRITE PermissionDecision that authorized it. Its `reason_codes`
  distinguish a real write (`FILESYSTEM_WRITE`) from a dry run (`NO_FILESYSTEM_WRITE`).
- The grant itself is persisted to the ledger — the decision the action was taken under,
  not just the event reporting it.
- `result["write"]` surfaces it to the caller; the CLI prints it to stderr.

## Deliberately excluded

Overwrite and delete (P4 / `DESTRUCTIVE_CHANGE` → APPROVAL_REQUIRED, and no approval flow
exists), writing outside `workspace/`, registry tool activation, and any executor handoff.
