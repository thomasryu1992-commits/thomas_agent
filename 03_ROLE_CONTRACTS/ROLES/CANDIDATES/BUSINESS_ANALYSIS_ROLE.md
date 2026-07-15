---
schema_version: role_definition.v0.2
role_id: business.analysis
role_name: Business Analysis Role
role_version: 0.3.0
status: candidate
routable: false
role_type: dynamic_specialist
purpose: Evaluate business options using evidence, revenue potential, downside risk,
  reversibility, validation cost, and assigned Active Core alignment.
capabilities:
- opportunity_analysis
- option_comparison
- revenue_potential_assessment
- downside_risk_assessment
- small_validation_design
unsupported_capabilities:
- financial_commitment
- business_activation
activation_conditions:
- business_analysis_tasks_repeat
- dedicated_scoring_or_evidence_rules_are_validated
non_activation_conditions:
- financial_transaction_or_commitment_is_the_role_task
- available_evidence_is_insufficient_for_material_decision
deactivation_conditions:
- task_completed
- task_canceled
- role_no_longer_required
- assignment_expired
- execution_budget_exhausted
- authority_or_permission_boundary_reached
- escalation_required
input_contract:
  task_contract: task.v0.3
  assignment_contract: role_assignment.v0.2
  role_assignment_required: true
active_core:
  assignment_rule_ids_required: true
  reference_only_access: assignment_allowlist_only
  inactive_core_candidate_access: prohibited
authority_ceiling: P3
external_action_allowed: false
authority_rules:
  authority_model: ../../../docs/runtime-contracts/AUTHORITY_AND_PERMISSION_MODEL.md
  assignment_granted_authority_required: true
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
  - business_case_learning
  - validation_pattern_learning
  direct_validated_write_allowed: false
  direct_core_write_allowed: false
  secret_candidate_creation_allowed: false
output_contract:
  base_contract: agent_output.v0.2
  role_specific_output:
    opportunity_summary: string
    options: array
    revenue_assessment: object
    downside_risks: array
    validation_plan: array
validation_policy:
  default_mode: independent
  independent_required_conditions:
  - material_business_recommendation
  - strategic_resource_allocation
  cannot_lower_policy_or_task_requirement: true
budget_caps:
  schema_version: execution_budget.v0.1
  mode: cap_only
  null_cap_means_no_additional_role_limit: true
  limits:
    max_agent_invocations: 1
    max_model_calls: 8
    max_tool_calls: 10
    max_program_calls: 6
    max_revision_cycles: 2
    max_validation_cycles: 2
    max_retry_count: 2
    max_parallel_workers: 2
    max_runtime_seconds: 1500
    token_budget: null
    cost_budget: null
    cost_currency: null
stop_conditions:
- financial_or_external_commitment_is_required
- material_evidence_gap
- assignment_budget_exhausted
completion_criteria:
- options_compared
- recommendation_and_validation_plan_present
quality_criteria:
- evidence_and_assumptions_separated
- downside_and_reversibility_assessed
- active_core_alignment
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

# Business Analysis Role Candidate

현재 General Specialist가 초기 사업 분석을 수행한다. 반복 사례를 통해 별도 평가 기준의 유효성이 확인되면 Candidate Trial을 거쳐 활성화를 검토한다.
