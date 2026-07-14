# PR #10 — Deferred Architecture Compression

**Status:** Prepared for ordered application after PR #9
**Runtime behavior change:** None
**Activation authority added:** None

## Result

PR #10 creates one canonical Deferred Architecture index and five concise requirement families:

```text
deferred/DEFERRED_ARCHITECTURE.yaml
├─ runtime_entry
├─ executor
├─ operations
├─ control_channel
└─ sandbox
```

I0.5.1-I0.5.5 become one Deferred Runtime Entry family. The numbered artifacts remain detailed historical/design evidence, not five current architecture stages.

## Validation compression

The Deferred Gate changes from eight top-level checks to one canonical harness:

```text
scripts/validate_deferred_architecture.py
```

The harness owns structure, family boundaries, contract/schema parity, Active/Deferred separation, and subordinate detailed validators. Existing phase validators remain available for deep evidence checks but no longer own Gate composition.

## Active/Deferred record split

`ValidationResult` and `AuditEvent` remain Active evidence records. `ExecutionRequest` and `ExecutionResult` are Deferred Executor preview records. The shared validator now supports explicit `--scope active|deferred|all` operation.

## Path strategy

This PR uses index-first, path-preserving compression. Existing implementation candidates, contracts, schemas, examples, fixtures, and validators remain in place to preserve references and test history. PR #11 may archive or remove them only after consumer scans and retirement criteria pass.

## Safety boundary

No Runtime Entry, Approval consumption, protected-state write, Runtime Session, Kernel handoff, Executor registration/enablement, Tool/Program execution, daemon, Scheduler, Control dispatch, Sandbox execution, external action, financial action, Permission expansion, Authority expansion, or Core activation is enabled.
