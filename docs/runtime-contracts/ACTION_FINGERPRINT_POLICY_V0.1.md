# Action Fingerprint Policy v0.1

**Payload Version:** `action_fingerprint_payload.v0.1`
**Document Version:** `0.1.1`
**Status:** `Active Review-Only Policy`
**Owner:** `Thomas`

## 1. Purpose

The action fingerprint prevents an Approval or Permission Decision for one action from being reused for a materially different action.

## 2. Canonical Payload

The payload contains:

| Field | Purpose |
| --- | --- |
| `schema_version` | Canonical payload version |
| `task_id` | Task lineage |
| `task_revision` | Exact Task revision |
| `core_context_binding_id` | Exact Core binding |
| `requester_ref` | `actor_type:actor_id` |
| `permission_scope` | Exact Thomas-approved policy scope |
| `action_type` | Normalized action type |
| `target_ref` | Exact target reference |
| `tool_id` | Exact Tool or null |
| `program_id` | Exact Program or null |
| `data_scope` | Sorted unique scope entries |
| `content_sha256` | Exact content hash or null |
| `amount_decimal` | Decimal string or null |
| `currency` | Uppercase currency code or null |
| `normalized_parameters` | Secret-free deterministic parameters |
| `expires_at` | Action decision expiration |

Task revision is part of the fingerprint. A revised Task cannot reuse the prior fingerprint.

## 3. Canonicalization

The implementation uses deterministic JSON:

```text
UTF-8
+ recursively sorted object keys
+ no insignificant whitespace
+ Unicode preserved
+ integers, booleans, strings, arrays, objects, and null only
+ float values forbidden
+ data_scope sorted
```

The design is aligned with the deterministic intent of RFC 8785, while the exact executable rule is the repository implementation in `scripts/lib/action_fingerprint.py`.

Do not substitute a different serializer without a version change and compatibility review.

## 4. Hash

```text
action_fingerprint
=
"sha256:"
+
lowercase SHA-256 hex digest
```

The hash input is the canonical UTF-8 JSON byte sequence.

## 5. Decimal Rule

Financial values use normalized strings:

```yaml
amount_decimal: "1250.50"
currency: USD
```

Binary floating-point values are forbidden.

`amount_decimal` and `currency` must both be present or both be null.

## 6. Secret Policy

Secret values must never enter the fingerprint payload.

Forbidden examples:

```text
API key
API secret
Private key
Password
Passphrase
Access token
Refresh token
Secret
```

Only non-secret metadata references or one-way content hashes may be included.

A fingerprint is not a secure secret vault. Hashing a secret does not make secret handling acceptable.

## 7. Reuse Invalidation

Create a new fingerprint when any material field changes, including:

- Task revision;
- Core Context Binding;
- requester;
- permission scope;
- action type;
- target;
- Tool;
- Program;
- data scope;
- content;
- amount;
- currency;
- normalized parameters;
- expiration.

## 8. Validation

The validator must:

1. reconstruct the canonical payload;
2. compute SHA-256;
3. compare the result with the stored fingerprint;
4. compare Task and Binding lineage;
5. compare Approval snapshot with the referenced Permission Decision;
6. fail closed on any mismatch.

## 9. Non-Goals

The fingerprint does not:

- prove Authority;
- grant Permission;
- verify Thomas identity;
- authorize execution;
- protect secrets;
- replace Audit.
