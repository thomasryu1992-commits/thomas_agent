# I0.5.4 Protected Governance State Boundary v0.1

I0.5.4 implements a **disabled, synthetic-test-only** local state candidate. It validates durable transaction and recovery mechanics; it does not enable Runtime Entry.

## Allowed now

- initialize SQLite state inside an explicit temporary test directory;
- register I0.5.3 synthetic Authorization fixtures;
- commit one synthetic all-or-none Authorization-consumption + Session-reservation transaction;
- reopen the database after process-boundary simulation;
- inspect integrity, linkage, receipts, and audit chain;
- prove replay, duplicate nonce, stale version, and crash-before-commit fail closed.

## Still disabled

```text
real Action Approval verification = false
real Action Approval consumption = false
Runtime-authoritative state write = false
Runtime Session start = false
Kernel call = false
model / Tool / Program / Executor = false
network = false
Task / Input Bundle / Current Core write = false
workspace / domain write = false
external / financial action = false
automatic retry = false
automatic resume = false
```

## Architectural precedence

I0.5.4 is subordinate to:

1. Thomas Core;
2. Task, Authority, Permission, and Approval contracts;
3. I0.5.1 Design/Activation Readiness;
4. I0.5.2 Entry Plan;
5. I0.5.3 Exact Entry Authorization and Atomic Transition design.

It narrows implementation mechanics only. It cannot grant Authority, Permission, Approval, Runtime activation, or Runtime Entry permission.

## Non-duplication rule

- Approval remains owned by `approval.v0.1`;
- exact Entry scope remains owned by `runtime_entry_authorization.v0.1`;
- intended transition semantics remain owned by the I0.5.3 Atomic Transition contract;
- audit semantics remain owned by `audit_event.v0.1`;
- I0.5.4 owns only the protected local storage transaction and recovery implementation candidate.
