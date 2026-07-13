# Runtime Entry Crash Recovery Contract v0.1

**Schema:** `runtime_entry_recovery_report.v0.1`
**Phase:** `I0.5.4`
**Status:** `Read-Only Recovery Inspector Candidate`
**Owner:** `Thomas`

## Purpose

Inspect the protected local governance state after process restart without changing any state or resuming a Runtime Entry.

## Recovery checks

- SQLite `integrity_check`;
- Authorization ↔ Session bidirectional linkage;
- Transition Receipt presence for every reserved Session;
- audit sequence continuity;
- audit previous-hash linkage;
- audit payload hash verification;
- consumed Authorization without Session;
- Session without Authorization;
- `UNUSED` Authorization with Session;
- committed Authorization with a still-`RESERVED` Session.

## Outcomes

```text
CLEAN_NO_PENDING_SESSION
```

No inconsistent or pending Session state exists.

```text
MANUAL_REVIEW_REQUIRED_NO_REUSE
```

A durable `CONSUMED + RESERVED` pair exists. This is the expected state after a crash occurring after the atomic commit but before any future Kernel completion evidence. Authorization reuse and automatic Session resume remain forbidden. A new Thomas decision is required.

```text
FAIL_CLOSED_INCONSISTENT_STATE
```

Integrity, linkage, receipt, or audit-chain evidence is inconsistent. Runtime Entry remains blocked.

## Current effect

The recovery inspector is read-only. It does not:

- change Authorization or Session state;
- consume Approval;
- reserve or start a Session;
- call the Kernel;
- retry work;
- write audit rows;
- modify Runtime, Core, Task, Input Bundle, workspace, domain, external, or financial state.
