---
schema_version: role_definition.v0.1
role_id: business.analysis
role_name: Business Analysis Role
role_version: 0.2.0
status: candidate
routable: false
role_type: dynamic_specialist
purpose: Evaluate business options using evidence, revenue potential, downside risk, reversibility, validation cost, and Active Core alignment.
capabilities:
  - opportunity_analysis
  - option_comparison
  - revenue_potential_assessment
  - downside_risk_assessment
  - small_validation_design
activation_conditions:
  - business_analysis_tasks_repeat
  - dedicated_scoring_or_evidence_rules_are_validated
non_activation_conditions:
  - financial_transaction_or_commitment_is_required
  - available_evidence_is_insufficient_for_material_decision
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
budget_caps:
  mode: cap_only
  model_calls: 8
  tool_calls: 10
  revision_cycles: 2
  retries: 2
  parallel_workers: 2
  runtime_seconds: 1500
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
change_control:
  owner: Thomas
  direct_self_modification_allowed: false
  activation_requires_thomas_approval: true
---

# Business Analysis Role Candidate

현재 General Specialist가 초기 사업 분석을 수행한다. 반복 사례를 통해 별도 평가 기준의 유효성이 확인될 때 활성화를 검토한다.
