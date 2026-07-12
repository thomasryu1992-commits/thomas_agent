# Thomas Autonomous Organization Document Map

**Status:** `MVP Document Structure v0.4`
**Owner:** `Thomas`

## 1. Document Structure

```text
Thomas
└─ Thomas Core
   └─ Operating Constitution
      Target document; inactive in MVP
      └─ Organization Architecture
         └─ MVP Operating Policy
            └─ Runtime Contract Precedence Addendum
               └─ Canonical Runtime Contracts
                  └─ Prime and Dynamic Role Contracts
                     └─ Role, Program, and Tool Registries
                        └─ Runtime Records and Audit Events
```

Organization Architecture defines structure and long-term boundaries.

MVP Operating Policy defines active operating rules.

Canonical Runtime contracts define machine-facing semantics and record structures.

## 2. Active MVP Documents

| Level | Document | MVP Use |
| --- | --- | --- |
| Current Core Release | `../THOMAS_CORE/CURRENT_CORE_RELEASE.yaml` | Exact approved Core Release used for new Task bindings |
| Thomas Core | `../THOMAS_CORE/MVP_ACTIVE_CORE.yaml` | Review-ready Active Rule projection; Runtime activation still depends on the Current Core Release pointer |
| Organization Architecture | `thomas-autonomous-organization-architecture-v0.1.md` | Target, MVP, and Dynamic Team architecture |
| Operating Policy | `MVP_OPERATING_POLICY.md` | Execution, risk, permission, Telegram, Memory, failure, learning, and Audit rules |
| Runtime Precedence | `runtime-contracts/RUNTIME_CONTRACT_PRECEDENCE_ADDENDUM_v0.4.md` | Resolves migration conflicts and identifies canonical contracts |
| Core Release Lifecycle | `runtime-contracts/CORE_RELEASE_LIFECYCLE_V0.3.md` | Self-contained Release snapshots, Approval authority, Activation, rollback, Revocation, deactivation, and Current pointer |
| Core Context Binding | `runtime-contracts/CORE_CONTEXT_BINDING_V0.3.md` | Exact Core Release, hashes, approval, Rule subset, inheritance, and rebind lineage for one Task revision |
| Programization Review | `runtime-contracts/PROGRAMIZATION_REVIEW_POLICY_V0.1.md` | Defines valid repetition and the review boundary before Program Candidate creation |
| Task | `runtime-contracts/TASK_CONTRACT_V0.3.md` | Canonical work unit, Core Binding reference, scope, classification, authority, Permission, routing, validation, budget, lifecycle, result, and Audit |
| Task State Machine | `runtime-contracts/TASK_STATE_MACHINE_V0.1.yaml` | Canonical state transitions and guards |
| Authority and Permission | `runtime-contracts/AUTHORITY_AND_PERMISSION_MODEL.md` | Separates P0–P6 from ALLOW–BLOCK |
| Agent Output | `runtime-contracts/AGENT_OUTPUT_CONTRACT_V0.2.md` | Assignment and Role lineage for Agent results |
| Execution Budget | `runtime-contracts/EXECUTION_BUDGET_SCHEMA.yaml` | Canonical numeric Task, Role, Assignment, and usage budget |
| Prime Charter | `../03_ROLE_CONTRACTS/THOMAS_PRIME_CHARTER.md` | Thomas Prime identity, responsibility, authority, and prohibitions |
| Dynamic Role Contract | `../03_ROLE_CONTRACTS/MVP_DYNAMIC_ROLE_CONTRACT.md` | Dynamic Role common rules |
| Role Definition Template | `../03_ROLE_CONTRACTS/ROLE_DEFINITION_TEMPLATE.yaml` | Machine-readable Role Definition structure |
| Role Assignment | `../03_ROLE_CONTRACTS/ROLE_ASSIGNMENT_CONTRACT.md` | Task-specific Role scope, authority, Permission, Memory, resources, validation, and budget |
| Role Registry | `../03_ROLE_CONTRACTS/ROLE_REGISTRY.yaml` | Role status, routability, version, hashes, and Candidate trial policy |
| Program Registry | `../05_REGISTRIES/PROGRAM_REGISTRY.yaml` | Registered Program status and Runtime availability |
| Tool Registry | `../05_REGISTRIES/TOOL_REGISTRY.yaml` | Registered Tool status and Runtime availability |

## 3. Machine-Readable Schemas

```text
schemas/
├── core_context_binding.v0.3.schema.json
├── execution_budget.v0.1.schema.json
└── task.v0.3.schema.json
```

The Task schema validates structure and selected state-specific invariants.

The Task State Machine validates transition direction and transition guards.

Both are required.

## 4. Legacy Integrated Contract Document

`thomas-twin-core-architecture-v0.1.md` remains useful as architecture and historical I/O reference.

For new Runtime records, the canonical contracts listed above supersede conflicting shorthand or older schema sections.

Existing records remain valid under their original schema versions.

## 5. Planned Documents

Next:

- Permission Decision Contract v0.3.
- Approval Contract v0.1.

Later:

- Tool Request Contract.
- Execution Request and Execution Result Contracts.
- Validation Result standalone schema if required.
- Audit Event schema.
- Department Definition after real department separation is justified.
- Runtime implementations for candidate Programs and Tools.

Planned documents do not grant Runtime permission.

## 6. Change Rule

- Core Release changes require a new immutable Manifest under `THOMAS_CORE/releases/<release_id>/`.
- Core Runtime activation requires a separate approval record and `CURRENT_CORE_RELEASE.yaml`.
- A running Task does not silently rebind to a newer Core Release.
- Core approval and Core Binding do not grant execution Permission.

- Thomas Core and Operating Constitution changes require Thomas approval.
- Active Core promotion always requires explicit Thomas approval, versioned update, and Audit.
- Runtime Contract changes require version updates and compatibility review.
- Role Definition changes require Registry consistency and hash updates.
- Material Task changes increment Task revision and supersede affected Runtime records.
- Runtime Records and Audit Events are append-only; corrections are new events rather than silent overwrite.


## Review-Only Learning and Programization Records

```text
programization_observation.v0.1
↓
programization_pattern.v0.1
↓
programization_candidate.v0.1
```

Ten independent valid repetitions trigger Review only.

A Program Candidate remains pending Program Registry and Permission Policy.

Validated Operational Knowledge includes review due dates, environment signatures, confidence, and stale/deprecated states.
