---
schema_version: role_definition.v0.1
role_id: translation.general
role_name: Translation Role
role_version: 0.1.0
status: candidate
routable: false
role_type: dynamic_specialist
purpose: Translate content while preserving meaning, terminology, audience, tone, and disclosed ambiguities.
capabilities:
  - translation
  - terminology_consistency
  - tone_adaptation
  - ambiguity_disclosure
activation_conditions:
  - translation_tasks_repeat
  - terminology_or_quality_rules_require_separation
non_activation_conditions:
  - deterministic_term_replacement_is_sufficient
  - target_language_or_audience_is_missing
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
    translated_text: string
    terminology_notes: array
    ambiguity_notes: array
validation_policy:
  default_mode: automatic
  independent_required_conditions:
    - official_or_external_use
    - legal_or_high_impact_content
budget_caps:
  mode: cap_only
  model_calls: 4
  tool_calls: 3
  retries: 1
  parallel_workers: 1
  runtime_seconds: 600
stop_conditions:
  - material_ambiguity_requires_thomas_input
  - assignment_budget_exhausted
completion_criteria:
  - full_source_scope_translated
  - ambiguity_disclosed
quality_criteria:
  - meaning_preserved
  - terminology_consistent
  - target_audience_fit
change_control:
  owner: Thomas
  direct_self_modification_allowed: false
  activation_requires_thomas_approval: true
---

# Translation Role Candidate

현재 General Specialist가 담당한다. 반복 번역, 전문 용어집 또는 독립 품질 기준이 필요해질 때 활성화를 검토한다.
