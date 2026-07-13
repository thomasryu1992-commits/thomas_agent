# Control Channel Identity Binding Contract v0.1

**Status:** `Active Review-Only Foundation`
**Owner:** `Thomas`
**Phase:** `I0.4.7`

## 1. Purpose

Defines metadata-only evidence for a future binding between Real Thomas and an authenticated private Telegram control channel. The record contains fingerprints rather than raw Telegram user IDs, chat IDs, bot tokens, webhook secrets, or credential values. It cannot connect a bot, verify a challenge, activate a binding, grant Approval authority, or dispatch a command.

## 2. Required Fields

| Field | Requirement |
| --- | --- |
| `schema_version` | Exact `control_channel_identity_binding.v0.1`. |
| `binding_id` | Immutable identity-binding review identifier. |
| `owner` | Thomas. |
| `status` | Review-only and unbound. |
| `channel` | Private Telegram channel metadata and non-secret fingerprints. |
| `identity` | Real Thomas identity claim without raw provider identifiers. |
| `verification` | Metadata-only review; no Runtime verification or challenge. |
| `command_policy` | Allowed vocabulary and strict private-channel requirements. |
| `binding_fingerprint_payload` | Canonical binding payload. |
| `binding_fingerprint` | Deterministic SHA-256. |
| `runtime_effect` | Every connection, activation, command, process, scheduler, and execution effect false. |
| `created_at` | Review record creation time. |
| `expires_at` | Review evidence expiration. |
| `audit_refs` | Related Audit references. |

## 3. Review-Only Boundary

```text
Identity Binding Review
!= Runtime Identity Verification
!= Control Channel Connection
!= Approval Authority
!= Command Dispatch
```

Raw identifier values and secret values are not required by this foundation and must not be included.
