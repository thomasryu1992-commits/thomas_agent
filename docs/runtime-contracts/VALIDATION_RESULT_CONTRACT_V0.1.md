# Validation Result Contract v0.1

**Schema Version:** `validation_result.v0.1`
**Document Version:** `0.1.0`
**Status:** `THOMAS_APPROVED_FOUNDATION`
**Owner:** `Thomas`

## 1. Purpose

Validation Result records an objective review of one exact subject without modifying the subject and without granting Permission.

## 2. Required Fields

| Field | Meaning |
| --- | --- |
| `schema_version` | `validation_result.v0.1` |
| `validation_result_id` | Unique Validation Result |
| `trace_id` | Trace lineage |
| `task_id` | Task lineage |
| `task_revision` | Exact revision |
| `core_context_binding_id` | Exact Core binding |
| `subject` | Exact record under review |
| `validator` | Validator identity and independence evidence |
| `validation` | Mode, checks, result, and reasons |
| `findings` | Facts, risks, omissions, assumptions, limitations |
| `evidence_refs` | Evidence used |
| `permission_boundary` | Mandatory non-authorizing guard |
| `runtime_effect` | Mandatory no-execution guard |
| `lifecycle` | Creation and supersession |
| `audit_refs` | Related Audit Events |

## 3. Results

```text
PASS
REVISE
BLOCK
```

Validation does not create `ALLOW`, Approval, Authority, Activation, or execution permission.

## 4. Independent Validation

When independent validation is required:

- validator actor must differ from subject creator;
- validator execution context must be fresh and distinct;
- self-review cannot be marked independent;
- independence evidence must be explicit.

## 5. Subject Immutability

Validation reads and evaluates the subject.

It does not edit, overwrite, activate, consume, approve, or execute the subject.

Corrections create a new subject version or a new Validation Result.

## 6. Permission Boundary

Every record must contain:

```yaml
permission_boundary:
  grants_permission: false
  grants_approval: false
  grants_authority: false
  grants_execution: false
  grants_activation: false
  mutates_subject: false
```

## 7. Final Rule

> PASS means the defined validation criteria passed. It never means the action may execute.
