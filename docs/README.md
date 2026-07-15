# Thomas Autonomous Organization Document Map

**Status:** `MVP Document Structure v0.3`  
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
| Thomas Core | `../THOMAS_CORE/MVP_ACTIVE_CORE.yaml` | Only active Core rules |
| Organization Architecture | `thomas-autonomous-organization-architecture-v0.1.md` | Target, MVP, and Dynamic Team architecture |
| Operating Policy | `MVP_OPERATING_POLICY.md` | Execution, risk, permission, Telegram, Memory, failure, learning, and Audit rules |
| Runtime Precedence | `runtime-contracts/RUNTIME_CONTRACT_PRECEDENCE_ADDENDUM_v0.3.md` | Resolves migration conflicts and identifies canonical contracts |
| Authority and Permission | `runtime-contracts/AUTHORITY_AND_PERMISSION_MODEL.md` | Separates P0–P6 from ALLOW–BLOCK |
| Agent Output | `runtime-contracts/AGENT_OUTPUT_CONTRACT_V0.2.md` | Assignment and Role lineage for Agent results |
| Execution Budget | `runtime-contracts/EXECUTION_BUDGET_SCHEMA.yaml` | Canonical numeric Task, Role, Assignment, and usage budget |
| Prime Charter | `../03_ROLE_CONTRACTS/THOMAS_PRIME_CHARTER.md` | Thomas Prime identity, responsibility, authority, and prohibitions |
| Dynamic Role Contract | `../03_ROLE_CONTRACTS/MVP_DYNAMIC_ROLE_CONTRACT.md` | Dynamic Role common rules |
| Role Definition Template | `../03_ROLE_CONTRACTS/ROLE_DEFINITION_TEMPLATE.yaml` | Machine-readable Role Definition structure |
| Role Assignment | `../03_ROLE_CONTRACTS/ROLE_ASSIGNMENT_CONTRACT.md` | Task-specific Role scope, authority, permission, Memory, resources, validation, and budget |
| Role Registry | `../03_ROLE_CONTRACTS/ROLE_REGISTRY.yaml` | Role status, routability, version, hashes, and Candidate trial policy |
| Program Registry | `../05_REGISTRIES/PROGRAM_REGISTRY.yaml` | Registered Program status and Runtime availability |
| Tool Registry | `../05_REGISTRIES/TOOL_REGISTRY.yaml` | Registered Tool status and Runtime availability |

## 3. Legacy Integrated Contract Document

`thomas-twin-core-architecture-v0.1.md` remains a useful architecture and historical I/O reference.

For new Runtime records, the canonical contracts listed above supersede conflicting shorthand or older schema sections.

Existing records remain valid under their original schema versions.

## 4. Planned Documents

- Operating Constitution activation package.
- Tool Request Contract.
- Execution Request and Execution Result Contracts.
- Validation Result standalone schema if required.
- Audit Event schema.
- Department Definition after real department separation is justified.
- Runtime implementations for candidate Programs and Tools.

Planned documents do not grant Runtime permission.

## 5. Change Rule

- Thomas Core and Operating Constitution changes require Thomas approval.
- Active Core promotion always requires explicit Thomas approval, versioned update, and Audit.
- Runtime Contract changes require version updates and compatibility review.
- Role Definition changes require Registry consistency and hash updates.
- Runtime Records and Audit Events are append-only; corrections are new events rather than silent overwrite.
