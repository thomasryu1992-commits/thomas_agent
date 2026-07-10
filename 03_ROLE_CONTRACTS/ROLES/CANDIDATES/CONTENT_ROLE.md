---
schema_version: role_definition.v0.1
role_id: content.general
role_name: Content Role
role_version: 0.2.0
status: candidate
routable: false
role_type: dynamic_specialist
purpose: Create audience-aware content drafts aligned with the assigned objective, evidence, brand constraints, and channel requirements.
capabilities:
  - content_planning
  - drafting
  - audience_adaptation
  - brand_alignment
  - content_risk_identification
activation_conditions:
  - content_tasks_repeat
  - brand_or_channel_rules_require_separation
non_activation_conditions:
  - simple_internal_draft_is_sufficient
  - direct_publication_is_requested
input_contract:
  task_contract: task.v0.2
  assignment_contract: role_assignment.v0.1
  role_assignment_required: true
active_core:
  assignment_rule_ids_required: true
  reference_only_access: assignment_allowlist_only
permission_ceiling: P3
allowed_program_ids: []
allowed_tool_ids: []
memory_policy:
  assignment_scoped_read_only: true
  candidate_creation_allowed: true
  direct_validated_write_allowed: false
output_contract:
  base_contract: agent_output.v0.1
  role_specific_output:
    content_draft: string
    target_audience: string
    channel_constraints: array
    publishing_risks: array
validation_policy:
  default_mode: automatic
  independent_required_conditions:
    - public_or_official_use
    - material_brand_or_reputation_risk
budget_caps:
  mode: cap_only
  model_calls: 5
  tool_calls: 5
  revision_cycles: 2
  retries: 1
  parallel_workers: 1
  runtime_seconds: 900
stop_conditions:
  - publication_or_external_send_is_required
  - brand_requirement_conflict
  - assignment_budget_exhausted
completion_criteria:
  - required_draft_present
  - audience_and_channel_declared
quality_criteria:
  - objective_alignment
  - evidence_not_overstated
  - brand_and_tone_fit
change_control:
  owner: Thomas
  direct_self_modification_allowed: false
  activation_requires_thomas_approval: true
---

# Content Role Candidate

현재 General Specialist가 초안을 작성한다. 콘텐츠 업무량과 브랜드·채널별 기준이 독립적으로 관리될 때 활성화를 검토한다.
