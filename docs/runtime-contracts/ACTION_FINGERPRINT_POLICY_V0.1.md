# Action Fingerprint v0.1 — Algorithm and Record Reference

**Payload Version:** `action_fingerprint_payload.v0.1`
**Document Version:** `0.2.0`
**Status:** `ALGORITHM_REFERENCE`
**Owner:** `Thomas`
**Authoritative Policy:** [`governance/GOVERNANCE_POLICY.yaml`](../../governance/GOVERNANCE_POLICY.yaml)
**Executable Implementation:** `scripts/lib/action_fingerprint.py`

## 1. Responsibility

The canonical Governance Policy owns the action-identity requirement, required payload fields, invalidation rule, SHA-256 requirement, and secret prohibition.

This document explains the deterministic payload and implementation contract. It does not independently define Permission, Approval, Authority, or Runtime policy.

## 2. Canonical payload

| Field | Purpose |
|---|---|
| `schema_version` | `action_fingerprint_payload.v0.1` |
| `task_id` | Task lineage |
| `task_revision` | Exact Task revision |
| `core_context_binding_id` | Exact Core binding |
| `requester_ref` | Exact requester identity |
| `permission_scope` | Canonical Governance scope |
| `action_type` | Normalized action type |
| `target_ref` | Exact target |
| `tool_id` | Exact Tool or null |
| `program_id` | Exact Program or null |
| `data_scope` | Sorted unique scope entries |
| `content_sha256` | Exact content hash or null |
| `amount_decimal` | Normalized decimal string or null |
| `currency` | Uppercase currency code or null |
| `normalized_parameters` | Secret-free deterministic parameters |
| `expires_at` | Exact decision expiration |

Task revision is part of the fingerprint. A revised Task cannot reuse a prior fingerprint.

## 3. Canonicalization

The executable implementation uses deterministic JSON with:

```text
UTF-8
+ recursively sorted object keys
+ no insignificant whitespace
+ Unicode preserved
+ integers, booleans, strings, arrays, objects, and null only
+ float values forbidden
+ data_scope sorted and unique
```

The design follows the deterministic intent of RFC 8785. The exact executable behavior is the versioned repository implementation in `scripts/lib/action_fingerprint.py`.

Do not substitute another serializer without a version change and compatibility review.

## 4. Hash output

```text
action_fingerprint
=
"sha256:"
+
lowercase SHA-256 hexadecimal digest
```

The hash input is the canonical UTF-8 JSON byte sequence.

## 5. Decimal and currency rule

```yaml
amount_decimal: "1250.50"
currency: USD
```

Binary floating-point values are forbidden. `amount_decimal` and `currency` must both be present or both be null.

## 6. Secret policy

Secret values must never enter the fingerprint payload.

Forbidden examples include API keys, API secrets, private keys, passwords, passphrases, access tokens, refresh tokens, credentials, and secret values.

Only non-secret metadata references or one-way content hashes may be included. Hashing a Secret does not make Secret handling acceptable.

## 7. Reuse invalidation

Any material change requires a new fingerprint and a new Permission Decision. This includes changes to Task revision, Core Context Binding, requester, Permission scope, action type, target, Tool, Program, data scope, content, amount, currency, normalized parameters, or expiration.

## 8. Validation

A validator must:

1. normalize the payload using the versioned implementation;
2. compute SHA-256;
3. compare the result with the stored fingerprint;
4. compare Task and Binding lineage;
5. compare an Approval snapshot with the referenced Permission Decision;
6. fail closed on any mismatch.

## 9. Non-goals

The fingerprint does not prove Authority, grant Permission, verify Thomas identity, authorize execution, protect Secrets, replace Audit, consume Approval, or activate an Executor.
