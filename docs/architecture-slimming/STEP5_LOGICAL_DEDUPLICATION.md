# THOMAS AGENT — STEP 5: LOGICAL DEDUPLICATION

**Status:** Implementation Candidate
**Baseline:** `THOMAS_AGENT_I0_5_5_PRE_SLIMMING`
**Runtime behavior change:** None
**Authority expansion:** None

## Objective

Reduce duplicated rule ownership before physical migration.

Priority:

1. establish one canonical Governance Policy;
2. reduce Role Registry to status and location metadata;
3. reduce Program Registry to status and location metadata;
4. reduce Tool Registry to status and location metadata;
5. preserve existing safety behavior through compatibility projection;
6. introduce no new autonomous capability.

## Canonical Governance

New candidate:

```text
governance/GOVERNANCE_POLICY.yaml
```

This is the only machine-readable owner of:

- effect classification;
- permission dispositions;
- approval requirements;
- authority limits;
- action identity rules;
- conflict resolution.

Existing files remain during migration:

| Existing file | Future status |
|---|---|
| `THOMAS_PERMISSION_APPROVAL_OPERATING_POLICY_V0.1.yaml` | compatibility source |
| `THOMAS_PERMISSION_APPROVAL_OPERATING_PRINCIPLES_V0.1.md` | human-readable reference |
| `AUTHORITY_AND_PERMISSION_MODEL.md` | explanatory reference |
| `PERMISSION_DECISION_CONTRACT_V0.3.md` | decision record only |
| `APPROVAL_CONTRACT_V0.1.md` | approval lifecycle record only |
| `ACTION_FINGERPRINT_POLICY_V0.1.md` | rule migrated into Governance |

No file is deleted in Step 5.

## Registry Slimming

Role Registry keeps only:

```yaml
role_id:
version:
role_type:
status:
routable:
definition_path:
definition_sha256:
```

Program Registry keeps only:

```yaml
program_id:
version:
status:
enabled:
definition_path:
definition_sha256:
runtime_implementation_available:
```

Tool Registry keeps only:

```yaml
tool_id:
version:
status:
enabled:
tool_class:
definition_path:
definition_sha256:
runtime_implementation_available:
```

Capabilities, restrictions, permission ceilings, validation requirements, behavior, and resource policy must be loaded from Definitions or Governance instead of copied into Registries.

## Compatibility Projection

Legacy Runtime and validators may still expect duplicated Registry fields.

Create:

```text
runtime/compat/legacy_registry_projection.py
```

It generates the legacy in-memory shape from:

- slim Registry;
- Definition;
- Governance Policy.

It is not authoritative.

```yaml
compatibility_projection:
  authoritative: false
  persistent: false
  generated_in_memory: true
  may_expand_authority: false
  fail_closed_on_missing_source: true
  fail_closed_on_hash_mismatch: true
```

## Validator Change

Old:

```text
Registry capabilities == Role Definition capabilities
```

New:

```text
Registry path exists
AND definition hash matches
AND Runtime loads capabilities from Definition
```

New validators:

```text
validate_governance_source_of_truth.py
validate_slim_role_registry.py
validate_slim_program_registry.py
validate_slim_tool_registry.py
```

They check:

1. identity fields;
2. path existence;
3. hash match;
4. valid status;
5. status/routability compatibility;
6. implementation availability where required;
7. absence of prohibited duplicated fields.

## Runtime Boundary

Step 5 does not:

- enable a model;
- enable a Tool;
- enable a Program;
- activate Runtime-authoritative entry;
- consume Approval;
- activate an Executor;
- enable external or financial effects;
- activate Core.

## Implementation Order

1. Add `governance/GOVERNANCE_POLICY.yaml`.
2. Add slim Registry candidates.
3. Add Program and Tool Definition candidates.
4. Add compatibility projection.
5. Add focused slim validators.
6. Update loaders to prefer canonical sources.
7. Run old and new validators in parallel for one migration release.
8. Retire duplicated fields only after compatibility validation passes.

## Acceptance

```yaml
step_5_acceptance:
  canonical_governance_policy_created: true
  governance_rules_have_one_machine_readable_owner: true
  slim_role_registry_candidate_created: true
  slim_program_registry_candidate_created: true
  slim_tool_registry_candidate_created: true
  compatibility_strategy_defined: true
  no_existing_source_deleted: true
  no_runtime_authority_added: true
  no_tool_or_program_enabled: true
  no_external_effect_enabled: true
```

## Next

Step 6 implements the compatibility projection and splits Kernel responsibility into:

```text
loader
preflight
policy
router
worker
validation
audit
assembler
```
