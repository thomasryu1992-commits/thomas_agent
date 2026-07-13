# Runtime Entry At-Most-Once Atomic Transition Contract v0.1

**Schema:** `runtime_entry_atomic_transition_preview.v0.1`
**Phase:** `I0.5.3`
**Status:** `Review-only atomic transition design`
**Owner:** `Thomas`

## Purpose

Define one future atomic control-plane transition that consumes an exact Runtime Entry Authorization and reserves one Runtime Session as one all-or-none operation.

I0.5.3 produces a deterministic preview only. It performs no compare-and-set, no state write, no Approval consumption, and no Session reservation.

## Why consumption and reservation are one contract

Separate consumption and reservation contracts can create split-brain states:

```text
Approval consumed
+
Session not reserved
```

or:

```text
Session reserved
+
Approval still reusable
```

Therefore I0.5.3 intentionally defines one transition:

```text
Expected:
Authorization = UNUSED
Session = NOT_RESERVED

Atomic target:
Authorization = CONSUMED
Session = RESERVED
```

The transition is all-or-none.

## At-most-once semantics

The guarantee is **at-most-one entry attempt**, not exactly-once success.

The future implementation must consume before the Kernel call. If the process crashes after durable consumption but before the Kernel starts, the Authorization remains terminal and cannot be reused. A new Thomas Action Approval is required.

Ambiguous outcomes fail closed as:

```text
CONSUMED_OR_UNKNOWN_FAIL_CLOSED
```

No automatic retry may reuse the same Authorization, Approval, nonce, or Action fingerprint.

## Future protected state boundary

A real I0.5.4 implementation will require a protected local governance state store for only:

- Authorization consumption state;
- Runtime Session reservation state;
- append-only Audit state.

Allowed future control-plane writes do not permit domain, workspace, Task source, Input Bundle, Core, Tool, Program, external-system, or financial writes.

In I0.5.3 all writes remain disabled.

## Required atomic properties

- durable compare-and-set;
- process-restart persistence;
- expected-state match;
- nonce uniqueness;
- exact Action fingerprint match;
- exact Task/Input Bundle/Current Core/component bindings;
- TTL and resource-cap revalidation immediately before transition;
- Kill Switch and Runtime boundary revalidation;
- all-or-none consumption and Session reservation;
- terminal no-reuse after success, failure, timeout, crash, or ambiguous outcome;
- append-only Audit linkage.

## Audit linkage

The existing `AUDIT_EVENT_CONTRACT_V0.1.md` remains the Audit schema source of truth. I0.5.3 only requires four future hash-chained events: authorization checked, consumption committed, Session reserved, and attempt terminated. No Audit write occurs in I0.5.3.

## Preview outcomes

```text
BLOCKED_NOT_ELIGIBLE
ELIGIBLE_FOR_I0_5_4_IMPLEMENTATION_REVIEW
```

Neither outcome performs or authorizes the transition.
