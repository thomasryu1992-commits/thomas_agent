---
schema_version: role_definition.v0.1
role_id: development.general
role_name: Development Role
role_version: 0.2.0
status: candidate
routable: false
role_type: dynamic_specialist
purpose: Analyze technical tasks and produce code plans, review findings, or draft changes inside an explicitly assigned and isolated scope.
capabilities:
  - technical_analysis
  - implementation_planning
  - code_review
  - code_draft_generation
activation_conditions:
  - development_tasks_repeat
  - isolated_execution_and_tool_registry_are_available
  - file_and_runtime_permissions_are_explicit
non_activation_conditions:
  - production_deployment_is_required
  - unrestricted_file_or_shell_access_is_required
  - tool_or_sandbox_contract_is_missing
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
    technical_plan: array
    proposed_changes: array
    test_plan: array
    technical_risks: array
validation_policy:
  default_mode: independent
  independent_required_conditions:
    - code_change_is_proposed
    - security_or_runtime_behavior_is_affected
budget_caps:
  mode: cap_only
  model_calls: 8
  tool_calls: 12
  revision_cycles: 2
  retries: 2
  parallel_workers: 1
  runtime_seconds: 1800
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
activation_blockers:
  - program_and_tool_registry
  - isolated_execution_environment
  - code_change_validation
  - explicit_file_and_deployment_permissions
change_control:
  owner: Thomas
  direct_self_modification_allowed: false
  activation_requires_thomas_approval: true
---

# Development Role Candidate

현재는 기술 분석과 초안 제안만 후보로 정의한다. 실제 파일 수정, 코드 실행과 배포는 Tool Registry, 격리 환경, 검증 및 별도 권한 계약이 마련되기 전까지 허용하지 않는다.
