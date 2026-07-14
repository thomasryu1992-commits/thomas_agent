# Thomas Agent Active Architecture

**Status:** Architecture Slimming Migration
**Baseline:** I0.5.5
**Runtime-authoritative execution:** Disabled

## Current Active Authority

The existing I0.5.5 authority sources remain active until explicit cutover.

The new Constitution, Governance Policy, Memory Policy, slim Registries, Compatibility Projection, and decomposed Kernel are migration candidates. They do not replace current authority merely by existing, passing validation, or being merged.

## Target Authority Chain

```text
Thomas
↓
Thomas Core
↓
System Constitution
↓
Governance Policy
↓
Thomas Prime
↓
Read-only Runtime Kernel
↓
General Specialist / Validation
↓
Memory Candidate / Audit
```

## Active Development Gate

```bash
python scripts/run_active_gate.py --check-only
```

The Active Gate validates current Core, Contracts, deterministic read-only Runtime, Validation, Audit, Security, and Architecture Slimming invariants.

## Deferred Architecture Gate

```bash
python scripts/run_deferred_architecture_gate.py --check-only
```

This validates future Runtime Entry, Executor, Operations, Control, Supervisor, Scheduler, Threshold, and Sandbox designs.

Passing this Gate grants no Runtime permission or activation.

## Legacy Compatibility Gate

```bash
python scripts/run_legacy_compatibility_gate.py --check-only
```

This protects frozen I0.4 and Core release compatibility.

## Full Compatibility Gate

The existing command remains available during migration:

```bash
python scripts/run_repository_release_gate.py --full --check-only
```

## Migration Rule

> New Active MVP work uses the Active Gate.
> Deferred design does not block ordinary Active MVP iteration unless the deferred scope itself changed.
> Candidate Governance or Registry files require a separate explicit cutover before becoming authoritative.
