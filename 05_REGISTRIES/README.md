# Runtime Registries

**Status:** `MVP Registry Foundation v0.1`
**Owner:** `Thomas`

This folder contains machine-readable registration sources for Runtime resources.

Current MVP rule:

> A Role may use only a Program or Tool that is registered, active, enabled, allowed by its Role Definition, allowed by its Role Assignment, within authority, within budget, and allowed by the current Permission Decision.

The initial registries intentionally contain no active Runtime resources.

Candidate entries are design placeholders only and do not grant execution permission.

## I0.5 Read-only Runtime Components

`I0_5_READ_ONLY_RUNTIME_COMPONENTS_REVIEW_ONLY.yaml` lists the I0.5 Kernel and deterministic Worker as `candidate` development-replay components. It is not a Runtime source of truth and cannot activate Runtime, Tools, Programs, Executors, external actions, or financial actions.
