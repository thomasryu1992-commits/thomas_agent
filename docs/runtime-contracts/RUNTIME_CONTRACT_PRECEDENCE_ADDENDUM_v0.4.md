# Runtime Contract Precedence Addendum v0.4

**Status:** `Active MVP Addendum`
**Owner:** `Thomas`

## 1. Purpose

This addendum identifies the canonical Runtime contracts while older integrated architecture and I/O documents are migrated.

## 2. Canonical Runtime Contracts

| Subject | Canonical Document |
| --- | --- |
| Core Release Lifecycle | `CORE_RELEASE_LIFECYCLE_V0.3.md` |
| Current approved Core Release | `../../THOMAS_CORE/CURRENT_CORE_RELEASE.yaml` |
| Core Context Binding | `CORE_CONTEXT_BINDING_V0.3.md` |
| Programization Review | `PROGRAMIZATION_REVIEW_POLICY_V0.1.md` |
| Task | `TASK_CONTRACT_V0.3.md` |
| Task state transitions | `TASK_STATE_MACHINE_V0.1.yaml` |
| Authority levels and Permission Decisions | `AUTHORITY_AND_PERMISSION_MODEL.md` |
| Agent Output | `AGENT_OUTPUT_CONTRACT_V0.2.md` |
| Execution Budget | `EXECUTION_BUDGET_SCHEMA.yaml` |
| Dynamic Role common rules | `../../03_ROLE_CONTRACTS/MVP_DYNAMIC_ROLE_CONTRACT.md` |
| Role Assignment | `../../03_ROLE_CONTRACTS/ROLE_ASSIGNMENT_CONTRACT.md` |
| Role Registry | `../../03_ROLE_CONTRACTS/ROLE_REGISTRY.yaml` |
| Program Registry | `../../05_REGISTRIES/PROGRAM_REGISTRY.yaml` |
| Tool Registry | `../../05_REGISTRIES/TOOL_REGISTRY.yaml` |

## 3. Superseded Runtime Shorthand

The following older shorthand is not Runtime source of truth.

- Comparing `ALLOW`, `APPROVAL_REQUIRED`, or `BLOCK` with P0–P6.
- Treating Permission Decision as an authority level.
- Flat Task fields that do not separate Authority, Permission, Routing, Validation, Lifecycle, and Audit.
- Agent Output v0.1 fields that do not preserve Assignment ID, Role ID, and Role Version.
- Mixed budget field names that do not follow `execution_budget.v0.1`.
- `none | low | medium | high | critical` as canonical Task risk values.
- `allow | allow_with_report | require_approval | deny | escalate` as canonical Permission Decision values.

Canonical Task risk:

```text
GREEN
YELLOW
ORANGE
RED
```

Canonical Permission Decision:

```text
ALLOW
EXECUTE_AND_REPORT
APPROVAL_REQUIRED
BLOCK
```

Canonical Task Permission evaluation status:

```text
NOT_EVALUATED
DECIDED
SUPERSEDED
```

## 4. Runtime Precedence

```text
Current Approved Thomas Core Release
↓
Core Context Binding v0.3
↓
Active Operating Constitution
(skip while inactive)
↓
MVP Operating Policy
↓
This Runtime Contract Addendum
↓
Canonical Runtime Contracts
↓
Thomas Prime Charter
↓
MVP Dynamic Role Contract
↓
Role Definition
↓
Role Assignment
↓
Runtime Defaults
```

Organization Architecture defines system structure and long-term boundaries. It does not independently grant Runtime permission.

## 5. New Runtime Record Versions

After adoption of this addendum, new records use:

```text
core_context_binding.v0.3
task.v0.3
role_assignment.v0.2
agent_output.v0.2
execution_budget.v0.1
```

Existing historical records remain valid under their original schema versions.

## 6. Migration Rule

Do not silently rewrite historical records.

Migration creates a new versioned record or an explicit compatibility view.

Material Task changes supersede affected Permission Decisions, invalidate affected approvals and Role Assignments, and preserve the prior records for Audit.


## 7. Core Rule Resolution

Rule ID alone is not sufficient for exact historical interpretation.

```text
Core Release ID
+
Active Core SHA256
+
Rule ID
→ Exact Rule meaning
```

Task, Role Assignment, and Agent Output use the same `core_context_binding_id`.

Core approval and Binding do not grant execution Permission.


## 8. Release and Activation Source of Truth

```text
Self-Contained Core Release Snapshot
↓
Runtime-Authoritative Approval
↓
Committed Activation Record
↓
CURRENT_CORE_RELEASE.yaml
↓
Core Context Binding v0.3
```

The current working-tree Core is a development source.

The bound Release snapshot is the historical Runtime source of truth.

Review-only Approval cannot activate Runtime use.

Core activation does not grant execution Permission.
