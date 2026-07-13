# I0.4 Runtime Contract Set Index v0.1

**Status:** `REVIEW_ONLY_CONSOLIDATED_NOT_RUNTIME_ACTIVE`
**Owner:** `Thomas`
**Machine-readable index:** `../../05_REGISTRIES/I0_4_RUNTIME_CONTRACT_SET_INDEX.yaml`

## 1. Purpose

This document defines the consolidated, frozen I0.4 review-only contract set that must be integrated and validated before I0.5 Read-only Runtime Kernel work begins.

The index is organizational and evidentiary. It is not a Runtime registry, Permission Decision, Approval, Activation, Executor Registry, or execution authority.

## 2. Required Fields

| Field | Meaning |
| --- | --- |
| `schema_version` | Exact index schema version |
| `document_version` | Index document version |
| `status` | Review-only consolidated state |
| `owner` | Thomas |
| `phase` | I0.4 consolidation phase |
| `runtime_source_of_truth` | Must remain false |
| `contract_set` | Freeze and no-permission assertions |
| `baseline_dependencies` | Existing Repository contracts required by the I0.4 set |
| `record_contracts` | Canonical I0.4 record contracts and Schemas |
| `non_schema_documents` | Policies and phase-boundary evidence |
| `focused_validators` | Required stage validators |
| `consolidation_assertions` | Deduplication, indexing, Gate, and Release assertions |
| `next_stage` | I0.5 entry gate and scope |

## 3. Review-Only Boundary

The consolidated index does not:

- activate a Runtime contract set;
- approve or activate Thomas Core;
- enable a Tool, Program, Executor, Supervisor, Scheduler, Control Channel, Monitoring daemon, Alert delivery, Sandbox, or Kill Switch Runtime;
- consume Approval;
- issue an execution token;
- permit external, financial, destructive, or privileged action;
- expand Permission or Authority.

## 4. Freeze Rule

After this checkpoint, I0.4 receives no new functional contract families. Only defect correction, security hardening, compatibility repair, or an explicit Thomas-approved governance correction may change the set.

New functional development moves to I0.5 Read-only Runtime Kernel after the real Repository passes the full consolidation entry gate.
