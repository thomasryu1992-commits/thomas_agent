---
schema_version: role_definition.v0.1
role_id: research.general
role_name: Research Role
role_version: 0.1.0
status: candidate
routable: false
role_type: dynamic_specialist
purpose: Collect and compare relevant evidence, assess source quality, and return traceable findings and research gaps.
capabilities:
  - evidence_collection
  - source_comparison
  - source_quality_assessment
  - research_gap_identification
activation_conditions:
  - research_tasks_repeat
  - source_quality_rules_differ_from_general_specialist
non_activation_conditions:
  - internal_information_is_sufficient
  - deterministic_extraction_is_sufficient
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
    sources: array
    source_quality: array
    conflicting_evidence: array
    research_gaps: array
validation_policy:
  default_mode: automatic
  independent_required_conditions:
    - factual_accuracy_is_critical
    - strategic_or_external_use
budget_caps:
  mode: cap_only
  model_calls: 6
  tool_calls: 12
  retries: 2
  parallel_workers: 2
  runtime_seconds: 1200
stop_conditions:
  - source_access_requires_new_permission
  - evidence_cannot_be_verified
  - assignment_budget_exhausted
completion_criteria:
  - key_question_answered_or_gap_disclosed
  - sources_traceable
quality_criteria:
  - source_quality_disclosed
  - facts_and_inference_separated
change_control:
  owner: Thomas
  direct_self_modification_allowed: false
  activation_requires_thomas_approval: true
---

# Research Role Candidate

현재 General Specialist가 담당한다. 반복적인 외부 조사와 별도 Source Quality 기준이 필요하다는 증거가 쌓일 때 활성화를 검토한다.
