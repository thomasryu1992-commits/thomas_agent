# Disabled Single Read-only Entry Integration Candidate Contract v0.1

**Schema:** `disabled_single_read_only_entry_integration_candidate.v0.1`
**Phase:** `I0.5.5`
**Status:** Review-only disabled integration candidate
**Owner:** Thomas

## Purpose

I0.5.5 connects the existing I0.5.2 Entry Plan boundary, I0.5.3 Exact Entry Authorization, I0.5.4 protected-state evidence, the existing disabled Entry Adapter, and the I0.5 Kernel identity into one hash-bound pre-Kernel integration record.

It does **not** create a new Approval type, Permission model, Audit model, Session runtime, or Kernel executor.

## Integration order

```text
Exact Entry Authorization
→ optional matching synthetic durable-transition evidence
→ existing disabled Entry Adapter boundary
→ exact Kernel invocation candidate envelope
→ BLOCKED_DISABLED_INTEGRATION_CANDIDATE
```

The final result is always blocked in I0.5.5. `candidate_envelope_created=true` means only that exact Task, Input Bundle, Current Core, Core Context, component hashes, read paths, limits, and output schemas were copied into a reviewable envelope. It never means Runtime handoff or Kernel invocation.

## Reused ownership

- Entry readiness: I0.5.1 Rev3;
- Entry Plan and disabled Adapter: I0.5.2;
- exact Authorization and at-most-once scope: I0.5.3;
- durable state and recovery semantics: I0.5.4;
- Approval meaning: `approval.v0.1`;
- Audit meaning: `audit_event.v0.1`;
- Runtime Kernel: I0.5 read-only Kernel candidate.

## Fail-closed rules

Block when exact bindings, component identities, hashes, resource limits, expected schemas, synthetic transition linkage, or disabled Adapter boundaries differ. Real Approval consumption remains unavailable. Synthetic evidence is never Runtime-eligible.

## Non-goals

No real Approval verification/consumption, production state write, Runtime Session start, Runtime handoff, Kernel call, model, Tool, Program, Executor, network, Data Plane write, external action, financial action, automatic retry, or automatic resume.
