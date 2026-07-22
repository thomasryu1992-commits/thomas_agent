---
schema_version: role_definition.v0.2
role_id: research.general
role_name: Research Role
role_version: 0.4.0
status: candidate
routable: false
role_type: dynamic_specialist
purpose: Collect and compare relevant evidence, assess source quality, and return
  traceable findings and research gaps.
capabilities:
- evidence_collection
- source_comparison
- source_quality_assessment
- research_gap_identification
unsupported_capabilities:
- external_execution
- unverified_claim_as_fact
activation_conditions:
- research_tasks_repeat
- source_quality_rules_differ_from_general_specialist
non_activation_conditions:
- internal_information_is_sufficient
- deterministic_extraction_is_sufficient
deactivation_conditions:
- task_completed
- task_canceled
- role_no_longer_required
- assignment_expired
- execution_budget_exhausted
- permission_boundary_reached
- escalation_required
input_contract:
  task_contract: task.v0.3
  task_contract_minimum: task.v0.3
  supported_task_contracts:
    - task.v0.3
  core_context_binding_required: true
  assignment_contract: role_assignment.v0.2
  role_assignment_required: true
active_core:
  assignment_rule_ids_required: true
  reference_only_access: assignment_allowlist_only
  inactive_core_candidate_access: prohibited
permission_ceiling: P3
external_action_allowed: false
authority_rules:
  authority_model: ../../../docs/runtime-contracts/AUTHORITY_AND_PERMISSION_MODEL.md
  assignment_granted_permission_required: true
  permission_decision_is_separate_axis: true
allowed_program_ids: []
allowed_tool_ids: []
memory_policy:
  assignment_scoped_read_only: true
  readable_scopes:
  - task_working_memory
  - related_validated_memory
  prohibited_scopes:
  - unrelated_private_memory
  - inactive_core_candidates
  - restricted_memory
  candidate_creation_allowed: true
  allowed_candidate_types:
  - reusable_knowledge
  - research_method_learning
  direct_validated_write_allowed: false
  direct_core_write_allowed: false
  secret_candidate_creation_allowed: false
output_contract:
  base_contract: agent_output.v0.2
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
  cannot_lower_policy_or_task_requirement: true
budget_caps:
  schema_version: execution_budget.v0.1
  mode: cap_only
  null_cap_means_no_additional_role_limit: true
  limits:
    max_agent_invocations: 1
    max_model_calls: 6
    max_tool_calls: 12
    max_program_calls: 6
    max_revision_cycles: 2
    max_validation_cycles: 2
    max_retry_count: 2
    max_parallel_workers: 2
    max_runtime_seconds: 1200
    token_budget: null
    cost_budget: null
    cost_currency: null
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
- provided_evidence_over_model_memory
- empty_result_reverified_before_conclusion
- direct_vs_circumstantial_evidence_distinguished
- absence_of_evidence_not_overinterpreted
- confidence_level_stated_per_finding
escalation:
  target: thomas_prime
  direct_to_thomas_allowed: false
change_control:
  owner: Thomas
  direct_self_modification_allowed: false
  activation_requires_thomas_approval: true
  semantic_versioning_required: true
candidate_trial_policy:
  normal_runtime_routing_allowed: false
  explicit_trial_assignment_allowed: true
  requirements:
  - explicit_thomas_approval
  - exact_candidate_role_version
  - candidate_trial_assignment_mode
  - isolated_trial_context
  - no_external_action
  - no_persistent_runtime_change
  - numeric_execution_budget
  - independent_validation
  - audit_required
---

# Research Role Candidate

현재 General Specialist가 담당한다. 반복적인 외부 조사와 별도 Source Quality 기준의 가치가 실제 Task에서 검증될 때 Candidate Trial을 거쳐 활성화를 검토한다.

## 조사 방법론 (quality_criteria 보충 설명)

v0.4.0에서 추가된 조사 원칙. 출처: OpenPlanter 조사 방법론 검토 (2026-07-22, Thomas 결정 — `cross_source_links_record_matching_fields`는 제외).

- `provided_evidence_over_model_memory` — 제공된 자료·증거가 모델의 사전 지식과 충돌하면 제공된 자료를 우선하고, 충돌 사실을 명시한다.
- `empty_result_reverified_before_conclusion` — 빈 조회 결과를 곧바로 "없음"으로 결론짓지 않고, 다른 표현·경로로 재확인했음을 밝히거나 재확인 불가를 한계로 공개한다.
- `direct_vs_circumstantial_evidence_distinguished` — 직접 증거와 정황 증거를 구분해 표시한다.
- `absence_of_evidence_not_overinterpreted` — 증거의 부재를 부정의 증거로 확대 해석하지 않는다.
- `confidence_level_stated_per_finding` — 개별 발견마다 신뢰 수준(high/medium/low)을 명시하며, 미확정 연결을 확정으로 다루지 않는다. `role_specific_output.source_quality` 배열에 담는다.
