# I0.5.3 Exact Entry Authorization and At-Most-Once Boundary v0.1

I0.5.3 is contract and deterministic-preview work only.

## Reused authorities

- I0.5.1 Rev3 Design/Activation Readiness remains the readiness source.
- I0.5.2 Entry Plan remains the bounded-entry source.
- Approval v0.1 remains the Thomas Action Approval evidence contract.
- Existing Task, Core Binding, Input Bundle, Validation, and Audit contracts remain authoritative for their domains.

I0.5.3 does not redefine any of them.

## New scope only

1. exact one-entry Authorization binding;
2. hash-only nonce and strict TTL/resource budgets;
3. at-most-once attempt semantics;
4. one atomic future transition combining Authorization consumption and Session reservation;
5. protected control-plane durable-state requirements for a later implementation;
6. machine-readable linkage to the existing Audit Event v0.1 contract;
7. a fail-closed future precondition that selected limits remain within the exact Task execution budget.

## Still disabled

```yaml
runtime_entry_allowed: false
runtime_session_start_allowed: false
approval_consumption_allowed: false
atomic_compare_and_set_allowed: false
governance_state_write_allowed: false
audit_write_allowed: false
kernel_call_allowed: false
model_invocation_allowed: false
tool_execution_allowed: false
program_execution_allowed: false
network_access_allowed: false
domain_write_allowed: false
workspace_write_allowed: false
core_write_allowed: false
external_action_allowed: false
financial_action_allowed: false
```

## Future I0.5.4 distinction

I0.5.4 may implement a disabled-by-default candidate for the protected control-plane transition. It must keep the Runtime data plane read-only. Any control-plane write capability requires separate Thomas approval, a durable-state design review, crash/recovery tests, and fail-closed audit evidence. The existing Audit Event v0.1 contract remains authoritative; I0.5.3 does not create a duplicate Audit schema.

The exact-path and resource-limit fields are a per-entry narrowing layer. They do not replace Task or Runtime Input Bundle contracts, and the future hot path must prove the limits are less than or equal to the exact Task budget bound by the Task hash.
