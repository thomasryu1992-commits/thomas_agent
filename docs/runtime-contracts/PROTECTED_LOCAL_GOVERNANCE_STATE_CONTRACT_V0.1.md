# Protected Local Governance State Contract v0.1

**Phase:** `I0.5.4`
**Status:** `Disabled Runtime / Synthetic-Test Candidate`
**Owner:** `Thomas`

## Purpose

Define the smallest durable local control-plane state required to prove an at-most-once Runtime Entry transition without making the Runtime Data Plane writable.

```text
Runtime Data Plane
Task / Input Bundle / Current Core / Workspace
→ read-only

Protected Control Plane
Synthetic Authorization state / Session reservation / Audit chain
→ test-only durable state candidate
```

## Storage candidate

The candidate uses Python standard-library SQLite with:

- `BEGIN IMMEDIATE` transaction boundary;
- rollback-journal mode (`DELETE`);
- `synchronous=FULL`;
- foreign-key enforcement;
- one unique Authorization row;
- one unique Session row per Authorization;
- one unique Transition Receipt;
- append-only hash-chained `audit_event.v0.1` records stored as rows.

Because `audit_event.v0.1` has a closed `event_type` enum, I0.5.4 reuses `event_type: OTHER` and places the specific Runtime Entry subtype in `event.reason_codes`. No new Audit schema is introduced.

SQLite is used because one local database transaction can update Authorization consumption, Session reservation, Transition Receipt, and Audit evidence atomically. Separate files would create a split-brain risk.

## Current enablement boundary

The implementation refuses to open unless all are true:

```text
record_scope = SYNTHETIC_TEST_ONLY
allow_test_writes = true
caller provides an explicit state_root
```

The component registry remains:

```text
enabled = false
runtime_authoritative = false
runtime_use_allowed = false
```

No Repository apply script creates a state database. Focused tests create databases only inside temporary directories.

## Permitted current effect

Only a focused synthetic test may write:

- synthetic Authorization state;
- synthetic Session reservation;
- synthetic Transition Receipt;
- synthetic audit rows.

These writes are not Action Approval consumption, Runtime Session start, Runtime mutation, or Runtime Entry.

## Prohibited effects

- no real Approval verification or consumption;
- no Runtime-authoritative state write;
- no Kernel call;
- no model, Tool, Program, Executor, or network call;
- no Task, Input Bundle, Current Core, workspace, domain, external, or financial write;
- no automatic retry or automatic resume after an ambiguous outcome;
- no secret value storage.

## Mutable-state location

The future protected state root is `.runtime_governance_state/`. It is mutable operational state, not Gate-owned source, not a Release artifact, and not a Core semantic artifact. It must remain ignored by Git and excluded from Repository source fingerprints.
