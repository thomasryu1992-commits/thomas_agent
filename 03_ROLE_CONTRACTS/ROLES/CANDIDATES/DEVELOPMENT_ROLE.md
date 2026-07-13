---
schema_version: role_definition.v0.2
role_id: development.general
role_name: Development Role
role_version: 0.3.0
status: candidate
routable: false
role_type: dynamic_specialist
purpose: Analyze technical tasks and produce code plans, review findings, or draft
  changes inside an explicitly assigned and isolated scope.
capabilities:
- technical_analysis
- implementation_planning
- code_review
- code_draft_generation
unsupported_capabilities:
- unrestricted_shell
- production_deployment
- secret_access
activation_conditions:
- development_tasks_repeat
- isolated_execution_and_tool_registry_are_available
- file_and_runtime_permissions_are_explicit
non_activation_conditions:
- production_deployment_is_the_role_task
- unrestricted_file_or_shell_access_is_required
- tool_or_sandbox_contract_is_missing
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
  - technical_learning
  - test_pattern_learning
  direct_validated_write_allowed: false
  direct_core_write_allowed: false
  secret_candidate_creation_allowed: false
output_contract:
  base_contract: agent_output.v0.2
  role_specific_output:
    technical_plan: array
    proposed_changes: array
    test_plan: array
    technical_risks: array
validation_policy:
  default_mode: independent
  independent_required_conditions:
  - code_change_is_proposed
  - security_or_runtime_behavior_is_affected
  cannot_lower_policy_or_task_requirement: true
budget_caps:
  schema_version: execution_budget.v0.1
  mode: cap_only
  null_cap_means_no_additional_role_limit: true
  limits:
    max_agent_invocations: 1
    max_model_calls: 8
    max_tool_calls: 12
    max_program_calls: 8
    max_revision_cycles: 2
    max_validation_cycles: 2
    max_retry_count: 2
    max_parallel_workers: 1
    max_runtime_seconds: 1800
    token_budget: null
    cost_budget: null
    cost_currency: null
stop_conditions:
- file_modification_or_code_execution_is_required_without_separate_permission
- deployment_or_secret_access_is_required
- assignment_budget_exhausted
completion_criteria:
- technical_scope_analyzed
- test_and_risk_plan_present
quality_criteria:
- changes_are_scoped
- tests_match_risk
- security_and_rollback_considered
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
activation_blockers:
- program_and_tool_registry
- isolated_execution_environment
- code_change_validation
- explicit_file_and_deployment_permissions
---

# Development Role Candidate

현재는 기술 분석과 초안 제안만 후보로 정의한다. 실제 파일 수정, 코드 실행과 배포는 Tool Registry, 격리 환경, 검증 및 별도 권한 계약이 마련되기 전까지 허용하지 않는다.
