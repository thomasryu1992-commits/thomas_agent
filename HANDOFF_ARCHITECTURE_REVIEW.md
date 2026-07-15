# Handoff — Architecture Review Remediation (A/B done, D next)

**Date:** 2026-07-15  ·  **main at handoff:** `7dc85ea`  ·  **Purpose:** cross-machine handoff so the next
session (on another computer) can continue with finding **D**.

This session ran an independent adversarial review of the whole architecture and fixed the two most
serious findings. This file records what was done, what was investigated, and the concrete plan for
the next task (D). Nothing here grants any runtime authority; it is notes only.

---

## Architecture review — the four findings

| | Finding | Status |
|---|---|---|
| **A** | Safety guarantees were *described, not enforced* — `model_invocation`/`network_access` gated only by an env var; the read-only kernel (the only real enforcement) is off the live path. | ✅ **DONE** — PR #13, merged |
| **B** | "Auditable, append-only, hash-chained" agent persisted nothing; failed/blocked runs + the model invocation itself were unaudited. | ✅ **DONE** — PR #14, merged |
| **C** | ~70% of the repo is speculative certification of disabled capabilities. | ⏸️ **PARKED** — premise overstated (see below) |
| **D** | One-concept-one-authority violated: `runtime_effect` + the authority invariant + the P0–P6 rank map are encoded in ~7 places and already drifting. | ⬜ **NEXT** — plan below |

---

## A — Enforced Safety-Flag Gate (PR #13, merged)

Chokepoint so a network-capable provider is only reachable behind a verified activation, never a bare env var.

- **`runtime/mvp_runtime/safety_gate.py`** (single authority): `authorize()` verifies a local, integrity-checked
  activation record at `.runtime_governance_state/safety_flag_activation.json` (gitignored, per-machine) —
  present / self-hash-consistent (tamper-evident) / unexpired / evidence-backed / flags+provider explicit →
  else fail-closed `SafetyGateBlocked`. `build_activation_record()` mints a valid record (computes the hash).
- **`providers.select_provider()`** requires `authorize()` before building a hosted provider;
  **`GoogleAIStudioProvider.generate()`** re-checks the authorization at egress (defense in depth).
- To run the REAL Gemini provider locally you must now WRITE an activation record (env var alone fail-closes) —
  see `build_activation_record()` and CLAUDE.md's Safety-flag bullet.

## B — Durable ledger + audit every outcome (PR #14, merged)

- **`runtime/mvp_runtime/store.py`** — `LedgerStore`: append-only JSONL under `.runtime_governance_state/runtime_ledger/`
  (gitignored): `audit_events` / `records` / `blocks`. Fail-closed `PersistenceError`. `last_audit_hash()` returns the
  chain tip so runs chain across each other (tamper-evident across runs).
- **`audit.py`** — new `MODEL_INVOKED` event (`OTHER`-typed; `audit_event.v0.1` has no model type, so model/tokens/
  finish/`network_egress` ride in reason_codes + a fingerprinted invocation payload) + `build_blocked_audit()`.
- **`pipeline.py`** — every terminal outcome is audited (post-binding fail → blocked trail; pre-binding fail →
  block ledger entry). A COMPLETED run whose evidence cannot be persisted is downgraded to BLOCKED and NOT delivered.
- `worker.py`/`providers.py` carry `network_egress` (Mock=False, hosted=True). `cli.py` persists every run.

**State after A+B:** full suite **196 passed**; repository release gate **PASS**; verified on-disk that two runs
produce a 10-event, intact cross-run hash chain with durable `MODEL_INVOKED` events.

---

## C — Investigated and PARKED (premise was overstated)

The review claimed ~70% / ~55k lines were deletable cruft and named "delete the kernel" and "delete `historical/`".
**Verified against the actual gates — this does not hold:**

- The **read-only kernel** (beyond `integrity.py`+`schema_validation.py`) is **required by the ACTIVE gate**:
  `scripts/validate_slimming_package.py::validate_active_kernel` pins all 10 modules
  (`kernel, loader, preflight, policy, router, worker_port, validation, audit, assembler, orchestrator`). Not removable.
- **`historical/`** is **required by the ACTIVE gate**: `scripts/lib/artifact_boundaries.py` requires its index, README,
  and the 6 `historical/compatibility/**` archived shims to exist.
- **`generated/`** is partly load-bearing (index/README/`generated/docs/CORE_PROJECTION_MAP.yaml` read by the active gate).
- The deferred material is **already governed/indexed/gated** via a completed formal slimming program (PR7–PR10) and a
  canonical index `deferred/DEFERRED_ARCHITECTURE.yaml` (5 families). The repo's own method is *"Separate first,
  deduplicate second, delete last."* The only remaining cleanup is the repo's anticipated **PR #11** (generated/historical
  consolidation after a consumer scan) — a delicate, multi-PR, one-family-at-a-time relocation with low functional payoff.

**The only genuinely safe, self-contained C slice** (if ever desired): retire the `runtime_entry` implementation
candidates `runtime/read_only_entry/` + `runtime/protected_governance_state/` (~3,311 LOC, disabled capability, not
imported by `runtime/mvp_runtime/`). It requires lockstep edits to `deferred/DEFERRED_ARCHITECTURE.yaml`
(`implementation_candidates`), the deferred validators/builders `scripts/validate_i0_5_2/3/4/5*` + `build_i0_5_2/3/5*`,
and CI patterns in `scripts/gate_matrix.py` (L90–91) — and triggers the full CI matrix. Deferred, not urgent.

**Conclusion:** C offers low ROI right now; do not chase mass deletion. Prefer D.

---

## D — NEXT TASK: collapse the duplicated authority/effect encodings

**Problem (real, active-path, already drifting).** Two safety predicates + the rank map are re-encoded in ~7 places:

- **`runtime_effect = REVIEW_ONLY / EVIDENCE_ONLY + all grant flags false`** appears independently in:
  `governance/GOVERNANCE_POLICY.yaml`, the `permission_decision.v0.3` / `audit_event.v0.1` schemas (hardcoded `const`),
  `scripts/validate_permission_approval_contracts.py`, `runtime/mvp_runtime/permission.py`, `.../validation.py`,
  `.../audit.py`, and `runtime/read_only_kernel/preflight.py`.
- **Authority invariant** `required <= effective <= granted <= ceiling` in `runtime/mvp_runtime/permission.py`,
  `.../assignment.py`, `runtime/read_only_kernel/preflight.py`, and the schema — and they already **disagree**
  (the kernel additionally forces `required == P2`, `ceiling <= P3`, Task==Assignment equality; the mvp path only
  checks the plain inequality).
- **P0–P6 rank map** copy-pasted as `_LEVEL_RANK` in `planner.py`, `permission.py`, `assignment.py`
  (and `AUTHORITY_ORDER` in the kernel).

**Proposed approach (scoped to `runtime/mvp_runtime/`, low-risk):**
1. Create a shared module, e.g. `runtime/mvp_runtime/authority.py`, that OWNS: the P0–P6 rank map + comparison helpers,
   the authority-invariant check (`required <= effective <= granted <= ceiling`), and the effect-block constants
   (`REVIEW_ONLY`, `EVIDENCE_ONLY` with all grant flags false) as canonical dicts/factory.
2. Refactor `planner.py`, `permission.py`, `assignment.py`, `audit.py`, `validation.py` to import from it (delete their
   local copies). Keep each record's *shape* identical (the closed schemas are unchanged).
3. **Do NOT modify the read-only kernel** (`preflight.py` etc.) — it is frozen; leave its copy and just DOCUMENT the
   intentional divergence (kernel enforces the stricter review-only invariant; it is off the live path). One authority
   *for the live runtime*; the kernel remains its own authority for the replay path.
4. **Do NOT change committed closed-schema `const` values** — that is a governance change needing explicit Thomas
   approval. The schema stays the record-shape authority; the shared module aligns the *Python* to it (removing the
   duplicated Python literals, not the schema).

**Why this is safe:** the existing tests already assert the effect/authority invariants
(`test_mvp_runtime_permission.py`, `_assignment.py`, `_audit.py`, `_pipeline.py::test_common_safety_invariants_hold...`),
so they guard the refactor. Target: no behavior change, full suite green, release gate PASS.

**Out of scope for D:** touching the kernel, editing schemas/governance policy, and finding C.

---

## Repo / resume notes

- **Branches on origin:** `main` (`7dc85ea`, A+B merged). `feat/r3-web-search-tool` (R3 read-only search-tool foundation,
  mock only — pushed this session, **not** merged, and now behind main → **rebase onto main before continuing R3**).
- **R3 remaining** (after D or whenever): real search adapter (network → must go through the A gate), wire search into the
  worker, and the `search.readonly` tool activation. Note `tool_request.v0.1` is the wrong contract for a read-only search
  (it's an executor-handoff review packet); model the search as an `INTERNAL_READ` ALLOW action instead.
- **Environment on a fresh machine** (see CLAUDE.md): CI is Python 3.12; `py -3 -m venv .venv`; install
  `requirements-validation.lock` + `pytest`; run `.venv/Scripts/python -m pytest tests/ -q` and
  `.venv/Scripts/python scripts/run_repository_release_gate.py --full --check-only`. Full-run pipeline tests need a local
  Core activation (`.runtime_governance_state/CURRENT_CORE_RELEASE.yaml`) — see CLAUDE.md "Core activation (local)".
  On Windows set `PYTHONUTF8=1` for non-ASCII I/O.
- **Local runtime state** (`.runtime_governance_state/`, incl. the new ledger + any safety-flag activation) is gitignored
  and per-machine — it does NOT travel between computers. Re-create as needed.
