# Architecture Review — Findings and Remediation Record

**Status:** Record / notes only  ·  **Normative authority:** None  ·  **Owner:** Thomas

**Original handoff date:** 2026-07-15  ·  **Merged into `main`:** 2026-07-16

This file records an independent adversarial review of the whole architecture: the four findings it
raised, what was fixed, and — for the one finding that was **not** acted on — the evidence for why.
It grants no runtime authority. For current status and roadmap, `CLAUDE.md` is the live source; this
document is the standing reference for **why finding C is parked**.

---

## The four findings

| | Finding | Status |
|---|---|---|
| **A** | Safety guarantees were *described, not enforced* — `model_invocation`/`network_access` gated only by an env var; the read-only kernel (the only real enforcement) is off the live path. | ✅ **DONE** — PR #13 |
| **B** | "Auditable, append-only, hash-chained" agent persisted nothing; failed/blocked runs + the model invocation itself were unaudited. | ✅ **DONE** — PR #14 |
| **C** | ~70% of the repo is speculative certification of disabled capabilities. | ⏸️ **PARKED** — premise overstated (evidence below) |
| **D** | One-concept-one-authority violated: `runtime_effect` + the authority invariant + the P0–P6 rank map encoded in ~7 places and already drifting. | ✅ **DONE** — PR #16 |

---

## A — Enforced Safety-Flag Gate (PR #13)

Chokepoint so a network-capable provider is only reachable behind a verified activation, never a bare env var.

- **`runtime/mvp_runtime/safety_gate.py`** (single authority): `authorize()` verifies a local, integrity-checked
  activation record at `.runtime_governance_state/safety_flag_activation.json` (gitignored, per-machine) —
  present / self-hash-consistent (tamper-evident) / unexpired / evidence-backed / flags+provider explicit →
  else fail-closed `SafetyGateBlocked`. `build_activation_record()` mints a valid record (computes the hash).
- **`providers.select_provider()`** requires `authorize()` before building a hosted provider;
  **`GoogleAIStudioProvider.generate()`** re-checks the authorization at egress (defense in depth).
- To run the REAL hosted provider locally you must WRITE an activation record (env var alone fail-closes) —
  see `scripts/activate_safety_flag.py` and CLAUDE.md's Safety-flag bullet.

## B — Durable ledger + audit every outcome (PR #14)

- **`runtime/mvp_runtime/store.py`** — `LedgerStore`: append-only JSONL under `.runtime_governance_state/runtime_ledger/`
  (gitignored): `audit_events` / `records` / `blocks`. Fail-closed `PersistenceError`. `last_audit_hash()` returns the
  chain tip so runs chain across each other (tamper-evident across runs).
- **`audit.py`** — `MODEL_INVOKED` event (`OTHER`-typed; `audit_event.v0.1` has no model type, so model/tokens/
  finish/`network_egress` ride in reason_codes + a fingerprinted invocation payload) + `build_blocked_audit()`.
- **`pipeline.py`** — every terminal outcome is audited (post-binding fail → blocked trail; pre-binding fail →
  block ledger entry). A COMPLETED run whose evidence cannot be persisted is downgraded to BLOCKED and NOT delivered.
- `worker.py`/`providers.py` carry `network_egress` (Mock=False, hosted=True). `cli.py` persists every run.

## D — Collapsed the duplicated authority/effect encodings (PR #16)

The problem was real and on the active path: `runtime_effect` (REVIEW_ONLY / EVIDENCE_ONLY + all grant flags false),
the authority invariant (`required <= effective <= granted <= ceiling`), and the P0–P6 rank map were each re-encoded
in several modules — and had already drifted.

**What was done:** `runtime/mvp_runtime/authority.py` now OWNS the P0–P6 rank map + comparison helpers, the
authority-invariant check, and the effect-block constants; `planner.py`, `permission.py`, `assignment.py`,
`audit.py`, `validation.py` import from it instead of keeping local copies. Record *shapes* are unchanged.

Two constraints were deliberately honored and remain in force:

1. **The read-only kernel was not modified** — it is frozen. It keeps its own stricter copy (it additionally forces
   `required == P2`, `ceiling <= P3`, Task==Assignment equality) and the divergence is documented as intentional:
   one authority *for the live runtime*; the kernel remains its own authority for the replay path.
2. **Committed closed-schema `const` values were not changed** — that would be a governance change requiring explicit
   Thomas approval. The schema stays the record-shape authority; the shared module aligns the *Python* to it.

---

## C — Investigated and PARKED (premise was overstated)

The review claimed ~70% / ~55k lines were deletable cruft and named "delete the kernel" and "delete `historical/`".
**Verified against the actual gates — this does not hold** (re-verified 2026-07-16, still accurate):

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
and CI patterns in `scripts/gate_matrix.py` — and triggers the full CI matrix. Deferred, not urgent.

**Conclusion:** C offers low ROI; do not chase mass deletion. The dormant material is governed and indexed, not loose.

---

## Environment notes

Superseded by `CLAUDE.md` (setup, commands, Core activation, safety-flag activation) — kept here only as a pointer.
Local runtime state (`.runtime_governance_state/`, incl. the ledger and any safety-flag activation) is gitignored and
per-machine: it does NOT travel between computers. Re-create it on each machine.
