# Runtime Entry Durable CAS Candidate Contract v0.1

**Schema:** `runtime_entry_durable_transition_result.v0.1`
**Phase:** `I0.5.4`
**Status:** `Synthetic-Test-Only Candidate`
**Owner:** `Thomas`

## Purpose

Implement and test the I0.5.3 all-or-none transition design using a durable local transaction while keeping the real Runtime Entry path disabled.

## Required transition

```text
Expected
Authorization = UNUSED
Authorization version = expected_version
Session = NOT_RESERVED
Nonce = unused

BEGIN IMMEDIATE
↓
Insert Session(RESERVED)
↓
CAS Authorization(UNUSED, version N)
→ Authorization(CONSUMED, version N+1)
↓
Append audit events
↓
Insert Transition Receipt
↓
COMMIT
```

All writes must commit together or roll back together.

## Current candidate restrictions

The transition accepts only an I0.5.3 record with:

```text
record_scope = SYNTHETIC_TEST_ONLY
status = APPROVED_NOT_CONSUMED_REVIEW_ONLY
Approval v0.1 real consumption supported = false
usable_for_runtime_entry = false
```

The candidate therefore proves transaction mechanics only. It cannot consume a real Action Approval.

## At-most-once semantics

The guarantee is:

```text
At-most-once transition attempt
```

not:

```text
Exactly-once successful Runtime execution
```

After a durable commit, Authorization reuse is forbidden even when a process crashes before any future Kernel call.

## Current result meanings

### `COMMITTED_SYNTHETIC_TEST_ONLY`

- synthetic Authorization state changed from `UNUSED` to `CONSUMED`;
- synthetic Session state changed from `NOT_RESERVED` to `RESERVED`;
- Transition Receipt and audit rows committed in the same SQLite transaction;
- no real Approval was consumed;
- no Runtime Session started;
- no Kernel was called.

### `BLOCKED_FAIL_CLOSED`

- precondition, hash, nonce, version, duplicate, expiry, or crash-before-commit check failed;
- transaction was rolled back;
- no state transition was committed.

## No automatic retry

A committed transition followed by a process crash becomes a recovery-review case. The candidate must never reset the Authorization to `UNUSED` or start the Session automatically.
