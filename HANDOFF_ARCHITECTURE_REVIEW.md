# Handoff — Architecture Review Remediation (A/B/D done, C parked; R3 done)

**Original handoff date:** 2026-07-15 (`main` at `7dc85ea`) · **Updated:** 2026-07-16 (`main` at `680f45c`)
**Purpose:** cross-machine handoff so the next session can continue after finding **D** and Phase **R3**.

The original session ran an independent adversarial review and fixed A and B. A later session
(this update) completed **D** and the whole of **R3** (read-only web-search tool). This file records
what was done, what was investigated, and what is next. Nothing here grants any runtime authority; it
is notes only.

> **Update 2026-07-16:** D and R3 are complete and merged. See "Update" boxes below; the original
> D plan is preserved for the record. Next task is **R4 (Operator/Telegram)**.

---

## Architecture review — the four findings

| | Finding | Status |
|---|---|---|
| **A** | Safety guarantees were *described, not enforced* — `model_invocation`/`network_access` gated only by an env var; the read-only kernel (the only real enforcement) is off the live path. | ✅ **DONE** — PR #13, merged |
| **B** | "Auditable, append-only, hash-chained" agent persisted nothing; failed/blocked runs + the model invocation itself were unaudited. | ✅ **DONE** — PR #14, merged |
| **C** | ~70% of the repo is speculative certification of disabled capabilities. | ⏸️ **PARKED** — premise overstated (see below) |
| **D** | One-concept-one-authority violated: `runtime_effect` + the authority invariant + the P0–P6 rank map are encoded in ~7 places and already drifting. | ✅ **DONE** — PR #16, merged |

Phase work merged in the same update: **R3 read-only web-search tool** — PRs #17 (foundation + gated
adapter), #19 (INTERNAL_READ permission model), #20 (pipeline wiring), #21 (operator activation helper +
docs).

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

## D — collapse the duplicated authority/effect encodings

> **✅ Update 2026-07-16 (PR #16, merged).** Done as planned. `runtime/mvp_runtime/authority.py` now OWNS
> the P0–P6 rank map + `rank_of`, `authority_invariant_holds`, and the REVIEW_ONLY/EVIDENCE_ONLY effect-block
> factories. `planner.py`, `permission.py`, `assignment.py`, `validation.py`, `audit.py`, and `safety_gate.py`
> (a seventh copy found during the work) import from it; local copies deleted. The read-only kernel was left
> frozen with its intentional divergence documented in the module. No closed-schema `const` changed. Records are
> byte-identical, so the audit hash-chain is unchanged. +21 tests; full suite + release gate green. The original
> plan below is preserved for the record.

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

## R3 — read-only web-search tool (✅ done, mock path complete)

Merged across PRs #17/#19/#20/#21. The specialist now runs an authorized read-only web search whose hits become
source-attributed `web_search` evidence and whose use is audited — modelled exactly as the plan prescribed:
an **`INTERNAL_READ` ALLOW action at P1** (not a `tool_request`).

- **`runtime/mvp_runtime/tools.py`** — `SearchTool` protocol, `MockSearchTool` (deterministic, no network),
  `WebSearchTool` (Brave REST adapter behind the A gate — re-checks egress authorization, key by env-var name),
  `select_search_tool()` (mock default; real tool only with a valid `network_access` activation), `run_search()`
  (tamper-evident tool-use evidence record).
- **`permission.build_search_permission_decision`** — the INTERNAL_READ/P1 decision (shared builder via `_ActionSpec`).
- **`pipeline.py` / `worker.py` / `audit.py` / `store.py` / `cli.py`** — execute the search, thread hits into the
  output as evidence, persist the search decision + tool-use record, and add a `TOOL_USED` audit event. Audit chain is
  now 6 events: `TASK_CREATED → PERMISSION_DECIDED → TOOL_USED → MODEL_INVOKED → VALIDATION_COMPLETED → TASK_STATE_CHANGED`.
- **Governance invariant preserved:** the search is a read-only capability, NOT a runtime tool-enablement —
  `allowed_tool_ids` stays empty, every effect stays REVIEW_ONLY/EVIDENCE_ONLY.
- **`scripts/activate_safety_flag.py`** — operator helper to activate the real search (or model) locally in one command.
  See `docs/runtime-contracts/READONLY_SEARCH_TOOL_V0.1.md` for the runbook.

**R3 remaining is operational only:** run the real Brave backend once (write an activation record with the helper, set
`BRAVE_SEARCH_API_KEY`, run with `MVP_SEARCH_TOOL=brave_search`). No code left; it needs a real API key so it is a local,
per-machine, gated step.

## Next task — R4 (Operator/Telegram)

Per the roadmap in CLAUDE.md: **R4 Operator/Telegram** channel → R4.5 Server Deploy → R5 Memory → R6 Scheduler →
R7 Multi-Agent → R8 Controlled Write. C stays parked (low ROI; see above).

## Repo / resume notes

- **`main` is at `680f45c`** with A/B/D + full R2 pipeline + R3 (mock path) merged. All prior stacked R3 branches are
  merged/superseded; the stale `feat/r3-web-search-tool` and `feat/r3-search-permission` origin branches can be deleted.
- **Environment on a fresh machine** (see CLAUDE.md): CI is Python 3.12; `py -3 -m venv .venv`; install
  `requirements-validation.lock` + `pytest`; run `.venv/Scripts/python -m pytest tests/ -q` and
  `.venv/Scripts/python scripts/run_repository_release_gate.py --full --check-only`. Full-run pipeline tests need a local
  Core activation (`.runtime_governance_state/CURRENT_CORE_RELEASE.yaml`) — see CLAUDE.md "Core activation (local)".
  On Windows set `PYTHONUTF8=1` for non-ASCII I/O.
- **Local runtime state** (`.runtime_governance_state/`, incl. the ledger, Core activation, and any safety-flag
  activation) is gitignored and per-machine — it does NOT travel between computers. Re-create as needed.
