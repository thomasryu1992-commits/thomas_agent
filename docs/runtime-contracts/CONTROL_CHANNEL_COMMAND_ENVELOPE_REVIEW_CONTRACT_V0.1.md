# Control Channel Command Envelope Review Contract v0.1

**Status:** `Active Review-Only Foundation`
**Owner:** `Thomas`
**Phase:** `I0.4.7`

## 1. Purpose

Defines a review envelope for a future Thomas control command. It binds the command to an identity-binding fingerprint and metadata-only message fingerprint, but cannot authenticate a live Telegram event, dispatch a command, mutate Kill Switch state, stop a process, change a schedule, approve an action, or resume Runtime.

## 2. Required Fields

| Field | Requirement |
| --- | --- |
| `schema_version` | Exact `control_channel_command_envelope_review.v0.1`. |
| `command_envelope_review_id` | Immutable review identifier. |
| `identity_binding_ref` | Reviewed identity-binding reference. |
| `identity_binding_fingerprint` | Exact binding fingerprint. |
| `message` | Metadata-only private-message claim without raw IDs or text. |
| `command` | PAUSE, STOP_TASK, KILL, or RESUME request. |
| `authentication_review` | Explicitly not Runtime verified. |
| `request_fingerprint_payload` | Canonical command envelope payload. |
| `request_fingerprint` | Deterministic SHA-256. |
| `decision` | Not dispatched, Review-only. |
| `effects` | All state, process, scheduler, approval, and execution effects false. |
| `runtime_effect` | All operational flags false. |
| `audit_refs` | Related Audit references. |

## 3. Review-Only Boundary

A private-chat claim and matching fingerprints are design evidence only. Future Runtime must verify the actual provider event, exact registered identity, freshness, replay protection, command reference, and active Kill Switch policy before any dispatch.
