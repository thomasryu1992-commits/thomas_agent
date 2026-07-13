# Agent Output Contract v0.2

**Schema Version:** `agent_output.v0.2`
**Document Version:** `0.2.1`
**Status:** `Active MVP Contract`
**Owner:** `Thomas`

## 1. Purpose

Agent Output records what a Role concluded, why it concluded it, what evidence and uncertainty exist, and which exact Task and Role Assignment produced the result.

Version 0.2 adds the missing Runtime lineage required for Role validation, audit, replay, and future learning.

## 2. Required Fields

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | string | Must be `agent_output.v0.2` |
| `agent_output_id` | string | Unique output ID |
| `trace_id` | string | End-to-end trace ID |
| `task_id` | string | Related Task ID |
| `core_context_binding_id` | string | Exact Core Context Binding inherited from Task and Assignment |
| `assignment_id` | string | Exact Role Assignment ID |
| `actor_instance_id` | string | Runtime Agent instance |
| `role_id` | string | Exact Role Registry ID |
| `role_version` | string | Exact Role Definition version |
| `status` | string | Agent Output result status |
| `goal` | string | Role-specific assigned objective |
| `summary` | string | Concise human-readable result |
| `facts` | array | Verified or source-supported factual findings |
| `evidence` | array | Source, document, data, or prior-record references |
| `inferences` | array | Analysis derived from facts or evidence |
| `assumptions` | array | Material assumptions used |
| `uncertainty` | array | Unknowns, confidence limits, conflicts, or staleness |
| `risks` | array | Material risks discovered |
| `recommendation` | object or null | Recommended option or next action |
| `limitations` | array | Known output limitations |
| `validation_recommended` | boolean | Whether independent validation is recommended |
| `permission_request_refs` | array | Permission Decisions or approvals requested |
| `next_actions` | array | Proposed next steps |
| `memory_candidates` | array | Memory Candidate records or references |
| `escalation_required` | boolean | Whether Prime review is required |
| `role_specific_output` | object | Role-specific fields only |
| `created_at` | string | UTC timestamp |


## Core Binding Lineage

Every Agent Output must use the same `core_context_binding_id` as its Task revision and Role Assignment.

```text
Task Binding
=
Assignment Binding
=
Agent Output Binding
```

The Agent Output cannot silently reinterpret the Core or select a newer Core Release.

The exact meaning of an Active Rule is resolved through the Binding's Release ID and Active Core hash.


## 3. Output Status

```text
draft
partial
final
needs_validation
blocked
failed
input_required
approval_required
rejected
```

These values describe the Agent Output result. They are not Task states and are not Role Assignment states.

## 4. Evidence Separation

Every Agent Output must separate:

```text
Fact
Evidence
Inference
Assumption
Uncertainty
Risk
Recommendation
```

The system must not silently convert inference into fact.

Missing, conflicting, stale, incomplete, or inaccessible evidence must be disclosed.

## 5. Role-Specific Extension

Role-specific fields may only be added under:

```yaml
role_specific_output: {}
```

Role Definitions must not redefine the common fields or common status values.

Example:

```yaml
role_specific_output:
  sources: []
  source_quality: []
  conflicting_evidence: []
  research_gaps: []
```

## 6. Validation Output

The Independent Validation Role uses the same common Agent Output contract.

Its Role-specific output is:

```yaml
role_specific_output:
  validation_decision: PASS
  findings: []
  evidence_check: {}
  remaining_risks: []
  required_revisions: []
```

Allowed validation decisions:

```text
PASS
REVISE
BLOCK
```

A Validation decision does not grant permission for external or high-risk execution.

## 7. Example

```yaml
schema_version: agent_output.v0.2
agent_output_id: agentout_01HX_example
trace_id: trace_01HX_example
task_id: task_01HX_example
core_context_binding_id: ccb-agent-output-example-001
assignment_id: assignment_01HX_example
actor_instance_id: agent_instance_01HX_example
role_id: general.specialist
role_version: 0.3.0
status: needs_validation
goal: Analyze the supplied material and identify material risks.
summary: The material is directionally coherent, but two assumptions require verification.
facts:
  - statement: The requested output is for internal review.
    evidence_refs:
      - task.request.raw_request
evidence:
  - ref: task.request.raw_request
    type: task_input
inferences:
  - statement: Independent validation is appropriate because the recommendation may affect a strategic decision.
assumptions:
  - The supplied document is complete.
uncertainty:
  - No external source verification was assigned.
risks:
  - The recommendation may overstate confidence if the missing evidence is material.
recommendation:
  action: validate_before_use
  reason: Material assumptions remain.
limitations:
  - Analysis is limited to assigned context.
validation_recommended: true
permission_request_refs: []
next_actions:
  - Run independent validation.
memory_candidates: []
escalation_required: false
role_specific_output:
  key_findings:
    - Two assumptions need verification.
  evidence_quality: medium
  unresolved_questions:
    - Is the supplied document complete?
created_at: "2026-07-10T09:00:00Z"
```

## 8. v0.1 Migration Mapping

| v0.1 | v0.2 |
| --- | --- |
| `agent_id` | `actor_instance_id` |
| `agent_role` | `role_id` |
| no field | `assignment_id` |
| no field | `role_version` |
| `response_to_thomas` | `summary` |
| `used_information` | `evidence` |
| `decision_rationale` | `inferences` and `recommendation.reason` |
| `risk_assessment` | `risks` |
| role-specific top-level fields | `role_specific_output` |

## 9. Final Rule

> Every Agent Output must be traceable to one Task revision, one exact Core Context Binding, one exact Role Assignment, one Role ID, and one Role Version.
