# Deterministic Contract Inspection Worker Contract v0.1

**Worker ID:** `kernel.contract_inspection.readonly`
**Worker Version:** `0.1.0`
**Status:** `Built-in I0.5 Development Replay Worker`
**Owner:** `Thomas`

## 1. Purpose

This Worker exercises the Role/Assignment/Output path without using an LLM, Tool, Program, network, external source, or filesystem mutation.

It summarizes only the explicit Task and Role Assignment records already loaded by the Kernel.

## 2. Inputs

- one validated `task.v0.3` snapshot;
- one validated `role_assignment.v0.2` snapshot;
- one deterministic `created_at` value supplied by the Kernel.

## 3. Output

The Worker produces one `agent_output.v0.2` record containing:

- explicit Task and Role facts;
- contract-bound inferences;
- assumptions and uncertainty;
- no-effect limitations;
- a review-only recommendation;
- Role-specific key findings.

The output initially requires contract validation and is finalized only after the Kernel's automatic no-effect and lineage checks pass.

## 4. Capability Boundary

```yaml
model_calls: 0
tool_calls: 0
program_calls: 0
network_calls: 0
filesystem_writes: 0
external_actions: 0
```

The Worker is not a general-purpose reasoning model and must not claim domain research, current facts, external verification, or live Runtime state.

## 5. Final Rule

> The Worker exists to validate orchestration and lineage, not to replace a future approved model or specialist Runtime.
