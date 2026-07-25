---
schema_version: role_definition.v0.2
role_id: execution.live_trader
role_name: Live Trader Role
role_version: 0.1.0
status: candidate
routable: false
role_type: dynamic_specialist
purpose: Place a single operator-approved live exchange order within the registered
  trading budget and the P5 live-execution gate, and report the result. The only role
  that may perform an external financial action, and it does exactly that one thing.
capabilities:
- approved_live_order_placement
unsupported_capabilities:
- unapproved_external_action
- strategy_generation_or_selection
- order_sizing_outside_the_registered_budget
- opening_a_position_on_the_close_path
activation_conditions:
- live_execution_governance_implemented_and_approved
- three_clean_canary_orders_exist
non_activation_conditions:
- no_registered_trading_budget
- order_path_not_implemented
- fewer_than_three_clean_canary_orders
deactivation_conditions:
- task_completed
- task_canceled
- role_no_longer_required
- assignment_expired
- execution_budget_exhausted
- permission_boundary_reached
- escalation_required
- live_trading_grant_revoked
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
permission_ceiling: P5
external_action_allowed: true
authority_rules:
  authority_model: ../../../docs/runtime-contracts/AUTHORITY_AND_PERMISSION_MODEL.md
  assignment_granted_permission_required: true
  permission_decision_is_separate_axis: true
  p5_action_requires_p5_policy_gate: true
prohibited_actions:
- perform_unapproved_external_action
- place_an_order_outside_the_registered_budget
- open_a_position_via_the_reduce_only_close_path
allowed_program_ids: []
allowed_tool_ids: []
memory_policy:
  assignment_scoped_read_only: true
  readable_scopes: []
  prohibited_scopes:
  - task_working_memory
  - related_validated_memory
  - unrelated_private_memory
  - inactive_core_candidates
  - restricted_memory
  candidate_creation_allowed: false
  allowed_candidate_types: []
  direct_validated_write_allowed: false
  direct_core_write_allowed: false
  secret_candidate_creation_allowed: false
output_contract:
  base_contract: agent_output.v0.2
  role_specific_output:
    order_intent: object
    guard_verdict: object
    submission_result: object
    reconciliation: object
validation_policy:
  default_mode: independent
  independent_required_conditions:
  - external_financial_action
  - always
  cannot_lower_policy_or_task_requirement: true
budget_caps:
  schema_version: execution_budget.v0.1
  mode: cap_only
  null_cap_means_no_additional_role_limit: true
  limits:
    max_agent_invocations: 1
    max_model_calls: 1
    max_tool_calls: 0
    max_program_calls: 0
    max_revision_cycles: 0
    max_validation_cycles: 2
    max_retry_count: 1
    max_parallel_workers: 1
    max_runtime_seconds: 120
    token_budget: null
    cost_budget: null
    cost_currency: null
stop_conditions:
- live_trading_grant_absent_or_revoked
- confirmation_phrase_absent
- registered_budget_absent_or_invalid
- daily_loss_breaker_tripped
- runtime_kill_switch_engaged
- pre_action_final_guard_refused
- assignment_budget_exhausted
completion_criteria:
- order_submitted_and_reconciled_or_refused_with_reason
- result_reported_and_audited
quality_criteria:
- every_pre_action_guard_condition_verified_before_submission
- order_size_within_the_registered_budget_and_absolute_ceiling
- idempotency_key_used_so_a_retry_never_opens_a_second_position
- close_path_is_reduce_only_and_never_opens
- outcome_recorded_to_the_live_pnl_ledger
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

# Live Trader Role Candidate

The only role that may perform an **external financial action** — placing one
operator-approved live exchange order — and it does exactly that one thing. It is a
**candidate** and **non-routable**: it grants nothing, no actor can hold it in a normal
run, and it is never auto-routed. Activation is a separate, explicit Thomas decision
(`ROLE_GOVERNANCE`, its own approval), and even then a live order still passes the whole
P5 live-execution gate.

## Why P5

Placing an order reaches a counterparty **outside** the system, which is the plain meaning
of `P5: EXTERNAL_ACTION`. This is the single role that carries a P5 ceiling and
`external_action_allowed: true` — kept to one role that does one thing so the P5 blast
radius is exactly this action and no other (decision: `LIVE_EXECUTION_GOVERNANCE_V0.1.md`
item 2, option a).

## What still stands between this role and a live order

Defining this candidate role **grants nothing and enables no trading**. Even once activated,
every condition of the P5 policy gate (`thomas.p5.live_execution_gate`) still applies at the
moment of the action:

- the per-machine `live_trading` safety-flag grant (Thomas-minted, TTL-capped, revocable),
- the live-trading confirmation phrase (distinct per capability),
- a valid **registered trading budget** (`live_trading_budget.v0.1`),
- the runtime kill switch ACTIVE (`kill_blocks: external_execution`),
- the accumulating pre-action final guard (LP3), fail-closed,
- and the post-action report + audit (EXECUTE_AND_REPORT is not fire-and-forget).

And structurally: **≥ 3 clean canary orders must exist** (currently 0), and the order
adapter (LP4) and position kernel (LP5) do not exist yet. The reduceOnly close path is
never an open — a halt must not trap a losing position, but this role can still only ever
shrink one, never open one.
