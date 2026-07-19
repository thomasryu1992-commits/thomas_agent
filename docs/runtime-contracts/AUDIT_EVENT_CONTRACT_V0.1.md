# Audit Event Contract v0.1

**Schema Version:** `audit_event.v0.1`
**Document Version:** `0.1.0`
**Status:** `THOMAS_APPROVED_FOUNDATION`
**Owner:** `Thomas`

## 1. Purpose

Audit Event is an append-only, hash-bound record of a meaningful system action, decision, review, state change, or blocked attempt.

## 2. Required Fields

| Field | Meaning |
| --- | --- |
| `schema_version` | `audit_event.v0.1` |
| `audit_event_id` | Unique event ID |
| `trace_id` | End-to-end trace |
| `task_id` | Related Task |
| `task_revision` | Exact Task revision |
| `core_context_binding_id` | Exact Core binding |
| `event_type` | Canonical event classification |
| `actor` | Actor that created the event |
| `subject` | Exact record affected or observed |
| `event` | Summary, outcome, reasons, evidence, related records |
| `lineage` | Parent events, prior hash, sequence |
| `integrity` | Canonical payload and event SHA-256 |
| `sensitivity` | Data handling classification |
| `runtime_effect` | No hidden permission or execution effect |
| `created_at` | UTC timestamp |

## 3. Append-Only Rule

```yaml
append_only: true
overwrite_allowed: false
delete_allowed: false
```

A correction is a new Audit Event that references the prior event.

## 4. Event Integrity

`audit_event_fingerprint_payload.v0.1` binds:

- Audit Event ID
- trace, Task, revision, and Core binding
- event type
- actor reference
- subject reference and fingerprint
- event summary and outcome
- reason codes
- payload hash when present
- evidence and related records
- parent events
- previous event hash
- sequence number
- creation time

The resulting SHA-256 is stored as `event_sha256`.

> **v0.2 (additive):** `audit_event.v0.2` / `audit_event_fingerprint_payload.v0.2` extend the
> fingerprint to also bind the actor's role id, role version, and assignment id, the subject
> type and id, the event `payload_ref`, and the record `sensitivity` — the fields whose
> editing v0.1 verification could not detect (they sat outside the payload). Required fields
> and every rule in this contract are unchanged. Ledgers holding v0.1 events remain
> verifiable: the payload↔record comparison is conditional on the keys the payload carries,
> and a key cannot be removed undetected because the payload is under its own hash.

## 5. Secret and Payload Policy

Secrets must not be embedded in Audit Event payloads.

Large or sensitive evidence is stored externally and referenced by:

```text
payload_ref
+
payload_sha256
```

The reference and hash must appear together.

## 6. Audit Does Not Authorize

An Audit Event may record Approval, Permission, Validation, Kill Switch, or execution state.

It does not create those states by itself.

## 7. Runtime Effect

Every event must explicitly deny hidden side effects, Permission expansion, Activation, and execution authority.

## 8. Final Rule

> Audit records what happened or what was blocked. Audit never grants the right to make it happen.
