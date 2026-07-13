# I0.5.2 Runtime-Authoritative Read-only Entry Boundary v0.1

## Allowed

- read and validate an I0.5.1 Rev3 Runtime Promotion Readiness record;
- build a review-only, single-run Entry Plan;
- produce disabled entry evidence;
- calculate deterministic hashes;
- run local validation and fail-closed fixtures.

## Prohibited

- Runtime-authoritative Kernel invocation;
- Task or Input Bundle creation for Runtime use;
- Current Core creation, Approval, or Activation;
- Action Approval creation, verification, consumption, or reuse;
- Runtime session start;
- Executor handoff;
- model, Tool, Program, network, filesystem-write, external, financial, or Runtime-mutation effects;
- Permission or Authority expansion.

## Stage rule

I0.5.2 may return `READY_FOR_THOMAS_ENTRY_APPROVAL_DESIGN`, but `ready_for_runtime_entry` and every execution/activation effect remain false. A later separately approved phase must define exact Task/Input Bundle binding and atomic Approval consumption before a single Runtime-authoritative read-only entry can be considered.
