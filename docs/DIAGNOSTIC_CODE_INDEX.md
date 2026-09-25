# Diagnostic code index — GENERATED, do not edit by hand

Regenerate with `python scripts/build_diagnostic_code_index.py`. `tests/test_diagnostic_code_index.py` fails when this file and the code disagree, so a new reason code lands here or the suite goes red.

Answers the question `REMAINING_WORK.md` §G3 says an operator actually asks: **a code came out of the runtime — where is it raised, and what test is it behind?** The `condition` column is the guarding `if`, unparsed from the source, so it cannot drift from what the code does the way a written description would.

- **631** distinct codes across **1223** raise sites
- **23** exception classes carry them
- **75** codes are raised from more than one module (see below)
- **134** raise sites build their code at runtime rather than from a literal and are not indexable; they are counted rather than guessed at
- **29** raise sites carry a human-readable **message** where a code would go, so there is nothing to look up — a different gap from the line above, and counted apart from it

## Codes raised from more than one module

Not automatically a defect — `APPROVAL_EXPIRED` meaning one thing in seven modules is shared vocabulary working correctly. It is here because the opposite case looks identical from the outside: one code, two meanings, and an operator reading it back gets the wrong module. The test pins this set, so the next one is a decision rather than an accident.

| code | modules |
|---|---|
| `ALREADY_CONSUMED` | `approval.py`, `switch_bridge.py` |
| `APPROVAL_CONTENT_MISMATCH` | `probe.py`, `promotion.py`, `retirement.py`, `registration.py` |
| `APPROVAL_EXPIRED` | `approval.py`, `probe.py`, `promotion.py`, `retirement.py`, `registration.py`, `switch_bridge.py` |
| `APPROVAL_MISSING` | `probe.py`, `promotion.py`, `retirement.py`, `registration.py` |
| `APPROVAL_NOT_APPROVED` | `probe.py`, `promotion.py`, `retirement.py`, `registration.py` |
| `APPROVAL_WRONG_ACTION` | `probe.py`, `promotion.py`, `retirement.py`, `registration.py` |
| `ARGUMENT_NOT_ACCEPTED` | `control.py`, `dispatch_bridge.py`, `knowledge_bridge.py`, `pipeline_worker.py`, `read_bridge.py`, `switch_bridge.py` |
| `AUTHORITY_RECORD_INVALID` | `policy.py`, `preflight.py` |
| `CANDIDATE_EXPIRED` | `consumption.py`, `memory_console.py` |
| `CANDIDATE_GONE` | `consumption.py`, `memory_console.py` |
| `CANDIDATE_INPUT_INVALID` | `programization.py`, `programization_cli.py` |
| `CANDIDATE_NOT_FOUND` | `program_request.py`, `programization.py`, `registration.py` |
| `CONSUMPTION_DISABLED` | `consumption.py`, `trial.py` |
| `CONTENT_CHANGED` | `consumption.py`, `trial.py` |
| `ENTRY_NOT_FOUND` | `registry_console.py`, `task_registry.py` |
| `FINGERPRINT_MISMATCH` | `approval.py`, `switch_bridge.py` |
| `FINGERPRINT_UNCOMPUTABLE` | `approval.py`, `switch_bridge.py` |
| `IDEATION_INPUTS_REQUIRED` | `blog_content.py`, `pipeline_worker.py` |
| `INVALID_CANDIDATE` | `memory.py`, `permission.py` |
| `INVALID_ROLE` | `assignment.py`, `permission.py` |
| `INVALID_TIMESTAMP` | `intake.py`, `permission.py` |
| `KILL_STATE_UNAVAILABLE` | `memory_console.py`, `registry_console.py` |
| `KIND_NOT_PERMITTED` | `dispatch_bridge.py`, `pipeline_worker.py` |
| `LEDGER_UNREADABLE` | `control.py`, `store.py` |
| `LEDGER_WRITE_FAILED` | `bridge_idempotency.py`, `store.py` |
| `LIFECYCLE_DECISION_INVALID` | `lifecycle.py`, `pool_transitions.py` |
| `LIFECYCLE_TERMINAL_IMMUTABLE` | `lifecycle.py`, `pool_transitions.py`, `retirement.py` |
| `LIFECYCLE_UNKNOWN_STRATEGY` | `pool_transitions.py`, `retirement.py` |
| `LIVE_HISTORY_TAMPERED` | `live_ledger.py`, `live_pnl.py` |
| `MALFORMED_DIRECTION` | `live_order.py`, `live_position.py` |
| `MALFORMED_REQUEST` | `bridge_idempotency.py`, `dispatch_bridge.py`, `knowledge_bridge.py`, `pipeline_worker.py`, `read_bridge.py`, `socket_door.py`, `switch_bridge.py` |
| `MALFORMED_RESULT` | `account.py`, `market_data.py`, `naver_research.py`, `tools.py` |
| `MISSING_OPERATOR` | `memory.py`, `program_request.py`, `programization.py` |
| `MISSING_REASON` | `memory.py`, `memory_console.py`, `program_request.py`, `programization.py` |
| `MISSING_SYMBOL` | `live_order.py`, `live_position.py` |
| `NOT_APPROVED` | `approval.py`, `switch_bridge.py` |
| `NOT_A_CANDIDATE` | `memory.py`, `planner.py` |
| `NOT_BOUND` | `assignment.py`, `permission.py`, `validator.py`, `worker.py` |
| `NO_API_KEY` | `account.py`, `market_data.py`, `naver_research.py`, `providers.py`, `tools.py` |
| `NO_MODEL_BUDGET` | `validator.py`, `worker.py` |
| `NO_ORDER_API_KEY` | `live_execution.py`, `testnet_execution.py` |
| `ORDER_HALTED` | `live_execution.py`, `testnet_execution.py` |
| `ORDER_MALFORMED_RESULT` | `live_execution.py`, `order_request.py`, `testnet_execution.py` |
| `ORDER_OUTCOME_UNKNOWN` | `live_execution.py`, `testnet_execution.py` |
| `ORDER_REJECTED` | `live_execution.py`, `testnet_execution.py` |
| `ORDER_TRANSPORT` | `live_execution.py`, `testnet_execution.py` |
| `PATTERN_NOT_FOUND` | `programization.py`, `programization_cli.py` |
| `PERMISSION_DECISION_MISSING` | `approval.py`, `switch_bridge.py` |
| `PLANNED_TASK_INVALID` | `prime.py`, `trial.py` |
| `PLAN_INVALID` | `dispatch_bridge.py`, `workflow.py` |
| `POLICY_UNAVAILABLE` | `approval.py`, `permission.py` |
| `PROVIDER_ERROR` | `validator.py`, `worker.py` |
| `REASON_REQUIRED` | `dispatch_bridge.py`, `pipeline_worker.py`, `switch_bridge.py` |
| `REGISTRY_UNAVAILABLE` | `planner.py`, `registry_console.py` |
| `REGISTRY_UNRESOLVABLE` | `program_request.py`, `registration.py` |
| `REQUEST_REQUIRED` | `dispatch_bridge.py`, `pipeline_worker.py` |
| `RESPONSE_TRUNCATED` | `validator.py`, `worker.py` |
| `ROLE_DEFINITION_INVALID` | `assignment.py`, `planner.py` |
| `ROUTE_NOT_SUPPORTED` | `router.py`, `worker_port.py` |
| `SCHEDULES_UNAVAILABLE` | `dispatch_bridge.py`, `store_reads.py` |
| `SECRET_IN_CANDIDATE` | `memory.py`, `programization.py` |
| `TOKEN_BUDGET_EXCEEDED` | `validator.py`, `worker.py` |
| `TOOL_ERROR` | `market_data.py`, `naver_research.py`, `tools.py` |
| `TOOL_TRANSPORT` | `market_data.py`, `naver_research.py`, `tools.py` |
| `TRANSITION_INVALID` | `task_registry.py`, `workflow.py`, `workflow_store.py` |
| `UNKNOWN_APPROVAL` | `approval.py`, `switch_bridge.py` |
| `UNKNOWN_CANDIDATE` | `approval_cli.py`, `pool.py` |
| `UNKNOWN_COMMAND` | `approval.py`, `control.py`, `registry_console.py` |
| `UNKNOWN_KIND` | `schedule_delegation.py`, `scheduler.py` |
| `UNKNOWN_REQUEST_KIND` | `planner.py`, `task_registry.py` |
| `USAGE` | `memory_console.py`, `registry_console.py`, `store_reads.py` |
| `VALIDATION_RESULT_INVALID` | `validation.py`, `validator.py` |
| `VERB_NOT_PERMITTED` | `dispatch_bridge.py`, `knowledge_bridge.py`, `read_bridge.py`, `switch_bridge.py` |
| `WORKER_UNAVAILABLE` | `dispatch_bridge.py`, `scheduler.py` |
| `WORKFLOW_UNAVAILABLE` | `dispatch_bridge.py`, `scheduler.py` |

## Every code

| code | class | module | line | function | condition |
|---|---|---|---|---|---|
| `ABSOLUTE_PATH` | `ToolBlocked` | `runtime/mvp_runtime/workspace.py` | 173 | `resolve_target` | `candidate.is_absolute() or candidate.drive or relative_path.startswith(('/', '\\'))` |
| `ACCEPT_REQUIRES_SHADOW_PASS` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 587 | `transition_candidate` | `action == 'accept' and shadow.get('status') != 'PASS'` |
| `ACCOUNT_FEED_NOT_CONFIGURED` | `ToolError` | `runtime/mvp_runtime/crypto/account_store.py` | 321 | `load_funds_view` | `not body.get('configured', False)` |
| `ACCOUNT_SNAPSHOT_DEGRADED` | `ToolError` | `runtime/mvp_runtime/crypto/account_store.py` | 326 | `load_funds_view` | `body.get('degraded')` |
| `ACCOUNT_SNAPSHOT_MISSING` | `ToolError` | `runtime/mvp_runtime/crypto/account_store.py` | 314 | `load_funds_view` | `body is None` |
| `ACCOUNT_SNAPSHOT_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/account_store.py` | 309 | `load_funds_view` | `snapshot_path(root).is_file()` |
| `ACTIVATION_CHANGED` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 172 | `assert_authorization` | `current != authorization.activation_sha256` |
| `ACTIVATION_EXPIRED` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 133 | `assert_authorization` | `now >= authorization.expires_at` |
| `ACTIVATION_REVOKED` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 159 | `assert_authorization` | `not record_path.is_file()` |
| `ACTOR_PROFILE_NOT_PERMITTED` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 286 | `apply_work` | `isinstance(raw_profile, str) and raw_profile.strip() in _ACTOR_PROFILES` |
| `ALREADY_CLASSIFIED` | `PlannerBlocked` | `runtime/mvp_runtime/planner.py` | 198 | `classify_task` | `classification.get('classification_status') != 'UNCLASSIFIED'` |
| `ALREADY_CONSUMED` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 278 | `validate_spendable_approval` | `status == STATUS_CONSUMED` |
| `ALREADY_CONSUMED` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 332 | `spend_lock` | `latest is None or latest.get('status') != STATUS_APPROVED` |
| `ALREADY_CONSUMED` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 439 | `build_consumed_record` | `status == STATUS_CONSUMED` |
| `ALREADY_CONSUMED` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 635 | `_spend` | `status == approval_mod.STATUS_CONSUMED` |
| `ALREADY_CONSUMED` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 726 | `_spend` | `fresh is None or fresh.get('status') != approval_mod.STATUS_APPROVED` |
| `ALREADY_REGISTERED` | `ProgramizationBlocked` | `runtime/mvp_runtime/registration.py` | 169 | `request_registration` | `_registry_has(registry, program_id, version)` |
| `ALREADY_REGISTERED` | `ProgramizationBlocked` | `runtime/mvp_runtime/registration.py` | 261 | `apply_registration` | `_registry_has(registry, program_id, version)` |
| `AMBIGUOUS_ENTRY_ID` | `TaskRegistryBlocked` | `runtime/mvp_runtime/task_registry.py` | 442 | `find` | `len(by_run) > 1` |
| `AMBIGUOUS_ENTRY_ID` | `TaskRegistryBlocked` | `runtime/mvp_runtime/task_registry.py` | 450 | `find` | `len(matches) > 1` |
| `AMBIGUOUS_ROLE` | `PlannerBlocked` | `runtime/mvp_runtime/planner.py` | 333 | `select_role` | `len(candidates) > 1` |
| `ANNOUNCE_POINTER_PERSIST_FAILED` | `OperatorBlocked` | `runtime/mvp_runtime/operator.py` | 1024 | `record_announced` | `—` |
| `APPROVALS_UNAVAILABLE` | `ControlBlocked` | `runtime/mvp_runtime/store_reads.py` | 148 | `read_approval_status` | `approval_store is None` |
| `APPROVAL_ARTIFACT_UNSIGNED` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/promotion.py` | 719 | `verify_promotion_approval` | `unsigned` |
| `APPROVAL_CONTENT_MISMATCH` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/probe.py` | 909 | `verify_probe_approval` | `snapshot.get('content_sha256') != probe_content_sha256(params)` |
| `APPROVAL_CONTENT_MISMATCH` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/promotion.py` | 705 | `verify_promotion_approval` | `snapshot.get('content_sha256') != content_sha256_of(candidates, keep_active=keep_active, live_t…` |
| `APPROVAL_CONTENT_MISMATCH` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/retirement.py` | 174 | `verify_retirement_approval` | `snapshot.get('content_sha256') != retirement_content_sha256(checked)` |
| `APPROVAL_CONTENT_MISMATCH` | `ApprovalBlocked` | `runtime/mvp_runtime/registration.py` | 229 | `verify_registration_approval` | `snapshot.get('content_sha256') != expected` |
| `APPROVAL_EXPIRED` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 284 | `validate_spendable_approval` | `is_expired(approval_rec, now=now)` |
| `APPROVAL_EXPIRED` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 363 | `record_decision` | `is_expired(approval, now=now)` |
| `APPROVAL_EXPIRED` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 867 | `apply_command` | `approval.get('status') == STATUS_PENDING and is_expired(approval, now=now)` |
| `APPROVAL_EXPIRED` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/probe.py` | 901 | `verify_probe_approval` | `not isinstance(expires_at, str) or timeutil.parse_iso(expires_at) <= timeutil.parse_iso(now)` |
| `APPROVAL_EXPIRED` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/promotion.py` | 678 | `verify_promotion_approval` | `not isinstance(expires_at, str) or timeutil.parse_iso(expires_at) <= timeutil.parse_iso(now)` |
| `APPROVAL_EXPIRED` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/retirement.py` | 165 | `verify_retirement_approval` | `not isinstance(expires_at, str) or timeutil.parse_iso(expires_at) <= timeutil.parse_iso(now)` |
| `APPROVAL_EXPIRED` | `ApprovalBlocked` | `runtime/mvp_runtime/registration.py` | 222 | `verify_registration_approval` | `not isinstance(expires_at, str) or timeutil.parse_iso(expires_at) <= timeutil.parse_iso(now)` |
| `APPROVAL_EXPIRED` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 643 | `_spend` | `approval_mod.is_expired(record, now=now)` |
| `APPROVAL_MISSING` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/probe.py` | 895 | `verify_probe_approval` | `approval is None` |
| `APPROVAL_MISSING` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/promotion.py` | 672 | `verify_promotion_approval` | `approval is None` |
| `APPROVAL_MISSING` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/retirement.py` | 159 | `verify_retirement_approval` | `approval is None` |
| `APPROVAL_MISSING` | `ApprovalBlocked` | `runtime/mvp_runtime/registration.py` | 216 | `verify_registration_approval` | `approval is None` |
| `APPROVAL_NOT_APPROVED` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/probe.py` | 898 | `verify_probe_approval` | `status != 'APPROVED'` |
| `APPROVAL_NOT_APPROVED` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/promotion.py` | 675 | `verify_promotion_approval` | `status != 'APPROVED'` |
| `APPROVAL_NOT_APPROVED` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/retirement.py` | 162 | `verify_retirement_approval` | `status != 'APPROVED'` |
| `APPROVAL_NOT_APPROVED` | `ApprovalBlocked` | `runtime/mvp_runtime/registration.py` | 219 | `verify_registration_approval` | `status != 'APPROVED'` |
| `APPROVAL_NOT_BOUND` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 981 | `approve_step` | `s['approval_id'] != approval_id or s['approval_plan_version'] != int(plan_version) or int(curre…` |
| `APPROVAL_NOT_CONSUMED` | `AuditError` | `runtime/mvp_runtime/audit.py` | 801 | `build_approval_consumption_audit` | `approval.get('status') != 'CONSUMED'` |
| `APPROVAL_NOT_CONSUMED` | `AuditError` | `runtime/mvp_runtime/audit.py` | 856 | `build_trial_consumption_audit` | `approval.get('status') != 'CONSUMED'` |
| `APPROVAL_NOT_FOUND` | `ControlBlocked` | `runtime/mvp_runtime/store_reads.py` | 157 | `read_approval_status` | `record is None` |
| `APPROVAL_OUTSIDE_ARM_WINDOW` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/promotion.py` | 691 | `verify_promotion_approval` | `answered is None or expires is None or (not answered <= timeutil.parse_iso(now) < expires)` |
| `APPROVAL_READ_FAILED` | `PersistenceError` | `runtime/mvp_runtime/approval_store.py` | 71 | `read_all` | `—` |
| `APPROVAL_READ_FAILED` | `PersistenceError` | `runtime/mvp_runtime/approval_store.py` | 115 | `get_permission_decision` | `—` |
| `APPROVAL_SCHEMA_INVALID` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 131 | `_validate` | `—` |
| `APPROVAL_SEMANTICS_INVALID` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 136 | `_validate` | `issues` |
| `APPROVAL_UNVERIFIED` | `AuditError` | `runtime/mvp_runtime/audit.py` | 753 | `build_approval_decision_audit` | `approver.get('verification_status') != 'VERIFIED'` |
| `APPROVAL_UNVERIFIED` | `AuditError` | `runtime/mvp_runtime/audit.py` | 804 | `build_approval_consumption_audit` | `approver.get('verification_status') != 'VERIFIED'` |
| `APPROVAL_UNVERIFIED` | `AuditError` | `runtime/mvp_runtime/audit.py` | 859 | `build_trial_consumption_audit` | `approver.get('verification_status') != 'VERIFIED'` |
| `APPROVAL_WRITE_FAILED` | `PersistenceError` | `runtime/mvp_runtime/approval_store.py` | 65 | `append` | `—` |
| `APPROVAL_WRITE_FAILED` | `PersistenceError` | `runtime/mvp_runtime/approval_store.py` | 107 | `append_permission_decision` | `—` |
| `APPROVAL_WRONG_ACTION` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/probe.py` | 904 | `verify_probe_approval` | `snapshot.get('action_type') != PROBE_ACTION_TYPE` |
| `APPROVAL_WRONG_ACTION` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/promotion.py` | 682 | `verify_promotion_approval` | `snapshot.get('action_type') != PROMOTION_ACTION_TYPE` |
| `APPROVAL_WRONG_ACTION` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/retirement.py` | 169 | `verify_retirement_approval` | `snapshot.get('action_type') != RETIREMENT_ACTION_TYPE` |
| `APPROVAL_WRONG_ACTION` | `ApprovalBlocked` | `runtime/mvp_runtime/registration.py` | 225 | `verify_registration_approval` | `snapshot.get('action_type') != REGISTRATION_ACTION_TYPE` |
| `ARCHIVE_ALL_BOOKS_DEGRADED` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 2002 | `_execute` | `summary['books'] and summary['degraded'] == summary['books']` |
| `ARCHIVE_NAME_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/candle_archive.py` | 134 | `archive_path` | `not all((part and _SAFE_NAME.fullmatch(part) for part in parts))` |
| `ARCHIVE_NOT_ENABLED` | `ToolBlocked` | `runtime/mvp_runtime/crypto/market_data.py` | 1011 | `collect` | `—` |
| `ARCHIVE_NOT_ENABLED` | `ToolBlocked` | `runtime/mvp_runtime/crypto/market_data.py` | 1017 | `live_symbols` | `—` |
| `ARCHIVE_RATE_LIMITED` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 2014 | `_execute` | `summary.get('rate_limited')` |
| `ARCHIVE_SYMBOL_MISSING` | `ToolError` | `runtime/mvp_runtime/crypto/candle_archive.py` | 291 | `append_candles` | `not str(symbol).strip()` |
| `ARCHIVE_TIMEFRAME_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/candle_archive.py` | 143 | `_require_timeframe` | `timeframe not in TIMEFRAMES` |
| `ARCHIVE_UNIVERSE_UNREADABLE` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 1995 | `_execute` | `summary['blocked']` |
| `ARGUMENT_NOT_ACCEPTED` | `ControlBlocked` | `runtime/mvp_runtime/control.py` | 824 | `apply_command` | `expected_state is not None and command != CMD_RESUME` |
| `ARGUMENT_NOT_ACCEPTED` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 249 | `apply_dispatch` | `unexpected` |
| `ARGUMENT_NOT_ACCEPTED` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 439 | `apply_workflow_command` | `unexpected` |
| `ARGUMENT_NOT_ACCEPTED` | `ControlBlocked` | `runtime/mvp_runtime/knowledge_bridge.py` | 128 | `apply_knowledge` | `unexpected` |
| `ARGUMENT_NOT_ACCEPTED` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 239 | `apply_work` | `unexpected` |
| `ARGUMENT_NOT_ACCEPTED` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 554 | `_apply_job` | `unexpected` |
| `ARGUMENT_NOT_ACCEPTED` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 580 | `_apply_job` | `other in request` |
| `ARGUMENT_NOT_ACCEPTED` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 612 | `_apply_job` | `'proposal_inputs' in request` |
| `ARGUMENT_NOT_ACCEPTED` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 634 | `_apply_job` | `'inventory' in request` |
| `ARGUMENT_NOT_ACCEPTED` | `ControlBlocked` | `runtime/mvp_runtime/read_bridge.py` | 158 | `apply_read` | `argument is not None and command not in _TAKES_ARGUMENT` |
| `ARGUMENT_NOT_ACCEPTED` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 562 | `_emergency_close_ask` | `unexpected` |
| `ARGUMENT_NOT_ACCEPTED` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 814 | `apply_switch` | `unexpected` |
| `ARGUMENT_NOT_ACCEPTED` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 901 | `apply_switch` | `approval_id is not None and 'scope' in request` |
| `ASSIGNMENT_LINEAGE_MISMATCH` | `KernelBlocked` | `runtime/read_only_kernel/router.py` | 18 | `select_route` | `assignment.get('assignment_id') != routing.get('role_assignment_ids', [None])[0]` |
| `ASSIGNMENT_SCHEMA_INVALID` | `PlannerBlocked` | `runtime/mvp_runtime/assignment.py` | 199 | `build_role_assignment` | `—` |
| `ATTEMPTS_EXHAUSTED` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 892 | `retry_step` | `int(s['attempts_opened']) >= wf.MAX_ATTEMPTS_PER_STEP` |
| `ATTEMPT_FENCED` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 753 | `record_result` | `fenced is not None` |
| `ATTEMPT_FENCED` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 758 | `record_result` | `a['status'] != wf.A_RUNNING or s['current_attempt_id'] != attempt_id` |
| `ATTEMPT_FENCED` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 1194 | `confirm_cancelled` | `a['status'] != wf.A_RUNNING or s['current_attempt_id'] != attempt_id` |
| `ATTEMPT_NOT_FOUND` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 739 | `record_result` | `a is None` |
| `ATTEMPT_NOT_FOUND` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 1191 | `confirm_cancelled` | `a is None` |
| `AUDIT_EVENT_INVALID` | `AuditError` | `runtime/mvp_runtime/audit.py` | 162 | `_make_event` | `—` |
| `AUTHORITY_INSUFFICIENT` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 638 | `build_permission_decision` | `not authority_sufficient and disposition != 'BLOCK'` |
| `AUTHORITY_INVARIANT` | `PlannerBlocked` | `runtime/mvp_runtime/assignment.py` | 90 | `build_role_assignment` | `not invariant_holds` |
| `AUTHORITY_RECORD_INVALID` | `KernelBlocked` | `runtime/read_only_kernel/policy.py` | 23 | `adapt_policy` | `authority.get('effective_permission_level') is None` |
| `AUTHORITY_RECORD_INVALID` | `KernelBlocked` | `runtime/read_only_kernel/preflight.py` | 369 | `run_preflight` | `—` |
| `BINDING_FAILED` | `PlannerBlocked` | `runtime/mvp_runtime/binding.py` | 55 | `bind_task_to_core` | `—` |
| `BRIDGE_ALREADY_RUNNING` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 438 | `__init__` | `door_is_live(path)` |
| `BRIDGE_CLIENT_GID_INVALID` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 558 | `resolve_client_gid` | `—` |
| `BRIDGE_CLIENT_GID_INVALID` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 563 | `resolve_client_gid` | `gid < 0` |
| `BRIDGE_CLIENT_GID_UNAVAILABLE` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 693 | `grant_client_access` | `—` |
| `BRIDGE_CLIENT_UID_INVALID` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 589 | `resolve_client_uids` | `—` |
| `BRIDGE_CLIENT_UID_INVALID` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 595 | `resolve_client_uids` | `uid < 0` |
| `BRIDGE_CLIENT_UID_INVALID` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 600 | `resolve_client_uids` | `not uids` |
| `BRIDGE_CONCURRENCY_INVALID` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 406 | `__init__` | `max_concurrent_requests < 1` |
| `BRIDGE_LIMITS_INVALID` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 412 | `__init__` | `max_frame_bytes < 1 or request_timeout_seconds <= 0` |
| `BUDGET_EXHAUSTED` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow.py` | 349 | `validate_plan` | `needed > budget.max_model_calls` |
| `CANARY_HISTORY_DUPLICATE` | `ToolError` | `runtime/mvp_runtime/crypto/live_promotion.py` | 83 | `read_canary_orders` | `order_id in seen` |
| `CANARY_HISTORY_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/live_promotion.py` | 79 | `read_canary_orders` | `not isinstance(stored, str) or integrity.sha256_record(body) != stored` |
| `CANARY_HISTORY_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_promotion.py` | 68 | `read_canary_orders` | `—` |
| `CANDIDATES_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/pool_state.py` | 259 | `read_candidates` | `not isinstance(stored, str) or integrity.sha256_record(body) != stored` |
| `CANDIDATES_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/pool_state.py` | 247 | `read_candidates` | `—` |
| `CANDIDATE_AMBIGUOUS` | `ToolError` | `runtime/mvp_runtime/crypto/pool.py` | 202 | `resolve_candidates` | `ambiguous` |
| `CANDIDATE_BEHAVIOUR_CLUSTER_OCCUPIED` | `ToolError` | `runtime/mvp_runtime/crypto/pool_admission.py` | 895 | `assert_no_cluster_siblings` | `—` |
| `CANDIDATE_BELOW_OBSERVATION_ENTRY_BAR` | `ToolError` | `runtime/mvp_runtime/crypto/pool_admission.py` | 1011 | `assert_observation_entry_bar` | `—` |
| `CANDIDATE_COST_BASIS_STALE` | `ToolError` | `runtime/mvp_runtime/crypto/pool_admission.py` | 80 | `assert_promotable_cost_basis` | `stale` |
| `CANDIDATE_DERIVATION_NOT_PROMOTABLE` | `ToolError` | `runtime/mvp_runtime/crypto/pool_admission.py` | 1098 | `assert_promotable_derivation` | `refused` |
| `CANDIDATE_EMPTY` | `ApprovalBlocked` | `runtime/mvp_runtime/consumption.py` | 137 | `_revalidated_candidate` | `not (isinstance(content, str) and content.strip())` |
| `CANDIDATE_EVIDENCE_DEPTH_UNRECORDED` | `ToolError` | `runtime/mvp_runtime/crypto/pool_admission.py` | 155 | `assert_promotable_evidence_depth` | `unknown` |
| `CANDIDATE_EXISTS` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 513 | `create_program_candidate` | `any((c.get('pattern_id') == pattern_id for c in store.read_candidates()))` |
| `CANDIDATE_EXPIRED` | `ApprovalBlocked` | `runtime/mvp_runtime/consumption.py` | 130 | `_revalidated_candidate` | `memory_is_expired(candidate, now=now)` |
| `CANDIDATE_EXPIRED` | `OperatorBlocked` | `runtime/mvp_runtime/memory_console.py` | 205 | `apply_memory_command` | `memory.is_expired(match, stamp)` |
| `CANDIDATE_FAMILY_CAP_EXCEEDED` | `ToolError` | `runtime/mvp_runtime/crypto/pool_admission.py` | 1054 | `assert_family_cap` | `—` |
| `CANDIDATE_GONE` | `ApprovalBlocked` | `runtime/mvp_runtime/consumption.py` | 122 | `_revalidated_candidate` | `candidate is None` |
| `CANDIDATE_GONE` | `OperatorBlocked` | `runtime/mvp_runtime/memory_console.py` | 199 | `apply_memory_command` | `match is None` |
| `CANDIDATE_INPUT_INVALID` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 484 | `create_program_candidate` | `not isinstance(review_input, Mapping)` |
| `CANDIDATE_INPUT_INVALID` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 491 | `create_program_candidate` | `not items` |
| `CANDIDATE_INPUT_INVALID` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 496 | `create_program_candidate` | `not (isinstance(rollback_ref, str) and rollback_ref.strip())` |
| `CANDIDATE_INPUT_INVALID` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization_cli.py` | 106 | `_load_review_input` | `not path_str` |
| `CANDIDATE_INPUT_INVALID` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization_cli.py` | 111 | `_load_review_input` | `—` |
| `CANDIDATE_INPUT_INVALID` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization_cli.py` | 113 | `_load_review_input` | `not isinstance(loaded, dict)` |
| `CANDIDATE_LINEAGE_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/pool_state.py` | 84 | `validate_candidate_lineage` | `not has_type` |
| `CANDIDATE_LINEAGE_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/pool_state.py` | 86 | `validate_candidate_lineage` | `derivation not in DERIVATION_TYPES` |
| `CANDIDATE_LINEAGE_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/pool_state.py` | 89 | `validate_candidate_lineage` | `not isinstance(parents, list) or not all((isinstance(p, str) and p for p in parents))` |
| `CANDIDATE_LINEAGE_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/pool_state.py` | 91 | `validate_candidate_lineage` | `len(set(parents)) != len(parents)` |
| `CANDIDATE_LINEAGE_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/pool_state.py` | 94 | `validate_candidate_lineage` | `len(parents) < lo or (hi is not None and len(parents) > hi)` |
| `CANDIDATE_NOT_FOUND` | `ProgramizationBlocked` | `runtime/mvp_runtime/program_request.py` | 146 | `create_program_request` | `candidate is None` |
| `CANDIDATE_NOT_FOUND` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 547 | `_require_candidate` | `latest is None` |
| `CANDIDATE_NOT_FOUND` | `ProgramizationBlocked` | `runtime/mvp_runtime/registration.py` | 64 | `_lineage` | `candidate is None` |
| `CANDIDATE_NOT_RETIRED` | `ApprovalBlocked` | `runtime/mvp_runtime/consumption.py` | 254 | `consume_approval` | `—` |
| `CANDIDATE_RECORD_UNSTAMPED` | `ToolError` | `runtime/mvp_runtime/crypto/pool_admission.py` | 1145 | `assert_promotable_record_stamp` | `refused` |
| `CANDIDATE_REQUIRES_REVIEW` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 508 | `create_program_candidate` | `latest.get('review_status') != 'UNDER_REVIEW'` |
| `CANDIDATE_SEMANTIC_DUPLICATE` | `ToolError` | `runtime/mvp_runtime/crypto/pool_admission.py` | 312 | `assert_no_semantic_duplicates` | `—` |
| `CANDIDATE_UNCONFIRMED_FOR_LIVE` | `ToolError` | `runtime/mvp_runtime/crypto/forward_confirmation.py` | 353 | `assert_live_tier_confirmed` | `—` |
| `CANDIDATE_UNHASHED` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/promotion.py` | 168 | `_resolve_identity` | `not (isinstance(c.get('strategy_rule_hash'), str) and c['strategy_rule_hash'])` |
| `CANDIDATE_VERSION_MISMATCH` | `PlannerBlocked` | `runtime/mvp_runtime/planner.py` | 288 | `select_candidate_role` | `version is not None and role.get('version') != version` |
| `CAPABILITY_EXCEEDS_ROLE` | `PlannerBlocked` | `runtime/mvp_runtime/assignment.py` | 75 | `build_role_assignment` | `not set(required_capabilities).issubset(capabilities)` |
| `CAPABILITY_NOT_PERMITTED` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow.py` | 311 | `validate_plan` | `cap is None` |
| `CAPACITY_EXHAUSTED` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 334 | `submit` | `open_steps + len(validated.steps) > MAX_OPEN_STEPS` |
| `CAPACITY_EXHAUSTED` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 1059 | `propose_update` | `open_steps + added - dropped > MAX_OPEN_STEPS` |
| `CHANNEL_PARTIAL_DELIVERY` | `OperatorBlocked` | `runtime/mvp_runtime/operator.py` | 1419 | `send` | `—` |
| `CHANNEL_TRANSPORT` | `OperatorBlocked` | `runtime/mvp_runtime/operator.py` | 1283 | `_call` | `—` |
| `CHANNEL_TRANSPORT` | `OperatorBlocked` | `runtime/mvp_runtime/operator.py` | 1285 | `_call` | `not isinstance(payload, dict) or not payload.get('ok')` |
| `CHAT_NOT_REGISTERED` | `OperatorBlocked` | `runtime/mvp_runtime/operator.py` | 247 | `verify_control_channel` | `not isinstance(message.chat_id, str) or message.chat_id != registration.chat_id` |
| `CONSUMED_NOT_PROMOTED` | `ApprovalBlocked` | `runtime/mvp_runtime/consumption.py` | 241 | `consume_approval` | `—` |
| `CONSUMED_UNAUDITED` | `ApprovalBlocked` | `runtime/mvp_runtime/consumption.py` | 262 | `consume_approval` | `—` |
| `CONSUMPTION_DISABLED` | `ApprovalBlocked` | `runtime/mvp_runtime/consumption.py` | 72 | `consume` | `—` |
| `CONSUMPTION_DISABLED` | `ApprovalBlocked` | `runtime/mvp_runtime/trial.py` | 96 | `authorize_spend` | `—` |
| `CONSUMPTION_SUBJECT_INVALID` | `AuditError` | `runtime/mvp_runtime/audit.py` | 808 | `build_approval_consumption_audit` | `not (isinstance(validated_id, str) and validated_id)` |
| `CONSUMPTION_SUBJECT_INVALID` | `AuditError` | `runtime/mvp_runtime/audit.py` | 862 | `build_trial_consumption_audit` | `not (isinstance(trial_task_id, str) and trial_task_id)` |
| `CONTENT_CHANGED` | `ApprovalBlocked` | `runtime/mvp_runtime/consumption.py` | 140 | `_revalidated_candidate` | `integrity.sha256_record({'content': content}) != snapshot.get('content_sha256')` |
| `CONTENT_CHANGED` | `ApprovalBlocked` | `runtime/mvp_runtime/trial.py` | 410 | `run_trial` | `trial_content_sha256(role, trial_request) != snapshot.get('content_sha256')` |
| `CONTENT_TOO_LARGE` | `ToolBlocked` | `runtime/mvp_runtime/workspace.py` | 216 | `_require_content` | `size > MAX_CONTENT_BYTES` |
| `CONTROL_CHARS` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 76 | `_reject_control_chars` | `code < 32 and ch not in _ALLOWED_CONTROL_CHARS or code == 127` |
| `CONTROL_STATE_CHANGED` | `ControlBlocked` | `runtime/mvp_runtime/control.py` | 1103 | `apply_command` | `latest != baseline` |
| `CONTROL_VERB_NOT_GRANTED` | `ControlBlocked` | `runtime/mvp_runtime/control.py` | 829 | `apply_command` | `command in POLICY_GATED_COMMANDS and command not in granted_emergency_controls()` |
| `CONTROL_WRITE_FAILED` | `ControlBlocked` | `runtime/mvp_runtime/control.py` | 646 | `save` | `—` |
| `CORE_CANDIDATE_ALREADY_DECIDED` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 483 | `decide_core_candidate` | `candidate.get('status') != CORE_CANDIDATE_STATUS` |
| `CORE_NOT_ACTIVATED` | `PlannerBlocked` | `runtime/mvp_runtime/binding.py` | 46 | `bind_task_to_core` | `not pointer.is_file()` |
| `CORRECTION_AMBIGUOUS` | `ToolError` | `runtime/mvp_runtime/crypto/live_correction.py` | 347 | `apply_corrections` | `target_id in targets` |
| `CORRECTION_ARITHMETIC_DISAGREES` | `ToolError` | `runtime/mvp_runtime/crypto/live_correction.py` | 261 | `verify_correction` | `recomputed is None` |
| `CORRECTION_ARITHMETIC_DISAGREES` | `ToolError` | `runtime/mvp_runtime/crypto/live_correction.py` | 267 | `verify_correction` | `abs(float(correction['corrected_realized_pnl_usdt']) - realized) > tolerance` |
| `CORRECTION_ARITHMETIC_DISAGREES` | `ToolError` | `runtime/mvp_runtime/crypto/live_correction.py` | 275 | `verify_correction` | `abs(float(correction['corrected_result_R']) - result_r) > tolerance / risk` |
| `CORRECTION_ARITHMETIC_DISAGREES` | `ToolError` | `runtime/mvp_runtime/crypto/live_correction.py` | 410 | `build_correction` | `recomputed is None` |
| `CORRECTION_TARGET_CHANGED` | `ToolError` | `runtime/mvp_runtime/crypto/live_correction.py` | 252 | `verify_correction` | `target.get('record_sha256') != correction['corrects_record_sha256']` |
| `CORRECTION_TARGET_MISSING` | `ToolError` | `runtime/mvp_runtime/crypto/live_correction.py` | 248 | `verify_correction` | `target is None` |
| `CORRECTION_UNAPPROVED` | `ToolError` | `runtime/mvp_runtime/crypto/live_correction.py` | 298 | `_verify_approval` | `not isinstance(approval_id, str) or not approval_id` |
| `CORRECTION_UNAPPROVED` | `ToolError` | `runtime/mvp_runtime/crypto/live_correction.py` | 301 | `_verify_approval` | `approvals is None` |
| `CORRECTION_UNAPPROVED` | `ToolError` | `runtime/mvp_runtime/crypto/live_correction.py` | 306 | `_verify_approval` | `approval is None` |
| `CORRECTION_UNAPPROVED` | `ToolError` | `runtime/mvp_runtime/crypto/live_correction.py` | 308 | `_verify_approval` | `approval.get('status') != 'APPROVED'` |
| `CORRECTION_UNAPPROVED` | `ToolError` | `runtime/mvp_runtime/crypto/live_correction.py` | 312 | `_verify_approval` | `snapshot.get('action_type') != CORRECTION_ACTION_TYPE` |
| `CORRECTION_UNAPPROVED` | `ToolError` | `runtime/mvp_runtime/crypto/live_correction.py` | 316 | `_verify_approval` | `snapshot.get('content_sha256') != content_sha256(correction)` |
| `COST_MODEL_UNMEASURED` | `ToolError` | `runtime/mvp_runtime/crypto/cost.py` | 701 | `cost_model_for` | `missing` |
| `COST_MODEL_VENUE_UNKNOWN` | `ToolError` | `runtime/mvp_runtime/crypto/cost.py` | 695 | `cost_model_for` | `declaration is None` |
| `COUNTERFACTUAL_BOOK_UNVERIFIABLE` | `ToolError` | `runtime/mvp_runtime/crypto/counterfactual.py` | 119 | `load_open_counterfactuals` | `—` |
| `COUNTERFACTUAL_BOOK_UNVERIFIABLE` | `ToolError` | `runtime/mvp_runtime/crypto/counterfactual.py` | 125 | `load_open_counterfactuals` | `rows is None and (not isinstance(book, dict))` |
| `COUNTERFACTUAL_HISTORY_DUPLICATE` | `ToolError` | `runtime/mvp_runtime/crypto/counterfactual.py` | 433 | `read_counterfactual_outcomes` | `settlement_id in seen_settlements` |
| `COUNTERFACTUAL_HISTORY_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/counterfactual.py` | 426 | `read_counterfactual_outcomes` | `not isinstance(stored, str) or integrity.sha256_record(body) != stored` |
| `COUNTERFACTUAL_HISTORY_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/counterfactual.py` | 414 | `read_counterfactual_outcomes` | `—` |
| `CRYPTO_RISK_LIMITS_EXPIRED` | `ToolError` | `runtime/mvp_runtime/crypto/risk_limits.py` | 361 | `resolve_risk_limits` | `not _window_covers(record, now)` |
| `CRYPTO_RISK_LIMITS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/risk_limits.py` | 119 | `seal_drawdown_exclusion` | `entry is None` |
| `CRYPTO_RISK_LIMITS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/risk_limits.py` | 151 | `build_risk_limits_record` | `missing` |
| `CRYPTO_RISK_LIMITS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/risk_limits.py` | 158 | `build_risk_limits_record` | `—` |
| `CRYPTO_RISK_LIMITS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/risk_limits.py` | 162 | `build_risk_limits_record` | `numeric[key] != int(numeric[key])` |
| `CRYPTO_RISK_LIMITS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/risk_limits.py` | 173 | `build_risk_limits_record` | `problems` |
| `CRYPTO_RISK_LIMITS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/risk_limits.py` | 179 | `build_risk_limits_record` | `not (isinstance(registered_by, str) and registered_by.strip())` |
| `CRYPTO_RISK_LIMITS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/risk_limits.py` | 211 | `build_risk_limits_record` | `not isinstance(ids, (list, tuple)) or not ids or (not all((isinstance(i, str) and i.strip() for…` |
| `CRYPTO_RISK_LIMITS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/risk_limits.py` | 216 | `build_risk_limits_record` | `not (isinstance(reason, str) and reason.strip())` |
| `CRYPTO_RISK_LIMITS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/risk_limits.py` | 223 | `build_risk_limits_record` | `len(deduped) != len(ids)` |
| `CRYPTO_RISK_LIMITS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/risk_limits.py` | 233 | `build_risk_limits_record` | `bare` |
| `CRYPTO_RISK_LIMITS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/risk_limits.py` | 252 | `_validate` | `—` |
| `CRYPTO_RISK_LIMITS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/risk_limits.py` | 291 | `read_registered_limits` | `window != (None, None) and (not (isinstance(window[0], str) and isinstance(window[1], str) and …` |
| `CRYPTO_RISK_LIMITS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/risk_limits.py` | 324 | `limits_from_record` | `problems` |
| `CRYPTO_RISK_LIMITS_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/risk_limits.py` | 279 | `read_registered_limits` | `—` |
| `CRYPTO_RISK_LIMITS_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/risk_limits.py` | 281 | `read_registered_limits` | `not isinstance(stored, str) or recomputed != stored` |
| `CRYPTO_RISK_LIMITS_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/risk_limits.py` | 439 | `write_registered_limits` | `not isinstance(stored, str) or integrity.sha256_record(body) != stored` |
| `CRYPTO_RISK_LIMITS_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/risk_limits.py` | 268 | `read_registered_limits` | `—` |
| `CRYPTO_RISK_LIMITS_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/risk_limits.py` | 270 | `read_registered_limits` | `not isinstance(data, dict)` |
| `CRYPTO_VENUE_UNKNOWN` | `ToolError` | `runtime/mvp_runtime/crypto/state.py` | 65 | `venue_state_dir` | `venue not in VENUES` |
| `DECISION_EXPIRED` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 192 | `build_approval_request` | `expires <= issued` |
| `DEFINITION_INPUT_INVALID` | `ProgramizationBlocked` | `runtime/mvp_runtime/registration.py` | 91 | `build_program_definition` | `not isinstance(definition_input, Mapping)` |
| `DEFINITION_INPUT_INVALID` | `ProgramizationBlocked` | `runtime/mvp_runtime/registration.py` | 94 | `build_program_definition` | `not (isinstance(purpose, str) and purpose.strip())` |
| `DEFINITION_INPUT_INVALID` | `ProgramizationBlocked` | `runtime/mvp_runtime/registration.py` | 100 | `build_program_definition` | `not items` |
| `DEFINITION_PATH_EXISTS` | `ProgramizationBlocked` | `runtime/mvp_runtime/registration.py` | 264 | `apply_registration` | `definition_path.exists()` |
| `DELIVERY_POINTER_PERSIST_FAILED` | `OperatorBlocked` | `runtime/mvp_runtime/operator_feedback.py` | 103 | `record_delivery` | `—` |
| `DELIVERY_STATUS_INVALID` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 506 | `record_delivery` | `status not in wf.DELIVERY_STATUSES` |
| `DOMAIN_EFFECT_MISMATCH` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 572 | `_emergency_close_ask` | `domain != _EMERGENCY_CLOSE_DOMAIN` |
| `DOMAIN_EFFECT_MISMATCH` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 711 | `_spend` | `len(_ALLOWED_DOMAINS) > 1` |
| `DOMAIN_NOT_PERMITTED` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 351 | `_require_domain` | `domain not in _ALLOWED_DOMAINS` |
| `DOMAIN_NOT_PERMITTED` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 686 | `_spend` | `domain not in _ALLOWED_DOMAINS` |
| `DOOR_REPLY_MALFORMED` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 780 | `call_door` | `size > max_reply_bytes` |
| `DOOR_REPLY_MALFORMED` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 802 | `call_door` | `—` |
| `DOOR_UNREACHABLE` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 788 | `call_door` | `—` |
| `DOOR_UNREACHABLE` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 796 | `call_door` | `not raw` |
| `DUPLICATE_CORE_RULES` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 192 | `build_task` | `len(set(rule_ids)) != len(rule_ids)` |
| `DUPLICATE_PROVIDER` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 328 | `select_env_gated_chain` | `len(set(names)) != len(names)` |
| `DUPLICATE_SELECTOR` | `ToolError` | `runtime/mvp_runtime/crypto/pool.py` | 209 | `resolve_candidates` | `record['candidate_id'] in seen` |
| `EMERGENCY_CLOSE_ACCOUNT_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_route.py` | 1754 | `run_emergency_close` | `snapshot is None` |
| `EMERGENCY_CLOSE_ASK_OPEN` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 492 | `_open_emergency_close_ask` | `standing is not None` |
| `EMERGENCY_CLOSE_BOOK_INCOMPLETE` | `ToolError` | `runtime/mvp_runtime/crypto/live_route.py` | 1668 | `emergency_close_content` | `incomplete is not None` |
| `EMERGENCY_CLOSE_GATE_CLOSED` | `ToolError` | `runtime/mvp_runtime/crypto/live_route.py` | 1724 | `run_emergency_close` | `adapter is None` |
| `EMERGENCY_CLOSE_HALT_CHANGED` | `ToolError` | `runtime/mvp_runtime/crypto/live_route.py` | 1721 | `run_emergency_close` | `problem is not None` |
| `EMERGENCY_CLOSE_NEEDS_HARD_HALT` | `ToolError` | `runtime/mvp_runtime/crypto/live_route.py` | 1661 | `emergency_close_content` | `problem is not None` |
| `EMERGENCY_CLOSE_NOTHING_BOOKED` | `ToolError` | `runtime/mvp_runtime/crypto/live_route.py` | 1664 | `emergency_close_content` | `not rows` |
| `EMERGENCY_CLOSE_NOTHING_BOOKED` | `ToolError` | `runtime/mvp_runtime/crypto/live_route.py` | 1734 | `run_emergency_close` | `not any((str(p.get('position_id')) in wanted for p in list_open_live_positions(root)))` |
| `EMERGENCY_CLOSE_NOTHING_CLOSABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_route.py` | 1762 | `run_emergency_close` | `all((skip is not None for skip in skips))` |
| `EMERGENCY_CLOSE_NO_CONFIRMATION` | `ToolError` | `runtime/mvp_runtime/crypto/live_route.py` | 1729 | `run_emergency_close` | `not limits.confirmation_present()` |
| `EMPTY_CONTENT` | `ToolBlocked` | `runtime/mvp_runtime/workspace.py` | 213 | `_require_content` | `not content` |
| `EMPTY_FEEDBACK` | `OperatorBlocked` | `runtime/mvp_runtime/operator_feedback.py` | 232 | `apply_feedback` | `not payload` |
| `EMPTY_PATH` | `ToolBlocked` | `runtime/mvp_runtime/workspace.py` | 162 | `resolve_target` | `not isinstance(relative_path, str) or not relative_path.strip()` |
| `EMPTY_QUERY` | `ToolBlocked` | `runtime/mvp_runtime/tools.py` | 96 | `_require_query` | `not isinstance(query, str) or not query.strip()` |
| `EMPTY_REQUEST` | `TaskRegistryBlocked` | `runtime/mvp_runtime/task_registry.py` | 270 | `build_entry` | `not text` |
| `EMPTY_SEED` | `ToolBlocked` | `runtime/mvp_runtime/naver_research.py` | 250 | `_require_seed` | `not isinstance(seed, str) or not seed.strip()` |
| `EMPTY_SYMBOL` | `ToolBlocked` | `runtime/mvp_runtime/crypto/market_data.py` | 777 | `_require_symbol` | `not isinstance(symbol, str) or not symbol.strip()` |
| `ENTRY_NOT_FOUND` | `OperatorBlocked` | `runtime/mvp_runtime/registry_console.py` | 392 | `_require_entry` | `entry is None` |
| `ENTRY_NOT_FOUND` | `TaskRegistryBlocked` | `runtime/mvp_runtime/task_registry.py` | 512 | `_current_locked` | `latest is None` |
| `ENV_OPT_IN_WITHDRAWN` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 151 | `assert_authorization` | `os.environ.get(env_var, '').strip().lower() != expected.strip().lower()` |
| `EVENT_FINGERPRINT_FAILED` | `AuditError` | `runtime/mvp_runtime/audit.py` | 115 | `_make_event` | `—` |
| `EVENT_FINGERPRINT_FAILED` | `AuditError` | `runtime/mvp_runtime/audit.py` | 918 | `rechain_events` | `—` |
| `EVENT_INVALID` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow.py` | 483 | `event_record` | `—` |
| `EVENT_STRUCTURE_INVALID` | `AuditError` | `runtime/mvp_runtime/audit.py` | 912 | `rechain_events` | `not (isinstance(integrity_block, MutableMapping) and isinstance(payload, MutableMapping) and is…` |
| `EXECUTION_STAGE_ALREADY_BINDS` | `ToolError` | `runtime/mvp_runtime/crypto/execution_stage.py` | 476 | `plan_transition` | `target == recorded` |
| `EXECUTION_STAGE_ATTESTATION_REQUIRED` | `ToolError` | `runtime/mvp_runtime/crypto/execution_stage.py` | 489 | `plan_transition` | `not (isinstance(attestation, str) and attestation.strip())` |
| `EXECUTION_STAGE_BOOTSTRAP_ONLY_SHADOW_OR_PAPER` | `ToolError` | `runtime/mvp_runtime/crypto/execution_stage.py` | 504 | `plan_transition` | `rebindable` |
| `EXECUTION_STAGE_CHANGED` | `ToolError` | `runtime/mvp_runtime/crypto/execution_stage.py` | 596 | `record_from_approved` | `content.get('stage_ref') != stage_ref(status_now)` |
| `EXECUTION_STAGE_CHANGED` | `ToolError` | `runtime/mvp_runtime/crypto/execution_stage.py` | 610 | `record_from_approved` | `replanned != dict(content)` |
| `EXECUTION_STAGE_DEMOTE_FROM_UNBOUND_RECORD` | `ToolError` | `runtime/mvp_runtime/crypto/execution_stage.py` | 684 | `demote_record` | `not status.binding` |
| `EXECUTION_STAGE_EVIDENCE_NOT_APPLICABLE` | `ToolError` | `runtime/mvp_runtime/crypto/execution_stage.py` | 514 | `plan_transition` | `testnet_cycle_id is not None` |
| `EXECUTION_STAGE_NOTHING_TO_DEMOTE` | `ToolError` | `runtime/mvp_runtime/crypto/execution_stage.py` | 667 | `demote_record` | `not status.record_present` |
| `EXECUTION_STAGE_NOT_DEFINED` | `ToolError` | `runtime/mvp_runtime/crypto/execution_stage.py` | 467 | `plan_transition` | `target not in LADDER` |
| `EXECUTION_STAGE_NOT_DEFINED` | `ToolError` | `runtime/mvp_runtime/crypto/execution_stage.py` | 482 | `plan_transition` | `target == ExecutionStage.LIVE_SCALED.value` |
| `EXECUTION_STAGE_NOT_DEFINED` | `ToolError` | `runtime/mvp_runtime/crypto/execution_stage.py` | 650 | `demote_record` | `target not in LADDER` |
| `EXECUTION_STAGE_NOT_LOWER` | `ToolError` | `runtime/mvp_runtime/crypto/execution_stage.py` | 669 | `demote_record` | `status.valid and status.recorded_stage == read_only` |
| `EXECUTION_STAGE_NOT_LOWER` | `ToolError` | `runtime/mvp_runtime/crypto/execution_stage.py` | 688 | `demote_record` | `rank(target) >= rank(status.recorded_stage)` |
| `EXECUTION_STAGE_POLICY_CHANGED_SINCE_ASK` | `ToolError` | `runtime/mvp_runtime/crypto/execution_stage.py` | 600 | `record_from_approved` | `identity is None or identity['policy_version'] != content.get('policy_version') or identity['po…` |
| `EXECUTION_STAGE_POLICY_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/execution_stage.py` | 522 | `plan_transition` | `identity is None` |
| `EXECUTION_STAGE_REBIND_FIRST` | `ToolError` | `runtime/mvp_runtime/crypto/execution_stage.py` | 499 | `plan_transition` | `rebindable` |
| `EXECUTION_STAGE_RECORD_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/execution_stage.py` | 309 | `read_registered_stage` | `—` |
| `EXECUTION_STAGE_RECORD_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/execution_stage.py` | 469 | `plan_transition` | `not (isinstance(registered_by, str) and registered_by.strip() and isinstance(reason, str) and r…` |
| `EXECUTION_STAGE_RECORD_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/execution_stage.py` | 569 | `_finish` | `—` |
| `EXECUTION_STAGE_RECORD_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/execution_stage.py` | 652 | `demote_record` | `not (isinstance(registered_by, str) and registered_by.strip() and isinstance(reason, str) and r…` |
| `EXECUTION_STAGE_RECORD_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/execution_stage.py` | 305 | `read_registered_stage` | `not isinstance(stored, str) or recomputed != stored` |
| `EXECUTION_STAGE_RECORD_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/execution_stage.py` | 294 | `read_registered_stage` | `—` |
| `EXECUTION_STAGE_RECORD_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/execution_stage.py` | 296 | `read_registered_stage` | `not isinstance(data, dict)` |
| `EXECUTION_STAGE_RECORD_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/execution_stage.py` | 303 | `read_registered_stage` | `—` |
| `EXECUTION_STAGE_SIGNED_TESTNET_EVIDENCE_REQUIRED` | `ToolError` | `runtime/mvp_runtime/crypto/execution_stage.py` | 550 | `_live_entry_evidence` | `not (isinstance(cycle_id, str) and cycle_id.strip())` |
| `EXECUTION_STAGE_SKIP_REFUSED` | `ToolError` | `runtime/mvp_runtime/crypto/execution_stage.py` | 480 | `plan_transition` | `rank(target) != rank(recorded) + 1` |
| `EXECUTION_STAGE_TOO_LOW_TO_ARM` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/promotion.py` | 258 | `_gate_execution_stage` | `—` |
| `EXECUTION_STAGE_USE_DEMOTE` | `ToolError` | `runtime/mvp_runtime/crypto/execution_stage.py` | 478 | `plan_transition` | `rank(target) < rank(recorded)` |
| `FACTORY_RUN_ID_MISSING` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 1707 | `_execute` | `run_id is None` |
| `FEEDBACK_TARGET_UNREADABLE` | `OperatorBlocked` | `runtime/mvp_runtime/operator_feedback.py` | 120 | `load_last_delivered` | `—` |
| `FEEDBACK_TARGET_UNREADABLE` | `OperatorBlocked` | `runtime/mvp_runtime/operator_feedback.py` | 125 | `load_last_delivered` | `not (isinstance(trace_id, str) and trace_id and isinstance(delivered_at, str) and delivered_at)` |
| `FEEDBACK_UNAVAILABLE` | `OperatorBlocked` | `runtime/mvp_runtime/operator_feedback.py` | 228 | `apply_feedback` | `store is None` |
| `FEED_ABSENT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1972 | `liquidation_history` | `—` |
| `FEED_ABSENT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1976 | `open_interest_history` | `—` |
| `FINANCIAL_SCHEDULE_REFUSED` | `ControlBlocked` | `runtime/mvp_runtime/schedule_delegation.py` | 267 | `apply_change` | `kind in FINANCIAL_KINDS` |
| `FINGERPRINT_FAILED` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 665 | `build_permission_decision` | `—` |
| `FINGERPRINT_MISMATCH` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 301 | `validate_spendable_approval` | `recomputed_fp != approval_rec.get('action_fingerprint')` |
| `FINGERPRINT_MISMATCH` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 661 | `_spend` | `recomputed != record.get('action_fingerprint')` |
| `FINGERPRINT_UNCOMPUTABLE` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 299 | `validate_spendable_approval` | `—` |
| `FINGERPRINT_UNCOMPUTABLE` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 659 | `_spend` | `—` |
| `FLAG_NOT_ENABLED` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 131 | `assert_authorization` | `missing` |
| `FORWARDED_MESSAGE` | `OperatorBlocked` | `runtime/mvp_runtime/operator.py` | 243 | `verify_control_channel` | `message.is_forwarded` |
| `FORWARD_BOOK_UNVERIFIABLE` | `ToolError` | `runtime/mvp_runtime/crypto/forward_book.py` | 167 | `_parse_book` | `not isinstance(raw, Mapping) or not isinstance(raw.get('entries'), Mapping)` |
| `FORWARD_BOOK_UNVERIFIABLE` | `ToolError` | `runtime/mvp_runtime/crypto/forward_book.py` | 169 | `_parse_book` | `raw.get('forward_book_version') != FORWARD_BOOK_VERSION` |
| `FORWARD_BOOK_UNVERIFIABLE` | `ToolError` | `runtime/mvp_runtime/crypto/forward_book.py` | 179 | `invalid` | `—` |
| `FORWARD_BOOK_UNVERIFIABLE` | `ToolError` | `runtime/mvp_runtime/crypto/forward_book.py` | 218 | `_assert_marks_run_forward` | `new_mark is None or timeutil.parse_iso(str(new_mark)) < timeutil.parse_iso(str(old_mark))` |
| `FORWARD_BOOK_UNVERIFIABLE` | `ToolError` | `runtime/mvp_runtime/crypto/forward_book.py` | 235 | `load_book` | `—` |
| `FORWARD_COHORT_EMPTY` | `ToolError` | `runtime/mvp_runtime/crypto/forward_cohort.py` | 293 | `freeze_cohort` | `not members` |
| `FORWARD_COHORT_NULLS_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/forward_cohort_null.py` | 198 | `read_null_records` | `not isinstance(record, dict) or record.get('forward_cohort_nulls_version') not in NULLS_VERSIONS` |
| `FORWARD_COHORT_NULLS_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/forward_cohort_null.py` | 203 | `read_null_records` | `not isinstance(stored, str) or integrity.sha256_record(body) != stored` |
| `FORWARD_COHORT_NULLS_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/forward_cohort_null.py` | 193 | `read_null_records` | `—` |
| `FORWARD_COHORT_POSITIONS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/forward_cohort.py` | 406 | `load_book_at` | `raw.get('forward_cohort_positions_version') != POSITIONS_VERSION` |
| `FORWARD_COHORT_POSITIONS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/forward_cohort.py` | 416 | `load_book_at` | `strangers` |
| `FORWARD_COHORT_POSITIONS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/forward_cohort.py` | 442 | `invalid` | `—` |
| `FORWARD_COHORT_POSITIONS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/forward_cohort.py` | 487 | `_assert_verdicts_kept` | `(after.get(walk_id) or {}).get(field) != stamp` |
| `FORWARD_COHORT_POSITIONS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/forward_cohort.py` | 495 | `invalid` | `—` |
| `FORWARD_COHORT_POSITIONS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/forward_cohort.py` | 533 | `_assert_marks_run_forward` | `new_mark is None or timeutil.parse_iso(str(new_mark)) < timeutil.parse_iso(str(old_mark))` |
| `FORWARD_COHORT_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/forward_cohort.py` | 168 | `read_cohorts` | `not isinstance(record, dict) or record.get('forward_cohort_version') != FORWARD_COHORT_VERSION` |
| `FORWARD_COHORT_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/forward_cohort.py` | 173 | `read_cohorts` | `not isinstance(stored, str) or integrity.sha256_record(body) != stored` |
| `FORWARD_COHORT_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/forward_cohort.py` | 163 | `read_cohorts` | `—` |
| `FORWARD_COHORT_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/forward_cohort.py` | 402 | `load_book_at` | `—` |
| `FORWARD_COHORT_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/forward_cohort.py` | 404 | `load_book_at` | `not isinstance(raw, Mapping) or not isinstance(raw.get('entries'), Mapping)` |
| `FORWARD_COHORT_WALK_FAILED` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 1410 | `_execute` | `summary['contexts'] and (not summary['walked'])` |
| `FORWARD_HISTORY_DUPLICATE` | `ToolError` | `runtime/mvp_runtime/crypto/forward_book.py` | 314 | `read_sealed_rows` | `settlement_id in seen_settlements` |
| `FORWARD_HISTORY_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/forward_book.py` | 301 | `read_sealed_rows` | `record.get('provenance') != provenance` |
| `FORWARD_HISTORY_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/forward_book.py` | 307 | `read_sealed_rows` | `not isinstance(stored, str) or integrity.sha256_record(body) != stored` |
| `FORWARD_HISTORY_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/forward_book.py` | 311 | `read_sealed_rows` | `not (isinstance(settlement_id, str) and settlement_id)` |
| `FORWARD_HISTORY_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/forward_book.py` | 295 | `read_sealed_rows` | `—` |
| `FRONTDESK_ROLE_HASH_MISMATCH` | `OperatorBlocked` | `runtime/mvp_runtime/frontdesk.py` | 188 | `_require_active_role` | `actual != expected` |
| `FRONTDESK_ROLE_INACTIVE` | `OperatorBlocked` | `runtime/mvp_runtime/frontdesk.py` | 165 | `_require_active_role` | `entry.get('status') != 'active'` |
| `FRONTDESK_ROLE_MISCONFIGURED` | `OperatorBlocked` | `runtime/mvp_runtime/frontdesk.py` | 174 | `_require_active_role` | `entry.get('routable') is not False` |
| `FRONTDESK_ROLE_UNRESOLVED` | `OperatorBlocked` | `runtime/mvp_runtime/frontdesk.py` | 155 | `_require_active_role` | `—` |
| `FRONTDESK_ROLE_UNRESOLVED` | `OperatorBlocked` | `runtime/mvp_runtime/frontdesk.py` | 159 | `_require_active_role` | `len(entries) != 1` |
| `FRONTDESK_ROLE_UNRESOLVED` | `OperatorBlocked` | `runtime/mvp_runtime/frontdesk.py` | 184 | `_require_active_role` | `—` |
| `GUARD_NOT_APPROVED` | `SubmitRefused` | `runtime/mvp_runtime/crypto/live_execution.py` | 857 | `submit_and_reconcile` | `not (isinstance(guard_verdict, Mapping) and guard_verdict.get('approved') is True)` |
| `HOST_NOT_ALLOWED` | `ToolBlocked` | `runtime/mvp_runtime/crypto/account.py` | 252 | `__init__` | `host not in ALLOWED_ACCOUNT_HOSTS` |
| `HYPOTHESIS_TRIAL_ALREADY_CLOSED` | `ToolError` | `runtime/mvp_runtime/crypto/forward_trial.py` | 193 | `close_trial` | `line.get('close') is not None` |
| `HYPOTHESIS_TRIAL_ALREADY_CLOSED` | `ToolError` | `runtime/mvp_runtime/crypto/forward_trial.py` | 216 | `close_trial` | `candidate in closed_trial_ids(root)` |
| `HYPOTHESIS_TRIAL_CLOSES_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/forward_trial.py` | 157 | `read_trial_closes` | `not isinstance(record, dict) or record.get('hypothesis_trial_close_version') != TRIAL_CLOSE_VER…` |
| `HYPOTHESIS_TRIAL_CLOSES_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/forward_trial.py` | 162 | `read_trial_closes` | `not isinstance(stored, str) or integrity.sha256_record(body) != stored` |
| `HYPOTHESIS_TRIAL_CLOSES_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/forward_trial.py` | 152 | `read_trial_closes` | `—` |
| `HYPOTHESIS_TRIAL_CLOSE_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/forward_trial.py` | 185 | `close_trial` | `decision not in CLOSE_DECISIONS` |
| `HYPOTHESIS_TRIAL_CLOSE_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/forward_trial.py` | 187 | `close_trial` | `not reason.strip()` |
| `HYPOTHESIS_TRIAL_NOT_GRADUABLE` | `ToolError` | `runtime/mvp_runtime/crypto/forward_trial.py` | 196 | `close_trial` | `decision == CLOSE_GRADUATE and line.get('holdout_status') != GRADUATION_HOLDOUT_STATUS` |
| `HYPOTHESIS_TRIAL_UNKNOWN` | `ToolError` | `runtime/mvp_runtime/crypto/forward_trial.py` | 191 | `close_trial` | `line is None` |
| `IDEATION_INPUTS_REQUIRED` | `ToolError` | `runtime/mvp_runtime/blog_content.py` | 443 | `run_content_ideation` | `not seeds and (not target_override)` |
| `IDEATION_INPUTS_REQUIRED` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 573 | `_apply_job` | `not isinstance(inputs, dict) or not str(inputs.get('seeds') or '').strip()` |
| `IDEMPOTENCY_UNAVAILABLE` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 316 | `apply_dispatch` | `request_id is not None and ledger is None` |
| `INVALID_ASSIGNMENT_MODE` | `PlannerBlocked` | `runtime/mvp_runtime/assignment.py` | 110 | `build_role_assignment` | `assignment_mode not in ('normal', 'candidate_trial')` |
| `INVALID_AUTHENTICATED` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 179 | `build_task` | `not isinstance(authenticated, bool)` |
| `INVALID_AUTHORITY` | `PlannerBlocked` | `runtime/mvp_runtime/assignment.py` | 88 | `build_role_assignment` | `—` |
| `INVALID_CANDIDATE` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 593 | `promote_candidate` | `not (isinstance(candidate_id, str) and candidate_id)` |
| `INVALID_CANDIDATE` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 595 | `promote_candidate` | `not (isinstance(content, str) and content.strip())` |
| `INVALID_CANDIDATE` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 930 | `build_memory_promotion_permission_decision` | `not (isinstance(candidate_id, str) and candidate_id)` |
| `INVALID_CANDIDATE` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 932 | `build_memory_promotion_permission_decision` | `not (isinstance(content, str) and content.strip())` |
| `INVALID_CANDIDATE_TRANSITION` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 573 | `transition_candidate` | `allowed is None` |
| `INVALID_CANDIDATE_TRANSITION` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 579 | `transition_candidate` | `to_status is None` |
| `INVALID_CHAIN` | `ProviderError` | `runtime/mvp_runtime/providers.py` | 922 | `__init__` | `len(providers) < 2` |
| `INVALID_CHANNEL` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 171 | `build_task` | `channel not in _ALLOWED_CHANNELS` |
| `INVALID_CONTENT` | `ToolBlocked` | `runtime/mvp_runtime/workspace.py` | 211 | `_require_content` | `not isinstance(content, str)` |
| `INVALID_CONTENT_HASH` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1386 | `build_workflow_step_permission_decision` | `not (isinstance(request_sha256, str) and request_sha256.startswith('sha256:'))` |
| `INVALID_CORE_CANDIDATE` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 489 | `decide_core_candidate` | `not (isinstance(candidate_id, str) and candidate_id)` |
| `INVALID_CORRECTION` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1835 | `build_live_outcome_correction_permission_decision` | `not (isinstance(corrects_outcome_id, str) and corrects_outcome_id)` |
| `INVALID_CORRECTION` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1837 | `build_live_outcome_correction_permission_decision` | `not (isinstance(corrects_record_sha256, str) and corrects_record_sha256)` |
| `INVALID_CORRECTION` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1839 | `build_live_outcome_correction_permission_decision` | `disposition not in ('VOID', 'SUPERSEDE')` |
| `INVALID_CORRECTION` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1841 | `build_live_outcome_correction_permission_decision` | `not (isinstance(reason, str) and reason.strip())` |
| `INVALID_DERIVATIVE_KIND` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 686 | `derivative_price_klines` | `kind not in DERIVATIVE_KLINE_PATHS` |
| `INVALID_DERIVATIVE_KIND` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1304 | `derivative_price_klines` | `kind not in DERIVATIVE_KLINE_PATHS` |
| `INVALID_DOMAIN` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1001 | `build_trading_switch_permission_decision` | `not (isinstance(domain, str) and domain.strip())` |
| `INVALID_DOMAIN` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1099 | `build_nonfinancial_resume_permission_decision` | `not (isinstance(domain, str) and domain.strip())` |
| `INVALID_EMERGENCY_CLOSE` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1283 | `build_emergency_close_permission_decision` | `not (isinstance(halt_ref, str) and halt_ref)` |
| `INVALID_EMERGENCY_CLOSE` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1287 | `build_emergency_close_permission_decision` | `not (isinstance(positions, list) and positions and all((isinstance(p, Mapping) and all((isinsta…` |
| `INVALID_EMERGENCY_CLOSE` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1291 | `build_emergency_close_permission_decision` | `not (isinstance(content.get(key), str) and content[key].strip())` |
| `INVALID_ENCODING` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 87 | `_require_text` | `—` |
| `INVALID_ENCODING` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 125 | `_clean_str_list` | `—` |
| `INVALID_EXPIRY` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 522 | `build_schedule` | `expires_at is not None and (not (isinstance(expires_at, str) and _TIMESTAMP_PATTERN.match(expir…` |
| `INVALID_INITIAL_STATUS` | `TaskRegistryBlocked` | `runtime/mvp_runtime/task_registry.py` | 276 | `build_entry` | `status not in (QUEUED, RUNNING)` |
| `INVALID_INTERVAL` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 515 | `build_schedule` | `isinstance(interval_seconds, bool) or not (isinstance(interval_seconds, int) and interval_secon…` |
| `INVALID_LEVEL` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 562 | `build_permission_decision` | `rank_of(required_permission_level) is None or rank_of(role_permission_ceiling) is None` |
| `INVALID_LIST` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 108 | `_clean_str_list` | `value is not None and isinstance(value, (str, bytes))` |
| `INVALID_LIST` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 116 | `_clean_str_list` | `—` |
| `INVALID_LIST_ITEM` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 121 | `_clean_str_list` | `not isinstance(item, str) or not item.strip()` |
| `INVALID_ORDER_BOOK_LIMIT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 740 | `order_book` | `int(limit) not in ORDER_BOOK_VALID_LIMITS` |
| `INVALID_ORDER_BOOK_LIMIT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1508 | `order_book` | `int(limit) not in ORDER_BOOK_VALID_LIMITS` |
| `INVALID_ORIGIN` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 100 | `_normalize_origin` | `missing` |
| `INVALID_PATH` | `ToolBlocked` | `runtime/mvp_runtime/workspace.py` | 166 | `resolve_target` | `any((ord(ch) < 32 for ch in relative_path))` |
| `INVALID_PATH` | `ToolBlocked` | `runtime/mvp_runtime/workspace.py` | 181 | `resolve_target` | `':' in relative_path` |
| `INVALID_PATH` | `ToolBlocked` | `runtime/mvp_runtime/workspace.py` | 186 | `resolve_target` | `any((part != part.rstrip('. ') for part in candidate.parts))` |
| `INVALID_PATH` | `ToolBlocked` | `runtime/mvp_runtime/workspace.py` | 194 | `resolve_target` | `any((part.split('.', 1)[0].lower() in _RESERVED_BASENAMES for part in candidate.parts))` |
| `INVALID_PATH` | `ToolBlocked` | `runtime/mvp_runtime/workspace.py` | 205 | `resolve_target` | `target == base_real` |
| `INVALID_PLAN` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 557 | `workflow_plan_of` | `—` |
| `INVALID_PLAN` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 559 | `workflow_plan_of` | `not isinstance(plan, dict)` |
| `INVALID_PLAN` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 563 | `workflow_plan_of` | `—` |
| `INVALID_POSITIONING_PERIOD` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 713 | `positioning_history` | `period not in POSITIONING_PERIOD_SECONDS` |
| `INVALID_POSITIONING_PERIOD` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1410 | `positioning_history` | `period not in POSITIONING_PERIOD_SECONDS` |
| `INVALID_POSITIONING_SERIES` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 711 | `positioning_history` | `series not in POSITIONING_PATHS` |
| `INVALID_POSITIONING_SERIES` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1408 | `positioning_history` | `series not in POSITIONING_PATHS` |
| `INVALID_PRIORITY` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 177 | `build_task` | `priority not in _ALLOWED_PRIORITIES` |
| `INVALID_PROBE_BATCH` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1460 | `build_slippage_probe_permission_decision` | `not (isinstance(batch_id, str) and batch_id)` |
| `INVALID_PROBE_BATCH` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1462 | `build_slippage_probe_permission_decision` | `not (isinstance(content_sha256, str) and content_sha256.startswith('sha256:'))` |
| `INVALID_PROBE_BATCH` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1465 | `build_slippage_probe_permission_decision` | `not symbols` |
| `INVALID_PROMOTION` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1726 | `build_strategy_promotion_permission_decision` | `not candidate_ids or not all((isinstance(c, str) and c for c in candidate_ids))` |
| `INVALID_PROMOTION` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1728 | `build_strategy_promotion_permission_decision` | `len(strategy_ids) != len(candidate_ids) or not all((isinstance(s, str) and s for s in strategy_…` |
| `INVALID_PROMOTION` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1730 | `build_strategy_promotion_permission_decision` | `len(rule_hashes) != len(candidate_ids) or not all((isinstance(h, str) and h for h in rule_hashe…` |
| `INVALID_PROMOTION` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1733 | `build_strategy_promotion_permission_decision` | `isinstance(artifact_sha256s, (str, bytes)) or len(artifact_sha256s) != len(candidate_ids) or (n…` |
| `INVALID_PROMOTION` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1735 | `build_strategy_promotion_permission_decision` | `live_tier not in (STRATEGY_POOL_TIER_OBSERVATION, STRATEGY_POOL_TIER_LIVE)` |
| `INVALID_REGISTRATION` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1642 | `build_program_registration_permission_decision` | `not (isinstance(definition_sha256, str) and definition_sha256.startswith('sha256:'))` |
| `INVALID_REQUESTER_TYPE` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 173 | `build_task` | `requester_type not in _ALLOWED_REQUESTER_TYPES` |
| `INVALID_REQUIRED_LEVEL` | `PlannerBlocked` | `runtime/mvp_runtime/planner.py` | 310 | `select_role` | `required_rank is None` |
| `INVALID_RETIREMENT` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1911 | `build_strategy_retirement_permission_decision` | `not strategy_ids or not all((isinstance(s, str) and s for s in strategy_ids))` |
| `INVALID_RETIREMENT` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1913 | `build_strategy_retirement_permission_decision` | `len(candidate_ids) != len(strategy_ids)` |
| `INVALID_RETIREMENT` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1915 | `build_strategy_retirement_permission_decision` | `len(rule_hashes) != len(strategy_ids) or not all((isinstance(h, str) and h for h in rule_hashes…` |
| `INVALID_RETIREMENT` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1917 | `build_strategy_retirement_permission_decision` | `not (isinstance(reason, str) and reason.strip())` |
| `INVALID_REVIEW_TRANSITION` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 414 | `transition_review` | `to_status not in _REVIEW_TRANSITIONS.get(from_status, set())` |
| `INVALID_ROLE` | `PlannerBlocked` | `runtime/mvp_runtime/assignment.py` | 69 | `build_role_assignment` | `not isinstance(role, Mapping) or not role.get('role_id') or (not role.get('version')) or (not r…` |
| `INVALID_ROLE` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1573 | `build_trial_permission_decision` | `not (isinstance(role_id, str) and role_id and isinstance(role_version, str) and role_version)` |
| `INVALID_ROLE` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1575 | `build_trial_permission_decision` | `not (isinstance(definition_sha256, str) and definition_sha256)` |
| `INVALID_SENSITIVITY` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 175 | `build_task` | `data_sensitivity not in _ALLOWED_SENSITIVITY` |
| `INVALID_STAGE_TRANSITION` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1188 | `build_execution_stage_permission_decision` | `missing` |
| `INVALID_STOP_REF` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1008 | `build_trading_switch_permission_decision` | `not (isinstance(stop_ref, str) and stop_ref.strip())` |
| `INVALID_STOP_REF` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1104 | `build_nonfinancial_resume_permission_decision` | `not (isinstance(stop_ref, str) and stop_ref.strip())` |
| `INVALID_STOP_SUMMARY` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1012 | `build_trading_switch_permission_decision` | `not (isinstance(stop_summary, str) and stop_summary.strip())` |
| `INVALID_STOP_SUMMARY` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1108 | `build_nonfinancial_resume_permission_decision` | `not (isinstance(stop_summary, str) and stop_summary.strip())` |
| `INVALID_SYMBOL` | `ToolBlocked` | `runtime/mvp_runtime/crypto/market_data.py` | 779 | `_require_symbol` | `not (pattern or _SYMBOL_PATTERN).fullmatch(symbol)` |
| `INVALID_TARGET` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1384 | `build_workflow_step_permission_decision` | `not (isinstance(workflow_id, str) and workflow_id.strip() and isinstance(step_key, str) and ste…` |
| `INVALID_TASK` | `PlannerBlocked` | `runtime/mvp_runtime/planner.py` | 192 | `classify_task` | `not isinstance(task, Mapping)` |
| `INVALID_TIMEFRAME` | `ToolBlocked` | `runtime/mvp_runtime/crypto/market_data.py` | 785 | `_require_timeframe` | `timeframe not in TIMEFRAMES` |
| `INVALID_TIMESTAMP` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 98 | `_validate_timestamp` | `not isinstance(value, str)` |
| `INVALID_TIMESTAMP` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 102 | `_validate_timestamp` | `—` |
| `INVALID_TIMESTAMP` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 526 | `_parse_ts` | `—` |
| `INVALID_TRIAL_REQUEST` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1577 | `build_trial_permission_decision` | `not (isinstance(trial_request, str) and trial_request.strip())` |
| `INVALID_TTL` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 180 | `build_approval_request` | `requested < 1` |
| `INVALID_VALIDATED_MEMORY` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 409 | `build_core_candidate` | `not (isinstance(validated_id, str) and validated_id)` |
| `INVALID_VALIDATED_MEMORY` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 411 | `build_core_candidate` | `not (isinstance(content, str) and content.strip())` |
| `INVENTORY_REQUIRED` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 606 | `_apply_job` | `not isinstance(inventory, dict) or not inventory` |
| `JOB_NOT_PERMITTED` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 544 | `_apply_job` | `not isinstance(job, str) or job.strip() not in _ALLOWED_JOBS` |
| `KILL_STATE_UNAVAILABLE` | `OperatorBlocked` | `runtime/mvp_runtime/memory_console.py` | 181 | `apply_memory_command` | `control_store is None` |
| `KILL_STATE_UNAVAILABLE` | `OperatorBlocked` | `runtime/mvp_runtime/registry_console.py` | 326 | `apply_registry_command` | `control_store is None` |
| `KIND_NOT_PERMITTED` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 270 | `apply_dispatch` | `kind not in _ALLOWED_KINDS` |
| `KIND_NOT_PERMITTED` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 265 | `apply_work` | `kind not in _ALLOWED_KINDS` |
| `KNOWLEDGE_CONTENT_AMBIGUOUS` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/service.py` | 87 | `add_document` | `(text is None) == (pdf_base64 is None)` |
| `KNOWLEDGE_DOCUMENT_DATE_INVALID` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/store.py` | 307 | `_optional_timestamp` | `not isinstance(value, str) or not value.strip()` |
| `KNOWLEDGE_DOCUMENT_DATE_INVALID` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/store.py` | 316 | `_optional_timestamp` | `—` |
| `KNOWLEDGE_LIMIT_INVALID` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/service.py` | 196 | `_resolve_limit` | `not isinstance(limit, int) or isinstance(limit, bool) or limit < 1` |
| `KNOWLEDGE_LIMIT_INVALID` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/service.py` | 198 | `_resolve_limit` | `limit > MAX_LIMIT` |
| `KNOWLEDGE_QUESTION_REQUIRED` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/service.py` | 147 | `query` | `not isinstance(question, str) or not question.strip()` |
| `KNOWLEDGE_QUESTION_TOO_LONG` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/service.py` | 150 | `query` | `len(question) > MAX_QUESTION_CHARS` |
| `KNOWLEDGE_RECORD_INVALID` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/store.py` | 266 | `validate_document` | `—` |
| `KNOWLEDGE_SENSITIVITY_INVALID` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/store.py` | 224 | `build_document` | `data_sensitivity not in SENSITIVITIES` |
| `KNOWLEDGE_SOURCE_TYPE_INVALID` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/store.py` | 219 | `build_document` | `source_type not in SOURCE_TYPES` |
| `KNOWLEDGE_TAGS_INVALID` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/store.py` | 282 | `_clean_tags` | `not isinstance(tags, (list, tuple))` |
| `KNOWLEDGE_TAGS_INVALID` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/store.py` | 286 | `_clean_tags` | `not isinstance(tag, str) or not tag.strip()` |
| `KNOWLEDGE_TAGS_INVALID` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/store.py` | 291 | `_clean_tags` | `len(cleaned) > MAX_TAGS` |
| `KNOWLEDGE_TEXT_REQUIRED` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/store.py` | 208 | `build_document` | `not isinstance(text, str) or not text.strip()` |
| `KNOWLEDGE_TEXT_TOO_LARGE` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/store.py` | 213 | `build_document` | `len(text) > MAX_TEXT_CHARS` |
| `KNOWLEDGE_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/knowledge/store.py` | 162 | `_read_unlocked` | `—` |
| `KNOWLEDGE_WRITE_FAILED` | `PersistenceError` | `runtime/mvp_runtime/knowledge/store.py` | 111 | `add` | `—` |
| `LEDGER_ARCHIVE_INDEX_FAILED` | `PersistenceError` | `runtime/mvp_runtime/retention.py` | 149 | `write_archive_index` | `—` |
| `LEDGER_EMPTY_KIND_FILTER` | `PersistenceError` | `runtime/mvp_runtime/store.py` | 175 | `_kind_prescreen` | `not tokens` |
| `LEDGER_INVALID_KEEP` | `PersistenceError` | `runtime/mvp_runtime/retention.py` | 189 | `rotate_file` | `not (isinstance(keep_rows, int) and keep_rows > 0)` |
| `LEDGER_PROTECTED_FROM_ROTATION` | `PersistenceError` | `runtime/mvp_runtime/retention.py` | 179 | `rotate_file` | `filename in PROTECTED_FILES` |
| `LEDGER_READ_FAILED` | `PersistenceError` | `runtime/mvp_runtime/bridge_idempotency.py` | 292 | `_live_record` | `—` |
| `LEDGER_ROTATION_FAILED` | `PersistenceError` | `runtime/mvp_runtime/retention.py` | 235 | `rotate_file` | `—` |
| `LEDGER_UNAVAILABLE` | `ApprovalBlocked` | `runtime/mvp_runtime/trial.py` | 434 | `run_trial` | `—` |
| `LEDGER_UNKNOWN_FILE` | `PersistenceError` | `runtime/mvp_runtime/retention.py` | 184 | `rotate_file` | `filename not in ROTATABLE_FILES` |
| `LEDGER_UNKNOWN_RECORD_KIND` | `PersistenceError` | `runtime/mvp_runtime/store.py` | 217 | `append_records` | `unknown` |
| `LEDGER_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/control.py` | 613 | `_mode_from_ledger` | `—` |
| `LEDGER_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/store.py` | 304 | `_tip` | `—` |
| `LEDGER_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/store.py` | 313 | `_tip` | `—` |
| `LEDGER_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/store.py` | 321 | `read_blocks` | `—` |
| `LEDGER_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/store.py` | 367 | `iter_records` | `—` |
| `LEDGER_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/store.py` | 409 | `iter_records_with_archive` | `—` |
| `LEDGER_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/store.py` | 412 | `iter_records_with_archive` | `—` |
| `LEDGER_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/store.py` | 458 | `read_scheduler_events` | `—` |
| `LEDGER_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/store.py` | 473 | `read_scheduler_events_tail` | `—` |
| `LEDGER_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/store.py` | 478 | `count_scheduler_events` | `—` |
| `LEDGER_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/store.py` | 489 | `read_audit_events` | `—` |
| `LEDGER_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/store.py` | 513 | `health` | `not entry['present']` |
| `LEDGER_WRITE_FAILED` | `PersistenceError` | `runtime/mvp_runtime/bridge_idempotency.py` | 265 | `_append` | `—` |
| `LEDGER_WRITE_FAILED` | `PersistenceError` | `runtime/mvp_runtime/store.py` | 209 | `append_audit_events` | `—` |
| `LEDGER_WRITE_FAILED` | `PersistenceError` | `runtime/mvp_runtime/store.py` | 263 | `_append_locked` | `—` |
| `LIFECYCLE_DECISION_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/lifecycle.py` | 362 | `operator_retirement_decision` | `not (isinstance(strategy_id, str) and strategy_id)` |
| `LIFECYCLE_DECISION_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/pool_transitions.py` | 126 | `apply_status_decisions` | `not (isinstance(strategy_id, str) and strategy_id and isinstance(new_status, str))` |
| `LIFECYCLE_DECISION_STALE` | `ToolError` | `runtime/mvp_runtime/crypto/pool_transitions.py` | 134 | `apply_status_decisions` | `all_or_nothing` |
| `LIFECYCLE_TERMINAL_IMMUTABLE` | `ToolError` | `runtime/mvp_runtime/crypto/lifecycle.py` | 367 | `operator_retirement_decision` | `current in TERMINAL_STATUSES` |
| `LIFECYCLE_TERMINAL_IMMUTABLE` | `ToolError` | `runtime/mvp_runtime/crypto/pool_transitions.py` | 140 | `apply_status_decisions` | `str(entry.get('status')) in TERMINAL_STATUSES` |
| `LIFECYCLE_TERMINAL_IMMUTABLE` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/retirement.py` | 81 | `resolve_pool_entries` | `terminal` |
| `LIFECYCLE_UNKNOWN_STRATEGY` | `ToolError` | `runtime/mvp_runtime/crypto/pool_transitions.py` | 132 | `apply_status_decisions` | `all_or_nothing and entry is None` |
| `LIFECYCLE_UNKNOWN_STRATEGY` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/retirement.py` | 78 | `resolve_pool_entries` | `unknown` |
| `LIMIT_FILL_UNPRICEABLE` | `ToolError` | `runtime/mvp_runtime/crypto/limit_entry.py` | 78 | `_price` | `isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0` |
| `LIMIT_FILL_UNPRICEABLE` | `ToolError` | `runtime/mvp_runtime/crypto/limit_entry.py` | 112 | `limit_entry_fill` | `direction not in (LONG, SHORT)` |
| `LIMIT_FILL_UNPRICEABLE` | `ToolError` | `runtime/mvp_runtime/crypto/limit_entry.py` | 114 | `limit_entry_fill` | `isinstance(limit_price, bool) or not isinstance(limit_price, (int, float)) or (not limit_price …` |
| `LIMIT_FILL_UNPRICEABLE` | `ToolError` | `runtime/mvp_runtime/crypto/limit_entry.py` | 116 | `limit_entry_fill` | `isinstance(tick_size, bool) or not isinstance(tick_size, (int, float)) or (not tick_size > 0)` |
| `LIMIT_FILL_UNPRICEABLE` | `ToolError` | `runtime/mvp_runtime/crypto/limit_entry.py` | 122 | `limit_entry_fill` | `isinstance(timeout_bars, bool) or not isinstance(timeout_bars, int) or timeout_bars < 1` |
| `LIMIT_FILL_UNPRICEABLE` | `ToolError` | `runtime/mvp_runtime/crypto/limit_entry.py` | 136 | `limit_entry_fill` | `not isinstance(bar, Mapping)` |
| `LIVE_API_BREAKER_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 1188 | `read_api_errors` | `—` |
| `LIVE_API_BREAKER_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 1190 | `read_api_errors` | `not isinstance(data, dict)` |
| `LIVE_API_BREAKER_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 1197 | `read_api_errors` | `not isinstance(held, dict)` |
| `LIVE_API_BREAKER_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 1203 | `read_api_errors` | `count is None or count < 0` |
| `LIVE_API_CALL_CLASS_UNKNOWN` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 1231 | `_known_class` | `call_class not in API_CALL_CLASSES` |
| `LIVE_ARM_ESCAPE_RETIRED` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/promotion.py` | 396 | `run_promotion_gates` | `live_tier == pool_store.LIVE_TIER_LIVE and escapes.get('allow_unconfirmed_holdout', False)` |
| `LIVE_BRACKET_BREAKER_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 954 | `read_bracket_failures` | `—` |
| `LIVE_BRACKET_BREAKER_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 956 | `read_bracket_failures` | `not isinstance(data, dict)` |
| `LIVE_BRACKET_BREAKER_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 963 | `read_bracket_failures` | `—` |
| `LIVE_BUDGET_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_budget.py` | 115 | `build_live_trading_budget_record` | `venue != SUPPORTED_VENUE` |
| `LIVE_BUDGET_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_budget.py` | 119 | `build_live_trading_budget_record` | `missing` |
| `LIVE_BUDGET_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_budget.py` | 126 | `build_live_trading_budget_record` | `—` |
| `LIVE_BUDGET_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_budget.py` | 131 | `build_live_trading_budget_record` | `numeric[key] <= 0` |
| `LIVE_BUDGET_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_budget.py` | 135 | `build_live_trading_budget_record` | `float(daily_count) != int(daily_count)` |
| `LIVE_BUDGET_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_budget.py` | 138 | `build_live_trading_budget_record` | `numeric['absolute_max_notional_usdt'] > HARD_CEILING_USDT` |
| `LIVE_BUDGET_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_budget.py` | 144 | `build_live_trading_budget_record` | `numeric['max_order_notional_usdt'] > numeric['absolute_max_notional_usdt']` |
| `LIVE_BUDGET_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_budget.py` | 153 | `build_live_trading_budget_record` | `not symbols` |
| `LIVE_BUDGET_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_budget.py` | 156 | `build_live_trading_budget_record` | `not (isinstance(registered_by, str) and registered_by.strip())` |
| `LIVE_BUDGET_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_budget.py` | 189 | `_validate` | `—` |
| `LIVE_BUDGET_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_budget.py` | 228 | `read_registered_budget` | `window != (None, None) and (not (isinstance(window[0], str) and isinstance(window[1], str) and …` |
| `LIVE_BUDGET_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/live_budget.py` | 216 | `read_registered_budget` | `—` |
| `LIVE_BUDGET_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/live_budget.py` | 218 | `read_registered_budget` | `not isinstance(stored, str) or recomputed != stored` |
| `LIVE_BUDGET_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/live_budget.py` | 287 | `write_registered_budget` | `not isinstance(stored, str) or integrity.sha256_record(body) != stored` |
| `LIVE_BUDGET_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_budget.py` | 205 | `read_registered_budget` | `—` |
| `LIVE_BUDGET_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_budget.py` | 207 | `read_registered_budget` | `not isinstance(data, dict)` |
| `LIVE_CORRECTIONS_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_correction.py` | 174 | `read_corrections` | `—` |
| `LIVE_CORRECTION_SCHEMA_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_correction.py` | 106 | `_validate_shape` | `missing` |
| `LIVE_CORRECTION_SCHEMA_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_correction.py` | 110 | `_validate_shape` | `unknown` |
| `LIVE_CORRECTION_SCHEMA_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_correction.py` | 113 | `_validate_shape` | `record['schema_version'] != SCHEMA_VERSION` |
| `LIVE_CORRECTION_SCHEMA_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_correction.py` | 121 | `_validate_shape` | `not (isinstance(code, str) and _REASON_CODE.fullmatch(code))` |
| `LIVE_CORRECTION_SCHEMA_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_correction.py` | 125 | `_validate_shape` | `record['disposition'] not in (VOID, SUPERSEDE)` |
| `LIVE_CORRECTION_SCHEMA_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_correction.py` | 133 | `_validate_shape` | `record['basis'] == ATTESTED` |
| `LIVE_CORRECTION_SCHEMA_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_correction.py` | 137 | `_validate_shape` | `record['basis'] != DERIVED` |
| `LIVE_CORRECTION_SCHEMA_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_correction.py` | 142 | `_validate_shape` | `supplied != _SUPERSEDE_ONLY` |
| `LIVE_CORRECTION_SCHEMA_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_correction.py` | 147 | `_validate_shape` | `not isinstance(value, (int, float)) or isinstance(value, bool)` |
| `LIVE_CORRECTION_SCHEMA_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_correction.py` | 152 | `_validate_shape` | `supplied` |
| `LIVE_CORRECTION_SCHEMA_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_correction.py` | 178 | `read_corrections` | `not isinstance(record, dict)` |
| `LIVE_CORRECTION_SCHEMA_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_correction.py` | 389 | `build_correction` | `disposition not in (VOID, SUPERSEDE)` |
| `LIVE_CORRECTION_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/live_correction.py` | 183 | `read_corrections` | `not isinstance(stored, str) or integrity.sha256_record(body) != stored` |
| `LIVE_CORRECTION_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/live_correction.py` | 185 | `read_corrections` | `record['previous_record_sha256'] != previous` |
| `LIVE_CORRECTION_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/live_correction.py` | 193 | `read_corrections` | `correction_id in seen_ids` |
| `LIVE_COUNTER_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 775 | `_stored_count` | `isinstance(value, bool) or not isinstance(value, int) or value < 0` |
| `LIVE_COUNTER_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 792 | `count_today` | `—` |
| `LIVE_COUNTER_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 794 | `count_today` | `not isinstance(data, dict)` |
| `LIVE_COUNTER_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 853 | `_increment` | `path.is_file()` |
| `LIVE_COUNTER_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 857 | `_increment` | `not isinstance(loaded, dict)` |
| `LIVE_DAILY_ORDER_CAP_REACHED` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 862 | `_increment` | `limit is not None and current >= limit` |
| `LIVE_ENTRY_BAR_UNKNOWN` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 1992 | `record_stop_cooldown` | `key is None or not _is_bar_time(until)` |
| `LIVE_ENTRY_CAPACITY_TAKEN` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 1964 | `mutate` | `problem is not None and problem[0] == LIVE_ENTRY_CAPACITY_TAKEN` |
| `LIVE_ENTRY_CLAIM_LOST` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 1980 | `mutate` | `not (isinstance(claim, Mapping) and claim.get('client_order_id') == client_order_id)` |
| `LIVE_ENTRY_CLAIM_MALFORMED` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 1944 | `claim_symbol` | `not (isinstance(symbol, str) and symbol.strip() and isinstance(door, str) and door.strip() and …` |
| `LIVE_ENTRY_COOLDOWN_UNCOMPUTABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 1860 | `stop_cooldown_until` | `isinstance(timeframe_minutes, bool) or not isinstance(timeframe_minutes, int) or timeframe_minu…` |
| `LIVE_ENTRY_COOLDOWN_UNCOMPUTABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 1862 | `stop_cooldown_until` | `not _is_bar_time(closed_at)` |
| `LIVE_ENTRY_EXPOSURE_TAKEN` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 1966 | `mutate` | `problem is not None` |
| `LIVE_ENTRY_MARKS_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 1721 | `read_live_entry_marks` | `—` |
| `LIVE_ENTRY_MARKS_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 1723 | `read_live_entry_marks` | `not isinstance(data, dict) or data.get('version') != ENTRY_MARKS_VERSION` |
| `LIVE_ENTRY_MARKS_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 1730 | `read_live_entry_marks` | `not isinstance(table, dict) or not all((isinstance(key, str) and _is_bar_time(value) for key, v…` |
| `LIVE_ENTRY_MARKS_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 1735 | `read_live_entry_marks` | `not isinstance(in_flight, dict) or not all((_is_claim(k, v) for k, v in in_flight.items()))` |
| `LIVE_ENTRY_MARKS_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 1740 | `read_live_entry_marks` | `not isinstance(notionals, dict) or not all((_is_claim_notional(k, v) for k, v in notionals.item…` |
| `LIVE_ENTRY_RESTING_ORDERS` | `ToolError` | `runtime/mvp_runtime/crypto/live_leg.py` | 798 | `execute_live_entry` | `left` |
| `LIVE_ENTRY_RESTING_ORDERS_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_leg.py` | 343 | `resting_orders` | `—` |
| `LIVE_ENTRY_RESTING_ORDERS_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_leg.py` | 346 | `resting_orders` | `not (isinstance(plain, list) and isinstance(conditional, list))` |
| `LIVE_ENTRY_SYMBOL_IN_FLIGHT` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 1954 | `mutate` | `held is not None` |
| `LIVE_ENTRY_SYMBOL_OCCUPIED` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 1960 | `mutate` | `any((str(p.get('symbol') or '') == symbol for p in booked))` |
| `LIVE_HISTORY_DUPLICATE` | `ToolError` | `runtime/mvp_runtime/crypto/live_ledger.py` | 110 | `read_live_outcomes_raw` | `outcome_id in seen_outcome_ids` |
| `LIVE_HISTORY_DUPLICATE` | `ToolError` | `runtime/mvp_runtime/crypto/live_ledger.py` | 115 | `read_live_outcomes_raw` | `settlement_id in seen_settlement_ids` |
| `LIVE_HISTORY_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/live_ledger.py` | 106 | `read_live_outcomes_raw` | `not isinstance(stored, str) or integrity.sha256_record(body) != stored` |
| `LIVE_HISTORY_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/live_pnl.py` | 152 | `daily_realized_pnl` | `—` |
| `LIVE_HISTORY_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_ledger.py` | 95 | `read_live_outcomes_raw` | `—` |
| `LIVE_ORDER_PERMDEC_MISSING` | `AuditError` | `runtime/mvp_runtime/audit.py` | 1188 | `build_live_order_audit` | `not (isinstance(permdec_id, str) and permdec_id)` |
| `LIVE_POSITION_SLOT_TAKEN` | `ToolError` | `runtime/mvp_runtime/crypto/live_position.py` | 417 | `_write` | `held is not None and held.get('position_id') != owner` |
| `LIVE_POSITION_STAGE_MISMATCH` | `ToolError` | `runtime/mvp_runtime/crypto/live_position.py` | 300 | `_read_position_file` | `data.get('stage') != LIVE_STAGE` |
| `LIVE_POSITION_STATE_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_position.py` | 292 | `_read_position_file` | `—` |
| `LIVE_POSITION_SYMBOL_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_position.py` | 271 | `live_position_path` | `not symbol or not symbol.replace('_', '').replace('-', '').isalnum()` |
| `LIVE_POSITION_SYMBOL_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_position.py` | 277 | `live_position_path` | `path.parent != resolved_base` |
| `LIVE_POSITION_UNATTRIBUTABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_position.py` | 248 | `position_symbol` | `not symbol` |
| `MALFORMED_ANALYSIS` | `WorkerBlocked` | `runtime/mvp_runtime/worker.py` | 509 | `_require_analysis` | `not isinstance(analysis, Mapping)` |
| `MALFORMED_ANALYSIS` | `WorkerBlocked` | `runtime/mvp_runtime/worker.py` | 512 | `_require_analysis` | `missing` |
| `MALFORMED_ANALYSIS` | `WorkerBlocked` | `runtime/mvp_runtime/worker.py` | 515 | `_require_analysis` | `not isinstance(summary, str) or not summary.strip()` |
| `MALFORMED_ANALYSIS` | `WorkerBlocked` | `runtime/mvp_runtime/worker.py` | 518 | `_require_analysis` | `not isinstance(facts, list)` |
| `MALFORMED_BRACKET_LEG` | `ToolError` | `runtime/mvp_runtime/crypto/live_leg.py` | 444 | `build_bracket_intent` | `leg not in ('SL', 'TP')` |
| `MALFORMED_DIRECTION` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 318 | `build_live_order_intent` | `direction not in {'LONG', 'SHORT'}` |
| `MALFORMED_DIRECTION` | `ToolError` | `runtime/mvp_runtime/crypto/live_position.py` | 170 | `build_live_position` | `direction not in {'LONG', 'SHORT'}` |
| `MALFORMED_LIVE_ORDER_INTENT` | `ToolError` | `runtime/mvp_runtime/crypto/order_request.py` | 129 | `build_order_request` | `not (isinstance(symbol, str) and symbol)` |
| `MALFORMED_LIVE_ORDER_INTENT` | `ToolError` | `runtime/mvp_runtime/crypto/order_request.py` | 131 | `build_order_request` | `side not in ('BUY', 'SELL')` |
| `MALFORMED_LIVE_ORDER_INTENT` | `ToolError` | `runtime/mvp_runtime/crypto/order_request.py` | 133 | `build_order_request` | `not (isinstance(client_order_id, str) and CLIENT_ORDER_ID_PATTERN.match(client_order_id))` |
| `MALFORMED_LIVE_ORDER_INTENT` | `ToolError` | `runtime/mvp_runtime/crypto/order_request.py` | 139 | `build_order_request` | `order_type not in SUPPORTED_ORDER_TYPES` |
| `MALFORMED_LIVE_ORDER_INTENT` | `ToolError` | `runtime/mvp_runtime/crypto/order_request.py` | 158 | `build_order_request` | `not (isinstance(stop_price, (int, float)) and stop_price > 0)` |
| `MALFORMED_LIVE_ORDER_INTENT` | `ToolError` | `runtime/mvp_runtime/crypto/order_request.py` | 169 | `build_order_request` | `working_type not in WORKING_TYPES` |
| `MALFORMED_LIVE_ORDER_INTENT` | `ToolError` | `runtime/mvp_runtime/crypto/order_request.py` | 179 | `build_order_request` | `close_position` |
| `MALFORMED_LIVE_ORDER_INTENT` | `ToolError` | `runtime/mvp_runtime/crypto/order_request.py` | 187 | `build_order_request` | `not (isinstance(price, (int, float)) and price > 0)` |
| `MALFORMED_LIVE_ORDER_INTENT` | `ToolError` | `runtime/mvp_runtime/crypto/order_request.py` | 194 | `build_order_request` | `time_in_force not in TIMES_IN_FORCE` |
| `MALFORMED_LIVE_ORDER_INTENT` | `ToolError` | `runtime/mvp_runtime/crypto/order_request.py` | 206 | `build_order_request` | `not (isinstance(quantity, (int, float)) and quantity > 0)` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/bridge_idempotency.py` | 87 | `request_id_of` | `not isinstance(raw, str) or not raw.strip()` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/bridge_idempotency.py` | 92 | `request_id_of` | `len(value) > MAX_REQUEST_ID_LENGTH` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/bridge_idempotency.py` | 116 | `fingerprint` | `—` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 238 | `apply_dispatch` | `not isinstance(request, dict)` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 268 | `apply_dispatch` | `isinstance(raw_kind, str) and raw_kind.strip()` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 284 | `apply_dispatch` | `not isinstance(raw_seeds, str) or not raw_seeds.strip()` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 288 | `apply_dispatch` | `len(raw_seeds) > MAX_SEED_CHARS` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 540 | `apply_workflow_command` | `not isinstance(workflow_id, str) or not wf.WORKFLOW_ID_PATTERN.match(workflow_id.strip())` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 554 | `apply_workflow_command` | `not isinstance(usage, dict)` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 568 | `apply_workflow_command` | `not isinstance(expected, int) or isinstance(expected, bool) or expected < 1` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 597 | `apply_workflow_command` | `not isinstance(step_key, str) or not step_key.strip()` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 618 | `_bounded_int` | `isinstance(value, bool) or not isinstance(value, int) or value < 0` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/knowledge_bridge.py` | 113 | `apply_knowledge` | `not isinstance(request, dict)` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/knowledge_bridge.py` | 117 | `apply_knowledge` | `not isinstance(command, str) or not command.strip()` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 235 | `apply_work` | `not isinstance(request, dict)` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 260 | `apply_work` | `not isinstance(kind, str) or not kind.strip()` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 307 | `apply_work` | `not isinstance(raw_seeds, str) or not raw_seeds.strip() or len(raw_seeds) > MAX_SEED_CHARS` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 438 | `_attempt_fields` | `not isinstance(attempt_id, str) or not ATTEMPT_ID_PATTERN.match(attempt_id)` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 440 | `_attempt_fields` | `not isinstance(workflow_id, str) or not WORKFLOW_ID_PATTERN.match(workflow_id)` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 442 | `_attempt_fields` | `profile_name != ASSISTANT_PROFILE` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 448 | `_attempt_fields` | `not isinstance(raw_options, dict) or set(raw_options) - _WORKFLOW_OPTION_KEYS` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 454 | `_attempt_fields` | `not isinstance(value, bool)` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 459 | `_attempt_fields` | `not isinstance(raw_inputs, dict) or not raw_inputs or len(raw_inputs) > MAX_STEPS` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 466 | `_attempt_fields` | `not (isinstance(key, str) and STEP_KEY_PATTERN.match(key) and isinstance(ref, str) and RESULT_R…` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/read_bridge.py` | 137 | `apply_read` | `not isinstance(request, dict)` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/read_bridge.py` | 145 | `apply_read` | `not isinstance(command, str) or not command.strip()` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/read_bridge.py` | 156 | `apply_read` | `argument is not None and (not isinstance(argument, str))` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 156 | `decode_request` | `—` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 206 | `client_id_of` | `not isinstance(raw, str) or not raw.strip()` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 211 | `client_id_of` | `len(value) > MAX_CLIENT_ID_LENGTH or not _CLIENT_ID.match(value)` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 348 | `_require_domain` | `not isinstance(raw, str) or not raw.strip()` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 366 | `_require_scope` | `not isinstance(raw, str) or not raw.strip()` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 810 | `apply_switch` | `not isinstance(request, dict)` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 823 | `apply_switch` | `not isinstance(command, str) or not command.strip()` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 869 | `apply_switch` | `isinstance(raw_mode, str) and raw_mode.strip()` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 892 | `apply_switch` | `approval_id is not None and (not isinstance(approval_id, str) or not approval_id.strip())` |
| `MALFORMED_RESPONSE` | `ProviderError` | `runtime/mvp_runtime/providers.py` | 576 | `_parse_hosted_response` | `—` |
| `MALFORMED_RESPONSE` | `ProviderError` | `runtime/mvp_runtime/providers.py` | 578 | `_parse_hosted_response` | `not isinstance(analysis, dict) or any((k not in analysis for k in _REQUIRED_ANALYSIS_KEYS))` |
| `MALFORMED_RESPONSE` | `ProviderError` | `runtime/mvp_runtime/providers.py` | 586 | `_parse_hosted_response` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/account.py` | 336 | `fill_history` | `not isinstance(rows, list)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/account.py` | 375 | `_signed_get` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/account.py` | 386 | `_build` | `not isinstance(account, dict)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1178 | `_parse` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1180 | `_parse` | `not isinstance(rows, list)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1190 | `_parse` | `not isinstance(row, list) or len(row) < 7` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1211 | `_parse` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1268 | `funding_history` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1367 | `_parse_derivative_page` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1369 | `_parse_derivative_page` | `not isinstance(rows, list)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1374 | `_parse_derivative_page` | `not isinstance(row, list) or len(row) < 7` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1378 | `_parse_derivative_page` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1467 | `_parse_positioning_page` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1469 | `_parse_positioning_page` | `not isinstance(rows, list)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1474 | `_parse_positioning_page` | `not isinstance(row, dict)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1480 | `_parse_positioning_page` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1529 | `order_book` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1531 | `order_book` | `not isinstance(payload, dict)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1553 | `_parse_book_side` | `not isinstance(rows, list)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1557 | `_parse_book_side` | `not isinstance(row, (list, tuple)) or len(row) < 2` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1561 | `_parse_book_side` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1567 | `_parse_book_side` | `not (price > 0.0 and quantity > 0.0)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1600 | `exchange_info` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1602 | `exchange_info` | `not isinstance(payload, dict)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1708 | `_parse` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1710 | `_parse` | `not isinstance(rows, list)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1719 | `_parse` | `not isinstance(row, dict)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1739 | `_parse` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1775 | `live_symbols` | `not isinstance(listings, list) or not isinstance(metas, list)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1777 | `live_symbols` | `len(listings) != len(metas)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1808 | `_info` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 2032 | `_parse` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 2036 | `_parse` | `not isinstance(payload, list)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 2052 | `_parse` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 2117 | `_parse_open_interest` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 2121 | `_parse_open_interest` | `not isinstance(payload, list)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 2144 | `_parse_open_interest` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/naver_research.py` | 650 | `_parse` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/naver_research.py` | 652 | `_parse` | `not isinstance(rows, list)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/naver_research.py` | 792 | `trend` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/naver_research.py` | 828 | `competition` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/naver_research.py` | 830 | `competition` | `not isinstance(items, list)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/tools.py` | 294 | `_parse` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/tools.py` | 296 | `_parse` | `not isinstance(results, list)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/tools.py` | 384 | `_parse` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/tools.py` | 386 | `_parse` | `not isinstance(results, list)` |
| `MEMORY_UNAVAILABLE` | `OperatorBlocked` | `runtime/mvp_runtime/memory_console.py` | 144 | `apply_memory_command` | `working_memory is None` |
| `MEMORY_UNAVAILABLE` | `OperatorBlocked` | `runtime/mvp_runtime/memory_console.py` | 175 | `apply_memory_command` | `ledger is None` |
| `MIRROR_IS_SEND_ONLY` | `OperatorBlocked` | `runtime/mvp_runtime/operator.py` | 886 | `poll` | `—` |
| `MIRROR_IS_SEND_ONLY` | `OperatorBlocked` | `runtime/mvp_runtime/operator.py` | 891 | `peek` | `—` |
| `MISSING_BRACKET_QUANTITY` | `ToolError` | `runtime/mvp_runtime/crypto/live_leg.py` | 465 | `build_bracket_intent` | `not (isinstance(quantity, (int, float)) and quantity > 0)` |
| `MISSING_CORE_RULES` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 190 | `build_task` | `not rule_ids` |
| `MISSING_CREATOR` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 530 | `build_schedule` | `not (isinstance(created_by, str) and created_by.strip())` |
| `MISSING_ENTRY_PRICE` | `ToolError` | `runtime/mvp_runtime/crypto/live_position.py` | 178 | `build_live_position` | `entry_price <= 0` |
| `MISSING_OPERATOR` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 418 | `build_core_candidate` | `not (isinstance(proposed_by, str) and proposed_by.strip())` |
| `MISSING_OPERATOR` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 496 | `decide_core_candidate` | `not (isinstance(decided_by, str) and decided_by.strip())` |
| `MISSING_OPERATOR` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 597 | `promote_candidate` | `not (isinstance(promoted_by, str) and promoted_by.strip())` |
| `MISSING_OPERATOR` | `ProgramizationBlocked` | `runtime/mvp_runtime/program_request.py` | 139 | `create_program_request` | `not (isinstance(requested_by, str) and requested_by.strip())` |
| `MISSING_OPERATOR` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 384 | `_require_operator` | `not (isinstance(actor, str) and actor.strip())` |
| `MISSING_ORDER_NOTIONAL` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 324 | `build_live_order_intent` | `notional_usdt <= 0` |
| `MISSING_ORDER_QUANTITY` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 322 | `build_live_order_intent` | `quantity <= 0` |
| `MISSING_POSITION_QUANTITY` | `ToolError` | `runtime/mvp_runtime/crypto/live_position.py` | 176 | `build_live_position` | `quantity <= 0` |
| `MISSING_PRINCIPAL` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 308 | `submit` | `not (isinstance(principal, str) and principal.strip())` |
| `MISSING_RATIONALE` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 420 | `build_core_candidate` | `not (isinstance(rationale, str) and rationale.strip())` |
| `MISSING_REASON` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 498 | `decide_core_candidate` | `not (isinstance(reason, str) and reason.strip())` |
| `MISSING_REASON` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 599 | `promote_candidate` | `not (isinstance(reason, str) and reason.strip())` |
| `MISSING_REASON` | `OperatorBlocked` | `runtime/mvp_runtime/memory_console.py` | 169 | `apply_memory_command` | `not reason` |
| `MISSING_REASON` | `ProgramizationBlocked` | `runtime/mvp_runtime/program_request.py` | 141 | `create_program_request` | `not (isinstance(reason, str) and reason.strip())` |
| `MISSING_REASON` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 386 | `_require_operator` | `not (isinstance(reason, str) and reason.strip())` |
| `MISSING_REQUEST` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 518 | `build_schedule` | `kind == KIND_TASK and (not request)` |
| `MISSING_REQUEST` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 524 | `build_schedule` | `kind == KIND_CONTENT_IDEATION and (not request)` |
| `MISSING_REQUESTER` | `TaskRegistryBlocked` | `runtime/mvp_runtime/task_registry.py` | 274 | `build_entry` | `not (isinstance(requester_id, str) and requester_id.strip())` |
| `MISSING_REQUESTER` | `TaskRegistryBlocked` | `runtime/mvp_runtime/task_registry.py` | 301 | `build_entry` | `origin == WORKFLOW_ORIGIN and attempt is None` |
| `MISSING_REQUEST_ID` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 310 | `submit` | `not (isinstance(request_id, str) and request_id.strip())` |
| `MISSING_SYMBOL` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 320 | `build_live_order_intent` | `not symbol` |
| `MISSING_SYMBOL` | `ToolError` | `runtime/mvp_runtime/crypto/live_position.py` | 172 | `build_live_position` | `not symbol` |
| `MISSING_TASK_ID` | `ControlBlocked` | `runtime/mvp_runtime/control.py` | 865 | `apply_command` | `not (isinstance(arg, str) and arg.strip())` |
| `MODE_NOT_PERMITTED` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 871 | `apply_switch` | `mode not in _DISABLE_MODES` |
| `NOT_ALLOWED` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 580 | `build_permission_decision` | `disposition not in _BUILDABLE_DISPOSITIONS` |
| `NOT_ALLOWED` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 587 | `build_permission_decision` | `disposition == EXECUTE_AND_REPORT and permission_scope not in _EXECUTE_AND_REPORT_SCOPES` |
| `NOT_ALLOWED` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 594 | `build_permission_decision` | `disposition == APPROVAL_REQUIRED and permission_scope not in _APPROVAL_REQUIRED_SCOPES` |
| `NOT_ALLOWED` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 601 | `build_permission_decision` | `disposition == 'BLOCK' and permission_scope not in _BLOCK_EVIDENCE_SCOPES` |
| `NOT_ALLOWED` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 786 | `build_resource_refusal_permission_decision` | `permission_scope not in _BLOCK_EVIDENCE_SCOPES` |
| `NOT_APPROVAL_REQUIRED` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 168 | `build_approval_request` | `decision != 'APPROVAL_REQUIRED'` |
| `NOT_APPROVED` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 280 | `validate_spendable_approval` | `status != STATUS_APPROVED` |
| `NOT_APPROVED` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 441 | `build_consumed_record` | `status != STATUS_APPROVED` |
| `NOT_APPROVED` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 639 | `_spend` | `status != approval_mod.STATUS_APPROVED` |
| `NOT_AUTHORIZED` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 123 | `assert_authorization` | `not isinstance(authorization, Authorization)` |
| `NOT_A_CANDIDATE` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 587 | `promote_candidate` | `not isinstance(candidate, Mapping)` |
| `NOT_A_CANDIDATE` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 589 | `promote_candidate` | `candidate.get('status') != CANDIDATE_STATUS or candidate.get('scope') != CANDIDATE_SCOPE` |
| `NOT_A_CANDIDATE` | `PlannerBlocked` | `runtime/mvp_runtime/planner.py` | 282 | `select_candidate_role` | `status != 'candidate' or role.get('routable') is not False` |
| `NOT_A_CORE_CANDIDATE` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 481 | `decide_core_candidate` | `not isinstance(candidate, Mapping) or candidate.get('scope') != CORE_CANDIDATE_SCOPE` |
| `NOT_BOUND` | `PlannerBlocked` | `runtime/mvp_runtime/assignment.py` | 66 | `build_role_assignment` | `not (isinstance(ccb, str) and ccb.startswith('ccb-'))` |
| `NOT_BOUND` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 559 | `build_permission_decision` | `not (isinstance(ccb, str) and ccb.startswith('ccb-'))` |
| `NOT_BOUND` | `WorkerBlocked` | `runtime/mvp_runtime/validator.py` | 208 | `run_validation_worker` | `not (isinstance(ccb, str) and ccb.startswith('ccb-'))` |
| `NOT_BOUND` | `WorkerBlocked` | `runtime/mvp_runtime/worker.py` | 649 | `run_analysis_worker` | `not (isinstance(ccb, str) and ccb.startswith('ccb-'))` |
| `NOT_EXPIRED` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 408 | `expire` | `not is_expired(approval, now=now)` |
| `NOT_INDEPENDENT` | `WorkerBlocked` | `runtime/mvp_runtime/validator.py` | 210 | `run_validation_worker` | `validator_assignment.get('role_id') == agent_output.get('role_id')` |
| `NOT_PENDING` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 111 | `_require_pending` | `status != STATUS_PENDING` |
| `NOT_PRIVATE_CHANNEL` | `OperatorBlocked` | `runtime/mvp_runtime/operator.py` | 241 | `verify_control_channel` | `message.channel != PRIMARY_CHANNEL or message.chat_type != 'private'` |
| `NOT_RECEIVED` | `PlannerBlocked` | `runtime/mvp_runtime/planner.py` | 196 | `classify_task` | `lifecycle.get('status') != 'RECEIVED'` |
| `NOT_VALIDATED_MEMORY` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 399 | `build_core_candidate` | `not isinstance(validated_entry, Mapping)` |
| `NOT_VALIDATED_MEMORY` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 402 | `build_core_candidate` | `validated_entry.get('status') != VALIDATED_STATUS or validated_entry.get('scope') != VALIDATED_…` |
| `NO_API_KEY` | `ToolError` | `runtime/mvp_runtime/crypto/account.py` | 346 | `_signed_get` | `not api_key or not api_secret` |
| `NO_API_KEY` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 2006 | `liquidation_history` | `not api_key` |
| `NO_API_KEY` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 2089 | `open_interest_history` | `not api_key` |
| `NO_API_KEY` | `ToolError` | `runtime/mvp_runtime/naver_research.py` | 586 | `_headers` | `missing` |
| `NO_API_KEY` | `ToolError` | `runtime/mvp_runtime/naver_research.py` | 717 | `_headers` | `missing` |
| `NO_API_KEY` | `ProviderError` | `runtime/mvp_runtime/providers.py` | 662 | `generate` | `not api_key` |
| `NO_API_KEY` | `ProviderError` | `runtime/mvp_runtime/providers.py` | 791 | `generate` | `not api_key` |
| `NO_API_KEY` | `ToolError` | `runtime/mvp_runtime/tools.py` | 266 | `search` | `not api_key` |
| `NO_API_KEY` | `ToolError` | `runtime/mvp_runtime/tools.py` | 357 | `search` | `not api_key` |
| `NO_APPROVAL_ID` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 174 | `build_approval_request` | `not (isinstance(approval_id, str) and approval_id.startswith('approval_'))` |
| `NO_APPROVAL_ID` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 845 | `apply_command` | `not approval_id` |
| `NO_BOT_TOKEN` | `OperatorBlocked` | `runtime/mvp_runtime/operator.py` | 1264 | `_assert` | `not token` |
| `NO_CONSUMPTION_REF` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 445 | `build_consumed_record` | `not (isinstance(consumption_ref, str) and consumption_ref.strip())` |
| `NO_DECISION_REASON` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 380 | `record_decision` | `not (isinstance(reason, str) and reason.strip())` |
| `NO_DELIVERABLE` | `OperatorBlocked` | `runtime/mvp_runtime/registry_console.py` | 305 | `apply_registry_command` | `entry.status != task_registry.DELIVERED` |
| `NO_ELIGIBLE_KEYWORD` | `ToolError` | `runtime/mvp_runtime/blog_content.py` | 483 | `run_content_ideation` | `not target` |
| `NO_FEEDBACK_TARGET` | `OperatorBlocked` | `runtime/mvp_runtime/operator_feedback.py` | 237 | `apply_feedback` | `target is None` |
| `NO_MODEL_BUDGET` | `WorkerBlocked` | `runtime/mvp_runtime/validator.py` | 217 | `run_validation_worker` | `not isinstance(max_model_calls, int) or max_model_calls < 1` |
| `NO_MODEL_BUDGET` | `WorkerBlocked` | `runtime/mvp_runtime/worker.py` | 656 | `run_analysis_worker` | `not isinstance(max_model_calls, int) or max_model_calls < 1` |
| `NO_ORDER_API_KEY` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 491 | `_signed_request` | `not api_key or not api_secret` |
| `NO_ORDER_API_KEY` | `ToolError` | `runtime/mvp_runtime/crypto/testnet_execution.py` | 199 | `_signed_request` | `not api_key or not api_secret` |
| `NO_ROLE_OUTPUT_CONTRACT` | `PlannerBlocked` | `runtime/mvp_runtime/planner.py` | 129 | `role_output_spec` | `not isinstance(contract, Mapping) or not contract` |
| `NO_ROUTABLE_ROLE` | `PlannerBlocked` | `runtime/mvp_runtime/planner.py` | 327 | `select_role` | `not candidates` |
| `NO_TRIAL_AUTHORIZATION` | `PlannerBlocked` | `runtime/mvp_runtime/assignment.py` | 114 | `build_role_assignment` | `trial and (not (isinstance(trial_authorization_ref, str) and trial_authorization_ref.strip()))` |
| `NO_VERIFICATION_REF` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 378 | `record_decision` | `not (isinstance(verification.verification_ref, str) and verification.verification_ref.strip())` |
| `OBSERVATION_INCOMPLETE` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 260 | `observe_completed_run` | `not (task_id and trace_id and ccb.startswith('ccb-') and isinstance(task_revision, int) and (ta…` |
| `OFFSET_PERSIST_FAILED` | `OperatorBlocked` | `runtime/mvp_runtime/operator.py` | 1324 | `_save_offset` | `—` |
| `OFFSET_STATE_MALFORMED` | `OperatorBlocked` | `runtime/mvp_runtime/operator.py` | 1297 | `_load_offset` | `—` |
| `OI_INTERVAL_UNKNOWN` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 2079 | `open_interest_history` | `interval not in OI_INTERVALS` |
| `OI_SYMBOL_MISSING` | `ToolError` | `runtime/mvp_runtime/crypto/oi_store.py` | 191 | `append_rows` | `not name` |
| `ORDERBOOK_CROSSED` | `ToolError` | `runtime/mvp_runtime/crypto/orderbook_store.py` | 193 | `summarize_book` | `not best_bid < best_ask` |
| `ORDERBOOK_DEPTH_EMPTY` | `ToolError` | `runtime/mvp_runtime/crypto/orderbook_store.py` | 199 | `summarize_book` | `total <= 0.0` |
| `ORDERBOOK_IMPACT_UNPRICEABLE` | `ToolError` | `runtime/mvp_runtime/crypto/orderbook_store.py` | 217 | `_walked_level` | `—` |
| `ORDERBOOK_IMPACT_UNPRICEABLE` | `ToolError` | `runtime/mvp_runtime/crypto/orderbook_store.py` | 220 | `_walked_level` | `isinstance(value, bool) or not isinstance(value, (int, float)) or (not 0.0 < value < float('inf…` |
| `ORDERBOOK_IMPACT_UNPRICEABLE` | `ToolError` | `runtime/mvp_runtime/crypto/orderbook_store.py` | 245 | `estimate_market_impact` | `—` |
| `ORDERBOOK_IMPACT_UNPRICEABLE` | `ToolError` | `runtime/mvp_runtime/crypto/orderbook_store.py` | 247 | `estimate_market_impact` | `side not in ('BUY', 'SELL')` |
| `ORDERBOOK_IMPACT_UNPRICEABLE` | `ToolError` | `runtime/mvp_runtime/crypto/orderbook_store.py` | 253 | `estimate_market_impact` | `not wanted > 0.0 or wanted == float('inf')` |
| `ORDERBOOK_SIDE_EMPTY` | `ToolError` | `runtime/mvp_runtime/crypto/orderbook_store.py` | 190 | `summarize_book` | `not bids or not asks` |
| `ORDERBOOK_SYMBOL_MISSING` | `ToolError` | `runtime/mvp_runtime/crypto/orderbook_store.py` | 371 | `append_snapshot` | `not name` |
| `ORDERBOOK_TIMESTAMP_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/orderbook_store.py` | 150 | `period_start` | `—` |
| `ORDERBOOK_TIMESTAMP_MISSING` | `ToolError` | `runtime/mvp_runtime/crypto/orderbook_store.py` | 374 | `append_snapshot` | `not stamp` |
| `ORDER_HALTED` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 553 | `submit` | `refusal is not None` |
| `ORDER_HALTED` | `SubmitRefused` | `runtime/mvp_runtime/crypto/live_execution.py` | 890 | `submit_and_reconcile` | `exc.reason_code == ORDER_HALTED` |
| `ORDER_HALTED` | `ToolError` | `runtime/mvp_runtime/crypto/testnet_execution.py` | 240 | `submit` | `refusal is not None` |
| `ORDER_HOST_NOT_ALLOWED` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 462 | `__init__` | `host not in ALLOWED_ORDER_HOSTS` |
| `ORDER_MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 536 | `_signed_request` | `—` |
| `ORDER_MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 740 | `position_mode` | `not isinstance(hedge, bool)` |
| `ORDER_MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/order_request.py` | 269 | `_order_rows` | `not (isinstance(body, list) and all((isinstance(row, dict) for row in body)))` |
| `ORDER_MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/testnet_execution.py` | 233 | `_signed_request` | `—` |
| `ORDER_OUTCOME_UNKNOWN` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 571 | `submit` | `code in VENUE_UNKNOWN_OUTCOME_CODES` |
| `ORDER_OUTCOME_UNKNOWN` | `ToolError` | `runtime/mvp_runtime/crypto/testnet_execution.py` | 256 | `submit` | `code in VENUE_UNKNOWN_OUTCOME_CODES` |
| `ORDER_REJECTED` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 563 | `submit` | `code == VENUE_DUPLICATE_CLIENT_ORDER_ID` |
| `ORDER_REJECTED` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 574 | `submit` | `code is not None` |
| `ORDER_REJECTED` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 653 | `fetch_order` | `code is not None` |
| `ORDER_REJECTED` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 689 | `open_orders` | `code is not None` |
| `ORDER_REJECTED` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 714 | `algo_open_orders` | `code is not None` |
| `ORDER_REJECTED` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 734 | `position_mode` | `code is not None` |
| `ORDER_REJECTED` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 778 | `cancel_order` | `code is not None` |
| `ORDER_REJECTED` | `ToolError` | `runtime/mvp_runtime/crypto/testnet_execution.py` | 113 | `submit` | `client_id in self._submitted` |
| `ORDER_REJECTED` | `ToolError` | `runtime/mvp_runtime/crypto/testnet_execution.py` | 249 | `submit` | `code == VENUE_DUPLICATE_CLIENT_ORDER_ID` |
| `ORDER_REJECTED` | `ToolError` | `runtime/mvp_runtime/crypto/testnet_execution.py` | 258 | `submit` | `code is not None` |
| `ORDER_REJECTED` | `ToolError` | `runtime/mvp_runtime/crypto/testnet_execution.py` | 272 | `fetch_order` | `code is not None` |
| `ORDER_REJECTED` | `ToolError` | `runtime/mvp_runtime/crypto/testnet_execution.py` | 291 | `open_positions` | `code is not None` |
| `ORDER_REJECTED` | `ToolError` | `runtime/mvp_runtime/crypto/testnet_execution.py` | 307 | `cancel_order` | `code is not None` |
| `ORDER_TRANSPORT` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 522 | `_signed_request` | `code is None` |
| `ORDER_TRANSPORT` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 532 | `_signed_request` | `—` |
| `ORDER_TRANSPORT` | `ToolError` | `runtime/mvp_runtime/crypto/testnet_execution.py` | 226 | `_signed_request` | `code is None` |
| `ORDER_TRANSPORT` | `ToolError` | `runtime/mvp_runtime/crypto/testnet_execution.py` | 229 | `_signed_request` | `—` |
| `OUTCOME_HISTORY_DUPLICATE` | `ToolError` | `runtime/mvp_runtime/crypto/paper.py` | 875 | `read_outcomes` | `outcome_id in seen_outcome_ids` |
| `OUTCOME_HISTORY_DUPLICATE` | `ToolError` | `runtime/mvp_runtime/crypto/paper.py` | 880 | `read_outcomes` | `settlement_id in seen_settlement_ids` |
| `OUTCOME_HISTORY_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/paper.py` | 869 | `read_outcomes` | `not isinstance(stored, str) or integrity.sha256_record(body) != stored` |
| `OUTCOME_HISTORY_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/paper.py` | 857 | `read_outcomes` | `—` |
| `OUTPUT_SCHEMA_INVALID` | `WorkerBlocked` | `runtime/mvp_runtime/worker.py` | 745 | `run_analysis_worker` | `—` |
| `OUT_OF_MVP_SCOPE` | `PlannerBlocked` | `runtime/mvp_runtime/planner.py` | 204 | `classify_task` | `_READ_ONLY_CONSTRAINT not in constraints` |
| `PATH_ESCAPE` | `ToolBlocked` | `runtime/mvp_runtime/workspace.py` | 175 | `resolve_target` | `'..' in candidate.parts` |
| `PATH_ESCAPE` | `ToolBlocked` | `runtime/mvp_runtime/workspace.py` | 203 | `resolve_target` | `target != base_real and base_real not in target.parents` |
| `PATH_TOO_LONG` | `ToolBlocked` | `runtime/mvp_runtime/workspace.py` | 164 | `resolve_target` | `len(relative_path) > MAX_PATH_CHARS` |
| `PATTERN_NOT_FOUND` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 411 | `transition_review` | `latest is None` |
| `PATTERN_NOT_FOUND` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 506 | `create_program_candidate` | `latest is None` |
| `PATTERN_NOT_FOUND` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization_cli.py` | 192 | `main` | `not args.target` |
| `PATTERN_NOT_FOUND` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization_cli.py` | 220 | `main` | `not args.target` |
| `PDF_BACKEND_UNKNOWN` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/pdf_text.py` | 258 | `_run_backend` | `—` |
| `PDF_BASE64_INVALID` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/service.py` | 215 | `_decode_pdf` | `—` |
| `PDF_BASE64_REQUIRED` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/service.py` | 209 | `_decode_pdf` | `not isinstance(payload, str) or not payload.strip()` |
| `PDF_CORRUPT` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/pdf_text.py` | 299 | `_extract_with_pdftotext` | `completed.returncode == 1` |
| `PDF_ENCRYPTED` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/pdf_text.py` | 305 | `_extract_with_pdftotext` | `completed.returncode == 3` |
| `PDF_ENCRYPTED` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/pdf_text.py` | 341 | `_extract_with_pypdf` | `getattr(reader, 'is_encrypted', False)` |
| `PDF_EXTRACTION_FAILED` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/pdf_text.py` | 162 | `extract_pdf_text` | `—` |
| `PDF_EXTRACTION_TIMEOUT` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/pdf_text.py` | 290 | `_extract_with_pdftotext` | `—` |
| `PDF_INPUT_EMPTY` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/pdf_text.py` | 237 | `_check_pdf_bytes` | `not data` |
| `PDF_INPUT_INVALID` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/pdf_text.py` | 235 | `_check_pdf_bytes` | `not isinstance(data, (bytes, bytearray))` |
| `PDF_NOT_A_PDF` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/pdf_text.py` | 247 | `_check_pdf_bytes` | `PDF_MAGIC not in bytes(data[:1024])` |
| `PDF_NO_BACKEND` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/pdf_text.py` | 121 | `extract_pdf_text` | `not backends` |
| `PDF_NO_TEXT_LAYER` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/pdf_text.py` | 156 | `extract_pdf_text` | `failures and all(('PDF_NO_TEXT_LAYER' in failure for failure in failures))` |
| `PDF_TEXT_TOO_LARGE` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/pdf_text.py` | 353 | `_extract_with_pypdf` | `total > MAX_TEXT_CHARS` |
| `PDF_TEXT_TOO_LARGE` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/pdf_text.py` | 363 | `_check_text_size` | `len(text) > MAX_TEXT_CHARS` |
| `PDF_TOO_LARGE` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/pdf_text.py` | 239 | `_check_pdf_bytes` | `len(data) > MAX_PDF_BYTES` |
| `PEER_CREDENTIALS_UNAVAILABLE` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 477 | `authorize_peer` | `creds is None` |
| `PEER_NOT_PERMITTED` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 485 | `authorize_peer` | `creds[1] not in self.allowed_client_uids` |
| `PERMISSION_DECISION_MISSING` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 290 | `validate_spendable_approval` | `permission_decision is None` |
| `PERMISSION_DECISION_MISSING` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 854 | `apply_command` | `permission_decision is None` |
| `PERMISSION_DECISION_MISSING` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 650 | `_spend` | `decision is None` |
| `PERMISSION_NOT_ALLOW` | `PlannerBlocked` | `runtime/mvp_runtime/assignment.py` | 95 | `build_role_assignment` | `permission_decision.get('decision', {}).get('permission_decision') != 'ALLOW'` |
| `PERMISSION_NOT_EXECUTABLE_READ_ONLY` | `KernelBlocked` | `runtime/read_only_kernel/policy.py` | 18 | `adapt_policy` | `permission.get('evaluation_status') != 'DECIDED'` |
| `PERMISSION_SCHEMA_INVALID` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 756 | `build_permission_decision` | `—` |
| `PERMISSION_SEMANTICS_INVALID` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 759 | `build_permission_decision` | `issues` |
| `PLANNED_TASK_INVALID` | `PlannerBlocked` | `runtime/mvp_runtime/prime.py` | 285 | `plan_task` | `—` |
| `PLANNED_TASK_INVALID` | `PlannerBlocked` | `runtime/mvp_runtime/trial.py` | 305 | `_plan_trial_run` | `—` |
| `PLAN_CONFLICT` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 1050 | `propose_update` | `ungated` |
| `PLAN_CONFLICT` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 1066 | `propose_update` | `validated.budget.max_model_calls < budget['reserved_model_calls']` |
| `PLAN_CONFLICT` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 1075 | `propose_update` | `key not in new_keys` |
| `PLAN_CONFLICT` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 1077 | `propose_update` | `s['status'] == wf.S_CANCELLED` |
| `PLAN_CONFLICT` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 1087 | `propose_update` | `changed` |
| `PLAN_INVALID` | `WorkflowBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 485 | `apply_workflow_command` | `not isinstance(plan, dict)` |
| `PLAN_INVALID` | `WorkflowBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 587 | `apply_workflow_command` | `not isinstance(plan, dict)` |
| `PLAN_INVALID` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow.py` | 278 | `plan_hash` | `—` |
| `PLAN_INVALID` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow.py` | 290 | `validate_plan` | `not isinstance(plan, Mapping)` |
| `PLAN_INVALID` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow.py` | 297 | `validate_plan` | `—` |
| `PLAN_INVALID` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow.py` | 303 | `validate_plan` | `len(set(keys)) != len(keys)` |
| `PLAN_INVALID` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow.py` | 319 | `validate_plan` | `unknown` |
| `PLAN_INVALID` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow.py` | 321 | `validate_plan` | `key in depends_on` |
| `PLAN_INVALID` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow.py` | 324 | `validate_plan` | `not_deps` |
| `PLAN_INVALID` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow.py` | 339 | `validate_plan` | `not steps[-1].request or not steps[-1].reason` |
| `PLAN_INVALID` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow.py` | 368 | `topological_order` | `not ready` |
| `POLICY_UNAVAILABLE` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 120 | `_policy` | `—` |
| `POLICY_UNAVAILABLE` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 568 | `build_permission_decision` | `—` |
| `POLICY_UNREADABLE` | `ControlBlocked` | `runtime/mvp_runtime/schedule_delegation.py` | 109 | `load_delegation` | `—` |
| `POOL_CONTEXT_CAP_EXCEEDED` | `ToolError` | `runtime/mvp_runtime/crypto/pool_admission.py` | 758 | `assert_pool_within_size_cap` | `over` |
| `POOL_CONTEXT_DIRECTION_SPLIT` | `ToolError` | `runtime/mvp_runtime/crypto/pool_admission.py` | 796 | `assert_pool_within_size_cap` | `split` |
| `POOL_RULE_ALREADY_ROUTED` | `ToolError` | `runtime/mvp_runtime/crypto/pool_admission.py` | 535 | `assert_rule_not_routed` | `rule_hashes_of(candidate) & rule_hashes_of(other)` |
| `POOL_RULE_ALREADY_ROUTED` | `ToolError` | `runtime/mvp_runtime/crypto/pool_admission.py` | 545 | `assert_rule_not_routed` | `routed` |
| `POOL_SILENT_REACTIVATION` | `ToolError` | `runtime/mvp_runtime/crypto/pool_admission.py` | 449 | `assert_no_silent_reactivation` | `—` |
| `POOL_SIZE_CAP_EXCEEDED` | `ToolError` | `runtime/mvp_runtime/crypto/pool_admission.py` | 739 | `assert_pool_within_size_cap` | `len(occupying) > MAX_ROUTABLE_STRATEGIES` |
| `POSITIONING_SERIES_UNKNOWN` | `ToolError` | `runtime/mvp_runtime/crypto/positioning_store.py` | 270 | `append_rows` | `series not in POSITIONING_SERIES` |
| `POSITIONING_SYMBOL_MISSING` | `ToolError` | `runtime/mvp_runtime/crypto/positioning_store.py` | 268 | `append_rows` | `not name` |
| `POSITION_CONTEXT_MISMATCH` | `ToolError` | `runtime/mvp_runtime/crypto/paper.py` | 699 | `__post_init__` | `not (isinstance(value, str) and _CONTEXT_PART_PATTERN.match(value))` |
| `POSITION_CONTEXT_MISMATCH` | `ToolError` | `runtime/mvp_runtime/crypto/paper.py` | 704 | `__post_init__` | `value.split('.', 1)[0].upper() in RESERVED_BASENAMES` |
| `POSITION_CONTEXT_MISMATCH` | `ToolError` | `runtime/mvp_runtime/crypto/paper.py` | 750 | `position_path` | `path.parent != resolved_base` |
| `POSITION_CONTEXT_MISMATCH` | `ToolError` | `runtime/mvp_runtime/crypto/paper.py` | 785 | `load_open_position` | `PositionContext.from_position(stored) != context` |
| `POSITION_CONTEXT_MISMATCH` | `ToolError` | `runtime/mvp_runtime/crypto/paper.py` | 820 | `list_open_positions` | `blocker is not None` |
| `POSITION_STATE_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/paper.py` | 764 | `_read_position_file` | `—` |
| `PROBE_BATCH_EXHAUSTED` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 544 | `select_cell` | `not empty` |
| `PROBE_BUDGET_EXCEEDED` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 318 | `assert_batch_budget` | `worst > cap` |
| `PROBE_CELL_OPEN` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 537 | `select_cell` | `opened is not None` |
| `PROBE_CELL_OPEN` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 971 | `abandon_plan` | `open_index is not None` |
| `PROBE_FILTERS_UNAVAILABLE` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 649 | `probe_quantity` | `filters is None or not filters.valid()` |
| `PROBE_FILTERS_UNAVAILABLE` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 672 | `probe_stop_price` | `trigger <= 0` |
| `PROBE_PARAMS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 249 | `build_batch_params` | `not names` |
| `PROBE_PARAMS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 252 | `build_batch_params` | `duplicates` |
| `PROBE_PARAMS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 266 | `build_batch_params` | `not name.endswith('USDT') or name == 'USDT' or (not name.isalnum())` |
| `PROBE_PARAMS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 272 | `build_batch_params` | `int(repeats) < 1 or int(timeout_minutes) < 1` |
| `PROBE_PARAMS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 274 | `build_batch_params` | `not float(stop_bps) > 0` |
| `PROBE_PARAMS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 278 | `build_batch_params` | `n != grid` |
| `PROBE_PARAMS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 284 | `build_batch_params` | `not (float(per_probe_notional_cap_usdt) > 0 and float(budget_cap_usdt) > 0)` |
| `PROBE_PARAMS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 959 | `abandon_plan` | `not isinstance(reason, str) or not reason.strip()` |
| `PROBE_PLAN_CHANGED` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 503 | `write_plan` | `stored != expected_sha256` |
| `PROBE_PLAN_EXISTS` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 935 | `confirm_probe_batch` | `existing is not None and existing['status'] == PLAN_ACTIVE` |
| `PROBE_PLAN_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 418 | `validate_plan` | `not isinstance(plan, dict)` |
| `PROBE_PLAN_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 422 | `validate_plan` | `unknown or missing` |
| `PROBE_PLAN_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 428 | `validate_plan` | `plan['plan_version'] != PLAN_VERSION` |
| `PROBE_PLAN_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 430 | `validate_plan` | `plan['status'] not in PLAN_STATUSES` |
| `PROBE_PLAN_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 433 | `validate_plan` | `not isinstance(params, dict) or set(params) != _PARAM_KEYS` |
| `PROBE_PLAN_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 435 | `validate_plan` | `plan['batch_id'] != batch_id_of(params)` |
| `PROBE_PLAN_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 438 | `validate_plan` | `not isinstance(cells, list) or len(cells) != int(params['n'])` |
| `PROBE_PLAN_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 441 | `validate_plan` | `not isinstance(cell, dict) or set(cell) != _CELL_KEYS` |
| `PROBE_PLAN_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 443 | `validate_plan` | `cell['status'] not in CELL_STATUSES` |
| `PROBE_PLAN_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 445 | `validate_plan` | `cell['symbol'] not in params['symbols'] or cell['regime'] not in REGIMES` |
| `PROBE_PLAN_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 565 | `mark_cell` | `not 0 <= index < len(cells)` |
| `PROBE_PLAN_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 569 | `mark_cell` | `status not in legal.get(current, set())` |
| `PROBE_PLAN_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 572 | `mark_cell` | `unknown` |
| `PROBE_PLAN_MISSING` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 962 | `abandon_plan` | `plan is None` |
| `PROBE_PLAN_NOT_ACTIVE` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 533 | `select_cell` | `plan['status'] != PLAN_ACTIVE` |
| `PROBE_PLAN_NOT_ACTIVE` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 964 | `abandon_plan` | `plan['status'] != PLAN_ACTIVE` |
| `PROBE_PLAN_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 466 | `read_plan` | `not isinstance(stored, str) or _plan_sha256(raw) != stored` |
| `PROBE_PLAN_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 480 | `_stored_plan_sha256` | `not isinstance(stored, str)` |
| `PROBE_PLAN_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 461 | `read_plan` | `—` |
| `PROBE_PLAN_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 463 | `read_plan` | `not isinstance(raw, dict)` |
| `PROBE_PLAN_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 477 | `_stored_plan_sha256` | `—` |
| `PROBE_PRICE_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 651 | `probe_quantity` | `not (isinstance(price, (int, float)) and (not isinstance(price, bool)) and (price > 0))` |
| `PROBE_PRICE_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 669 | `probe_stop_price` | `not (isinstance(fill_price, (int, float)) and (not isinstance(fill_price, bool)) and (fill_pric…` |
| `PROBE_REGIME_EXHAUSTED` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 550 | `select_cell` | `—` |
| `PROBE_REGIME_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 352 | `regime_of` | `isinstance(atr_percentile, bool) or not isinstance(atr_percentile, (int, float))` |
| `PROGRAMIZATION_RECORD_INVALID` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 228 | `_validate` | `—` |
| `PROGRAMIZATION_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/programization.py` | 115 | `read_observations` | `—` |
| `PROGRAMIZATION_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/programization.py` | 120 | `read_patterns` | `—` |
| `PROGRAMIZATION_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/programization.py` | 142 | `read_candidates` | `—` |
| `PROGRAMIZATION_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/programization.py` | 160 | `read_requests` | `—` |
| `PROGRAMIZATION_WRITE_FAILED` | `PersistenceError` | `runtime/mvp_runtime/programization.py` | 133 | `append_observation` | `—` |
| `PROGRAMIZATION_WRITE_FAILED` | `PersistenceError` | `runtime/mvp_runtime/programization.py` | 137 | `append_pattern` | `—` |
| `PROGRAMIZATION_WRITE_FAILED` | `PersistenceError` | `runtime/mvp_runtime/programization.py` | 155 | `append_candidate` | `—` |
| `PROGRAMIZATION_WRITE_FAILED` | `PersistenceError` | `runtime/mvp_runtime/programization.py` | 164 | `append_request` | `—` |
| `PROMOTED_NOT_RETIRED` | `OperatorBlocked` | `runtime/mvp_runtime/memory_console.py` | 232 | `apply_memory_command` | `—` |
| `PROMOTED_UNAUDITED` | `OperatorBlocked` | `runtime/mvp_runtime/memory_console.py` | 240 | `apply_memory_command` | `—` |
| `PROMOTION_ACTOR_MISSING` | `AuditError` | `runtime/mvp_runtime/audit.py` | 632 | `build_promotion_audit` | `not (isinstance(promoted_by, str) and promoted_by.strip())` |
| `PROMOTION_ARTIFACT_INVALID` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/promotion.py` | 119 | `artifact_pairs` | `isinstance(artifact_sha256s, (str, bytes)) or len(artifact_sha256s) != len(candidate_ids) or (n…` |
| `PROMOTION_ORIGIN_INVALID` | `AuditError` | `runtime/mvp_runtime/audit.py` | 629 | `build_promotion_audit` | `missing` |
| `PROMOTION_ORIGIN_MISSING` | `AuditError` | `runtime/mvp_runtime/audit.py` | 622 | `build_promotion_audit` | `not isinstance(origin, Mapping)` |
| `PROMOTION_REASON_MISSING` | `AuditError` | `runtime/mvp_runtime/audit.py` | 634 | `build_promotion_audit` | `not (isinstance(reason, str) and reason.strip())` |
| `PROMOTION_SUBJECT_INVALID` | `AuditError` | `runtime/mvp_runtime/audit.py` | 646 | `build_promotion_audit` | `not (isinstance(candidate_id, str) and candidate_id and isinstance(validated_id, str) and valid…` |
| `PROMOTION_TIER_INVALID` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/promotion.py` | 97 | `promotion_content_sha256` | `live_tier not in pool_store.LIVE_TIERS` |
| `PROPOSAL_INPUTS_REQUIRED` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 629 | `_apply_job` | `not isinstance(inputs, dict)` |
| `PROPOSAL_INPUTS_REQUIRED` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 639 | `_apply_job` | `not isinstance(families, list) or not all((isinstance(f, str) for f in families)) or len(famili…` |
| `PROPOSAL_INPUTS_REQUIRED` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 645 | `_apply_job` | `focus is not None and (not isinstance(focus, str))` |
| `PROPOSAL_INPUTS_REQUIRED` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 655 | `_apply_job` | `timeframe is not None and timeframe not in ALLOWED_TIMEFRAMES` |
| `PROTO_UNSUPPORTED` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 193 | `negotiate_proto` | `isinstance(raw, bool) or not isinstance(raw, int) or raw not in SUPPORTED_PROTOS` |
| `PROVIDER_ERROR` | `WorkerBlocked` | `runtime/mvp_runtime/validator.py` | 229 | `run_validation_worker` | `—` |
| `PROVIDER_ERROR` | `WorkerBlocked` | `runtime/mvp_runtime/worker.py` | 672 | `run_analysis_worker` | `—` |
| `PROVIDER_NOT_AUTHORIZED` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 128 | `assert_authorization` | `authorization.provider_id != provider_id` |
| `PROVIDER_TRANSPORT` | `ProviderError` | `runtime/mvp_runtime/providers.py` | 382 | `_post_json_with_retry` | `—` |
| `PROVIDER_TRANSPORT` | `ProviderError` | `runtime/mvp_runtime/providers.py` | 387 | `_post_json_with_retry` | `—` |
| `PROVIDER_UNAVAILABLE` | `ProviderError` | `runtime/mvp_runtime/providers.py` | 379 | `_post_json_with_retry` | `exc.code in _RETRYABLE_HTTP` |
| `PROVIDER_UNAVAILABLE` | `ProviderError` | `runtime/mvp_runtime/providers.py` | 958 | `generate` | `—` |
| `QUERY_TOO_LONG` | `ToolBlocked` | `runtime/mvp_runtime/tools.py` | 98 | `_require_query` | `len(query) > MAX_QUERY_CHARS` |
| `QUEUE_FULL` | `TaskRegistryBlocked` | `runtime/mvp_runtime/task_registry.py` | 576 | `enqueue` | `depth >= QUEUE_DEPTH_LIMIT` |
| `REASON_REQUIRED` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 277 | `apply_dispatch` | `not isinstance(reason, str) or not reason.strip()` |
| `REASON_REQUIRED` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 574 | `apply_workflow_command` | `not isinstance(reason, str) or not reason.strip()` |
| `REASON_REQUIRED` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 272 | `apply_work` | `not isinstance(reason, str) or not reason.strip()` |
| `REASON_REQUIRED` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 337 | `_require_reason` | `not isinstance(reason, str) or not reason.strip()` |
| `REGISTRATION_MALFORMED` | `OperatorBlocked` | `runtime/mvp_runtime/operator.py` | 227 | `load_operator_registration` | `—` |
| `REGISTRATION_MALFORMED` | `OperatorBlocked` | `runtime/mvp_runtime/operator.py` | 231 | `load_operator_registration` | `not (isinstance(operator_id, str) and operator_id and isinstance(chat_id, str) and chat_id)` |
| `REGISTRATION_MISSING` | `OperatorBlocked` | `runtime/mvp_runtime/operator.py` | 220 | `load_operator_registration` | `not path.is_file()` |
| `REGISTRATION_REQUIRES_ACCEPTED` | `ProgramizationBlocked` | `runtime/mvp_runtime/registration.py` | 66 | `_lineage` | `candidate.get('status') != 'ACCEPTED'` |
| `REGISTRATION_REQUIRES_REQUEST` | `ProgramizationBlocked` | `runtime/mvp_runtime/registration.py` | 73 | `_lineage` | `not isinstance(request, dict)` |
| `REGISTRATION_SELF_CHECK_FAILED` | `ProgramizationBlocked` | `runtime/mvp_runtime/registration.py` | 293 | `apply_registration` | `—` |
| `REGISTRY_RECORD_INVALID` | `TaskRegistryBlocked` | `runtime/mvp_runtime/task_registry.py` | 213 | `from_record` | `—` |
| `REGISTRY_RECORD_INVALID` | `TaskRegistryBlocked` | `runtime/mvp_runtime/task_registry.py` | 218 | `from_record` | `status not in _ALLOWED_TRANSITIONS` |
| `REGISTRY_RECORD_INVALID` | `TaskRegistryBlocked` | `runtime/mvp_runtime/task_registry.py` | 249 | `_validate` | `—` |
| `REGISTRY_UNAVAILABLE` | `PlannerBlocked` | `runtime/mvp_runtime/planner.py` | 254 | `load_resolved_roles` | `—` |
| `REGISTRY_UNAVAILABLE` | `OperatorBlocked` | `runtime/mvp_runtime/registry_console.py` | 262 | `apply_registry_command` | `registry is None` |
| `REGISTRY_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/task_registry.py` | 356 | `_read_rows` | `—` |
| `REGISTRY_UNRESOLVABLE` | `ProgramizationBlocked` | `runtime/mvp_runtime/program_request.py` | 72 | `_registry_snapshot` | `—` |
| `REGISTRY_UNRESOLVABLE` | `ProgramizationBlocked` | `runtime/mvp_runtime/registration.py` | 167 | `request_registration` | `—` |
| `REGISTRY_UNRESOLVABLE` | `ProgramizationBlocked` | `runtime/mvp_runtime/registration.py` | 259 | `apply_registration` | `—` |
| `REGISTRY_WRITE_FAILED` | `PersistenceError` | `runtime/mvp_runtime/task_registry.py` | 364 | `_append` | `—` |
| `REPORTED_USAGE_INVALID` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 537 | `record_reported_usage` | `not isinstance(usage, dict) or set(usage) - _REPORTED_KEYS or (not _REPORTED_REQUIRED <= set(us…` |
| `REPORTED_USAGE_INVALID` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 542 | `record_reported_usage` | `isinstance(v, bool) or not isinstance(v, int) or v < 0` |
| `REPORTED_USAGE_INVALID` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 545 | `record_reported_usage` | `isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost < 0 or (cost != cost)` |
| `REPORTED_USAGE_INVALID` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 547 | `record_reported_usage` | `usage['cost_status'] not in wf.REPORTED_COST_STATUSES` |
| `REPORTED_USAGE_INVALID` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 551 | `record_reported_usage` | `not isinstance(source, str) or not source.strip() or len(source) > wf.REPORTED_SOURCE_MAX` |
| `REPORTED_USAGE_INVALID` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 554 | `record_reported_usage` | `not isinstance(as_of, str) or not as_of.strip()` |
| `REPORTED_USAGE_INVALID` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 558 | `record_reported_usage` | `—` |
| `REQUEST_EXISTS` | `ProgramizationBlocked` | `runtime/mvp_runtime/program_request.py` | 153 | `create_program_request` | `any((row.get('candidate_id') == candidate_id for row in store.read_requests()))` |
| `REQUEST_ID_CONFLICT` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 321 | `submit` | `prior['fingerprint'] != validated.plan_hash` |
| `REQUEST_ID_REQUIRED` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 482 | `apply_workflow_command` | `request_id is None` |
| `REQUEST_ID_REUSED` | `ControlBlocked` | `runtime/mvp_runtime/bridge_idempotency.py` | 141 | `claim` | `prior.get('request_sha256') != request_fingerprint` |
| `REQUEST_INPUT_INVALID` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization_cli.py` | 238 | `main` | `not (args.program_id and args.program_version)` |
| `REQUEST_IN_FLIGHT` | `ControlBlocked` | `runtime/mvp_runtime/bridge_idempotency.py` | 152 | `claim` | `prior.get('state') == STATE_CLAIMED` |
| `REQUEST_LINEAGE_MISSING` | `ProgramizationBlocked` | `runtime/mvp_runtime/program_request.py` | 105 | `_lineage_task` | `anchor is None` |
| `REQUEST_REQUIRED` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 260 | `apply_dispatch` | `not isinstance(text, str) or not text.strip()` |
| `REQUEST_REQUIRED` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 254 | `apply_work` | `not isinstance(text, str) or not text.strip()` |
| `REQUEST_REQUIRES_ACCEPTED` | `ProgramizationBlocked` | `runtime/mvp_runtime/program_request.py` | 148 | `create_program_request` | `candidate.get('status') != 'ACCEPTED'` |
| `RESPONSE_TRUNCATED` | `WorkerBlocked` | `runtime/mvp_runtime/validator.py` | 234 | `run_validation_worker` | `response_was_truncated(result.finish_reason)` |
| `RESPONSE_TRUNCATED` | `WorkerBlocked` | `runtime/mvp_runtime/worker.py` | 678 | `run_analysis_worker` | `response_was_truncated(result.finish_reason)` |
| `RESULT_UNAVAILABLE` | `OperatorBlocked` | `runtime/mvp_runtime/registry_console.py` | 314 | `apply_registry_command` | `rendered is None` |
| `RETIREMENT_DUPLICATE_SELECTOR` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/retirement.py` | 71 | `resolve_pool_entries` | `duplicates` |
| `RETIREMENT_EMPTY` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/retirement.py` | 68 | `resolve_pool_entries` | `not strategy_ids` |
| `RETIREMENT_REASON_REQUIRED` | `ToolError` | `runtime/mvp_runtime/crypto/lifecycle.py` | 364 | `operator_retirement_decision` | `not (isinstance(reason, str) and reason.strip())` |
| `RETRY_NOT_APPLICABLE` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 885 | `retry_step` | `s['status'] == wf.S_BLOCKED and s['last_reason_code'] == wf.DEPENDENCY_FAILED` |
| `RETRY_NOT_APPLICABLE` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 890 | `retry_step` | `s['status'] not in (wf.S_FAILED, wf.S_BLOCKED, wf.S_NEEDS_RECONCILIATION)` |
| `RISK_BELOW_DISPOSITION_FLOOR` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 616 | `build_permission_decision` | `declared_rank is None or declared_rank < RISK_ORDER[risk_floor]` |
| `RISK_SNAPSHOT_ID_CONFLICT` | `ToolError` | `runtime/mvp_runtime/crypto/pre_order_gate.py` | 691 | `append` | `snapshot_id in recorded` |
| `RISK_SNAPSHOT_INTENT_MISMATCH` | `ToolError` | `runtime/mvp_runtime/crypto/pre_order_gate.py` | 573 | `verify_snapshot` | `mismatch is not None` |
| `RISK_SNAPSHOT_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/pre_order_gate.py` | 566 | `verify_snapshot` | `problem is not None` |
| `RISK_SNAPSHOT_MISSING` | `ToolError` | `runtime/mvp_runtime/crypto/pre_order_gate.py` | 555 | `verify_snapshot` | `not isinstance(snapshot, Mapping)` |
| `RISK_SNAPSHOT_MISSING` | `ToolError` | `runtime/mvp_runtime/crypto/pre_order_gate.py` | 678 | `append` | `not (isinstance(snapshot_id, str) and snapshot_id and isinstance(sha, str) and sha)` |
| `RISK_SNAPSHOT_NOT_APPROVED` | `ToolError` | `runtime/mvp_runtime/crypto/pre_order_gate.py` | 561 | `verify_snapshot` | `snapshot.get('approved') is not True or snapshot.get('failed_checks')` |
| `RISK_SNAPSHOT_NO_STORE` | `ToolError` | `runtime/mvp_runtime/crypto/pre_order_gate.py` | 588 | `verify_and_persist` | `store is None` |
| `RISK_SNAPSHOT_NO_STORE` | `ToolError` | `runtime/mvp_runtime/crypto/pre_order_gate.py` | 590 | `verify_and_persist` | `require_durable and getattr(store, 'filesystem_write', False) is not True` |
| `RISK_SNAPSHOT_STALE` | `ToolError` | `runtime/mvp_runtime/crypto/pre_order_gate.py` | 576 | `verify_snapshot` | `stale is not None` |
| `RISK_SNAPSHOT_STORE_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/pre_order_gate.py` | 636 | `_read_rows` | `—` |
| `RISK_SNAPSHOT_STORE_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/pre_order_gate.py` | 638 | `_read_rows` | `not isinstance(row, dict)` |
| `RISK_SNAPSHOT_STORE_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/pre_order_gate.py` | 731 | `read_snapshots` | `not _seal_matches(row)` |
| `RISK_SNAPSHOT_STORE_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/pre_order_gate.py` | 733 | `read_snapshots` | `_schema_problem(row) is not None or _unsupported(row, for_send=False) is not None` |
| `RISK_SNAPSHOT_STORE_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/pre_order_gate.py` | 737 | `read_snapshots` | `snapshot_id in seen and seen[snapshot_id] != row.get('risk_snapshot_sha256')` |
| `RISK_SNAPSHOT_STORE_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/pre_order_gate.py` | 623 | `_read_rows` | `—` |
| `RISK_SNAPSHOT_STORE_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/pre_order_gate.py` | 628 | `_read_rows` | `—` |
| `RISK_SNAPSHOT_STORE_UNWRITABLE` | `ToolError` | `runtime/mvp_runtime/crypto/pre_order_gate.py` | 703 | `append` | `—` |
| `RISK_SNAPSHOT_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/pre_order_gate.py` | 557 | `verify_snapshot` | `snapshot.get('snapshot_version') != SNAPSHOT_VERSION or snapshot.get('risk_gate_id') != GATE_ID` |
| `RISK_SNAPSHOT_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/pre_order_gate.py` | 559 | `verify_snapshot` | `not _seal_matches(snapshot)` |
| `RISK_SNAPSHOT_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/pre_order_gate.py` | 598 | `verify_and_persist` | `written != sha` |
| `RISK_SNAPSHOT_UNSUPPORTED` | `ToolError` | `runtime/mvp_runtime/crypto/pre_order_gate.py` | 569 | `verify_snapshot` | `unsupported is not None` |
| `RISK_SNAPSHOT_VENUE_MISMATCH` | `ToolError` | `runtime/mvp_runtime/crypto/pre_order_gate.py` | 593 | `verify_and_persist` | `getattr(store, 'venue', None) != snapshot.get('venue')` |
| `ROLE_ALREADY_ACTIVE` | `PlannerBlocked` | `runtime/mvp_runtime/planner.py` | 278 | `select_candidate_role` | `status == 'active'` |
| `ROLE_BINDING_UNSUPPORTED` | `ProviderError` | `runtime/mvp_runtime/providers.py` | 938 | `bind_role_output_keys` | `binder is None` |
| `ROLE_DEFINITION_INVALID` | `PlannerBlocked` | `runtime/mvp_runtime/assignment.py` | 104 | `build_role_assignment` | `—` |
| `ROLE_DEFINITION_INVALID` | `PlannerBlocked` | `runtime/mvp_runtime/planner.py` | 120 | `load_role_definition` | `—` |
| `ROLE_DEFINITION_INVALID` | `PlannerBlocked` | `runtime/mvp_runtime/planner.py` | 256 | `load_resolved_roles` | `—` |
| `ROLE_OUTPUT_CONTRACT_UNSUPPORTED_BY_PROVIDER` | `WorkerBlocked` | `runtime/mvp_runtime/pipeline.py` | 167 | `_provider_for_role` | `getattr(provider, 'network_egress', False)` |
| `ROLE_REGISTRY_MISMATCH` | `KernelBlocked` | `runtime/read_only_kernel/preflight.py` | 404 | `run_preflight` | `—` |
| `ROUTE_NOT_SUPPORTED` | `KernelBlocked` | `runtime/read_only_kernel/router.py` | 13 | `select_route` | `routing.get('selected_route') != 'ROLE'` |
| `ROUTE_NOT_SUPPORTED` | `KernelBlocked` | `runtime/read_only_kernel/worker_port.py` | 18 | `invoke_worker` | `route.selected_route != 'ROLE'` |
| `SCHEDULER_EVENT_INVALID` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 857 | `mutation_event` | `action not in MUTATION_ACTIONS` |
| `SCHEDULER_EVENT_INVALID` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 2033 | `_execute` | `run_id is None` |
| `SCHEDULER_LEDGER_UNAVAILABLE` | `ControlBlocked` | `runtime/mvp_runtime/store_reads.py` | 94 | `read_scheduler_events` | `ledger is None or not hasattr(ledger, 'read_scheduler_events_tail')` |
| `SCHEDULER_UNKNOWN_LANE` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 344 | `lane_kinds` | `—` |
| `SCHEDULES_UNAVAILABLE` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 521 | `apply_workflow_command` | `schedule_store is None` |
| `SCHEDULES_UNAVAILABLE` | `ControlBlocked` | `runtime/mvp_runtime/store_reads.py` | 68 | `read_schedules` | `schedules is None` |
| `SCHEDULES_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/scheduler.py` | 587 | `list` | `—` |
| `SCHEDULES_WRITE_FAILED` | `PersistenceError` | `runtime/mvp_runtime/scheduler.py` | 595 | `_save` | `—` |
| `SCHEDULE_CHANGE_INVALID` | `ControlBlocked` | `runtime/mvp_runtime/schedule_delegation.py` | 130 | `parse` | `not isinstance(raw, Mapping)` |
| `SCHEDULE_CHANGE_INVALID` | `ControlBlocked` | `runtime/mvp_runtime/schedule_delegation.py` | 133 | `parse` | `unexpected` |
| `SCHEDULE_CHANGE_INVALID` | `ControlBlocked` | `runtime/mvp_runtime/schedule_delegation.py` | 136 | `parse` | `not isinstance(action, str) or action.strip().lower() not in ACTIONS` |
| `SCHEDULE_CHANGE_INVALID` | `ControlBlocked` | `runtime/mvp_runtime/schedule_delegation.py` | 140 | `parse` | `not isinstance(reason, str) or not reason.strip() or len(reason) > MAX_REASON_CHARS` |
| `SCHEDULE_CHANGE_INVALID` | `ControlBlocked` | `runtime/mvp_runtime/schedule_delegation.py` | 144 | `parse` | `not isinstance(kind, str) or not kind.strip()` |
| `SCHEDULE_CHANGE_INVALID` | `ControlBlocked` | `runtime/mvp_runtime/schedule_delegation.py` | 146 | `parse` | `not isinstance(request, str) or len(request) > MAX_REQUEST_CHARS` |
| `SCHEDULE_CHANGE_INVALID` | `ControlBlocked` | `runtime/mvp_runtime/schedule_delegation.py` | 148 | `parse` | `isinstance(interval, bool) or not isinstance(interval, int)` |
| `SCHEDULE_CHANGE_INVALID` | `ControlBlocked` | `runtime/mvp_runtime/schedule_delegation.py` | 150 | `parse` | `raw.get('schedule_id') is not None` |
| `SCHEDULE_CHANGE_INVALID` | `ControlBlocked` | `runtime/mvp_runtime/schedule_delegation.py` | 154 | `parse` | `not isinstance(schedule_id, str) or not schedule_id.strip()` |
| `SCHEDULE_CHANGE_INVALID` | `ControlBlocked` | `runtime/mvp_runtime/schedule_delegation.py` | 157 | `parse` | `raw.get(key) not in (None, '')` |
| `SCHEDULE_DELEGATION_DISABLED` | `ControlBlocked` | `runtime/mvp_runtime/schedule_delegation.py` | 261 | `apply_change` | `delegation is None` |
| `SCHEDULE_DELEGATION_INVALID` | `ControlBlocked` | `runtime/mvp_runtime/schedule_delegation.py` | 65 | `from_clause` | `not isinstance(clause, Mapping) or clause.get('mutation_allowed') is not True` |
| `SCHEDULE_DELEGATION_INVALID` | `ControlBlocked` | `runtime/mvp_runtime/schedule_delegation.py` | 68 | `from_clause` | `not isinstance(kinds, list) or not kinds or (not all((isinstance(k, str) for k in kinds)))` |
| `SCHEDULE_DELEGATION_INVALID` | `ControlBlocked` | `runtime/mvp_runtime/schedule_delegation.py` | 71 | `from_clause` | `unknown` |
| `SCHEDULE_DELEGATION_INVALID` | `ControlBlocked` | `runtime/mvp_runtime/schedule_delegation.py` | 74 | `from_clause` | `financial` |
| `SCHEDULE_DELEGATION_INVALID` | `ControlBlocked` | `runtime/mvp_runtime/schedule_delegation.py` | 81 | `from_clause` | `isinstance(value, bool) or not isinstance(value, int) or value < floor` |
| `SCHEDULE_DELEGATION_INVALID` | `ControlBlocked` | `runtime/mvp_runtime/schedule_delegation.py` | 255 | `apply_change` | `isinstance(delegation, InvalidDelegation)` |
| `SCHEDULE_NOT_FOUND` | `SchedulerBlocked` | `runtime/mvp_runtime/schedule_delegation.py` | 250 | `apply_change` | `existing is None` |
| `SCHEDULE_NOT_FOUND` | `SchedulerBlocked` | `runtime/mvp_runtime/schedule_delegation.py` | 318 | `apply_change` | `affected is None` |
| `SCHEDULE_RECORD_INVALID` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 440 | `from_record` | `—` |
| `SCHEDULE_RECORD_INVALID` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 445 | `from_record` | `not (isinstance(next_run_at, str) and _TIMESTAMP_PATTERN.match(next_run_at))` |
| `SCHEDULE_RECORD_INVALID` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 453 | `from_record` | `expires_at is not None and (not (isinstance(expires_at, str) and _TIMESTAMP_PATTERN.match(expir…` |
| `SCHEMA_INVALID` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 332 | `build_task` | `—` |
| `SCHEMA_UNAVAILABLE` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 328 | `build_task` | `not schema_path.is_file()` |
| `SCOPE_NOT_CONSUMABLE` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 306 | `validate_spendable_approval` | `snapshot.get('permission_scope') != expected_scope` |
| `SCOPE_NOT_PERMITTED` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 369 | `_require_scope` | `scope not in _ALLOWED_SCOPES` |
| `SCOPE_NOT_SPENDABLE` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 666 | `_spend` | `snapshot.get('permission_scope') != TRADING_SWITCH_PERMISSION_SCOPE` |
| `SECRET_BEARING_KEY` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 321 | `build_task` | `—` |
| `SECRET_IN_CANDIDATE` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 177 | `build_memory_candidates` | `—` |
| `SECRET_IN_CANDIDATE` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 230 | `build_correction_candidate` | `—` |
| `SECRET_IN_CANDIDATE` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 535 | `create_program_candidate` | `—` |
| `SECRET_IN_CANDIDATE` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 644 | `record_shadow_result` | `—` |
| `SECRET_IN_CORE_CANDIDATE` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 455 | `build_core_candidate` | `—` |
| `SECRET_IN_CORE_CANDIDATE` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 515 | `decide_core_candidate` | `—` |
| `SECRET_IN_DEFINITION` | `ProgramizationBlocked` | `runtime/mvp_runtime/registration.py` | 122 | `build_program_definition` | `—` |
| `SECRET_IN_REQUEST` | `ProgramizationBlocked` | `runtime/mvp_runtime/program_request.py` | 339 | `create_program_request` | `—` |
| `SECRET_IN_VALIDATED` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 633 | `promote_candidate` | `—` |
| `SEED_TOO_LONG` | `ToolBlocked` | `runtime/mvp_runtime/naver_research.py` | 252 | `_require_seed` | `len(seed) > MAX_SEED_CHARS` |
| `SETTLEMENT_RACE_LOST` | `ToolError` | `runtime/mvp_runtime/crypto/paper.py` | 1055 | `settle_position` | `current is None or current.get('position_id') != expected_id` |
| `SHADOW_EVIDENCE_MISSING` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 624 | `record_shadow_result` | `not (isinstance(comparison_ref, str) and comparison_ref.strip())` |
| `SHADOW_EVIDENCE_MISSING` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 626 | `record_shadow_result` | `not (isinstance(result, str) and result.strip())` |
| `SHADOW_NOT_RUNNING` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 630 | `record_shadow_result` | `latest.get('status') != 'VALIDATING' or latest.get('shadow_validation', {}).get('status') != 'R…` |
| `SHADOW_OUTCOME_INVALID` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 622 | `record_shadow_result` | `outcome not in ('PASS', 'FAIL')` |
| `STATE_FOREIGN_ROOT_RUN` | `PersistenceError` | `runtime/mvp_runtime/state_guard.py` | 192 | `assert_not_foreign_root_run` | `owner is not None` |
| `STATE_NOT_WRITABLE` | `PersistenceError` | `runtime/mvp_runtime/state_guard.py` | 209 | `assert_state_writable` | `offenders` |
| `STEP_NOT_FOUND` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 883 | `retry_step` | `s is None` |
| `STOP_CHANGED` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 753 | `_spend` | `stop_ref(current) != approved_stop` |
| `STOP_NOT_NAMED` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 746 | `_spend` | `not isinstance(approved_stop, str) or not approved_stop` |
| `STORE_READ_ONLY` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 257 | `_write` | `self._readonly` |
| `STRATEGY_ARTIFACT_UNHASHABLE` | `ToolError` | `runtime/mvp_runtime/crypto/strategy_artifact.py` | 179 | `_spec_fingerprint` | `not isinstance(spec, Mapping)` |
| `STRATEGY_ARTIFACT_UNHASHABLE` | `ToolError` | `runtime/mvp_runtime/crypto/strategy_artifact.py` | 183 | `_spec_fingerprint` | `—` |
| `STRATEGY_ARTIFACT_UNHASHABLE` | `ToolError` | `runtime/mvp_runtime/crypto/strategy_artifact.py` | 224 | `from_pool_entry` | `not isinstance(carried, Mapping)` |
| `STRATEGY_ARTIFACT_UNHASHABLE` | `ToolError` | `runtime/mvp_runtime/crypto/strategy_artifact.py` | 226 | `from_pool_entry` | `carried.get('version') != STRATEGY_ARTIFACT_VERSION` |
| `STRATEGY_ARTIFACT_UNHASHABLE` | `ToolError` | `runtime/mvp_runtime/crypto/strategy_artifact.py` | 245 | `_record_sha` | `—` |
| `STRATEGY_POOL_ARTIFACT_MISMATCH` | `ToolError` | `runtime/mvp_runtime/crypto/strategy_artifact.py` | 293 | `assert_pool_artifacts` | `problem is not None` |
| `STRATEGY_POOL_DUPLICATE` | `ToolError` | `runtime/mvp_runtime/crypto/pool_state.py` | 136 | `assert_pool_identity_unique` | `strategy_id in seen_strategy` |
| `STRATEGY_POOL_DUPLICATE` | `ToolError` | `runtime/mvp_runtime/crypto/pool_state.py` | 141 | `assert_pool_identity_unique` | `candidate_id in seen_candidate` |
| `STRATEGY_POOL_DUPLICATE` | `ToolError` | `runtime/mvp_runtime/crypto/pool_state.py` | 168 | `assert_pool_identity_unique` | `others` |
| `STRATEGY_POOL_DUPLICATE` | `ToolError` | `runtime/mvp_runtime/crypto/pool_state.py` | 173 | `assert_pool_identity_unique` | `inherited_by.setdefault(key, index) != index` |
| `STRATEGY_POOL_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/pool_state.py` | 159 | `assert_pool_identity_unique` | `not isinstance(keys, list) or not all((is_lineage_key(key) for key in keys))` |
| `STRATEGY_POOL_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/pool_state.py` | 190 | `_read_active_pool` | `—` |
| `STRATEGY_POOL_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/pool_state.py` | 186 | `_read_active_pool` | `—` |
| `SUBJECT_FINGERPRINT_FAILED` | `AuditError` | `runtime/mvp_runtime/audit.py` | 49 | `_fingerprint` | `—` |
| `TARGET_EXISTS` | `ToolBlocked` | `runtime/mvp_runtime/workspace.py` | 138 | `write` | `—` |
| `TARGET_EXISTS` | `ToolBlocked` | `runtime/mvp_runtime/workspace.py` | 249 | `run_write` | `target.exists()` |
| `TARGET_NOT_CANDIDATE` | `ApprovalBlocked` | `runtime/mvp_runtime/consumption.py` | 193 | `consume_approval` | `not target_ref.startswith(_CANDIDATE_TARGET_PREFIX)` |
| `TARGET_NOT_CANDIDATE_ROLE` | `ApprovalBlocked` | `runtime/mvp_runtime/trial.py` | 213 | `_parse_target` | `not target_ref.startswith(_TARGET_PREFIX) or '@' not in target_ref` |
| `TARGET_NOT_CANDIDATE_ROLE` | `ApprovalBlocked` | `runtime/mvp_runtime/trial.py` | 219 | `_parse_target` | `not role_id or not version` |
| `TARGET_NOT_SWITCH` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 678 | `_spend` | `prefix is None` |
| `TASK_NOT_FINISHED` | `OperatorBlocked` | `runtime/mvp_runtime/registry_console.py` | 298 | `apply_registry_command` | `not entry.is_terminal` |
| `TESTNET_EVIDENCE_DUPLICATE` | `ToolError` | `runtime/mvp_runtime/crypto/testnet_evidence.py` | 204 | `read_cycles` | `cycle_id in seen` |
| `TESTNET_EVIDENCE_INCOMPLETE` | `ToolError` | `runtime/mvp_runtime/crypto/testnet_evidence.py` | 228 | `assert_complete_cycle` | `row is None` |
| `TESTNET_EVIDENCE_INCOMPLETE` | `ToolError` | `runtime/mvp_runtime/crypto/testnet_evidence.py` | 231 | `assert_complete_cycle` | `findings` |
| `TESTNET_EVIDENCE_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/testnet_evidence.py` | 200 | `read_cycles` | `not isinstance(stored, str) or recomputed != stored` |
| `TESTNET_EVIDENCE_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/testnet_evidence.py` | 188 | `read_cycles` | `—` |
| `TESTNET_EVIDENCE_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/testnet_evidence.py` | 198 | `read_cycles` | `—` |
| `TESTNET_ORDER_HOST_NOT_ALLOWED` | `ToolError` | `runtime/mvp_runtime/crypto/testnet_execution.py` | 173 | `__init__` | `host not in ALLOWED_TESTNET_HOSTS` |
| `TOKEN_BUDGET_EXCEEDED` | `WorkerBlocked` | `runtime/mvp_runtime/validator.py` | 241 | `run_validation_worker` | `token_budget and tokens_used > int(token_budget)` |
| `TOKEN_BUDGET_EXCEEDED` | `WorkerBlocked` | `runtime/mvp_runtime/worker.py` | 686 | `run_analysis_worker` | `token_budget and tokens_used > int(token_budget)` |
| `TOOL_ERROR` | `ToolBlocked` | `runtime/mvp_runtime/crypto/market_data.py` | 828 | `collect_market_data` | `—` |
| `TOOL_ERROR` | `ToolBlocked` | `runtime/mvp_runtime/naver_research.py` | 324 | `run_keyword_research` | `—` |
| `TOOL_ERROR` | `ToolBlocked` | `runtime/mvp_runtime/tools.py` | 121 | `run_search` | `—` |
| `TOOL_RATE_LIMITED` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 174 | `classify_transport_error` | `isinstance(status, int) and status in _RATE_LIMIT_STATUSES` |
| `TOOL_REQUEST_REJECTED` | `ToolError` | `runtime/mvp_runtime/naver_research.py` | 151 | `_rejected` | `—` |
| `TOOL_TRANSPORT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 178 | `classify_transport_error` | `—` |
| `TOOL_TRANSPORT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1342 | `derivative_price_klines` | `—` |
| `TOOL_TRANSPORT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1438 | `positioning_history` | `—` |
| `TOOL_TRANSPORT` | `ToolError` | `runtime/mvp_runtime/naver_research.py` | 641 | `keywords` | `—` |
| `TOOL_TRANSPORT` | `ToolError` | `runtime/mvp_runtime/naver_research.py` | 744 | `_fetch` | `—` |
| `TOOL_TRANSPORT` | `ToolError` | `runtime/mvp_runtime/tools.py` | 284 | `search` | `—` |
| `TOOL_TRANSPORT` | `ToolError` | `runtime/mvp_runtime/tools.py` | 374 | `search` | `—` |
| `TOO_LONG` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 89 | `_require_text` | `len(value) > max_len` |
| `TOO_LONG` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 127 | `_clean_str_list` | `len(item) > MAX_FIELD_CHARS` |
| `TOO_MANY_ITEMS` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 118 | `_clean_str_list` | `len(items) > MAX_LIST_ITEMS` |
| `TRANSITION_INVALID` | `TaskRegistryBlocked` | `runtime/mvp_runtime/task_registry.py` | 323 | `_assert_transition` | `target not in _ALLOWED_TRANSITIONS.get(current, frozenset())` |
| `TRANSITION_INVALID` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow.py` | 218 | `assert_transition` | `current not in table` |
| `TRANSITION_INVALID` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow.py` | 220 | `assert_transition` | `target not in table[current]` |
| `TRANSITION_INVALID` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 960 | `bind_approval` | `s is None or s['status'] != wf.S_WAITING_APPROVAL` |
| `TRANSITION_INVALID` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 976 | `approve_step` | `s is None or s['status'] != wf.S_WAITING_APPROVAL` |
| `TRANSITION_INVALID` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 1002 | `refuse_step_approval` | `s is None or s['status'] != wf.S_WAITING_APPROVAL` |
| `TRIAL_REQUEST_MISSING` | `ApprovalBlocked` | `runtime/mvp_runtime/trial.py` | 399 | `run_trial` | `not (isinstance(trial_request, str) and trial_request.strip())` |
| `TTL_EXCEEDS_POLICY` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 182 | `build_approval_request` | `requested > policy_max` |
| `UNEXPECTED_TRIAL_AUTHORIZATION` | `PlannerBlocked` | `runtime/mvp_runtime/assignment.py` | 119 | `build_role_assignment` | `not trial and trial_authorization_ref is not None` |
| `UNIX_SOCKETS_UNAVAILABLE` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 401 | `__init__` | `not UNIX_SOCKETS_AVAILABLE` |
| `UNIX_SOCKETS_UNAVAILABLE` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 761 | `call_door` | `not UNIX_SOCKETS_AVAILABLE or not hasattr(socket, 'AF_UNIX')` |
| `UNKNOWN_APPROVAL` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 275 | `validate_spendable_approval` | `approval_rec is None` |
| `UNKNOWN_APPROVAL` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 850 | `apply_command` | `approval is None` |
| `UNKNOWN_APPROVAL` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 632 | `_spend` | `record is None` |
| `UNKNOWN_CANDIDATE` | `MvpRuntimeError` | `runtime/mvp_runtime/approval_cli.py` | 59 | `_find_candidate` | `entry is None` |
| `UNKNOWN_CANDIDATE` | `ToolError` | `runtime/mvp_runtime/crypto/pool.py` | 200 | `resolve_candidates` | `missing` |
| `UNKNOWN_COMMAND` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 842 | `apply_command` | `verb not in COMMANDS` |
| `UNKNOWN_COMMAND` | `ControlBlocked` | `runtime/mvp_runtime/control.py` | 817 | `apply_command` | `command not in COMMANDS` |
| `UNKNOWN_COMMAND` | `OperatorBlocked` | `runtime/mvp_runtime/registry_console.py` | 376 | `apply_registry_command` | `—` |
| `UNKNOWN_CORE_CANDIDATE_DECISION` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 491 | `decide_core_candidate` | `decision not in CORE_CANDIDATE_DECISIONS` |
| `UNKNOWN_CORE_CANDIDATE_TYPE` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 413 | `build_core_candidate` | `candidate_type not in CORE_CANDIDATE_TYPES` |
| `UNKNOWN_DOMAIN_COMMAND` | `OperatorBlocked` | `runtime/mvp_runtime/domain_console.py` | 201 | `apply_domain_command` | `verb not in _SUBCOMMANDS` |
| `UNKNOWN_DOMAIN_SUBCOMMAND` | `OperatorBlocked` | `runtime/mvp_runtime/domain_console.py` | 208 | `apply_domain_command` | `handler is None` |
| `UNKNOWN_FLAG` | `TaskRegistryBlocked` | `runtime/mvp_runtime/task_registry.py` | 283 | `build_entry` | `unknown` |
| `UNKNOWN_HALT_LEVEL` | `ControlBlocked` | `runtime/mvp_runtime/control.py` | 820 | `apply_command` | `halt_level is not None and (command != CMD_HALT_TRADING or not isinstance(halt_level, str) or h…` |
| `UNKNOWN_KIND` | `SchedulerBlocked` | `runtime/mvp_runtime/schedule_delegation.py` | 272 | `apply_change` | `change.action == 'create' and kind not in scheduler.KINDS` |
| `UNKNOWN_KIND` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 513 | `build_schedule` | `kind not in KINDS` |
| `UNKNOWN_KIND` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 2058 | `_execute` | `schedule.kind != KIND_TASK` |
| `UNKNOWN_ORIGIN` | `TaskRegistryBlocked` | `runtime/mvp_runtime/task_registry.py` | 272 | `build_entry` | `origin not in ORIGINS` |
| `UNKNOWN_ORIGIN` | `TaskRegistryBlocked` | `runtime/mvp_runtime/task_registry.py` | 299 | `build_entry` | `attempt is not None and origin != WORKFLOW_ORIGIN` |
| `UNKNOWN_PARENT_CANDIDATE` | `ToolError` | `runtime/mvp_runtime/crypto/pool_state.py` | 100 | `validate_candidate_lineage` | `unknown` |
| `UNKNOWN_PROVIDER` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 322 | `select_env_gated_chain` | `unknown` |
| `UNKNOWN_REQUEST_KIND` | `PlannerBlocked` | `runtime/mvp_runtime/planner.py` | 147 | `capabilities_for_request_kind` | `capabilities is None` |
| `UNKNOWN_REQUEST_KIND` | `TaskRegistryBlocked` | `runtime/mvp_runtime/task_registry.py` | 292 | `build_entry` | `kind is not None and kind not in REQUEST_KIND_CAPABILITIES` |
| `UNKNOWN_ROLE` | `PlannerBlocked` | `runtime/mvp_runtime/planner.py` | 293 | `select_candidate_role` | `—` |
| `UNKNOWN_SCOPE` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 571 | `build_permission_decision` | `disposition is None` |
| `UNKNOWN_VENUE` | `ToolBlocked` | `runtime/mvp_runtime/crypto/market_data.py` | 123 | `venue_feeds` | `—` |
| `UNREGISTERED_USER` | `OperatorBlocked` | `runtime/mvp_runtime/operator.py` | 245 | `verify_control_channel` | `not isinstance(message.sender_id, str) or message.sender_id != registration.operator_id` |
| `UNREPORTED_ORDER_EVIDENCE_MISSING` | `AuditError` | `runtime/mvp_runtime/audit.py` | 1282 | `build_unreported_live_order_audit` | `not (isinstance(canary_order_id, str) and canary_order_id)` |
| `UNREPORTED_ORDER_EVIDENCE_UNHASHED` | `AuditError` | `runtime/mvp_runtime/audit.py` | 1290 | `build_unreported_live_order_audit` | `not (isinstance(record_sha, str) and record_sha)` |
| `UNREPORTED_ORDER_REASON_MISSING` | `AuditError` | `runtime/mvp_runtime/audit.py` | 1295 | `build_unreported_live_order_audit` | `not reason.strip()` |
| `UNREPORTED_ORDER_TASK_INVALID` | `AuditError` | `runtime/mvp_runtime/audit.py` | 1276 | `build_unreported_live_order_audit` | `not isinstance(recording_task.get(field_name), Mapping)` |
| `UNVERIFIED_SOURCE` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 373 | `record_decision` | `verification.method != TELEGRAM_VERIFICATION_METHOD` |
| `USAGE` | `OperatorBlocked` | `runtime/mvp_runtime/memory_console.py` | 163 | `apply_memory_command` | `not candidate_id` |
| `USAGE` | `OperatorBlocked` | `runtime/mvp_runtime/registry_console.py` | 385 | `_require_entry` | `not argument` |
| `USAGE` | `ControlBlocked` | `runtime/mvp_runtime/store_reads.py` | 151 | `read_approval_status` | `not approval_id` |
| `V2_INTAKE_CLOSED` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 305 | `apply_dispatch` | `live is None` |
| `V2_INTAKE_CLOSED` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 355 | `apply_dispatch` | `prior is None and (not v2_intake)` |
| `VALIDATION_RESULT_INVALID` | `ValidationError` | `runtime/mvp_runtime/validation.py` | 319 | `validate_agent_output` | `—` |
| `VALIDATION_RESULT_INVALID` | `ValidationError` | `runtime/mvp_runtime/validator.py` | 355 | `run_validation_worker` | `—` |
| `VENUE_CONTRACT_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/venue_contract.py` | 210 | `_validate` | `—` |
| `VENUE_CONTRACT_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/venue_contract.py` | 291 | `build_record` | `status not in (STATUS_PASS, STATUS_FAIL)` |
| `VENUE_CONTRACT_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/venue_contract.py` | 328 | `read_verification` | `—` |
| `VENUE_CONTRACT_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/venue_contract.py` | 330 | `read_verification` | `not isinstance(stored, str) or stored != recomputed` |
| `VENUE_CONTRACT_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/venue_contract.py` | 320 | `read_verification` | `—` |
| `VENUE_CONTRACT_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/venue_contract.py` | 322 | `read_verification` | `not isinstance(data, dict)` |
| `VERB_NOT_PERMITTED` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 446 | `apply_workflow_command` | `not isinstance(command, str) or command.strip().lower() not in V3_COMMANDS` |
| `VERB_NOT_PERMITTED` | `ControlBlocked` | `runtime/mvp_runtime/knowledge_bridge.py` | 121 | `apply_knowledge` | `command not in _COMMANDS` |
| `VERB_NOT_PERMITTED` | `ControlBlocked` | `runtime/mvp_runtime/read_bridge.py` | 149 | `apply_read` | `command not in _READS` |
| `VERB_NOT_PERMITTED` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 826 | `apply_switch` | `command not in _ALLOWED_COMMANDS` |
| `VERSION_CONFLICT` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 842 | `request_cancel` | `int(w['row_version']) != int(expected_version)` |
| `VERSION_CONFLICT` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 877 | `retry_step` | `int(w['row_version']) != int(expected_version)` |
| `VERSION_CONFLICT` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 1041 | `propose_update` | `int(w['row_version']) != int(expected_version)` |
| `WINDOW_TOO_EARLY` | `ToolError` | `runtime/mvp_runtime/naver_research.py` | 775 | `trend` | `start_date < self.EARLIEST_PERIOD` |
| `WORKER_SOCKET_IN_ASSISTANT_DIR` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 735 | `open_door` | `'bridge' in {part.lower() for part in path.parts}` |
| `WORKER_UID_ALLOWLIST_REQUIRED` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 724 | `open_door` | `not socket_door.resolve_client_uids()` |
| `WORKER_UNAVAILABLE` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 335 | `apply_dispatch` | `execute is None` |
| `WORKER_UNAVAILABLE` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 369 | `apply_dispatch` | `not isinstance(reply, dict)` |
| `WORKER_UNAVAILABLE` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 678 | `_forward` | `exc.reason_code in {'DOOR_UNREACHABLE', 'DOOR_REPLY_MALFORMED'}` |
| `WORKER_UNAVAILABLE` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 966 | `delegate_analysis_task` | `not isinstance(reply, dict)` |
| `WORKER_UNAVAILABLE` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 1036 | `delegate_data_review` | `not isinstance(record, dict) or not record.get('review_id')` |
| `WORKER_UNAVAILABLE` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 1080 | `delegate_content_ideation` | `not reply.get('package_id')` |
| `WORKER_UNAVAILABLE` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 1158 | `delegate_proposal_generation` | `not isinstance(generation, dict) or 'raw' not in generation` |
| `WORKFLOW_CANCELLING` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 875 | `retry_step` | `w['status'] == wf.W_CANCELLING` |
| `WORKFLOW_CANCELLING` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 1039 | `propose_update` | `w['status'] == wf.W_CANCELLING` |
| `WORKFLOW_NOT_FOUND` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 419 | `status_view` | `row is None` |
| `WORKFLOW_NOT_FOUND` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 561 | `record_reported_usage` | `conn.execute('SELECT 1 FROM workflows WHERE workflow_id=?', (workflow_id,)).fetchone() is None` |
| `WORKFLOW_NOT_FOUND` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 629 | `_budget_locked` | `row is None` |
| `WORKFLOW_NOT_FOUND` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 838 | `request_cancel` | `w is None` |
| `WORKFLOW_NOT_FOUND` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 871 | `retry_step` | `w is None` |
| `WORKFLOW_NOT_FOUND` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 1035 | `propose_update` | `w is None` |
| `WORKFLOW_SNAPSHOT_FAILED` | `PersistenceError` | `runtime/mvp_runtime/workflow_store.py` | 1232 | `snapshot` | `—` |
| `WORKFLOW_SNAPSHOT_MISSING` | `PersistenceError` | `runtime/mvp_runtime/workflow_cli.py` | 63 | `verify_snapshot` | `not path.is_file()` |
| `WORKFLOW_SNAPSHOT_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/workflow_cli.py` | 76 | `verify_snapshot` | `—` |
| `WORKFLOW_SNAPSHOT_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/workflow_cli.py` | 87 | `verify_snapshot` | `manifest_path.is_file()` |
| `WORKFLOW_STORE_UNAVAILABLE` | `PersistenceError` | `runtime/mvp_runtime/workflow_store.py` | 216 | `initialize` | `'locked' not in str(exc).lower() and 'busy' not in str(exc).lower()` |
| `WORKFLOW_STORE_UNAVAILABLE` | `PersistenceError` | `runtime/mvp_runtime/workflow_store.py` | 221 | `initialize` | `—` |
| `WORKFLOW_STORE_UNAVAILABLE` | `PersistenceError` | `runtime/mvp_runtime/workflow_store.py` | 222 | `initialize` | `—` |
| `WORKFLOW_STORE_UNAVAILABLE` | `PersistenceError` | `runtime/mvp_runtime/workflow_store.py` | 241 | `_connect` | `not self._path.is_file()` |
| `WORKFLOW_STORE_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/workflow_store.py` | 278 | `_read` | `—` |
| `WORKFLOW_STORE_WRITE_FAILED` | `PersistenceError` | `runtime/mvp_runtime/workflow_store.py` | 268 | `_write` | `—` |
| `WORKFLOW_TERMINAL` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 840 | `request_cancel` | `w['status'] in wf.WORKFLOW_TERMINAL` |
| `WORKFLOW_TERMINAL` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 873 | `retry_step` | `w['status'] in wf.WORKFLOW_TERMINAL` |
| `WORKFLOW_TERMINAL` | `WorkflowBlocked` | `runtime/mvp_runtime/workflow_store.py` | 1037 | `propose_update` | `w['status'] in wf.WORKFLOW_TERMINAL` |
| `WORKFLOW_UNAVAILABLE` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 467 | `apply_workflow_command` | `workflow_store is None` |
| `WORKFLOW_UNAVAILABLE` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 2027 | `_execute` | `workflow_store is None` |
| `WORKING_MEMORY_WRITE_FAILED` | `PersistenceError` | `runtime/mvp_runtime/working_memory.py` | 167 | `prune_expired` | `removed` |
| `WRITE_FAILED` | `ToolError` | `runtime/mvp_runtime/workspace.py` | 140 | `write` | `—` |
| `WRITE_FAILED` | `ToolError` | `runtime/mvp_runtime/workspace.py` | 145 | `write` | `—` |
| `WRONG_APPROVER` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 368 | `record_decision` | `verification.approved_by != REQUIRED_APPROVER` |
| `direction_mismatch` | `FusionRefused` | `runtime/mvp_runtime/crypto/factory.py` | 4241 | `fuse_specs` | `first.direction != second.direction` |
| `holdout_unjudgeable` | `FusionRefused` | `runtime/mvp_runtime/crypto/factory.py` | 4263 | `fuse_specs` | `len(conditions) > MAX_FUSION_ENTRY_CONDITIONS` |
| `non_and_parent` | `FusionRefused` | `runtime/mvp_runtime/crypto/factory.py` | 4249 | `fuse_specs` | `'OR' in (first.entry_rules.operator, second.entry_rules.operator)` |
| `schema_version_mismatch` | `FusionRefused` | `runtime/mvp_runtime/crypto/factory.py` | 4230 | `fuse_specs` | `first.schema_version != second.schema_version` |
| `stop_model_mismatch` | `FusionRefused` | `runtime/mvp_runtime/crypto/factory.py` | 4247 | `fuse_specs` | `first.exit_rules.stop_model != second.exit_rules.stop_model` |
| `symbol_scope_mismatch` | `FusionRefused` | `runtime/mvp_runtime/crypto/factory.py` | 4245 | `fuse_specs` | `sorted(first.symbol_scope) != sorted(second.symbol_scope)` |
| `timeframe_mismatch` | `FusionRefused` | `runtime/mvp_runtime/crypto/factory.py` | 4243 | `fuse_specs` | `first.timeframe != second.timeframe` |
| `too_many_conditions` | `FusionRefused` | `runtime/mvp_runtime/crypto/factory.py` | 4257 | `fuse_specs` | `len(conditions) > MAX_ENTRY_CONDITIONS` |
| `venue_mismatch` | `FusionRefused` | `runtime/mvp_runtime/crypto/factory.py` | 4239 | `fuse_specs` | `first.venue != second.venue` |
