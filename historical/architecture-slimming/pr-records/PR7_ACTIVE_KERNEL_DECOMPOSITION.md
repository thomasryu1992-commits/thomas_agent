# PR #7 — Active Kernel In-place Decomposition

**Status:** Implementation package
**Baseline:** I0.5.5 plus PR #6 Documentation and Gate Consolidation
**Runtime-authoritative execution:** Disabled
**Behavioral intent:** Structural decomposition with deterministic output and fail-closed parity

## 1. Scope

This change decomposes the current active `runtime/read_only_kernel/kernel.py` in place. It does not promote `runtime/kernel_slim/` as a second active Runtime and does not introduce a new Runtime mode.

The active package is separated into:

```text
kernel.py       → stable public facade and exception mapping
loader.py       → repository-bounded immutable input loading
preflight.py    → schema, lineage, authority, Registry, and no-effect checks
policy.py       → non-owning adapter over already-validated Permission/Authority records
router.py       → validated ROLE route selection
worker_port.py  → deterministic Worker invocation boundary
validation.py   → ValidationResult construction
audit.py        → transition and validation AuditEvent construction
assembler.py    → completed/blocked Run assembly and explicit no-effect evidence
orchestrator.py → execution order and data flow only
```

Existing `io.py`, `integrity.py`, `schema_validation.py`, and `worker.py` remain the active low-level implementations.

## 2. Compatibility

The following public entrypoints remain stable:

```python
from runtime.read_only_kernel.kernel import (
    KERNEL_ID,
    KERNEL_VERSION,
    KernelBlocked,
    ReadOnlyRuntimeKernel,
    run_bundle,
)
```

`KERNEL_ID` and `KERNEL_VERSION` remain unchanged so the structural refactor does not alter deterministic run identity by itself.

## 3. Safety Boundary

This PR does not enable:

- Runtime-authoritative entry;
- model invocation;
- Tool or Program execution;
- network access;
- filesystem writes;
- Approval consumption;
- Executor handoff;
- Scheduler or Control Channel dispatch;
- Runtime mutation;
- Permission or Authority expansion;
- external or financial effects;
- Core activation.

The existing positive replay, schema validation, mutation fixtures, blocked-run behavior, lifecycle evidence, and Audit chain remain the behavioral acceptance source.

## 4. Validation

```bash
python scripts/validate_i0_5_read_only_runtime.py
python scripts/validate_active_kernel_decomposition.py
python -m unittest tests.test_active_kernel_decomposition -v
python scripts/run_architecture_gate.py --scope active --check-only
python scripts/run_repository_release_gate.py --full --check-only
```

## 5. Out of Scope

- Active Role/Program/Tool Registry slimming → PR #8
- Governance atomic cutover → PR #9
- Deferred architecture compression → PR #10
- Generated/Historical cleanup → PR #11
